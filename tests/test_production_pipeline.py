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
            "experience_raw", "max_experience_required",
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

    async def fetch_full_description(self, url: str) -> str:
        return (
            "About the Role\n"
            "We are looking for a talented developer to join our team.\n\n"
            "Requirements\n"
            "- Proficiency in Python, Django, and REST APIs.\n"
            "- 0-1 years of experience required.\n"
            "- Strong understanding of software development principles.\n\n"
            "Responsibilities\n"
            "- Build scalable backend services.\n"
            "- Collaborate with cross-functional teams.\n"
        )


class DummyFailingScraper(BaseScraper):
    @property
    def source_name(self) -> str:
        return "failing_scraper"

    async def health_check(self) -> bool:
        return False

    async def scrape(self, keywords, locations, *, max_results=50):
        await asyncio.sleep(0.01)
        raise RuntimeError("Cloudflare block simulation 403")

    async def fetch_full_description(self, url: str) -> str:
        raise RuntimeError("Failed fetch simulation")


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


# =========================================================================== #
# Step 5: Experience Parsing & Fresher Filter Tests                           #
# =========================================================================== #

class TestExperienceNormalizerAndFilter:
    @pytest.mark.parametrize(
        ("exp_raw", "desc", "expected_max"),
        [
            ("0-1 Yrs", "", 1),
            ("0-2 Yrs", "", 2),
            ("0-3 Yrs", "", 3),
            ("0-6 Yrs", "", 6),
            ("1 - 3 yrs", "", 3),
            ("1 - 4 years", "", 4),
            ("2 to 5 years", "", 5),
            ("3+ years", "", 3),
            ("5+ years", "", 5),
            ("3-7 Yrs", "", 7),
            ("3-8 Yrs", "", 8),
            ("3 - 8 Yrs", "", 8),
            ("5-10 Yrs", "", 10),
            ("Fresher", "", 0),
            ("", "Requires 3+ years of experience in Python", 3),
            ("", "Min 2 years with AWS and Docker", 2),
            ("", "Minimum of 4 years required", 4),
            ("", "Candidates must have at least 5 years experience", 5),
            ("", "Freshers can apply for this entry-level role", 0),
            ("", "No prior experience required", 0),
            ("", "Strong Python skills and competitive pay", None),
            (None, "", None),
        ],
    )
    def test_parse_max_experience(self, exp_raw, desc, expected_max):
        normalizer = DataNormalizer()
        assert normalizer._parse_max_experience(exp_raw, desc) == expected_max
        assert normalizer._parse_experience(exp_raw, desc) == expected_max

    def test_experience_filtering_fresher_logic(self):
        """Test rule: keep max_experience_required <= 1 or None; drop > 1."""
        jobs = [
            Job(title="Fresher Role", company="A", url="https://a.com/1", source="naukri", max_experience_required=0),
            Job(title="Junior Dev", company="B", url="https://b.com/2", source="remoteok", max_experience_required=1),
            Job(title="Unspecified Role", company="C", url="https://c.com/3", source="wellfound", max_experience_required=None),
            Job(title="0-3 Yrs Role", company="D", url="https://d.com/4", source="naukri", max_experience_required=3),
            Job(title="0-6 Yrs Role", company="E", url="https://e.com/5", source="naukri", max_experience_required=6),
            Job(title="3-7 Yrs Role", company="F", url="https://f.com/6", source="naukri", max_experience_required=7),
            Job(title="Senior Lead", company="G", url="https://g.com/7", source="remoteok", max_experience_required=5),
        ]
        kept = [j for j in jobs if j.max_experience_required is None or j.max_experience_required <= 1]
        dropped = [j for j in jobs if j.max_experience_required is not None and j.max_experience_required > 1]
        assert len(kept) == 3
        assert [j.title for j in kept] == ["Fresher Role", "Junior Dev", "Unspecified Role"]
        assert len(dropped) == 4
        assert [j.title for j in dropped] == ["0-3 Yrs Role", "0-6 Yrs Role", "3-7 Yrs Role", "Senior Lead"]

    def test_to_dict_includes_experience_fields(self):
        job = Job(
            title="Backend Engineer",
            company="Acme Corp",
            url="https://acme.com/job/1",
            source="naukri",
            experience_raw="0-2 Yrs",
            max_experience_required=2,
        )
        d = job.to_dict()
        assert "experience_raw" in d
        assert "max_experience_required" in d
        assert d["experience_raw"] == "0-2 Yrs"
        assert d["max_experience_required"] == 2

    @pytest.mark.parametrize(
        ("title", "exp_raw", "should_block"),
        [
            ("Senior Software Engineer", None, True),
            ("Sr. Software Developer", None, True),
            ("Sr Software Developer", None, True),
            ("Technical Lead", None, True),
            ("Principal Architect", None, True),
            ("Product Manager", None, True),
            ("Solutions Architect", None, True),
            ("Engineering Director", None, True),
            ("Head of Engineering", None, True),
            ("Software Developer", "senior", True),
            ("Software Engineer", "Sr. Level", True),
            ("Software Developer", "0-1 Yrs", False),
            ("Junior Developer", None, False),
            ("Software Engineer", None, False),
            ("Thread Worker", None, False),
        ],
    )
    def test_senior_blocklist_detection(self, title, exp_raw, should_block):
        from src.main import has_blocked_senior_keyword
        job = Job(
            title=title,
            company="Test Inc",
            url="https://example.com/test",
            source="naukri",
            experience_raw=exp_raw,
        )
        assert has_blocked_senior_keyword(job) == should_block

    def test_strict_filter_enforcement_combined_rules(self):
        """
        1. If title or experience_raw contains blocked senior keyword -> DROP
        2. If max_experience_required > 1 -> DROP
        3. Otherwise -> KEEP
        """
        from src.main import has_blocked_senior_keyword
        jobs = [
            # Kept:
            Job(title="Junior Developer", company="A", url="https://a.com/1", source="naukri", max_experience_required=0),
            Job(title="Software Engineer", company="B", url="https://b.com/2", source="remoteok", max_experience_required=1),
            Job(title="Fullstack Developer", company="C", url="https://c.com/3", source="wellfound", max_experience_required=None),
            # Dropped due to Senior keyword:
            Job(title="Senior Developer", company="D", url="https://d.com/4", source="naukri", max_experience_required=0),
            Job(title="Software Engineer", company="E", url="https://e.com/5", source="remoteok", experience_raw="senior"),
            Job(title="Tech Lead", company="F", url="https://f.com/6", source="wellfound", max_experience_required=None),
            Job(title="Sr. Software Developer", company="G", url="https://g.com/7", source="naukri", max_experience_required=None),
            # Dropped due to max_experience_required > 1:
            Job(title="Software Engineer", company="H", url="https://h.com/8", source="naukri", max_experience_required=3),
            Job(title="Python Developer", company="I", url="https://i.com/9", source="remoteok", max_experience_required=2),
            Job(title="Backend Dev", company="J", url="https://j.com/10", source="naukri", experience_raw="0-3 Yrs", max_experience_required=3),
            Job(title="Data Dev", company="K", url="https://k.com/11", source="naukri", experience_raw="0-6 Yrs", max_experience_required=6),
        ]

        filtered = []
        for j in jobs:
            if has_blocked_senior_keyword(j):
                continue
            if j.max_experience_required is not None and j.max_experience_required > 1:
                continue
            filtered.append(j)

        assert len(filtered) == 3
        assert [j.title for j in filtered] == ["Junior Developer", "Software Engineer", "Fullstack Developer"]


