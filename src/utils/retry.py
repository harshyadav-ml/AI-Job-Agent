"""
Async retry decorator with full exponential backoff + jitter.

Built on top of ``tenacity`` (already in requirements) so we get
battle-tested retry semantics without reinventing them.  The decorator
can be applied to any async coroutine.

Usage::

    from src.utils.retry import async_retry
    import httpx

    @async_retry(
        max_attempts=4,
        base_delay=2.0,
        retryable_exceptions=(httpx.HTTPStatusError, httpx.NetworkError),
    )
    async def fetch(url: str) -> str:
        ...
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from collections.abc import Callable, Awaitable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def async_retry(
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """
    Decorator factory for async functions with exponential backoff retries.

    Args:
        max_attempts:           Total attempts (1 = no retry, 3 = 2 retries).
        base_delay:             Initial sleep duration in seconds.
        max_delay:              Cap on computed sleep duration.
        exponential_base:       Growth factor between attempts (default 2×).
        jitter:                 Add ±50 % random jitter to avoid thundering-herd.
        retryable_exceptions:   Only retry on these exception types.
        on_retry:               Optional callback(attempt, exc, delay) for metrics.
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: object, **kwargs: object) -> T:
            last_exc: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)

                except retryable_exceptions as exc:  # type: ignore[misc]
                    last_exc = exc

                    if attempt == max_attempts:
                        logger.error(
                            "[retry] %s exhausted %d/%d attempts. Final error: %s",
                            fn.__qualname__,
                            attempt,
                            max_attempts,
                            exc,
                        )
                        break

                    delay = min(
                        base_delay * (exponential_base ** (attempt - 1)),
                        max_delay,
                    )
                    if jitter:
                        # Full jitter: uniform random in [0, computed_delay]
                        delay = random.uniform(0, delay)

                    logger.warning(
                        "[retry] %s attempt %d/%d failed (%s: %s). Retrying in %.2fs…",
                        fn.__qualname__,
                        attempt,
                        max_attempts,
                        type(exc).__name__,
                        exc,
                        delay,
                    )

                    if on_retry is not None:
                        try:
                            on_retry(attempt, exc, delay)
                        except Exception:
                            pass

                    await asyncio.sleep(delay)

            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator


async def retry_after_aware_sleep(response_headers: dict, default: float = 60.0) -> None:
    """
    Read the ``Retry-After`` header and sleep for that duration.
    Falls back to ``default`` if the header is absent or unparseable.
    """
    raw = response_headers.get("Retry-After") or response_headers.get("retry-after", "")
    try:
        seconds = float(raw)
    except (ValueError, TypeError):
        seconds = default
    seconds = min(max(seconds, 1.0), 300.0)  # clamp to [1s, 5min]
    logger.info("Respecting Retry-After: sleeping %.0fs", seconds)
    await asyncio.sleep(seconds)

