"""
Configuration management for Job Agent.
Loads environment variables and provides configuration access.
"""

import os
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Settings:
    """Application settings loaded from environment variables."""
    
    # Base paths
    BASE_DIR = Path(__file__).parent.parent.parent
    DATA_DIR = BASE_DIR / os.getenv("DATA_DIR", "data")
    LOGS_DIR = BASE_DIR / "logs"
    
    # Firecrawl Configuration
    FIRECRAWL_API_KEY: str = os.getenv("FIRECRAWL_API_KEY", "")
    
    # Search Configuration
    SEARCH_KEYWORDS: List[str] = [
        keyword.strip() 
        for keyword in os.getenv("SEARCH_KEYWORDS", "python,developer").split(",")
    ]
    JOB_TITLES: List[str] = [
        title.strip() 
        for title in os.getenv("JOB_TITLES", "Software Engineer").split(",")
    ]
    
    # Location Filters
    LOCATIONS: List[str] = [
        location.strip() 
        for location in os.getenv("LOCATIONS", "remote").split(",")
    ]
    COUNTRY_FILTER: str = os.getenv("COUNTRY_FILTER", "")
    
    # Experience Level
    EXPERIENCE_LEVEL: List[str] = [
        level.strip() 
        for level in os.getenv("EXPERIENCE_LEVEL", "mid-level").split(",")
    ]
    
    # Rate Limiting Configuration
    REQUEST_DELAY: float = float(os.getenv("REQUEST_DELAY", "2"))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
    TIMEOUT: int = int(os.getenv("TIMEOUT", "30"))
    BROWSER_TIMEOUT: int = int(os.getenv("BROWSER_TIMEOUT", "30"))  # Browser timeout in seconds (30s)
    
    # Naukri Configuration
    NAUKRI_USER_AGENT: str = os.getenv(
        "NAUKRI_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )
    
    # RemoteOK Configuration
    REMOTEOK_API_URL: str = os.getenv("REMOTEOK_API_URL", "https://remoteok.com/api")
    
    # Wellfound Configuration
    WELLFOUND_BASE_URL: str = os.getenv("WELLFOUND_BASE_URL", "https://wellfound.com")
    
    # Logging Configuration
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str = os.getenv("LOG_FILE", "logs/job_agent.log")
    
    # Data Storage Configuration
    CSV_FILENAME_PATTERN: str = os.getenv("CSV_FILENAME_PATTERN", "jobs_{date}.csv")
    
    @classmethod
    def validate(cls) -> bool:
        """Validate that required configuration is present."""
        if not cls.FIRECRAWL_API_KEY:
            print("Warning: FIRECRAWL_API_KEY not set. Wellfound scraper will not work.")
        
        # Create necessary directories
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        
        return True
    
    @classmethod
    def get_csv_filename(cls, date: str = None) -> str:
        """Generate CSV filename with date."""
        if date is None:
            from datetime import datetime
            date = datetime.now().strftime("%Y-%m-%d")
        return cls.CSV_FILENAME_PATTERN.format(date=date)


# Global settings instance
settings = Settings()