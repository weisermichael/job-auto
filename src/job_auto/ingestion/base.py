"""Abstract base class for job board scrapers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import httpx

from job_auto.models.job_posting import JobPosting
from job_auto.utils.logging import get_logger
from job_auto.utils.rate_limiter import rate_limiter

logger = get_logger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class AbstractScraper(ABC):
    """
    Base class for job board scrapers.

    Subclasses must implement:
    - `can_handle(url)` — class method returning True if this scraper owns the URL
    - `parse(url)` — fetch + parse a job posting URL → JobPosting
    - `search(query, **kwargs)` — run a keyword search → list[JobPosting]
    """

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "AbstractScraper":
        self._client = httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=30)
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    @classmethod
    @abstractmethod
    def can_handle(cls, url: str) -> bool:
        """Return True if this scraper can handle the given URL."""

    @abstractmethod
    async def parse(self, url: str) -> JobPosting:
        """Fetch and parse a job posting from the given URL."""

    @abstractmethod
    async def search(self, query: str, **kwargs) -> list[JobPosting]:
        """Search for job postings matching the query."""

    async def _get(self, url: str) -> httpx.Response:
        """Rate-limited GET request."""
        assert self._client is not None, "Use as async context manager"
        await rate_limiter.wait(url)
        logger.debug("scraper_get", url=url)
        response = await self._client.get(url)
        response.raise_for_status()
        return response
