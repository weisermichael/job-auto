"""Shared Playwright browser manager with stealth configuration."""

from __future__ import annotations

import asyncio
import json
import os
import random
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, Playwright

from job_auto.config import config
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--disable-web-security",
]

# WSL2 Wayland socket — Chrome needs this for headed mode when WAYLAND_DISPLAY isn't set.
# Evaluated once at import time; uses the real uid so it works for any user, not just uid 1000.
_WAYLAND_SOCKET = Path(f"/run/user/{os.getuid()}/wayland-0")
_WAYLAND_SOCKET_EXISTS: bool = _WAYLAND_SOCKET.exists()


def _headed_launch_args_and_env(base_args: list[str]) -> tuple[list[str], dict]:
    """Return (args, extra_env) for headed Chromium when Wayland is available.

    Chrome needs --ozone-platform=wayland to use Wayland even when WAYLAND_DISPLAY
    is set.  If WAYLAND_DISPLAY is not set but the WSLg socket exists, we inject
    it into the subprocess env as well.
    """
    if os.environ.get("WAYLAND_DISPLAY"):
        return base_args + ["--ozone-platform=wayland"], {}
    if _WAYLAND_SOCKET_EXISTS:
        logger.debug("wsl2_wayland_injected", socket=str(_WAYLAND_SOCKET))
        return (
            base_args + ["--ozone-platform=wayland"],
            {**os.environ, "WAYLAND_DISPLAY": str(_WAYLAND_SOCKET)},
        )
    return base_args, {}


async def _do_launch(playwright: Playwright, headless: bool) -> Browser:
    """Launch Chromium with stealth args, injecting Wayland env when headed."""
    if not headless:
        args, env = _headed_launch_args_and_env(_LAUNCH_ARGS)
        return await playwright.chromium.launch(headless=False, args=args, env=env or None)
    return await playwright.chromium.launch(headless=True, args=_LAUNCH_ARGS)


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


def _load_or_create_fingerprint(fingerprint_path: Path) -> tuple[dict, str]:
    """Return (viewport, user_agent), loading from disk or creating and saving a fresh one."""
    if fingerprint_path.exists():
        try:
            data = json.loads(fingerprint_path.read_text())
            return data["viewport"], data["user_agent"]
        except (json.JSONDecodeError, KeyError):
            pass  # fall through to regenerate
    viewport = random.choice(_VIEWPORTS)
    user_agent = random.choice(_USER_AGENTS)
    fingerprint_path.parent.mkdir(parents=True, exist_ok=True)
    fingerprint_path.write_text(json.dumps({"viewport": viewport, "user_agent": user_agent}))
    logger.info("linkedin_fingerprint_created", path=str(fingerprint_path))
    return viewport, user_agent


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
async def launch_browser(playwright: Playwright, headless: bool | None = None) -> AsyncGenerator[Browser, None]:
    """Launch a browser and close it on exit (no context)."""
    if headless is None:
        headless = config.headless_browser
    browser = await _do_launch(playwright, headless)
    try:
        yield browser
    finally:
        await browser.close()


@asynccontextmanager
async def browser_context(
    playwright_or_browser: Playwright | Browser,
    headless: bool | None = None,
    storage_state_path: Path | None = None,
    fingerprint_path: Path | None = None,
) -> AsyncGenerator[tuple[Browser, BrowserContext], None]:
    """Create a stealth browser context.

    Accepts either a Playwright instance (launches and owns a new browser) or
    an existing Browser instance (uses it directly, does not close it on exit).

    If storage_state_path is provided and exists, the saved cookies/localStorage
    are loaded so the session is restored.  On exit the current state is written
    back to the same path so future runs inherit it.
    """
    if isinstance(playwright_or_browser, Browser):
        browser = playwright_or_browser
        own_browser = False
    else:
        if headless is None:
            headless = config.headless_browser
        browser = await _do_launch(playwright_or_browser, headless)
        own_browser = True

    if fingerprint_path is not None:
        viewport, user_agent = _load_or_create_fingerprint(fingerprint_path)
    else:
        viewport = random.choice(_VIEWPORTS)
        user_agent = random.choice(_USER_AGENTS)

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
        if own_browser:
            await browser.close()


@asynccontextmanager
async def linkedin_persistent_context(
    playwright: Playwright,
    headless: bool | None = None,
) -> AsyncGenerator[BrowserContext, None]:
    """Launch a persistent Chromium profile for LinkedIn.

    Uses launch_persistent_context so that service workers, caches, and
    IndexedDB survive across runs, making sessions far more durable than
    storage_state alone.
    """
    if headless is None:
        headless = config.headless_browser

    viewport, user_agent = _load_or_create_fingerprint(config.linkedin_fingerprint_path)

    profile_dir = config.linkedin_profile_path
    profile_dir.mkdir(parents=True, exist_ok=True)
    is_new_profile = not any(profile_dir.iterdir())

    args = _LAUNCH_ARGS
    launch_kwargs: dict = dict(
        headless=headless,
        viewport=viewport,
        user_agent=user_agent,
        locale="en-US",
        timezone_id="America/New_York",
        permissions=["geolocation"],
        java_script_enabled=True,
    )
    if not headless:
        args, env = _headed_launch_args_and_env(_LAUNCH_ARGS)
        launch_kwargs["env"] = env or None
    launch_kwargs["args"] = args

    context = await playwright.chromium.launch_persistent_context(
        str(profile_dir), **launch_kwargs
    )

    async def _block_tracking(route):
        logger.debug("tracking_route_blocked", url=route.request.url)
        await route.abort()

    await context.route(
        "**/(google-analytics|googletagmanager|hotjar|segment).**",
        _block_tracking,
    )

    # One-time migration: import existing storage_state cookies into fresh profile
    if is_new_profile and config.linkedin_session_path.exists():
        try:
            old_state = json.loads(config.linkedin_session_path.read_text())
            cookies = old_state.get("cookies", [])
            if cookies:
                await context.add_cookies(cookies)
                logger.info("linkedin_session_migrated", cookies=len(cookies))
        except Exception as exc:
            logger.warning("linkedin_session_migration_failed", error=str(exc))

    logger.debug("linkedin_persistent_context_created", profile=str(profile_dir))
    try:
        yield context
    finally:
        await context.close()


@asynccontextmanager
async def new_page(context: BrowserContext) -> AsyncGenerator[Page, None]:
    """Open a new page with stealth init scripts applied."""
    page = await context.new_page()
    await apply_stealth(page)
    try:
        yield page
    finally:
        await page.close()
