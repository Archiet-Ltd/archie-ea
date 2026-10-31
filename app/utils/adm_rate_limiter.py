"""
ADM Rate Limiter

Per-user rate limits for ADM Kanban API endpoints, counted in the one shared counter store
(``app.services.rate_limiter.count_hit``) so every worker sees the same count.
"""

import logging
from typing import Any, Dict

from flask import current_app

from app.services.rate_limiter import _counter_key, count_hit, counter_storage

logger = logging.getLogger(__name__)


class ADMRateLimitExceeded(Exception):
    """Exception raised when rate limit is exceeded."""

    def __init__(self, message: str, retry_after: int = 60, verdict=None):
        self.message = message
        self.retry_after = retry_after
        self.verdict = verdict
        super().__init__(self.message)


class ADMRateLimiter:
    """
    Rate limiter for ADM Kanban API.

    Provides per-user rate limits with different tiers for different operations.
    """

    # Rate limit tiers (requests per window)
    DEFAULT_LIMITS = {
        "read": {"requests": 100, "window": 60},  # 100 reads per minute
        "write": {"requests": 20, "window": 60},  # 20 writes per minute
        "approval": {"requests": 10, "window": 60},  # 10 approvals per minute
        "transition": {"requests": 5, "window": 60},  # 5 transitions per minute
        "admin": {"requests": 50, "window": 60},  # 50 admin ops per minute
    }

    @staticmethod
    def _scope(action_type: str) -> str:
        return "adm-" + action_type

    def check_rate_limit(
        self, user_id: int, action_type: str = "read"
    ) -> Dict[str, Any]:
        """
        Count one action for the user and check the limit.

        Args:
            user_id: User ID to check
            action_type: Type of action (read, write, approval, transition, admin)

        Returns:
            Rate limit status

        Raises:
            ADMRateLimitExceeded: If rate limit exceeded
        """
        limit_config = self.DEFAULT_LIMITS.get(action_type, self.DEFAULT_LIMITS["read"])
        if not current_app.config.get("RATE_LIMITING_ENABLED", True):
            return {"allowed": True, "remaining": -1}

        verdict = count_hit(
            self._scope(action_type), str(user_id), limit_config["requests"], limit_config["window"]
        )
        if not verdict.allowed:
            raise ADMRateLimitExceeded(
                f"Rate limit exceeded for {action_type}. Try again in {verdict.retry_after} seconds.",
                retry_after=verdict.retry_after,
                verdict=verdict,
            )
        return {
            "allowed": True,
            "remaining": verdict.remaining,
            "limit": verdict.limit,
            "window": limit_config["window"],
        }

    def get_rate_limit_status(self, user_id: int) -> Dict[str, Any]:
        """Get current rate limit status for all action types."""
        storage = counter_storage()
        status = {}
        for action_type, config in self.DEFAULT_LIMITS.items():
            used = storage.get(_counter_key(self._scope(action_type), str(user_id)))
            status[action_type] = {
                "used": used,
                "remaining": max(0, config["requests"] - used),
                "limit": config["requests"],
            }
        return status


# Singleton instance
adm_rate_limiter = ADMRateLimiter()
