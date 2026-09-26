"""Unit tests for the Playwright-based Naukri scraper."""

from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup

from src.models import Job
from src.scrapers.naukri_scraper import NaukriScraper


class FakeElement:
    def __init__(self, text="", attribute=None, exists=True):
        self.text = text
        self.attribute = attribute
        self.exists = exists

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.exists else 0

    def inner_text(self):
        return self.text

    def get_attribute(self, name):
        return self.attribute


class FakeCard:
    def __init__(self, values):
        self.values = values

    def locator(self, selector):
        return self.values.get(selector, FakeElement(exists=False))


class FakeCards:
    def __init__(self, cards):
        self.cards = cards

    def count(self):
        return len(self.cards)

    def nth(self, index):
        return self.cards[index]


class FakePage:
    def __init__(self, cards):
        self.cards = cards
        self.goto_calls = []
        self.wait_calls = []
        self.mouse = MagicMock()

    def evaluate(self, script):
        self.evaluate_script = script

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))

    def wait_for_selector(self, selector, **kwargs):
        self.wait_calls.append((selector, kwargs))

    def locator(self, selector):
        return FakeCards(self.cards)


def make_job(title="Python Developer"):
    return Job(
        title=title,
        company="Test Company",
        location="Bangalore",
        description="Build Python services.",
        url="https://www.naukri.com/python-developer-123",
        source="naukri",
    )


def test_scraper_initialization():
    scraper = NaukriScraper()
    assert scraper.base_url == "https://www.naukri.com"
    assert "article.jobTuple" in scraper.JOB_CARD_SELECTORS
    assert "styles_job-listing-container" in scraper.JOB_CARD_SELECTORS
    assert scraper.browser_timeout == 30000
    assert scraper.USER_AGENT.startswith("Mozilla/5.0 (Macintosh")


def test_scrape_jobs_uses_browser_search_url():
    scraper = NaukriScraper()
    with patch.object(scraper, "_scrape_with_browser", return_value=[]) as mock_browser, \
         patch.object(scraper, "_scrape_with_http", return_value=[]):
        jobs = scraper.scrape_jobs(["product manager"], ["bangalore"])

    assert jobs == []
    mock_browser.assert_called_once_with(
        "https://www.naukri.com/product-manager-jobs-in-bangalore"
    )


def test_scrape_page_waits_for_cards_and_maps_rendered_dom():
    scraper = NaukriScraper()
    card = FakeCard(
        {
            "a.title": FakeElement("Python Developer", "/python-developer-123"),
            "a.comp-name": FakeElement("Test Company"),
            "span.loc-wrap": FakeElement("Bangalore"),
            "span.exp-wrap": FakeElement("2-4 years"),
            "span.sal-wrap": FakeElement("₹5,00,000 - ₹8,00,000"),
            "span.job-post-day": FakeElement("2 days ago"),
        }
    )
    page = FakePage([card])

    with patch.object(scraper, "_rate_limit"):
        jobs = scraper._scrape_page(page, "https://www.naukri.com/python-developer-jobs-in-bangalore")

    assert len(jobs) == 1
    assert jobs[0].title == "Python Developer"
    assert jobs[0].company == "Test Company"
    assert jobs[0].location == "Bangalore"
    assert jobs[0].salary == "5,00,000 - ₹8,00,000"
    assert jobs[0].url == "https://www.naukri.com/python-developer-123"
    assert page.goto_calls[0][0].endswith("python-developer-jobs-in-bangalore")
    assert page.wait_calls[0] == (
        ".cust-job-tuple, [data-job-id], .srp-jobtuple-wrapper, div.row1",
        {"timeout": 30000},
    )
    assert page.evaluate_script == "window.scrollBy(0, 500)"


def test_scrape_page_falls_back_to_beautifulsoup_on_wait_timeout():
    scraper = NaukriScraper()
    page = MagicMock()
    page.wait_for_selector.side_effect = Exception("Timeout 30000ms exceeded")
    page.content.return_value = """
    <div class="srp-jobtuple-wrapper">
        <a class="title" href="/job-details/python-dev-123">Python Developer</a>
        <a class="comp-name">Acme Corp</a>
        <span class="loc-wrap">Bangalore</span>
    </div>
    """
    with patch.object(scraper, "_rate_limit"):
        jobs = scraper._scrape_page(page, "https://www.naukri.com/python-developer-jobs")

    assert len(jobs) == 1
    assert jobs[0].title == "Python Developer"
    assert jobs[0].company == "Acme Corp"
    assert jobs[0].location == "Bangalore"


