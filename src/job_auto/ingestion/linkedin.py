"""LinkedIn job posting scraper + Easy Apply detector."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode, urlparse

from bs4 import BeautifulSoup

from job_auto.ingestion.base import AbstractScraper
from job_auto.models.job_posting import ExperienceLevel, JobBoard, JobPosting
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)

_LEVEL_MAP: dict[str, ExperienceLevel] = {
    "internship": ExperienceLevel.INTERN,
    "entry level": ExperienceLevel.ENTRY,
    "associate": ExperienceLevel.ENTRY,
    "mid-senior level": ExperienceLevel.MID,
    "mid level": ExperienceLevel.MID,
    "senior": ExperienceLevel.SENIOR,
    "director": ExperienceLevel.DIRECTOR,
    "executive": ExperienceLevel.PRINCIPAL,
}


class LinkedInScraper(AbstractScraper):
    BASE_SEARCH = "https://www.linkedin.com/jobs/search/"
    BASE_JOB = "https://www.linkedin.com/jobs/view/"

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return "linkedin.com" in url

    async def parse(self, url: str) -> JobPosting:
        """Parse a LinkedIn job posting page."""
        response = await self._get(url)
        soup = BeautifulSoup(response.text, "lxml")
        return self._extract_job(soup, url)

    async def search(
        self,
        query: str,
        location: str = "",
        remote: bool = False,
        limit: int = 20,
        **kwargs,
    ) -> list[JobPosting]:
        """Search LinkedIn jobs (public listing page, no auth required)."""
        params: dict[str, str | int] = {
            "keywords": query,
            "location": location or "United States",
            "start": 0,
        }
        if remote:
            params["f_WT"] = 2  # remote filter

        results: list[JobPosting] = []
        start = 0
        while len(results) < limit:
            params["start"] = start
            url = f"{self.BASE_SEARCH}?{urlencode(params)}"
            response = await self._get(url)
            soup = BeautifulSoup(response.text, "lxml")

            cards = soup.select("li.jobs-search__results-list > div")
            if not cards:
                # Try alternate selector for public listing
                cards = soup.select("ul.jobs-search__results-list li")

            if not cards:
                logger.warning("linkedin_no_cards", url=url)
                break

            for card in cards:
                if len(results) >= limit:
                    break
                job_url = self._extract_card_url(card)
                if job_url:
                    try:
                        job = await self.parse(job_url)
                        results.append(job)
                    except Exception as e:
                        logger.warning("linkedin_parse_error", url=job_url, error=str(e))
            start += 25

        return results

    def _extract_job(self, soup: BeautifulSoup, url: str) -> JobPosting:
        title = self._text(soup.select_one("h1.top-card-layout__title, h1.jobs-unified-top-card__job-title"))
        company = self._text(soup.select_one(
            "a.topcard__org-name-link, span.jobs-unified-top-card__company-name a, "
            ".jobs-unified-top-card__company-name"
        ))
        location_el = soup.select_one(
            "span.topcard__flavor--bullet, span.jobs-unified-top-card__bullet, "
            ".jobs-unified-top-card__workplace-type"
        )
        location = self._text(location_el)

        description_el = soup.select_one(
            "div.show-more-less-html__markup, div.jobs-description-content__text"
        )
        description = description_el.get_text(" ", strip=True) if description_el else ""

        # Easy Apply detection
        easy_apply = bool(
            soup.select_one(
                "button.jobs-apply-button, "
                "[data-control-name='jobdetails_topcard_inapply'], "
                ".jobs-apply-button--top-card"
            )
        )

        # Experience level
        level_el = soup.select_one(".jobs-unified-top-card__job-insight span, .description__job-criteria-text")
        level_text = self._text(level_el).lower()
        level = ExperienceLevel.UNKNOWN
        for key, val in _LEVEL_MAP.items():
            if key in level_text:
                level = val
                break

        remote = any(
            word in (location or "").lower()
            for word in ("remote", "anywhere", "distributed")
        )

        salary_min, salary_max = self._parse_salary(description)

        return JobPosting(
            id=uuid.uuid4().hex[:12],
            title=title or "Unknown Title",
            company=company or "Unknown Company",
            board=JobBoard.LINKEDIN,
            url=url,  # type: ignore[arg-type]
            description=description,
            location=location,
            remote=remote,
            experience_level=level,
            easy_apply_available=easy_apply,
            salary_min=salary_min,
            salary_max=salary_max,
            date_found=datetime.utcnow(),
        )

    def _extract_card_url(self, card) -> Optional[str]:
        link = card.select_one("a.base-card__full-link, a[data-tracking-control-name='public_jobs_jserp-card_search-card']")
        if link and link.get("href"):
            return link["href"].split("?")[0]
        return None

    @staticmethod
    def _text(el) -> str:
        if el is None:
            return ""
        return el.get_text(" ", strip=True)

    @staticmethod
    def _parse_salary(text: str) -> tuple[Optional[int], Optional[int]]:
        patterns = [
            r"\$(\d{1,3}(?:,\d{3})*(?:\.\d+)?)[Kk]?\s*[-–—to]+\s*\$(\d{1,3}(?:,\d{3})*(?:\.\d+)?)[Kk]?",
            r"\$(\d{1,3}(?:,\d{3})*)[Kk]?\s*[-–—/]",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                def parse_num(s: str) -> int:
                    n = float(s.replace(",", ""))
                    if n < 1000:
                        n *= 1000
                    return int(n)
                try:
                    lo = parse_num(m.group(1))
                    hi = parse_num(m.group(2)) if m.lastindex and m.lastindex >= 2 else lo
                    return lo, hi
                except (ValueError, IndexError):
                    pass
        return None, None
