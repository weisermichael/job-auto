"""Indeed job posting scraper.

Indeed's search endpoint and job pages are protected by Cloudflare and return
403/CAPTCHA challenges for plain HTTP clients. This scraper uses Playwright
(a real browser with JS execution) which passes those challenges transparently.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from job_auto.ingestion.base import AbstractScraper
from job_auto.models.job_posting import JobBoard, JobPosting
from job_auto.utils.logging import get_logger
from job_auto.utils.rate_limiter import rate_limiter

logger = get_logger(__name__)

_SEARCH_URL = "https://www.indeed.com/jobs"
_CARD_WAIT_SEL = "h2.jobTitle, [data-testid='job-title'], .jobTitle"
_CLOUDFLARE_SEL = "#challenge-running, #cf-challenge-running, #challenge-form"


class IndeedScraper(AbstractScraper):
    """Playwright-based Indeed scraper (bypasses Cloudflare JS challenge)."""

    BASE_URL = "https://www.indeed.com"

    def __init__(self) -> None:
        super().__init__()
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return "indeed.com" in url

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def __aenter__(self) -> "IndeedScraper":
        from job_auto.config import config
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=config.headless_browser,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    # ── public API ───────────────────────────────────────────────────────────

    async def parse(self, url: str) -> JobPosting:
        """Parse an Indeed job posting page via Playwright."""
        page = await self._new_page()
        try:
            await rate_limiter.wait(url)
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await self._wait_past_cloudflare(page)
            # Wait for the JS-rendered job description before snapshotting HTML.
            # Indeed loads job content asynchronously; domcontentloaded fires
            # before the React component fills in the description element.
            try:
                await page.wait_for_selector(
                    "div#jobDescriptionText, [data-testid='jobDescriptionText']",
                    timeout=10_000,
                )
            except Exception:
                pass  # Page may have no description; proceed with what we have
            canonical_url = page.url  # use final URL after any redirect
            html = await page.content()
        finally:
            await page.close()

        soup = BeautifulSoup(html, "lxml")
        return self._extract_job(soup, canonical_url)

    async def search(
        self,
        query: str,
        location: str = "remote",
        remote: bool = True,
        limit: int = 20,
        **kwargs,
    ) -> list[JobPosting]:
        """Scrape Indeed search results via Playwright."""
        results: list[JobPosting] = []
        start = 0

        while len(results) < limit:
            params = f"?q={quote_plus(query)}&l={quote_plus(location)}&start={start}"
            if remote:
                params += "&remotejob=032b3046-06a3-4876-8dfd-474eb5e7ed11"
            search_url = f"{_SEARCH_URL}{params}"

            page = await self._new_page()
            try:
                await rate_limiter.wait(search_url)
                await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
                await self._wait_past_cloudflare(page)

                # Wait for at least one job card
                try:
                    await page.wait_for_selector(_CARD_WAIT_SEL, timeout=10_000)
                except Exception:
                    logger.warning("indeed_no_cards_playwright", url=search_url)
                    await page.close()
                    break

                html = await page.content()
            finally:
                await page.close()

            soup = BeautifulSoup(html, "lxml")
            job_links = self._extract_card_urls(soup)

            if not job_links:
                logger.warning("indeed_no_card_urls", url=search_url)
                break

            for link in job_links:
                if len(results) >= limit:
                    break
                try:
                    job = await self.parse(link)
                    results.append(job)
                except Exception as e:
                    logger.warning("indeed_parse_error", url=link, error=str(e))

            if len(job_links) < 10:
                break  # Last page
            start += 10

        logger.info("indeed_search_done", query=query, found=len(results))
        return results

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _new_page(self) -> Page:
        assert self._context is not None, "Use IndeedScraper as async context manager"
        from job_auto.automation.browser import apply_stealth
        page = await self._context.new_page()
        await apply_stealth(page)
        return page

    async def _wait_past_cloudflare(self, page: Page, timeout_ms: int = 15_000) -> None:
        """Wait while Cloudflare challenge is active; give up after timeout."""
        try:
            await page.wait_for_function(
                f"!document.querySelector('{_CLOUDFLARE_SEL}')",
                timeout=timeout_ms,
            )
        except Exception:
            # Either not Cloudflare or timed out — continue and let the caller
            # handle any resulting parse failures
            logger.debug("cloudflare_wait_skipped", url=page.url)

    def _extract_card_urls(self, soup: BeautifulSoup) -> list[str]:
        """Pull job URLs from an Indeed search results page."""
        urls: list[str] = []
        seen: set[str] = set()
        for a in soup.select("h2.jobTitle a, [data-testid='job-title'] a, .jobTitle a"):
            href = a.get("href", "")
            if not href:
                continue
            full = self.BASE_URL + href if href.startswith("/") else href
            # Normalise: strip tracking params after the job key
            full = re.sub(r"&from=.*", "", full)
            if full not in seen:
                seen.add(full)
                urls.append(full)
        return urls

    def _extract_job(self, soup: BeautifulSoup, url: str) -> JobPosting:
        title = self._text(soup.select_one(
            "h1.jobsearch-JobInfoHeader-title, h1[data-testid='jobTitle'], h1.css-1vg6q84"
        ))
        company = self._text(soup.select_one(
            "div.jobsearch-InlineCompanyRating-companyHeader a, "
            "[data-testid='inlineHeader-companyName'] a, .css-1ioi40n"
        ))
        location = self._text(soup.select_one(
            "div.icl-u-xs-mt--xs.icl-u-textColor--secondary, "
            "[data-testid='job-location'], .css-6z8o9s"
        ))
        desc_el = soup.select_one(
            "div#jobDescriptionText, div.jobsearch-jobDescriptionText, "
            "[data-testid='jobDescriptionText']"
        )
        description = desc_el.get_text(" ", strip=True) if desc_el else ""

        quick_apply = bool(soup.select_one(
            "button#indeedApplyButton, [data-testid='indeedApplyButton'], .indeed-apply-button"
        ))
        remote = any(
            w in (location or "").lower() or w in description[:500].lower()
            for w in ("remote", "work from home", "wfh", "distributed")
        )
        salary_min, salary_max = self._parse_salary(
            self._text(soup.select_one(
                "#salaryInfoAndJobType span, [data-testid='attribute_snippet_testid'], .css-2iqe2o"
            )) or description
        )

        return JobPosting(
            id=uuid.uuid4().hex[:12],
            title=title or "Unknown Title",
            company=company or "Unknown Company",
            board=JobBoard.INDEED,
            url=url,  # type: ignore[arg-type]
            description=description,
            location=location,
            remote=remote,
            easy_apply_available=quick_apply,
            salary_min=salary_min,
            salary_max=salary_max,
            date_found=datetime.utcnow(),
        )

    @staticmethod
    def _text(el) -> str:
        return el.get_text(" ", strip=True) if el else ""

    @staticmethod
    def _parse_salary(text: str) -> tuple[Optional[int], Optional[int]]:
        m = re.search(
            r"\$(\d{1,3}(?:,\d{3})*(?:\.\d+)?)[Kk]?\s*(?:[-–—a-z]+)\s*"
            r"\$(\d{1,3}(?:,\d{3})*(?:\.\d+)?)[Kk]?",
            text,
        )
        if m:
            def parse_num(s: str) -> int:
                n = float(s.replace(",", ""))
                return int(n * 1000 if n < 1000 else n)
            try:
                return parse_num(m.group(1)), parse_num(m.group(2))
            except ValueError:
                pass
        return None, None
