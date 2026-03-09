"""Authenticated LinkedIn scraper using Playwright."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from job_auto.config import config
from job_auto.ingestion.linkedin import _resolve_posted_within, LinkedInScraper
from job_auto.models.job_posting import JobPosting
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)


class LinkedInAuthError(Exception):
    """Raised when LinkedIn authentication fails or credentials are missing."""


class LinkedInPlaywrightScraper:
    """Authenticated LinkedIn scraper.

    Uses Playwright with a persistent session (linkedin_session.json).  On
    first use (or after session expiry) it logs in with the credentials from
    config and saves the session for future runs.

    Usage::

        async with LinkedInPlaywrightScraper() as scraper:
            jobs = await scraper.search("senior python engineer", easy_apply_only=True)
    """

    BASE_SEARCH = "https://www.linkedin.com/jobs/search/"

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return "linkedin.com" in url

    def __init__(self) -> None:
        self._stack = contextlib.AsyncExitStack()
        self._page = None
        self._httpx_scraper: Optional[LinkedInScraper] = None

    async def __aenter__(self) -> "LinkedInPlaywrightScraper":
        from playwright.async_api import async_playwright

        from job_auto.automation.browser import browser_context, new_page
        from job_auto.automation.linkedin import LinkedInApplicator

        pw = await self._stack.enter_async_context(async_playwright())
        _, context = await self._stack.enter_async_context(
            browser_context(pw, storage_state_path=config.linkedin_session_path)
        )
        self._page = await self._stack.enter_async_context(new_page(context))

        # LinkedInApplicator.login() handles session restore, fresh credential
        # login, and security challenge resolution transparently.
        try:
            applicator = LinkedInApplicator(self._page)
            await applicator.login()
        except RuntimeError as exc:
            raise LinkedInAuthError(str(exc)) from exc

        self._httpx_scraper = await self._stack.enter_async_context(LinkedInScraper())

        return self

    async def __aexit__(self, *args) -> None:
        await self._stack.aclose()

    async def parse(self, url: str) -> JobPosting:
        """Fetch a LinkedIn job URL and return a JobPosting via httpx."""
        assert self._httpx_scraper is not None, "Use as async context manager"
        return await self._httpx_scraper.parse(url)

    async def search(
        self,
        query: str,
        location: str = "",
        remote: bool = False,
        limit: int = 20,
        easy_apply_only: bool = False,
        posted_within: str | None = None,
        **kwargs,
    ) -> list[JobPosting]:
        """Search LinkedIn jobs while authenticated.

        When easy_apply_only=True, passes f_LF=f_AL to LinkedIn's search so
        the server filters to Easy Apply jobs before we fetch any detail pages.
        All results from a filtered search are marked easy_apply_available=True
        authoritatively (trusting the server filter).
        """
        assert self._page is not None, "Use as async context manager"

        params: dict[str, str | int] = {
            "keywords": query,
            "location": location or "United States",
            "start": 0,
        }
        if remote:
            params["f_WT"] = 2
        if easy_apply_only:
            params["f_LF"] = "f_AL"
        f_tpr = _resolve_posted_within(posted_within)
        if f_tpr:
            params["f_TPR"] = f_tpr

        # Phase 1: collect all job URLs via Playwright (authenticated search pages only).
        # All Playwright navigation completes before any httpx requests are made,
        # avoiding fingerprint-mismatch detection that occurs when the two clients
        # interleave requests to LinkedIn from the same IP.
        job_urls: list[str] = []
        start = 0

        while len(job_urls) < limit:
            params["start"] = start
            search_url = f"{self.BASE_SEARCH}?{urlencode(params)}"

            await self._page.goto(search_url, wait_until="domcontentloaded")
            try:
                await self._page.wait_for_selector(
                    "div.job-card-container, li.scaffold-layout__list-item",
                    timeout=15_000,
                )
            except Exception:
                logger.warning("linkedin_playwright_no_results_container", url=search_url)
                break

            # Allow JS rendering to settle after domcontentloaded
            await asyncio.sleep(3)

            # Scroll the job list panel incrementally to trigger lazy-loaded cards.
            # The 25 <li> placeholders exist immediately but only populate with card
            # content when scrolled into view via IntersectionObserver. An instant
            # scrollTo(bottom) skips intermediate observers; step-scrolling fires them all.
            # We locate the scroll container by walking up from a known stable child
            # rather than using an obfuscated/hashed class name that changes over time.
            await self._page.evaluate("""
                (async () => {
                    const li = document.querySelector('li.scaffold-layout__list-item');
                    if (!li) return;
                    let panel = li.parentElement;
                    while (panel) {
                        const overflow = window.getComputedStyle(panel).overflowY;
                        if ((overflow === 'auto' || overflow === 'scroll')
                                && panel.scrollHeight > panel.clientHeight) {
                            break;
                        }
                        panel = panel.parentElement;
                    }
                    if (!panel) return;
                    while (panel.scrollTop + panel.clientHeight < panel.scrollHeight) {
                        panel.scrollBy(0, 300);
                        await new Promise(r => setTimeout(r, 100));
                    }
                })()
            """)
            await asyncio.sleep(3)

            html = await self._page.content()
            soup = BeautifulSoup(html, "lxml")

            page_urls = self._extract_card_urls(soup)
            if not page_urls:
                logger.warning("linkedin_playwright_no_cards", url=search_url)
                break

            for url in page_urls:
                if len(job_urls) >= limit:
                    break
                job_urls.append(url)

            start += 25

        # Phase 2: fetch job detail pages via httpx (rate-limited, no Playwright overhead).
        results: list[JobPosting] = []
        for job_url in job_urls:
            try:
                job = await self.parse(job_url)
                if easy_apply_only:
                    # Trust the server-side filter: all returned jobs are Easy Apply.
                    job = job.model_copy(update={"easy_apply_available": True})
                results.append(job)
            except Exception as exc:
                logger.warning(
                    "linkedin_playwright_parse_error", url=job_url, error=str(exc)
                )

        return results

    @staticmethod
    def _extract_card_urls(soup: BeautifulSoup) -> list[str]:
        """Extract job page URLs from authenticated LinkedIn search result cards."""
        seen: set[str] = set()
        urls: list[str] = []

        for link in soup.select(
            "a.job-card-list__title--link, "
            "a.job-card-container__link, "
            "a[data-job-id]"
        ):
            href = link.get("href", "")
            if "/jobs/view/" not in href:
                continue
            full = href if href.startswith("http") else f"https://www.linkedin.com{href}"
            url = full.split("?")[0]
            if url not in seen:
                seen.add(url)
                urls.append(url)

        return urls
