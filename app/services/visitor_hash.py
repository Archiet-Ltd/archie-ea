"""Privacy-preserving, non-reversible visitor correlation.

Cookieless, consent-free analytics (what Plausible, Fathom and Simple
Analytics do) works because there is no persistent identifier anywhere --
PECR/GDPR consent is only required for a non-essential tracking identifier,
and a value that cannot be used to recognise the same visitor again
tomorrow, and is never itself stored, is not one.

A visitor's IP address and user agent are combined with a server-side
secret (``VISITOR_HASH_SECRET``, or the application's ``SECRET_KEY`` if
that is not set) and the current UTC calendar day, then hashed with
SHA-256. Only the resulting digest and the day are ever stored -- never the
IP address or user agent themselves, and the digest is never sent to the
client or to any third party.

Because the day is part of the hashed material, the same visitor gets a
different hash every day: the value can group a few events that happened
close together today (e.g. sign-up started -> sign-up completed, a plan
click -> an enquiry submitted a minute later), but it cannot be joined
across days to build a profile of anyone, and the hash cannot be reversed
back to an IP address or user agent. That is the whole design: nothing
reversible to a person is ever stored, so there is nothing here that needs
a visitor's consent.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

__all__ = ["compute_visitor_hash", "current_utc_day", "request_visitor_hash"]


def compute_visitor_hash(ip: str, user_agent: str, secret: str, day: date) -> str:
    """One-way SHA-256 digest of *ip*, *user_agent*, *secret* and *day*.

    Pure function, independent of Flask, so it can be tested directly: the
    same inputs always produce the same hash, and changing only *day*
    changes the hash completely (rotation).
    """
    material = "|".join([ip or "", user_agent or "", secret or "", day.isoformat()])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def current_utc_day() -> date:
    """Today's date in UTC -- the rotation boundary for the visitor hash."""
    return datetime.now(timezone.utc).date()


def _client_ip(request_obj) -> str:
    # Same idiom as app/middleware/audit_middleware.py's _get_request_context:
    # the first address in X-Forwarded-For (set by the load balancer) when
    # present, else the direct peer address. Truncated defensively -- this
    # value is only ever hashed, never stored raw.
    forwarded = request_obj.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request_obj.remote_addr or "")[:45]


def request_visitor_hash(request_obj, secret: str, day: date | None = None) -> str:
    """Today's (or *day*'s) visitor hash for a Flask request.

    Reads the request's IP and User-Agent, hashes them with *secret* and
    the day, and returns only the digest -- the caller never sees or stores
    the raw values.
    """
    ip = _client_ip(request_obj)
    user_agent = (request_obj.headers.get("User-Agent", "") or "")[:500]
    return compute_visitor_hash(ip, user_agent, secret, day or current_utc_day())
