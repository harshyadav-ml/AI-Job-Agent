"""Naukri scraper: undetected-chromedriver (primary) → HTTP/BeautifulSoup → Naukri REST API."""

import logging
import random
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag
from playwright.sync_api import Locator, sync_playwright

try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    _UC_AVAILABLE = True
except ImportError:
    _UC_AVAILABLE = False

try:
    from playwright_stealth import stealth_sync
except ImportError:
    try:
        from playwright_stealth import Stealth

        def stealth_sync(page):
            """Apply stealth with the current playwright-stealth API."""
            Stealth().apply_stealth_sync(page)
    except ImportError:
        def stealth_sync(page):
            pass

from src.config import get_logger, settings
from src.models import Job
from src.processors.query_parser import ParsedQuery, QueryParser
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class NaukriScraper(BaseScraper):
    """Scrape Naukri jobs while tolerating API and bot-protection failures."""

    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    )
    API_URL = "https://www.naukri.com/jobapi/v3/search"
    API_HEADERS = {
        "User-Agent": USER_AGENT,
        "appid": "109",
        "systemid": "109",
        "clientid": "d3Wh4DeBn0q20",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    JOB_CARD_SELECTORS = (
        "article.jobTuple, div.srp-jobtuple-wrapper, div.cust-job-tuple, "
        "div[data-job-id], [class*='styles_job-listing-container']"
    )

    def __init__(self):
        self.logger = get_logger(__name__)
        self.base_url = "https://www.naukri.com"
        self.query_parser = QueryParser()
        self.session = requests.Session()
        self.session.headers.update(self.API_HEADERS)
        self.request_delay = getattr(settings, "REQUEST_DELAY", 2.0)
        self.last_request_time = 0.0
        self.max_pages = 1
        timeout_sec = int(getattr(settings, "BROWSER_TIMEOUT", 30))
        self.browser_timeout = timeout_sec * 1000 if timeout_sec < 1000 else timeout_sec

    @property
    def source_name(self) -> str:
        return "naukri"

    def scrape_jobs(self, keywords: List[str], locations: List[str]) -> List[Job]:
        """Scrape Naukri search pages through the stealth browser with fallback."""
        self.logger.info(
            "Starting Naukri scraping for keywords: %s, locations: %s",
            keywords,
            locations,
        )
        all_jobs: List[Job] = []
        search_locations = locations or [""]

        for keyword in keywords[:2]:
            for location in search_locations[:2]:
                base_url = self._build_search_url(keyword, location)
                for page in range(1, self.max_pages + 1):
                    target_url = f"{base_url}-{page}" if page > 1 else base_url
                    self.logger.info("Scraping Naukri search URL: %s", target_url)
                    jobs = self._scrape_with_browser(target_url)
                    if not jobs:
                        self.logger.info("Browser returned no jobs; falling back to HTTP/BeautifulSoup parser for %s", target_url)
                        jobs = self._scrape_with_http(target_url)
                    if not jobs:
                        self.logger.info(
                            "HTTP fallback returned no jobs; invoking Naukri API fallback for %s", keyword
                        )
                        jobs = self._fetch_api_jobs(keyword, location)
                    all_jobs.extend(jobs)

        unique_jobs = self._remove_duplicates(all_jobs)
        self.logger.info("Found %d unique jobs from Naukri", len(unique_jobs))
        return unique_jobs

    def scrape(self, role: str, location: Optional[str] = None) -> List[Job]:
        """Parse a natural-language query or role/location and scrape Naukri results."""
        if location:
            return self.scrape_jobs([role], [location])
        parsed_query = self.query_parser.parse(role)
        if parsed_query.locations:
            return self.scrape_jobs([parsed_query.keywords], parsed_query.locations)
        return self.scrape_jobs([role], [])

    def _fetch_api_jobs(self, keyword: str, location: str) -> List[Job]:
        """Fetch one Naukri API response and map its jobDetails."""
        params = {
            "noOfResults": 20,
            "urlType": "search_by_keyword",
            "searchType": "adv",
            "keyword": keyword,
            "location": location,
            "pageNo": 1,
            "k": keyword,
            "l": location,
        }
        try:
            self._rate_limit()
            response = self.session.get(self.API_URL, params=params, timeout=settings.TIMEOUT)
            if response.status_code in (403, 406):
                raise requests.HTTPError(f"Naukri API blocked request with HTTP {response.status_code}")
            response.raise_for_status()
            payload = response.json()
            details = payload.get("jobDetails", []) if isinstance(payload, dict) else []
            if not isinstance(details, list):
                return []
            jobs = []
            for item in details:
                if not isinstance(item, dict):
                    continue
                job = self._parse_api_job(item, location)
                if job and job.title:
                    jobs.append(job)
            return jobs
        except Exception as exc:
            self.logger.warning("Naukri API request failed: %s", exc)
            return []

    def _parse_api_job(self, item: Dict[str, Any], fallback_location: str = "") -> Optional[Job]:
        placeholders = item.get("placeholders") or []
        location = self._extract_placeholder(placeholders, "location") or fallback_location.title()
        salary = item.get("salary") or self._extract_placeholder(placeholders, "salary")
        url = item.get("jdURL") or item.get("staticUrl") or ""
        if not url and item.get("jobId"):
            url = f"/job-listings-{item['jobId']}"
        return self._make_job(
            title=item.get("title") or "",
            company=item.get("companyName") or "",
            location=location,
            description=item.get("jobDescription") or "",
            salary=salary or "Not specified",
            url=url,
            posted_date=item.get("createdDate") or "",
        )

    def _scrape_with_browser(self, url: str) -> List[Job]:
        """Render one search URL with undetected-chromedriver and parse cards via BeautifulSoup.

        Falls back cleanly to the HTTP/BeautifulSoup parser if UC is unavailable or
        the driver raises any exception.
        """
        if not _UC_AVAILABLE:
            self.logger.warning("undetected-chromedriver not installed; skipping browser scrape")
            return []

        driver = None
        try:
            options = uc.ChromeOptions()
            options.add_argument("--no-sandbox")
            options.add_argument("--window-size=1440,900")
            options.add_argument("--disable-dev-shm-usage")

            self._rate_limit()
            driver = uc.Chrome(version_main=153, options=options, headless=False)
            driver.get(url)

            # Allow Cloudflare clearance to finish and React root to render
            time.sleep(5)

            # Attempt an immediate parse on whatever is already rendered
            soup = BeautifulSoup(driver.page_source, "html.parser")
            cards = self._find_job_cards(soup)
            jobs = []
            for card in cards:
                job = self._parse_job_card(card)
                if job and job.title:
                    jobs.append(job)
            if jobs:
                self.logger.info(
                    "undetected-chromedriver early-parse found %d jobs from %s", len(jobs), url
                )
                return jobs

            # No cards yet — wait for the job list to appear and try once more
            card_css = "#listContainer, div.srp-jobtuple-wrapper, div[data-job-id]"
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, card_css))
                )
            except Exception as wait_exc:
                self.logger.warning(
                    "UC wait_for_element timed out (%s); parsing whatever was rendered",
                    wait_exc,
                )

            soup = BeautifulSoup(driver.page_source, "html.parser")
            cards = self._find_job_cards(soup)
            jobs = []
            for card in cards:
                job = self._parse_job_card(card)
                if job and job.title:
                    jobs.append(job)

            if jobs:
                self.logger.info(
                    "undetected-chromedriver parsed %d jobs from %s", len(jobs), url
                )
            return jobs

        except Exception as exc:
            self.logger.warning(
                "undetected-chromedriver failed (%s); falling back to HTTP/BeautifulSoup parser",
                exc,
            )
            return []
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _scrape_page(self, page: Any, url: str) -> List[Job]:
        """Navigate to a rendered page and parse cards, falling back to BeautifulSoup if wait times out."""
        self._rate_limit()
        page.goto(url, wait_until="domcontentloaded", timeout=self.browser_timeout)
        try:
            page.evaluate("window.scrollBy(0, 500)")
        except Exception:
            try:
                page.mouse.wheel(0, 500)
            except Exception:
                pass
        card_selector = ".cust-job-tuple, [data-job-id], .srp-jobtuple-wrapper, div.row1"
        try:
            page.wait_for_selector(card_selector, timeout=self.browser_timeout)
            cards = page.locator(card_selector)
            jobs = []
            for index in range(cards.count()):
                job = self._parse_rendered_card(cards.nth(index))
                if job and job.title:
                    jobs.append(job)
            if jobs:
                return jobs
        except Exception as wait_exc:
            self.logger.warning(
                "Naukri Playwright wait_for_selector timed out (%s); falling back to BeautifulSoup HTML parser on page content",
                wait_exc,
            )

        # Clean fallback to BeautifulSoup on rendered page content
        try:
            html = page.content() if hasattr(page, "content") else ""
            if html:
                soup = BeautifulSoup(html, "html.parser")
                cards = self._find_job_cards(soup)
                jobs = []
                for card in cards:
                    job = self._parse_job_card(card)
                    if job and job.title:
                        jobs.append(job)
                if jobs:
                    self.logger.info("BeautifulSoup page fallback parsed %d jobs", len(jobs))
                    return jobs
        except Exception as parse_exc:
            self.logger.warning("BeautifulSoup page content fallback failed: %s", parse_exc)

        return []

    def _scrape_with_http(self, url: str) -> List[Job]:
        """Fetch static HTML with requests and parse job cards via BeautifulSoup."""
        try:
            self._rate_limit()
            headers = {
                "User-Agent": self.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.google.com/",
            }
            response = self.session.get(url, headers=headers, timeout=settings.TIMEOUT)
            if response.status_code == 200 and response.text:
                soup = BeautifulSoup(response.text, "html.parser")
                cards = self._find_job_cards(soup)
                jobs = []
                for card in cards:
                    job = self._parse_job_card(card)
                    if job and job.title:
                        jobs.append(job)
                if jobs:
                    self.logger.info("HTTP/BeautifulSoup parser parsed %d jobs from %s", len(jobs), url)
                    return jobs
        except Exception as exc:
            self.logger.warning("HTTP/BeautifulSoup scrape failed for %s: %s", url, exc)
        return []

    def _fetch_html_with_retry(self, url: str, retries: int = 2) -> Optional[str]:
        """Fetch static HTML with retries, then use a browser page if blocked."""
        for attempt in range(retries):
            try:
                self._rate_limit()
                response = self.session.get(url, timeout=settings.TIMEOUT)
                if response.status_code == 200 and response.text:
                    return response.text
                self.logger.warning("Naukri HTML request returned HTTP %s", response.status_code)
            except requests.RequestException as exc:
                self.logger.warning("Naukri HTML attempt %d failed: %s", attempt + 1, exc)
            if attempt < retries - 1:
                time.sleep(1)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                    ],
                )
                context = browser.new_context(
                    user_agent=self.USER_AGENT,
                    viewport={"width": 1366, "height": 768},
                    locale="en-US",
                )
                page = context.new_page()
                stealth_sync(page)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=self.browser_timeout)
                    return page.content()
                finally:
                    context.close()
                    browser.close()
        except Exception as exc:
            self.logger.warning("Naukri browser HTML fallback failed: %s", exc)
            return None

    def _build_search_url(self, keyword: str, location: str) -> str:
        role_slug = self.query_parser.role_slug(keyword)
        location_slug = self.query_parser.location_slug(location)
        if location_slug:
            return f"{self.base_url}/{role_slug}-jobs-in-{location_slug}"
        return f"{self.base_url}/{role_slug}-jobs"

    def _generate_search_urls(self, keywords: List[str], locations: List[str]) -> List[str]:
        return [
            self._build_search_url(keyword, location)
            for keyword in keywords[:2]
            for location in (locations[:2] if locations else [""])
        ]

    def _parse_rendered_card(self, card: Any) -> Optional[Job]:
        title = self._locator_text(card, ["a.title", "div.row1 a", "h2 a", ".job-title a"])
        if not title:
            return None
        description = self._locator_text(card, [".job-description", ".description"])
        return self._make_job(
            title=title,
            company=self._locator_text(card, ["a.comp-name", "div.row2 span a", ".company-name a"]),
            location=self._locator_text(card, ["span.loc-wrap", ".location span", ".job-location"]),
            description=description or self._locator_text(card, ["span.exp-wrap", ".experience span"]),
            salary=self._locator_text(card, ["span.sal-wrap", ".salary span"]),
            url=self._locator_attribute(card, ["a.title", "div.row1 a"], "href"),
            posted_date=self._locator_text(card, ["span.job-post-day", ".posting-date"]),
        )

    def _find_job_cards(self, soup: BeautifulSoup) -> List[Tag]:
        for selector in [
            "article.jobTuple",
            "div.srp-jobtuple-wrapper",
            "div.cust-job-tuple",
            "div[data-job-id]",
            "[class*='styles_job-listing-container']",
            "div.jobTuple",
        ]:
            cards = soup.select(selector)
            if cards:
                return cards
        return []

    def _clean_company_name(self, name: str) -> str:
        name = re.sub(r"\s+", " ", name or "").strip()
        name = re.sub(r"\s+\d+(\.\d+)?\s*\**$", "", name)
        name = re.sub(r"\s+\d+(\.\d+)?\s*(Reviews?|Ratings?|Followers?).*$", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\s+\d+k\+?\s*(Reviews?|Ratings?|Followers?).*$", "", name, flags=re.IGNORECASE)
        return name.strip()

    def _parse_job_card(self, card: Tag) -> Optional[Job]:
        title = self._extract_text(card, ["a.title", "div.row1 a", "h2 a", ".job-title a"])
        if not title:
            return None
        description = self._extract_text(card, [".job-description", ".description"])
        company = self._clean_company_name(self._extract_text(card, ["a.comp-name", "div.row2 span a", ".company-name a"]))
        return self._make_job(
            title=title,
            company=company or "N/A",
            location=self._extract_text(card, ["span.loc-wrap", ".location span", ".job-location"]) or "Not specified",
            description=description,
            salary=self._extract_text(card, ["span.sal-wrap", ".salary span"]),
            url=self._extract_attribute(card, ["a.title", "div.row1 a"], "href"),
            posted_date=self._extract_text(card, ["span.job-post-day", ".posting-date"]),
        )

    def _make_job(
        self,
        title: str,
        company: str,
        location: str,
        description: str,
        salary: str,
        url: str,
        posted_date: str,
    ) -> Job:
        job_type = "Internship" if "intern" in f"{title} {description}".lower() else "Full-time"
        return Job(
            title=title,
            company=company,
            location=location,
            description=description,
            salary=self._format_salary(salary),
            url=urljoin(self.base_url, url),
            posted_date=self._format_date(posted_date),
            job_type=job_type,
            source="naukri",
            scraped_at=datetime.utcnow().isoformat(),
        )

    @staticmethod
    def _extract_placeholder(placeholders: List[Any], value_type: str) -> str:
        for placeholder in placeholders:
            if not isinstance(placeholder, dict):
                continue
            ptype = str(placeholder.get("type") or "").lower()
            if value_type.lower() in ptype:
                return str(
                    placeholder.get("label")
                    or placeholder.get("value")
                    or placeholder.get("text")
                    or ""
                ).strip()
        return ""

    def _locator_text(self, element: Any, selectors: List[str]) -> str:
        for selector in selectors:
            found = element.locator(selector).first
            if found.count():
                text = found.inner_text().strip()
                if text:
                    return text
        return ""

    def _locator_attribute(self, element: Any, selectors: List[str], attribute: str) -> str:
        for selector in selectors:
            found = element.locator(selector).first
            if found.count():
                value = found.get_attribute(attribute)
                if value:
                    return value
        return ""

    def _extract_text(self, element: Tag, selectors: List[str]) -> str:
        for selector in selectors:
            found = element.select_one(selector)
            if found and found.get_text(strip=True):
                return found.get_text(strip=True)
        return ""

    def _extract_attribute(self, element: Tag, selectors: List[str], attribute: str) -> str:
        for selector in selectors:
            found = element.select_one(selector)
            if found and found.get(attribute):
                return found.get(attribute, "")
        return ""

    def _format_salary(self, value: str) -> str:
        if not value:
            return "Not specified"
        value = re.sub(r"^(salary|₹|rs\.?\s*)", "", value.strip(), flags=re.IGNORECASE)
        return re.sub(r"\s*(per annum|p\.a\.|annually)$", "", value, flags=re.IGNORECASE).strip()

    def _format_date(self, value: str) -> str:
        if not value:
            return ""
        normalized = value.strip().lower()
        match = re.search(r"(\d+)\s+(day|hour|week|month)", normalized)
        if "ago" in normalized and match:
            amount = int(match.group(1))
            units = {"day": amount, "hour": amount / 24, "week": amount * 7, "month": amount * 30}
            return (datetime.now() - timedelta(days=units[match.group(2)])).strftime("%Y-%m-%d")
        for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(normalized, date_format).strftime("%Y-%m-%d")
            except ValueError:
                pass
        return value

    def _rate_limit(self):
        if self.request_delay <= 0:
            self.last_request_time = time.time()
            return
        if self.last_request_time > 0:
            elapsed = time.time() - self.last_request_time
            if elapsed < self.request_delay:
                time.sleep(self.request_delay - elapsed)
        self.last_request_time = time.time()

    def _remove_duplicates(self, jobs: List[Job]) -> List[Job]:
        seen = set()
        unique = []
        for job in jobs:
            key = f"{job.title.lower()}|{job.company.lower()}|{job.location.lower()}"
            if key not in seen:
                seen.add(key)
                unique.append(job)
        return unique
