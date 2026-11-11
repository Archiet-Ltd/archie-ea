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


def _signed_in() -> bool:
    from flask import has_request_context
    from flask_login import current_user

    return has_request_context() and bool(current_user.is_authenticated)


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
    soon_items.append(link({"label": "Early access", "href": "/early-access"}))
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


def _released_entries() -> list[dict[str, Any]]:
    """The coming-soon entries whose working screen is released: the one check every surface reads.

    The menus, the footer, the home page and the features page all promote an entry on this list and
    nothing else, signed in or not; a visitor and a signed-in person differ only in where its link goes.
    """
    released = []
    for entry in raw_registry()["product"]["coming_soon"]["items"]:
        live = entry.get("signed_in")
        # the same wave check the menu applies: a working screen that is not released yet is not offered
        if live and live.get("href") and wave_released({**entry, "href": live["href"]}):
            released.append(entry)
    return released


def signed_in_target(href: str) -> str:
    """Where a link to a released module goes: the working screen for a signed-in person, its own page for a visitor.

    The features page reads the same entries as the menus and the footer, so one module never opens
    "not built yet" from one place and its working screen from another.
    """
    if _signed_in():
        for entry in _released_entries():
            if entry["href"] == href:
                return entry["signed_in"]["href"]
    return href


def _drop_sentences_for_unreleased_screens(text: str) -> str:
    """Keep one of two paragraphs by the release check of a screen, the check the menu uses.

    A paragraph with id ``needs-<screen>`` stays only once that screen's release wave is released; one with
    id ``until-<screen>`` stays only while it is not; one with id ``visitor-needs-<screen>`` stays only once
    it is released and only for a person who is not signed in. ``simulations`` stands for the screen at
    ``/simulations``. A section can then say early access or available, never both, and a signed-in person
    is never told to sign in.
    """
    import re

    def keep_or_drop(match: "re.Match[str]") -> str:
        kind = match.group(1)
        released = wave_released({"href": "/" + match.group(2)})
        wanted = {
            "needs": released,
            "until": not released,
            "visitor-needs": released and not _signed_in(),
        }[kind]
        return "<p>%s</p>\n" % match.group(3) if wanted else ""

    return re.sub(r'<p id="(needs|until|visitor-needs)-([a-z0-9-]+)">(.*?)</p>\s*', keep_or_drop, text, flags=re.S)


def open_signed_in_links(body_html: str) -> str:
    """A page's hand-written links to coming-soon modules, pointed at where *signed_in_target* says."""
    # str.replace on the plain text: Markup.replace would escape the quotes in what it looks for
    text = str.__str__(body_html)
    text = _drop_sentences_for_unreleased_screens(text)
    opened = []
    for entry in _released_entries():
        target = signed_in_target(entry["href"])
        if target != entry["href"]:
            text = text.replace('href="%s"' % entry["href"], 'href="%s"' % target)
        opened.append(target)
    return type(body_html)(_list_opened_as_available(text, opened))


def _list_opened_as_available(text: str, opened: list[str]) -> str:
    """Move the Coming soon entries a signed-in person can already open into their own Available now list.

    A released module is listed there for a visitor as well as a signed-in person. The waiting-list
    sentence then stands only above what is not available; when nothing is left waiting, the Coming
    soon section goes with it.
    """
    import re

    marker = '<h2 id="coming-soon">'
    start = text.find(marker)
    if not opened or start < 0:
        return text
    head, section = text[:start], text[start:]
    end = section.find("</ul>")
    if end < 0:
        return text
    items = re.findall(r"<li>.*?</li>", section[:end], flags=re.S)
    live = [i for i in items if any('href="%s"' % t in i for t in opened)]
    waiting = [i for i in items if i not in live]
    if not live:
        return text
    tail = section[end + len("</ul>"):]
    intro = "You are signed in, so these open on the working screen." if _signed_in() else "These are available now."
    available = '<h2 id="available-now">Available now</h2>\n\n<p>%s</p>\n<ul>\n%s\n</ul>\n' % (intro, "\n".join(live))
    if not waiting:
        return head + available + tail
    keep = section[:end].split("<ul>")[0]
    return head + available + keep + "<ul>\n%s\n</ul>" % "\n".join(waiting) + tail


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
    solutions_soon = raw["solutions"]["coming_soon"]
    # a capability whose screen is released is listed as available, not as coming soon, for everyone;
    # a signed-in person's link opens the working screen, a visitor's its own page
    promoted: set[str] = set()
    for group in groups:
        for entry in _released_entries():
            live = entry["signed_in"]
            if live.get("group") == group["id"] and live.get("blurb"):
                opened = _visible([_item({**entry, "href": live["href"], "blurb": live["blurb"]})])
                if not _signed_in():
                    opened = [{**i, "href": entry["href"]} for i in opened]
                group["items"] += opened
                if opened:
                    promoted.add(entry["label"])
    soon = {**soon, "items": [i for i in soon["items"] if i["label"] not in promoted]}
    solutions_soon = {**solutions_soon, "items": [i for i in solutions_soon["items"] if i["label"] not in promoted]}

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
                "label": solutions_soon["label"],
                "items": [_item(i, coming_soon=True) for i in solutions_soon["items"]],
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


def registry_entry(label: str) -> dict[str, Any] | None:
    """One product entry by label, as the menus show it: in its menu group once released, else under coming soon.

    A page that names a module outside the menus (the home page) reads this, so it says "coming soon"
    exactly while the menu and the footer do.
    """
    reg = site_registry()
    for group in reg["product"]["groups"]:
        for item in group["items"]:
            if item["label"] == label:
                return item
    for item in reg["product"]["coming_soon"]["items"]:
        if item["label"] == label:
            return item
    return None


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
