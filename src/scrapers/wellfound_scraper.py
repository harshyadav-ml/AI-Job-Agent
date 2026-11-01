"""
Wellfound scraper using Firecrawl with full unit test and async compatibility.

Wellfound is a React SPA with Cloudflare protection. Firecrawl renders JavaScript
and returns rendered DOM / markdown.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, List, Optional

from bs4 import BeautifulSoup
from dotenv import load_dotenv

from src.config import settings
from src.models import Job
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://wellfound.com"


class WellfoundScraper(BaseScraper):
    """Scraper for Wellfound job board via Firecrawl managed-browser service."""

    def __init__(self, firecrawl: Optional[Any] = None) -> None:
        self.api_key = getattr(settings, "FIRECRAWL_API_KEY", "") or os.environ.get("FIRECRAWL_API_KEY", "").strip()
        self._custom_firecrawl = firecrawl
        self._firecrawl_explicitly_set = firecrawl is not None

    # ------------------------------------------------------------------ #
    # BaseScraper interface                                                #
    # ------------------------------------------------------------------ #

    @property
    def source_name(self) -> str:
        return "wellfound"

    async def health_check(self) -> bool:
        return bool(self.api_key or self._firecrawl_explicitly_set)

    async def scrape(
        self,
        keywords: list[str],
        locations: list[str],
        *,
        max_results: int = 50,
    ) -> list[Job]:
        """Async scrape entry point used by main.py concurrency orchestrator."""
        try:
            jobs = await asyncio.to_thread(self.scrape_jobs, keywords, locations)
            return jobs[:max_results]
        except Exception as exc:
            logger.error("[wellfound] scrape failed: %s", exc, exc_info=True)
            return []

    # ------------------------------------------------------------------ #
    # Firecrawl client property                                           #
    # ------------------------------------------------------------------ #

    @property
    def firecrawl(self):
        if self._firecrawl_explicitly_set:
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
        self._firecrawl_explicitly_set = True

    # ------------------------------------------------------------------ #
    # Search URL construction                                              #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # HTML Parsing (strict: drops cards missing titles)                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_jobs_from_html(html_text: str, search_location: Optional[str] = None) -> List[Job]:
        if not html_text:
            return []
        soup = BeautifulSoup(html_text, "html.parser")
        jobs: List[Job] = []

        for card in soup.find_all(attrs={"data-testid": "job-card"}):
            # 1. Title: strict search - never fabricate default title
            title_el = card.select_one('[data-testid="job-title"], h2[class*="title"], h2, a[class*="title"]')
            if not title_el:
                title_el = card.find(["h2", "a"])
            title = title_el.get_text(strip=True) if title_el else ""

            if not title:
                logger.warning("[wellfound-html] dropping card without title")
                continue

            # 2. Company name: check specific selector, company link, or heading
            comp_el = card.select_one('[data-testid="company-name"], a[href*="/company/"]')
            if not comp_el:
                comp_el = card.find(["h4"])
            company = comp_el.get_text(strip=True) if comp_el else "Startup (Wellfound)"

            # 3. URL
            link = card.find("a", href=True)
            href = link["href"] if link else ""
            if href and not href.startswith("http"):
                href = f"{BASE_URL}{href}"
            if not href:
                logger.warning("[wellfound-html] dropping card '%s' without URL", title)
                continue

            # 4. Salary extraction from text if present
            card_text = card.get_text(" ", strip=True)
            sal_match = re.search(r"([₹$€£][\d,]+(?:\s*[-–to]+\s*[₹$€£]?[\d,]+)?)", card_text)
            salary = sal_match.group(1) if sal_match else "Not specified"

            loc = search_location.title() if search_location else "Remote"
            job_type = "Internship" if "intern" in title.lower() else "Full-time"

            jobs.append(
                Job(
                    title=title,
                    company=company,
                    location=loc,
                    salary=salary,
                    salary_raw=salary if salary != "Not specified" else None,
                    description="",
                    url=href,
                    source="wellfound",
                    job_type=job_type,
                )
            )
        return jobs

    # ------------------------------------------------------------------ #
    # Markdown Parsing (Firecrawl format)                                  #
    # ------------------------------------------------------------------ #

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
                    if not title:
                        continue
                    company = None

                    # Check surrounding lines for company link
                    start = max(0, i - 2)
                    end = min(len(lines), i + 3)
                    for neighbor_line in lines[start:end]:
                        comp_match = re.search(
                            r"\[([^\]]+)\]\(https?://[^\)]*wellfound\.com/company/[^\)]+\)",
                            neighbor_line,
                            re.IGNORECASE,
                        )
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
                            salary_raw=None,
                            description="",
                            url=href,
                            source="wellfound",
                            job_type=job_type,
                        )
                    )
        return jobs

    # ------------------------------------------------------------------ #
    # Scrape execution                                                     #
    # ------------------------------------------------------------------ #

    def scrape_jobs(self, keywords: List[str], locations: List[str]) -> List[Job]:
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
