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

    def __init__(self, limit: int, window: str, retry_after: int = None, verdict=None):
        self.limit = limit
        self.window = window
        self.retry_after = retry_after
        self.verdict = verdict
        super().__init__(f"Rate limit exceeded: {limit} per {window}")


class RateLimiter:
    """Named-key limiter over the one shared counter store.

    There is no per-process state here: every count lives in the database table that backs the
    request limiter, so two workers, or a restart, cannot change what a key has used.
    """

    def check_rate_limit(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, Optional[int]]:
        """Count one action against ``key``. Returns (allowed, retry_after seconds or None)."""
        verdict = count_hit("key", key, limit, window_seconds, honour_switch=False)
        return verdict.allowed, (None if verdict.allowed else verdict.retry_after)

    def check_db_rate_limit(
        self, user_id: int, limit: int, window_seconds: int
    ) -> tuple[bool, Optional[int]]:
        """Check the user's recorded LLM interactions in the window (counted, not incremented)."""
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
                f"counting in the shared store instead: {e}"
            )
            return self.check_rate_limit(f"db_fallback:user:{user_id}", limit, window_seconds)

    def reset(self, key: str):
        """Reset rate limit for a key."""
        counter_storage().clear(_counter_key("key", key))


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

            verdict = count_hit("route", key, limit, window_seconds)
            if not verdict.allowed:
                logger.warning(f"Rate limit exceeded for {key}: {limit}/{window}")
                raise RateLimitExceeded(limit, window, verdict.retry_after, verdict)

            return f(*args, **kwargs)

        return wrapped

    return decorator


def check_pipeline_rate_limit(user_id: int) -> tuple[bool, Optional[str]]:
    """
    Check if user can execute another pipeline.

    Uses database-backed counting (LLMInteraction table) to survive server
    restarts. Falls back to the shared counter if the interaction table cannot be read.

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
    restarts. Falls back to the shared counter if the interaction table cannot be read.

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


# ---------------------------------------------------------------------------------------------
# Shared limit, allow and deny lists, violations
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
    "own_user": "That would block the user you are signed in as, so you would lose access.",
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


# ---- one counter for every limiter -----------------------------------------------------------
#
# The request limiter, the route decorators, the import and ADM limiters, the webhook receiver and
# the sign-up limits all count here, in DatabaseStorage. Nothing in the application keeps a count in
# process memory.

class Verdict:
    """What one counted action came to: allowed or not, and the allowance left in its window."""

    __slots__ = ("allowed", "limit", "count", "remaining", "retry_after", "reset_at")

    def __init__(self, allowed, limit, count, retry_after, reset_at):
        self.allowed = allowed
        self.limit = limit
        self.count = count
        self.remaining = max(0, limit - count)
        self.retry_after = retry_after
        self.reset_at = reset_at


_storage_override = {"storage": None}


def counter_storage():
    """The shared counter store. A test may hand each simulated worker its own connection pool."""
    return _storage_override["storage"] or DatabaseStorage()


def _counter_key(scope, identity):
    return "%s:%s" % (scope, identity)


def count_hit(scope, identity, limit, window_seconds, *, honour_switch=True):
    """Count one action for ``identity`` under ``scope`` in the shared store and say if it is allowed.

    The window starts at the first action and every worker sees the same count. A caller on the
    administrator's allow list, or a deployment with throttling switched off, is allowed and not
    counted. A store that cannot be reached lets the action through (and says so in the log) rather
    than turning a database fault into a refusal of everything.
    """
    now = time.time()
    if honour_switch:
        from flask import g

        if not current_app.config.get("RATE_LIMITING_ENABLED", True) or getattr(g, "_rate_limit_allowed", False):
            return Verdict(True, limit, 0, None, now + window_seconds)
    key = _counter_key(scope, identity)
    storage = counter_storage()
    try:
        count = storage.incr(key, window_seconds)
        reset_at = storage.get_expiry(key)
    except Exception as exc:
        logger.warning("rate limit store unavailable, not counting %s: %s", scope, exc)
        return Verdict(True, limit, 0, None, now + window_seconds)
    allowed = count <= limit
    retry_after = None if allowed else max(1, int(-(-(reset_at - now) // 1)))
    verdict = Verdict(allowed, limit, count, retry_after, reset_at)
    note_verdict(verdict)
    return verdict


def peek_count(scope, identity):
    """How many actions are counted for ``identity`` under ``scope`` right now, without adding one."""
    try:
        return counter_storage().get(_counter_key(scope, identity))
    except Exception as exc:
        logger.warning("rate limit store unavailable, not reading %s: %s", scope, exc)
        return 0


def note_verdict(verdict):
    """Remember the tightest verdict of this request so its response can report the allowance."""
    try:
        from flask import g

        held = getattr(g, "_counted_verdict", None)
        if held is None or verdict.remaining <= held.remaining:
            g._counted_verdict = verdict
    except RuntimeError:  # no request: nothing to report to
        pass


def apply_headers(response, verdict=None):
    """Put RateLimit-Limit / -Remaining / -Reset (and Retry-After on a refusal) on a response."""
    if verdict is None:
        from flask import g

        verdict = getattr(g, "_counted_verdict", None)
    if verdict is None:
        return response
    response.headers["RateLimit-Limit"] = str(verdict.limit)
    response.headers["RateLimit-Remaining"] = str(verdict.remaining)
    response.headers["RateLimit-Reset"] = str(int(verdict.reset_at))
    if not verdict.allowed and verdict.retry_after:
        response.headers["Retry-After"] = str(verdict.retry_after)
    return response


def client_address():
    """The connection's address. A forwarded-for header sent by the caller is never read."""
    return request.remote_addr or "unknown"


