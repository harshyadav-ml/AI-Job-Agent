"""Scrapers module for Job Agent."""

from .naukri_scraper import NaukriScraper
from .remoteok_scraper import RemoteOKScraper
from .wellfound_scraper import WellfoundScraper
from ..models import Job

__all__ = ["NaukriScraper", "RemoteOKScraper", "WellfoundScraper", "Job"]