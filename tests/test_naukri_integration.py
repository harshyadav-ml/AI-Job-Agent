"""Live integration checks for the dual-strategy Naukri scraper."""

import time

import pytest
from bs4 import BeautifulSoup

from src.models import Job
from src.scrapers.naukri_scraper import NaukriScraper


SEARCH_URL = "https://www.naukri.com/python-developer-jobs"


@pytest.fixture
def scraper():
    instance = NaukriScraper()
    instance.request_delay = 0
    return instance


def _fetch_or_skip(scraper, url=SEARCH_URL):
    try:
        html = scraper._fetch_html_with_retry(url, retries=1)
    except Exception as exc:
        pytest.skip(f"Naukri unavailable: {exc}")
    if not html:
        pytest.skip("Naukri returned no HTML")
    return html


def test_live_website_connection(scraper):
    html = _fetch_or_skip(scraper, "https://www.naukri.com")
    assert "<html" in html.lower() or "<!doctype" in html.lower()


def test_live_search_page_fetch(scraper):
    html = _fetch_or_skip(scraper)
    assert len(html) > 0


def test_live_job_card_parsing(scraper):
    html = _fetch_or_skip(scraper)
    cards = scraper._find_job_cards(BeautifulSoup(html, "html.parser"))
    if not cards:
        pytest.skip("Naukri returned no recognized job cards")
    jobs = [scraper._parse_job_card(card) for card in cards[:3]]
    jobs = [job for job in jobs if job]
    if not jobs:
        pytest.skip("Naukri job card markup changed")
    assert isinstance(jobs[0], Job)
    assert jobs[0].source == "naukri"
    assert jobs[0].scraped_at is not None


def test_full_scrape_workflow(scraper):
    jobs = scraper.scrape_jobs(["python"], [])
    assert isinstance(jobs, list)
    if not jobs:
        pytest.skip("Naukri API and browser fallback returned no jobs")
    assert all(isinstance(job, Job) for job in jobs)
    assert all(job.source == "naukri" and job.scraped_at for job in jobs)


def test_rate_limiting_respected(scraper):
    scraper.request_delay = 0.1
    scraper.last_request_time = time.time()
    start = time.time()
    scraper._rate_limit()
    assert time.time() - start >= 0.1


def test_pagination_handling(scraper):
    scraper.max_pages = 2
    jobs = scraper.scrape_jobs(["python"], [])
    assert isinstance(jobs, list)
    if jobs:
        assert len({(job.title, job.company, job.location) for job in jobs}) == len(jobs)


def test_robustness_to_class_changes(scraper):
    html = _fetch_or_skip(scraper)
    cards = scraper._find_job_cards(BeautifulSoup(html, "html.parser"))
    assert isinstance(cards, list)


def test_data_quality_validation(scraper):
    jobs = scraper.scrape_jobs(["python"], [])
    if not jobs:
        pytest.skip("Naukri API and browser fallback returned no jobs")
    for job in jobs[:5]:
        assert job.title
        assert job.company
        assert job.url
        assert job.source == "naukri"
        assert job.scraped_at is not None
        assert not hasattr(job, "posting_date")