# =========================================================================== #
# Step 6: Deep Scrape Mechanism Tests                                        #
# =========================================================================== #

class TestDeepScrapeMechanism:
    def test_enrich_jobs_with_deep_scrape_success(self):
        from src.main import enrich_jobs_with_deep_scrape

        scrapers = [DummyWorkingScraper()]
        jobs = [
            Job(
                title="Frontend Developer",
                company="Corp A",
                url="https://corp-a.com/job/101",
                source="working_scraper",
                description="Short snippet",
            ),
            Job(
                title="Backend Developer",
                company="Corp A",
                url="https://corp-a.com/job/102",
                source="working_scraper",
                description="Short snippet 2",
            ),
        ]

        enriched = asyncio.run(enrich_jobs_with_deep_scrape(jobs, scrapers))

        assert len(enriched) == 2
        # Both jobs were enriched by the working scraper with rich deep-scraped text
        assert "About the Role" in enriched[0].description
        assert len(enriched[0].description) > 80
        assert "About the Role" in enriched[1].description
        assert len(enriched[1].description) > 80

    def test_enrich_jobs_with_deep_scrape_resilience_to_failures(self):
        from src.main import enrich_jobs_with_deep_scrape

        scrapers = [DummyWorkingScraper(), DummyFailingScraper()]
        jobs = [
            Job(
                title="Frontend Developer",
                company="Corp A",
                url="https://corp-a.com/job/101",
                source="working_scraper",
                description="Short snippet",
            ),
            Job(
                title="Cloud Engineer",
                company="Corp B",
                url="https://corp-b.com/job/102",
                source="failing_scraper",
                description="Original short description",
            ),
        ]

        enriched = asyncio.run(enrich_jobs_with_deep_scrape(jobs, scrapers))

        assert len(enriched) == 2
        # Working scraper was enriched with rich deep-scraped text
        assert "About the Role" in enriched[0].description
        assert len(enriched[0].description) > 80
        # Failing scraper preserved its original description without raising
        assert enriched[1].description == "Original short description"

    def test_enrich_jobs_handles_empty_urls_and_unknown_sources(self):
        from src.main import enrich_jobs_with_deep_scrape

        scrapers = [DummyWorkingScraper()]
        jobs = [
            Job(
                title="Dev 1",
                company="Corp A",
                url="https://corp-a.com/job/101",
                source="unknown_source",
                description="Existing desc 1",
            ),
            Job(
                title="Dev 2",
                company="Corp B",
                url="https://corp-b.com/job/102",
                source="another_unregistered_platform",
                description="Existing desc 2",
            ),
        ]

        enriched = asyncio.run(enrich_jobs_with_deep_scrape(jobs, scrapers))
        assert enriched[0].description == "Existing desc 1"
        assert enriched[1].description == "Existing desc 2"



