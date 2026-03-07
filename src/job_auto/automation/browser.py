"""Shared Playwright browser manager with stealth configuration."""

from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from job_auto.config import config
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)

_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1280, "height": 800},
]

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]


async def apply_stealth(page: Page) -> None:
    """Apply stealth patches to evade basic bot detection."""
    # Override navigator properties
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        window.chrome = { runtime: {} };
    """)


async def human_type(page: Page, selector: str, text: str, delay_range: tuple[int, int] = (50, 150)) -> None:
    """Type text with human-like random delays between keystrokes."""
    await page.click(selector)
    await page.evaluate("sel => { document.querySelector(sel).value = ''; }", selector)
    for char in text:
        await page.type(selector, char, delay=random.randint(*delay_range))


async def human_move_and_click(page: Page, selector: str) -> None:
    """Move mouse naturally before clicking."""
    element = await page.query_selector(selector)
    if element:
        box = await element.bounding_box()
        if box:
            # Move to a random point within the element
            x = box["x"] + random.uniform(box["width"] * 0.2, box["width"] * 0.8)
            y = box["y"] + random.uniform(box["height"] * 0.2, box["height"] * 0.8)
            await page.mouse.move(x, y, steps=random.randint(5, 15))
            await asyncio.sleep(random.uniform(0.05, 0.2))
    await page.click(selector)


@asynccontextmanager
async def browser_context(
    playwright: Playwright,
    headless: Optional[bool] = None,
    storage_state_path: Optional[Path] = None,
) -> AsyncGenerator[tuple[Browser, BrowserContext], None]:
    """Create a stealth browser context.

    If storage_state_path is provided and exists, the saved cookies/localStorage
    are loaded so the session is restored.  On exit the current state is written
    back to the same path so future runs inherit it.
    """
    if headless is None:
        headless = config.headless_browser

    viewport = random.choice(_VIEWPORTS)
    user_agent = random.choice(_USER_AGENTS)

    browser = await playwright.chromium.launch(
        headless=headless,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-web-security",
        ],
    )

    context_kwargs: dict = dict(
        viewport=viewport,
        user_agent=user_agent,
        locale="en-US",
        timezone_id="America/New_York",
        permissions=["geolocation"],
        java_script_enabled=True,
    )
    if storage_state_path is not None and storage_state_path.exists():
        context_kwargs["storage_state"] = str(storage_state_path)
        logger.debug("browser_session_restored", path=str(storage_state_path))

    context = await browser.new_context(**context_kwargs)

    # Block tracking/analytics to reduce fingerprinting surface
    async def _block_tracking(route):
        logger.debug("tracking_route_blocked", url=route.request.url)
        await route.abort()

    await context.route(
        "**/(google-analytics|googletagmanager|hotjar|segment).**",
        _block_tracking,
    )

    logger.debug("browser_context_created", viewport=viewport, user_agent=user_agent, headless=headless)
    try:
        yield browser, context
    finally:
        if storage_state_path is not None:
            storage_state_path.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(storage_state_path))
            logger.debug("browser_session_saved", path=str(storage_state_path))
        await context.close()
        await browser.close()


@asynccontextmanager
async def new_page(context: BrowserContext) -> AsyncGenerator[Page, None]:
    """Open a new page with stealth init scripts applied."""
    page = await context.new_page()
    await apply_stealth(page)
    try:
        yield page
    finally:
        await page.close()
