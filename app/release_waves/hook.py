"""The request hook of the wave switch: one function, answers ahead of every other check."""

from __future__ import annotations

import re

from flask import g, jsonify, make_response, redirect, render_template, request, url_for

from app.release_waves import (
    CLOSED,
    REDIRECT,
    code_slug,
    configured_wave,
    entry_for,
    group_for,
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
        target = redirect_target(entry, request.view_args)
        if target:
            return redirect(target, code=302)
    code, info = group_for(entry)
    slug = code_slug(code)
    if wants_json():
        response = jsonify(
            error="early_access",
            message=f"{info['title']} opens in a later release. Join the waiting list to hear the day it opens.",
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
