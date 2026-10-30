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


# ---------------------------------------------------------------------------------------------
# Shared limit, allow and deny lists, violations (T-limits01)
#
# The request limiter itself is Flask-Limiter, installed once for the whole application in
# app/_bootstrap/rate_limiting.py. What lives here is everything that limiter stands on:
#   * DatabaseStorage      its counters, in the database, so two workers count one limit;
#   * the allow and deny lists, read through a cache keyed by a version of the list so a change
#     made on one worker is honoured by every worker on its next request;
#   * the violation log, kept thirty days.
# ---------------------------------------------------------------------------------------------
import ipaddress  # noqa: E402
import threading  # noqa: E402

from sqlalchemy import text  # noqa: E402

try:  # the limits package ships with Flask-Limiter; without it there is no shared storage to offer
    from limits.storage import Storage as _LimitsStorage
except ImportError:  # pragma: no cover - dependency is pinned
    _LimitsStorage = None

DENIED_MESSAGE = (
    "Requests from this address are blocked; ask the administrator of this service to lift "
    "the block if that is a mistake."
)
STORAGE_URI = "archiedb://"
_ENTRY_ERRORS = {
    "address": "Enter an address such as 203.0.113.7 or a range such as 203.0.113.0/24.",
    "user": "No user has that email address.",
    "action": "Choose whether the entry allows or denies.",
    "duplicate": "That entry is already on the list.",
    "own_address": "That would block the address you are using now, so you would lose access.",
}


def _engine():
    from app import db

    return db.engine


if _LimitsStorage is not None:

    class DatabaseStorage(_LimitsStorage):
        """Flask-Limiter / limits storage that counts in the rate_limit_counters table.

        Every call runs on its own connection and commits, so the count is shared by every worker and
        survives a restart, and a request that later rolls back does not give its count back.
        """

        STORAGE_SCHEME = ["archiedb"]

        def __init__(self, uri=None, wrap_exceptions=False, engine=None, **options):
            super().__init__(uri, wrap_exceptions=wrap_exceptions, **options)
            self._engine_override = engine  # a test gives each simulated worker its own connection pool

        def _db(self):
            return self._engine_override or _engine()

        @property
        def base_exceptions(self):
            return Exception

        def incr(self, key: str, expiry: int, amount: int = 1) -> int:
            now = time.time()
            with self._db().begin() as connection:
                count = connection.execute(
                    text(
                        "INSERT INTO rate_limit_counters (key, count, expires_at) "
                        "VALUES (:k, :a, :until) "
                        "ON CONFLICT (key) DO UPDATE SET "
                        "count = CASE WHEN rate_limit_counters.expires_at <= :now "
                        "THEN :a ELSE rate_limit_counters.count + :a END, "
                        "expires_at = CASE WHEN rate_limit_counters.expires_at <= :now "
                        "THEN :until ELSE rate_limit_counters.expires_at END "
                        "RETURNING count"
                    ),
                    {"k": key[:255], "a": amount, "until": now + expiry, "now": now},
                ).scalar()
                if int(now * 1000) % 200 == 0:  # occasional sweep of finished windows
                    connection.execute(
                        text("DELETE FROM rate_limit_counters WHERE expires_at <= :cutoff"),
                        {"cutoff": now - 3600},
                    )
            return int(count)

        def get(self, key: str) -> int:
            with self._db().connect() as connection:
                value = connection.execute(
                    text("SELECT count FROM rate_limit_counters WHERE key = :k AND expires_at > :now"),
                    {"k": key[:255], "now": time.time()},
                ).scalar()
            return int(value or 0)

        def get_expiry(self, key: str) -> float:
            with self._db().connect() as connection:
                value = connection.execute(
                    text("SELECT expires_at FROM rate_limit_counters WHERE key = :k"),
                    {"k": key[:255]},
                ).scalar()
            return float(value if value is not None else time.time())

        def check(self) -> bool:
            with self._db().connect() as connection:
                connection.execute(text("SELECT 1"))
            return True

        def reset(self):
            with self._db().begin() as connection:
                return connection.execute(text("DELETE FROM rate_limit_counters")).rowcount

        def clear(self, key: str) -> None:
            with self._db().begin() as connection:
                connection.execute(text("DELETE FROM rate_limit_counters WHERE key = :k"), {"k": key[:255]})

else:  # pragma: no cover
    DatabaseStorage = None


# ---- allow and deny lists --------------------------------------------------------------------

_rules_lock = threading.Lock()
_rules_cache = {"version": None, "rules": ()}


