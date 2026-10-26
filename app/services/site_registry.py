"""The one registry behind entelim.org's navbar, menus, footer and roles hub.

``content/site/registry.yml`` is the single list of public links. The navbar,
its Product, Solutions, Roles, Compare and Resources menus, the footer and the
roles catalogue on /roles all read :func:`site_registry`, so a label cannot
differ between the navbar and the footer and a link cannot exist in one and
not the other. No template holds a link list.

The registry carries no page copy, only what a menu row needs: label, one-line
description, icon and where it links. Where an item's own page is not built
yet, ``href`` is the existing page that shows it and ``own_page`` names the
page still to be written (:func:`items_needing_own_page`).
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

import yaml

REGISTRY_PATH = Path(__file__).resolve().parent.parent.parent / "content" / "site" / "registry.yml"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")


@functools.lru_cache(maxsize=4)
def _raw_registry(mtime_ns: int) -> dict[str, Any]:
    with REGISTRY_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def raw_registry() -> dict[str, Any]:
    """The registry file as written, cached until the file changes."""
    return _raw_registry(REGISTRY_PATH.stat().st_mtime_ns)


def _path_of(href: str) -> str:
    return href.split("#", 1)[0]


def is_live_page(href: str) -> bool:
    """True when *href* is a published page: in the feed (not held, not merged)
    or one of the hubs that exist only while they list something."""
    from app.services.public_pages import HELD_PAGE_URLS, MERGED_PAGES, _pages_by_url

    path = _path_of(href)
    if path in MERGED_PAGES or path in HELD_PAGE_URLS:
        return False
    pages = _pages_by_url()  # cached per process until a content file changes
    page = pages.get(path)
    if page is not None:
        return not page.is_held
    if path in ("/connectors", "/roles"):
        family = "connector" if path == "/connectors" else "role"
        return any(p.family == family and not p.is_held and p.url not in MERGED_PAGES for p in pages.values())
    # A route-built page (/pricing, /vs, /use-cases, /features ...) with no
    # content file of its own is live when the route answers.
    return True


def _item(raw: dict[str, Any], *, coming_soon: bool = False) -> dict[str, Any]:
    item = dict(raw)
    item["id"] = slugify(raw["label"])
    item["blurb"] = raw.get("blurb") or ""
    item["coming_soon"] = coming_soon
    item["has_own_page"] = not raw.get("own_page")
    return item


def wave_released(item: dict[str, Any]) -> bool:
    """True when the wave switch is off, or the item's page opens in a released wave.

    An item may name its wave (``wave: W3``); otherwise the wave is that of the page its ``href`` opens
    (a content page by the screen it describes, any other address by the route it matches)."""
    from app.release_waves import OPEN, configured_wave, is_released_path, state_for

    wave = configured_wave()
    if wave is None:
        return True
    code = item.get("wave")
    if code:
        return state_for([str(code), "", "page", None, None], wave) == OPEN
    path = _path_of(item["href"])
    from app.services.public_pages import _pages_by_url

    page = _pages_by_url().get(path)
    if page is not None and page.wave_closed:
        return False
    return is_released_path(path)


def _visible(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shown = []
    for item in items:
        if item.get("shown_when") == "live_page" and not is_live_page(item["href"]):
            continue
        if not wave_released(item):
            continue
        shown.append(item)
    return shown


def _resolve_actions(raw: dict[str, Any]) -> list[dict[str, Any]]:
    from flask import url_for

    actions = []
    for action in raw.get("actions", []):
        resolved = dict(action)
        if action.get("endpoint"):
            resolved["href"] = url_for(action["endpoint"])
        actions.append(resolved)
    return actions


def _roles(raw: dict[str, Any]) -> dict[str, Any]:
    section = raw["roles"]
    families = []
    for family in section["families"]:
        family_href = f"/roles#{family['id']}"
        roles = []
        for role in family["roles"]:
            role_id = slugify(role["label"])
            has_page = bool(role.get("href")) and wave_released(role)
            roles.append({
                "id": role_id,
                "label": role["label"],
                "href": role["href"] if has_page else f"/roles#{role_id}",
                "has_page": has_page,
                "card_title": role.get("card_title", role["label"]),
                "card_blurb": role.get("card_blurb", ""),
            })
        with_page: list[dict[str, Any]] = []
        for role in roles:
            if role["has_page"] and all(role["href"] != other["href"] for other in with_page):
                with_page.append(role)
        families.append({
            "id": family["id"],
            "label": family["label"],
            "href": family_href,
            "roles": roles,
            "shown": with_page,
            "has_page": bool(with_page),
        })
    return {
        "label": section["label"],
        "all_link": section["all_link"],
        "families": families,
        "menu_families": [f for f in families if f["has_page"]],
        "cards": _role_cards(families),
        "count": sum(len(f["roles"]) for f in families),
    }


def _role_cards(families: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The role pages as cards for the /roles hub: one card per page, grouped by
    family, the startup family first. A page shared by two roles is one card (see the family's shown list)."""
    ordered = sorted(families, key=lambda f: f["id"] != "startups-and-scale-ups")
    groups = []
    for family in ordered:
        cards = []
        for role in family["shown"]:
            cards.append({"title": role["card_title"], "blurb": role["card_blurb"], "href": role["href"]})
        if cards:
            groups.append({"id": family["id"], "label": family["label"], "cards": cards})
    return groups


