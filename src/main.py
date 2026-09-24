"""
Main entry point for the Job Agent application.
Orchestrates all scrapers and coordinates data flow.
"""

import logging
import argparse
from typing import List, Dict, Any
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import settings, setup_logging, get_logger
from src.scrapers import NaukriScraper, RemoteOKScraper, WellfoundScraper
from src.processors import DataNormalizer, Deduplicator, QueryParser
from src.storage import CSVWriter


def main():
    """Main function to run the job agent."""
    argument_parser = argparse.ArgumentParser(description="Scrape jobs from configured platforms")
    argument_parser.add_argument(
        '-q', '--query',
        help='Natural-language job query, for example: find product manager roles in bangalore',
    )
    args = argument_parser.parse_args()

    # Set up logging
    setup_logging()
    logger = get_logger(__name__)
    
    logger.info("Starting Job Agent")

    query_keywords = settings.SEARCH_KEYWORDS
    query_locations = settings.LOCATIONS
    if args.query:
        parsed_query = QueryParser().parse(args.query)
        query_keywords = [parsed_query.keywords] if parsed_query.keywords else settings.SEARCH_KEYWORDS
        query_locations = parsed_query.locations or settings.LOCATIONS
        logger.info(f"Extracted keyword: {parsed_query.keywords}")
        logger.info(f"Extracted location: {', '.join(parsed_query.locations)}")
    
    # Validate configuration
    if not settings.validate():
        logger.error("Configuration validation failed")
        return
    
    # Initialize components
    naukri_scraper = NaukriScraper()
    remoteok_scraper = RemoteOKScraper()
    wellfound_scraper = WellfoundScraper()
    
    normalizer = DataNormalizer()
    deduplicator = Deduplicator()
    csv_writer = CSVWriter()
    
    # Scrape jobs from all platforms without letting one failed scraper stop the run.
    all_jobs = []
    scrapers = [
        ("Naukri", naukri_scraper, query_keywords, query_locations),
        ("RemoteOK", remoteok_scraper, query_keywords, query_locations),
        ("Wellfound", wellfound_scraper, query_keywords, query_locations),
    ]

    for scraper_name, scraper, keywords, locations in scrapers:
        try:
            logger.info(f"Scraping jobs from {scraper_name}...")
            jobs = scraper.scrape_jobs(keywords=keywords, locations=locations)
            all_jobs.extend(jobs)
        except Exception as exc:
            logger.exception(f"Error during {scraper_name} scraping: {exc}")
            continue
    
    logger.info(f"Total jobs scraped: {len(all_jobs)}")
    
    if not all_jobs:
        logger.warning("No jobs found")
        return
    
    # Convert to dictionaries for processing
    jobs_dicts = [job.to_dict() for job in all_jobs]
    
    # Normalize data
    logger.info("Normalizing job data...")
    normalized_jobs = normalizer.normalize_jobs(jobs_dicts)
    
    # Deduplicate
    logger.info("Deduplicating jobs...")
    unique_jobs = deduplicator.deduplicate_jobs(normalized_jobs)
    
    # Write to CSV
    logger.info("Writing jobs to CSV...")
    csv_path = csv_writer.write_jobs(
        unique_jobs,
        keywords=query_keywords if args.query else None,
        locations=query_locations if args.query else None,
        query=args.query,
    )
    
    logger.info(f"Job Agent completed successfully. Jobs saved to: {csv_path}")


if __name__ == "__main__":
    main()