def _compile_rule(row):
    """(action, kind, matcher, id, label) for one stored entry; None for an entry that cannot match."""
    action, kind, value, rule_id = row
    if kind == "address":
        try:
            return (action, kind, ipaddress.ip_network(value, strict=False), rule_id, value)
        except ValueError:
            return None
    return (action, kind, str(value), rule_id, "user " + str(value))


def current_rules():
    """The entries in force, re-read only when the list has changed since this worker last looked.

    The version is (highest id, number of rows): ids are never reused, so adding or removing an
    entry always changes it, and one aggregate over a tiny table is the whole per-request cost.
    """
    with _engine().connect() as connection:
        version = tuple(
            connection.execute(text("SELECT COALESCE(MAX(id), 0), COUNT(*) FROM rate_limit_rules")).one()
        )
        if version == _rules_cache["version"]:
            return _rules_cache["rules"]
        rows = connection.execute(text("SELECT action, kind, value, id FROM rate_limit_rules")).all()
    compiled = tuple(rule for rule in (_compile_rule(tuple(row)) for row in rows) if rule is not None)
    with _rules_lock:
        _rules_cache["version"] = version
        _rules_cache["rules"] = compiled
    return compiled


def _rule_matches(rule, address, user_id) -> bool:
    _action, kind, matcher, _rule_id, _label = rule
    if kind == "user":
        return user_id is not None and matcher == str(user_id)
    if address is None:
        return False
    try:
        return ipaddress.ip_address(address) in matcher
    except ValueError:
        return False


def access_verdict(address, user_id=None):
    """("deny" | "allow" | None, label of the entry that decided). A deny entry beats any allow entry."""
    rules = current_rules()
    for wanted in ("deny", "allow"):
        for rule in rules:
            if rule[0] == wanted and _rule_matches(rule, address, user_id):
                return wanted, rule[4]
    return None, None


class RuleError(ValueError):
    """An allow or deny entry that cannot be saved; the message is the sentence to show."""


def add_rule(action, kind, value, *, note=None, created_by_id=None, own_address=None):
    """Save one entry. Returns the new row's id. Raises RuleError with a plain sentence."""
    from app.models.rate_limit import ACTIONS

    if action not in ACTIONS:
        raise RuleError(_ENTRY_ERRORS["action"])
    value = (value or "").strip()
    if kind == "user":
        with _engine().connect() as connection:
            user_id = connection.execute(
                text("SELECT id FROM users WHERE lower(email) = lower(:e)"), {"e": value}
            ).scalar()
        if user_id is None:
            raise RuleError(_ENTRY_ERRORS["user"])
        stored = str(user_id)
    else:
        kind = "address"
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            raise RuleError(_ENTRY_ERRORS["address"]) from None
        stored = str(network.network_address) if network.num_addresses == 1 else str(network)
        if action == "deny" and own_address:
            try:
                own = ipaddress.ip_address(own_address)
            except ValueError:
                own = None
            if own is not None and own in network:
                raise RuleError(_ENTRY_ERRORS["own_address"])
    with _engine().begin() as connection:
        existing = connection.execute(
            text("SELECT 1 FROM rate_limit_rules WHERE action = :a AND kind = :k AND value = :v"),
            {"a": action, "k": kind, "v": stored},
        ).scalar()
        if existing:
            raise RuleError(_ENTRY_ERRORS["duplicate"])
        return connection.execute(
            text(
                "INSERT INTO rate_limit_rules (action, kind, value, note, created_by_id, created_at) "
                "VALUES (:a, :k, :v, :n, :by, :now) RETURNING id"
            ),
            {"a": action, "k": kind, "v": stored, "n": (note or "")[:255] or None,
             "by": created_by_id, "now": datetime.utcnow()},
        ).scalar()


def remove_rule(rule_id) -> bool:
    with _engine().begin() as connection:
        return connection.execute(
            text("DELETE FROM rate_limit_rules WHERE id = :i"), {"i": rule_id}
        ).rowcount > 0


def list_rules():
    """Every entry for the page, with the email of a user entry."""
    with _engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT r.id, r.action, r.kind, r.value, r.note, r.created_at, u.email "
                "FROM rate_limit_rules r LEFT JOIN users u "
                "ON r.kind = 'user' AND r.value = CAST(u.id AS VARCHAR) "
                "ORDER BY r.id DESC"
            )
        ).all()
    return [
        {"id": r[0], "action": r[1], "kind": r[2], "value": r[3], "note": r[4], "created_at": r[5],
         "subject": (r[6] or ("user " + r[3])) if r[2] == "user" else r[3]}
        for r in rows
    ]


