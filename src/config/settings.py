"""
Application settings — validated at startup via pydantic-settings.

All configuration is sourced from environment variables or the .env file.
Bad values (wrong type, out-of-range numbers, missing required keys) raise
a ``ValidationError`` immediately on import — not buried in a runtime crash
ten minutes into a scrape run.

Required env vars (must be present in .env or environment):
    NAUKRI_CLIENT_ID   — Internal Naukri API client credential.

Optional but strongly recommended:
    FIRECRAWL_API_KEY  — Without this, the Wellfound scraper is disabled.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Typed, validated application configuration.

    Pydantic-settings reads from (in priority order):
    1. Real environment variables
    2. The .env file in the project root
    """

    model_config = SettingsConfigDict(
        # Walk up from this file to find the project root .env
        env_file=str(Path(__file__).parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Unknown extra env vars are silently ignored — avoids breakage when
        # the shell carries unrelated variables.
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Paths                                                                #
    # ------------------------------------------------------------------ #
    BASE_DIR: Path = Path(__file__).parent.parent.parent
    DATA_DIR: Path = Field(default=Path("data"))
    LOGS_DIR: Path = Field(default=Path("logs"))

    @field_validator("DATA_DIR", "LOGS_DIR", mode="before")
    @classmethod
    def _resolve_path(cls, v: object) -> Path:
        return Path(str(v))

    # ------------------------------------------------------------------ #
    # Firecrawl                                                            #
    # ------------------------------------------------------------------ #
    FIRECRAWL_API_KEY: str = Field(
        default="",
        description="Firecrawl API key. Wellfound scraper is disabled if empty.",
    )

    # ------------------------------------------------------------------ #
    # Search defaults                                                      #
    # ------------------------------------------------------------------ #
    # Typed as str (not List[str]) intentionally: pydantic-settings would
    # attempt JSON-decode on a List field read from an env var / .env file,
    # which breaks with plain comma-separated values like "python,developer".
    # We expose parsed list properties below.
    SEARCH_KEYWORDS: str = Field(default="python developer")
    JOB_TITLES: str = Field(default="Software Engineer")
    LOCATIONS: str = Field(default="remote")
    COUNTRY_FILTER: str = ""
    EXPERIENCE_LEVEL: str = Field(default="mid-level")

    @property
    def search_keywords_list(self) -> List[str]:
        return [s.strip() for s in self.SEARCH_KEYWORDS.split(",") if s.strip()]

    @property
    def job_titles_list(self) -> List[str]:
        return [s.strip() for s in self.JOB_TITLES.split(",") if s.strip()]

    @property
    def locations_list(self) -> List[str]:
        return [s.strip() for s in self.LOCATIONS.split(",") if s.strip()]

    @property
    def experience_level_list(self) -> List[str]:
        return [s.strip() for s in self.EXPERIENCE_LEVEL.split(",") if s.strip()]

    # ------------------------------------------------------------------ #
    # Rate-limiting / networking                                           #
    # ------------------------------------------------------------------ #
    REQUEST_DELAY: float = Field(
        default=2.0,
        ge=0.0,
        le=120.0,
        description="Minimum seconds between HTTP requests to the same host.",
    )
    MAX_RETRIES: int = Field(default=3, ge=1, le=10)
    TIMEOUT: int = Field(
        default=30,
        ge=5,
        le=300,
        description="HTTP request timeout in seconds.",
    )
    BROWSER_TIMEOUT: int = Field(
        default=30,
        ge=5,
        le=300,
        description="Playwright/Selenium page-load timeout in seconds.",
    )

    # ------------------------------------------------------------------ #
    # Naukri                                                               #
    # ------------------------------------------------------------------ #
    NAUKRI_CLIENT_ID: str = Field(
        default="d3Wh4DeBn0q20",
        description=(
            "Naukri internal API clientid header. "
            "Rotate this via .env when Naukri invalidates the default."
        ),
    )
    NAUKRI_APP_ID: str = Field(default="109")
    NAUKRI_SYSTEM_ID: str = Field(default="109")
    NAUKRI_USER_AGENT: str = Field(
        default=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        )
    )

    # ------------------------------------------------------------------ #
    # RemoteOK                                                             #
    # ------------------------------------------------------------------ #
    REMOTEOK_API_URL: str = Field(default="https://remoteok.com/api")

    # ------------------------------------------------------------------ #
    # Wellfound                                                            #
    # ------------------------------------------------------------------ #
    WELLFOUND_BASE_URL: str = Field(default="https://wellfound.com")

    # ------------------------------------------------------------------ #
    # Logging                                                              #
    # ------------------------------------------------------------------ #
    LOG_LEVEL: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    LOG_FILE: str = Field(default="logs/job_agent.log")

    # ------------------------------------------------------------------ #
    # Storage                                                              #
    # ------------------------------------------------------------------ #
    CSV_FILENAME_PATTERN: str = Field(default="jobs_{date}.csv")

    # ------------------------------------------------------------------ #
    # Post-init: ensure directories exist and emit warnings for missing   #
    # optional but important keys.                                        #
    # ------------------------------------------------------------------ #
    @model_validator(mode="after")
    def _ensure_dirs_and_warn(self) -> "Settings":
        abs_data = (self.BASE_DIR / self.DATA_DIR) if not self.DATA_DIR.is_absolute() else self.DATA_DIR
        abs_logs = (self.BASE_DIR / self.LOGS_DIR) if not self.LOGS_DIR.is_absolute() else self.LOGS_DIR
        abs_data.mkdir(parents=True, exist_ok=True)
        abs_logs.mkdir(parents=True, exist_ok=True)

        import logging
        log = logging.getLogger(__name__)
        if not self.FIRECRAWL_API_KEY:
            log.warning(
                "FIRECRAWL_API_KEY is not set — Wellfound scraper will be disabled."
            )
        return self

    # ------------------------------------------------------------------ #
    # Convenience helpers                                                  #
    # ------------------------------------------------------------------ #
    def get_absolute_data_dir(self) -> Path:
        p = self.DATA_DIR
        return (self.BASE_DIR / p) if not p.is_absolute() else p

    def get_csv_filename(self, date: Optional[str] = None) -> str:
        if date is None:
            from datetime import datetime
            date = datetime.now().strftime("%Y%m%d_%H%M%S")
        return self.CSV_FILENAME_PATTERN.format(date=date)

    def validate(self) -> bool:
        """Legacy compatibility shim used by main.py."""
        return True


# Module-level singleton — validated at import time.
settings = Settings()
