"""
Import Rate Limiter

Rate limiting for the import and batch-import endpoints, counted in the one shared counter store
(``app.services.rate_limiter.count_hit``) so every worker sees the same count and a restart does not
clear it. Nothing here keeps a count in process memory.

Each decorated endpoint has a per-minute, per-hour and per-day allowance for the caller (the signed-in
user, else the connection's address). An address that is refused repeatedly is blocked for an hour.
A refusal is an HTTP 429 that says how long to wait, with Retry-After and RateLimit headers.
"""

import functools
import logging
import time

from flask import current_app, jsonify, request
from flask_login import current_user

from app.services.rate_limiter import (
    Verdict,
    _counter_key,
    apply_headers,
    client_address,
    count_hit,
    counter_storage,
    peek_count,
    wait_phrase,
)

logger = logging.getLogger(__name__)

VIOLATION_THRESHOLD = 5  # refusals before the address is blocked
IP_BLOCK_DURATION = 3600  # one hour

_WINDOWS = (("minute", 60), ("hour", 3600), ("day", 86400))


def _caller():
    if getattr(current_user, "is_authenticated", False):
        return "user:%s" % current_user.get_id()
    return "ip:%s" % client_address()


def check_import_limit(max_calls_per_minute=10, max_calls_per_hour=100, max_calls_per_day=500):
    """Count one import call. Returns (verdict, message) where verdict is None when throttling is off.

    The verdict returned is the one that decides: the first window that is exhausted, else the
    tightest one that is not.
    """
    if not current_app.config.get("RATE_LIMITING_ENABLED", True):
        return None, None
    address = client_address()
    if peek_count("import-violations", address) >= VIOLATION_THRESHOLD:
        until = counter_storage().get_expiry(_counter_key("import-violations", address))
        return Verdict(False, 0, 0, max(1, int(until - time.time())), until), "This address is temporarily blocked after repeated refused imports."
    caller = _caller()
    endpoint = request.endpoint or "import"
    limits = dict(zip(("minute", "hour", "day"), (max_calls_per_minute, max_calls_per_hour, max_calls_per_day)))
    verdicts = []
    for name, seconds in _WINDOWS:
        verdicts.append((name, count_hit("import-" + name, "%s:%s" % (caller, endpoint), limits[name], seconds)))
    for name, verdict in verdicts:
        if not verdict.allowed:
            count_hit("import-violations", address, VIOLATION_THRESHOLD, IP_BLOCK_DURATION)
            return verdict, "Too many imports: the limit is %d per %s. Try again in %s." % (
                verdict.limit, name, wait_phrase(verdict.retry_after))
    return min((v for _, v in verdicts), key=lambda v: v.remaining), None


def import_rate_limit(max_calls_per_minute: int = 10, max_calls_per_hour: int = 100, max_calls_per_day: int = 500):
    """Decorator for the import endpoints; the arguments are the allowances per window."""

    def decorator(f):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            verdict, message = check_import_limit(max_calls_per_minute, max_calls_per_hour, max_calls_per_day)
            if verdict is not None and not verdict.allowed:
                response = jsonify({
                    "success": False,
                    "error": message,
                    "error_type": "rate_limited",
                    "retry_after": verdict.retry_after,
                })
                response.status_code = 429
                logger.warning("Import limit reached for %s at %s", _caller(), client_address())
                return apply_headers(response, verdict)
            return f(*args, **kwargs)

        return decorated_function

    return decorator


def add_rate_limit_headers(response):
    """Report the allowance left on a response (the same RateLimit headers every limiter sends)."""
    return apply_headers(response)
