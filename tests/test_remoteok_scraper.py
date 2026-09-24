"""
Unit tests for RemoteOK scraper.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import Mock, patch, MagicMock
import requests

from src.scrapers.remoteok_scraper import RemoteOKScraper
from src.models import Job


class TestRemoteOKScraper:
    """Test suite for RemoteOK scraper."""
    
    @pytest.fixture
    def scraper(self):
        """Create a RemoteOK scraper instance for testing."""
        return RemoteOKScraper()
    
    @pytest.fixture
    def sample_api_response(self):
        """Sample API response data."""
        return [
            {
                "legal": "API Terms of Service...",
                "last_updated": 1790172002
            },
            {
                "slug": "test-job-1",
                "id": "123456",
                "epoch": 1790087814,
                "date": "2026-09-22T14:36:54+00:00",
                "company": "Test Company",
                "company_logo": "https://example.com/logo.png",
                "position": "Python Developer",
                "tags": ["python", "remote", "developer"],
                "description": "<p>This is a test job description for a Python developer position.</p>",
                "location": "",
                "apply_url": "https://remoteok.com/test-job-1",
                "salary_min": 80000,
                "salary_max": 120000,
                "logo": "https://example.com/logo.png",
                "url": "https://remoteok.com/test-job-1"
            }
        ]
    
    def test_scraper_initialization(self, scraper):
        """Test that scraper initializes correctly."""
        assert scraper.api_url == "https://remoteok.com/api"
        assert scraper.session is not None
        assert scraper.request_delay > 0
    
    def test_rate_limiting(self, scraper):
        """Test rate limiting functionality."""
        import time
        
        # First call should not delay
        start_time = time.time()
        scraper._rate_limit()
        first_call_duration = time.time() - start_time
        
        # Immediate second call should delay
        start_time = time.time()
        scraper._rate_limit()
        second_call_duration = time.time() - start_time
        
        # Second call should take longer due to rate limiting
        assert second_call_duration >= first_call_duration
    
    @patch('src.scrapers.remoteok_scraper.requests.Session.get')
    def test_fetch_jobs_from_api_success(self, mock_get, scraper, sample_api_response):
        """Test successful API fetch."""
        mock_response = Mock()
        mock_response.json.return_value = sample_api_response
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        jobs = scraper._fetch_jobs_from_api()
        
        assert len(jobs) == 1
        assert jobs[0]['position'] == "Python Developer"
        assert jobs[0]['company'] == "Test Company"
        mock_get.assert_called_once()
    
    @patch('src.scrapers.remoteok_scraper.requests.Session.get')
    def test_fetch_jobs_from_api_http_error(self, mock_get, scraper):
        """Test API fetch with HTTP error."""
        mock_get.side_effect = requests.exceptions.HTTPError("404 Not Found")
        
        with pytest.raises(requests.exceptions.HTTPError):
            scraper._fetch_jobs_from_api()
    
    @patch('src.scrapers.remoteok_scraper.requests.Session.get')
    def test_fetch_jobs_from_api_json_error(self, mock_get, scraper):
        """Test API fetch with JSON parsing error."""
        mock_response = Mock()
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        with pytest.raises(ValueError):
            scraper._fetch_jobs_from_api()
    
    def test_matches_keywords_exact_match(self, scraper):
        """Test keyword matching with exact match."""
        job = {
            "position": "Python Developer",
            "tags": ["python", "developer"],
            "description": "Job description"
        }
        
        assert scraper._matches_keywords(job, ["python"]) is True
        assert scraper._matches_keywords(job, ["developer"]) is True
    
    def test_matches_keywords_case_insensitive(self, scraper):
        """Test keyword matching is case insensitive."""
        job = {
            "position": "Python Developer",
            "tags": ["python", "developer"],
            "description": "Job description"
        }
        
        assert scraper._matches_keywords(job, ["PYTHON"]) is True
        assert scraper._matches_keywords(job, ["DeVeLoPeR"]) is True
    
    def test_matches_keywords_no_match(self, scraper):
        """Test keyword matching with no match."""
        job = {
            "position": "Python Developer",
            "tags": ["python", "developer"],
            "description": "Job description"
        }
        
        assert scraper._matches_keywords(job, ["java", "golang"]) is False
    
    def test_matches_keywords_empty_list(self, scraper):
        """Test keyword matching with empty keyword list."""
        job = {
            "position": "Python Developer",
            "tags": ["python", "developer"],
            "description": "Job description"
        }
        
        assert scraper._matches_keywords(job, []) is True
    
    def test_matches_location_remote(self, scraper):
        """Test location matching for remote jobs."""
        job = {
            "location": ""
        }
        
        assert scraper._matches_location(job, ["remote"]) is True
        assert scraper._matches_location(job, ["Remote"]) is True
    
    def test_matches_location_specific(self, scraper):
        """Test location matching for specific locations."""
        job = {
            "location": "Bengaluru, India"
        }
        
        assert scraper._matches_location(job, ["remote"]) is True

        job["location"] = "San Francisco, CA"
        assert scraper._matches_location(job, ["remote"]) is False

    def test_matches_location_worldwide_without_restrictions(self, scraper):
        """Test that unrestricted worldwide jobs are accepted."""
        job = {
            "location": "Remote - Worldwide",
            "tags": ["python"],
            "description": "Work from anywhere."
        }

        assert scraper._matches_location(job, ["remote"]) is True

    def test_matches_location_rejects_non_indian_place_in_description(self, scraper):
        """Test that worldwide labels do not bypass a specific non-Indian place."""
        job = {
            "location": "Remote",
            "tags": ["developer"],
            "description": "Mostly remote within Germany with monthly Berlin office visits."
        }

        assert scraper._matches_location(job, ["remote"]) is False

    @pytest.mark.parametrize("restriction", [
        "US only",
        "Europe only",
        "LATAM",
        "UK only",
    ])
    def test_matches_location_rejects_restricted_worldwide_jobs(self, scraper, restriction):
        """Test that country-restricted worldwide jobs are rejected."""
        job = {
            "location": "Worldwide",
            "tags": [restriction],
            "description": "Remote role"
        }

        assert scraper._matches_location(job, ["remote"]) is False
    
    def test_matches_location_empty_list(self, scraper):
        """Test strict location matching regardless of configured locations."""
        job = {
            "location": "San Francisco, CA"
        }
        
        assert scraper._matches_location(job, []) is False
    
    def test_clean_html(self, scraper):
        """Test HTML cleaning functionality."""
        html = "<p>This is <strong>bold</strong> text with <a href='#'>links</a>.</p>"
        cleaned = scraper._clean_html(html)
        
        assert "<p>" not in cleaned
        assert "<strong>" not in cleaned
        assert "bold" in cleaned
        assert "links" in cleaned
    
    def test_clean_html_truncation(self, scraper):
        """Test HTML cleaning with truncation."""
        long_html = "<p>" + "x" * 1000 + "</p>"
        cleaned = scraper._clean_html(long_html)
        
        assert len(cleaned) <= 503  # 500 + "..."
    
    def test_format_salary_both_values(self, scraper):
        """Test salary formatting with both min and max."""
        salary = scraper._format_salary(80000, 120000)
        assert "80,000" in salary
        assert "120,000" in salary
    
    def test_format_salary_min_only(self, scraper):
        """Test salary formatting with only minimum."""
        salary = scraper._format_salary(80000, 0)
        assert "80,000" in salary
        assert "+" in salary
    
    def test_format_salary_max_only(self, scraper):
        """Test salary formatting with only maximum."""
        salary = scraper._format_salary(0, 120000)
        assert "120,000" in salary
        assert "Up to" in salary
    
    def test_format_salary_no_values(self, scraper):
        """Test salary formatting with no values."""
        salary = scraper._format_salary(0, 0)
        assert salary == "Not specified"
    
    def test_determine_job_type_contract(self, scraper):
        """Test job type determination for contract."""
        job_type = scraper._determine_job_type(["contract", "python"])
        assert job_type == "Contract"
    
    def test_determine_job_type_part_time(self, scraper):
        """Test job type determination for part-time."""
        job_type = scraper._determine_job_type(["part time", "developer"])
        assert job_type == "Part-time"
    
    def test_determine_job_type_internship(self, scraper):
        """Test job type determination for internship."""
        job_type = scraper._determine_job_type(["internship", "junior"])
        assert job_type == "Internship"
    
    def test_determine_job_type_default(self, scraper):
        """Test job type determination default."""
        job_type = scraper._determine_job_type(["python", "senior"])
        assert job_type == "Full-time"
    
    def test_format_date_valid(self, scraper):
        """Test date formatting with valid ISO date."""
        date = scraper._format_date("2026-09-22T14:36:54+00:00")
        assert date == "2026-09-22"
    
    def test_format_date_invalid(self, scraper):
        """Test date formatting with invalid date."""
        date = scraper._format_date("invalid-date")
        assert date == "invalid-date"
    
    def test_convert_to_job(self, scraper):
        """Test conversion of API data to Job object."""
        job_data = {
            "slug": "test-job",
            "id": "123",
            "epoch": 1790087814,
            "date": "2026-09-22T14:36:54+00:00",
            "company": "Test Company",
            "company_logo": "https://example.com/logo.png",
            "position": "Python Developer",
            "tags": ["python", "remote"],
            "description": "<p>Test description</p>",
            "location": "",
            "apply_url": "https://remoteok.com/test",
            "salary_min": 80000,
            "salary_max": 120000,
            "logo": "https://example.com/logo.png",
            "url": "https://remoteok.com/test"
        }
        
        job = scraper._convert_to_job(job_data)
        
        assert isinstance(job, Job)
        assert job.title == "Python Developer"
        assert job.company == "Test Company"
        # Empty location defaults to Remote
        assert job.location == "Remote"
        assert job.source == "remoteok"
        assert job.salary_min == 80000
        assert job.salary_max == 120000
        assert "python" in job.tags
    
    def test_filter_jobs(self, scraper, sample_api_response):
        """Test job filtering functionality."""
        # Remove metadata element for filtering test
        jobs_data = sample_api_response[1:]
        
        filtered = scraper._filter_jobs(jobs_data, ["python"], ["remote"])
        
        assert len(filtered) == 1
        assert filtered[0]['position'] == "Python Developer"
    
    @patch('src.scrapers.remoteok_scraper.RemoteOKScraper._fetch_jobs_from_api')
    @patch('src.scrapers.remoteok_scraper.RemoteOKScraper._filter_jobs')
    def test_scrape_jobs_integration(self, mock_filter, mock_fetch, scraper, sample_api_response):
        """Test full scrape_jobs method integration."""
        mock_fetch.return_value = sample_api_response[1:]
        mock_filter.return_value = sample_api_response[1:]
        
        jobs = scraper.scrape_jobs(["python"], ["remote"])
        
        assert len(jobs) == 1
        assert isinstance(jobs[0], Job)
        mock_fetch.assert_called_once()
        mock_filter.assert_called_once()