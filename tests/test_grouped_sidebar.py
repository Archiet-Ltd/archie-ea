"""T-nav-01: the grouped sidebar and its routing (navigation specification v1, sections 2 and 9, N-01 and N-02).

The rendered sidebar for Platform Admin and for Architect shows exactly the approved tree, in order;
every entry answers; the nine Tier 1 routes and their old sidebar URLs still answer; the page being
viewed is marked. The expected tree below is written out in full on purpose: it is the contract, not
a copy of the configuration under test.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from tests.test_sidebar_render import _make_logged_in_client

pytestmark = pytest.mark.usefixtures("db_session")

ROLES = ["platform_admin", "enterprise_architect", "solution_architect"]

# (group title, [level-1 entries]); a level-1 entry is ("link", label, href) or
# ("toggle", label, [(sub label, href), ...]).
EXPECTED_TREE = [
    ("Overview", [("link", "Dashboard Overview", "/dashboard/overview")]),
    ("Answers", [
        ("link", "Ask a question", "/intelligence/ask"),
        ("link", "Impact Analysis", "/strategic/impact-analysis"),
    ]),
    ("Model", [
        ("link", "Applications", "/applications/"),
        ("toggle", "Capability Map", [
            ("Map", "/capability-map/"),
            ("Hierarchy", "/capability-map/hierarchy"),
            ("Network", "/capability-map/network"),
            ("Simple view", "/capability-map/simple"),
            ("Application view", "/enterprise/capability-map/applications"),
            ("Capability list", "/enterprise/capability-map/capabilities"),
        ]),
        ("link", "Traceability Matrix", "/architecture/traceability"),
        ("link", "Vendors", "/applications/vendors"),
        ("toggle", "Import", [
            ("Import a model file", "/architecture/import/oef"),
            ("Import a spreadsheet", "/batch-import/"),
        ]),
    ]),
    ("Change", [
        ("link", "Gap Analysis", "/enterprise/implementation/gap-analysis"),
        ("link", "Work Packages", "/enterprise/implementation/work-packages"),
        ("link", "Roadmaps", "/capability-roadmap"),
        ("link", "Risk Register", "/risks/"),
        ("link", "Approval Inbox", "@approval_inbox"),
    ]),
    ("Catalogue", [
        ("link", "All catalogues", "/modules/"),
        ("link", "Layer overview", "/architecture/dashboard"),
        ("toggle", "Business architecture", [
            ("All business elements", "/architecture/dashboard?layer=business"),
            ("Actors", "/architecture/business/actors"),
            ("Processes", "/architecture/business/processes"),
            ("Services", "/architecture/business/services"),
        ]),
        ("toggle", "Application architecture", [
            ("All application elements", "/architecture/dashboard?layer=application"),
            ("Applications and components", "/architecture/application/components"),
            ("Application services", "/architecture/application/services"),
        ]),
        ("toggle", "Data architecture", [
            ("All data elements", "/architecture/application/data-objects"),
            ("Data objects", "/architecture/application/data-objects"),
        ]),
        ("toggle", "Technology architecture", [
            ("All technology elements", "/architecture/dashboard?layer=technology"),
            ("Nodes", "/architecture/technology/nodes"),
            ("Devices", "/architecture/technology/devices"),
            ("System software", "/architecture/technology/system-software"),
        ]),
        ("toggle", "Strategy and motivation", [
            ("All strategy elements", "/architecture/dashboard?layer=motivation"),
            ("Capabilities", "/architecture/strategy/capabilities/tree"),
            ("Value streams", "/architecture/strategy/value-streams"),
            ("Goals", "/architecture/motivation/goals"),
            ("Requirements", "/architecture/motivation/requirements"),
            ("Stakeholders", "/architecture/motivation/stakeholders"),
        ]),
        ("toggle", "Delivery and change", [
            ("All delivery elements", "/architecture/dashboard?layer=implementation"),
            ("Plateaus", "/architecture/implementation/plateaus"),
            ("Deliverables", "/architecture/implementation/deliverables"),
        ]),
    ]),
]

# The nine Tier 1 routes the founder named, with the sidebar URL each had before the grouping.
TIER1_ROUTES = {
    "Dashboard": "/dashboard/overview",
    "Ask": "/intelligence/ask",
    "Impact": "/strategic/impact-analysis",
    "Applications": "/applications/",
    "Capability Map": "/capability-map/",
    "Gap Analysis": "/enterprise/implementation/gap-analysis",
    "Work Packages": "/enterprise/implementation/work-packages",
    "Roadmaps": "/capability-roadmap",
    "Risk Register": "/risks/",
}


class _Walker(HTMLParser):
    """Flattens the rendered sidebar into ordered tokens using the macro's data-slot markers."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens = []
        self._in_heading = False
        self._heading = ""
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "template":
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "div" and a.get("role") == "heading":
            self._in_heading, self._heading = True, ""
        slot = a.get("data-slot")
        if slot == "sidebar-item":
            self.tokens.append(("item", a["title"], a["href"], a.get("aria-current")))
        elif slot == "sidebar-subitem":
            self.tokens.append(("sub", a["title"], a["href"], a.get("aria-current")))
        elif slot == "sidebar-collapsible":
            self.tokens.append(("toggle", None, a.get("data-sidebar-group-id"), None))
        elif tag == "button" and a.get("aria-controls", "").startswith("sidebar-sub-"):
            label = a["title"]
            last = self.tokens[-1]
            self.tokens[-1] = ("toggle", label, last[2], a.get("aria-expanded"))

    def handle_endtag(self, tag):
        if tag == "template" and self._skip_depth:
            self._skip_depth -= 1
        if tag == "div" and self._in_heading:
            self._in_heading = False
            self.tokens.append(("group", self._heading.strip(), None, None))

    def handle_data(self, data):
        if self._in_heading and not self._skip_depth:
            self._heading += data


