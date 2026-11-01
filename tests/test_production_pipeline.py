"""
Comprehensive tests for production-grade Phase 1 refactor:
1. Date normalization (Naukri, Wellfound, Remote OK formats).
2. Pydantic v2 validators & salary bound swapping without recursion.
3. Concurrency & async orchestration resilience.
4. Stateless deduplication & data normalization.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.models import Job, parse_date, parse_salary_bounds
from src.processors.data_normalizer import DataNormalizer
from src.processors.deduplicator import Deduplicator, job_fingerprint
from src.scrapers.base import BaseScraper
from src.main import run_all_scrapers


# =========================================================================== #
# Step 1: Date Normalizer Tests                                               #
# =========================================================================== #

class TestDateNormalizer:
    @pytest.mark.parametrize(
        ("raw_input", "expected_contains"),
        [
            ("2026-10-15", "2026-10-15"),
            ("2026/10/15", "2026-10-15"),
            ("15/10/2026", "2026-10-15"),
            ("15-10-2026", "2026-10-15"),
            ("15 Oct 2026", "2026-10-15"),
            ("October 15, 2026", "2026-10-15"),
            ("2026-09-22T14:36:54+00:00", "2026-09-22"),
            ("2026-09-22T14:36:54Z", "2026-09-22"),
            ("1790087814", "2026-09-22"),  # Unix timestamp seconds
            ("1790087814000", "2026-09-22"),  # Unix timestamp milliseconds
            ("Just now", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            ("Today", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            ("Few hours ago", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            ("Yesterday", (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")),
            ("2 days ago", (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")),
            ("Posted 3 days ago", (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")),
            ("Active 1w ago", (datetime.now(timezone.utc) - timedelta(weeks=1)).strftime("%Y-%m-%d")),
            ("3w ago", (datetime.now(timezone.utc) - timedelta(weeks=3)).strftime("%Y-%m-%d")),
            ("3w", (datetime.now(timezone.utc) - timedelta(weeks=3)).strftime("%Y-%m-%d")),
            ("30+ days ago", (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")),
        ],
    )
    def test_parse_date_diverse_formats(self, raw_input, expected_contains):
        result = parse_date(raw_input)
        assert result == expected_contains

    @pytest.mark.parametrize("invalid_val", [None, "", "   ", "Not specified", "N/A", "null", "none"])
    def test_parse_date_invalid_or_empty(self, invalid_val):
        assert parse_date(invalid_val) is None

    def test_job_model_posted_date_auto_coercion(self):
        job = Job(
            title="Senior Python Engineer",
            company="Tech Corp",
            url="https://example.com/job/1",
            source="naukri",
            posted_date="2 days ago",
        )
        expected = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
        assert job.posted_date == expected

    def test_normalizer_applies_date_formatting(self):
        normalizer = DataNormalizer()
        job = Job(
            title="Data Architect",
            company="Enterprise AI",
            url="https://example.com/job/2",
            source="wellfound",
            posted_date="3w ago",
        )
        normalized = normalizer.normalize_jobs([job])[0]
        expected = (datetime.now(timezone.utc) - timedelta(weeks=3)).strftime("%Y-%m-%d")
        assert normalized.posted_date == expected


# =========================================================================== #
# Step 2: Pydantic Validators & Salary Swapping Tests                         #
# =========================================================================== #

class TestPydanticValidators:
    def test_salary_min_max_inverted_swap(self):
        """Ensure salary_min and salary_max are swapped without recursion."""
        job = Job(
            title="Backend Lead",
            company="Fintech Inc",
            url="https://example.com/job/3",
            source="remoteok",
            salary_min=180000,
            salary_max=120000,
        )
        assert job.salary_min == 120000
        assert job.salary_max == 180000

    def test_salary_raw_auto_derivation_inr(self):
        job = Job(
            title="Python Developer",
            company="Startup",
            url="https://example.com/job/4",
            source="naukri",
            salary_raw="8 - 14 LPA",
        )
        assert job.salary_min == 800000
        assert job.salary_max == 1400000

    def test_salary_raw_auto_derivation_usd(self):
        job = Job(
            title="Staff Engineer",
            company="US Remote",
            url="https://example.com/job/5",
            source="remoteok",
            salary_raw="$130,000 - $160,000",
        )
        assert job.salary_min == 130000
        assert job.salary_max == 160000

    def test_empty_url_rejected(self):
        with pytest.raises(ValidationError):
            Job(title="Test", company="Co", url="", source="naukri")

    def test_job_id_deterministic(self):
        j1 = Job(title="Eng", company="A", url="https://a.com/job1", source="naukri")
        j2 = Job(title="Eng", company="A", url="https://a.com/job1/", source="remoteok")
        assert j1.job_id == j2.job_id
        assert len(j1.job_id) == 16

    def test_to_dict_core_contract(self):
        job = Job(
            title="QA Lead",
            company="Quality Inc",
            url="https://example.com/job/6",
            source="naukri",
        )
        d = job.to_dict()
        assert set(d.keys()) == {
            "title", "company", "location", "description", "salary",
            "url", "posted_date", "job_type", "source", "scraped_at",
        }


# =========================================================================== #
# Step 3: Concurrency & Orchestration Resilience Tests                        #
# =========================================================================== #

class DummyWorkingScraper(BaseScraper):
    @property
    def source_name(self) -> str:
        return "working_scraper"

    async def health_check(self) -> bool:
        return True

    async def scrape(self, keywords, locations, *, max_results=50):
        await asyncio.sleep(0.01)
        return [
            Job(title="Job 1", company="Corp A", url="https://corp-a.com/1", source="working_scraper"),
            Job(title="Job 2", company="Corp B", url="https://corp-b.com/2", source="working_scraper"),
        ]


class DummyFailingScraper(BaseScraper):
    @property
    def source_name(self) -> str:
        return "failing_scraper"

    async def health_check(self) -> bool:
        return False

    async def scrape(self, keywords, locations, *, max_results=50):
        await asyncio.sleep(0.01)
        raise RuntimeError("Cloudflare block simulation 403")


def test_async_orchestrator_resilience_to_failures():
    """Ensure that a failing scraper does not crash or cancel concurrent scrapers."""
    scrapers = [DummyWorkingScraper(), DummyFailingScraper()]
    jobs = asyncio.run(run_all_scrapers(scrapers, ["python"], ["remote"]))

    assert len(jobs) == 2
    assert jobs[0].company == "Corp A"
    assert jobs[1].company == "Corp B"


# =========================================================================== #
# Step 4: Normalizer & Deduplicator Integration Tests                        #
# =========================================================================== #

class TestNormalizerAndDeduplicator:
    def test_normalizer_cleans_titles_and_stars(self):
        normalizer = DataNormalizer()
        job = Job(
            title="  Senior AI / ML Engineer  ",
            company="Amazon Web Services 4.3 ★★★",
            url="https://amazon.com/jobs/ai-1",
            source="naukri",
            location="Work from Home",
        )
        norm = normalizer.normalize_jobs([job])[0]
        assert norm.title == "Senior AI / ML Engineer"
        assert norm.company == "Amazon Web Services"
        assert norm.location == "Remote"

    def test_stateless_deduplicator(self):
        dedup = Deduplicator()
        jobs = [
            Job(title="Dev", company="A", url="https://a.com/j1", source="naukri"),
            Job(title="Dev", company="A", url="https://a.com/j1", source="remoteok"),
            Job(title="Lead", company="B", url="https://b.com/j2", source="wellfound"),
        ]
        run1 = dedup.deduplicate_jobs(jobs)
        run2 = dedup.deduplicate_jobs(jobs)
        assert len(run1) == 2
        assert len(run2) == 2
