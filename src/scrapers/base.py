"""
Abstract base scraper — the only interface every platform adapter must satisfy.

Contract rules:
1. ``scrape()`` is async and returns ``list[Job]``.
2. ``scrape()`` must NEVER propagate unhandled exceptions — return ``[]`` on
   total failure so the pipeline orchestrator can continue with other scrapers.
3. ``health_check()`` is a lightweight connectivity probe used by main.py
   before investing in a full scrape.
4. ``source_name`` must be a lowercase, hyphen-free, stable string.

Adding a new platform (e.g. LinkedIn):
    class LinkedInScraper(BaseScraper):
        @property
        def source_name(self) -> str: return "linkedin"

        async def scrape(self, keywords, locations, *, max_results=50): ...

        async def health_check(self) -> bool: ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.models.job import Job


class BaseScraper(ABC):
    """Abstract interface every platform scraper must implement."""

    # ------------------------------------------------------------------ #
    # Required overrides                                                   #
    # ------------------------------------------------------------------ #

    @property
    @abstractmethod
    def source_name(self) -> str:
        """
        Stable lowercase identifier for the platform.

        Examples: ``'naukri'``, ``'remoteok'``, ``'wellfound'``
        """

    @abstractmethod
    async def scrape(
        self,
        keywords: list[str],
        locations: list[str],
        *,
        max_results: int = 50,
    ) -> list[Job]:
        """
        Fetch job listings and return validated ``Job`` objects.

        Args:
            keywords:    Role / skill keywords to search for.
            locations:   City or region filters.  Pass ``[]`` for remote-only.
            max_results: Soft cap on the number of results returned.

        Returns:
            A (possibly empty) list of ``Job`` instances.  Must never raise.
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Lightweight probe to verify the platform endpoint is reachable.

        Returns:
            ``True`` if the endpoint responds with a success status.
        """

    # ------------------------------------------------------------------ #
    # Provided helpers — subclasses may use or override                   #
    # ------------------------------------------------------------------ #

    def scrape_jobs(
        self,
        keywords: list[str],
        locations: list[str],
        *,
        max_results: int = 50,
    ) -> list[Job]:
        """
        Synchronous shim for callers that haven't migrated to async yet.

        .. deprecated::
            Prefer ``await scraper.scrape(...)`` directly.  This shim exists
            only to keep legacy integration tests green during the migration
            window and will be removed in Phase 2.
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Inside an already-running event loop (e.g. Jupyter / tests):
                # schedule and immediately retrieve via a new thread.
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        self.scrape(keywords, locations, max_results=max_results),
                    )
                    return future.result()
            return loop.run_until_complete(
                self.scrape(keywords, locations, max_results=max_results)
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(
                "[%s] scrape_jobs shim failed: %s", self.source_name, exc
            )
            return []
