"""Wellfound scraper using Firecrawl with full unit test compatibility."""

import logging
import os
import re
from typing import Optional, List, Any
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from src.models import Job
from src.scrapers.base import BaseScraper
from ..config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://wellfound.com"


class WellfoundScraper(BaseScraper):
    def __init__(self, firecrawl: Optional[Any] = None) -> None:
        load_dotenv()
        self.api_key = os.environ.get("FIRECRAWL_API_KEY", "").strip() or getattr(settings, "FIRECRAWL_API_KEY", "")
        self._custom_firecrawl = firecrawl

    @property
    def source_name(self) -> str:
        return "wellfound"

    @property
    def firecrawl(self):
        if self._custom_firecrawl is not None:
            return self._custom_firecrawl
        if not self.api_key:
            return None
        try:
            from firecrawl import V1FirecrawlApp
            return V1FirecrawlApp(api_key=self.api_key)
        except ImportError:
            try:
                from firecrawl import FirecrawlApp
                return FirecrawlApp(api_key=self.api_key)
            except ImportError:
                return None

    @firecrawl.setter
    def firecrawl(self, value):
        self._custom_firecrawl = value

    @staticmethod
    def _slugify(text: str) -> str:
        clean = re.sub(r"[^a-zA-Z0-9\s-]", "", text.strip().lower())
        return re.sub(r"[\s_-]+", "-", clean)

    def _build_search_url(self, role: str, location: Optional[str] = None) -> str:
        role_slug = self._slugify(role)
        if location:
            loc_slug = self._slugify(location)
            if loc_slug in ("bangalore", "bengaluru"):
                loc_slug = "bangalore"
            return f"{BASE_URL}/role/l/{role_slug}/{loc_slug}"
        return f"{BASE_URL}/role/r/{role_slug}"

    def _build_search_urls(self, role: str, location: Optional[str] = None) -> List[str]:
        urls = [self._build_search_url(role, location)]
        role_slug = self._slugify(role)
        fallback = f"{BASE_URL}/role/r/{role_slug}"
        if fallback not in urls:
            urls.append(fallback)
        return urls

    @staticmethod
    def _parse_jobs_from_html(html_text: str, search_location: Optional[str] = None) -> List[Job]:
        if not html_text:
            return []
        soup = BeautifulSoup(html_text, "html.parser")
        jobs: List[Job] = []
        for card in soup.find_all(attrs={"data-testid": "job-card"}):
            title_el = card.find(["h2", "a"])
            title = title_el.get_text(strip=True) if title_el else ""
            comp_el = card.find(["h4", "span"])
            company = comp_el.get_text(strip=True) if comp_el else "Startup (Wellfound)"
            link = card.find("a", href=True)
            href = link["href"] if link else ""
            if href and not href.startswith("http"):
                href = f"{BASE_URL}{href}"

            # Extract salary if present in text (e.g. ₹12,00,000)
            card_text = card.get_text(" ", strip=True)
            sal_match = re.search(r"([₹$€£][\d,]+(?:\s*-\s*[₹$€£][\d,]+)?)", card_text)
            salary = sal_match.group(1) if sal_match else "Not specified"

            loc = search_location.title() if search_location else "Remote"
            jobs.append(
                Job(
                    title=title or "Software Engineer",
                    company=company or "Startup (Wellfound)",
                    location=loc,
                    salary=salary,
                    description="",
                    url=href,
                    source="wellfound",
                    job_type="Full-time",
                )
            )
        return jobs

    @staticmethod
    def _parse_jobs_from_markdown(md: str, search_location: Optional[str] = None) -> List[Job]:
        if not md:
            return []
        lowered = md.lower()
        if any(k in lowered for k in ("verify you are human", "access-restriction", "challenge-platform")):
            return []

        jobs: List[Job] = []
        lines = md.splitlines()
        link_re = re.compile(r"\[([^\]]+)\]\((https?://[^\)]*wellfound\.com/[^\)]*)\)", re.IGNORECASE)

        for i, line in enumerate(lines):
            matches = link_re.findall(line)
            for text, href in matches:
                if "/jobs/" in href.lower():
                    title = text.strip().strip("*# ")
                    company = None

                    # Check surrounding lines for company link
                    start = max(0, i - 2)
                    end = min(len(lines), i + 3)
                    for neighbor_line in lines[start:end]:
                        comp_match = re.search(r"\[([^\]]+)\]\(https?://[^\)]*wellfound\.com/company/[^\)]+\)", neighbor_line, re.IGNORECASE)
                        if comp_match:
                            company = comp_match.group(1).strip()
                            break

                    comp_name = company or "Startup (Wellfound)"
                    loc = search_location.title() if search_location else "Remote"
                    job_type = "Internship" if "intern" in title.lower() else "Full-time"
                    jobs.append(
                        Job(
                            title=title,
                            company=comp_name,
                            location=loc,
                            salary="Not specified",
                            description="",
                            url=href,
                            source="wellfound",
                            job_type=job_type,
                        )
                    )
        return jobs

    def scrape_jobs(self, keywords: List[str], locations: List[str]) -> List[Job]:
        # If firecrawl client was explicitly set to None or not provided without key
        if self._custom_firecrawl is None and not self.api_key:
            return []
        client = self.firecrawl
        if not client:
            return []

        role = keywords[0] if keywords else "software-engineer"
        loc = locations[0] if locations else None
        target_urls = self._build_search_urls(role, loc)

        for url in target_urls:
            try:
                try:
                    res = client.scrape_url(url, formats=["markdown", "html"], wait_for=5000)
                except TypeError:
                    res = client.scrape_url(url)
            except Exception as exc:
                logger.warning("Firecrawl failure: %s", exc)
                continue

            md = getattr(res, "markdown", "") or (res.get("markdown", "") if isinstance(res, dict) else "")
            html = getattr(res, "html", "") or (res.get("html", "") if isinstance(res, dict) else "")

            if any(term in (md + html).lower() for term in ("verify you are human", "access-restriction")):
                return []

            if html and "<article" in html:
                jobs = self._parse_jobs_from_html(html, search_location=loc)
                if jobs:
                    return jobs

            if md:
                jobs = self._parse_jobs_from_markdown(md, search_location=loc)
                if jobs:
                    return jobs

        return []

    def scrape(self, role: str, location: Optional[str] = None) -> List[Job]:
        return self.scrape_jobs([role], [location] if location else [])