def test_scrape_jobs_returns_empty_when_playwright_fails():
    scraper = NaukriScraper()
    with patch.object(scraper, "_scrape_with_browser", return_value=[]), \
         patch.object(scraper, "_scrape_with_http", return_value=[]):
        assert scraper.scrape_jobs(["python developer"], ["pune"]) == []


def test_scrape_jobs_falls_back_to_http_parser():
    scraper = NaukriScraper()
    with patch.object(scraper, "_scrape_with_browser", return_value=[]), \
         patch.object(scraper, "_scrape_with_http", return_value=[make_job()]) as mock_http:
        jobs = scraper.scrape_jobs(["python developer"], ["bangalore"])
        assert len(jobs) == 1
        mock_http.assert_called_once()


def test_api_fallback_maps_job_details():
    scraper = NaukriScraper()
    response = MagicMock()
    response.json.return_value = {
        "jobDetails": [{
            "title": "Python Developer",
            "companyName": "Acme",
            "placeholders": [
                {"type": "location", "value": "Pune"},
                {"type": "salary", "value": "₹8-12 Lacs P.A."},
            ],
            "jobDescription": "Build APIs.",
            "jdURL": "/python-developer-123",
            "createdDate": "2026-09-24",
        }]
    }

    with patch.object(scraper.session, "get", return_value=response) as mock_get:
        jobs = scraper._fetch_api_jobs("python developer", "pune")

    assert mock_get.call_args.kwargs["params"] == {
        "noOfResults": 20,
        "urlType": "search_by_keyword",
        "searchType": "adv",
        "keyword": "python developer",
        "location": "pune",
        "pageNo": 1,
        "k": "python developer",
        "l": "pune",
    }
    assert mock_get.call_args.args == ("https://www.naukri.com/jobapi/v3/search",)
    response.raise_for_status.assert_called_once_with()
    assert jobs[0].title == "Python Developer"
    assert jobs[0].company == "Acme"
    assert jobs[0].location == "Pune"
    assert jobs[0].source == "naukri"


def test_api_location_uses_placeholder_label():
    scraper = NaukriScraper()
    job = scraper._parse_api_job(
        {
            "title": "Machine Learning Intern",
            "companyName": "Acme",
            "placeholders": [{"type": "location", "label": "Bangalore"}],
        },
        "",
    )

    assert job.location == "Bangalore"


def test_generate_search_urls():
    scraper = NaukriScraper()
    urls = scraper._generate_search_urls(["python developer"], ["Bangalore"])
    assert urls == ["https://www.naukri.com/python-developer-jobs-in-bangalore"]


def test_static_card_helpers_remain_compatible():
    scraper = NaukriScraper()
    html = """
    <div class="srp-jobtuple-wrapper">
        <a class="title" href="/job-details/python-developer-123">Python Developer</a>
        <a class="comp-name">Test Company</a>
        <span class="loc-wrap">Bangalore</span>
    </div>
    """
    card = BeautifulSoup(html, "html.parser").find("div", class_="srp-jobtuple-wrapper")
    job = scraper._parse_job_card(card)
    assert isinstance(job, Job)
    assert job.title == "Python Developer"
    assert job.url == "https://www.naukri.com/job-details/python-developer-123"


def test_remove_duplicates_is_case_insensitive():
    scraper = NaukriScraper()
    first = make_job()
    second = make_job()
    second.title = "python developer"
    second.company = "test company"
    second.location = "bangalore"
    assert len(scraper._remove_duplicates([first, second])) == 1


def test_format_date_and_salary():
    scraper = NaukriScraper()
    assert scraper._format_date("2026-09-23") == "2026-09-23"
    assert len(scraper._format_date("2 days ago")) == 10
    assert scraper._format_salary("") == "Not specified"
    assert "5,00,000" in scraper._format_salary("₹5,00,000 per annum")
