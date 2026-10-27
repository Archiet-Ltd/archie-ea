"""Navigation v3.1, rendered: what each person's sidebar shows, and where every link goes.

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

TOP = ["Home", "AI chat", "Approvals"]
WORK = ["Systems", "Impact", "Changes", "Spend", "Compliance"]
LAYERS = ["Strategy", "Business", "Application", "Technology", "Physical", "Motivation",
          "Implementation and migration", "Other"]
MODEL = LAYERS + ["Frameworks", "Composer"]
# Connections is for the architects who author the model and for administrators.
INTERFACES = {"business_architect", "data_architect", "enterprise_architect", "platform_admin", "security_architect",
              "solution_architect"}
CONNECTING = {"enterprise_architect", "business_architect", "solution_architect", "cto"}


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


def _forget_cached_user():
    """The test app context outlives a request: drop the person the last request cached."""
    from flask import g, has_app_context

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)


_SIDEBAR_RE = re.compile(r'data-testid="sidebar".*?</aside>', re.DOTALL)


class _Links(HTMLParser):
    """Every <a> and every collapsible item in the sidebar, in screen order, with the item a
    sub-link sits under. Links inside the template that holds a closed item's sub-items are
    included (they are the page the item opens); other templates are skipped."""

    def __init__(self):
        super().__init__()
        self.links, self._template, self._open, self._parent = [], 0, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "template":
            if a.get("data-nav-children") is None:
                self._template += 1
        elif tag == "button" and a.get("data-nav-toggle"):
            self._parent = a["data-nav-toggle"]
            self._open = {"href": None, "slot": "toggle", "key": a["data-nav-toggle"], "testid": None,
                          "current": None, "text": "", "expanded": a.get("aria-expanded"), "parent": None}
            self.links.append(self._open)
        elif tag == "a" and not self._template:
            self._open = {"href": a.get("href"), "slot": a.get("data-slot"), "key": a.get("data-nav-key"),
                          "testid": a.get("data-testid"), "current": a.get("aria-current"), "text": "",
                          "expanded": None,
                          "parent": self._parent if a.get("data-slot") == "sidebar-menu-sub-button" else None}
            self.links.append(self._open)

    def handle_endtag(self, tag):
        if tag == "template":
            if self._template:
                self._template -= 1
        elif tag in ("a", "button"):
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
    """Sidebar entries (links and collapsible items) in screen order."""
    return [link for link in links if link["slot"] in ("sidebar-menu-button", "toggle")]


def _subs(links, key):
    """The labels of the sub-items under one collapsible item."""
    return _labels([link for link in links if link["parent"] == key])


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


def _expected_entries(role, admin):
    connections = ["Connections"] if (admin or role in CONNECTING) else []
    return TOP + WORK + MODEL + connections + ["Reports", "Help"]


@pytest.mark.parametrize("role,label,admin,platform", PERSONAS)
def test_the_entries_are_the_same_for_everyone_and_within_budget(app, db_session, make_org, role, label, admin, platform):
    client, _ = _person(app, db_session, make_org, role, label, admin=admin, platform=platform)
    html, sidebar_html, links = _sidebar(client)
    assert _labels(_entries(links)) == _expected_entries(role, admin or platform)
    # The groups are named; the page carries only the links in view (closed items keep theirs inside a template).
    assert re.search(r'role="heading"[^>]*>\s*Work\s*<', sidebar_html)
    assert re.search(r'role="heading"[^>]*>\s*Model\s*<', sidebar_html)
    assert "Architecture" not in sidebar_html
    assert _real_link_count(sidebar_html) <= SIDEBAR_LINK_BUDGET
    # One search box on the page, in the sidebar; no second search in the header.
    assert html.count('data-testid="sidebar-search"') == 1
    assert 'id="search-modal"' not in html and "Search navigation" not in html
    assert "All modules" not in sidebar_html
    assert "/intelligence/ask" not in html


@pytest.mark.parametrize("role,label,admin,platform", PERSONAS)
def test_every_layer_holds_its_element_types_and_every_job_its_pages(app, db_session, make_org, role, label, admin, platform):
    client, _ = _person(app, db_session, make_org, role, f"{label}-subs", admin=admin, platform=platform)
    _, _, links = _sidebar(client)
    systems = ["Applications", "Portfolio", "Data register", "Interfaces", "Technology radar"]
    if role not in INTERFACES:
        systems.remove("Interfaces")  # the page's own guard: the data integration section
    assert _subs(links, "systems") == systems
    assert _subs(links, "impact") == ["Impact analysis", "Traceability"]
    assert _subs(links, "changes") == ["Gaps", "Solutions", "Options analysis", "Business cases", "Work packages", "Roadmap",
                                       "Programmes", "Canvases"]
    assert _subs(links, "compliance") == ["Controls", "Risks", "Decisions", "Review board", "Evidence"]
    assert _subs(links, "layer_strategy") == ["Capability", "Value stream", "Resource", "Course of action"]
    assert _subs(links, "layer_other") == ["Location", "Grouping"]
    assert len(_subs(links, "layer_business")) == 13
    assert len(_subs(links, "layer_application")) == 9
    assert len(_subs(links, "layer_technology")) == 13
    assert _subs(links, "layer_motivation")[:3] == ["Stakeholder", "Driver", "Assessment"]
    assert _subs(links, "layer_implementation") == ["Work package", "Deliverable", "Implementation event",
                                                    "Plateau", "Gap"]


def test_the_items_wait_closed_until_opened_and_only_the_page_s_own_item_is_open(app, db_session, make_org):
    client, _ = _person(app, db_session, make_org, "enterprise_architect", "ea-closed")
    _, home, links = _sidebar(client)
    assert {link["key"]: link["expanded"] for link in links if link["slot"] == "toggle"} == {
        key: "false" for key in ("systems", "impact", "changes", "spend", "compliance",
                                 *("layer_" + k for k in ("strategy", "business", "application", "technology",
                                                          "physical", "motivation", "implementation", "other")))}
    assert home.count("<template data-nav-children=") == 13
    _, _, on_roadmap = _sidebar(client, "/genome/roadmap/")
    open_items = [link["key"] for link in on_roadmap if link["slot"] == "toggle" and link["expanded"] == "true"]
    assert open_items == ["changes"]
    assert [link["text"].strip() for link in on_roadmap if link["parent"] == "changes" and link["current"] == "page"] == ["Roadmap"]


def test_what_a_person_opened_or_closed_is_kept(app, db_session, make_org):
    from app.models.user import User

    client, _ = _person(app, db_session, make_org, "enterprise_architect", "ea-kept")
    user = User.query.filter(User.email.like("nav3-ea-kept-%")).one()
    client.set_cookie("sidebar_items_%s" % user.id, "changes:0,layer_business:1")
    _, _, links = _sidebar(client, "/genome/roadmap/")
    state = {link["key"]: link["expanded"] for link in links if link["slot"] == "toggle"}
    assert state["changes"] == "false" and state["layer_business"] == "true"
    # Another person's cookie changes nothing.
    other, _ = _person(app, db_session, make_org, "enterprise_architect", "ea-other")
    other.set_cookie("sidebar_items_%s" % user.id, "systems:1")
    _, _, links = _sidebar(other)
    assert {link["expanded"] for link in links if link["slot"] == "toggle"} == {"false"}


def test_a_startup_cto_sees_the_work_and_model_groups_connections_and_the_connect_card(app, db_session, make_org):
    client, _ = _person(app, db_session, make_org, "platform_admin", "cto10", admin=True)
    _, sidebar_html, links = _sidebar(client)
    assert _labels(_entries(links)) == _expected_entries("platform_admin", True)
    assert 'data-testid="connect-source-card"' in sidebar_html
    assert "Connect GitHub or Jira to build your model" in sidebar_html
    button = next(link for link in links if link["testid"] == "connect-source-button")
    assert button["href"] == "/admin/connectors"
    connections = next(link for link in links if link["key"] == "connections")
    assert connections["href"] == "/admin/connectors"


def test_the_connect_card_goes_once_a_source_is_connected(app, db_session, make_org):
    from app.models.connector_config import ConnectorConfig

    client, org = _person(app, db_session, make_org, "platform_admin", "connected", admin=True)
    db_session.add(ConnectorConfig(organization_id=org.id, connector_type="github", name="GitHub", config={}))
    db_session.flush()
    _, sidebar_html, links = _sidebar(client)
    assert 'data-testid="connect-source-card"' not in sidebar_html
    assert _labels(_entries(links)) == _expected_entries("platform_admin", True)


@pytest.mark.parametrize("role,label,expected", [
    ("procurement", "proc-sub", ["Costs", "Vendors", "Vendor catalogue", "Contracts and licences", "Renewals", "Investments"]),
    ("portfolio_manager", "pm-sub", ["Costs", "Vendors", "Vendor catalogue", "Contracts and licences", "Renewals", "Investments"]),
    ("cto", "cto-sub", ["Costs", "Vendors", "Vendor catalogue", "Contracts and licences", "Renewals", "Investments"]),
    # Finance may open licences and spend, not contracts or renewals.
    ("finance", "fin-sub", ["Costs", "Vendors", "Vendor catalogue", "Licences", "Investments"]),
    ("enterprise_architect", "ea-sub", ["Investments"]),
])
def test_spend_holds_the_pages_the_person_may_open(app, db_session, make_org, role, label, expected):
    client, _ = _person(app, db_session, make_org, role, label)
    _, _, links = _sidebar(client)
    assert _subs(links, "spend") == expected


def test_approvals_shows_the_count_of_items_waiting_for_the_person(app, db_session, make_org):
    """The number is what the approvals inbox lists for the signed-in person: another person's
    pending proposals in the same organisation count, their own and another organisation's do not."""
    from datetime import datetime, timedelta

    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.models.user import User

    org = make_org("nav3-approvals")
    other_org = make_org("nav3-approvals-other")
    reviewer, _ = _person(app, db_session, make_org, "enterprise_architect", "reviewer", org=org)
    requester, _ = _person(app, db_session, make_org, "solution_architect", "requester", org=org)
    stranger, _ = _person(app, db_session, make_org, "solution_architect", "stranger", org=other_org)
    reviewer_id = User.query.filter(User.email.like("nav3-reviewer-%")).one().id
    requester_id = User.query.filter(User.email.like("nav3-requester-%")).one().id
    stranger_id = User.query.filter(User.email.like("nav3-stranger-%")).one().id

    def waiting(org_id, user_id, n):
        return AIChatCRUDApproval(
            user_id=user_id, organization_id=org_id, operation_type="create", entity_type="capability",
            original_command="add %s" % n, operation_payload='{"name": "Item %s"}' % n, summary="Add item %s" % n,
            status=ApprovalStatus.PENDING, expires_at=datetime.utcnow() + timedelta(days=1))

    _, none_html, none_links = _sidebar(reviewer)
    assert 'data-testid="nav-count-approvals"' not in none_html
    db_session.add_all([waiting(org.id, requester_id, 1), waiting(org.id, requester_id, 2),
                        waiting(org.id, reviewer_id, 3), waiting(other_org.id, stranger_id, 4)])
    db_session.flush()
    _, html, links = _sidebar(reviewer)
    assert re.search(r'data-testid="nav-count-approvals"[^>]*>\s*2\s*<', html), "the reviewer is waiting on two items"
    approvals = next(link for link in links if link["key"] == "approvals")
    page = reviewer.get(approvals["href"])
    assert page.status_code == 200
    # The requester sees their own item only when someone else asked: one from the stranger's org, none here.
    _forget_cached_user()
    _, req_html, _ = _sidebar(requester)
    assert re.search(r'data-testid="nav-count-approvals"[^>]*>\s*1\s*<', req_html), "the requester is waiting on the reviewer's item"


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


