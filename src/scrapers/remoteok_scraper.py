"""
RemoteOK job scraper using public API.
"""

import logging
import time
import re
from typing import List, Dict, Any, Optional
from datetime import datetime
import requests
from bs4 import BeautifulSoup

from src.config import get_logger, settings
from src.models import Job
from src.scrapers.base import BaseScraper

logger = get_logger(__name__)


class RemoteOKScraper(BaseScraper):
    """Scraper for RemoteOK job board using public API."""
    
    def __init__(self):
        """Initialize the RemoteOK scraper."""
        self.logger = get_logger(__name__)
        self.api_url = settings.REMOTEOK_API_URL
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Job-Agent/1.0 (https://github.com/yourusername/job-agent)',
            'Accept': 'application/json',
        })
        
        # Rate limiting settings
        self.request_delay = settings.REQUEST_DELAY
        self.last_request_time = 0.0

    @property
    def source_name(self) -> str:
        return "remoteok"
    
    def scrape_jobs(self, keywords: List[str], locations: List[str]) -> List[Job]:
        """
        Scrape jobs from RemoteOK based on keywords and locations.
        
        Args:
            keywords: List of job keywords to search
            locations: List of locations to filter
        
        Returns:
            List of Job objects
        """
        self.logger.info(f"Starting RemoteOK scraping for keywords: {keywords}, locations: {locations}")
        
        try:
            # Fetch jobs from API with rate limiting
            raw_jobs = self._fetch_jobs_from_api()
            
            # Filter jobs based on criteria
            filtered_jobs = self._filter_jobs(raw_jobs, keywords, locations)
            
            # Convert to Job objects
            jobs = [self._convert_to_job(job_data) for job_data in filtered_jobs]
            
            self.logger.info(f"Found {len(jobs)} jobs from RemoteOK after filtering")
            return jobs
            
        except Exception as e:
            self.logger.error(f"Error during RemoteOK scraping: {e}")
            return []

    def scrape(self, role: str, location: Optional[str] = None) -> List[Job]:
        """Fetch job listings matching role and optional location."""
        return self.scrape_jobs([role], [location] if location else [])
    
    def _fetch_jobs_from_api(self) -> List[Dict[str, Any]]:
        """
        Fetch jobs from RemoteOK API with rate limiting.
        
        Returns:
            List of job dictionaries from API
        """
        # Rate limiting
        self._rate_limit()
        
        try:
            self.logger.info(f"Fetching jobs from {self.api_url}")
            response = self.session.get(
                self.api_url,
                timeout=settings.TIMEOUT
            )
            response.raise_for_status()
            
            data = response.json()
            
            # First element is metadata, skip it
            if len(data) > 1 and 'legal' in data[0]:
                jobs_data = data[1:]
            else:
                jobs_data = data
            
            self.logger.info(f"Fetched {len(jobs_data)} jobs from RemoteOK API")
            return jobs_data
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"HTTP error fetching RemoteOK jobs: {e}")
            raise
        except ValueError as e:
            self.logger.error(f"JSON parsing error: {e}")
            raise
    
    def _rate_limit(self):
        """Implement rate limiting between requests."""
        current_time = time.time()
        time_since_last_request = current_time - self.last_request_time
        
        if self.last_request_time > 0 and time_since_last_request < self.request_delay:
            sleep_time = self.request_delay - time_since_last_request
            self.logger.debug(f"Rate limiting: sleeping for {sleep_time:.2f} seconds")
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()
    
    def _filter_jobs(self, jobs: List[Dict[str, Any]], keywords: List[str], locations: List[str]) -> List[Dict[str, Any]]:
        """
        Filter jobs by keywords and locations.
        
        Args:
            jobs: List of job dictionaries from API
            keywords: List of keywords to filter by
            locations: List of locations to filter by
        
        Returns:
            Filtered list of job dictionaries
        """
        self.logger.info(f"Filtering {len(jobs)} jobs by keywords: {keywords}, locations: {locations}")
        
        filtered_jobs = []
        
        for job in jobs:
            # Check keyword match
            keyword_match = self._matches_keywords(job, keywords)
            
            # Check location match
            location_match = self._matches_location(job, locations)
            
            if keyword_match and location_match:
                filtered_jobs.append(job)
        
        self.logger.info(f"Filtered down to {len(filtered_jobs)} jobs")
        return filtered_jobs
    
    # Generic role words that should NOT be used as standalone signals —
    # they appear on thousands of unrelated jobs (e.g. 'developer' on a Go job).
    _GENERIC_ROLE_WORDS = frozenset({
        'developer', 'engineer', 'dev', 'senior', 'junior', 'lead', 'staff',
        'intern', 'manager', 'specialist', 'analyst', 'architect',
    })

    def _matches_keywords(self, job: Dict[str, Any], keywords: List[str]) -> bool:
        """
        Check if a job matches any of the specified keywords.

        Matching passes (first match wins):
        1. Full keyword phrase appears anywhere in position+tags+description.
        2. Every individual word of the keyword appears somewhere in searchable text.
        3. The PRIMARY word of the keyword (first non-generic token, e.g. 'python' from
           'python developer') appears in the job's position or any tag — this is the key
           pass that catches 'Python Engineer', 'Python Backend', 'Senior Python Dev', etc.
        4. Any non-generic keyword word appears in position or tags (broadest fallback).

        Args:
            job: Job dictionary
            keywords: List of keywords to match

        Returns:
            True if job matches any keyword
        """
        if not keywords:
            return True

        position = str(job.get('position') or '').lower()
        raw_tags = job.get('tags', [])
        if isinstance(raw_tags, (list, tuple, set)):
            tags = [str(tag).lower() for tag in raw_tags]
        else:
            tags = [str(raw_tags).lower()] if raw_tags else []
        description = BeautifulSoup(
            str(job.get('description') or ''), 'html.parser'
        ).get_text().lower()

        searchable_text = f"{position} {' '.join(tags)} {description}"

        for keyword in keywords:
            kw = keyword.lower().strip()
            if not kw:
                continue

            # Pass 1: exact phrase
            if kw in searchable_text:
                return True

            words = [w for w in kw.split() if w]

            # Identify the primary (most specific/non-generic) word up-front
            specific_words = [w for w in words if len(w) > 2 and w not in self._GENERIC_ROLE_WORDS]
            primary_word = specific_words[0] if specific_words else None

            # Pass 2: all words present in searchable text AND primary word in position/tags
            # (guards against description mentions like "No python here" on a Java job)
            if words and all(w in searchable_text for w in words):
                if primary_word is None or (
                    primary_word in position or any(primary_word in tag for tag in tags)
                ):
                    return True

            # Pass 3: primary technology/role word present in position or any tag
            if primary_word and (
                primary_word in position
                or any(primary_word in tag for tag in tags)
            ):
                return True

            # Pass 4: any non-generic keyword word in position or tags (broadest)
            if any(
                w in position or any(w in tag for tag in tags)
                for w in words
                if len(w) > 2 and w not in self._GENERIC_ROLE_WORDS
            ):
                return True

        return False
    
    def _matches_location(self, job: Dict[str, Any], locations: List[str]) -> bool:
        """
        Check if a job matches requested locations or is an eligible remote listing.

        Rules (evaluated in order):
        1. If the listing has an explicit regional exclusion (US only, UK only, etc.)
           in its tags or description → always reject.
        2. If no locations were requested → reject non-remote listings (RemoteOK is
           remote-first; a blank location list still means we want remote-compatible jobs).
        3. If a specific city is requested:
           - Listings whose location field is blank, 'Remote', 'Worldwide', or
             'Anywhere' are preserved unconditionally (they are truly remote).
           - Listings whose location mentions an Indian city (fallback for local posts
             that leak into the API) are also preserved.
           - All other locations are rejected.

        Args:
            job: Job dictionary
            locations: List of locations to match

        Returns:
            True if job matches location criteria
        """
        job_location = str(job.get('location') or '').strip().lower()
        requested_locations = [str(loc).strip().lower() for loc in locations if loc]

        # Build tag info once
        raw_tags = job.get('tags', [])
        if isinstance(raw_tags, (list, tuple, set)):
            tags_list = [str(tag).lower() for tag in raw_tags]
            tags_text = ' '.join(str(tag) for tag in raw_tags)
        else:
            tags_list = [str(raw_tags).lower()] if raw_tags else []
            tags_text = str(raw_tags or '')

        # --- Step 1: Reject explicit regional exclusions (tags) ---
        restricted_tag_patterns = (
            r'\bus\s+only\b',
            r'\bu\.s\.\s+only\b',
            r'\bunited states\s+only\b',
            r'\beurope\s+only\b',
            r'\blatam\b',
            r'\blatin america\s+only\b',
            r'\buk\s+only\b',
            r'\bunited kingdom\s+only\b',
        )
        if any(re.search(pat, tags_text, re.IGNORECASE) for pat in restricted_tag_patterns):
            return False

        # --- Step 1b: Reject explicit regional exclusions (description) ---
        description = BeautifulSoup(
            str(job.get('description') or ''), 'html.parser'
        ).get_text(separator=' ', strip=True)

        restricted_desc_patterns = (
            r'\bus\s+only\b',
            r'\bu\.s\.\s+only\b',
            r'\bunited states\s+only\b',
            r'\beurope\s+only\b',
            r'\blatam\b',
            r'\buk\s+only\b',
            r'\bwithin\s+germany\b',
            r'\bberlin\s+office\b',
        )
        if any(re.search(pat, description, re.IGNORECASE) for pat in restricted_desc_patterns):
            return False

        # --- Step 2: Determine whether the listing is remote-eligible ---
        remote_markers = ('remote', 'worldwide', 'anywhere')
        is_marked_remote = (
            not job_location                                           # blank location on RemoteOK = remote
            or any(marker in job_location for marker in remote_markers)
            or any(marker in tags_list for marker in remote_markers)
        )

        # If no specific city was requested, require the listing to be remote-eligible
        if not requested_locations:
            india_locations = (
                'india', 'bengaluru', 'bangalore', 'hyderabad', 'pune',
                'gurgaon', 'noida', 'delhi', 'mumbai', 'chennai',
            )
            is_india_location = any(loc in job_location for loc in india_locations)
            return is_marked_remote or is_india_location

        # --- Step 3: City-specific search ---
        # Remote/worldwide/anywhere listings are always eligible regardless of the
        # searched city — they can be worked from anywhere.
        if is_marked_remote:
            return True

        # Also accept listings explicitly mentioning an Indian city (local hybrid posts)
        india_locations = (
            'india', 'bengaluru', 'bangalore', 'hyderabad', 'pune',
            'gurgaon', 'noida', 'delhi', 'mumbai', 'chennai',
        )
        if any(loc in job_location for loc in india_locations):
            return True

        # Finally, accept a direct city match
        return any(loc in job_location for loc in requested_locations)

    
    def _convert_to_job(self, job_data: Dict[str, Any]) -> Job:
        """
        Convert API response to Job object.
        
        Args:
            job_data: Job dictionary from API
        
        Returns:
            Job object
        """
        # Extract and clean description (remove HTML)
        description_html = job_data.get('description', '')
        description = self._clean_html(description_html)
        
        # Format salary information
        salary = self._format_salary(
            job_data.get('salary_min', 0),
            job_data.get('salary_max', 0)
        )
        
        # Determine job type from tags
        job_type = self._determine_job_type(job_data.get('tags', []))
        
        # Format date
        posting_date = self._format_date(job_data.get('date', ''))
        
        # Create Job object
        job = Job(
            title=job_data.get('position', ''),
            company=job_data.get('company', ''),
            location=job_data.get('location', '') or 'Remote',  # Default to Remote if empty
            description=description,
            salary=salary,
            url=job_data.get('url', ''),
            posted_date=posting_date,
            job_type=job_type,
            source='remoteok',
            salary_min=job_data.get('salary_min') if job_data.get('salary_min', 0) and job_data.get('salary_min', 0) > 0 else None,
            salary_max=job_data.get('salary_max') if job_data.get('salary_max', 0) and job_data.get('salary_max', 0) > 0 else None,
            tags=job_data.get('tags', []),
            company_logo=job_data.get('company_logo', '')
        )
        
        return job
    
    def _clean_html(self, html_content: str) -> str:
        """
        Remove HTML tags and clean up text content.
        
        Args:
            html_content: HTML string
        
        Returns:
            Cleaned text content
        """
        if not html_content:
            return ""
        
        soup = BeautifulSoup(html_content, 'html.parser')
        text = soup.get_text(separator=' ', strip=True)
        
        # Remove excessive whitespace
        text = re.sub(r'\s+', ' ', text)
        
        # Truncate if too long (keep first 500 chars for summary)
        if len(text) > 500:
            text = text[:500] + "..."
        
        return text.strip()
    
    def _format_salary(self, salary_min: int, salary_max: int) -> str:
        """
        Format salary information.
        
        Args:
            salary_min: Minimum salary
            salary_max: Maximum salary
        
        Returns:
            Formatted salary string
        """
        salary_min = salary_min or 0
        salary_max = salary_max or 0
        if salary_min > 0 and salary_max > 0:
            return f"${salary_min:,} - ${salary_max:,}"
        elif salary_min > 0:
            return f"${salary_min:,}+"
        elif salary_max > 0:
            return f"Up to ${salary_max:,}"
        else:
            return "Not specified"
    
    def _determine_job_type(self, tags: List[str]) -> str:
        """
        Determine job type from tags.
        
        Args:
            tags: List of job tags
        
        Returns:
            Job type string
        """
        tags_lower = [str(tag).lower() for tag in tags]
        
        if any(term in tags_lower for term in ('contract', 'freelance')):
            return 'Contract'
        elif any(term in tags_lower for term in ('part time', 'part-time')):
            return 'Part-time'
        elif any(term in tags_lower for term in ('internship', 'junior')):
            return 'Internship'
        else:
            return 'Full-time'  # Default assumption
    
    def _format_date(self, date_str: str) -> str:
        """
        Format date string to standard format.
        
        Args:
            date_str: ISO 8601 date string
        
        Returns:
            Formatted date string
        """
        if not date_str:
            return ""
        
        try:
            # Parse ISO 8601 format
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            return dt.strftime('%Y-%m-%d')
        except (ValueError, AttributeError):
            return date_str