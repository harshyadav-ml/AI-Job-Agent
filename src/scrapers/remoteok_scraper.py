"""
RemoteOK scraper — supports async orchestration (httpx/tenacity) and legacy sync API.

RemoteOK exposes a public REST API that returns active listings.
Client-side keyword and location filtering is applied per RemoteOK guidelines.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from typing import Any, List, Optional

import httpx
import requests
from bs4 import BeautifulSoup
from pydantic import ValidationError

from src.config import get_logger, settings
from src.models import Job
from src.models.job import parse_date
from src.scrapers.base import BaseScraper
from src.utils.retry import async_retry, retry_after_aware_sleep
from src.utils.ua_rotator import build_request_headers

logger = get_logger(__name__)


class RemoteOKScraper(BaseScraper):
    """Scraper for RemoteOK job board using public REST API."""

    _GENERIC_ROLE_WORDS: frozenset[str] = frozenset(
        {
            "developer",
            "engineer",
            "dev",
            "senior",
            "junior",
            "lead",
            "staff",
            "intern",
            "manager",
            "specialist",
            "analyst",
            "architect",
        }
    )

    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self.api_url = settings.REMOTEOK_API_URL
        self._api_url = self.api_url
        self.request_delay = settings.REQUEST_DELAY
        self._request_delay = self.request_delay
        self.timeout = settings.TIMEOUT
        self._timeout = self.timeout
        self.last_request_time = 0.0

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Job-Agent/1.0 (https://github.com/yourusername/job-agent)",
            "Accept": "application/json",
        })

    # ------------------------------------------------------------------ #
    # BaseScraper interface                                                #
    # ------------------------------------------------------------------ #

    @property
    def source_name(self) -> str:
        return "remoteok"

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.head(self.api_url, headers=build_request_headers())
                return r.status_code < 500
        except Exception as exc:
            self.logger.warning("[remoteok] health_check failed: %s", exc)
            return False

    async def scrape(
        self,
        keywords: list[str],
        locations: list[str],
        *,
        max_results: int = 50,
    ) -> list[Job]:
        """Async scrape entry point used by main.py concurrency orchestrator."""
        self.logger.info(
            "[remoteok] scrape started — keywords=%s locations=%s", keywords, locations
        )
        try:
            # Run the scraper logic in a thread to keep requests.Session/mock compatibility
            jobs = await asyncio.to_thread(self.scrape_jobs, keywords, locations)
            return jobs[:max_results]
        except Exception as exc:
            self.logger.error("[remoteok] scrape failed: %s", exc, exc_info=True)
            return []

    async def fetch_full_description(self, url: str) -> str:
        """
        Fetch full job description by making an async HTTP request to the job URL.

        Extracts the entire body text from the main job content container rather than
        relying on a single rigid CSS class. This handles diverse heading structures
        like "About the role", "Roles and Responsibilities", "Requirements".
        """
        if not url:
            return ""

        headers = build_request_headers(
            referer="https://remoteok.com/",
            extra={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        try:
            async with httpx.AsyncClient(timeout=settings.TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200 and resp.text:
                    return self._extract_body_text(resp.text)
        except Exception as exc:
            self.logger.warning("[remoteok] fetch_full_description failed for %s: %s", url, exc)

        return ""

    @staticmethod
    def _extract_body_text(html: str) -> str:
        """
        Extract clean full-body text from a RemoteOK job detail page.

        Strategy: cascading selector waterfall from most specific to broadest,
        stripping nav/header/footer noise. Returns cleaned newline-delimited text.
        """
        if not html:
            return ""
        soup = BeautifulSoup(html, "html.parser")

        # Remove noisy chrome elements before extracting
        for tag in soup.select("nav, header, footer, script, style, [class*='apply'], [class*='sidebar'], [class*='social']"):
            tag.decompose()

        # Ordered waterfall: most-specific first
        candidates = [
            "div.description",
            "td.description",
            "div[class*='description']",
            "div.markdown",
            ".job-description",
            "article",
            "main",
            "body",
        ]
        for sel in candidates:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(separator="\n", strip=True)
                # Collapse blank lines but preserve paragraph breaks
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                if len(text) > 80:
                    return text

        return ""

    # ------------------------------------------------------------------ #
    # Synchronous scraping pipeline                                        #
    # ------------------------------------------------------------------ #

    def scrape_jobs(self, keywords: List[str], locations: List[str]) -> List[Job]:
        """Fetch, filter, and convert RemoteOK jobs."""
        self.logger.info(
            f"Starting RemoteOK scraping for keywords: {keywords}, locations: {locations}"
        )
        try:
            raw_jobs = self._fetch_jobs_from_api()
            filtered_jobs = self._filter_jobs(raw_jobs, keywords, locations)
            jobs: List[Job] = []
            for job_data in filtered_jobs:
                job = self._convert_to_job(job_data)
                if job:
                    jobs.append(job)

            self.logger.info(f"Found {len(jobs)} jobs from RemoteOK after filtering")
            return jobs
        except Exception as e:
            self.logger.error(f"Error during RemoteOK scraping: {e}")
            return []

    def _fetch_jobs_from_api(self) -> List[dict[str, Any]]:
        """Fetch listings from RemoteOK API with rate limiting."""
        self._rate_limit()

        try:
            self.logger.info(f"Fetching jobs from {self.api_url}")
            response = self.session.get(self.api_url, timeout=self.timeout)
            response.raise_for_status()

            data = response.json()
            if len(data) > 1 and "legal" in data[0]:
                jobs_data = data[1:]
            else:
                jobs_data = data

            self.logger.info(f"Fetched {len(jobs_data)} jobs from RemoteOK API")
            return jobs_data
        except requests.exceptions.RequestException as e:
            self.logger.error(f"HTTP error fetching RemoteOK jobs: {e}")
            raise
        except ValueError as e:
            self.logger.error(f"JSON parsing error: {e}")
            raise

    def _rate_limit(self) -> None:
        """Rate limiting between requests."""
        current_time = time.time()
        time_since = current_time - self.last_request_time

        if self.last_request_time > 0 and time_since < self.request_delay:
            sleep_time = self.request_delay - time_since
            self.logger.debug(f"Rate limiting: sleeping for {sleep_time:.2f} seconds")
            time.sleep(sleep_time)

        self.last_request_time = time.time()

    # ------------------------------------------------------------------ #
    # Filtering & normalization                                            #
    # ------------------------------------------------------------------ #

    def _filter_jobs(
        self,
        jobs: List[dict[str, Any]],
        keywords: List[str],
        locations: List[str],
    ) -> List[dict[str, Any]]:
        self.logger.info(
            f"Filtering {len(jobs)} jobs by keywords: {keywords}, locations: {locations}"
        )
        filtered = []
        for job in jobs:
            if self._matches_keywords(job, keywords) and self._matches_location(job, locations):
                filtered.append(job)
        self.logger.info(f"Filtered down to {len(filtered)} jobs")
        return filtered

    def _matches_keywords(self, job: dict[str, Any], keywords: List[str]) -> bool:
        if not keywords:
            return True

        position = str(job.get("position") or "").lower()
        raw_tags = job.get("tags", [])
        if isinstance(raw_tags, (list, tuple, set)):
            tags = [str(tag).lower() for tag in raw_tags]
        else:
            tags = [str(raw_tags).lower()] if raw_tags else []

        description = BeautifulSoup(
            str(job.get("description") or ""), "html.parser"
        ).get_text().lower()

        searchable_text = f"{position} {' '.join(tags)} {description}"

        for keyword in keywords:
            kw = keyword.lower().strip()
            if not kw:
                continue

            if kw in searchable_text:
                return True

            words = [w for w in kw.split() if w]
            specific_words = [w for w in words if len(w) > 2 and w not in self._GENERIC_ROLE_WORDS]
            primary_word = specific_words[0] if specific_words else None

            if words and all(w in searchable_text for w in words):
                if primary_word is None or (
                    primary_word in position or any(primary_word in tag for tag in tags)
                ):
                    return True

            if primary_word and (
                primary_word in position or any(primary_word in tag for tag in tags)
            ):
                return True

            if any(
                w in position or any(w in tag for tag in tags)
                for w in words
                if len(w) > 2 and w not in self._GENERIC_ROLE_WORDS
            ):
                return True

        return False

    def _matches_location(self, job: dict[str, Any], locations: List[str]) -> bool:
        job_location = str(job.get("location") or "").strip().lower()
        requested_locations = [str(loc).strip().lower() for loc in locations if loc]

        raw_tags = job.get("tags", [])
        if isinstance(raw_tags, (list, tuple, set)):
            tags_list = [str(tag).lower() for tag in raw_tags]
            tags_text = " ".join(str(tag) for tag in raw_tags)
        else:
            tags_list = [str(raw_tags).lower()] if raw_tags else []
            tags_text = str(raw_tags or "")

        # 1. Reject explicit regional exclusions in tags
        restricted_tag_patterns = (
            r"\bus\s+only\b",
            r"\bu\.s\.\s+only\b",
            r"\bunited states\s+only\b",
            r"\beurope\s+only\b",
            r"\blatam\b",
            r"\blatin america\s+only\b",
            r"\buk\s+only\b",
            r"\bunited kingdom\s+only\b",
        )
        if any(re.search(pat, tags_text, re.IGNORECASE) for pat in restricted_tag_patterns):
            return False

        # 1b. Reject explicit regional exclusions in description
        description = BeautifulSoup(
            str(job.get("description") or ""), "html.parser"
        ).get_text(separator=" ", strip=True)

        restricted_desc_patterns = (
            r"\bus\s+only\b",
            r"\bu\.s\.\s+only\b",
            r"\bunited states\s+only\b",
            r"\beurope\s+only\b",
            r"\blatam\b",
            r"\buk\s+only\b",
            r"\bwithin\s+germany\b",
            r"\bberlin\s+office\b",
        )
        if any(re.search(pat, description, re.IGNORECASE) for pat in restricted_desc_patterns):
            return False

        # 2. Remote eligibility markers
        remote_markers = ("remote", "worldwide", "anywhere")
        is_marked_remote = (
            not job_location
            or any(marker in job_location for marker in remote_markers)
            or any(marker in tags_list for marker in remote_markers)
        )

        india_locations = (
            "india", "bengaluru", "bangalore", "hyderabad", "pune",
            "gurgaon", "noida", "delhi", "mumbai", "chennai",
        )
        is_india_location = any(loc in job_location for loc in india_locations)

        if not requested_locations:
            return is_marked_remote or is_india_location

        if is_marked_remote:
            return True

        if is_india_location:
            return True

        return any(loc in job_location for loc in requested_locations)

    def _convert_to_job(self, job_data: dict[str, Any]) -> Job:
        """Convert API dictionary to unified Job model."""
        description = self._clean_html(job_data.get("description", ""))
        sal_min = (
            int(job_data["salary_min"])
            if job_data.get("salary_min") and int(job_data["salary_min"]) > 0
            else None
        )
        sal_max = (
            int(job_data["salary_max"])
            if job_data.get("salary_max") and int(job_data["salary_max"]) > 0
            else None
        )
        salary_str = self._format_salary(sal_min or 0, sal_max or 0)
        job_type = self._determine_job_type(job_data.get("tags", []))
        posting_date = self._format_date(str(job_data.get("date", "")))

        url = job_data.get("url") or job_data.get("apply_url") or ""

        # Extract experience from badges / tags if available
        experience_raw: Optional[str] = None
        for tag in job_data.get("tags") or []:
            tag_str = str(tag).strip()
            if re.search(r"\b(\d+[\s\-\+to]*\d*\s*(?:yrs?|years?))\b", tag_str, re.IGNORECASE):
                experience_raw = tag_str
                break
            elif tag_str.lower() in ("junior", "entry level", "entry-level", "intern", "internship"):
                experience_raw = tag_str
                break
            elif tag_str.lower() in ("senior", "lead", "staff", "principal"):
                experience_raw = tag_str
                break

        return Job(
            title=job_data.get("position", ""),
            company=job_data.get("company", ""),
            location=job_data.get("location", "") or "Remote",
            description=description,
            salary=salary_str,
            salary_raw=salary_str if salary_str != "Not specified" else None,
            salary_min=sal_min,
            salary_max=sal_max,
            experience_raw=experience_raw,
            url=url,
            posted_date=posting_date,
            job_type=job_type,
            source="remoteok",
            tags=job_data.get("tags", []),
            company_logo=job_data.get("company_logo", ""),
        )

    def _clean_html(self, html_content: str) -> str:
        """Remove HTML tags and clean up whitespace."""
        if not html_content:
            return ""
        soup = BeautifulSoup(html_content, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        if len(text) > 500:
            text = text[:500] + "..."
        return text.strip()

    def _format_salary(self, salary_min: int, salary_max: int) -> str:
        salary_min = salary_min or 0
        salary_max = salary_max or 0
        if salary_min > 0 and salary_max > 0:
            return f"${salary_min:,} - ${salary_max:,}"
        elif salary_min > 0:
            return f"${salary_min:,}+"
        elif salary_max > 0:
            return f"Up to ${salary_max:,}"
        else:
            return "Not specified"

    def _determine_job_type(self, tags: List[str]) -> str:
        tags_lower = [str(tag).lower() for tag in tags]
        if any(term in tags_lower for term in ("contract", "freelance")):
            return "Contract"
        elif any(term in tags_lower for term in ("part time", "part-time")):
            return "Part-time"
        elif any(term in tags_lower for term in ("internship", "junior")):
            return "Internship"
        else:
            return "Full-time"

    def _format_date(self, date_str: str) -> str:
        if not date_str:
            return ""
        parsed = parse_date(date_str)
        return parsed or date_str
