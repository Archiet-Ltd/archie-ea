"""Validate a user-supplied ``next`` URL before redirecting to it.

The login views accepted any value that did not start with ``//`` and did not
contain ``://``. That looks sufficient and is not: browsers normalise a
backslash to a forward slash in the authority position, so ``/\\evil.com``
passes both tests and Chrome and Firefox then navigate to ``//evil.com``.

That turns a post-authentication redirect into a credential-phishing chain: the
victim signs in to the real site and lands on the attacker's copy, having just
been trained that the flow is legitimate.

Validation here is allow-list shaped - the value must parse as a site-relative
path - rather than a list of the tricks known today.
"""

from __future__ import annotations

import posixpath
from urllib.parse import unquote, urlparse

__all__ = ["is_safe_next_url", "safe_next_url"]


def is_safe_next_url(candidate: str | None) -> bool:
    """True only for a same-origin, site-relative path such as ``/dashboard``."""
    if not candidate:
        return False

    # Leading/trailing whitespace and control characters are stripped by
    # browsers before parsing, so strip them before deciding too.
    cleaned = candidate.strip().strip("\x00\t\r\n")
    if cleaned != candidate.strip():
        return False
    if not cleaned:
        return False

    # A backslash anywhere means the browser may re-read the authority section.
    # There is no legitimate reason for one in a path we generated.
    if "\\" in cleaned:
        return False

    # Protocol-relative ("//host") and absolute ("https://host") are off-site.
    if cleaned.startswith("//"):
        return False

    parsed = urlparse(cleaned)
    if parsed.scheme or parsed.netloc:
        return False

    # The connector's OAuth area is reachable through a sign-in only at its authorise step. Decode
    # and normalise the path first so an encoded or dot-segment path cannot reach another endpoint.
    decoded = unquote(unquote(parsed.path))
    if "\\" in decoded:
        return False
    normalised = posixpath.normpath(decoded)
    if parsed.path.lower().startswith("/oauth") and (".." in decoded.split("/") or decoded != parsed.path):
        return False
    if normalised.lower().startswith("/oauth") and (
        normalised != "/oauth/authorize" or decoded != normalised
    ):
        return False

    # Anything that is not rooted could still be relative to the current path,
    # which is harmless, but accepting only rooted paths keeps the rule simple.
    return cleaned.startswith("/")


def safe_next_url(candidate: str | None, fallback: str) -> str:
    """Return *candidate* when it is a safe relative path, else *fallback*."""
    return candidate.strip() if is_safe_next_url(candidate) else fallback
