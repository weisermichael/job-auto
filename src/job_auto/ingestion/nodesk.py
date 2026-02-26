"""Nodesk.co remote job board scraper."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from job_auto.ingestion.base import AbstractScraper
from job_auto.models.job_posting import JobBoard, JobPosting
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)


class NodeskScraper(AbstractScraper):
    BASE_URL = "https://nodesk.co"
    REMOTE_JOBS_URL = "https://nodesk.co/remote-jobs/"

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return "nodesk.co" in url

    async def parse(self, url: str) -> JobPosting:
        response = await self._get(url)
        soup = BeautifulSoup(response.text, "lxml")
        return self._extract_job(soup, url)

    async def search(
        self,
        query: str = "",
        limit: int = 20,
        **kwargs,
    ) -> list[JobPosting]:
        """Scrape Nodesk remote job listings."""
        results: list[JobPosting] = []
        page = 1

        while len(results) < limit:
            url = f"{self.REMOTE_JOBS_URL}?page={page}"
            if query:
                from urllib.parse import quote_plus
                url = f"{self.REMOTE_JOBS_URL}?search={quote_plus(query)}&page={page}"

            response = await self._get(url)
            soup = BeautifulSoup(response.text, "lxml")

            cards = soup.select("article.job, li.job-listing, div.job-card")
            if not cards:
                # Try generic listing items
                cards = soup.select("a[href*='/remote-jobs/']")

            if not cards:
                logger.warning("nodesk_no_cards", url=url)
                break

            seen_urls: set[str] = set()
            for card in cards:
                if len(results) >= limit:
                    break
                job_url = self._extract_card_url(card)
                if job_url and job_url not in seen_urls:
                    seen_urls.add(job_url)
                    try:
                        job = await self.parse(job_url)
                        results.append(job)
                    except Exception as e:
                        logger.warning("nodesk_parse_error", url=job_url, error=str(e))
            page += 1

        return results

    def _extract_job(self, soup: BeautifulSoup, url: str) -> JobPosting:
        title = self._text(soup.select_one(
            "h1.job-title, h1.title, h1[itemprop='title'], article h1"
        ))
        company = self._text(soup.select_one(
            "span.company-name, a[itemprop='hiringOrganization'], .company, h2.company"
        ))
        desc_el = soup.select_one(
            "div.job-description, div[itemprop='description'], section.description, article .content"
        )
        description = desc_el.get_text(" ", strip=True) if desc_el else ""

        location_el = soup.select_one(
            "span.location, span[itemprop='jobLocation'], .location, .remote-tag"
        )
        location = self._text(location_el) or "Remote"

        return JobPosting(
            id=uuid.uuid4().hex[:12],
            title=title or "Unknown Title",
            company=company or "Unknown Company",
            board=JobBoard.NODESK,
            url=url,  # type: ignore[arg-type]
            description=description,
            location=location,
            remote=True,  # Nodesk is exclusively remote jobs
            easy_apply_available=False,
            date_found=datetime.utcnow(),
        )

    def _extract_card_url(self, card) -> Optional[str]:
        href = card.get("href", "")
        if not href:
            link = card.select_one("a[href]")
            href = link.get("href", "") if link else ""
        if href.startswith("/"):
            return self.BASE_URL + href
        if href.startswith("http"):
            return href
        return None

    @staticmethod
    def _text(el) -> str:
        if el is None:
            return ""
        return el.get_text(" ", strip=True)
