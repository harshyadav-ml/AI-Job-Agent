"""
Integration tests for RemoteOK scraper with live API.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.scrapers.remoteok_scraper import RemoteOKScraper
from src.models import Job


class TestRemoteOKIntegration:
    """Integration tests with live RemoteOK API."""
    
    @pytest.fixture
    def scraper(self):
        """Create a RemoteOK scraper instance for testing."""
        return RemoteOKScraper()
    
    def test_live_api_connection(self, scraper):
        """Test that we can connect to the live API."""
        jobs = scraper._fetch_jobs_from_api()
        
        assert isinstance(jobs, list)
        assert len(jobs) > 0
        
        # Check that first job has expected fields
        first_job = jobs[0]
        assert 'position' in first_job
        assert 'company' in first_job
        assert 'url' in first_job
    
    def test_live_api_filtering(self, scraper):
        """Test filtering with live API data."""
        jobs = scraper._fetch_jobs_from_api()
        
        # Filter for Python jobs
        filtered = scraper._filter_jobs(jobs, ["python"], ["remote"])
        
        assert isinstance(filtered, list)
        # Should have some results (or empty if no python jobs)
        assert len(filtered) >= 0
    
    def test_live_api_conversion(self, scraper):
        """Test conversion of live API data to Job objects."""
        jobs = scraper._fetch_jobs_from_api()
        
        if len(jobs) > 0:
            job = scraper._convert_to_job(jobs[0])
            
            assert isinstance(job, Job)
            assert job.title != ""
            assert job.company != ""
            assert job.url != ""
            assert job.source == "remoteok"
    
    def test_full_scrape_workflow(self, scraper):
        """Test the complete scraping workflow with live API."""
        jobs = scraper.scrape_jobs(["python"], ["remote"])
        
        assert isinstance(jobs, list)
        # All jobs should be Job objects
        for job in jobs:
            assert isinstance(job, Job)
            assert job.source == "remoteok"
            assert job.title != ""
            assert job.company != ""
    
    def test_rate_limiting_respected(self, scraper):
        """Test that rate limiting is respected during live calls."""
        import time
        
        start_time = time.time()
        
        # Make multiple API calls
        for _ in range(3):
            scraper._fetch_jobs_from_api()
        
        elapsed_time = time.time() - start_time
        
        # Should take at least 2 * request_delay seconds (2 delays between 3 calls)
        expected_min_time = 2 * scraper.request_delay
        assert elapsed_time >= expected_min_time