_SIDEBAR_RE = re.compile(r'data-testid="sidebar".*?</aside>', re.DOTALL)
_NAV_RE = re.compile(r'<nav\b.*?</nav>', re.DOTALL)


def _login(app, db_session, make_org, role, label):
    from app.models.user import Role, User

    client = _make_logged_in_client(app, db_session, make_org, role, label)
    user = User.query.order_by(User.id.desc()).first()
    if role != "platform_admin":
        Role.insert_roles()
        user.role = Role.query.filter_by(name="Architect").first()
        db_session.flush()
    return client


def _tokens(html):
    nav = _NAV_RE.search(_SIDEBAR_RE.search(html).group(0)).group(0)
    walker = _Walker()
    walker.feed(nav)
    return walker.tokens


def _flatten_expected(approval_href):
    out = []
    for group, entries in EXPECTED_TREE:
        out.append(("group", group))
        for entry in entries:
            if entry[0] == "link":
                href = approval_href if entry[2] == "@approval_inbox" else entry[2]
                out.append(("item", entry[1], href))
            else:
                out.append(("toggle", entry[1]))
                for sub_label, href in entry[2]:
                    out.append(("sub", sub_label, href))
    return out


def _approval_href(app):
    with app.test_request_context():
        from flask import url_for

        return url_for("unified_ai_chat.approval_inbox")


@pytest.mark.parametrize("role", ROLES)
def test_rendered_sidebar_is_exactly_the_approved_tree(app, db_session, make_org, role):
    client = _login(app, db_session, make_org, role, f"nav1-tree-{role}"[:30])
    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200
    rendered = [
        (t[0], t[1]) if t[0] in ("group", "toggle") else (t[0], t[1], t[2])
        for t in _tokens(resp.get_data(as_text=True))
    ]
    assert rendered == _flatten_expected(_approval_href(app))


def _all_hrefs(app):
    hrefs = {}
    for item in _flatten_expected(_approval_href(app)):
        if item[0] in ("item", "sub"):
            hrefs[item[2]] = item[1]
    return hrefs


