"""
Naukri scraper: undetected-chromedriver (primary) → HTTP/BeautifulSoup → Naukri REST API.

Supports both async execution for the concurrency orchestrator and backwards-compatible
synchronous methods for unit and integration testing.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx
import requests
from bs4 import BeautifulSoup, Tag

try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    _UC_AVAILABLE = True
except ImportError:
    _UC_AVAILABLE = False

try:
    from playwright.sync_api import Locator, sync_playwright
    from playwright.async_api import async_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

try:
    from playwright_stealth import stealth_sync
except ImportError:
    try:
        from playwright_stealth import Stealth

        def stealth_sync(page):
            Stealth().apply_stealth_sync(page)
    except ImportError:
        def stealth_sync(page):
            pass

from src.config import get_logger, settings
from src.models import Job
from src.models.job import parse_date
from src.processors.query_parser import ParsedQuery, QueryParser
from src.scrapers.base import BaseScraper
from src.utils.ua_rotator import build_request_headers, get_random_ua

logger = logging.getLogger(__name__)


class NaukriScraper(BaseScraper):
    """Scrape Naukri jobs with multi-tier anti-bot and API fallback."""

    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    )
    API_URL = "https://www.naukri.com/jobapi/v3/search"
    JOB_CARD_SELECTORS = (
        "article.jobTuple, div.srp-jobtuple-wrapper, div.cust-job-tuple, "
        "div[data-job-id], [class*='styles_job-listing-container'], .jobTuple"
    )

    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self.base_url = "https://www.naukri.com"
        self._BASE_URL = self.base_url
        self._API_URL = self.API_URL
        self.query_parser = QueryParser()

        # Strict API credentials matching Naukri WAF requirements:
        # appid: "109", systemid: "naukri", and clientid from settings/.env
        self.API_HEADERS = {
            "User-Agent": get_random_ua(),
            "appid": "109",
            "systemid": "naukri",
            "clientid": getattr(settings, "NAUKRI_CLIENT_ID", "d3Wh4DeBn0q20"),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.naukri.com/",
            "Origin": "https://www.naukri.com",
        }

        self.session = requests.Session()
        self.session.headers.update(self.API_HEADERS)

        self.request_delay = getattr(settings, "REQUEST_DELAY", 2.0)
        self._request_delay = self.request_delay
        self.last_request_time = 0.0
        self.max_pages = 1

        timeout_sec = int(getattr(settings, "BROWSER_TIMEOUT", 30))
        self.browser_timeout = timeout_sec * 1000 if timeout_sec < 1000 else timeout_sec
        self._browser_timeout_ms = self.browser_timeout

    # ------------------------------------------------------------------ #
    # BaseScraper interface                                                #
    # ------------------------------------------------------------------ #

    @property
    def source_name(self) -> str:
        return "naukri"

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.head(
                    self.base_url,
                    headers=build_request_headers(),
                    follow_redirects=True,
                )
                return r.status_code < 500
        except Exception as exc:
            self.logger.warning("[naukri] health_check failed: %s", exc)
            return False

    async def scrape(
        self,
        keywords: list[str],
        locations: list[str],
        *,
        max_results: int = 50,
    ) -> list[Job]:
        """Async scrape entry point used by main.py concurrency orchestrator."""
        self.logger.info(
            "[naukri] scrape started — keywords=%s locations=%s", keywords, locations
        )
        try:
            jobs = await asyncio.to_thread(self.scrape_jobs, keywords, locations)
            return jobs[:max_results]
        except Exception as exc:
            self.logger.error("[naukri] scrape failed: %s", exc, exc_info=True)
            return []

    # ------------------------------------------------------------------ #
    # Synchronous scraping pipeline                                        #
    # ------------------------------------------------------------------ #

    def scrape_jobs(self, keywords: List[str], locations: List[str]) -> List[Job]:
        """Scrape Naukri search pages through browser with fallbacks."""
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
                        self.logger.info(
                            "Browser returned no jobs; falling back to HTTP parser for %s",
                            target_url,
                        )
                        jobs = self._scrape_with_http(target_url)
                    if not jobs:
                        self.logger.info(
                            "HTTP fallback returned no jobs; invoking Naukri API fallback for %s",
                            keyword,
                        )
                        jobs = self._fetch_api_jobs(keyword, location)
                    all_jobs.extend(jobs)

        unique_jobs = self._remove_duplicates(all_jobs)
        self.logger.info("Found %d unique jobs from Naukri", len(unique_jobs))
        return unique_jobs

    def _scrape_with_browser(self, url: str) -> List[Job]:
        """
        Render search URL with undetected-chromedriver and parse cards.
        Includes anti-bot evasion arguments and explicit WebDriverWait for job tuples.
        """
        if not _UC_AVAILABLE:
            self.logger.warning("undetected-chromedriver not installed; skipping browser scrape")
            return []

        driver = None
        try:
            options = uc.ChromeOptions()
            # Anti-bot stealth arguments to bypass Cloudflare/Akamai fingerprinting
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--start-maximized")
            options.add_argument("--disable-infobars")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-popup-blocking")

            # Match browser UA with dynamic rotator
            browser_ua = get_random_ua()
            options.add_argument(f"--user-agent={browser_ua}")

            self._rate_limit()
            # Launch undetected-chromedriver in non-headless mode to allow challenge resolution
            driver = uc.Chrome(options=options, headless=False)
            driver.get(url)

            # Explicit WebDriverWait (15s) for job card container to allow Cloudflare challenge to clear
            card_presence_css = (
                ".srp-jobtuple-wrapper, .jobTuple, article.jobTuple, "
                "div.srp-jobtuple-wrapper, div.cust-job-tuple, div[data-job-id]"
            )
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, card_presence_css))
                )
                # Human-like micro-scroll and settling delay so lazy-loaded cards hydrate
                driver.execute_script("window.scrollBy(0, 400);")
                time.sleep(1.0)
            except Exception as wait_exc:
                self.logger.warning(
                    "WebDriverWait timed out after 15s waiting for job cards (%s). "
                    "Current page title: '%s'. Parsing available DOM.",
                    wait_exc,
                    getattr(driver, "title", "Unknown"),
                )

            soup = BeautifulSoup(driver.page_source, "html.parser")
            cards = self._find_job_cards(soup)
            jobs = []
            for card in cards:
                job = self._parse_job_card(card)
                if job and job.title:
                    jobs.append(job)

            if jobs:
                self.logger.info("undetected-chromedriver parsed %d jobs from %s", len(jobs), url)
            return jobs

        except Exception as exc:
            self.logger.warning("undetected-chromedriver failed (%s); falling back", exc)
            return []
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _scrape_page(self, page: Any, url: str) -> List[Job]:
        """Navigate to rendered page and parse cards, with BeautifulSoup fallback."""
        self._rate_limit()
        page.goto(url, wait_until="domcontentloaded", timeout=self.browser_timeout)
        try:
            page.evaluate("window.scrollBy(0, 500)")
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
            self.logger.warning("Playwright wait timed out (%s); falling back to BeautifulSoup", wait_exc)

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
                    return jobs
        except Exception as parse_exc:
            self.logger.warning("BeautifulSoup page fallback failed: %s", parse_exc)

        return []

    def _scrape_with_http(self, url: str) -> List[Job]:
        """Fetch static HTML via requests session and parse job cards."""
        try:
            self._rate_limit()
            headers = {
                "User-Agent": get_random_ua(),
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
                    self.logger.info("HTTP/BeautifulSoup parser found %d jobs from %s", len(jobs), url)
                    return jobs
        except Exception as exc:
            self.logger.warning("HTTP scrape failed for %s: %s", url, exc)
        return []

    def _fetch_html_with_retry(self, url: str, retries: int = 2) -> Optional[str]:
        """Fetch HTML with retries, falling back to sync Playwright browser."""
        for attempt in range(retries):
            try:
                self._rate_limit()
                headers = {"User-Agent": get_random_ua()}
                response = self.session.get(url, headers=headers, timeout=settings.TIMEOUT)
                if response.status_code == 200 and response.text:
                    return response.text
                self.logger.warning("Naukri HTML request returned HTTP %s", response.status_code)
            except requests.RequestException as exc:
                self.logger.warning("Naukri HTML attempt %d failed: %s", attempt + 1, exc)
            if attempt < retries - 1:
                time.sleep(1)

        if not _PLAYWRIGHT_AVAILABLE:
            return None

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                        "--disable-gpu",
                    ],
                )
                context = browser.new_context(
                    user_agent=get_random_ua(),
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

    def _fetch_api_jobs(self, keyword: str, location: str) -> List[Job]:
        """
        Fetch listings via internal Naukri REST API.
        Strictly requires appid: '109', systemid: 'naukri', clientid from env,
        and dynamically synchronized User-Agent from ua_rotator.
        """
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
            # Match User-Agent dynamically with ua_rotator
            current_ua = get_random_ua()
            self.API_HEADERS.update({
                "User-Agent": current_ua,
                "appid": "109",
                "systemid": "naukri",
                "clientid": getattr(settings, "NAUKRI_CLIENT_ID", "d3Wh4DeBn0q20"),
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.naukri.com/",
                "Origin": "https://www.naukri.com",
            })
            self.session.headers.update(self.API_HEADERS)

            response = self.session.get(
                self.API_URL,
                params=params,
                headers=self.API_HEADERS,
                timeout=settings.TIMEOUT,
            )
            if response.status_code in (403, 406):
                raise requests.HTTPError(
                    f"Naukri API blocked request with HTTP {response.status_code}: {response.text[:120]}"
                )
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

    def _find_job_cards(self, soup: BeautifulSoup) -> List[Tag]:
        for selector in [
            ".srp-jobtuple-wrapper",
            "div.srp-jobtuple-wrapper",
            ".jobTuple",
            "article.jobTuple",
            "div.jobTuple",
            "div.cust-job-tuple",
            "div[data-job-id]",
            "[class*='styles_job-listing-container']",
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
        title = self._extract_text(card, ["a.title", "div.row1 a", "h2 a", ".job-title a", "a[class*='title']"])
        if not title:
            return None
        description = self._extract_text(card, [".job-description", ".description", ".job-desc", "div.row4"])
        company = self._clean_company_name(
            self._extract_text(card, ["a.comp-name", "div.row2 span a", ".company-name a", "a[class*='comp-name']"])
        )
        return self._make_job(
            title=title,
            company=company or "N/A",
            location=self._extract_text(card, ["span.loc-wrap", ".location span", ".job-location", "span[class*='loc']"]) or "Not specified",
            description=description,
            salary=self._extract_text(card, ["span.sal-wrap", ".salary span", "span[class*='sal']"]),
            url=self._extract_attribute(card, ["a.title", "div.row1 a", "h2 a", "a[class*='title']"], "href"),
            posted_date=self._extract_text(card, ["span.job-post-day", ".posting-date", "span[class*='date']"]),
        )

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
        full_url = urljoin(self.base_url, url)
        salary_fmt = self._format_salary(salary)
        return Job(
            title=title,
            company=company,
            location=location or "Not specified",
            description=description,
            salary=salary_fmt,
            salary_raw=salary_fmt if salary_fmt != "Not specified" else None,
            url=full_url,
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
        parsed = parse_date(value)
        return parsed or value

    def _rate_limit(self) -> None:
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
            key = (
                (job.title or "").strip().lower(),
                (job.company or "").strip().lower(),
                (job.location or "").strip().lower(),
            )
            if key not in seen:
                seen.add(key)
                unique.append(job)
        return unique
