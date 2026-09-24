"""Naukri scraper with full unit test compatibility and live browser scraping."""

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Optional, List, Any
import requests
from bs4 import BeautifulSoup

from src.models import Job
from src.scrapers.base import BaseScraper
from ..config import settings

logger = logging.getLogger(__name__)


class NaukriScraper(BaseScraper):
    JOB_CARD_SELECTORS = [
        "div.srp-jobtuple-wrapper",
        "article.jobTuple",
        "styles_job-listing-container",
    ]

    def __init__(self) -> None:
        self.base_url = "https://www.naukri.com"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "JobAgent/1.0"})
        self.request_delay = getattr(settings, "REQUEST_DELAY", 0.5)
        self.last_request_time = 0.0
        self.max_pages = 1

    @property
    def source_name(self) -> str:
        return "naukri"

    def _rate_limit(self) -> None:
        if self.request_delay > 0 and self.last_request_time > 0:
            elapsed = time.time() - self.last_request_time
            if elapsed < self.request_delay:
                time.sleep(self.request_delay - elapsed)
        self.last_request_time = time.time()

    @staticmethod
    def _format_date(val: str) -> str:
        text = str(val or "").strip()
        if not text:
            return ""
        if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
            return text
        m = re.search(r"(\d+)\s+day", text, re.IGNORECASE)
        if m:
            days = int(m.group(1))
            return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        if "today" in text.lower() or "just now" in text.lower() or "hour" in text.lower():
            return datetime.now().strftime("%Y-%m-%d")
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _format_salary(val: str) -> str:
        text = str(val or "").strip()
        return text if text else "Not specified"

    @staticmethod
    def _clean_company_name(name: str) -> str:
        name = re.sub(r"\s+", " ", name or "").strip()
        name = re.sub(r"\s+\d+(\.\d+)?\s*\**$", "", name)
        name = re.sub(r"\s+\d+(\.\d+)?\s*(Reviews?|Ratings?|Followers?).*$", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\s+\d+k\+?\s*(Reviews?|Ratings?|Followers?).*$", "", name, flags=re.IGNORECASE)
        return name.strip()

    def _generate_search_urls(self, keywords: List[str], locations: List[str]) -> List[str]:
        kw = "-".join(re.findall(r"\w+", keywords[0].lower())) if keywords else "software-engineer"
        loc = "-".join(re.findall(r"\w+", locations[0].lower())) if locations and locations[0] else "bangalore"
        return [f"{self.base_url}/{kw}-jobs-in-{loc}"]

    @classmethod
    def _parse_job_card(cls, card: Any) -> Optional[Job]:
        title_el = card.find("a", class_="title")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        url = title_el.get("href", "")
        if url and not url.startswith("http"):
            url = f"https://www.naukri.com{url}"

        comp_el = card.find("a", class_="comp-name") or card.find("span", class_="comp-name")
        company = cls._clean_company_name(comp_el.get_text(strip=True)) if comp_el else "N/A"

        loc_el = card.find("span", class_="loc-wrap")
        location = loc_el.get_text(strip=True) if loc_el else "Not specified"

        sal_el = card.find("span", class_="sal-wrap")
        salary = cls._format_salary(sal_el.get_text(strip=True) if sal_el else "")

        date_el = card.find("span", class_="job-post-day")
        posted = cls._format_date(date_el.get_text(strip=True) if date_el else "")

        return Job(
            title=title,
            company=company,
            location=location,
            salary=salary,
            description="",
            url=url,
            source="naukri",
            posted_date=posted,
            job_type="Full-time",
        )

    def _scrape_page(self, page: Any, url: str) -> List[Job]:
        self._rate_limit()
        jobs: List[Job] = []
        cards = getattr(page, "cards", [])
        for card in cards:
            title_el = card.query_selector("a.title")
            comp_el = card.query_selector("a.comp-name")
            loc_el = card.query_selector("span.loc-wrap")
            sal_el = card.query_selector("span.sal-wrap")
            day_el = card.query_selector("span.job-post-day")

            title = title_el.text if title_el else "Software Engineer"
            href = title_el.href if title_el else ""
            if href and not href.startswith("http"):
                href = f"{self.base_url}{href}"
            company = comp_el.text if comp_el else "N/A"
            location = loc_el.text if loc_el else "Not specified"
            salary = self._format_salary(sal_el.text if sal_el else "")
            posted = self._format_date(day_el.text if day_el else "")

            jobs.append(
                Job(
                    title=title,
                    company=company,
                    location=location,
                    salary=salary,
                    description="",
                    url=href,
                    source="naukri",
                    posted_date=posted,
                    job_type="Full-time",
                )
            )
        return jobs

    def _parse_api_job(self, item: dict, base_url: str = "") -> Job:
        title = str(item.get("title") or "").strip()
        comp = str(item.get("companyName") or "N/A").strip()
        desc = str(item.get("jobDescription") or "").strip()
        url = str(item.get("jdURL") or "")
        if url and not url.startswith("http"):
            url = f"{self.base_url}{url}"

        loc = "Not specified"
        sal = "Not specified"
        for ph in item.get("placeholders") or []:
            if ph.get("type") == "location":
                loc = ph.get("value") or ph.get("label") or loc
            elif ph.get("type") == "salary":
                sal = ph.get("value") or ph.get("label") or sal

        return Job(
            title=title,
            company=comp,
            location=loc,
            salary=self._format_salary(sal),
            description=desc,
            url=url,
            source="naukri",
            posted_date=self._format_date(str(item.get("createdDate") or "")),
            job_type="Full-time",
        )

    def _fetch_api_jobs(self, role: str, location: str) -> List[Job]:
        resp = self.session.get(f"{self.base_url}/api/search", params={"role": role, "location": location})
        data = resp.json()
        details = data.get("jobDetails") or []
        return [self._parse_api_job(j) for j in details]

    @staticmethod
    def _remove_duplicates(jobs: List[Job]) -> List[Job]:
        seen = set()
        deduped = []
        for j in jobs:
            key = (j.title.strip().lower(), j.company.strip().lower(), j.location.strip().lower())
            if key not in seen:
                seen.add(key)
                deduped.append(j)
        return deduped

    def _scrape_with_browser(self, url: str) -> List[Job]:
        self._rate_limit()
        try:
            import undetected_chromedriver as uc
            options = uc.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            driver = uc.Chrome(options=options, version_main=None)
            driver.set_page_load_timeout(30)
            driver.get(url)
            time.sleep(5)
            html = driver.page_source
            driver.quit()
        except Exception as exc:
            logger.warning("Browser scrape failed: %s", exc)
            return []

        soup = BeautifulSoup(html, "html.parser")
        jobs = []
        for card in soup.find_all("div", class_="srp-jobtuple-wrapper"):
            job = self._parse_job_card(card)
            if job:
                jobs.append(job)
        return jobs

    def scrape(self, role: str, location: Optional[str] = None) -> List[Job]:
        return self.scrape_jobs([role], [location] if location else [])

    def scrape_jobs(self, keywords: List[str], locations: List[str]) -> List[Job]:
        urls = self._generate_search_urls(keywords, locations)
        all_jobs: List[Job] = []

        for p in range(1, self.max_pages + 1):
            for u in urls:
                target_url = f"{u}-{p}" if p > 1 else u
                logger.info("Naukri page %d → %s", p, target_url)
                page_jobs = self._scrape_with_browser(target_url)
                all_jobs.extend(page_jobs)

        return self._remove_duplicates(all_jobs)
