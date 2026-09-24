"""RemoteOK scraper backed by the platform's public JSON API."""

import logging
import re
from datetime import datetime
from html import unescape
from typing import Optional, List, Any
import requests
from bs4 import BeautifulSoup

from src.models import Job
from src.scrapers.base import BaseScraper
from ..config import settings

logger = logging.getLogger(__name__)

API_URL = getattr(settings, "REMOTEOK_API_URL", "https://remoteok.com/api")
USER_AGENT = "JobAgent/1.0 (https://github.com/job-agent)"

EXCLUDED_TITLE_KEYWORDS = (
    "marketing", "sales", "business development", "customer service",
    "customer support", "voice over", "artist", "drilling", "surveyor",
    "clinical", "technician", "scheduler", "legal", "recruiter", "accountant"
)


class RemoteOKScraper(BaseScraper):
    """Scrape remote-only jobs from RemoteOK without location filtering."""

    def __init__(self) -> None:
        self.api_url = API_URL
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.request_delay = getattr(settings, "REQUEST_DELAY", 0)

    @property
    def source_name(self) -> str:
        return "remoteok"

    @staticmethod
    def _matches(item: dict, role: str) -> bool:
        requested_role = re.sub(r"\s+", " ", str(role or "")).strip().lower()
        position = re.sub(r"\s+", " ", str(item.get("position") or "")).strip().lower()
        raw_tags = item.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        tags = [str(tag).strip().lower() for tag in raw_tags]
        searchable = " ".join([position, *tags])

        if not requested_role or not position:
            return False

        # Exclude non-tech roles
        if any(neg in position for neg in EXCLUDED_TITLE_KEYWORDS):
            return False

        # Software Engineer / Developer queries
        if any(term in requested_role for term in ("software engineer", "software developer", "engineer", "developer")):
            swe_patterns = (
                r"\bsoftware\s*(?:engineer|developer)\b",
                r"\b(?:backend|frontend|full[\s-]?stack)\b",
                r"\b(?:golang|python|react|node|java|\.net|c#|c\+\+|mobile|ios|android)\s*(?:engineer|developer)?\b",
                r"\b(?:ai|ml|data|systems?|infrastructure|platform|cloud|devops|sre)\s+(?:engineer|architect)\b",
                r"\b(?:staff|principal|lead|senior|junior)\s+(?:software\s+)?(?:engineer|developer)\b",
            )
            return any(re.search(pat, searchable) for pat in swe_patterns)

        # AI / ML queries
        if any(term in requested_role for term in ("ai", "machine learning", "ml", "deep learning")):
            ai_patterns = (
                r"\bai\b", r"\bmachine learning\b", r"\bml\b", r"\bllm\b",
                r"\bnlp\b", r"\bcomputer vision\b", r"\bartificial intelligence\b",
                r"\bdeep learning\b"
            )
            return any(re.search(pat, searchable) for pat in ai_patterns)

        # Exact match fallback
        return requested_role in position or any(requested_role in tag for tag in tags)

    def _fetch_jobs_from_api(self) -> List[dict]:
        response = self.session.get(self.api_url, timeout=getattr(settings, "TIMEOUT", 15))
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError("RemoteOK API response must be a list")
        return [item for item in data[1:] if isinstance(item, dict)]

    def _filter_jobs(
        self,
        items: List[dict],
        keywords: List[str],
        locations: Optional[List[str]] = None,
    ) -> List[dict]:
        """Filter by role only. Location is deliberately ignored."""
        del locations
        role = keywords[0] if keywords else ""
        return [item for item in items if self._matches(item, role)]

    @staticmethod
    def _clean_html(value: Any) -> str:
        text = BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", unescape(text)).strip()
        return text[:500] + ("..." if len(text) > 500 else "")

    @staticmethod
    def _format_salary(salary_min: Any, salary_max: Any) -> str:
        try:
            minimum = int(salary_min or 0)
            maximum = int(salary_max or 0)
        except (TypeError, ValueError):
            return "Not specified"
        if minimum and maximum:
            return f"${minimum:,} - ${maximum:,}"
        if minimum:
            return f"${minimum:,}+"
        if maximum:
            return f"Up to ${maximum:,}"
        return "Not specified"

    @staticmethod
    def _format_date(value: Any) -> str:
        date_value = str(value or "")
        if "T" not in date_value:
            return date_value
        try:
            return datetime.fromisoformat(date_value.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return date_value.split("T", 1)[0]

    @staticmethod
    def _determine_job_type(tags: List[str]) -> str:
        searchable = " ".join(str(tag).lower() for tag in tags)
        if any(term in searchable for term in ("intern", "trainee", "junior")):
            return "Internship"
        if "part time" in searchable or "part-time" in searchable:
            return "Part-time"
        if "contract" in searchable:
            return "Contract"
        return "Full-time"

    @staticmethod
    def _convert_to_job(item: dict) -> Job:
        position = str(item.get("position") or "").strip()
        raw_tags = item.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        tags = [str(tag).strip().lower() for tag in raw_tags]

        job_kwargs = {
            "title": position,
            "company": str(item.get("company") or "N/A").strip(),
            "location": str(item.get("location") or "").strip() or "Remote",
            "salary": RemoteOKScraper._format_salary(item.get("salary_min"), item.get("salary_max")),
            "description": RemoteOKScraper._clean_html(item.get("description")),
            "url": str(item.get("apply_url") or item.get("url") or "").strip(),
            "source": "remoteok",
            "posted_date": RemoteOKScraper._format_date(item.get("date")),
            "job_type": RemoteOKScraper._determine_job_type(tags),
        }

        # Dynamically attach extended attributes if supported by Job model
        job = Job(**job_kwargs)
        if hasattr(job, "tags"):
            setattr(job, "tags", tags)
        if hasattr(job, "salary_min"):
            setattr(job, "salary_min", item.get("salary_min"))
        if hasattr(job, "salary_max"):
            setattr(job, "salary_max", item.get("salary_max"))

        return job

    def scrape(self, role: str, location: Optional[str] = None) -> List[Job]:
        """Fetch listings matching role; location is deliberately ignored."""
        del location
        try:
            listings = self._fetch_jobs_from_api()
        except (requests.RequestException, ValueError) as exc:
            logger.error("RemoteOK API request failed: %s", exc)
            return []
        logger.info("RemoteOK returned %d total listings.", len(listings))
        return [self._convert_to_job(item) for item in self._filter_jobs(listings, [role]) if item.get("position")]

    def scrape_jobs(self, keywords: List[str], locations: List[str]) -> List[Job]:
        del locations
        role = keywords[0] if keywords else "software engineer"
        return self.scrape(role=role)
EOF