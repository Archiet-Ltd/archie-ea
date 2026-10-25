"""Navigation v3, rendered: what each person's sidebar shows, and where every link goes.

Signs in as each persona, opens the real Home page (layouts/admin_base.html), reads the
sidebar the page carries and then follows every link in it as that person. A link that
answers anything but 200 for the person it was shown to is a dead end and fails here.

The registry (app/navigation/registry.py) is the one source; tests/test_sidebar_budgets.py
pins its data, this file pins what reaches the screen.
"""

from __future__ import annotations

import re
import uuid
from html.parser import HTMLParser

import pytest

from app.utils.role_access import SIDEBAR_LINK_BUDGET
from scripts.check_sidebar_links import count_links as _real_link_count

pytestmark = pytest.mark.usefixtures("db_session")

MAIN = ["Home", "AI chat", "Systems", "Impact", "Changes", "Spend", "Compliance"]
SECONDARY = ["Integrations", "Reports", "Help"]


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid

    sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if sid:
            sess["_sid"] = sid

    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _person(app, db_session, make_org, role, label, *, admin=False, platform=False, org=None):
    """A signed-in client for one persona. ``admin`` makes them an administrator of their
    organisation (a self-serve owner is one); ``platform`` adds the cross-organisation flag."""
    from app.models.user import Permission, Role, User

    org = org or make_org(f"nav3-{label}")
    user = User(
        email=f"nav3-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Nav",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
        is_platform_admin=platform,
    )
    if admin or platform:
        Role.insert_roles()
        user.role = Role.query.filter(
            Role.permissions.op("&")(Permission.ADMINISTER) == Permission.ADMINISTER
        ).first()
        user.is_org_admin = True
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    _login(client, user.id)
    return client, org


_SIDEBAR_RE = re.compile(r'data-testid="sidebar".*?</aside>', re.DOTALL)


class _Links(HTMLParser):
    """Every <a> in the sidebar with its data-slot / data-nav-key, outside <template>s."""

    def __init__(self):
        super().__init__()
        self.links, self._template, self._open = [], 0, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "template":
            self._template += 1
        elif tag == "a" and not self._template:
            self._open = {"href": a.get("href"), "slot": a.get("data-slot"), "key": a.get("data-nav-key"),
                          "testid": a.get("data-testid"), "current": a.get("aria-current"), "text": ""}
            self.links.append(self._open)

    def handle_endtag(self, tag):
        if tag == "template":
            self._template -= 1
        elif tag == "a":
            self._open = None

    def handle_data(self, data):
        if self._open is not None:
            self._open["text"] += data


def _sidebar(client, path="/dashboard/overview"):
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} answered {resp.status_code}: {resp.get_data(as_text=True)[:800]}"
    html = resp.get_data(as_text=True)
    match = _SIDEBAR_RE.search(html)
    assert match, "no sidebar on the page"
    parser = _Links()
    parser.feed(match.group(0))
    return html, match.group(0), parser.links


def _entries(links):
    """Sidebar entries (main, Architecture and secondary) in screen order."""
    return [link for link in links if link["slot"] == "sidebar-item"]


def _labels(links):
    return [" ".join(link["text"].split()) for link in links]


PERSONAS = [
    # (role, label, admin, platform)
    ("platform_admin", "owner", True, False),          # a startup CTO who signed up
    ("enterprise_architect", "ea", False, False),
    ("solution_architect", "sa", False, False),
    ("arb_member", "arb", False, False),
    ("cto", "cto", False, False),
    ("business_architect", "ba", False, False),
    ("procurement", "proc", False, False),
    ("finance", "fin", False, False),
    ("portfolio_manager", "pm", False, False),
    ("security_architect", "sec", False, False),
    ("data_architect", "data", False, False),
    ("platform_admin", "padmin", True, True),          # holds the platform flag
]


