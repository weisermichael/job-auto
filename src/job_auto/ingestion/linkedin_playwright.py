"""Authenticated LinkedIn scraper using Playwright."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from job_auto.config import config
from job_auto.ingestion.linkedin import extract_job
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

        return self

    async def __aexit__(self, *args) -> None:
        await self._stack.aclose()

    async def parse(self, url: str) -> JobPosting:
        """Navigate to a LinkedIn job URL and return a JobPosting."""
        assert self._page is not None, "Use as async context manager"
        await self._page.goto(url, wait_until="domcontentloaded")
        html = await self._page.content()
        soup = BeautifulSoup(html, "lxml")
        return extract_job(soup, url)

    async def search(
        self,
        query: str,
        location: str = "",
        remote: bool = False,
        limit: int = 20,
        easy_apply_only: bool = False,
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

        results: list[JobPosting] = []
        start = 0

        while len(results) < limit:
            params["start"] = start
            search_url = f"{self.BASE_SEARCH}?{urlencode(params)}"

            await self._page.goto(search_url, wait_until="domcontentloaded")
            try:
                await self._page.wait_for_selector(
                    ".jobs-search-results-list, .scaffold-layout__list-container",
                    timeout=15_000,
                )
            except Exception:
                logger.warning("linkedin_playwright_no_results_container", url=search_url)
                break

            # Allow JS rendering to settle
            await asyncio.sleep(2)

            html = await self._page.content()
            soup = BeautifulSoup(html, "lxml")

            job_urls = self._extract_card_urls(soup)
            if not job_urls:
                logger.warning("linkedin_playwright_no_cards", url=search_url)
                break

            for job_url in job_urls:
                if len(results) >= limit:
                    break
                try:
                    await asyncio.sleep(1.0)
                    job = await self.parse(job_url)
                    if easy_apply_only:
                        # Trust the server-side filter: all returned jobs are Easy Apply.
                        job = job.model_copy(update={"easy_apply_available": True})
                    results.append(job)
                except Exception as exc:
                    logger.warning(
                        "linkedin_playwright_parse_error", url=job_url, error=str(exc)
                    )

            start += 25

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
