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

    # ---- Deduplicate ----
    logger.info("Deduplicating…")
    unique_jobs = deduplicator.deduplicate_jobs(normalised_jobs)
    logger.info("Unique jobs after dedup: %d", len(unique_jobs))

    # ---- Persist to CSV ----
    logger.info("Writing to CSV…")
    job_dicts = [j.to_dict() for j in unique_jobs]
    csv_path = csv_writer.write_jobs(
        job_dicts,
        keywords=query_keywords if args.query else None,
        locations=query_locations if args.query else None,
        query=args.query,
    )

    logger.info("=== Pipeline complete. %d jobs saved to: %s ===", len(unique_jobs), csv_path)
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
