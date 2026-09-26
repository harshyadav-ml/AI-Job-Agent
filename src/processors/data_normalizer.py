"""
Data normalizer for standardizing job data across different platforms.
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import re

from ..config import get_logger

logger = get_logger(__name__)


class DataNormalizer:
    """Normalizes job data from different platforms to a standard format."""
    
    def __init__(self):
        """Initialize the data normalizer."""
        self.logger = get_logger(__name__)
    
    def normalize_jobs(self, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Normalize a list of job dictionaries.
        
        Args:
            jobs: List of job dictionaries from various sources
        
        Returns:
            List of normalized job dictionaries
        """
        self.logger.info(f"Normalizing {len(jobs)} jobs")
        
        normalized_jobs = []
        for job in jobs:
            try:
                normalized_job = self.normalize_single_job(job)
                normalized_jobs.append(normalized_job)
            except Exception as e:
                self.logger.error(f"Error normalizing job: {e}")
                continue
        
        self.logger.info(f"Successfully normalized {len(normalized_jobs)} jobs")
        return normalized_jobs
    
    def normalize_single_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize a single job dictionary.
        
        Args:
            job: Job dictionary from a source
        
        Returns:
            Normalized job dictionary
        """
        normalized = {
            'title': self._normalize_title(job.get('title', '')),
            'company': self._normalize_company(job.get('company', '')),
            'location': self._normalize_location(job.get('location', '')),
            'description': self._normalize_description(job.get('description', '')),
            'salary': self._normalize_salary(job.get('salary', '')),
            'url': job.get('url', ''),
            'posted_date': self._normalize_date(job.get('posted_date', job.get('posting_date', ''))),
            'job_type': self._normalize_job_type(job.get('job_type', '')),
            'source': job.get('source', 'unknown'),
            'scraped_at': job.get('scraped_at', '')
        }
        
        return normalized
    
    def _normalize_title(self, title: str) -> str:
        """Normalize job title."""
        return title.strip().title() if title else ""
    
    def _normalize_company(self, company: str) -> str:
        """Normalize company name."""
        return company.strip() if company else ""
    
    def _normalize_location(self, location: str) -> str:
        """Normalize location information."""
        if not location:
            return ""
        
        location = location.strip()
        
        # Standardize remote indicators
        remote_patterns = ['remote', 'work from home', 'wfh', 'telecommute']
        if any(pattern in location.lower() for pattern in remote_patterns):
            return "Remote"
        
        return location
    
    def _normalize_description(self, description: str) -> str:
        """Normalize job description."""
        if not description:
            return ""
        
        # Remove excessive whitespace
        description = re.sub(r'\s+', ' ', description)
        return description.strip()
    
    def _normalize_salary(self, salary: str) -> str:
        """Normalize salary information."""
        if not salary:
            return ""
        
        # Clean up salary string
        salary = salary.strip()
        # TODO: Add more sophisticated salary normalization
        return salary
    
    def _normalize_date(self, date_str: str) -> str:
        """Normalize date to ISO format."""
        if not date_str:
            return ""
        
        # TODO: Implement date parsing for various formats
        # For now, return as-is if it looks like a date
        return date_str
    
    def _normalize_job_type(self, job_type: str) -> str:
        """Normalize job type."""
        if not job_type:
            return "Full-time"  # Default assumption
        
        job_type = job_type.strip().lower()
        
        # Standardize job types
        type_mapping = {
            'full time': 'Full-time',
            'full-time': 'Full-time',
            'part time': 'Part-time',
            'part-time': 'Part-time',
            'contract': 'Contract',
            'freelance': 'Contract',
            'internship': 'Internship',
        }
        
        return type_mapping.get(job_type, job_type.title())