def test_the_entry_a_page_belongs_to_is_open_and_a_shared_page_is_current_under_both(app, db_session, make_org):
    client, _ = _person(app, db_session, make_org, "enterprise_architect", "ea-current")
    _, _, links = _sidebar(client, "/capability-map/")
    # The capability map is Capability under Model, Strategy as well as a page of Systems.
    assert sorted(link["key"] for link in links if link["slot"] == "toggle" and link["expanded"] == "true") == [
        "layer_strategy", "systems"]
    _, _, links = _sidebar(client, "/applications/")
    current = [(link["parent"], link["text"].strip()) for link in links if link["current"] == "page"]
    assert sorted(current) == [("layer_application", "Application component"), ("systems", "Applications")]
    open_items = [link["key"] for link in links if link["slot"] == "toggle" and link["expanded"] == "true"]
    assert sorted(open_items) == ["layer_application", "systems"]


def test_search_finds_pages_by_old_and_new_names(app, db_session, make_org):
    client, _ = _person(app, db_session, make_org, "platform_admin", "owner-search", admin=True)

    def pages(q):
        resp = client.get("/api/sidebar/search", query_string={"q": q})
        assert resp.status_code == 200
        return [(r["name"], r["url"].rstrip("/") or "/", r["zone"]) for r in resp.get_json()["results"]
                if r["type"] == "module"]

    assert pages("AI chat")[0][:2] == ("AI chat", "/ai-chat")
    assert pages("ask a question")[0][1] == "/ai-chat"
    assert ("Capability", "/capability-map", "Model, Strategy") in pages("capability map")
    # One page, two names: Application component finds Applications.
    assert pages("application component")[0] == ("Applications", "/applications", "Systems")
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