@pytest.mark.parametrize("role", ["platform_admin", "enterprise_architect"])
def test_every_sidebar_entry_answers(app, db_session, make_org, role):
    client = _login(app, db_session, make_org, role, f"nav1-200-{role}"[:30])
    failures = []
    for href, label in _all_hrefs(app).items():
        resp = client.get(href, follow_redirects=True)
        if resp.status_code != 200:
            failures.append((label, href, resp.status_code))
    assert not failures, failures


@pytest.mark.parametrize("role", ["platform_admin", "enterprise_architect"])
def test_tier1_routes_and_their_old_sidebar_urls_answer(app, db_session, make_org, role):
    client = _login(app, db_session, make_org, role, f"nav1-t1-{role}"[:30])
    failures = []
    for name, url in TIER1_ROUTES.items():
        resp = client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            failures.append((name, url, resp.status_code))
    assert not failures, failures
    # The old sidebar URLs are the same URLs: no tree entry moved a Tier 1 route.
    tree_hrefs = set(_all_hrefs(app))
    assert set(TIER1_ROUTES.values()) <= tree_hrefs


@pytest.mark.parametrize(
    "url,parent,leaf",
    [
        ("/capability-map/hierarchy", "Capability Map", "Hierarchy"),
        ("/architecture/business/actors", "Business architecture", "Actors"),
        ("/architecture/dashboard?layer=technology", "Technology architecture", "All technology elements"),
    ],
)
def test_active_item_is_marked_and_its_parent_is_open(app, db_session, make_org, url, parent, leaf):
    client = _login(app, db_session, make_org, "enterprise_architect", "nav1-active")
    resp = client.get(url)
    assert resp.status_code == 200
    tokens = _tokens(resp.get_data(as_text=True))
    current = [t for t in tokens if t[0] in ("item", "sub") and t[3] == "page"]
    assert [(t[1]) for t in current] == [leaf], current
    toggles = {t[1]: t[3] for t in tokens if t[0] == "toggle"}
    assert toggles[parent] == "true"
    # Every other collapsible starts closed.
    assert [name for name, state in toggles.items() if state == "true"] == [parent]


def test_first_load_has_every_collapsible_closed_and_the_layer_overview_marked(app, db_session, make_org):
    client = _login(app, db_session, make_org, "enterprise_architect", "nav1-closed")
    tokens = _tokens(client.get("/dashboard/overview").get_data(as_text=True))
    assert all(t[3] == "false" for t in tokens if t[0] == "toggle")
    current = [t[1] for t in tokens if t[0] in ("item", "sub") and t[3] == "page"]
    assert current == ["Dashboard Overview"]
    # On the layer overview itself only "Layer overview" is marked, not the layer links beside it.
    tokens = _tokens(client.get("/architecture/dashboard").get_data(as_text=True))
    assert [t[1] for t in tokens if t[0] in ("item", "sub") and t[3] == "page"] == ["Layer overview"]


def test_footer_keeps_all_modules_and_the_page_still_lists_demoted_modules(app, db_session, make_org):
    client = _login(app, db_session, make_org, "enterprise_architect", "nav1-footer")
    html = client.get("/dashboard/overview").get_data(as_text=True)
    aside = _SIDEBAR_RE.search(html).group(0)
    after_nav = aside[aside.index("</nav>"):]
    assert 'href="/modules/"' in after_nav and "All modules" in after_nav
    page = client.get("/modules/").get_data(as_text=True)
    assert "Not in the launch cut" in page


def test_viewer_does_not_see_the_approval_inbox(app, db_session, make_org):
    from app.models.user import Role

    client = _login(app, db_session, make_org, "enterprise_architect", "nav1-viewer")
    from app.models.user import User

    user = User.query.order_by(User.id.desc()).first()
    user.role = Role.query.filter_by(permissions=0).first() or Role(name="ReadOnlyNav1", permissions=0)
    db_session.add(user)
    db_session.flush()
    labels = [t[1] for t in _tokens(client.get("/dashboard/overview").get_data(as_text=True))]
    assert "Approval Inbox" not in labels
    assert "Gap Analysis" in labels
