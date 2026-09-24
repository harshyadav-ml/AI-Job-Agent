"""
CSV writer for storing job data in CSV format.
"""

import logging
import csv
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

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
        jobs: List[Dict[str, Any]],
        filename: Optional[str] = None,
        mode: str = 'w',
        keywords: Optional[List[str]] = None,
        locations: Optional[List[str]] = None,
        query: Optional[str] = None,
    ) -> str:
        """
        Write jobs to a CSV file.
        
        Args:
            jobs: List of job dictionaries
            filename: Name of the CSV file (auto-generated if not provided)
            mode: File write mode ('a' for append, 'w' for write)
            keywords: Parsed role keywords for generated filenames
            locations: Parsed locations for generated filenames
            query: Original search query; used to determine whether this is a search run
        
        Returns:
            Path to the written CSV file
        """
        if not jobs:
            self.logger.warning("No jobs to write")
            return ""
        
        # Generate a unique search-specific filename if not provided.
        if filename is None:
            filename = self._generate_filename(keywords, locations, query)
        
        filepath = self.data_dir / filename
        
        # Add scraped timestamp to each job
        scraped_at = datetime.now().isoformat()
        for job in jobs:
            job['posted_date'] = job.get('posted_date') or job.get('posting_date') or ""
            job['scraped_at'] = scraped_at
        
        # Write to CSV
        write_header = not filepath.exists() or mode == 'w'
        
        try:
            with open(filepath, mode, newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=self.fieldnames)
                
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
    
    def get_existing_files(self) -> List[str]:
        """
        Get list of existing CSV files in the data directory.
        
        Returns:
            List of CSV filenames
        """
        csv_files = list(self.data_dir.glob("*.csv"))
        return [f.name for f in csv_files]