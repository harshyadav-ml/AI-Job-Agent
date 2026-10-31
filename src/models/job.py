"""
Canonical, validated job posting model for the AI Job Agent pipeline.

All three platform scrapers (Naukri, RemoteOK, Wellfound) produce instances
of this single model. Pydantic v2 enforces schema validation, field coercion,
and deduplication ID generation.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Salary parsing helpers (shared across the pipeline)
# ---------------------------------------------------------------------------

_SALARY_RE = re.compile(
    r"(?P<curr1>[₹$€£])?\s*"
    r"(?P<min>[\d,]+(?:\.\d+)?)\s*(?P<unit1>lpa|lakh|lac|k|cr|crore)?"
    r"(?:\s*[-–to]+\s*(?P<curr2>[₹$€£])?\s*(?P<max>[\d,]+(?:\.\d+)?)\s*(?P<unit2>lpa|lakh|lac|k|cr|crore)?)?"
    r"(?:\s*(?P<unit_end>lpa|lakh|lac|k|cr|crore|\+|pa|per\s+annum|annually))?",
    re.IGNORECASE,
)

_UNIT_MULTIPLIERS: dict[str, float] = {
    "lpa": 100_000,
    "lakh": 100_000,
    "lac": 100_000,
    "cr": 10_000_000,
    "crore": 10_000_000,
    "k": 1_000,
    "pa": 1,
    "per annum": 1,
    "annually": 1,
    "+": 1,
}


def parse_salary_bounds(raw: str | None) -> tuple[Optional[int], Optional[int]]:
    """
    Extract (salary_min, salary_max) as raw integers from any salary string.

    Handles:
    - USD:  "$80,000 - $120,000", "$80k", "$200k - $300k"
    - INR:  "₹6,00,000", "6-12 Lacs PA", "8 LPA", "₹12,00,000"
    - None / "Not specified" → (None, None)
    """
    if not raw:
        return None, None
    cleaned = raw.strip().lower()
    if cleaned in ("", "not specified", "n/a", "negotiable", "competitive"):
        return None, None

    cleaned = cleaned.replace(",", "")
    m = _SALARY_RE.search(cleaned)
    if not m:
        return None, None

    try:
        lo = float(m.group("min") or 0)
        has_max = m.group("max") is not None
        hi = float(m.group("max") or lo)

        unit1 = (m.group("unit1") or "").lower()
        unit2 = (m.group("unit2") or "").lower()
        unit_end = (m.group("unit_end") or "").lower()

        unit_lo = unit1 or unit2 or unit_end
        unit_hi = unit2 or unit_end or unit1

        mult_lo = _UNIT_MULTIPLIERS.get(unit_lo, 1.0)
        mult_hi = _UNIT_MULTIPLIERS.get(unit_hi, 1.0) if has_max else mult_lo

        if not unit_lo and lo < 100:
            mult_lo = 100_000
        if not unit_hi and hi < 100:
            mult_hi = 100_000

        return int(lo * mult_lo), int(hi * mult_hi)
    except (ValueError, TypeError):
        return None, None


# ---------------------------------------------------------------------------
# Date parsing helpers
# ---------------------------------------------------------------------------

_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
    "%d-%b-%Y",
    "%d-%B-%Y",
)

_RELATIVE_DATE_RE = re.compile(
    r"(\d+)\s*\+?\s*(s|sec|second|m|min|minute|h|hr|hour|d|day|w|wk|week|mo|month|y|yr|year)s?(?:\s*ago)?\b",
    re.IGNORECASE,
)

_RELATIVE_SECONDS: dict[str, int] = {
    "s": 1, "sec": 1, "second": 1,
    "m": 60, "min": 60, "minute": 60,
    "h": 3600, "hr": 3600, "hour": 3600,
    "d": 86400, "day": 86400,
    "w": 604800, "wk": 604800, "week": 604800,
    "mo": 2592000, "month": 2592000,
    "y": 31536000, "yr": 31536000, "year": 31536000,
}


def parse_date(date_val: object) -> Optional[str]:
    """
    Parse diverse date strings into strict ISO-8601 'YYYY-MM-DD' format.

    Handles:
    - datetime / date instances
    - Unix timestamps (seconds or ms as int/str)
    - Relative dates ("2 days ago", "3w ago", "3w", "5h ago", "30+ days ago")
    - Word relative ("today", "yesterday", "just now", "few hours ago")
    - ISO-8601 strings ("2026-09-22T14:36:54+00:00", "2026-09-22T14:36:54Z")
    - Standard date strings ("2026-10-15", "15/10/2026", "15 Oct 2026")
    - Prefix cleanup ("Posted 2 days ago", "Active 1w ago")
    """
    if date_val is None:
        return None

    if isinstance(date_val, datetime):
        return date_val.strftime("%Y-%m-%d")
    if isinstance(date_val, date):
        return date_val.strftime("%Y-%m-%d")

    raw = str(date_val).strip()
    if not raw or raw.lower() in ("none", "null", "not specified", "n/a", ""):
        return None

    # 1. Numeric timestamp (seconds or milliseconds)
    if raw.isdigit():
        try:
            ts = float(raw)
            if len(raw) >= 12:  # milliseconds
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            pass

    clean = raw.lower()
    clean = re.sub(r"^(posted|active|updated)\s*[:\-]?\s*", "", clean).strip()

    # 2. 'today', 'just now', 'few hours ago', 'yesterday'
    now = datetime.now(timezone.utc)
    if any(clean.startswith(x) for x in ("just now", "today", "few hours ago", "a few hours ago", "moments ago")):
        return now.strftime("%Y-%m-%d")
    if clean.startswith("yesterday"):
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # 3. Relative patterns: "30+ days ago", "2 days ago", "3w ago", "3w", "2d ago"
    rel_match = _RELATIVE_DATE_RE.search(clean)
    if rel_match:
        amount = int(rel_match.group(1))
        unit = rel_match.group(2).lower()
        sec = _RELATIVE_SECONDS.get(unit, 86400) * amount
        return (now - timedelta(seconds=sec)).strftime("%Y-%m-%d")

    # 4. ISO-8601 parsing via datetime.fromisoformat
    iso_candidate = raw.replace("Z", "+00:00").replace("z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_candidate)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass

    # 5. Standard strptime formats (exact case preserved)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


# ---------------------------------------------------------------------------
# Primary model
# ---------------------------------------------------------------------------


class Job(BaseModel):
    """
    Unified, validated job posting produced by every platform scraper.
    """

    # ------------------------------------------------------------------ #
    # Required fields                                                      #
    # ------------------------------------------------------------------ #
    title: str = Field(..., min_length=1, max_length=512)
    company: str = Field(..., min_length=1, max_length=256)
    source: str = Field(
        ...,
        description="Lowercase platform identifier, e.g. 'naukri', 'remoteok', 'wellfound'.",
    )
    url: str = Field(..., description="Canonical URL of the job listing.")

    # ------------------------------------------------------------------ #
    # Semi-required (sensible defaults)                                    #
    # ------------------------------------------------------------------ #
    location: str = Field(default="Remote", max_length=256)
    description: str = Field(default="")
    job_type: str = Field(default="Full-time")

    # ------------------------------------------------------------------ #
    # Optional / nullable                                                  #
    # ------------------------------------------------------------------ #
    salary: Optional[str] = Field(
        default=None,
        description="Salary text (e.g. '$80,000 - $120,000', '12-18 LPA').",
    )
    salary_raw: Optional[str] = Field(
        default=None,
        description="Raw salary text preserved verbatim.",
    )
    salary_min: Optional[int] = Field(
        default=None,
        ge=0,
        description="Parsed lower bound of salary range.",
    )
    salary_max: Optional[int] = Field(
        default=None,
        ge=0,
        description="Parsed upper bound of salary range.",
    )
    experience_raw: Optional[str] = Field(
        default=None,
        description="Raw experience string from scraper (e.g. '0-2 Yrs', '3+ years').",
    )
    max_experience_required: Optional[int] = Field(
        default=None,
        ge=0,
        description="Maximum years of experience required (upper bound parsed as integer).",
    )
    tags: list[str] = Field(default_factory=list)
    posted_date: Optional[str] = Field(
        default=None,
        description="ISO-8601 date string YYYY-MM-DD or None.",
    )
    company_logo: Optional[str] = Field(
        default=None,
        description="Company logo image URL, or None.",
    )
    scraped_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
        description="UTC ISO-8601 timestamp when scraped.",
    )

    # ------------------------------------------------------------------ #
    # Derived / computed                                                   #
    # ------------------------------------------------------------------ #
    job_id: str = Field(
        default="",
        description="Deterministic dedup fingerprint.",
    )

    # ------------------------------------------------------------------ #
    # Pre-validation & field validators                                    #
    # ------------------------------------------------------------------ #

    @model_validator(mode="before")
    @classmethod
    def _sync_salary_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "salary_raw" in data and not data.get("salary"):
                data["salary"] = data["salary_raw"]
            elif "salary" in data and not data.get("salary_raw"):
                data["salary_raw"] = data["salary"]
            if "min_experience" in data and "max_experience_required" not in data:
                data["max_experience_required"] = data.pop("min_experience")
        return data

    @field_validator("title", "company", "location", "description", mode="before")
    @classmethod
    def strip_str(cls, v: object) -> str:
        return (str(v) if v is not None else "").strip()

    @field_validator("source", mode="before")
    @classmethod
    def normalise_source(cls, v: object) -> str:
        return (str(v) if v is not None else "").lower().strip()

    @field_validator("url", mode="before")
    @classmethod
    def clean_url(cls, v: object) -> str:
        value = (str(v) if v is not None else "").strip()
        if not value:
            raise ValueError("url must not be empty")
        return value

    @field_validator("job_type", mode="before")
    @classmethod
    def normalise_job_type(cls, v: object) -> str:
        raw = (str(v) if v is not None else "").strip()
        mapping: dict[str, str] = {
            "full time": "Full-time",
            "full-time": "Full-time",
            "fulltime": "Full-time",
            "part time": "Part-time",
            "part-time": "Part-time",
            "parttime": "Part-time",
            "contract": "Contract",
            "freelance": "Contract",
            "internship": "Internship",
            "intern": "Internship",
        }
        return mapping.get(raw.lower(), raw or "Full-time")

    @field_validator("tags", mode="before")
    @classmethod
    def normalise_tags(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, (list, tuple, set)):
            return [str(tag).strip() for tag in v if tag]
        return [str(v).strip()] if v else []

    @field_validator("salary_min", "salary_max", mode="before")
    @classmethod
    def coerce_salary_int(cls, v: object) -> Optional[int]:
        if v is None:
            return None
        try:
            val = int(float(str(v)))
            return val if val > 0 else None
        except (ValueError, TypeError):
            return None

    @field_validator("posted_date", mode="before")
    @classmethod
    def normalise_posted_date(cls, v: object) -> Optional[str]:
        return parse_date(v)

    # ------------------------------------------------------------------ #
    # Post-validation & post-init hooks                                    #
    # ------------------------------------------------------------------ #

    @model_validator(mode="after")
    def _validate_model(self) -> "Job":
        """Validate salary bounds and compute job_id."""
        # 1. Swap inverted salary bounds
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            _lo, _hi = self.salary_max, self.salary_min
            object.__setattr__(self, "salary_min", _lo)
            object.__setattr__(self, "salary_max", _hi)

        # 2. Derive salary bounds if raw text present
        raw_sal = self.salary or self.salary_raw
        if raw_sal and self.salary_min is None and self.salary_max is None:
            lo, hi = parse_salary_bounds(raw_sal)
            object.__setattr__(self, "salary_min", lo)
            object.__setattr__(self, "salary_max", hi)

        # 3. Compute deterministic job_id
        if not self.job_id:
            url_clean = (self.url or "").lower().rstrip("/")
            sig = url_clean if url_clean and url_clean not in ("", "#", "/") \
                else f"{self.title.lower()}|{self.company.lower()}"
            object.__setattr__(self, "job_id", hashlib.sha256(sig.encode()).hexdigest()[:16])

        return self

    def model_post_init(self, __context: Any) -> None:
        """
        Pydantic v2 post-initialization hook.
        Guarantees derived fields are populated without triggering recursion.
        """
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            _lo, _hi = self.salary_max, self.salary_min
            object.__setattr__(self, "salary_min", _lo)
            object.__setattr__(self, "salary_max", _hi)

        raw_sal = self.salary or self.salary_raw
        if raw_sal and self.salary_min is None and self.salary_max is None:
            lo, hi = parse_salary_bounds(raw_sal)
            object.__setattr__(self, "salary_min", lo)
            object.__setattr__(self, "salary_max", hi)

        if self.salary_raw and not self.salary:
            object.__setattr__(self, "salary", self.salary_raw)
        elif self.salary and not self.salary_raw:
            object.__setattr__(self, "salary_raw", self.salary)

        if not self.job_id:
            url_clean = (self.url or "").lower().rstrip("/")
            sig = url_clean if url_clean and url_clean not in ("", "#", "/") \
                else f"{self.title.lower()}|{self.company.lower()}"
            object.__setattr__(self, "job_id", hashlib.sha256(sig.encode()).hexdigest()[:16])

    # ------------------------------------------------------------------ #
    # Compatibility properties                                             #
    # ------------------------------------------------------------------ #

    @property
    def min_experience(self) -> Optional[int]:
        """Backward-compatible alias for max_experience_required."""
        return self.max_experience_required

    # ------------------------------------------------------------------ #
    # Serialisation                                                        #
    # ------------------------------------------------------------------ #

    def to_dict(self, include_metadata: bool = False) -> dict:
        """Return a dictionary suitable for CSV/DataFrame serialisation."""
        base = {
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "description": self.description,
            "salary": self.salary or self.salary_raw or "",
            "experience_raw": self.experience_raw or "",
            "max_experience_required": self.max_experience_required if self.max_experience_required is not None else "",
            "url": self.url,
            "posted_date": self.posted_date or "",
            "job_type": self.job_type,
            "source": self.source,
            "scraped_at": self.scraped_at,
        }
        if include_metadata:
            base.update({
                "salary_min": self.salary_min,
                "salary_max": self.salary_max,
                "max_experience_required": self.max_experience_required,
                "tags": ", ".join(self.tags),
                "job_id": self.job_id,
                "company_logo": self.company_logo or "",
            })
        return base

    model_config = {"str_strip_whitespace": True}
