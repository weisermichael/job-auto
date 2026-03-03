"""Abstract applicator base class + self-healing retry loop."""

from __future__ import annotations

import asyncio
import json
import random
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from job_auto.ai.error_analyzer import analyze_failure
from job_auto.config import config
from job_auto.db import repository as repo
from job_auto.db.session import get_session
from job_auto.models.application import ApplicationRecord, ApplicationStatus
from job_auto.models.job_posting import JobPosting
from job_auto.models.procedure import ApplicationProcedure, ProcedureStep
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)


class ApplicationResult:
    def __init__(self, success: bool, message: str = "") -> None:
        self.success = success
        self.message = message


class AbstractApplicator(ABC):
    """
    Base class for all job board application bots.

    Subclasses must implement:
    - board_name: str
    - load_procedure() → ApplicationProcedure
    - execute_step(page, step, context) → bool
    """

    board_name: str = "generic"

    def __init__(self, page: Page) -> None:
        self.page = page
        self._procedure: Optional[ApplicationProcedure] = None
        self._current_step: int = 0

    @abstractmethod
    def load_procedure(self) -> ApplicationProcedure:
        """Load the application procedure from the knowledge base."""

    @abstractmethod
    async def execute_step(
        self,
        step: ProcedureStep,
        context: dict[str, str],
    ) -> bool:
        """
        Execute a single procedure step.
        Return True on success, raise on failure.
        """

    async def submit(
        self,
        job: JobPosting,
        application: ApplicationRecord,
    ) -> ApplicationResult:
        """
        Run the full application procedure with self-healing.
        Updates the database on success or failure.
        """
        procedure = self.load_procedure()
        self._procedure = procedure

        context = self._build_context(job, application)

        for step in sorted(procedure.steps, key=lambda s: s.order):
            self._current_step = step.order
            result = await self._run_step_with_healing(step, context, application)
            if not result.success:
                return result

        # Mark submitted
        with get_session() as session:
            repo.mark_submitted(session, application.id)

        from job_auto.knowledge_base.updater import record_success
        record_success(self.board_name, [s.model_dump() for s in procedure.steps])

        logger.info("application_submitted", app_id=application.id, job_id=job.id)
        return ApplicationResult(success=True, message="Application submitted successfully")

    async def _run_step_with_healing(
        self,
        step: ProcedureStep,
        context: dict[str, str],
        application: ApplicationRecord,
    ) -> ApplicationResult:
        """Execute a step; on failure, attempt AI-guided self-healing."""
        for attempt in range(config.max_retries + 1):
            try:
                success = await self.execute_step(step, context)
                if success:
                    if step.wait_after_ms > 0:
                        await asyncio.sleep(step.wait_after_ms / 1000)
                    return ApplicationResult(success=True)
            except (PlaywrightTimeout, Exception) as e:
                error_msg = str(e)
                logger.warning(
                    "step_failed",
                    step=step.order,
                    attempt=attempt,
                    error=error_msg[:200],
                )

                if attempt >= config.max_retries:
                    # Take screenshot for record
                    screenshot_path = await self._take_screenshot(application.id, step.order)

                    with get_session() as session:
                        repo.increment_failure(
                            session,
                            application.id,
                            reason=error_msg,
                            screenshot=str(screenshot_path) if screenshot_path else None,
                        )

                    from job_auto.knowledge_base.updater import record_failure
                    record_failure(self.board_name, error_msg)

                    return ApplicationResult(success=False, message=error_msg)

                # Self-heal disabled for debugging — TODO: re-enable once stable
                # correction = await self._self_heal(step, error_msg, application)
                # if correction and correction.get("step_to_retry", -1) == -1:
                #     abort_reason = correction.get("abort_reason", "AI decided to abort")
                #     logger.info("self_heal_abort", reason=abort_reason)
                #     return ApplicationResult(success=False, message=abort_reason)
                # if correction:
                #     try:
                #         await self._apply_correction(correction, context)
                #     except Exception as heal_err:
                #         logger.warning("self_heal_correction_failed", error=str(heal_err)[:200])

                # Random back-off before retry
                await asyncio.sleep(random.uniform(1, 3))

        return ApplicationResult(success=False, message="Max retries exceeded")

    async def _self_heal(
        self,
        step: ProcedureStep,
        error_msg: str,
        application: ApplicationRecord,
    ) -> Optional[dict[str, Any]]:
        """Take a screenshot + DOM snapshot, send to Claude for analysis."""
        screenshot_path = await self._take_screenshot(application.id, step.order)
        dom_snippet = await self.page.inner_html("body")
        dom_snippet = dom_snippet[:8000]

        procedure_dict = {}
        if self._procedure:
            procedure_dict = self._procedure.model_dump(mode="json")

        try:
            correction = analyze_failure(
                error_message=error_msg,
                failed_step=step.model_dump(mode="json"),
                procedure=procedure_dict,
                dom_snippet=dom_snippet,
                screenshot_path=screenshot_path,
            )
            logger.info(
                "self_heal_correction",
                step=step.order,
                action=correction.get("corrective_action", {}).get("action"),
            )

            # Persist to knowledge base if suggested
            if correction.get("update_kb") and self._procedure:
                from job_auto.knowledge_base.updater import record_patch
                error_sig = f"{type(Exception).__name__}::{step.selector or step.action}"
                record_patch(self.board_name, error_sig, correction.get("corrective_action", {}))

            # Update application record with correction summary
            application.last_correction = correction.get("explanation")
            with get_session() as session:
                repo.update_application(session, application)

            return correction
        except Exception as e:
            logger.error("self_heal_failed", error=str(e))
            return None

    async def _apply_correction(
        self, correction: dict[str, Any], context: dict[str, str]
    ) -> None:
        """Apply a corrective action returned by Claude."""
        action = correction.get("corrective_action", {})
        act = action.get("action", "wait")
        selector = action.get("selector")
        value = action.get("value", "")
        wait_ms = action.get("wait_after_ms", 1000)

        if act == "wait":
            await asyncio.sleep(wait_ms / 1000)
        elif act == "click" and selector:
            await self.page.click(selector)
        elif act == "fill" and selector:
            await self.page.fill(selector, value or "")
        elif act == "navigate" and value:
            await self.page.goto(value)
        elif act == "dismiss_modal":
            for sel in ["button[aria-label='Dismiss']", ".modal-close", "[data-dismiss]", "button.close"]:
                try:
                    await self.page.click(sel, timeout=2000)
                    break
                except Exception:
                    pass

        if wait_ms > 0:
            await asyncio.sleep(wait_ms / 1000)

    async def _take_screenshot(self, app_id: str, step: int) -> Optional[Path]:
        """Capture a screenshot to the screenshots directory."""
        try:
            config.ensure_dirs()
            ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
            path = config.screenshots_dir / f"{app_id}_step{step}_{ts}.png"
            await self.page.screenshot(path=str(path))
            return path
        except Exception as e:
            logger.warning("screenshot_failed", error=str(e))
            return None

    @staticmethod
    def _build_context(job: JobPosting, application: ApplicationRecord) -> dict[str, str]:
        """Build template variable context for step value interpolation."""
        return {
            "job_url": str(job.url),
            "job_title": job.title,
            "company": job.company,
            "resume_path": application.resume_path or "",
            "cover_letter_path": application.cover_letter_path or "",
        }