def _footer(reg: dict[str, Any]) -> dict[str, Any]:
    """One set of columns: Product, Solutions, Roles, Compare, Resources, Company
    and Legal, each a plain list of live links. Everything not available yet sits
    in one labelled group, Coming soon, and nothing live is in it."""

    def link(entry: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {"label": entry["label"], "href": entry["href"], **extra}

    product = [link(i) for g in reg["product"]["groups"] for i in g["items"]]
    product.append(link(reg["product"]["all_link"]))

    solutions = [link(i) for i in reg["solutions"]["items"]]
    roles = [link(f) for f in reg["roles"]["menu_families"]] + [link(reg["roles"]["all_link"])]
    compare = [link(i) for i in reg["compare"]["items"]] + [link(reg["compare"]["all_link"])]

    resources_ids = ("learn", "services")
    resources = [link(i) for g in reg["resources"]["groups"] if g["id"] in resources_ids for i in g["items"]]
    company = [link(i) for g in reg["resources"]["groups"] if g["id"] not in resources_ids for i in g["items"]]
    company += [link(reg["pricing"]["items"][0])]

    from app.services.legal_pages import legal_links

    legal = [{"label": item["label"], "href": item["href"], "legal_slug": item["slug"]}
             for item in legal_links()]
    legal += [link(i) for i in reg["legal"].get("extra_items", [])]

    soon_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in reg["product"]["coming_soon"]["items"] + reg["solutions"]["coming_soon"]["items"]:
        if entry["label"] not in seen:
            seen.add(entry["label"])
            soon_items.append(link(entry, coming_soon=True))
    soon_items.append(link(reg["product"]["coming_soon"]["all_link"]))

    columns = [
        {"id": "product", "label": "Product", "items": product},
        {"id": "solutions", "label": reg["solutions"]["label"], "items": solutions},
        {"id": "roles", "label": reg["roles"]["label"], "items": roles},
        {"id": "compare", "label": "Compare", "items": compare},
        {"id": "resources", "label": "Resources", "items": resources},
        {"id": "company", "label": "Company", "items": company},
        {"id": "legal", "label": reg["legal"]["label"], "items": legal},
        {"id": "coming-soon", "label": "Coming soon", "items": soon_items},
    ]
    return {"rows": [{"id": "site-map", "label": "Site map", "columns": [c for c in columns if c["items"]]}]}


def site_registry() -> dict[str, Any]:
    """The registry resolved for rendering: menus, footer rows and the roles
    catalogue. Needs a request context (account endpoints are resolved)."""
    raw = raw_registry()

    product = raw["product"]
    groups = [
        {"id": g["id"], "label": g["label"], "items": _visible([_item(i) for i in g["items"]])}
        for g in product["groups"]
    ]
    from app.release_waves import configured_wave

    if configured_wave() is not None:
        # a menu group whose every page opens in a later wave is left out, heading and all
        groups = [g for g in groups if g["items"]]
    by_id = {g["id"]: g for g in groups}
    columns = [[by_id[gid] for gid in column if gid in by_id] for column in product["columns"]]
    if configured_wave() is not None:
        columns = [column for column in columns if column]
    soon = product["coming_soon"]

    reg: dict[str, Any] = {
        "nav": list(raw["nav"]),
        "actions": _resolve_actions(raw),
        "product": {
            "groups": groups,
            "columns": columns,
            "all_link": product["all_link"],
            "coming_soon": {
                "label": soon["label"],
                "items": [_item(i, coming_soon=True) for i in soon["items"]],
                "all_link": soon["all_link"],
            },
        },
        "solutions": {
            "label": raw["solutions"]["label"],
            "items": _visible([_item(i) for i in raw["solutions"]["items"]]),
            "coming_soon": {
                "label": raw["solutions"]["coming_soon"]["label"],
                "items": [_item(i, coming_soon=True) for i in raw["solutions"]["coming_soon"]["items"]],
            },
        },
        "roles": _roles(raw),
        "compare": {
            "label": raw["compare"]["label"],
            "all_link": raw["compare"]["all_link"],
            "items": _visible([_item(i) for i in raw["compare"]["items"]]),
        },
        "resources": {"groups": [
            {"id": g["id"], "label": g["label"], "items": _visible([_item(i) for i in g["items"]])}
            for g in raw["resources"]["groups"]
        ]},
        "pricing": raw["pricing"],
        "legal": raw["legal"],
    }
    reg["resources"]["groups"] = [g for g in reg["resources"]["groups"] if g["items"]]
    reg["footer"] = _footer(reg)
    return reg


def registry_links(reg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every link in the navbar and the footer, flat, for tests and the gate:
    ``{label, href, where}`` where ``where`` is ``navbar`` or ``footer``."""
    reg = reg or site_registry()
    out: list[dict[str, Any]] = []

    def add(entry: dict[str, Any], where: str) -> None:
        out.append({"label": entry["label"], "href": entry["href"], "where": where})

    for entry in reg["nav"]:
        if entry["kind"] == "link":
            add(entry, "navbar")
    for action in reg["actions"]:
        add(action, "navbar")
    add(reg["product"]["all_link"], "navbar")
    for group in reg["product"]["groups"]:
        for item in group["items"]:
            add(item, "navbar")
    for item in reg["product"]["coming_soon"]["items"]:
        add(item, "navbar")
    add(reg["product"]["coming_soon"]["all_link"], "navbar")
    for item in reg["solutions"]["items"] + reg["solutions"]["coming_soon"]["items"]:
        add(item, "navbar")
    for family in reg["roles"]["menu_families"]:
        add(family, "navbar")
        for role in family["shown"]:
            add(role, "navbar")
    add(reg["roles"]["all_link"], "navbar")
    for item in reg["compare"]["items"]:
        add(item, "navbar")
    add(reg["compare"]["all_link"], "navbar")
    for group in reg["resources"]["groups"]:
        for item in group["items"]:
            add(item, "navbar")
    for row in reg["footer"]["rows"]:
        for column in row["columns"]:
            for item in column["items"]:
                add(item, "footer")
    return out


def items_needing_own_page(reg: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Menu items that link to a page that shows them but is not their own
    page yet, for the content writer: ``{label, shown_on, own_page}``."""
    raw = raw_registry()
    wanted: list[dict[str, str]] = []
    for group in raw["product"]["groups"]:
        for item in group["items"]:
            if item.get("own_page"):
                wanted.append({"label": item["label"], "shown_on": item["href"], "own_page": item["own_page"]})
    for item in raw["solutions"]["items"]:
        if item.get("own_page"):
            wanted.append({"label": item["label"], "shown_on": item["href"], "own_page": item["own_page"]})
    for family in raw["roles"]["families"]:
        for role in family["roles"]:
            if not role.get("href"):
                wanted.append({"label": role["label"], "shown_on": f"/roles#{slugify(role['label'])}",
                               "own_page": f"/roles/{slugify(role['label'])}"})
    return wanted
