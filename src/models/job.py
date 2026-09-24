"""
Shared data models for the Job Agent application.
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class Job:
    """Unified data model for job information across all platforms."""
    
    # Core job information
    title: str
    company: str
    location: str
    description: str
    url: str
    source: str
    
    # Optional fields (as per requirements)
    salary: Optional[str] = None
    posted_date: Optional[str] = None
    job_type: Optional[str] = None
    
    # Additional metadata for enhanced functionality
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    tags: list = field(default_factory=list)
    company_logo: str = ""
    scraped_at: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert Job object to dictionary."""
        return {
            'title': self.title,
            'company': self.company,
            'location': self.location,
            'description': self.description,
            'salary': self.salary or "",
            'url': self.url,
            'posted_date': self.posted_date or "",
            'job_type': self.job_type or "Full-time",
            'source': self.source,
            'scraped_at': self.scraped_at or datetime.now().isoformat()
        }
    
    def __post_init__(self):
        """Validate and clean data after initialization."""
        # Clean up whitespace for string fields
        self.title = self.title.strip() if self.title else ""
        self.company = self.company.strip() if self.company else ""
        self.location = self.location.strip() if self.location else ""
        self.description = self.description.strip() if self.description else ""
        self.url = self.url.strip() if self.url else ""
        
        # Clean optional string fields if they exist
        if self.salary:
            self.salary = self.salary.strip()
        if self.posted_date:
            self.posted_date = self.posted_date.strip()
        if self.job_type:
            self.job_type = self.job_type.strip()
        
        # Set scraped_at if not provided
        if not self.scraped_at:
            self.scraped_at = datetime.now().isoformat()