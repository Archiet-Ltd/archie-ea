"""The wave switch: with RELEASE_WAVE=N the product answers only for the pages of waves 1 to N.

``wave_map.json`` (built by ``scripts/build_wave_map.py`` from the release plan's page-to-wave
map) gives every registered route rule a wave. With ``RELEASE_WAVE`` unset nothing here changes
any answer. With it set:

* a rule in a released wave answers as it always did;
* a rule of infrastructure (``A``), foundation (``W0``) or operations (``W6``) answers as it always
  did, to whoever its own permission checks allow (operations pages stay platform-administrator only);
* a retired address (``RT``) whose destination ships in a released wave redirects to it;
* every other rule, and any rule the map does not list, is closed: a GET for a page answers the
  early-access page for its capability; a write or a data call answers 404 with a plain JSON message.
  Nothing is deleted and nothing writes.

Navigation, the module directory, search, the site menus and the sitemap ask the same
questions (``is_released_endpoint``, ``is_released_path``) so they list only what opens.
"""

from __future__ import annotations

import functools
import json
import os
import re
from pathlib import Path
from typing import Any

MAP_PATH = Path(__file__).resolve().parent / "wave_map.json"
_PARAM = re.compile(r"<[^>]*>")
EARLY_ACCESS_PREFIX = "/early-access"
OPEN, REDIRECT, CLOSED = "open", "redirect", "closed"


@functools.lru_cache(maxsize=1)
def wave_map() -> dict[str, Any]:
    """The shipped map, read once."""
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


def configured_wave() -> int | None:
    """The released wave number, or None when the switch is off."""
    try:
        from flask import current_app, has_app_context

        value = current_app.config.get("RELEASE_WAVE") if has_app_context() else None
    except RuntimeError:
        value = None
    if value in (None, ""):
        value = os.environ.get("RELEASE_WAVE")
    if value in (None, ""):
        return None
    try:
        number = int(str(value).strip().lstrip("Ww"))
    except ValueError:
        # a switch that cannot be read closes the product rather than opening it
        return 0
    return max(number, 0)


def _wave_number(code: str) -> int | None:
    m = re.fullmatch(r"W(\d)", code or "")
    return int(m.group(1)) if m else None


def page_wave_code(value) -> str:
    """A content page's front-matter wave (3, "3", "W3", "EA") as a map code; anything else is "X" (closed)."""
    text = str(value).strip().upper() if value is not None else ""
    if re.fullmatch(r"[0-6]", text):
        return "W" + text
    if re.fullmatch(r"W[0-6]", text) or text in ("EA", "RT"):
        return text
    return "X"


def entry_for(rule: str, endpoint: str | None = None) -> list | None:
    """[wave, group, basis, to, ships] for a route rule, or None when the map does not list it."""
    rules = wave_map()["rules"]
    if endpoint is not None:
        hit = rules.get(f"endpoint:{endpoint}")
        if hit is not None:
            return hit
    return rules.get(_PARAM.sub("<x>", rule))


def state_for(entry: list | None, wave: int) -> str:
    """OPEN, REDIRECT or CLOSED for one map entry when waves 1..wave are released."""
    if entry is None:
        return CLOSED  # an unmapped rule never defaults to open
    code = entry[0]
    if code in wave_map()["pass_waves"]:
        return OPEN
    if code == "RT":
        ships = _wave_number(entry[4] or "")
        return REDIRECT if entry[3] and ships is not None and ships <= wave else CLOSED
    number = _wave_number(code)
    if number is not None and number <= wave:
        return OPEN
    return CLOSED


def is_released_rule(rule: str, endpoint: str | None = None) -> bool:
    wave = configured_wave()
    if wave is None:
        return True
    return state_for(entry_for(rule, endpoint), wave) == OPEN


def is_released_endpoint(endpoint: str) -> bool:
    """True when the switch is off or a page of this endpoint opens in the released waves."""
    wave = configured_wave()
    if wave is None:
        return True
    from flask import current_app, has_app_context

    if not has_app_context():
        return True
    cache = current_app.extensions.setdefault("release_waves_endpoints", {})
    key = (endpoint, wave)
    if key in cache:
        return cache[key]
    rules = list(current_app.url_map.iter_rules(endpoint)) if endpoint in current_app.view_functions else []
    get_rules = [r for r in rules if "GET" in (r.methods or ())] or rules
    result = any(state_for(entry_for(r.rule, endpoint), wave) == OPEN for r in get_rules) if get_rules else True
    cache[key] = result
    return result


def is_released_path(path: str) -> bool:
    """True when the switch is off, or the address opens in the released waves (or matches no route)."""
    wave = configured_wave()
    if wave is None:
        return True
    from flask import current_app, has_app_context
    from werkzeug.exceptions import HTTPException

    if not has_app_context():
        return True
    path = path.split("#", 1)[0].split("?", 1)[0]
    try:
        rule, _args = current_app.url_map.bind("").match(path, return_rule=True)
    except HTTPException:
        return True
    except Exception:  # noqa: BLE001 - an address the router cannot read is not ours to hide
        return True
    return state_for(entry_for(rule.rule, rule.endpoint), wave) == OPEN


def group_for(entry: list | None) -> tuple[str, dict]:
    """(group code, {"title", "blurb"}) for the early-access page of an entry."""
    groups = wave_map()["groups"]
    code = entry[1] if entry else "infra"
    return code, groups.get(code) or {"title": "This capability", "blurb": ""}


def code_slug(code: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", code.lower()).strip("-")


def group_by_slug(slug: str) -> tuple[str, dict] | None:
    for code, info in wave_map()["groups"].items():
        if code_slug(code) == slug:
            return code, info
    return None


def install_hook(app) -> None:
    """Install the request hook. Call before the extensions so it answers ahead of the CSRF check."""
    from app.release_waves.hook import enforce

    app.before_request(enforce)


def register_routes(app) -> None:
    """Register the early-access pages (after the rate limiter is installed)."""
    from app.release_waves.hook import point_closed_links_at_early_access
    from app.release_waves.routes import early_access_bp

    app.register_blueprint(early_access_bp)
    app.after_request(point_closed_links_at_early_access)
