from abc import ABC, abstractmethod
from typing import Optional

from src.models import Job


class BaseScraper(ABC):
    """Abstract interface that every platform scraper must implement."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Return a lowercase identifier for the platform (e.g. 'naukri')."""
        ...

    @abstractmethod
    def scrape(self, role: str, location: Optional[str] = None) -> list[Job]:
        """Fetch job listings matching *role* (and optional *location*)."""
        ...
