"""Tests that request a route directly read the wave map.

With RELEASE_WAVE set, a route the wave closes answers the early-access answer: its page for a read, a plain 404
for a write or a data call. A test of that route's own behaviour then has one thing to prove, that answer, and
``answers_early_access`` proves it and tells the test it has nothing more to check at this wave. With the switch
off it does nothing, so every test is unchanged.
"""

from __future__ import annotations

import re

from app.release_waves import CLOSED, REDIRECT, configured_wave, entry_for, state_for

_PARAMETER = re.compile(r"<(?:int:|string:|path:)?[^>]*>")


def closed_at_wave(app, path: str, method: str = "GET") -> bool:
    """True when the switch is on and the wave closes the route this address belongs to."""
    wave = configured_wave()
    if wave is None:
        return False
    try:
        rule, _args = app.url_map.bind("").match(path.split("?", 1)[0], method=method, return_rule=True)
    except Exception:  # noqa: BLE001 - an address no route reads is not the wave's to answer
        return False
    return state_for(entry_for(rule.rule, rule.endpoint), wave) in (CLOSED, REDIRECT)


def address_of(rule: str, record_id: int = 1) -> str:
    """A request path for a route rule, every parameter filled with a record number."""
    return _PARAMETER.sub(str(record_id), rule)


def assert_early_access_answer(resp, method: str = "GET") -> None:
    """A page answers its early-access page; a write or a data call (any /api address) answers a plain 404."""
    if method.upper() in ("GET", "HEAD") and resp.status_code in (301, 302, 303, 307, 308):
        return  # a retired address whose destination has shipped
    if method.upper() in ("GET", "HEAD") and resp.status_code == 200:
        assert b'data-testid="early-access-page"' in resp.get_data(), "a closed page must answer its early-access page"
        return
    assert resp.status_code == 404, "a closed route must answer its early-access answer, got %s" % resp.status_code
    body = resp.get_json(silent=True) or {}
    assert body.get("error") == "early_access", body


def answers_early_access(client, path: str, method: str = "GET", **kwargs) -> bool:
    """When the wave closes ``path``, check the early-access answer and return True (nothing else to check)."""
    if not closed_at_wave(client.application, path, method):
        return False
    assert_early_access_answer(client.open(path, method=method, **kwargs), method)
    return True
