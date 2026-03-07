"""Indeed Quick Apply automation bot."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime
from pathlib import Path

from job_auto.automation.base import AbstractApplicator
from job_auto.automation.browser import human_move_and_click, human_type
from job_auto.knowledge_base.store import kb_store
from job_auto.models.procedure import ApplicationProcedure, FormSelector, ProcedureStep
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_PROCEDURE = ApplicationProcedure(
    board="indeed",
    version=1,
    last_updated=datetime.utcnow(),
    steps=[
        ProcedureStep(
            order=1,
            description="Navigate to job posting",
            action="navigate",
            value="{{job_url}}",
            wait_after_ms=2000,
        ),
        ProcedureStep(
            order=2,
            description="Click Apply / Indeed Apply button",
            action="click",
            selector="#indeedApplyButton, .indeed-apply-button, [data-testid='indeedApplyButton']",
            wait_after_ms=2000,
        ),
        ProcedureStep(
            order=3,
            description="Upload resume if prompted",
            action="upload",
            selector="input[type='file'][accept*='pdf'], input[type='file'][accept*='doc']",
            value="{{resume_path}}",
            wait_after_ms=2000,
            optional=True,
        ),
        ProcedureStep(
            order=4,
            description="Navigate multi-step form",
            action="submit",
            selector="button[id*='form-action-continue'], button[data-testid='continue-button']",
            wait_after_ms=3000,
        ),
    ],
    selectors={
        "apply_btn": FormSelector(css="#indeedApplyButton"),
        "file_upload": FormSelector(css="input[type='file']"),
        "continue_btn": FormSelector(css="button[id*='form-action-continue']"),
    },
)


class IndeedApplicator(AbstractApplicator):
    board_name = "indeed"

    def load_procedure(self) -> ApplicationProcedure:
        stored = kb_store.get_procedure("indeed")
        return stored or _DEFAULT_PROCEDURE

    async def execute_step(self, step: ProcedureStep, context: dict[str, str]) -> bool:
        page = self.page
        action = step.action
        selector = step.selector
        value = step.render_value(context)

        if action == "navigate":
            nav_url = value or context["job_url"]
            logger.debug("navigate_start", url=nav_url)
            await page.goto(nav_url, wait_until="domcontentloaded")
            logger.debug("navigate_complete", url=page.url)

        elif action == "click":
            logger.debug("apply_button_clicking", selector=selector)
            await self._smart_click(selector or "")
            logger.debug("apply_button_clicked", url=page.url)

        elif action == "fill":
            if not value:
                if step.optional:
                    return True
                raise ValueError(f"No value for fill step {step.order}")
            await self._smart_fill(selector or "", value)

        elif action == "upload":
            if not value or not Path(value).exists():
                if step.optional:
                    return True
                raise FileNotFoundError(f"Resume not found: {value}")
            await page.set_input_files(selector or "input[type='file']", value)

        elif action == "submit":
            await self._handle_multipage_form(context)

        elif action == "wait":
            await asyncio.sleep((step.wait_after_ms or 1000) / 1000)

        return True

    async def _handle_multipage_form(self, context: dict[str, str]) -> None:
        """Navigate through Indeed's multi-step application form."""
        continue_selectors = [
            "button[id*='form-action-continue']",
            "button[data-testid='continue-button']",
            "button[id*='submit']",
            "button[type='submit']",
        ]
        max_steps = 10
        for _ in range(max_steps):
            # Look for submit final button first
            for final_sel in ["button[data-testid='submit-button']", "input[type='submit']"]:
                try:
                    visible = await self.page.is_visible(final_sel, timeout=1000)
                    if visible:
                        await human_move_and_click(self.page, final_sel)
                        return
                except Exception:
                    pass

            # Click continue
            clicked = False
            for sel in continue_selectors:
                try:
                    visible = await self.page.is_visible(sel, timeout=1000)
                    if visible:
                        logger.debug("indeed_form_page_advance", selector=sel, url=self.page.url)
                        await human_move_and_click(self.page, sel)
                        await asyncio.sleep(random.uniform(1.0, 2.0))
                        clicked = True
                        break
                except Exception:
                    pass
            if not clicked:
                break

    async def _smart_click(self, selector: str) -> None:
        for sel in [s.strip() for s in selector.split(",")]:
            try:
                await self.page.wait_for_selector(sel, timeout=5000)
                await human_move_and_click(self.page, sel)
                return
            except Exception:
                continue
        raise RuntimeError(f"Could not click: {selector}")

    async def _smart_fill(self, selector: str, value: str) -> None:
        for sel in [s.strip() for s in selector.split(",")]:
            try:
                await self.page.wait_for_selector(sel, timeout=5000)
                await human_type(self.page, sel, value)
                return
            except Exception:
                continue
        raise RuntimeError(f"Could not fill: {selector}")
