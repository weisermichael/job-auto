"""Generic fallback scraper for arbitrary job posting URLs."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from job_auto.ingestion.base import AbstractScraper
from job_auto.models.job_posting import JobBoard, JobPosting
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)

# Common job description container selectors, in priority order
_DESC_SELECTORS = [
    "[class*='job-description']",
    "[class*='jobDescription']",
    "[class*='description']",
    "[id*='job-description']",
    "[id*='jobDescription']",
    "article",
    "main",
]

_TITLE_SELECTORS = [
    "h1[class*='title']",
    "h1[class*='job']",
    "[class*='job-title']",
    "[class*='jobTitle']",
    "h1",
]

_COMPANY_SELECTORS = [
    "[class*='company']",
    "[class*='employer']",
    "[class*='organization']",
    "[itemprop='hiringOrganization']",
]


class GenericScraper(AbstractScraper):
    @classmethod
    def can_handle(cls, url: str) -> bool:
        """Generic scraper handles any URL as a fallback."""
        return True

    async def parse(self, url: str) -> JobPosting:
        """Best-effort parse of any job posting page."""
        response = await self._get(url)
        soup = BeautifulSoup(response.text, "lxml")

        # Remove noisy elements
        for tag in soup.select("nav, footer, header, script, style, .cookie-banner, .ad"):
            tag.decompose()

        title = self._find_first(soup, _TITLE_SELECTORS)
        company = self._find_first(soup, _COMPANY_SELECTORS)
        description = self._find_first(soup, _DESC_SELECTORS, long=True)

        remote = any(
            w in (description or "").lower()
            for w in ("remote", "work from home", "distributed", "anywhere")
        )

        return JobPosting(
            id=uuid.uuid4().hex[:12],
            title=title or self._extract_title_from_page(soup),
            company=company or self._extract_domain(url),
            board=JobBoard.GENERIC,
            url=url,  # type: ignore[arg-type]
            description=description or "",
            remote=remote,
            easy_apply_available=False,
            date_found=datetime.utcnow(),
        )

    async def search(self, query: str, **kwargs) -> list[JobPosting]:
        """Generic scraper doesn't support search; raise."""
        raise NotImplementedError(
            "GenericScraper does not support search — use board-specific scrapers."
        )

    @staticmethod
    def _find_first(soup: BeautifulSoup, selectors: list[str], long: bool = False) -> str:
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(" ", strip=True)
                if long or len(text) > 10:
                    return text
        return ""

    @staticmethod
    def _extract_title_from_page(soup: BeautifulSoup) -> str:
        title_tag = soup.find("title")
        if title_tag:
            return title_tag.get_text(" ", strip=True).split("|")[0].strip()
        return "Unknown Title"

    @staticmethod
    def _extract_domain(url: str) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.netloc.replace("www.", "")


def get_scraper(url: str, use_playwright: bool = False) -> AbstractScraper:
    """Return the most appropriate scraper for the given URL.

    Pass use_playwright=True to get the authenticated Playwright-based
    LinkedIn scraper instead of the default public httpx scraper.
    """
    from job_auto.ingestion.indeed import IndeedScraper
    from job_auto.ingestion.linkedin import LinkedInScraper
    from job_auto.ingestion.nodesk import NodeskScraper

    if use_playwright and "linkedin.com" in url:
        from job_auto.ingestion.linkedin_playwright import LinkedInPlaywrightScraper
        return LinkedInPlaywrightScraper()  # type: ignore[return-value]

    for scraper_cls in [LinkedInScraper, IndeedScraper, NodeskScraper]:
        if scraper_cls.can_handle(url):
            return scraper_cls()
    return GenericScraper()
