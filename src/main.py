"""
Main entry point — async orchestrator for the AI Job Agent pipeline.

Execution flow:
  1. Parse CLI args / settings → keywords + locations
  2. Launch all three scrapers concurrently with asyncio.gather()
     (one scraper crashing does NOT cancel the others)
  3. Normalise → deduplicate → write to CSV

Exit codes:
  0 — success (≥1 job written)
  1 — configuration error
  2 — no jobs found after all scrapers ran
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Make ``src`` importable when running as ``python -m src.main``
sys.path.insert(0, str(Path(__file__).parent.parent))

import re

from src.config import settings, setup_logging, get_logger
from src.models.job import Job
from src.processors.data_normalizer import DataNormalizer
from src.processors.deduplicator import Deduplicator
from src.processors.query_parser import QueryParser
from src.scrapers.base import BaseScraper
from src.scrapers.naukri_scraper import NaukriScraper
from src.scrapers.remoteok_scraper import RemoteOKScraper
from src.scrapers.wellfound_scraper import WellfoundScraper
from src.storage.csv_writer import CSVWriter


# ---------------------------------------------------------------------------
# Strict Seniority & Experience Blocklist Helpers
# ---------------------------------------------------------------------------

BLOCKED_SENIOR_KEYWORDS: list[str] = [
    "senior", "sr", "sr.", "lead", "principal", "manager", "architect", "director", "head"
]

_SENIOR_BLOCK_PATTERN = re.compile(
    r"\b(senior|sr|lead|principal|manager|architect|director|head)\b|\bsr\.",
    re.IGNORECASE,
)


def has_blocked_senior_keyword(job: Job) -> bool:
    """Return True if job.title or job.experience_raw contains blocked senior keywords."""
    if job.title and _SENIOR_BLOCK_PATTERN.search(job.title):
        return True
    if job.experience_raw and _SENIOR_BLOCK_PATTERN.search(job.experience_raw):
        return True
    return False


# ---------------------------------------------------------------------------
# Async scrape orchestration
# ---------------------------------------------------------------------------


async def _run_scraper(
    scraper: BaseScraper,
    keywords: list[str],
    locations: list[str],
    max_results: int,
) -> list[Job]:
    """
    Invoke a single scraper and return its results.

    Any unhandled exception is caught here so that one failed scraper
    does not cancel the concurrent tasks for the other platforms.
    """
    logger = get_logger(__name__)
    try:
        logger.info("[%s] scrape starting…", scraper.source_name)
        jobs = await scraper.scrape(keywords, locations, max_results=max_results)
        logger.info("[%s] scrape finished — %d jobs", scraper.source_name, len(jobs))
        return jobs
    except Exception as exc:
        logger.exception("[%s] scraper raised an unhandled exception: %s", scraper.source_name, exc)
        return []


async def run_all_scrapers(
    scrapers: list[BaseScraper],
    keywords: list[str],
    locations: list[str],
    max_results: int = 50,
) -> list[Job]:
    """
    Execute all scrapers **concurrently** and merge results.

    ``asyncio.gather(..., return_exceptions=True)`` ensures that a crash
    in one scraper never prevents the others from completing.
    """
    tasks = [
        asyncio.create_task(
            _run_scraper(s, keywords, locations, max_results),
            name=s.source_name,
        )
        for s in scrapers
    ]
    results: list[list[Job] | BaseException] = await asyncio.gather(
        *tasks, return_exceptions=True
    )

    all_jobs: list[Job] = []
    logger = get_logger(__name__)
    for scraper, result in zip(scrapers, results):
        if isinstance(result, BaseException):
            logger.error(
                "[%s] gather caught exception: %s", scraper.source_name, result
            )
        else:
            all_jobs.extend(result)

    return all_jobs


async def enrich_jobs_with_deep_scrape(
    jobs: list[Job],
    scrapers: list[BaseScraper],
    max_concurrency: int = 5,
) -> list[Job]:
    """
    Fetch full job descriptions from detail pages for shortlisted jobs.

    For each surviving job:
    1. Fetches the full JD body text from the job detail page.
    2. Updates job.description with the full text (if richer than current).
    3. Runs DataNormalizer.parse_deep_scrape_fields() on the full text to
       intelligently extract experience_raw, max_experience_required, salary_raw,
       salary_min, salary_max — filling any fields left blank during the SRP scrape.

    Uses asyncio.Semaphore(max_concurrency) to cap simultaneous requests.
    """
    from src.processors.data_normalizer import DataNormalizer

    logger = get_logger(__name__)
    if not jobs:
        return []

    scraper_map: dict[str, BaseScraper] = {s.source_name.lower(): s for s in scrapers}
    sem = asyncio.Semaphore(max_concurrency)

    async def _fetch_for_job(job: Job) -> None:
        source_key = (job.source or "").lower().strip()
        scraper = scraper_map.get(source_key)
        if not scraper:
            logger.debug(
                "No scraper found for source '%s' (job: %s)", job.source, job.title
            )
            return

        if not job.url or job.url in ("#", "/", ""):
            return

        async with sem:
            try:
                full_text = await scraper.fetch_full_description(job.url)
                if not full_text or len(full_text.strip()) < 80:
                    return

                full_text = full_text.strip()

                # 1. Update description if the deep-scraped text is richer
                if len(full_text) > len(job.description or ""):
                    object.__setattr__(job, "description", full_text)
                    logger.debug(
                        "Enriched description for '%s' (%s, %d chars)",
                        job.title,
                        job.source,
                        len(full_text),
                    )

                # 2. Parse experience, salary from full text; fill blanks
                parsed = DataNormalizer.parse_deep_scrape_fields(full_text)
                changed: list[str] = []

                if parsed.get("experience_raw") and not job.experience_raw:
                    object.__setattr__(job, "experience_raw", parsed["experience_raw"])
                    changed.append("experience_raw")

                if parsed.get("max_experience_required") is not None and job.max_experience_required is None:
                    object.__setattr__(job, "max_experience_required", parsed["max_experience_required"])
                    changed.append("max_experience_required")

                if parsed.get("salary_raw") and not job.salary_raw:
                    object.__setattr__(job, "salary_raw", parsed["salary_raw"])
                    if not job.salary:
                        object.__setattr__(job, "salary", parsed["salary_raw"])
                    changed.append("salary")

                if parsed.get("salary_min") and job.salary_min is None:
                    object.__setattr__(job, "salary_min", parsed["salary_min"])
                    changed.append("salary_min")

                if parsed.get("salary_max") and job.salary_max is None:
                    object.__setattr__(job, "salary_max", parsed["salary_max"])
                    changed.append("salary_max")

                if changed:
                    logger.debug(
                        "Deep-parsed fields %s for '%s' (%s)",
                        changed, job.title, job.source,
                    )

            except Exception as exc:
                logger.warning(
                    "Deep scrape failed for '%s' at '%s' (%s): %s",
                    job.title,
                    job.url,
                    job.source,
                    exc,
                )

    tasks = [asyncio.create_task(_fetch_for_job(job)) for job in jobs]
    await asyncio.gather(*tasks, return_exceptions=True)

    enriched_count = sum(1 for j in jobs if len(j.description or "") > 200)
    logger.info(
        "Deep scrape complete: %d/%d shortlisted jobs enriched with full descriptions",
        enriched_count,
        len(jobs),
    )
    return jobs


# ---------------------------------------------------------------------------
# Async main
# ---------------------------------------------------------------------------


async def async_main() -> int:
    """Run the full pipeline.  Returns an integer exit code."""
    setup_logging()
    logger = get_logger(__name__)
    logger.info("=== AI Job Agent starting ===")

    # ---- CLI argument parsing ----
    arg_parser = argparse.ArgumentParser(
        description="Scrape jobs from Naukri, RemoteOK, and Wellfound."
    )
    arg_parser.add_argument(
        "-q",
        "--query",
        help='Natural-language search query. Example: "python developer jobs in bangalore"',
    )
    arg_parser.add_argument(
        "--max-results",
        type=int,
        default=50,
        help="Soft cap on results per scraper (default: 50).",
    )
    args = arg_parser.parse_args()

    # ---- Resolve keywords + locations ----
    query_keywords: list[str] = settings.search_keywords_list
    query_locations: list[str] = settings.locations_list

    if args.query:
        parsed = QueryParser().parse(args.query)
        if parsed.keywords:
            query_keywords = [parsed.keywords]
        if parsed.locations:
            query_locations = parsed.locations
        logger.info(
            "Query parsed — keywords=%s locations=%s", query_keywords, query_locations
        )

    # ---- Config validation ----
    if not settings.validate():
        logger.error("Settings validation failed — aborting.")
        return 1

    # ---- Initialise components ----
    scrapers: list[BaseScraper] = [
        NaukriScraper(),
        RemoteOKScraper(),
        WellfoundScraper(),
    ]
    normalizer = DataNormalizer()
    deduplicator = Deduplicator()
    csv_writer = CSVWriter()

    # ---- Concurrent scrape ----
    logger.info(
        "Launching %d scrapers concurrently — keywords=%s locations=%s",
        len(scrapers),
        query_keywords,
        query_locations,
    )
    all_jobs = await run_all_scrapers(
        scrapers,
        query_keywords,
        query_locations,
        max_results=args.max_results,
    )
    logger.info("Total raw jobs collected: %d", len(all_jobs))

    if not all_jobs:
        logger.warning("No jobs found across all platforms.")
        return 2

    # ---- Normalise ----
    logger.info("Normalising job data…")
    normalised_jobs = normalizer.normalize_jobs(all_jobs)

    # ---- Strict Experience & Seniority Filter ----
    logger.info("Applying strict experience & seniority filter (Fresher rules: <= 1 yr max exp, no senior keywords)...")
    filtered_jobs: list[Job] = []
    dropped_senior = 0
    dropped_experience = 0

    for job in normalised_jobs:
        # Rule 1: If job.title or job.experience_raw contains a blocked senior keyword -> DROP
        if has_blocked_senior_keyword(job):
            dropped_senior += 1
            logger.debug(
                "Dropping job '%s' at '%s' (blocked senior keyword found; experience_raw='%s')",
                job.title,
                job.company,
                job.experience_raw,
            )
            continue

        # Rule 2: Keep the job IF job.max_experience_required is None OR job.max_experience_required <= 1.
        # Drop the job if job.max_experience_required > 1.
        if job.max_experience_required is not None and job.max_experience_required > 1:
            dropped_experience += 1
            logger.debug(
                "Dropping job '%s' at '%s' (max experience %d > 1 yr; experience_raw='%s')",
                job.title,
                job.company,
                job.max_experience_required,
                job.experience_raw,
            )
            continue

        # Otherwise -> KEEP
        filtered_jobs.append(job)

    logger.info(
        "Filter summary: %d kept, %d dropped (%d senior keywords, %d max experience > 1 yr)",
        len(filtered_jobs),
        dropped_senior + dropped_experience,
        dropped_senior,
        dropped_experience,
    )

    # ---- Deduplicate ----
    logger.info("Deduplicating shortlisted jobs…")
    unique_jobs = deduplicator.deduplicate_jobs(filtered_jobs)
    logger.info("Unique jobs after dedup: %d", len(unique_jobs))

    # ---- Deep Scrape: Fetch Full Job Descriptions for Shortlisted Jobs ----
    logger.info(
        "Deep scraping full job descriptions for %d shortlisted jobs...",
        len(unique_jobs),
    )
    enriched_jobs = await enrich_jobs_with_deep_scrape(unique_jobs, scrapers)

    # ---- Persist to CSV ----
    logger.info("Writing to CSV…")
    job_dicts = [j.to_dict() for j in enriched_jobs]
    csv_path = csv_writer.write_jobs(
        job_dicts,
        keywords=query_keywords if args.query else None,
        locations=query_locations if args.query else None,
        query=args.query,
    )

    logger.info("=== Pipeline complete. %d jobs saved to: %s ===", len(enriched_jobs), csv_path)
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Synchronous entry point — starts the async event loop."""
    exit_code = asyncio.run(async_main())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
