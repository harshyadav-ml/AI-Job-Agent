"""
User-Agent rotation pool.

Provides a deterministically shuffled, randomly sampled pool of real
browser UA strings. Avoids the single static UA fingerprinting that modern
bot-detection systems (Cloudflare, DataDome, PerimeterX) trivially catch.

Usage::

    from src.utils.ua_rotator import get_random_ua, build_request_headers
    headers = build_request_headers()
"""

from __future__ import annotations

import random
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# UA pool — Chrome + Firefox on macOS / Windows / Linux
# Keep this list updated as browser versions advance.
# ---------------------------------------------------------------------------
_UA_POOL: list[str] = [
    # Chrome 128 — macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    # Chrome 127 — Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.6533.120 Safari/537.36",
    # Chrome 126 — Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    # Chrome 128 — Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.6613.120 Safari/537.36",
    # Firefox 128 — macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.6; rv:128.0) Gecko/20100101 Firefox/128.0",
    # Firefox 128 — Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    # Firefox 127 — Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
    # Edge 128 — Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
    # Safari 17 — macOS (for variety)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

# Rotate the pool once at module import so different processes use different
# starting points even if seeded by the same process time.
random.shuffle(_UA_POOL)


def get_random_ua() -> str:
    """Return a randomly selected User-Agent string from the pool."""
    ua = random.choice(_UA_POOL)
    logger.debug("UA rotator selected: %s", ua[:60])
    return ua


def build_request_headers(
    *,
    accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    referer: str = "https://www.google.com/",
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    Build a realistic browser-like header dict with a rotated UA.

    Args:
        accept:  Value of the Accept header.
        referer: Simulated referrer (defaults to Google to mimic organic traffic).
        extra:   Additional headers to merge (overrides defaults on collision).
    """
    headers: dict[str, str] = {
        "User-Agent": get_random_ua(),
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": referer,
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if extra:
        headers.update(extra)
    return headers

