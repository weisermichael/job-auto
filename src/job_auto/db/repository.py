"""CRUD operations for jobs, applications, and outcomes."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from job_auto.db.models import ApplicationRecordORM, JobPostingORM
from job_auto.models.application import ApplicationRecord, ApplicationStatus
from job_auto.models.job_posting import JobPosting


# ──────────────────────────────────────────────────────────
# Conversion helpers
# ──────────────────────────────────────────────────────────

def _job_to_orm(job: JobPosting) -> JobPostingORM:
    return JobPostingORM(
        id=job.id,
        title=job.title,
        company=job.company,
        board=job.board.value,
        url=str(job.url),
        description=job.description,
        requirements=json.dumps(job.requirements),
        nice_to_haves=json.dumps(job.nice_to_haves),
        responsibilities=json.dumps(job.responsibilities),
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        location=job.location,
        remote=job.remote,
        experience_level=job.experience_level.value,
        easy_apply_available=job.easy_apply_available,
        date_found=job.date_found,
        tech_stack=json.dumps(job.tech_stack),
        keywords=json.dumps(job.keywords),
    )


def _orm_to_job(orm: JobPostingORM) -> JobPosting:
    from job_auto.models.job_posting import ExperienceLevel, JobBoard
    return JobPosting(
        id=orm.id,
        title=orm.title,
        company=orm.company,
        board=JobBoard(orm.board),
        url=orm.url,  # type: ignore[arg-type]
        description=orm.description,
        requirements=json.loads(orm.requirements),
        nice_to_haves=json.loads(orm.nice_to_haves),
        responsibilities=json.loads(orm.responsibilities),
        salary_min=orm.salary_min,
        salary_max=orm.salary_max,
        location=orm.location,
        remote=orm.remote,
        experience_level=ExperienceLevel(orm.experience_level),
        easy_apply_available=orm.easy_apply_available,
        date_found=orm.date_found,
        tech_stack=json.loads(orm.tech_stack),
        keywords=json.loads(orm.keywords),
    )


def _app_to_orm(app: ApplicationRecord) -> ApplicationRecordORM:
    return ApplicationRecordORM(
        id=app.id,
        job_id=app.job_id,
        status=app.status.value,
        resume_path=app.resume_path,
        cover_letter_path=app.cover_letter_path,
        tailored_resume_text=app.tailored_resume_text,
        cover_letter_text=app.cover_letter_text,
        autonomous_mode=app.autonomous_mode,
        created_at=app.created_at,
        submitted_at=app.submitted_at,
        failure_count=app.failure_count,
        last_failure_reason=app.last_failure_reason,
        last_failure_screenshot=app.last_failure_screenshot,
        last_correction=app.last_correction,
    )


def _orm_to_app(orm: ApplicationRecordORM) -> ApplicationRecord:
    return ApplicationRecord(
        id=orm.id,
        job_id=orm.job_id,
        status=ApplicationStatus(orm.status),
        resume_path=orm.resume_path,
        cover_letter_path=orm.cover_letter_path,
        tailored_resume_text=orm.tailored_resume_text,
        cover_letter_text=orm.cover_letter_text,
        autonomous_mode=orm.autonomous_mode,
        created_at=orm.created_at,
        submitted_at=orm.submitted_at,
        failure_count=orm.failure_count,
        last_failure_reason=orm.last_failure_reason,
        last_failure_screenshot=orm.last_failure_screenshot,
        last_correction=orm.last_correction,
    )


# ──────────────────────────────────────────────────────────
# Job repository
# ──────────────────────────────────────────────────────────

def upsert_job(session: Session, job: JobPosting) -> JobPosting:
    """Insert or update a job posting."""
    existing = session.get(JobPostingORM, job.id)
    if existing:
        orm = _job_to_orm(job)
        for col in JobPostingORM.__table__.columns:
            setattr(existing, col.name, getattr(orm, col.name))
        return job
    session.add(_job_to_orm(job))
    return job


def get_job(session: Session, job_id: str) -> Optional[JobPosting]:
    orm = session.get(JobPostingORM, job_id)
    return _orm_to_job(orm) if orm else None


def job_exists_by_url(session: Session, url: str) -> bool:
    from sqlalchemy import select
    stmt = select(JobPostingORM.id).where(JobPostingORM.url == url)
    return session.execute(stmt).scalar() is not None


def get_job_by_url(session: Session, url: str) -> Optional[JobPosting]:
    """Return the existing JobPosting for this URL, or None if not in DB."""
    from sqlalchemy import select
    stmt = select(JobPostingORM).where(JobPostingORM.url == url)
    orm = session.scalars(stmt).first()
    return _orm_to_job(orm) if orm else None


def list_jobs(session: Session, limit: int = 100, offset: int = 0) -> list[JobPosting]:
    from sqlalchemy import select
    stmt = select(JobPostingORM).order_by(JobPostingORM.date_found.desc()).limit(limit).offset(offset)
    return [_orm_to_job(row) for row in session.scalars(stmt)]


# ──────────────────────────────────────────────────────────
# Application repository
# ──────────────────────────────────────────────────────────

def create_application(session: Session, app: ApplicationRecord) -> ApplicationRecord:
    session.add(_app_to_orm(app))
    return app


def update_application(session: Session, app: ApplicationRecord) -> ApplicationRecord:
    existing = session.get(ApplicationRecordORM, app.id)
    if not existing:
        raise ValueError(f"Application {app.id} not found")
    orm = _app_to_orm(app)
    for col in ApplicationRecordORM.__table__.columns:
        setattr(existing, col.name, getattr(orm, col.name))
    return app


def get_application(session: Session, app_id: str) -> Optional[ApplicationRecord]:
    orm = session.get(ApplicationRecordORM, app_id)
    return _orm_to_app(orm) if orm else None


def get_application_by_job(session: Session, job_id: str) -> Optional[ApplicationRecord]:
    from sqlalchemy import select
    stmt = select(ApplicationRecordORM).where(ApplicationRecordORM.job_id == job_id)
    orm = session.scalars(stmt).first()
    return _orm_to_app(orm) if orm else None


def list_applications(
    session: Session,
    status: Optional[ApplicationStatus] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ApplicationRecord]:
    from sqlalchemy import select
    stmt = select(ApplicationRecordORM).order_by(ApplicationRecordORM.created_at.desc())
    if status:
        stmt = stmt.where(ApplicationRecordORM.status == status.value)
    stmt = stmt.limit(limit).offset(offset)
    return [_orm_to_app(row) for row in session.scalars(stmt)]


def increment_failure(session: Session, app_id: str, reason: str, screenshot: Optional[str] = None) -> None:
    orm = session.get(ApplicationRecordORM, app_id)
    if orm:
        orm.failure_count += 1
        orm.last_failure_reason = reason
        orm.last_failure_screenshot = screenshot
        orm.status = ApplicationStatus.FAILED.value


def mark_submitted(session: Session, app_id: str) -> None:
    orm = session.get(ApplicationRecordORM, app_id)
    if orm:
        orm.status = ApplicationStatus.SUBMITTED.value
        orm.submitted_at = datetime.utcnow()


def mark_needs_answers(session: Session, app_id: str, reason: str) -> None:
    """Mark application as needing human-supplied answers (not a hard failure)."""
    orm = session.get(ApplicationRecordORM, app_id)
    if orm:
        orm.status = ApplicationStatus.NEEDS_ANSWERS.value
        orm.last_failure_reason = reason


def list_unapplied_jobs(session: Session, limit: int = 50) -> list[JobPosting]:
    """Return jobs that have no application record yet (never been processed)."""
    from sqlalchemy import select
    applied_job_ids = select(ApplicationRecordORM.job_id)
    stmt = (
        select(JobPostingORM)
        .where(JobPostingORM.id.not_in(applied_job_ids))
        .order_by(JobPostingORM.date_found.desc())
        .limit(limit)
    )
    return [_orm_to_job(row) for row in session.scalars(stmt)]


def count_submitted_today(session: Session) -> int:
    from datetime import date
    from sqlalchemy import func, select
    today_start = datetime.combine(date.today(), datetime.min.time())
    stmt = (
        select(func.count(ApplicationRecordORM.id))
        .where(ApplicationRecordORM.status == ApplicationStatus.SUBMITTED.value)
        .where(ApplicationRecordORM.submitted_at >= today_start)
    )
    return session.execute(stmt).scalar() or 0
