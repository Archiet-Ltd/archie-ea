"""The request hook of the wave switch: one function, answers ahead of every other check."""

from __future__ import annotations

import html as htmllib
import re
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode

from flask import g, jsonify, make_response, redirect, render_template, request, url_for

from app.release_waves import (
    CLOSED,
    EARLY_ACCESS_PREFIX,
    REDIRECT,
    code_slug,
    configured_wave,
    early_access_address,
    entry_for,
    group_for,
    has_page,
    state_for,
)

_PLACEHOLDER = re.compile(r"<([^>]*)>")
_JSON_PREFIXES = ("/api/", "/mcp", "/oauth/token", "/webhooks/")


def redirect_target(entry: list, view_args: dict | None) -> str | None:
    """The destination of a retired address, with the record's own values filled in; None when it cannot be built."""
    to = entry[3] or ""
    if not to.startswith("/"):
        return None
    named = dict(view_args or {})
    values = list(named.values())
    names = _PLACEHOLDER.findall(to)
    out = to
    for index, name in enumerate(names):
        if name in named:
            replacement = named[name]
        elif len(names) == len(values):
            replacement = values[index]
        else:
            return None
        out = out.replace(f"<{name}>", str(replacement), 1)
    return out


def wants_json() -> bool:
    if request.method not in ("GET", "HEAD"):
        return True
    if request.path.startswith(_JSON_PREFIXES) or request.is_json:
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    return request.accept_mimetypes.best == "application/json"


def _rule_for_other_method():
    """The rule an address belongs to when the router refused the method (a POST to a page that only reads).

    A closed page answers every method the same way, so a write to it is 404, not 405."""
    from flask import current_app
    from werkzeug.exceptions import MethodNotAllowed

    if not isinstance(request.routing_exception, MethodNotAllowed):
        return None
    try:
        rule, _args = current_app.url_map.bind("").match(request.path, method="GET", return_rule=True)
    except Exception:  # noqa: BLE001
        return None
    return rule


def enforce():
    """Under RELEASE_WAVE, answer for a closed rule with its early-access page or a plain 404."""
    wave = configured_wave()
    if wave is None:
        return None
    if request.method == "OPTIONS":
        return None
    rule = request.url_rule
    if rule is None:
        rule = _rule_for_other_method()
    if rule is None:
        return None
    entry = entry_for(rule.rule, rule.endpoint)
    state = state_for(entry, wave)
    if state not in (CLOSED, REDIRECT):
        return None
    if state == REDIRECT and request.method in ("GET", "HEAD"):
        kind, address = follow_moves(request.path, wave)
        if kind != "cycle" and address != request.path:
            return redirect(merge_query(address, request.query_string.decode("utf-8", "replace")), code=302)
        # addresses that move into each other have no page to go to: this one answers its early-access page
    code, info = group_for(entry)
    slug = code_slug(code)
    if not has_page(info):
        # plumbing, a moved address or a capability that already opens has no page of its own: the hub says what is coming
        if wants_json():
            response = jsonify(error="early_access", message="This is not open yet.", early_access=EARLY_ACCESS_PREFIX)
            response.status_code = 404
            return response
        if not hasattr(g, "csp_nonce"):
            import secrets

            g.csp_nonce = secrets.token_urlsafe(32)
        from app.release_waves.routes import coming_entries

        out = make_response(render_template("public/early_access_hub.html", entries=coming_entries()))
        out.headers["X-Robots-Tag"] = "noindex"
        return out
    if wants_json():
        response = jsonify(
            error="early_access",
            message=f"{info['title']} is not open yet. Join the waiting list and we will tell you the day it opens.",
            early_access=f"/early-access/{slug}",
        )
        response.status_code = 404
        return response
    if not hasattr(g, "csp_nonce"):
        import secrets

        g.csp_nonce = secrets.token_urlsafe(32)
    out = make_response(render_template(
        "public/early_access.html",
        group_slug=slug,
        group=info,
        thanks=False,
        error=None,
        form_action=url_for("early_access.join", slug=slug),
    ))
    out.headers["X-Robots-Tag"] = "noindex"
    return out


def _resolve_once(path: str, wave: int):
    """One step: ("closed", early-access address), ("moved", destination) or None for an open or unknown address."""
    from flask import current_app
    from werkzeug.exceptions import HTTPException

    try:
        rule, args = current_app.url_map.bind("").match(path, return_rule=True)
    except HTTPException:
        # the address matches no route, or only the wrong method: not ours to rewrite.
        return None

    try:
        entry = entry_for(rule.rule, rule.endpoint)
        state = state_for(entry, wave)
        if state == REDIRECT:
            destination = redirect_target(entry, args)
            return ("moved", destination) if destination else None
        if state == CLOSED:
            code, info = group_for(entry)
            return ("closed", early_access_address(code, info))
        return None
    except Exception:
        # The router matched a real rule but the wave map cannot read it: a wrong mapping, not an
        # absent one. Surface it in testing so a bad map entry fails the build instead of quietly
        # leaving the link exactly as written; outside testing, log and degrade rather than take
        # the whole page down over one link.
        if current_app.testing:
            raise
        current_app.logger.exception("release_waves: wrong mapping for %r (endpoint %r)", rule.rule, rule.endpoint)
        return None