def wait_phrase(seconds):
    """A wait as a person says it, rounded up so it is never understated: '45 seconds', '12 minutes', '2 hours'."""
    seconds = max(1, int(-(-(seconds or 0) // 1)))
    if seconds < 90:
        n, unit = seconds, "second"
    elif seconds < 3600 - 30:
        n, unit = -(-seconds // 60), "minute"
    else:
        n, unit = -(-seconds // 3600), "hour"
    return "%d %s%s" % (n, unit, "" if n == 1 else "s")


# ---- allow and deny lists --------------------------------------------------------------------

_rules_lock = threading.Lock()
_rules_cache = {"version": None, "rules": (), "checked_at": 0.0}
# How long one worker trusts the entries it holds before asking the database whether they changed.
# A change made on this worker is seen at once; one made on another worker, within this many seconds.
RULES_CACHE_SECONDS = 2.0


def forget_rules():
    """Drop this worker's held entries, so its next request reads the list again."""
    with _rules_lock:
        _rules_cache["version"] = None
        _rules_cache["checked_at"] = 0.0


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
    entry always changes it. A worker asks for it at most once every RULES_CACHE_SECONDS, and a
    write made on this worker drops the held entries at once, so most requests cost no query.
    """
    if _rules_cache["version"] is not None and time.monotonic() - _rules_cache["checked_at"] < RULES_CACHE_SECONDS:
        return _rules_cache["rules"]
    with _engine().connect() as connection:
        version = tuple(
            connection.execute(text("SELECT COALESCE(MAX(id), 0), COUNT(*) FROM rate_limit_rules")).one()
        )
        if version == _rules_cache["version"]:
            _rules_cache["checked_at"] = time.monotonic()
            return _rules_cache["rules"]
        rows = connection.execute(text("SELECT action, kind, value, id FROM rate_limit_rules")).all()
    compiled = tuple(rule for rule in (_compile_rule(tuple(row)) for row in rows) if rule is not None)
    with _rules_lock:
        _rules_cache["version"] = version
        _rules_cache["rules"] = compiled
        _rules_cache["checked_at"] = time.monotonic()
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


def add_rule(action, kind, value, *, note=None, created_by_id=None, own_address=None, own_user_id=None):
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
        if action == "deny" and own_user_id is not None and user_id == own_user_id:
            raise RuleError(_ENTRY_ERRORS["own_user"])
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
        rule_id = connection.execute(
            text(
                "INSERT INTO rate_limit_rules (action, kind, value, note, created_by_id, created_at) "
                "VALUES (:a, :k, :v, :n, :by, :now) RETURNING id"
            ),
            {"a": action, "k": kind, "v": stored, "n": (note or "")[:255] or None,
             "by": created_by_id, "now": datetime.utcnow()},
        ).scalar()
    forget_rules()
    return rule_id


def remove_rule(rule_id) -> bool:
    with _engine().begin() as connection:
        removed = connection.execute(
            text("DELETE FROM rate_limit_rules WHERE id = :i"), {"i": rule_id}
        ).rowcount > 0
    forget_rules()
    return removed


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