# ---- violations ------------------------------------------------------------------------------

_last_purge = {"at": 0.0}


def purge_violations(now=None) -> int:
    """Delete refusals older than the retention period; returns how many went."""
    from app.models.rate_limit import VIOLATION_RETENTION_DAYS

    cutoff = (now or datetime.utcnow()) - timedelta(days=VIOLATION_RETENTION_DAYS)
    with _engine().begin() as connection:
        connection.execute(text("SELECT set_config('archie.platform_scope', 'on', true)"))
        return connection.execute(
            text("DELETE FROM rate_limit_violations WHERE created_at < :c"), {"c": cutoff}
        ).rowcount


def record_violation(*, address, user_id, route, rule):
    """Log one refusal. Never raises: a log that fails must not turn a refusal into an error."""
    try:
        with _engine().begin() as connection:
            # A refusal can happen before any organisation is known, so the row is written in the
            # platform scope; the organisation recorded on it is the signed-in user's home one.
            connection.execute(text("SELECT set_config('archie.platform_scope', 'on', true)"))
            organization_id = None
            if user_id is not None:
                organization_id = connection.execute(
                    text("SELECT organization_id FROM users WHERE id = :u"), {"u": user_id}
                ).scalar()
            connection.execute(
                text(
                    "INSERT INTO rate_limit_violations "
                    "(organization_id, address, user_id, route, rule, created_at) "
                    "VALUES (:o, :a, :u, :r, :rule, :now)"
                ),
                {"o": organization_id, "a": (address or "")[:64], "u": user_id,
                 "r": (route or "")[:255], "rule": (rule or "")[:120], "now": datetime.utcnow()},
            )
        if time.time() - _last_purge["at"] > 3600:
            _last_purge["at"] = time.time()
            purge_violations()
    except Exception as exc:
        logger.warning("rate limit violation not logged: %s", exc)


def violation_page(*, organization_id=None, address=None, user=None, since=None, until=None,
                   page=1, per_page=25):
    """One page of the violation log, newest first, inside the retention period.

    organization_id=None reads every organisation (the platform administrator's view, which also
    includes refusals of people who were not signed in). A number reads only that organisation's
    own users' refusals, in the database as well as in the query.
    """
    from app.models.rate_limit import VIOLATION_RETENTION_DAYS

    where = ["v.created_at >= :floor"]
    params = {"floor": datetime.utcnow() - timedelta(days=VIOLATION_RETENTION_DAYS)}
    if organization_id is not None:
        where.append("v.organization_id = :org")
        params["org"] = organization_id
    if address:
        where.append("v.address LIKE :addr")
        params["addr"] = "%" + address.strip() + "%"
    if user:
        where.append("lower(u.email) LIKE :usr")
        params["usr"] = "%" + user.strip().lower() + "%"
    if since:
        where.append("v.created_at >= :since")
        params["since"] = since
    if until:
        where.append("v.created_at < :until")
        params["until"] = until
    clause = " AND ".join(where)
    page = max(1, int(page or 1))
    with _engine().begin() as connection:
        if organization_id is None:
            connection.execute(text("SELECT set_config('archie.platform_scope', 'on', true)"))
        else:
            connection.execute(
                text("SELECT set_config('archie.organization_id', :o, true)"), {"o": str(organization_id)}
            )
        total = connection.execute(
            text(  # nosec B608 - clause is built from fixed fragments, values are bound
                "SELECT COUNT(*) FROM rate_limit_violations v "
                "LEFT JOIN users u ON u.id = v.user_id WHERE " + clause
            ),
            params,
        ).scalar()
        rows = connection.execute(
            text(  # nosec B608 - clause is built from fixed fragments, values are bound
                "SELECT v.id, v.created_at, v.address, u.email, v.route, v.rule "
                "FROM rate_limit_violations v LEFT JOIN users u ON u.id = v.user_id WHERE "
                + clause + " ORDER BY v.created_at DESC, v.id DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": per_page, "off": (page - 1) * per_page},
        ).all()
    return {
        "rows": [
            {"id": r[0], "at": r[1], "address": r[2], "user": r[3], "route": r[4], "rule": r[5]}
            for r in rows
        ],
        "total": int(total or 0),
        "page": page,
        "per_page": per_page,
        "pages": max(1, -(-int(total or 0) // per_page)),
    }