class TestDeepScrapeFieldParsing:
    """Unit tests for DataNormalizer.parse_deep_scrape_fields()."""

    def test_extracts_range_experience(self):
        text = "Requirements\nWe need someone with 0-3 years of experience in Python."
        result = DataNormalizer.parse_deep_scrape_fields(text)
        assert result.get("max_experience_required") == 3
        assert "experience_raw" in result

    def test_extracts_max_of_range(self):
        text = "1 - 4 years of hands-on experience with cloud platforms."
        result = DataNormalizer.parse_deep_scrape_fields(text)
        assert result.get("max_experience_required") == 4

    def test_extracts_plus_experience(self):
        text = "Minimum 2+ years of experience with Django required."
        result = DataNormalizer.parse_deep_scrape_fields(text)
        assert result.get("max_experience_required") == 2

    def test_extracts_yrs_range(self):
        text = "Experience: 3-7 Yrs"
        result = DataNormalizer.parse_deep_scrape_fields(text)
        assert result.get("max_experience_required") == 7

    def test_fresher_keyword_marker(self):
        text = "Freshers can apply. No prior experience required."
        result = DataNormalizer.parse_deep_scrape_fields(text)
        assert result.get("max_experience_required") == 0

    def test_entry_level_marker(self):
        text = "This is an entry-level role open to all graduates."
        result = DataNormalizer.parse_deep_scrape_fields(text)
        assert result.get("max_experience_required") == 0

    def test_no_experience_returns_empty(self):
        text = "Great culture, flexible hours, remote work. Competitive pay."
        result = DataNormalizer.parse_deep_scrape_fields(text)
        assert "max_experience_required" not in result

    def test_extracts_lpa_salary(self):
        text = "Salary: 6-12 LPA based on experience and skills."
        result = DataNormalizer.parse_deep_scrape_fields(text)
        assert "salary_raw" in result

    def test_extracts_inr_range_salary(self):
        text = "CTC: ₹8,00,000 - ₹15,00,000 per annum"
        result = DataNormalizer.parse_deep_scrape_fields(text)
        assert "salary_raw" in result

    def test_empty_text_returns_empty_dict(self):
        result = DataNormalizer.parse_deep_scrape_fields("")
        assert result == {}

    def test_whitespace_only_returns_empty_dict(self):
        result = DataNormalizer.parse_deep_scrape_fields("   \n\t  ")
        assert result == {}
