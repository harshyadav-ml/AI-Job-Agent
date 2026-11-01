"""
Deduplicator — removes duplicate job listings across platforms.

Design decisions vs. the original:
1. **Stateless** — deduplication is a pure function; instance state accumulation
   across calls caused silent cross-run deduplication bugs.
2. **URL-first fingerprinting** — the canonical job URL is the most stable
   identifier.  Falls back to title + company when URL is missing/relative.
3. **SHA-256 instead of MD5** — MD5 is deprecated; SHA-256 is standard.
4. **Location excluded from dedup key** — the same posting can appear as
   "Remote" on one platform and "Bangalore, Remote" on another.  Including
   location caused false negatives (two records for the same job).
5. **Fuzzy dedup** — implemented using difflib; no external dependency.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
from typing import Any, Optional

from src.config import get_logger
from src.models.job import Job

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pure-function fingerprint helpers (module-level, importable by tests)
# ---------------------------------------------------------------------------


def job_fingerprint(job: Job) -> str:
    """
    Return a 16-hex-char SHA-256 fingerprint for deduplication.

    Priority:
    1. Canonical URL (stripped of trailing slash and lowercased)
    2. Normalised title + company (when URL is absent/relative/placeholder)
    """
    url = (job.url or "").strip().lower().rstrip("/")
    if url and url not in ("#", "/", ""):
        return hashlib.sha256(url.encode()).hexdigest()[:16]
    sig = f"{(job.title or '').lower().strip()}|{(job.company or '').lower().strip()}"
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def fingerprint_from_dict(d: dict[str, Any]) -> str:
    """Same logic but for raw dicts (used by legacy normalize_dicts path)."""
    url = str(d.get("url") or "").strip().lower().rstrip("/")
    if url and url not in ("#", "/", ""):
        return hashlib.sha256(url.encode()).hexdigest()[:16]
    sig = f"{str(d.get('title','') or '').lower().strip()}|{str(d.get('company','') or '').lower().strip()}"
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Deduplicator class
# ---------------------------------------------------------------------------


class Deduplicator:
    """
    Remove duplicate ``Job`` objects.

    All public methods are stateless — they do NOT mutate ``self.seen_*``
    between calls.  Re-use the same instance freely across multiple pipeline
    runs without calling ``reset()``.
    """

    def __init__(self) -> None:
        self.logger = get_logger(__name__)

    # ------------------------------------------------------------------ #
    # Primary API                                                          #
    # ------------------------------------------------------------------ #

    def deduplicate_jobs(self, jobs: list[Job]) -> list[Job]:
        """
        Remove exact duplicates from a list of ``Job`` objects.

        Uses URL-first SHA-256 fingerprinting.  Preserves first-seen order.

        Args:
            jobs: Validated ``Job`` instances from any number of platforms.

        Returns:
            De-duplicated list (first occurrence wins).
        """
        self.logger.info("Deduplicating %d jobs", len(jobs))
        seen: set[str] = set()
        unique: list[Job] = []
        dupes = 0

        for job in jobs:
            fprint = job.job_id or job_fingerprint(job)
            if fprint in seen:
                dupes += 1
                self.logger.debug(
                    "Duplicate dropped: '%s' @ '%s' (id=%s)",
                    job.title,
                    job.company,
                    fprint,
                )
            else:
                seen.add(fprint)
                unique.append(job)

        self.logger.info(
            "Deduplication complete: %d unique, %d duplicates removed",
            len(unique),
            dupes,
        )
        return unique

    def deduplicate_dicts(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Legacy compatibility: accept raw dicts, deduplicate, return dicts.

        Prefer ``deduplicate_jobs()`` for typed pipelines.
        """
        self.logger.info("Deduplicating %d job dicts", len(jobs))
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        dupes = 0

        for d in jobs:
            fprint = fingerprint_from_dict(d)
            if fprint in seen:
                dupes += 1
            else:
                seen.add(fprint)
                unique.append(d)

        self.logger.info("%d unique, %d duplicates removed", len(unique), dupes)
        return unique

    # Keep the old name working for any code that calls it directly
    def deduplicate(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Alias for ``deduplicate_dicts``."""
        return self.deduplicate_dicts(jobs)

    # ------------------------------------------------------------------ #
    # Fuzzy deduplication                                                  #
    # ------------------------------------------------------------------ #

    def fuzzy_deduplicate(
        self,
        jobs: list[Job],
        similarity_threshold: float = 0.85,
    ) -> list[Job]:
        """
        Remove near-duplicate listings using title+company string similarity.

        Two jobs are considered near-duplicates when their combined
        ``title + company`` strings have a sequence-match ratio above
        ``similarity_threshold``.

        Uses ``difflib.SequenceMatcher`` — no external dependencies.

        Args:
            jobs:                   Validated ``Job`` instances.
            similarity_threshold:   Similarity ratio in [0, 1].  Default 0.85.

        Returns:
            De-duplicated list (first occurrence wins).
        """
        self.logger.info(
            "Fuzzy-deduplicating %d jobs (threshold=%.2f)", len(jobs), similarity_threshold
        )
        unique: list[Job] = []
        fuzzy_dupes = 0

        for candidate in jobs:
            candidate_sig = f"{candidate.title.lower()} {candidate.company.lower()}"
            is_dupe = False
            for accepted in unique:
                accepted_sig = f"{accepted.title.lower()} {accepted.company.lower()}"
                ratio = difflib.SequenceMatcher(
                    None, candidate_sig, accepted_sig
                ).ratio()
                if ratio >= similarity_threshold:
                    self.logger.debug(
                        "Fuzzy dupe (%.2f): '%s' ≈ '%s'",
                        ratio,
                        candidate.title,
                        accepted.title,
                    )
                    is_dupe = True
                    fuzzy_dupes += 1
                    break
            if not is_dupe:
                unique.append(candidate)

        self.logger.info(
            "Fuzzy dedup complete: %d unique, %d near-duplicates removed",
            len(unique),
            fuzzy_dupes,
        )
        return unique

    # ------------------------------------------------------------------ #
    # Cross-platform convenience                                           #
    # ------------------------------------------------------------------ #

    def cross_platform_deduplicate(
        self, jobs_by_platform: dict[str, list[Job]]
    ) -> list[Job]:
        """
        Merge jobs from multiple platforms and deduplicate across all of them.

        Args:
            jobs_by_platform: ``{"naukri": [...], "remoteok": [...], ...}``

        Returns:
            Single de-duplicated list from all platforms.
        """
        all_jobs: list[Job] = []
        for platform, jobs in jobs_by_platform.items():
            self.logger.info("Merging %d jobs from '%s'", len(jobs), platform)
            all_jobs.extend(jobs)
        return self.deduplicate_jobs(all_jobs)

    # ------------------------------------------------------------------ #
    # Legacy shim                                                          #
    # ------------------------------------------------------------------ #

    def reset(self) -> None:
        """No-op — Deduplicator is now stateless. Kept for API compatibility."""
        self.logger.debug("reset() called — Deduplicator is stateless, nothing to clear.")
