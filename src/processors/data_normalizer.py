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
        exp_raw = job.experience_raw or ""
        max_exp = job.max_experience_required
        if max_exp is None:
            max_exp = self._parse_max_experience(exp_raw, job.description)

        data = job.model_dump()
        data.update({
            "title": self._normalize_title(job.title),
            "company": self._normalize_company(job.company),
            "location": self._normalize_location(job.location),
            "description": self._normalize_description(job.description),
            "job_type": self._normalize_job_type(job.job_type),
            "posted_date": self._normalize_date(job.posted_date),
            "experience_raw": exp_raw.strip() if exp_raw else None,
            "max_experience_required": max_exp,
        })

        # Re-parse salary bounds from raw string if missing
        if job.salary_raw and (job.salary_min is None or job.salary_max is None):
            lo, hi = parse_salary_bounds(job.salary_raw)
            data["salary_min"] = job.salary_min or lo
            data["salary_max"] = job.salary_max or hi

        # Re-instantiate Job to re-run validation and model_post_init (e.g. job_id update)
        return Job(**data)

    @staticmethod
    def _parse_max_experience(
        experience_raw: Optional[str], description: Optional[str]
    ) -> Optional[int]:
        """
        Extract the MAXIMUM required years of experience (upper bound) as an integer.

        Checks experience_raw first. Only falls back to scanning the description
        if experience_raw is completely empty or absent.

        Examples:
        - "0-3 Yrs" -> 3
        - "0-6 Yrs" -> 6
        - "1 - 4 years" -> 4
        - "5+ years" -> 5
        - "0-1 Yrs" -> 1
        - "Fresher" -> 0
        - "2 years" -> 2
        """
        # 1. First check experience_raw
        if experience_raw and str(experience_raw).strip():
            raw = str(experience_raw).strip().lower()

            # 1a. Explicit range: "0-3 Yrs", "0-6 Yrs", "1 - 4 years", "3-7 Yrs" -> capture maximum integer
            m = re.search(r"(\d+)\s*(?:-|–|to)\s*(\d+)", raw)
            if m:
                try:
                    return max(int(m.group(1)), int(m.group(2)))
                except ValueError:
                    pass

            # 1b. Plus: "5+ years", "3+ yrs" -> capture integer
            m = re.search(r"(\d+)\s*\+", raw)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass

            # 1c. Explicit fresher/entry/intern/trainee markers
            if re.search(
                r"\b(freshers?|entry[\s\-]level|intern(?:ship)?|trainee|no\s+(?:prior\s+)?exp(?:erience)?)\b",
                raw,
            ):
                return 0

            # 1d. Min/minimum/at least: "min 2 years", "at least 3 yrs"
            m = re.search(r"\b(?:min(?:imum)?(?:\s+of)?|at\s+least)\s*(\d+)", raw)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass

            # 1e. Single number with year unit: "2 yrs", "1 year", "0 yrs"
            m = re.search(r"(\d+)\s*(?:yrs?|years?)", raw)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass

            # 1f. Standalone single integer
            m = re.search(r"\b(\d+)\b", raw)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass

            # If experience_raw was provided but contained no recognizable number/keyword
            return None

        # 2. Only fall back to scanning description if experience_raw is completely empty
        if not description or not str(description).strip():
            return None

        desc = str(description).lower()

        # 2a. Fresher / entry-level in description
        if re.search(
            r"\b(freshers?(?:\s+can\s+apply)?|entry[\s\-]level|no\s+(?:prior\s+)?experience\s+required)\b",
            desc,
        ):
            return 0

        # 2b. Range patterns in description: "0-3 years of experience", "1 - 4 yrs", "2 to 5 years"
        m = re.search(
            r"(\d+)\s*(?:-|–|to)\s*(\d+)\s*(?:yrs?|years?)(?:\s+(?:of\s+)?experience)?",
            desc,
        )
        if m:
            try:
                return max(int(m.group(1)), int(m.group(2)))
            except ValueError:
                pass

        # 2c. Plus pattern: "5+ years of experience", "3+ yrs"
        m = re.search(
            r"(\d+)\s*\+\s*(?:yrs?|years?)(?:\s+(?:of\s+)?experience)?",
            desc,
        )
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass

        # 2d. "min 2 years", "minimum 3 years of experience", "at least 4 years"
        m = re.search(
            r"\b(?:min(?:imum)?(?:\s+of)?|at\s+least)\s*(\d+)\s*(?:\+)?\s*(?:yrs?|years?)\b",
            desc,
        )
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass

        # 2e. "experience of 2-4 years" (range after prefix)
        m = re.search(
            r"(?:experience|exp)\s*(?:required|needed)?\s*[:\-]?\s*(?:of\s+)?(\d+)\s*(?:-|–|to)\s*(\d+)\s*(?:yrs?|years?)",
            desc,
        )
        if m:
            try:
                return max(int(m.group(1)), int(m.group(2)))
            except ValueError:
                pass

        # 2f. "experience of 2 years", "experience: 3 years"
        m = re.search(
            r"(?:experience|exp)\s*(?:required|needed)?\s*[:\-]?\s*(?:of\s+)?(\d+)\s*(?:\+)?\s*(?:yrs?|years?)",
            desc,
        )
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass

        # 2g. "2 years of experience", "3 yrs experience"
        m = re.search(
            r"(\d+)\s*(?:yrs?|years?)\s+(?:of\s+)?experience",
            desc,
        )
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass

        return None

    @classmethod
    def _parse_experience(
        cls, experience_raw: Optional[str], description: Optional[str]
    ) -> Optional[int]:
        """Backward-compatible alias for _parse_max_experience."""
        return cls._parse_max_experience(experience_raw, description)

    @classmethod
    def parse_deep_scrape_fields(
        cls, full_text: str
    ) -> dict:
        """
        Intelligently parse a full JD body text (from deep scrape) to extract:
          - experience_raw  : the raw experience string found in the text
          - max_experience_required : parsed upper-bound integer
          - salary_raw      : the raw salary string found in the text
          - salary_min, salary_max  : parsed integers

        Searches section headings like "Requirements", "Responsibilities",
        "About the role", etc. regardless of order or casing.

        Returns a dict with only the keys where a value was actually found.
        """
        from src.models.job import parse_salary_bounds

        result: dict = {}
        if not full_text or not str(full_text).strip():
            return result

        text = str(full_text)
        text_lower = text.lower()

        # ---- Experience extraction ----
        # Ordered patterns from most-explicit to most-generic
        _EXP_PATTERNS = [
            # "0-3 years of experience", "1 - 4 yrs experience"
            (r"(\d+)\s*(?:-|–|to)\s*(\d+)\s*(?:\+\s*)?(?:yrs?|years?)(?:\s+of)?\s*(?:experience|exp)?",
             "range"),
            # "3+ years of experience", "2+ yrs"
            (r"(\d+)\s*\+\s*(?:yrs?|years?)(?:\s+of)?\s*(?:experience|exp)?",
             "plus"),
            # "minimum 2 years", "at least 3 years", "min. 1 year"
            (r"\b(?:min(?:imum)?\.?(?:\s+of)?|at\s+least)\s*(\d+)\s*(?:\+)?\s*(?:yrs?|years?)\b",
             "min_prefix"),
            # "experience: 2-4 years", "experience required: 3 years"
            (r"(?:experience|exp)\.?\s*(?:required|needed|preferred|:)?\s*[:\-]?\s*(\d+)\s*(?:-|–|to)\s*(\d+)\s*(?:yrs?|years?)",
             "exp_range"),
            (r"(?:experience|exp)\.?\s*(?:required|needed|preferred|:)?\s*[:\-]?\s*(\d+)\s*(?:\+)?\s*(?:yrs?|years?)",
             "exp_single"),
            # "2 years of experience", "3 yrs experience"
            (r"(\d+)\s*(?:yrs?|years?)\s+(?:of\s+)?(?:experience|exp)\b",
             "single"),
            # bare range like "0-1 Yrs", "3-7 Yrs" (common in Naukri badge text)
            (r"\b(\d+)\s*[-–]\s*(\d+)\s*(?:yrs?|years?)\b",
             "bare_range"),
        ]

        exp_raw_found: Optional[str] = None
        max_exp_found: Optional[int] = None

        for pattern, ptype in _EXP_PATTERNS:
            m = re.search(pattern, text_lower)
            if not m:
                continue

            # Extract raw snippet (±30 chars around match for context)
            start = max(0, m.start() - 5)
            end = min(len(text), m.end() + 5)
            snippet = text[start:end].strip()

            try:
                if ptype == "range":
                    lo, hi = int(m.group(1)), int(m.group(2))
                    exp_raw_found = snippet
                    max_exp_found = max(lo, hi)
                elif ptype == "plus":
                    val = int(m.group(1))
                    exp_raw_found = snippet
                    max_exp_found = val
                elif ptype in ("min_prefix", "exp_single", "single"):
                    val = int(m.group(1))
                    exp_raw_found = snippet
                    max_exp_found = val
                elif ptype == "exp_range":
                    lo, hi = int(m.group(1)), int(m.group(2))
                    exp_raw_found = snippet
                    max_exp_found = max(lo, hi)
                elif ptype == "bare_range":
                    lo, hi = int(m.group(1)), int(m.group(2))
                    exp_raw_found = snippet
                    max_exp_found = max(lo, hi)
            except (ValueError, IndexError):
                continue

            if max_exp_found is not None:
                break

        # Check for fresher/entry-level markers if no numeric range found
        if max_exp_found is None:
            if re.search(
                r"\b(freshers?\s+(?:only|can\s+apply|welcome)|entry[\s\-]level|"
                r"no\s+(?:prior\s+)?experience\s+(?:required|needed)|"
                r"0\s+(?:yrs?|years?)\s+(?:of\s+)?experience)\b",
                text_lower,
            ):
                max_exp_found = 0
                exp_raw_found = "Fresher"

        if exp_raw_found is not None:
            result["experience_raw"] = exp_raw_found.strip()
        if max_exp_found is not None:
            result["max_experience_required"] = max_exp_found

        # ---- Salary extraction ----
        # Common salary patterns: "₹6 LPA", "$80,000 - $120,000", "15-20 Lacs PA", "CTC: 3-6 LPA"
        _SAL_PATTERNS = [
            # Labelled: "Salary: ...", "CTC: ...", "Package: ..."
            r"(?:salary|ctc|compensation|package|pay)\s*[:\-]?\s*([₹$€£]?\s*[\d,.]+\s*(?:lpa|lakh|lac|k|l|cr|crore|pa|per\s+annum)?(?:\s*[-–to]+\s*[₹$€£]?\s*[\d,.]+\s*(?:lpa|lakh|lac|k|l|cr|crore|pa|per\s+annum)?)?)",
            # "₹6,00,000 – ₹12,00,000", "$80,000 - $120,000"
            r"[₹$€£]\s*[\d,]+(?:\.\d+)?(?:\s*[-–to]+\s*[₹$€£]?\s*[\d,]+(?:\.\d+)?)?(?:\s*(?:lpa|lakh|lac|k|cr|pa|per\s+annum|annually))?",
            # "6-12 LPA", "15 Lacs PA", "8 LPA"
            r"\b(\d+(?:\.\d+)?)\s*[-–to]*\s*(\d+(?:\.\d+)?)?\s*(?:lpa|lakh|lac|l\b|cr|crore)\b",
            # "80k - 120k", "$80k+"
            r"[₹$]?\s*(\d+(?:\.\d+)?)\s*k\s*(?:[-–to]+\s*[₹$]?\s*(\d+(?:\.\d+)?)\s*k)?",
        ]

        salary_raw_found: Optional[str] = None
        for pattern in _SAL_PATTERNS:
            m = re.search(pattern, text_lower)
            if m:
                # Extract the matched portion from the original (case-preserved) text
                raw_match = text[m.start():m.end()].strip()
                if len(raw_match) >= 3:  # sanity check: at least "6k" or "₹5"
                    salary_raw_found = raw_match
                    break

        if salary_raw_found:
            result["salary_raw"] = salary_raw_found
            lo, hi = parse_salary_bounds(salary_raw_found)
            if lo:
                result["salary_min"] = lo
            if hi:
                result["salary_max"] = hi

        return result

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
