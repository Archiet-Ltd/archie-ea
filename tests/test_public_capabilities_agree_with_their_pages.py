"""At the first release (RELEASE_WAVE=1), what the public site sells as working is what a signed-in person can open.

A buyer reads /, /features, /pricing, every module page and the early-access hub. This crawls each of them and
checks every claim against the product itself, by asking for the capability's own page as a signed-in person:

* a link the page sells as working today never leads to a waiting list;
* an item marked early access never names a capability whose page opens;
* a plan card, the plan comparison, the home plan table and the pricing structured data never include a capability
  whose page is closed.

Whether a page opens is read from the answer, not from the wave map: a closed page answers the early-access page or
hub in place of itself.
"""

from __future__ import annotations

import json
import re
import uuid
from urllib.parse import urlsplit

import pytest
from bs4 import BeautifulSoup

CRAWLED = ("/", "/features", "/pricing", "/early-access")

# What a plan line names, and the page that capability lives on. Kept here, apart from the code under test, so the
# check does not trust the same table it checks.
PLAN_CAPABILITIES = (
    (re.compile(r"single sign-on", re.I), "/admin/sso"),
    (re.compile(r"\bSAML\b"), "/admin/sso"),
    (re.compile(r"review[- ]board", re.I), "/arb/"),
    (re.compile(r"composer", re.I), "/archimate/composer"),
    (re.compile(r"open exchange", re.I), "/architecture/import/oef"),
    (re.compile(r"as of a date|changed between two dates", re.I), "/intelligence/history/as-of"),
    (re.compile(r"business model canvas", re.I), "/business-model/"),
    (re.compile(r"AI chat|every question", re.I), "/ai-chat/"),
    (re.compile(r"audit (?:log )?export", re.I), "/admin/audit-log"),
    (re.compile(r"spreadsheet|file import|import from files", re.I), "/batch-import/"),
)

EARLY_MARK = re.compile(r"early access|waiting list", re.I)


@pytest.fixture
def first_release(app, db_session, make_org, login_as, monkeypatch):
    """The app as production runs the first release, a signed-out visitor, and a signed-in person of an organisation."""
    from app.models.user import User

    monkeypatch.setitem(app.config, "RELEASE_WAVE", "1")
    monkeypatch.setenv("RELEASE_WAVE", "1")
    org = make_org("first-release")
    user = User(email=f"first-release-{uuid.uuid4().hex[:8]}@example.com", first_name="Fi", last_name="Rst",
                confirmed=True, organization_id=org.id, enterprise_role="enterprise_architect")
    db_session.add(user)
    db_session.flush()
    visitor = app.test_client()
    member = app.test_client()

    def opens(path: str) -> bool:
        """True when the signed-in person reaches the page itself, not the early-access page or hub in its place."""
        login_as(member, user)
        reply = member.get(re.sub(r"<[^>]+>", "1", path))
        if reply.status_code == 404 and (reply.is_json and "early_access" in (reply.get_json() or {})):
            return False
        html = reply.get_data(as_text=True)
        return 'data-testid="early-access-page"' not in html and 'data-testid="early-access-hub"' not in html

    return visitor, opens


def _main(html: str):
    soup = BeautifulSoup(html, "html.parser")
    return soup.find("main") or soup


def _block_text(link) -> str:
    block = link.find_parent(["li", "p", "td", "tr", "dd"]) or link
    return " ".join(block.get_text(" ").split())


def _in_early_section(link) -> bool:
    heading = link.find_previous(["h2"])
    return heading is not None and heading.get("id") == "coming-soon"


def _module_pages():
    from app.services.public_pages import MERGED_PAGES, load_all_pages

    return [p for p in load_all_pages() if p.family == "module" and p.url not in MERGED_PAGES]


def _crawl(visitor):
    pages = {path: visitor.get(path, follow_redirects=True) for path in CRAWLED}
    for page in _module_pages():
        pages[page.url] = visitor.get(page.url, follow_redirects=True)
    for path, reply in pages.items():
        assert reply.status_code == 200, (path, reply.status_code)
    return {path: reply.get_data(as_text=True) for path, reply in pages.items()}


def _group_main(slug: str) -> str | None:
    """The main page of the capability an early-access address names, from the one place that names it."""
    from app.release_waves import group_by_slug, wave_map

    found = group_by_slug(slug)
    if found is None:
        return None
    return (wave_map()["groups"].get(found[0]) or {}).get("main")


def _page_route(page) -> str | None:
    """The address of the screen a module page describes, from the endpoint it names."""
    from flask import current_app

    endpoint = page.front_matter.get("endpoint")
    if endpoint not in current_app.view_functions:
        return None
    rules = [r for r in current_app.url_map.iter_rules(endpoint) if "GET" in (r.methods or ())]
    return rules[0].rule if rules else None