def follow_moves(path: str, wave: int) -> tuple[str, str]:
    """Where an address leads once every move is followed: (kind, address).

    kind is "open" (the page the moves end on, or the address itself), "closed" (an early-access address) or
    "cycle" (addresses that move into each other; the address is then the early-access page of the last one
    reached, which always opens). Followed one step at a time with a visited set."""
    seen = {path}
    current = path
    while True:
        step = _resolve_once(current, wave)
        if step is None:
            return "open", current
        kind, destination = step
        if kind == "closed":
            return "closed", destination
        if destination in seen:
            closed = _early_access_of(current)
            return "cycle", closed or current
        seen.add(destination)
        current = destination


def _early_access_of(path: str) -> str | None:
    from flask import current_app

    try:
        rule, _args = current_app.url_map.bind("").match(path, return_rule=True)
    except Exception:  # noqa: BLE001
        return None
    code, info = group_for(entry_for(rule.rule, rule.endpoint))
    return early_access_address(code, info)


_ANCHOR = re.compile(r"""(<a\b[^>]*?\bhref=)(["'])(/[^"'#?]*)([^"']*)\2""")


def early_access_target(path: str) -> str | None:
    """The early-access address for a closed page, or None when the address is open, unknown or not a page."""
    from flask import current_app

    wave = configured_wave()
    if wave is None or path.startswith(("/early-access", "/static/", "/api/", "/oauth", "/mcp", "/.well-known")):
        return None

    cache = current_app.extensions.get("release_waves_link_cache")
    if cache is None:

        @lru_cache(maxsize=4096)
        def get_target(path, wave):
            # A moved address is not closed: the request hook sends the person on. Point the link
            # straight at the page it ends on; addresses that lead back into each other end at an
            # early-access page (logged once, this result is cached).
            kind, address = follow_moves(path, wave)
            if kind == "cycle":
                current_app.logger.warning("release_waves: moved addresses lead back to themselves (from %r)", path)
            return address if address != path else None

        cache = get_target
        current_app.extensions["release_waves_link_cache"] = cache

    return cache(path, wave)


# Keys that choose which page or view a destination opens: the destination's value always stands.
ROUTING_KEYS = frozenset({"lens", "view", "tab"})


def _pairs(text: str) -> list[tuple[str, str]]:
    """The (key, value) pairs of a query string, decoded once; a pair with no value keeps an empty value."""
    return parse_qsl(text, keep_blank_values=True)


def merge_query(target: str, query: str, *, in_html: bool = False) -> str:
    """The destination with the link's own query added: one "?".

    The destination's routing keys (lens, view, tab) win over the link's; the link's other keys (q, page, filters)
    win over the destination's. With in_html the query comes from an HTML attribute: "&amp;" is read as "&" and
    the result is encoded once again when the link was written that way. Pairs are decoded once and rebuilt once; an empty value in the
    destination never overrides the link's value."""
    if not query:
        return target
    base, _, own = target.partition("?")
    extra = query[1:] if query.startswith("?") else query
    encoded = in_html and "&amp;" in extra
    if in_html:
        extra = htmllib.unescape(extra)
    fragment = ""
    if "#" in extra:
        extra, _, fragment = extra.partition("#")
        fragment = "#" + fragment
    own_pairs = _pairs(own)
    fixed = {k for k, _v in own_pairs} & ROUTING_KEYS
    given = [(k, v) for k, v in _pairs(extra) if k not in fixed]
    taken = {k for k, _v in given}
    kept = [(k, v) for k, v in own_pairs if k not in taken]
    joined = urlencode(kept + given)
    if encoded:
        joined = htmllib.escape(joined, quote=False)
    return f"{base}?{joined}{fragment}" if joined else f"{base}{fragment}"


def point_closed_links_at_early_access(response):
    """Under RELEASE_WAVE, a link to a page the wave closes leads to that capability's early-access page.

    Applied to every HTML page, so no template has to know which wave is released."""
    if configured_wave() is None or response.status_code != 200 or response.direct_passthrough:
        return response
    if response.mimetype != "text/html" or response.headers.get("Content-Encoding"):
        return response
    html = response.get_data(as_text=True)

    def swap(match):
        target = early_access_target(match.group(3))
        if target is None:
            return match.group(0)
        query = "" if target.startswith(EARLY_ACCESS_PREFIX) else match.group(4)
        return f"{match.group(1)}{match.group(2)}{merge_query(target, query, in_html=True)}{match.group(2)}"

    rewritten = _ANCHOR.sub(swap, html)
    if rewritten != html:
        response.set_data(rewritten)
    return response
