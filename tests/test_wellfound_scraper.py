"""Unit tests for the Firecrawl-backed Wellfound scraper."""

from unittest.mock import MagicMock

from src.models import Job
from src.scrapers.wellfound_scraper import WellfoundScraper


def test_build_search_url():
    scraper = WellfoundScraper()
    assert scraper._build_search_url("product manager", "bangalore") == (
        "https://wellfound.com/role/l/product-manager/bangalore"
    )


def test_scrape_jobs_calls_firecrawl_and_maps_html():
    firecrawl = MagicMock()
    firecrawl.scrape_url.return_value = {
        "html": """
        <article data-testid="job-card">
          <a href="/jobs/product-manager-123"><h2>Product Manager</h2></a>
          <h4>Acme Labs</h4>
          <p>Bangalore, India · ₹12,00,000 - ₹18,00,000</p>
        </article>
        """,
        "markdown": "",
    }
    scraper = WellfoundScraper(firecrawl=firecrawl)

    jobs = scraper.scrape_jobs(["product manager"], ["bangalore"])

    firecrawl.scrape_url.assert_called_once()
    assert len(jobs) == 1
    assert isinstance(jobs[0], Job)
    assert jobs[0].title == "Product Manager"
    assert jobs[0].company == "Acme Labs"
    assert jobs[0].url == "https://wellfound.com/jobs/product-manager-123"
    assert jobs[0].source == "wellfound"
    assert "₹12,00,000" in jobs[0].salary
    assert set(jobs[0].to_dict()) == {
        "title", "company", "location", "description", "salary", "url",
        "posted_date", "job_type", "source", "scraped_at",
    }


def test_scrape_jobs_handles_firecrawl_failure():
    firecrawl = MagicMock()
    firecrawl.scrape_url.side_effect = RuntimeError("quota exceeded")
    firecrawl.scrape.side_effect = RuntimeError("quota exceeded")
    scraper = WellfoundScraper(firecrawl=firecrawl)

    assert scraper.scrape_jobs(["engineer"], ["pune"]) == []


def test_parse_markdown_fills_company_and_location_fallback():
    scraper = WellfoundScraper()
    markdown = (
        "## [Data Engineer](https://wellfound.com/jobs/data-engineer-123)\n"
        "[Acme Labs](https://wellfound.com/company/acme-labs)\n"
    )

    jobs = scraper._parse_jobs_from_markdown(markdown, "hyderabad")

    assert jobs[0].company == "Acme Labs"
    assert jobs[0].location == "Hyderabad"


def test_parse_markdown_falls_back_to_startup_company():
    scraper = WellfoundScraper()
    markdown = "[AI Engineer](https://wellfound.com/jobs/ai-engineer-123)"

    jobs = scraper._parse_jobs_from_markdown(markdown, "remote")

    assert jobs[0].company == "Startup (Wellfound)"
    assert jobs[0].location == "Remote"


def test_restricted_page_returns_no_jobs():
    firecrawl = MagicMock()
    firecrawl.scrape_url.return_value = {
        "markdown": "One more step before you proceed...",
        "html": "<html><body>Verify you are human</body></html>",
    }

    assert WellfoundScraper(firecrawl=firecrawl).scrape_jobs(["engineer"], ["pune"]) == []


def test_scrape_jobs_without_key_returns_empty():
    scraper = WellfoundScraper()
    scraper.firecrawl = None
    assert scraper.scrape_jobs(["engineer"], ["pune"]) == []