@pytest.mark.parametrize("role,label,admin,platform", PERSONAS)
def test_main_entries_are_the_same_for_everyone_and_within_budget(app, db_session, make_org, role, label, admin, platform):
    client, _ = _person(app, db_session, make_org, role, label, admin=admin, platform=platform)
    html, sidebar_html, links = _sidebar(client)
    labels = _labels(_entries(links))
    assert labels[:7] == MAIN, labels
    assert labels[-3:] == SECONDARY, labels
    assert len(MAIN) <= 10
    assert _real_link_count(sidebar_html) <= SIDEBAR_LINK_BUDGET
    # One search box on the page, in the sidebar; no second search in the header.
    assert html.count('data-testid="sidebar-search"') == 1
    assert 'id="search-modal"' not in html and "Search navigation" not in html
    assert "All modules" not in sidebar_html
    assert "/intelligence/ask" not in html


def test_a_startup_cto_sees_exactly_ten_entries_and_the_connect_card(app, db_session, make_org):
    client, _ = _person(app, db_session, make_org, "platform_admin", "cto10", admin=True)
    _, sidebar_html, links = _sidebar(client)
    entries = _entries(links)
    assert _labels(entries) == MAIN + SECONDARY
    # Only the data-subject requests page, which an organisation administrator may open.
    assert _labels([link for link in links if link["slot"] == "sidebar-subitem"]) == ["Data Subject Requests"]
    assert "Architecture" not in sidebar_html
    assert 'data-testid="connect-source-card"' in sidebar_html
    assert "Connect GitHub or Jira to build your model" in sidebar_html
    button = next(link for link in links if link["testid"] == "connect-source-button")
    assert button["href"] == "/admin/connectors"


def test_the_connect_card_goes_once_a_source_is_connected(app, db_session, make_org):
    from app.models.connector_config import ConnectorConfig

    client, org = _person(app, db_session, make_org, "platform_admin", "connected", admin=True)
    db_session.add(ConnectorConfig(organization_id=org.id, connector_type="github", name="GitHub", config={}))
    db_session.flush()
    _, sidebar_html, links = _sidebar(client)
    assert 'data-testid="connect-source-card"' not in sidebar_html
    assert _labels(_entries(links)) == MAIN + SECONDARY


@pytest.mark.parametrize("role,label", [("enterprise_architect", "ea-arch"), ("solution_architect", "sa-arch"), ("arb_member", "arb-arch")])
def test_architecture_roles_get_one_extra_group(app, db_session, make_org, role, label):
    client, _ = _person(app, db_session, make_org, role, label)
    _, sidebar_html, links = _sidebar(client)
    labels = _labels(_entries(links))
    # No entry per framework (ADR-0017): frameworks are views on their job pages.
    assert labels == MAIN + ["Model", "Composer", "Review board", "Value streams"] + SECONDARY
    assert "Frameworks" not in labels and "Canvases" not in labels
    assert re.search(r'role="heading"[^>]*>\s*Architecture\s*<', sidebar_html)


@pytest.mark.parametrize("role,label,expected", [
    ("procurement", "proc-sub", ["Procurement", "Contracts", "Licences", "Renewals", "Spend analytics"]),
    ("finance", "fin-sub", ["Licences", "Spend analytics"]),
    ("portfolio_manager", "pm-sub", ["Procurement", "Contracts", "Licences", "Renewals", "Spend analytics"]),
])
def test_procurement_roles_get_their_pages_under_spend(app, db_session, make_org, role, label, expected):
    client, _ = _person(app, db_session, make_org, role, label)
    _, _, links = _sidebar(client)
    order = [link for link in links if link["slot"] in ("sidebar-item", "sidebar-subitem")]
    spend_at = _labels(order).index("Spend")
    subs = []
    for link in order[spend_at + 1:]:
        if link["slot"] != "sidebar-subitem":
            break
        subs.append(link)
    assert _labels(subs) == expected
    assert _labels(order)[spend_at + 1 + len(subs)] == "Compliance"


