"""
Data normalizer — standardises ``Job`` records from all three platforms
into a single, consistent schema before storage.

The previous implementation had ``# TODO`` stubs for salary and date
normalisation.  This version replaces every stub with production-grade
regex parsing.

Key design decisions:
- Salary: extracted into ``salary_min`` / ``salary_max`` integers plus a
  cleaned ``salary`` display string.  Multi-currency support (USD + INR/Lakhs).
- Date: handles ISO-8601, relative ("3 days ago"), and Indian date formats.
- Title: only strip/clean whitespace — do NOT call ``.title()`` which would
  corrupt acronyms like "AI/ML Engineer" → "Ai/Ml Engineer".
- Location: canonicalise common remote synonyms to "Remote".
- Job type: map all known variants to a controlled vocabulary.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from src.config import get_logger
from src.models.job import Job, parse_date, parse_salary_bounds

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

_JOB_TYPE_MAP: dict[str, str] = {
    "full time": "Full-time",
    "full-time": "Full-time",
    "fulltime": "Full-time",
    "permanent": "Full-time",
    "part time": "Part-time",
    "part-time": "Part-time",
    "parttime": "Part-time",
    "contract": "Contract",
    "freelance": "Contract",
    "consulting": "Contract",
    "internship": "Internship",
    "intern": "Internship",
    "apprenticeship": "Internship",
    "temporary": "Contract",
}

_REMOTE_SYNONYMS: frozenset[str] = frozenset(
    {"remote", "work from home", "wfh", "telecommute", "anywhere", "worldwide"}
)

# Supported date formats in parsing order (most specific first)
_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d %b %Y",
    "%b %d, %Y",
    "%B %d, %Y",
)

_RELATIVE_DATE_RE = re.compile(
    r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s*ago",
    re.IGNORECASE,
)
_RELATIVE_SECONDS: dict[str, int] = {
    "second": 1,
    "minute": 60,
    "hour": 3_600,
    "day": 86_400,
    "week": 604_800,
    "month": 2_592_000,   # 30-day approximation
    "year": 31_536_000,
}


class DataNormalizer:
    """
    Normalise a list of ``Job`` objects or raw dicts to a consistent schema.

    Call ``normalize_jobs()`` with a list of ``Job`` instances returned by
    scrapers.  Returns normalised ``Job`` instances (Pydantic models ensure
    the output is always valid).
    """

    def __init__(self) -> None:
        self.logger = get_logger(__name__)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def normalize_jobs(self, jobs: list[Job]) -> list[Job]:
        """
        Normalise every Job in the list.

        Records that fail normalisation are logged and skipped — they are
        never silently corrupted and passed downstream.
        """
        self.logger.info("Normalizing %d jobs", len(jobs))
        result: list[Job] = []
        for job in jobs:
            try:
                result.append(self._normalize(job))
            except Exception as exc:
                self.logger.error(
                    "Failed to normalise job '%s' from '%s': %s",
                    getattr(job, "title", "?"),
                    getattr(job, "source", "?"),
                    exc,
                    exc_info=True,
                )
        self.logger.info("Normalised %d / %d jobs successfully", len(result), len(jobs))
        return result

    def normalize_dicts(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Legacy compatibility: accept raw dicts, normalise, return dicts.

        Prefer ``normalize_jobs()`` for type-safe pipelines.
        """
        jobs: list[Job] = []
        for d in raw:
            try:
                jobs.append(Job(**d))
            except Exception as exc:
                self.logger.warning("Could not construct Job from dict: %s", exc)
        return [j.to_dict() for j in self.normalize_jobs(jobs)]

    # ------------------------------------------------------------------ #
    # Core normalisation                                                   #
    # ------------------------------------------------------------------ #

    def _normalize(self, job: Job) -> Job:
        """Return a new Job with all fields normalised."""
        data = job.model_dump()
        data.update({
            "title": self._normalize_title(job.title),
            "company": self._normalize_company(job.company),
            "location": self._normalize_location(job.location),
            "description": self._normalize_description(job.description),
            "job_type": self._normalize_job_type(job.job_type),
            "posted_date": self._normalize_date(job.posted_date),
        })

        # Re-parse salary bounds from raw string if missing
        if job.salary_raw and (job.salary_min is None or job.salary_max is None):
            lo, hi = parse_salary_bounds(job.salary_raw)
            data["salary_min"] = job.salary_min or lo
            data["salary_max"] = job.salary_max or hi

        # Re-instantiate Job to re-run validation and model_post_init (e.g. job_id update)
        return Job(**data)

    # ------------------------------------------------------------------ #
    # Field normalisers                                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_title(title: str) -> str:
        """
        Clean whitespace only.  Do NOT call .title() — it corrupts "AI/ML",
        "DevOps", "iOS", "SDE-2", etc.
        """
        return re.sub(r"\s+", " ", (title or "").strip())

    @staticmethod
    def _normalize_company(company: str) -> str:
        name = re.sub(r"\s+", " ", (company or "").strip())
        # Strip trailing star-ratings appended by some scrapers: "Google 4.2 ★"
        name = re.sub(r"\s+\d+(\.\d+)?\s*[★*]+$", "", name)
        return name.strip()

    @staticmethod
    def _normalize_location(location: str) -> str:
        if not location:
            return "Remote"
        loc = location.strip()
        if any(syn in loc.lower() for syn in _REMOTE_SYNONYMS):
            return "Remote"
        return loc

    @staticmethod
    def _normalize_description(description: str) -> str:
        if not description:
            return ""
        # Collapse any run of whitespace (including \n, \t) to a single space
        return re.sub(r"\s+", " ", description).strip()

    @staticmethod
    def _normalize_job_type(job_type: str) -> str:
        if not job_type:
            return "Full-time"
        key = job_type.strip().lower()
        return _JOB_TYPE_MAP.get(key, job_type.strip() or "Full-time")

    @staticmethod
    def _normalize_date(date_str: Optional[str]) -> Optional[str]:
        """
        Parse and normalise a date string to strict ISO-8601 ``YYYY-MM-DD``.
        Delegates to parse_date which supports relative dates, timestamps,
        and multiple regional/international calendar formats.
        """
        return parse_date(date_str)
