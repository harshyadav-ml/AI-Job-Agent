"""
Deduplicator for removing duplicate job listings across platforms.
"""

import logging
from typing import List, Dict, Any, Set
import hashlib

from ..config import get_logger

logger = get_logger(__name__)


class Deduplicator:
    """Removes duplicate job listings from aggregated data."""
    
    def __init__(self):
        """Initialize the deduplicator."""
        self.logger = get_logger(__name__)
        self.seen_job_ids: Set[str] = set()
    
    def deduplicate_jobs(self, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Remove duplicate jobs from the list.
        
        Args:
            jobs: List of job dictionaries
        
        Returns:
            List of deduplicated job dictionaries
        """
        self.logger.info(f"Deduplicating {len(jobs)} jobs")
        
        unique_jobs = []
        duplicates_count = 0
        
        for job in jobs:
            job_id = self._generate_job_id(job)
            
            if job_id not in self.seen_job_ids:
                self.seen_job_ids.add(job_id)
                unique_jobs.append(job)
            else:
                duplicates_count += 1
                self.logger.debug(f"Duplicate job found: {job.get('title')} at {job.get('company')}")
        
        self.logger.info(f"Removed {duplicates_count} duplicates, {len(unique_jobs)} unique jobs remain")
        return unique_jobs
    
    def _generate_job_id(self, job: Dict[str, Any]) -> str:
        """
        Generate a unique identifier for a job.
        
        Uses a combination of title, company, and location to identify duplicates.
        
        Args:
            job: Job dictionary
        
        Returns:
            Unique job identifier hash
        """
        # Create a string that represents the unique aspects of a job
        job_signature = f"{job.get('title', '').lower()}|{job.get('company', '').lower()}|{job.get('location', '').lower()}"
        
        # Generate hash
        return hashlib.md5(job_signature.encode()).hexdigest()
    
    def fuzzy_deduplicate(self, jobs: List[Dict[str, Any]], similarity_threshold: float = 0.8) -> List[Dict[str, Any]]:
        """
        Perform fuzzy deduplication for similar but not identical jobs.
        
        Args:
            jobs: List of job dictionaries
            similarity_threshold: Threshold for considering jobs as similar (0-1)
        
        Returns:
            List of deduplicated job dictionaries
        """
        self.logger.info(f"Performing fuzzy deduplication on {len(jobs)} jobs")
        
        # TODO: Implement fuzzy matching logic
        # This could use libraries like:
        # - difflib for string similarity
        # - fuzzywuzzy for fuzzy string matching
        # - scikit-learn for more advanced similarity
        
        # For now, return jobs as-is
        return jobs
    
    def cross_platform_deduplicate(self, jobs_by_platform: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """
        Deduplicate jobs across multiple platforms.
        
        Args:
            jobs_by_platform: Dictionary mapping platform names to job lists
        
        Returns:
            List of deduplicated jobs from all platforms
        """
        self.logger.info(f"Cross-platform deduplication for {len(jobs_by_platform)} platforms")
        
        all_jobs = []
        for platform, jobs in jobs_by_platform.items():
            self.logger.info(f"Processing {len(jobs)} jobs from {platform}")
            all_jobs.extend(jobs)
        
        return self.deduplicate_jobs(all_jobs)
    
    def reset(self):
        """Reset the seen job IDs."""
        self.seen_job_ids.clear()
        self.logger.info("Deduplicator reset")