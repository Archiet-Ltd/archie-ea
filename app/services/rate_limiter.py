"""
Rate Limiting Service - Prevents abuse and controls resource usage.

Provides decorators and utility functions for rate limiting pipeline operations,
API calls, and expensive LLM requests.

Uses a hybrid approach: in-memory token bucket for fast checking with
database-backed counting via LLMInteraction table for persistence across
server restarts (AUDIT-AI-004 fix).
"""
import logging
import time
from datetime import datetime, timedelta
from functools import wraps
from typing import Callable, Optional

from flask import current_app, request
from flask_login import current_user

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded."""

    def __init__(self, limit: int, window: str, retry_after: int = None):
        self.limit = limit
        self.window = window
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded: {limit} per {window}")


class RateLimiter:
    """
    Hybrid rate limiter: in-memory token bucket + database-backed counting.

    The in-memory bucket provides fast checking for normal operation.
    Database-backed counting via LLMInteraction ensures rate limits
    survive server restarts and cannot be bypassed by restarting the process.
    """

    def __init__(self):
        self._buckets = {}  # key -> {'tokens': int, 'last_update': float}
        self._cleanup_threshold = 10000  # Clean up every N accesses

    def check_rate_limit(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, Optional[int]]:
        """
        Check if action is within rate limit using in-memory token bucket.

        Args:
            key: Unique identifier (user_id, ip, etc.)
            limit: Maximum number of actions
            window_seconds: Time window in seconds

        Returns:
            Tuple of (allowed: bool, retry_after: Optional[int])
        """
        now = time.time()

        # Initialize bucket if not exists
        if key not in self._buckets:
            self._buckets[key] = {"tokens": limit, "last_update": now}

        bucket = self._buckets[key]

        # Calculate tokens to add based on time elapsed
        time_elapsed = now - bucket["last_update"]
        tokens_to_add = (time_elapsed / window_seconds) * limit
        bucket["tokens"] = min(limit, bucket["tokens"] + tokens_to_add)
        bucket["last_update"] = now

        # Check if we have tokens available
        if bucket["tokens"] >= 1:
            bucket["tokens"] -= 1
            return True, None
        else:
            # Calculate retry_after in seconds
            tokens_needed = 1 - bucket["tokens"]
            retry_after = int((tokens_needed / limit) * window_seconds) + 1
            return False, retry_after

    def check_db_rate_limit(
        self, user_id: int, limit: int, window_seconds: int
    ) -> tuple[bool, Optional[int]]:
        """
        Check rate limit using database-backed counting via LLMInteraction table.

        Survives server restarts - counts actual recorded interactions in the
        time window. Falls back to in-memory check if DB query fails.

        Args:
            user_id: User ID to check
            limit: Maximum number of actions allowed
            window_seconds: Time window in seconds

        Returns:
            Tuple of (allowed: bool, retry_after: Optional[int])
        """
        try:
            from app import db
            from app.models.models import LLMInteraction

            cutoff = datetime.utcnow() - timedelta(seconds=window_seconds)
            # tenant-scoping-ok: user_id scopes to one org
            interaction_count = (
                db.session.query(db.func.count(LLMInteraction.id))
                .filter(
                    LLMInteraction.user_id == user_id,
                    LLMInteraction.created_at >= cutoff,
                )
                .scalar()
            ) or 0

            if interaction_count >= limit:
                # Estimate retry_after based on oldest interaction in window
                oldest_in_window = (
                    db.session.query(LLMInteraction.created_at)
                    .filter(
                        LLMInteraction.user_id == user_id,
                        LLMInteraction.created_at >= cutoff,
                    )
                    .order_by(LLMInteraction.created_at.asc())
                    .first()
                )
                if oldest_in_window and oldest_in_window[0]:
                    oldest_time = oldest_in_window[0]
                    retry_after = int(
                        (oldest_time - cutoff).total_seconds() + window_seconds
                    )
                    retry_after = max(1, min(retry_after, window_seconds))
                else:
                    retry_after = window_seconds
                return False, retry_after

            return True, None

        except Exception as e:
            logger.warning(
                f"DB rate limit check failed for user {user_id}, "
                f"falling back to in-memory: {e}"
            )
            # Fall back to in-memory check if DB is unavailable
            key = f"db_fallback:user:{user_id}"
            return self.check_rate_limit(key, limit, window_seconds)

    def reset(self, key: str):
        """Reset rate limit for a key."""
        if key in self._buckets:
            del self._buckets[key]

    def cleanup_old_buckets(self, max_age_seconds: int = 3600):
        """Remove buckets not accessed recently."""
        now = time.time()
        to_remove = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket["last_update"] > max_age_seconds
        ]
        for key in to_remove:
            del self._buckets[key]

        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} old rate limit buckets")


# Global rate limiter instance
_rate_limiter = RateLimiter()


def rate_limit(
    limit: int,
    window: str,
    key_func: Callable = None,
    methods: Optional[tuple[str, ...] | list[str]] = None,
):
    """
    Decorator to rate limit endpoints.

    Args:
        limit: Maximum requests
        window: Time window ('1h', '1m', '1d')
        key_func: Optional function to generate rate limit key
                 (defaults to user_id or IP address)

    Example:
        @rate_limit(10, '1h')  # 10 requests per hour
        def my_endpoint():
            ...
    """
    # Parse window string
    window_map = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = window[-1]
    value = int(window[:-1])
    window_seconds = value * window_map.get(unit, 1)

    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            # Optional method scoping: e.g. methods=("POST",) to avoid
            # throttling read-only page loads on mixed GET/POST routes.
            if methods:
                allowed_methods = {m.upper() for m in methods}
                if request.method.upper() not in allowed_methods:
                    return f(*args, **kwargs)

            # Internal environments can disable app-level throttling centrally.
            if not current_app.config.get("RATE_LIMITING_ENABLED", True):
                return f(*args, **kwargs)

            # Determine rate limit key — include endpoint so limits are per-route,
            # not shared across all rate-limited endpoints for the same user.
            endpoint = request.endpoint or f.__name__
            if key_func:
                key = key_func()
            elif current_user and current_user.is_authenticated:
                key = f"user:{current_user.id}:{endpoint}"
            else:
                key = f"ip:{request.remote_addr}:{endpoint}"

            # Check rate limit
            allowed, retry_after = _rate_limiter.check_rate_limit(key, limit, window_seconds)

            if not allowed:
                logger.warning(f"Rate limit exceeded for {key}: {limit}/{window}")
                raise RateLimitExceeded(limit, window, retry_after)

            return f(*args, **kwargs)

        return wrapped

    return decorator


def check_pipeline_rate_limit(user_id: int) -> tuple[bool, Optional[str]]:
    """
    Check if user can execute another pipeline.

    Uses database-backed counting (LLMInteraction table) to survive server
    restarts. Falls back to in-memory check if DB is unavailable.

    Args:
        user_id: User ID

    Returns:
        Tuple of (allowed, error_message)
    """
    # Limit: 10 pipelines per hour per user
    # Use DB-backed check to persist across server restarts (AUDIT-AI-004)
    allowed, retry_after = _rate_limiter.check_db_rate_limit(
        user_id=user_id, limit=10, window_seconds=3600
    )

    if not allowed:
        return False, f"Pipeline execution rate limit exceeded. Retry in {retry_after}s"

    return True, None


def check_llm_rate_limit(user_id: int) -> tuple[bool, Optional[str]]:
    """
    Check if user can make another LLM API call.

    Uses database-backed counting (LLMInteraction table) to survive server
    restarts. Falls back to in-memory check if DB is unavailable.

    Args:
        user_id: User ID

    Returns:
        Tuple of (allowed, error_message)
    """
    # Limit: 100 LLM calls per hour per user
    # Use DB-backed check to persist across server restarts (AUDIT-AI-004)
    allowed, retry_after = _rate_limiter.check_db_rate_limit(
        user_id=user_id, limit=100, window_seconds=3600
    )

    if not allowed:
        return False, f"LLM API rate limit exceeded. Retry in {retry_after}s"

    return True, None


def track_api_usage(user_id: int, endpoint: str):
    """
    Track API usage for analytics.

    Args:
        user_id: User ID
        endpoint: API endpoint name
    """
    from app import db
    from app.models import APIUsage

    try:
        usage = APIUsage(
            user_id=user_id,
            endpoint=endpoint,
            timestamp=datetime.utcnow(),
            ip_address=request.remote_addr if request else None,
        )
        db.session.add(usage)
        db.session.commit()
    except Exception as e:
        logger.error(f"Failed to track API usage: {e}")
        # Don't fail the request if tracking fails


# Periodic cleanup task (call from scheduler)
def cleanup_rate_limiters():
    """Clean up old rate limiter buckets (call periodically)."""
    _rate_limiter.cleanup_old_buckets(max_age_seconds=7200)  # 2 hours
