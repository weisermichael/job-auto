"""Orchestrator: ingest → tailor → review → apply."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from job_auto.ai.cover_letter import cover_letter_to_markdown, generate_cover_letter
from job_auto.ai.tailoring import (
    load_base_resume,
    merge_tailored_into_base,
    tailor_resume,
    tailored_resume_text_for_cover_letter,
    tailored_to_yaml,
)
from job_auto.config import config
from job_auto.db import repository as repo
from job_auto.db.session import get_session
from job_auto.ingestion.generic import get_scraper
from job_auto.models.application import ApplicationRecord, ApplicationStatus
from job_auto.models.job_posting import JobBoard, JobPosting
from job_auto.utils.logging import get_logger
from job_auto.utils.resume_converter import md_to_pdf, yaml_to_pdf

logger = get_logger(__name__)


class PipelineError(Exception):
    pass


class Pipeline:
    """End-to-end job application orchestrator."""

    def __init__(self, autonomous: Optional[bool] = None, tailor: bool = False) -> None:
        self.autonomous = autonomous if autonomous is not None else config.autonomous_mode
        self.tailor = tailor

    # ──────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────

    async def apply(self, url: str, dry_run: bool = False) -> ApplicationRecord:
        """
        Full pipeline: ingest → dedupe → tailor → review → apply.

        dry_run=True runs everything except the actual Playwright submission.
        """
        config.ensure_dirs()

        # 1. INGEST
        job = await self._ingest(url)
        logger.info("job_ingested", job_id=job.id, title=job.title, company=job.company)

        # 2. DEDUPE / UPSERT
        # Always look up by URL — the freshly-scraped job has a new UUID that won't
        # match any existing DB record, so we must use the existing row's ID.
        with get_session() as session:
            existing_job = repo.get_job_by_url(session, str(job.url))
            if existing_job is not None:
                job = existing_job  # use the canonical DB record (correct ID)
                existing_app = repo.get_application_by_job(session, job.id)
                if existing_app and existing_app.status not in {
                    ApplicationStatus.FAILED,
                    ApplicationStatus.REVIEW_REJECTED,
                    ApplicationStatus.SUBMITTING,  # stuck due to crash — allow retry
                }:
                    logger.info("duplicate_skip", url=url)
                    return existing_app
                logger.info("job_exists_no_application", job_id=job.id)
            else:
                repo.upsert_job(session, job)

        # 3. TAILOR or prepare base documents
        if self.tailor:
            application = await self._tailor(job)
            application = self._render_pdfs(job, application)
        else:
            application = await self._prepare_default_documents(job)

        # 5. REVIEW (if not autonomous)
        if not self.autonomous:
            application = self._review(job, application)
            if application.status == ApplicationStatus.REVIEW_REJECTED:
                logger.info("application_rejected_in_review", app_id=application.id)
                return application

        # 6. APPLY
        if dry_run:
            logger.info("dry_run_skip_submit", app_id=application.id)
            application.status = ApplicationStatus.QUEUED
            with get_session() as session:
                repo.update_application(session, application)
            return application

        # Check daily limit
        with get_session() as session:
            today_count = repo.count_submitted_today(session)
        if today_count >= config.daily_apply_limit:
            logger.warning(
                "daily_limit_reached", limit=config.daily_apply_limit, today=today_count
            )
            raise PipelineError(
                f"Daily application limit ({config.daily_apply_limit}) reached. Try again tomorrow."
            )

        application = await self._submit(job, application)
        return application

    async def apply_from_job(self, job: JobPosting, dry_run: bool = False) -> ApplicationRecord:
        """
        Run the tailor → review → submit pipeline for a job already in the DB.
        Skips re-ingestion — use this for jobs returned by `scan`.
        """
        config.ensure_dirs()

        # Dedupe: skip if a non-failed application already exists
        with get_session() as session:
            existing = repo.get_application_by_job(session, job.id)
        if existing and existing.status not in {
            ApplicationStatus.FAILED,
            ApplicationStatus.REVIEW_REJECTED,
            ApplicationStatus.SUBMITTING,  # stuck due to crash — allow retry
        }:
            logger.info("already_applied_skip", job_id=job.id, status=existing.status)
            return existing

        # Check daily limit before doing any AI work
        with get_session() as session:
            today_count = repo.count_submitted_today(session)
        if not dry_run and today_count >= config.daily_apply_limit:
            raise PipelineError(
                f"Daily application limit ({config.daily_apply_limit}) reached. "
                "Try again tomorrow."
            )

        if self.tailor:
            application = await self._tailor(job)
            application = self._render_pdfs(job, application)
        else:
            application = await self._prepare_default_documents(job)

        if not self.autonomous:
            application = self._review(job, application)
            if application.status == ApplicationStatus.REVIEW_REJECTED:
                return application

        if dry_run:
            application.status = ApplicationStatus.QUEUED
            with get_session() as session:
                repo.update_application(session, application)
            return application

        return await self._submit(job, application)

    async def apply_all_queued(
        self,
        limit: int = 10,
        dry_run: bool = False,
    ) -> list[ApplicationRecord]:
        """
        Apply to all scanned-but-unapplied jobs in the DB, up to `limit`.
        Stops early if the daily submission cap is reached.
        """
        config.ensure_dirs()

        with get_session() as session:
            jobs = repo.list_unapplied_jobs(session, limit=limit)

        if not jobs:
            logger.info("apply_all_no_jobs")
            return []

        logger.info("apply_all_start", count=len(jobs))
        results: list[ApplicationRecord] = []

        for job in jobs:
            try:
                app = await self.apply_from_job(job, dry_run=dry_run)
                results.append(app)
            except PipelineError as e:
                logger.warning("apply_all_stopped", reason=str(e))
                break
            except Exception as e:
                logger.error("apply_all_job_error", job_id=job.id, error=str(e))
                # Continue to next job on non-limit errors

        return results

    async def submit_queued(self, limit: int = 10) -> list[ApplicationRecord]:
        """Submit all applications that are in QUEUED status (approved, not yet submitted)."""
        with get_session() as session:
            apps = repo.list_applications(session, status=ApplicationStatus.QUEUED, limit=limit)

        if not apps:
            logger.info("submit_queued_none_found")
            return []

        logger.info("submit_queued_start", count=len(apps))
        results: list[ApplicationRecord] = []

        for app in apps:
            with get_session() as session:
                job = repo.get_job(session, app.job_id)
            if not job:
                logger.warning("submit_queued_job_not_found", app_id=app.id, job_id=app.job_id)
                continue

            with get_session() as session:
                today_count = repo.count_submitted_today(session)
            if today_count >= config.daily_apply_limit:
                logger.warning("daily_limit_reached", limit=config.daily_apply_limit, today=today_count)
                raise PipelineError(
                    f"Daily application limit ({config.daily_apply_limit}) reached. Try again tomorrow."
                )

            try:
                app = await self._submit(job, app)
                results.append(app)
            except Exception as e:
                logger.error("submit_queued_error", app_id=app.id, error=str(e))

        return results

    async def scan(
        self,
        board: str,
        query: str = "",
        limit: int = 20,
        remote: bool = True,
        **kwargs,
    ) -> list[JobPosting]:
        """Scan a job board for new listings and store them."""
        config.ensure_dirs()

        scraper_map: dict[str, str] = {
            "linkedin": "https://www.linkedin.com",
            "indeed": "https://www.indeed.com",
            "nodesk": "https://nodesk.co",
        }
        url = scraper_map.get(board.lower(), f"https://{board}")
        scraper = get_scraper(url)

        logger.info("scan_start", board=board, query=query, limit=limit)
        async with scraper:
            jobs = await scraper.search(query=query, remote=remote, limit=limit, **kwargs)

        new_jobs = []
        with get_session() as session:
            for job in jobs:
                if not repo.job_exists_by_url(session, str(job.url)):
                    repo.upsert_job(session, job)
                    new_jobs.append(job)

        logger.info("scan_complete", board=board, found=len(jobs), new=len(new_jobs))
        return new_jobs

    # ──────────────────────────────────────────────────────────
    # Pipeline steps
    # ──────────────────────────────────────────────────────────

    async def _ingest(self, url: str) -> JobPosting:
        scraper = get_scraper(url)
        async with scraper:
            return await scraper.parse(url)

    async def _prepare_default_documents(self, job: JobPosting) -> ApplicationRecord:
        """Create an ApplicationRecord using the cached base-resume PDF (no AI calls)."""
        app = ApplicationRecord(
            id=uuid.uuid4().hex[:12],
            job_id=job.id,
            status=ApplicationStatus.REVIEW_PENDING if not self.autonomous else ApplicationStatus.QUEUED,
            autonomous_mode=self.autonomous,
            created_at=datetime.utcnow(),
        )

        # Cached base PDF — regenerate only when resume.yaml is newer
        cached_pdf = config.resumes_dir / "base_resume.pdf"
        yaml_path = config.resume_yaml_path
        needs_render = (
            not cached_pdf.exists()
            or yaml_path.stat().st_mtime > cached_pdf.stat().st_mtime
        )
        if needs_render:
            logger.info("rendering_base_resume_pdf", path=str(cached_pdf))
            yaml_text = load_base_resume()
            yaml_to_pdf(yaml_text, cached_pdf)

        app.resume_path = str(cached_pdf)

        with get_session() as session:
            repo.create_application(session, app)

        logger.info("default_documents_ready", app_id=app.id, resume=str(cached_pdf))
        return app

    async def _tailor(self, job: JobPosting) -> ApplicationRecord:
        """Run AI tailoring and create the ApplicationRecord."""
        app = ApplicationRecord(
            id=uuid.uuid4().hex[:12],
            job_id=job.id,
            status=ApplicationStatus.TAILORING,
            autonomous_mode=self.autonomous,
            created_at=datetime.utcnow(),
        )

        with get_session() as session:
            repo.create_application(session, app)

        base_yaml = load_base_resume()

        # Tailor resume: Claude returns modified cv.sections as JSON
        tailored_result = tailor_resume(job, base_yaml)
        merged_dict = merge_tailored_into_base(base_yaml, tailored_result)
        app.tailored_resume_text = tailored_to_yaml(merged_dict)

        # Generate cover letter using a plain-text extraction of the tailored resume
        resume_text_for_cl = tailored_resume_text_for_cover_letter(merged_dict)
        letter = generate_cover_letter(job, resume_text_for_cl, candidate_name=config.candidate_name)
        app.cover_letter_text = cover_letter_to_markdown(letter)

        app.status = ApplicationStatus.REVIEW_PENDING if not self.autonomous else ApplicationStatus.QUEUED

        with get_session() as session:
            repo.update_application(session, app)

        logger.info("tailoring_done", app_id=app.id)
        return app

    def _render_pdfs(self, job: JobPosting, app: ApplicationRecord) -> ApplicationRecord:
        """Convert tailored resume and cover letter to PDFs."""
        slug = f"{app.id}_{job.company[:20].replace(' ', '_')}"

        if app.tailored_resume_text:
            resume_path = config.resumes_dir / f"{slug}_resume.pdf"
            yaml_to_pdf(app.tailored_resume_text, resume_path)
            app.resume_path = str(resume_path)

        if app.cover_letter_text:
            cl_path = config.cover_letters_dir / f"{slug}_cover_letter.pdf"
            md_to_pdf(app.cover_letter_text, cl_path)
            app.cover_letter_path = str(cl_path)

        with get_session() as session:
            repo.update_application(session, app)

        return app

    def _review(self, job: JobPosting, app: ApplicationRecord) -> ApplicationRecord:
        """Interactive human review; returns updated application."""
        from job_auto.ai.tailoring import load_base_resume
        from job_auto.review.diff_display import review_application

        base_resume = load_base_resume()
        new_status, notes = review_application(job, app, base_resume)
        app.status = new_status
        if notes:
            app.last_correction = notes

        with get_session() as session:
            repo.update_application(session, app)

        return app

    async def _submit(self, job: JobPosting, app: ApplicationRecord) -> ApplicationRecord:
        """Run the Playwright application bot."""
        from playwright.async_api import async_playwright

        from job_auto.automation.browser import browser_context, new_page

        board = job.board.value
        applicator_class = self._get_applicator_class(board)

        app.status = ApplicationStatus.SUBMITTING
        with get_session() as session:
            repo.update_application(session, app)

        session_path = config.linkedin_session_path if board == "linkedin" else None
        try:
            async with async_playwright() as pw:
                async with browser_context(pw, storage_state_path=session_path) as (_, context):
                    async with new_page(context) as page:
                        applicator = applicator_class(page)

                        # Log in if LinkedIn
                        if board == "linkedin" and hasattr(applicator, "login"):
                            await applicator.login()

                        result = await applicator.submit(job, app)
                        if result.success:
                            app.status = ApplicationStatus.SUBMITTED
                        else:
                            app.status = result.intended_status or ApplicationStatus.FAILED
                            app.last_failure_reason = result.message
        except Exception:
            # Ensure status never stays stuck at SUBMITTING if something crashes
            if app.status == ApplicationStatus.SUBMITTING:
                app.status = ApplicationStatus.FAILED
            raise
        finally:
            with get_session() as session:
                repo.update_application(session, app)

        return app

    @staticmethod
    def _get_applicator_class(board: str):
        from job_auto.automation.indeed import IndeedApplicator
        from job_auto.automation.linkedin import LinkedInApplicator
        from job_auto.automation.nodesk import NodeskApplicator

        return {
            "linkedin": LinkedInApplicator,
            "indeed": IndeedApplicator,
            "nodesk": NodeskApplicator,
        }.get(board, LinkedInApplicator)
