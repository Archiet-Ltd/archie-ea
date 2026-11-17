"""
Unified rate limiter base class and re-exports of domain-specific limiters.

The three existing limiters (ADM, AI, Import) are domain-specific with different
designs. Rather than forcibly merging them, this module provides:

1. A ``RateLimiterBase`` abstract class for new rate limiters.
2. Re-exports so that ``from app.core.security.rate_limiter import ...`` works.

Existing sources:
- app/utils/adm_rate_limiter.py  — ADMRateLimiter (shared counter, per-user)
- app/utils/ai_rate_limiter.py   — AIUsageTracker (DB-backed, per-user)
- app/utils/import_rate_limiter.py — import_rate_limit (shared counter)
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class RateLimiterBase(ABC):
    """Abstract base for rate limiters.

    Subclasses must implement ``check()`` and ``record()``.
    """

    @abstractmethod
    def check(self, identity: str, action: str = "default") -> Tuple[bool, Optional[int]]:
        """Check whether the request is allowed.

        Args:
            identity: User ID, IP address, or API key.
            action: Action category (e.g. "read", "write").

        Returns:
            (allowed, retry_after_seconds).  ``retry_after`` is None when allowed.
        """

    @abstractmethod
    def record(self, identity: str, action: str = "default") -> None:
        """Record a request for the given identity/action."""


class InMemoryRateLimiter(RateLimiterBase):
    """Simple in-memory sliding-window rate limiter (dev / fallback).

    Not suitable for multi-process production — use Redis-backed limiters.
    """

    def __init__(self, requests: int = 60, window: int = 60):
        self._requests = requests
        self._window = window
        self._store: Dict[str, list] = {}

    def _key(self, identity: str, action: str) -> str:
        return f"{identity}:{action}"

    def check(self, identity: str, action: str = "default") -> Tuple[bool, Optional[int]]:
        key = self._key(identity, action)
        now = time.time()
        timestamps = self._store.get(key, [])
        timestamps = [t for t in timestamps if now - t < self._window]
        self._store[key] = timestamps

        if len(timestamps) >= self._requests:
            retry_after = int(self._window - (now - timestamps[0])) + 1
            return False, retry_after
        return True, None

    def record(self, identity: str, action: str = "default") -> None:
        key = self._key(identity, action)
        self._store.setdefault(key, []).append(time.time())