@pytest.mark.parametrize("role,label,admin,platform", PERSONAS)
def test_every_sidebar_link_opens_a_page_for_the_person_shown_it(app, db_session, make_org, role, label, admin, platform):
    """Follow every link the sidebar shows (entries, sub-items, the connect button, the
    user menu, the organisation menu) as the person it was shown to."""
    client, _ = _person(app, db_session, make_org, role, f"{label}-follow", admin=admin, platform=platform)
    _, _, links = _sidebar(client)
    targets = sorted({link["href"] for link in links if link["href"] and link["href"].startswith("/")
                      and not link["href"].startswith("/account/logout")})
    assert targets
    failures = {}
    for href in targets:
        resp = client.get(href, follow_redirects=True)
        if resp.status_code != 200:
            failures[href] = resp.status_code
            continue
        body = resp.get_data(as_text=True)
        # The page it lands on carries the same sidebar (a page-level name must not hide it).
        if 'data-testid="sidebar"' in body and 'data-nav-key="home"' not in body:
            failures[href] = "sidebar without its entries"
    assert failures == {}, f"{role}: links that do not open a page: {failures}"


def test_platform_admin_entry_opens_the_admin_area(app, db_session, make_org):
    client, _ = _person(app, db_session, make_org, "platform_admin", "pa-area", admin=True, platform=True)
    _, _, links = _sidebar(client)
    entry = next(link for link in links if link["testid"] == "user-menu-platform_admin")
    resp = client.get(entry["href"])
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    for tool in ("Organisations", "AI prompts", "Errors", "Feature flags", "Platform health", "Agent registry",
                 "Framework management"):
        assert tool in page, tool

    owner, _ = _person(app, db_session, make_org, "platform_admin", "owner-no-area", admin=True)
    _, owner_sidebar, _ = _sidebar(owner)
    assert "Platform admin" not in owner_sidebar
    assert owner.get(entry["href"]).status_code == 403


def test_the_entry_a_page_belongs_to_is_marked_current(app, db_session, make_org):
    client, _ = _person(app, db_session, make_org, "enterprise_architect", "ea-current")
    _, _, links = _sidebar(client, "/capability-map/")
    current = [link for link in _entries(links) if link["current"] == "page"]
    assert _labels(current) == ["Systems"]


def test_search_finds_pages_by_old_and_new_names(app, db_session, make_org):
    client, _ = _person(app, db_session, make_org, "platform_admin", "owner-search", admin=True)

    def pages(q):
        resp = client.get("/api/sidebar/search", query_string={"q": q})
        assert resp.status_code == 200
        return [(r["name"], r["url"].rstrip("/") or "/", r["zone"]) for r in resp.get_json()["results"]
                if r["type"] == "module"]

    assert pages("AI chat")[0][:2] == ("AI chat", "/ai-chat")
    assert pages("ask a question")[0][1] == "/ai-chat"
    assert ("Capability map", "/capability-map", "Systems") in pages("capability map")
    assert pages("gap analysis")[0][1] == "/enterprise/implementation/gap-analysis"
    assert pages("connect github")[0][1] == "/admin/connectors"
    assert not [p for p in pages("organisations") if p[1].startswith("/admin/organizations")]


def test_framework_management_and_config_reachable_from_the_admin_area(app, db_session, make_org):
    """Framework management and configuration live in the platform admin area and on the
    administration page (Settings); neither is a customer sidebar entry."""
    client, _ = _person(app, db_session, make_org, "platform_admin", "pa-fw", admin=True, platform=True)
    html = client.get("/admin/platform").get_data(as_text=True)
    assert 'href="/framework-management/"' in html
    assert 'href="/framework-config/' in html
    sa, _ = _person(app, db_session, make_org, "solution_architect", "sa-no-admin")
    assert sa.get("/admin/").status_code in (302, 403)
