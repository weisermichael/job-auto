"""Nodesk.co application bot (typically links out to company ATS)."""

from __future__ import annotations

import asyncio
from datetime import datetime

from job_auto.automation.base import AbstractApplicator
from job_auto.knowledge_base.store import kb_store
from job_auto.models.procedure import ApplicationProcedure, ProcedureStep
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_PROCEDURE = ApplicationProcedure(
    board="nodesk",
    version=1,
    last_updated=datetime.utcnow(),
    steps=[
        ProcedureStep(
            order=1,
            description="Navigate to Nodesk job posting",
            action="navigate",
            value="{{job_url}}",
            wait_after_ms=2000,
        ),
        ProcedureStep(
            order=2,
            description="Click Apply button to reach company application page",
            action="click",
            selector="a.apply-button, a[href*='apply'], a.btn-apply, .job-apply-link",
            wait_after_ms=3000,
        ),
    ],
)


class NodeskApplicator(AbstractApplicator):
    """
    Nodesk jobs typically redirect to external ATS (Greenhouse, Lever, Workday, etc.).
    This bot navigates to the application link; actual form filling requires
    board-specific handling or falls back to GenericApplicator.
    """

    board_name = "nodesk"

    def load_procedure(self) -> ApplicationProcedure:
        stored = kb_store.get_procedure("nodesk")
        return stored or _DEFAULT_PROCEDURE

    async def execute_step(self, step: ProcedureStep, context: dict[str, str]) -> bool:
        page = self.page
        action = step.action
        value = step.render_value(context)

        if action == "navigate":
            nav_url = value or context["job_url"]
            logger.debug("navigate_start", url=nav_url)
            await page.goto(nav_url, wait_until="domcontentloaded")
            logger.debug("navigate_complete", url=page.url)

        elif action == "click":
            selector = step.selector or ""
            for sel in [s.strip() for s in selector.split(",")]:
                logger.debug("apply_button_trying", selector=sel)
                try:
                    await page.wait_for_selector(sel, timeout=5000)
                    await page.click(sel)
                    logger.debug("apply_button_clicked", selector=sel)
                    return True
                except Exception:
                    continue
            raise RuntimeError(f"Apply button not found on Nodesk page: {context['job_url']}")

        elif action == "wait":
            await asyncio.sleep((step.wait_after_ms or 1000) / 1000)

        return True
