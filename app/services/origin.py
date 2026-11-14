"""Origin resolution for the site's canonical address and request-scoped origin.

PUBLIC_BASE_URL is the one configured canonical origin (e.g. https://entelim.tech).
PUBLIC_BASE_URL_ALIASES is a comma-separated list of additional origins that the
OAuth/MCP/connector surfaces also answer on (e.g. https://entelim.org).

An origin is taken from the request ONLY when it matches the canonical origin or
a listed alias exactly (scheme and host). Any other Host value — including the
server's IP address name and a forged header — gets the canonical origin.
"""

from __future__ import annotations

from flask import current_app, has_app_context, request


def _parse_aliases(raw: str | None) -> list[str]:
    """Parse comma-separated aliases, stripping whitespace and trailing slashes."""
    if not raw:
        return []
    return [a.strip().rstrip("/") for a in raw.split(",") if a.strip()]


def canonical_origin() -> str:
    """The site's canonical origin.

    Returns PUBLIC_BASE_URL when set, otherwise defaults to https://entelim.tech.
    Never returns an empty string. Works inside or outside an application context.
    """
    if has_app_context():
        base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
        if base:
            return base
    return "https://entelim.tech"


def _allowed_origins() -> list[str]:
    """All origins this deployment answers on: canonical + aliases."""
    if has_app_context():
        aliases = _parse_aliases(current_app.config.get("PUBLIC_BASE_URL_ALIASES"))
    else:
        aliases = []
    return [canonical_origin()] + aliases


def request_origin() -> str:
    """The origin to use for the current request.

    Returns the request's own origin (scheme + host) ONLY when it exactly matches
    the canonical origin or one of the configured aliases. Otherwise returns the
    canonical origin. This prevents origin poisoning from forged Host headers or
    the server's IP address name.
    """
    allowed = _allowed_origins()
    # request.host includes port if non-standard; request.host_url is scheme+host+/
    # We need just the origin (scheme+host).
    req_origin = request.host_url.rstrip("/")
    if req_origin in allowed:
        return req_origin
    return canonical_origin()


def absolutise(path: str) -> str:
    """Build an absolute URL from a path using the canonical origin."""
    return f"{canonical_origin()}{path}"


def absolutise_for_request(path: str) -> str:
    """Build an absolute URL from a path using the request's origin (when allowed)."""
    return f"{request_origin()}{path}"