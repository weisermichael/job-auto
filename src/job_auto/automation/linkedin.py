"""LinkedIn Easy Apply automation bot."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Optional

from playwright.async_api import Page

from job_auto.automation.base import AbstractApplicator, ApplicationResult
from job_auto.automation.browser import human_move_and_click, human_type
from job_auto.config import config
from job_auto.knowledge_base.store import kb_store
from job_auto.models.application import ApplicationRecord
from job_auto.models.job_posting import JobPosting
from job_auto.models.procedure import ApplicationProcedure, FormSelector, ProcedureStep
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_PROCEDURE = ApplicationProcedure(
    board="linkedin",
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
            description="Click Easy Apply button",
            action="click",
            selector="a[aria-label*='Easy Apply'], .jobs-apply-button--top-card, button[aria-label*='Easy Apply']",
            wait_after_ms=1500,
        ),
        ProcedureStep(
            order=3,
            description="Fill and submit Easy Apply modal",
            action="submit",
            wait_after_ms=3000,
        ),
    ],
    selectors={
        "easy_apply_btn": FormSelector(css="a[aria-label*='Easy Apply'], .jobs-apply-button--top-card, button[aria-label*='Easy Apply']"),
        "submit_btn": FormSelector(css="button[aria-label='Submit application']"),
    },
)


class LinkedInApplicator(AbstractApplicator):
    board_name = "linkedin"

    def __init__(self, page):
        super().__init__(page)
        from job_auto.automation.easy_apply_modal import load_profile
        self._profile = load_profile()

    def load_procedure(self) -> ApplicationProcedure:
        stored = kb_store.get_procedure("linkedin")
        return stored or _DEFAULT_PROCEDURE

    async def execute_step(self, step: ProcedureStep, context: dict[str, str]) -> bool:
        page = self.page
        action = step.action
        selector = step.selector
        value = step.render_value(context)

        if action == "navigate":
            nav_url = value or context.get("job_url", "")
            logger.debug("navigate_start", url=nav_url)
            await page.goto(nav_url, wait_until="load")
            logger.debug("navigate_complete", url=page.url)
            # If LinkedIn redirected to login/checkpoint, re-authenticate and retry navigation
            current_url = page.url
            if any(p in current_url for p in ("/login", "/checkpoint/", "/challenge/")):
                logger.warning("linkedin_post_navigate_auth_required", url=current_url)
                await self.login()
                await page.goto(nav_url, wait_until="load")
                logger.debug("navigate_complete_post_login", url=page.url)
            # Dismiss any contextual sign-in modal that LinkedIn injects and blocks UI interaction.
            # The modal is activated by JavaScript that runs after the load event, so wait for it.
            try:
                dismiss_sel = "button.modal__dismiss, button[aria-label='Dismiss']"
                await page.wait_for_selector(dismiss_sel, state="visible", timeout=5000)
                await page.click(dismiss_sel, timeout=3000)
                logger.info("linkedin_signin_modal_dismissed")
            except Exception:
                pass  # modal not present

        elif action == "click":
            await self._smart_click(selector or "")

        elif action == "fill":
            if not value:
                if step.optional:
                    return True
                raise ValueError(f"No value for fill step {step.order}")
            await self._smart_fill(selector or "", value)

        elif action == "submit":
            from job_auto.automation.easy_apply_modal import handle_easy_apply_modal
            await handle_easy_apply_modal(self.page, context, self._profile)

        elif action == "wait":
            ms = step.wait_after_ms or 1000
            await asyncio.sleep(ms / 1000)

        elif action == "screenshot":
            await self._take_screenshot(context.get("app_id", "unknown"), step.order)

        return True

    async def _smart_click(self, selector: str) -> None:
        """Click with retry across comma-separated selector fallbacks."""
        selectors = [s.strip() for s in selector.split(",")]
        for sel in selectors:
            logger.debug("smart_click_trying", selector=sel)
            try:
                await self.page.wait_for_selector(sel, timeout=5000)
                await human_move_and_click(self.page, sel)
                logger.debug("smart_click_success", selector=sel)
                return
            except Exception:
                continue
        raise RuntimeError(f"Could not find clickable element: {selector}")

    async def _smart_fill(self, selector: str, value: str) -> None:
        """Fill a field with retry across comma-separated selector fallbacks."""
        selectors = [s.strip() for s in selector.split(",")]
        for sel in selectors:
            logger.debug("smart_fill_trying", selector=sel, value_len=len(value))
            try:
                await self.page.wait_for_selector(sel, timeout=5000)
                await human_type(self.page, sel, value)
                logger.debug("smart_fill_success", selector=sel)
                return
            except Exception:
                continue
        raise RuntimeError(f"Could not find fillable element: {selector}")

    async def login(self) -> None:
        """Log in to LinkedIn, handling session restore and security challenges."""
        # Stage 1 — Session check: if cookies were restored, we may already be logged in.
        logger.debug("login_stage1_session_check")
        await self.page.goto("https://www.linkedin.com/feed", wait_until="domcontentloaded")
        if "/feed" in self.page.url:
            logger.info("linkedin_session_valid_skipping_login")
            return

        # Stage 2 — Fresh login
        logger.debug("login_stage2_fresh_login", url=self.page.url)
        if not config.linkedin_email:
            raise RuntimeError("LinkedIn credentials not configured in .env")

        await self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        # Wait for either the standard form (#username + #password) or the
        # "Welcome back" form (only #password, email pre-filled from li_rm cookie).
        await self.page.wait_for_selector("#username, #password", timeout=15_000)
        if await self.page.query_selector("#username"):
            await human_type(self.page, "#username", config.linkedin_email)
        await human_type(
            self.page, "#password", config.linkedin_password.get_secret_value()
        )
        await self.page.click("button[type='submit']")
        await self.page.wait_for_load_state("load")
        logger.debug("login_stage2_submitted", url=self.page.url)

        # Stage 3 — Challenge handling
        if "/checkpoint/" in self.page.url or "/challenge/" in self.page.url:
            logger.warning("linkedin_security_challenge_detected")
            code = await self._fetch_security_code()
            if code:
                pin_sel = "input[name='pin'], input[id*='verification'], input[id*='code']"
                await self.page.wait_for_selector(pin_sel, timeout=5000)
                await self._smart_fill(pin_sel, code)
                await self.page.keyboard.press("Enter")
                try:
                    await self.page.wait_for_url(
                        lambda url: "/checkpoint/" not in url and "/challenge/" not in url,
                        timeout=15_000,
                    )
                except Exception:
                    pass  # URL check below is the source of truth
            if "/checkpoint/" in self.page.url or "/challenge/" in self.page.url:
                raise RuntimeError("LinkedIn security verification was not completed")

        logger.info("linkedin_login_complete")

    async def _fetch_security_code(self) -> Optional[str]:
        """Try Gmail API for the security code; fall back to manual terminal prompt."""
        from job_auto.utils.gmail import fetch_linkedin_code

        token_path = config.gmail_token_path
        if token_path.exists():
            try:
                logger.info("fetching_linkedin_code_from_gmail")
                return await asyncio.to_thread(fetch_linkedin_code, token_path)
            except TimeoutError:
                logger.warning("gmail_code_fetch_timed_out_falling_back_to_manual")
            except Exception as exc:
                logger.warning("gmail_code_fetch_failed", error=str(exc))

        # Manual fallback
        print("\n" + "=" * 60)
        print("ACTION REQUIRED: LinkedIn sent a security code to your email.")
        print("Enter it in the browser window, then press Enter here.")
        print("=" * 60 + "\n")
        await asyncio.to_thread(input, "Press Enter after completing verification... ")
        return None
