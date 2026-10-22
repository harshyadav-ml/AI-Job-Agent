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


def test_csv_header_is_first_line_and_properly_escaped(tmp_path: Path):
    writer = CSVWriter(data_dir=tmp_path)
    jobs = [
        {
            "title": "Software Engineer, Full Stack",
            "company": "Acme, Inc.",
            "location": "Bengaluru, India",
            "description": "Building next-gen platforms with Python, React, and SQL.",
            "salary": "$120,000 - $150,000",
            "url": "https://example.com/job/1",
            "posted_date": "2026-10-01",
            "job_type": "Full-time",
            "source": "test",
        }
    ]

    filepath = writer.write_jobs(jobs, filename="test_jobs.csv")
    assert Path(filepath).exists()

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # The header MUST be the first line, no empty lines or metadata comments
    first_line = lines[0].strip()
    assert first_line == "title,company,location,description,salary,url,posted_date,job_type,source,scraped_at"

    # Verify second line contains properly escaped fields
    second_line = lines[1]
    assert '"Software Engineer, Full Stack"' in second_line
    assert '"Acme, Inc."' in second_line

    # Verify DuckDB query with header=true
    results = writer.query_jobs_duckdb("test_jobs.csv")
    assert len(results) == 1
    assert list(results[0].keys()) == writer.fieldnames
    assert results[0]["title"] == "Software Engineer, Full Stack"
    assert results[0]["company"] == "Acme, Inc."


def test_write_jobs_pandas_exports_header_true_index_false(tmp_path: Path):
    writer = CSVWriter(data_dir=tmp_path)
    jobs = [
        {
            "title": "Backend Developer",
            "company": "Tech Corp",
            "location": "Remote",
            "description": "Python API services",
            "salary": "$100,000+",
            "url": "https://example.com/job/2",
            "posted_date": "2026-10-05",
            "job_type": "Full-time",
            "source": "remoteok",
        }
    ]

    filepath = writer.write_jobs_pandas(jobs, filename="test_pandas_jobs.csv")
    assert Path(filepath).exists()

    with open(filepath, "r", encoding="utf-8") as f:
        first_line = f.readline().strip()

    # Must have header as first row and no index column (e.g. Unnamed: 0)
    assert first_line == "title,company,location,description,salary,url,posted_date,job_type,source,scraped_at"
    assert "Unnamed" not in first_line

    # Test reading back with read_jobs_df
    df = writer.read_jobs_df("test_pandas_jobs.csv")
    assert list(df.columns) == writer.fieldnames
    assert len(df) == 1
    assert df.iloc[0]["title"] == "Backend Developer"

    # Test querying with DuckDB
    results = writer.query_jobs_duckdb("test_pandas_jobs.csv")
    assert len(results) == 1
    assert results[0]["company"] == "Tech Corp"


def test_jobs_software_banglore_csv_verification():
    """Verify that existing workspace CSV file is valid and opens properly with column names."""
    data_dir = Path(__file__).parent.parent / "data"
    writer = CSVWriter(data_dir=data_dir)
    filename = "jobs_software_banglore_20261008_080702.csv"
    filepath = data_dir / filename

    if not filepath.exists():
        return

    # Check raw header line
    with open(filepath, "r", encoding="utf-8") as f:
        first_line = f.readline().strip()
    assert first_line == "title,company,location,description,salary,url,posted_date,job_type,source,scraped_at"

    # DuckDB read_csv with header=true
    records = writer.query_jobs_duckdb(filename)
    assert len(records) > 0
    assert list(records[0].keys()) == writer.fieldnames

    # pandas read_jobs_df
    df = writer.read_jobs_df(filename)
    assert list(df.columns) == writer.fieldnames
    assert len(df) == len(records)