"""
CSV writer for storing job data in CSV format.
"""

import logging
import csv
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
from datetime import datetime

try:
    import pandas as pd
except ImportError:
    pd = None

from ..config import get_logger, settings

logger = get_logger(__name__)


class CSVWriter:
    """Handles writing job data to CSV files."""
    
    def __init__(self, data_dir: Optional[Path] = None):
        """
        Initialize the CSV writer.
        
        Args:
            data_dir: Directory to store CSV files (uses settings default if not provided)
        """
        self.logger = get_logger(__name__)
        self.data_dir = data_dir or settings.DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # CSV fieldnames
        self.fieldnames = [
            'title',
            'company', 
            'location',
            'description',
            'salary',
            'url',
            'posted_date',
            'job_type',
            'source',
            'scraped_at'
        ]
    
    def write_jobs(
        self,
        jobs: Any,
        filename: Optional[str] = None,
        mode: str = 'w',
        keywords: Optional[List[str]] = None,
        locations: Optional[List[str]] = None,
        query: Optional[str] = None,
        use_pandas: bool = False,
    ) -> str:
        """
        Write jobs to a CSV file.
        
        Ensures the header is explicitly written as the first row (header=True or
        writer.writeheader()), properly escaped, and not preceded by empty lines
        or metadata comments. If pandas is used, df.to_csv(..., index=False, header=True)
        is guaranteed.
        
        Args:
            jobs: List of job dictionaries or a pandas DataFrame
            filename: Name of the CSV file (auto-generated if not provided)
            mode: File write mode ('a' for append, 'w' for write)
            keywords: Parsed role keywords for generated filenames
            locations: Parsed locations for generated filenames
            query: Original search query; used to determine whether this is a search run
            use_pandas: When True, exports using pandas DataFrame.to_csv (defaults to False)
        
        Returns:
            Path to the written CSV file
        """
        is_df = pd is not None and isinstance(jobs, pd.DataFrame)
        if not is_df and not jobs:
            self.logger.warning("No jobs to write")
            return ""
        if is_df and jobs.empty:
            self.logger.warning("No jobs to write")
            return ""
        
        # Generate a unique search-specific filename if not provided.
        if filename is None:
            filename = self._generate_filename(keywords, locations, query)
        
        filepath = self.data_dir / filename
        
        # Determine whether to write header:
        # Header is written if mode == 'w', or if the file does not exist, or if existing file is empty
        write_header = (mode == 'w') or (not filepath.exists()) or (filepath.stat().st_size == 0)
        
        scraped_at = datetime.now().isoformat()
        
        try:
            if use_pandas or is_df:
                if pd is None:
                    raise ImportError("pandas is required for export when use_pandas=True or DataFrame is provided")
                
                if not isinstance(jobs, pd.DataFrame):
                    job_dicts = []
                    for job in jobs:
                        item = dict(job)
                        item['posted_date'] = item.get('posted_date') or item.get('posting_date') or ""
                        item['scraped_at'] = item.get('scraped_at') or scraped_at
                        job_dicts.append(item)
                    df = pd.DataFrame(job_dicts)
                else:
                    df = jobs.copy()
                    if 'scraped_at' not in df.columns:
                        df['scraped_at'] = scraped_at
                    if 'posted_date' not in df.columns and 'posting_date' in df.columns:
                        df['posted_date'] = df['posting_date']
                
                # Order columns matching fieldnames first, then any extra columns
                cols = [c for c in self.fieldnames if c in df.columns]
                extra_cols = [c for c in df.columns if c not in self.fieldnames]
                df = df[cols + extra_cols]
                
                # Explicitly write CSV with index=False and header=True
                # Header is written directly as the first row with no preceding empty lines or comments
                df.to_csv(
                    filepath,
                    mode=mode,
                    index=False,
                    header=True if mode == 'w' else write_header,
                    encoding='utf-8',
                )
                self.logger.info(f"Saved {len(df)} jobs to {filepath} using pandas")
                return str(filepath)
            
            else:
                # Add scraped timestamp to each job
                for job in jobs:
                    job['posted_date'] = job.get('posted_date') or job.get('posting_date') or ""
                    job['scraped_at'] = job.get('scraped_at') or scraped_at
                
                # Write to CSV using csv module:
                # Header is explicitly written as the first row with writer.writeheader()
                # Fields are properly escaped using csv.QUOTE_MINIMAL
                with open(filepath, mode, newline='', encoding='utf-8') as csvfile:
                    writer = csv.DictWriter(
                        csvfile,
                        fieldnames=self.fieldnames,
                        extrasaction='ignore',
                        quoting=csv.QUOTE_MINIMAL,
                    )
                    
                    if write_header:
                        writer.writeheader()
                    
                    writer.writerows(jobs)
                
                self.logger.info(f"Saved {len(jobs)} jobs to {filepath}")
                return str(filepath)
        
        except Exception as e:
            self.logger.error(f"Error writing to CSV file {filepath}: {e}")
            raise

    def _generate_filename(
        self,
        keywords: Optional[List[str]],
        locations: Optional[List[str]],
        query: Optional[str],
    ) -> str:
        """Build a unique filename for a search run."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if not query:
            return self._unique_filename(f"jobs_all_{timestamp}.csv")

        role = self._slugify(" ".join(keywords or [])) or "all"
        location = self._slugify(" ".join(locations or [])) or "all"
        return self._unique_filename(f"jobs_{role}_{location}_{timestamp}.csv")

    def _unique_filename(self, filename: str) -> str:
        """Avoid a collision if multiple runs start within one second."""
        candidate = self.data_dir / filename
        if not candidate.exists():
            return filename

        stem = candidate.stem
        suffix = candidate.suffix
        counter = 1
        while (self.data_dir / f"{stem}_{counter}{suffix}").exists():
            counter += 1
        return f"{stem}_{counter}{suffix}"

    @staticmethod
    def _slugify(value: str) -> str:
        """Convert search text into a filesystem-safe lowercase slug."""
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug
    
    def validate_job_data(self, job: Dict[str, Any]) -> bool:
        """
        Validate that a job dictionary has all required fields.
        
        Args:
            job: Job dictionary to validate
        
        Returns:
            True if valid, False otherwise
        """
        required_fields = ['title', 'company', 'url', 'source']
        
        for field in required_fields:
            if not job.get(field):
                self.logger.warning(f"Job missing required field: {field}")
                return False
        
        return True
    
    def read_jobs(self, filename: str) -> List[Dict[str, Any]]:
        """
        Read jobs from a CSV file.
        
        Args:
            filename: Name of the CSV file to read
        
        Returns:
            List of job dictionaries
        """
        filepath = self.data_dir / filename
        
        if not filepath.exists():
            self.logger.warning(f"CSV file does not exist: {filepath}")
            return []
        
        try:
            with open(filepath, 'r', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                jobs = list(reader)
            
            self.logger.info(f"Read {len(jobs)} jobs from {filepath}")
            return jobs
        
        except Exception as e:
            self.logger.error(f"Error reading CSV file {filepath}: {e}")
            return []
    
    def write_jobs_pandas(
        self,
        jobs: Any,
        filename: Optional[str] = None,
        mode: str = 'w',
        keywords: Optional[List[str]] = None,
        locations: Optional[List[str]] = None,
        query: Optional[str] = None,
    ) -> str:
        """
        Write jobs to a CSV file explicitly using pandas.
        
        Guarantees df.to_csv(..., index=False, header=True) is used with header
        as the first row, properly escaped, and not preceded by empty lines or
        metadata comments.
        """
        return self.write_jobs(
            jobs,
            filename=filename,
            mode=mode,
            keywords=keywords,
            locations=locations,
            query=query,
            use_pandas=True,
        )

    def read_jobs_df(self, filename: str) -> Any:
        """
        Read jobs from a CSV file into a pandas DataFrame.
        
        Args:
            filename: Name of the CSV file to read
            
        Returns:
            pandas DataFrame with properly parsed column names
        """
        if pd is None:
            raise ImportError("pandas is required for read_jobs_df")
        
        filepath = self.data_dir / filename
        if not filepath.exists():
            self.logger.warning(f"CSV file does not exist: {filepath}")
            return pd.DataFrame(columns=self.fieldnames)
        
        return pd.read_csv(filepath, header=0, encoding='utf-8')

    def get_duckdb_query(self, filename: str) -> str:
        """
        Generate a DuckDB query expression to read the CSV file with header=true.
        
        Args:
            filename: Name of the CSV file
            
        Returns:
            SQL query string using read_csv(..., header=true)
        """
        filepath = (self.data_dir / filename).resolve()
        return f"SELECT * FROM read_csv('{filepath}', header=true)"

    def query_jobs_duckdb(self, filename: str, sql: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Query jobs from a CSV file using DuckDB with header=true explicitly specified.
        
        Args:
            filename: Name of the CSV file to query
            sql: Optional custom SQL query. If '{table}' is in sql, it is replaced with
                 read_csv('<filepath>', header=true). If None, selects all rows.
                 
        Returns:
            List of dictionary records with proper column names
        """
        import duckdb
        
        filepath = (self.data_dir / filename).resolve()
        table_expr = f"read_csv('{filepath}', header=true)"
        
        if sql is None:
            query_str = f"SELECT * FROM {table_expr}"
        elif "{table}" in sql:
            query_str = sql.format(table=table_expr)
        else:
            query_str = sql
        
        conn = duckdb.connect()
        try:
            rel = conn.execute(query_str)
            columns = [desc[0] for desc in rel.description]
            rows = rel.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        finally:
            conn.close()

    def get_existing_files(self) -> List[str]:
        """
        Get list of existing CSV files in the data directory.
        
        Returns:
            List of CSV filenames
        """
        csv_files = list(self.data_dir.glob("*.csv"))
        return [f.name for f in csv_files]