def test_every_capability_a_public_page_sells_as_live_opens_and_every_early_access_one_does_not(app, first_release):
    visitor, opens = first_release
    with app.app_context():
        crawled = _crawl(visitor)
        modules = {p.url: p for p in _module_pages()}
        wrong: list[str] = []
        landed: dict[str, str] = {}

        def lands_on_waiting_list(href: str) -> bool:
            if href not in landed:
                reply = visitor.get(href, follow_redirects=True)
                html = reply.get_data(as_text=True)
                final = reply.request.path
                landed[href] = "waiting" if (final.startswith("/early-access") or 'data-testid="early-access-page"' in html
                                             or 'data-testid="early-access-hub"' in html) else "page"
            return landed[href] == "waiting"

        for path, html in crawled.items():
            main = _main(html)
            waiting = 'data-testid="early-access-page"' in html
            # the hub and a waiting-list page are early access throughout: each link on them is about what is coming
            hub = path == "/early-access" or waiting
            module = modules.get(path)
            if module is not None and waiting:
                route = _page_route(module)
                if route and opens(route):
                    wrong.append(f"{path} offers a waiting list, but its screen {route} opens")
            for link in main.find_all("a", href=True):
                href = link["href"]
                parts = urlsplit(href)
                if parts.scheme or parts.netloc or not parts.path.startswith("/") or parts.path.startswith("/static/"):
                    continue
                text = _block_text(link)
                early = hub or _in_early_section(link) or bool(EARLY_MARK.search(text)) or bool(EARLY_MARK.search(link.get_text()))
                if early:
                    if parts.path.startswith("/early-access/"):
                        main_route = _group_main(parts.path.rsplit("/", 1)[-1])
                        if main_route and opens(main_route):
                            wrong.append(f"{path}: early access {text[:70]!r} names a capability whose page {main_route} opens")
                    continue
                if parts.path.startswith("/early-access"):
                    wrong.append(f"{path}: {text[:70]!r} is sold as working but links to {href}")
                elif lands_on_waiting_list(parts.path):
                    wrong.append(f"{path}: {text[:70]!r} is sold as working but {href} lands on a waiting list")
        assert not wrong, "\n".join(sorted(set(wrong)))


def _plan_texts(crawled: dict) -> list[tuple[str, str]]:
    """Every line a plan table or the structured data includes: (where, text)."""
    texts: list[tuple[str, str]] = []
    pricing = BeautifulSoup(crawled["/pricing"], "html.parser")
    for card in pricing.select('[data-testid^="plan-card-"]'):
        texts.extend((card["data-testid"], " ".join(li.get_text(" ").split())) for li in card.select("li"))
    compare = pricing.select_one('[data-testid="pricing-compare"]')
    assert compare is not None
    texts.extend(("plan comparison", " ".join(th.get_text(" ").split())) for th in compare.select('tbody th[scope="row"]'))
    home = BeautifulSoup(crawled["/"], "html.parser")
    table = home.select_one('[aria-label="Pricing plans table, scrollable"]')
    assert table is not None
    for row in table.select("tbody tr"):
        cells = row.find_all("td")
        texts.append(("home plan table", " ".join(cells[-1].get_text(" ").split())))
    for where, soup in (("pricing structured data", pricing), ("home structured data", home)):
        for script in soup.find_all("script", type="application/ld+json"):
            stack = [json.loads(script.string or "null")]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    if node.get("@type") in ("Offer", "AggregateOffer") and node.get("description"):
                        texts.append((where, node["description"]))
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)
    return texts


def test_no_plan_table_or_structured_data_includes_a_capability_whose_page_is_closed(app, first_release):
    visitor, opens = first_release
    with app.app_context():
        crawled = {path: visitor.get(path).get_data(as_text=True) for path in ("/", "/pricing")}
        texts = _plan_texts(crawled)
        assert len([t for w, t in texts if w.startswith("plan-card")]) >= 10
        assert [t for w, t in texts if w == "pricing structured data"], "the pricing page carries no plan offers"
        wrong = []
        for where, text in texts:
            if EARLY_MARK.search(text):
                wrong.append(f"{where}: {text[:80]!r} is an early-access line inside a plan table")
                continue
            for pattern, route in PLAN_CAPABILITIES:
                if pattern.search(text) and not opens(route):
                    wrong.append(f"{where}: {text[:80]!r} includes {pattern.pattern!r}, whose page {route} is closed")
        assert not wrong, "\n".join(sorted(set(wrong)))
        # what the plans include is real: the capabilities that open are still named
        joined = " ".join(t for _w, t in texts)
        for pattern, route in PLAN_CAPABILITIES:
            if route in ("/ai-chat/", "/business-model/", "/batch-import/", "/admin/audit-log"):
                assert opens(route) and pattern.search(joined), (pattern.pattern, route)
