"""Tests for unique, search-specific CSV filenames."""

from pathlib import Path

from src.storage.csv_writer import CSVWriter


def test_write_jobs_generates_search_specific_filename(tmp_path: Path):
    writer = CSVWriter(data_dir=tmp_path)
    jobs = [{"title": "Engineer", "company": "Acme", "url": "https://example.com", "source": "test"}]

    path = writer.write_jobs(
        jobs,
        keywords=["product manager"],
        locations=["Bangalore"],
        query="find product manager roles in bangalore",
    )

    assert Path(path).parent == tmp_path
    assert Path(path).name.startswith("jobs_product-manager_bangalore_")
    assert Path(path).suffix == ".csv"
    assert Path(path).exists()


def test_write_jobs_avoids_same_second_collision(tmp_path: Path, monkeypatch):
    writer = CSVWriter(data_dir=tmp_path)
    jobs = [{"title": "Engineer", "company": "Acme", "url": "https://example.com", "source": "test"}]

    class FixedDatetime:
        @classmethod
        def now(cls):
            from datetime import datetime
            return datetime(2026, 9, 24, 12, 30, 45)

    monkeypatch.setattr("src.storage.csv_writer.datetime", FixedDatetime)
    first = writer.write_jobs(jobs, keywords=["python"], locations=["Pune"], query="python developer in pune")
    second = writer.write_jobs(jobs, keywords=["python"], locations=["Pune"], query="python developer in pune")

    assert Path(first).name == "jobs_python_pune_20260924_123045.csv"
    assert Path(second).name == "jobs_python_pune_20260924_123045_1.csv"


def test_write_jobs_without_query_uses_all_fallback(tmp_path: Path):
    writer = CSVWriter(data_dir=tmp_path)
    jobs = [{"title": "Engineer", "company": "Acme", "url": "https://example.com", "source": "test"}]

    path = writer.write_jobs(jobs)

    assert Path(path).name.startswith("jobs_all_")