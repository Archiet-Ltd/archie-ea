"""Navigation v3.1: what each role's sidebar holds, read from the one registry.

The registry (app/navigation/registry.py) is the only place the sidebar, the search
index and the breadcrumbs learn what exists. These tests pin its structure for every
role: Home, AI chat and Approvals; the Work group (Systems, Impact, Changes, Spend,
Compliance) with its sub-items; the Model group (one item per ArchiMate layer with its
element types, then Frameworks and Composer) for every role; Connections only for the
people who may connect a source; and the Spend pages only for the roles whose routes
admit them. tests/test_sidebar_render.py checks the rendered page and follows every link.
"""

from __future__ import annotations

import re

import pytest

from app.models.user import (
    ROLE_BUSINESS_ARCHITECT,
    ROLE_CTO,
    ROLE_DATA_ARCHITECT,
    ROLE_ENTERPRISE_ARCHITECT,
    ROLE_FINANCE,
    ROLE_PLATFORM_ADMIN,
    ROLE_PORTFOLIO_MANAGER,
    ROLE_PROCUREMENT,
    ROLE_SECURITY_ARCHITECT,
    ROLE_SOLUTION_ARCHITECT,
    VALID_ROLES,
)
from app.navigation import registry
from app.utils.role_access import SIDEBAR_LINK_BUDGET

pytestmark = pytest.mark.usefixtures("db_session")

TOP = ["Home", "AI chat", "Approvals"]
WORK = ["Systems", "Impact", "Changes", "Spend", "Compliance"]
LAYERS = ["Strategy", "Business", "Application", "Technology", "Physical", "Motivation",
          "Implementation and migration", "Other"]
MODEL = LAYERS + ["Frameworks", "Composer"]
SECONDARY = ["Connections", "Reports", "Help"]

WORK_CHILDREN = {
    "Systems": ["Applications", "Portfolio", "Data register", "Interfaces", "Technology radar"],
    "Impact": ["Impact analysis", "Traceability"],
    "Changes": ["Gaps", "Solutions", "Options analysis", "Business cases", "Work packages", "Roadmap", "Programmes", "Canvases"],
    "Spend": ["Costs", "Vendors", "Vendor catalogue", "Contracts and licences", "Renewals", "Investments"],
    "Compliance": ["Controls", "Risks", "Decisions", "Review board", "Evidence"],
}
LAYER_TYPES = {
    "Strategy": ["Capability", "Value stream", "Resource", "Course of action"],
    "Business": ["Business actor", "Business role", "Business collaboration", "Business interface",
                 "Business process", "Business function", "Business interaction", "Business event",
                 "Business service", "Business object", "Contract", "Representation", "Product"],
    "Application": ["Application component", "Application collaboration", "Application interface",
                    "Application function", "Application interaction", "Application process",
                    "Application event", "Application service", "Data object"],
    "Technology": ["Node", "Device", "System software", "Technology collaboration", "Technology interface",
                   "Path", "Communication network", "Technology function", "Technology process",
                   "Technology interaction", "Technology event", "Technology service", "Artifact"],
    "Physical": ["Equipment", "Facility", "Distribution network", "Material"],
    "Motivation": ["Stakeholder", "Driver", "Assessment", "Goal", "Outcome", "Principle", "Requirement",
                   "Constraint", "Meaning", "Value"],
    "Implementation and migration": ["Work package", "Deliverable", "Implementation event", "Plateau", "Gap"],
    "Other": ["Location", "Grouping"],
}
# Who may read the Spend pages: the cost viewers and the roles each page was built for.
COST_VIEWERS = {ROLE_CTO, ROLE_PORTFOLIO_MANAGER, ROLE_PLATFORM_ADMIN, ROLE_PROCUREMENT}
# The roles with the data integration section: the Interfaces page's own guard.
INTERFACE_ROLES = {ROLE_BUSINESS_ARCHITECT, ROLE_DATA_ARCHITECT, ROLE_ENTERPRISE_ARCHITECT, ROLE_PLATFORM_ADMIN,
                   ROLE_SECURITY_ARCHITECT, ROLE_SOLUTION_ARCHITECT}
CONNECTING_ROLES = {ROLE_ENTERPRISE_ARCHITECT, ROLE_BUSINESS_ARCHITECT, ROLE_SOLUTION_ARCHITECT, ROLE_CTO}


class _Person:
    """Enough of User for the registry's guards: a persona, the Administrator
    permission (``admin``), and the cross-organisation flag (``platform``)."""

    is_authenticated = True
    is_active = True
    is_anonymous = False

    def __init__(self, role, admin=False, platform=False, org_id=None):
        self.id = 0
        self.enterprise_role = role
        self.is_platform_admin = platform
        self.is_org_admin = admin
        self.organization_id = org_id
        self._admin = admin

    def is_admin(self):
        return self._admin

    def can(self, permission):
        from app.models.user import Permission

        if permission == Permission.ADMINISTER:
            return self._admin
        return True


@pytest.fixture
def ctx(app):
    with app.test_request_context("/dashboard/overview"):
        from flask import g

        g.current_org_id = None
        yield


def _labels(items):
    return [item.label for item in items]


def _nav(person):
    return registry.sidebar_for(person, "dashboard.overview")


def test_the_groups_hold_the_v31_entries_in_order():
    assert [link.label for link in registry.TOP] == TOP
    assert [link.label for link in registry.WORK] == WORK
    assert [link.label for link in registry.MODEL] == MODEL
    assert [link.label for link in registry.SECONDARY] == SECONDARY
    assert [link.label for link in registry.USER_MENU] == ["Account", "Billing", "Team", "Settings", "Platform admin"]


def test_each_work_item_holds_its_pages_in_order():
    held = {link.label: [c.label for c in link.children] for link in registry.WORK}
    assert held == WORK_CHILDREN


def test_each_model_layer_holds_its_element_types_in_order():
    held = {link.label: [c.label for c in link.children] for link in registry.MODEL if link.toggle}
    assert held == LAYER_TYPES


def test_job_entries_open_onto_the_canonical_pages():
    """The kept page for each page named in the sidebar (ADR-0015, ADR-0016, ADR-0018)."""
    pages = {c.label: c.endpoint for link in registry.WORK for c in link.children}
    assert pages["Applications"] == "unified_applications.application_list"
    assert pages["Portfolio"] == "unified_applications.rationalization_dashboard"
    assert pages["Interfaces"] == "interface_register.index"
    assert pages["Roadmap"] == "genome_roadmap.index"
    assert pages["Solutions"] == "solution_design.list_solutions"
    assert pages["Risks"] == "risk.risk_register"
    assert pages["Review board"] == "arb.dashboard"
    top = {link.label: link.endpoint for link in registry.TOP}
    assert top["Approvals"] == "unified_ai_chat.approval_inbox"


def test_a_page_two_entries_open_is_one_page():
    """Applications (Systems) and Application component (Model, Application) are one page, and the
    Data register and Data object, Gaps and Gap likewise: no page is built twice."""
    work = {c.label: c.endpoint for link in registry.WORK for c in link.children}
    model = {c.label: c.endpoint for link in registry.MODEL for c in link.children}
    assert model["Application component"] == work["Applications"]
    assert model["Data object"] == work["Data register"]
    assert model["Gap"] == work["Gaps"]
    assert model["Work package"] == work["Work packages"]
    assert model["Value stream"] == "value_stream.index"


@pytest.mark.parametrize("role", VALID_ROLES)
def test_every_role_sees_the_same_top_work_and_model_entries(ctx, role):
    nav = _nav(_Person(role))
    assert _labels(nav["main"]) == TOP
    assert _labels(nav["work"]) == WORK
    assert _labels(nav["model"]) == MODEL
    layers = {item.label: _labels(item.children) for item in nav["model"] if item.toggle}
    assert layers == LAYER_TYPES
    assert _labels(nav["secondary"])[-2:] == ["Reports", "Help"]


@pytest.mark.parametrize("role", VALID_ROLES)
def test_work_pages_follow_who_may_open_them(ctx, role):
    # Authority is the account's Administrator permission, not the stored persona.
    nav = _nav(_Person(role, admin=role == ROLE_PLATFORM_ADMIN))
    held = {item.label: _labels(item.children) for item in nav["work"]}
    spend = list(WORK_CHILDREN["Spend"])
    if role == ROLE_FINANCE:
        spend = ["Costs", "Vendors", "Vendor catalogue", "Licences", "Investments"]
    elif role not in COST_VIEWERS:
        spend = ["Investments"]
    assert held["Spend"] == spend
    systems = list(WORK_CHILDREN["Systems"])
    if role not in INTERFACE_ROLES:
        systems.remove("Interfaces")
    assert held["Systems"] == systems
    for label in ("Impact", "Changes"):
        assert held[label] == WORK_CHILDREN[label]
    assert held["Compliance"] == WORK_CHILDREN["Compliance"]


def test_a_startup_cto_sees_every_page_of_the_work_group_and_connections(ctx):
    """A new self-serve owner: the default persona, administrator of their own organisation."""
    nav = _nav(_Person(ROLE_PLATFORM_ADMIN, admin=True, org_id=None))
    held = {item.label: _labels(item.children) for item in nav["work"]}
    assert held == WORK_CHILDREN
    assert _labels(nav["secondary"]) == SECONDARY
    assert nav["secondary"][0].href == "/admin/connectors"


@pytest.mark.parametrize("role", VALID_ROLES)
def test_connections_is_shown_only_to_people_who_may_connect_a_source(ctx, role):
    nav = _nav(_Person(role))
    assert ("Connections" in _labels(nav["secondary"])) == (role in CONNECTING_ROLES)
    admin = _nav(_Person(role, admin=True))
    assert _labels(admin["secondary"]) == SECONDARY


def test_the_architecture_group_is_gone(ctx):
    nav = _nav(_Person(ROLE_ENTERPRISE_ARCHITECT))
    assert "architecture" not in nav
    labels = set(_labels(nav["work"] + nav["model"] + nav["main"] + nav["secondary"]))
    assert "Architecture" not in labels and "Integrations" not in labels
    # Review board moved to Compliance, Value streams to Model > Strategy.
    compliance = next(item for item in nav["work"] if item.label == "Compliance")
    assert "Review board" in _labels(compliance.children)
    strategy = next(item for item in nav["model"] if item.label == "Strategy")
    assert "Value stream" in _labels(strategy.children)


def test_platform_admin_is_one_user_menu_entry_for_the_platform_flag_only(ctx):
    owner = _nav(_Person(ROLE_PLATFORM_ADMIN, admin=True))
    platform = _nav(_Person(ROLE_PLATFORM_ADMIN, admin=True, platform=True))
    assert "Platform admin" not in _labels(owner["user_menu"])
    assert _labels(platform["user_menu"])[-1] == "Platform admin"
    tools = {tool.endpoint for tool in registry.PLATFORM_ADMIN_TOOLS}
    for nav in (owner, platform):
        sidebar = nav["main"] + nav["work"] + nav["model"] + nav["secondary"]
        assert not [item for item in sidebar if registry._by_key(item.key).endpoint in tools]


def test_billing_team_and_settings_are_for_administrators(ctx):
    member = _nav(_Person(ROLE_ENTERPRISE_ARCHITECT))
    admin = _nav(_Person(ROLE_PLATFORM_ADMIN, admin=True))
    assert _labels(member["user_menu"]) == ["Account"]
    assert _labels(admin["user_menu"])[:2] == ["Account", "Billing"]
    assert "Settings" in _labels(admin["user_menu"])


def test_the_open_items_follow_the_page_and_what_the_person_chose(ctx):
    """With no choice an item is open when it holds the page; a remembered choice wins."""
    person = _Person(ROLE_PLATFORM_ADMIN, admin=True)
    here = registry.sidebar_for(person, "enterprise.gap_analysis")
    assert {i.key for i in here["work"] if i.open} == {"changes"}
    assert not [i for i in here["model"] if i.open and i.key != "layer_implementation"]
    closed = registry.sidebar_for(person, "enterprise.gap_analysis", None, {"changes": False, "systems": True})
    assert {i.key for i in closed["work"] if i.open} == {"systems"}


def test_a_page_two_entries_open_is_current_under_both(ctx):
    person = _Person(ROLE_PLATFORM_ADMIN, admin=True)
    nav = registry.sidebar_for(person, "unified_applications.application_list")
    systems = next(i for i in nav["work"] if i.key == "systems")
    application = next(i for i in nav["model"] if i.key == "layer_application")
    assert [c.label for c in systems.children if c.current] == ["Applications"]
    assert [c.label for c in application.children if c.current] == ["Application component"]
    assert systems.open and application.open


def test_two_element_types_on_one_list_page_are_told_apart(ctx):
    person = _Person(ROLE_PLATFORM_ADMIN, admin=True)
    nav = registry.sidebar_for(person, "archimate_crud.list_elements",
                               {"layer": "business", "element_type": "BusinessRole"})
    business = next(i for i in nav["model"] if i.key == "layer_business")
    assert [c.label for c in business.children if c.current] == ["Business role"]


def test_the_remembered_choices_are_read_from_a_cookie_named_for_the_person():
    person = _Person(ROLE_PLATFORM_ADMIN)
    person.id = 42
    assert registry.remembered_cookie_name(person) == "sidebar_items_42"
    assert registry.remembered_items({"sidebar_items_42": "systems:1,layer_business:0,bad key:1,x:2"}, person) == {
        "systems": True, "layer_business": False}
    assert registry.remembered_items({"sidebar_items_7": "systems:1"}, person) == {}
    # The page script stores the value URL-encoded; the choice is still read back.
    assert registry.remembered_items({"sidebar_items_42": "layer_business%3A1%2Csystems%3A0"}, person) == {
        "layer_business": True, "systems": False}


@pytest.mark.parametrize("person", [
    _Person(ROLE_PLATFORM_ADMIN, admin=True, platform=True),
    _Person(ROLE_ENTERPRISE_ARCHITECT),
    _Person(ROLE_PROCUREMENT),
])
def test_nothing_points_at_the_retired_ask_page_or_all_modules(ctx, person):
    nav = registry.sidebar_for(person, None)
    every = [i for group in ("main", "work", "model", "secondary", "user_menu") for i in nav[group]]
    every += [c for i in every for c in i.children]
    assert not [i for i in every if i.href and "/intelligence/ask" in i.href]
    assert not [i for i in every if i.label == "All modules" or (i.href or "").startswith("/modules")]
    assert not [row for row in registry.search_index(person) if row["url"].startswith("/intelligence/ask")]


def test_labels_are_sentence_case():
    allowed_capitals = {"AI", "ArchiMate"}
    links = list(registry.ALL_ENTRIES) + list(registry.PLATFORM_ADMIN_TOOLS)
    links += [c for link in registry.WORK + registry.MODEL for c in link.children]
    for link in links:
        words = link.label.split()
        assert words[0][0].isupper(), link.label
        for word in words[1:]:
            assert word in allowed_capitals or word == word.lower(), link.label


def test_two_levels_at_most():
    for link in registry.ALL_ENTRIES:
        for child in link.children:
            assert not child.children, (link.label, child.label)


def test_every_catalogue_page_has_a_home_in_the_sidebar(app):
    """Every page the old sidebar and the All-modules list carried is filed under one
    entry, so search labels it and its breadcrumb starts from that entry."""
    from app.modules.modules_directory.routes import all_module_links

    orphans = [link["endpoint"] for link in all_module_links() if registry.entry_for_endpoint(link["endpoint"]) is None]
    assert orphans == []


@pytest.mark.parametrize("endpoint,entry", [
    ("capability_map.index", "systems"),
    ("unified_applications.vendors", "spend"),
    ("genome_roadmap.index", "changes"),
    ("enterprise.work_packages", "changes"),
    ("procurement.contracts_list", "spend"),
    ("arch_decisions.list_decisions", "compliance"),
    ("arb.dashboard", "compliance"),
    ("strategic.impact_analysis", "impact"),
    ("intelligence_ui.ask", "ai_chat"),
    ("unified_ai_chat.approval_inbox", "approvals"),
    ("archimate_crud.list_elements", "archimate_model"),
    ("admin.organizations_list", "platform_admin"),
])
def test_pages_are_filed_under_the_job_they_serve(endpoint, entry):
    assert registry.entry_for_endpoint(endpoint).key == entry


@pytest.mark.parametrize("query,first", [
    ("ask a question", "AI chat"),
    ("applications", "Applications"),
    ("gap analysis", "Gaps"),
    ("rationalization", "Portfolio"),
    ("risk register", "Risks"),
    ("connectors", "Connections"),
    ("integrations", "Connections"),
    ("vendor options", "Options analysis"),
    ("plateaus", "Roadmap"),
])
def test_old_names_find_the_new_entries(ctx, query, first):
    person = _Person(ROLE_PLATFORM_ADMIN, admin=True)
    labels = [row["label"] for row in registry.search_pages(person, query)]
    assert labels[0] == first, labels


def test_every_entry_is_in_the_search_index_under_its_name_and_where_it_sits(ctx):
    person = _Person(ROLE_PLATFORM_ADMIN, admin=True)
    rows = registry.search_index(person)
    names = {term.lower(): row for row in rows for term in row["terms"]}
    for link in registry.WORK + registry.MODEL:
        for child in link.children:
            row = names.get(child.label.lower())
            assert row is not None, (link.label, child.label)
            assert row["section"] in (link.label, "Model, " + link.label, "Changes", "Systems"), (child.label, row["section"])
    for link in registry.TOP + registry.SECONDARY:
        assert link.label.lower() in names, link.label
    # Two entries to one page are one row that answers to both names.
    row = names["application component"]
    assert "applications" in {t.lower() for t in row["terms"]}
    assert len({r["url"] for r in rows}) == len(rows)


def test_link_budget_is_the_v3_ceiling():
    """The ceiling of 25 links a page holds for the rendered sidebar (navigation standard rule 4):
    a closed item keeps its sub-items out of the page, so the page carries what is in view."""
    assert SIDEBAR_LINK_BUDGET == 25


def test_icons_are_distinct_within_the_sidebar():
    icons = [link.icon for link in registry.TOP + registry.WORK + registry.MODEL + registry.SECONDARY]
    assert len(icons) == len(set(icons)), icons
    for icon in icons:
        assert re.fullmatch(r"[a-z0-9-]+", icon)


@pytest.mark.parametrize("query,url,section", [
    ("business process", "/architecture/business/BusinessProcess", "Model, Business"),
    ("business actors", "/architecture/business/BusinessActor", "Model, Business"),
    ("node", "/architecture/technology/Node", "Model, Technology"),
    ("requirement", "/architecture/motivation/Requirement", "Model, Motivation"),
    ("principle", "/architecture/motivation/Principle", "Model, Motivation"),
    ("deliverable", "/architecture/implementation/Deliverable", "Model, Implementation and migration"),
    ("plateau", "/enterprise/implementation/plateaus", "Model, Implementation and migration"),
    ("work package", "/enterprise/implementation/work-packages", "Changes"),
    ("application component", "/applications/", "Systems"),
])
def test_every_element_type_is_found_by_name_where_it_sits(ctx, query, url, section):
    """ADR-0018: one list page per ArchiMate element type, found by name and labelled with where it sits."""
    rows = registry.search_pages(_Person(ROLE_ENTERPRISE_ARCHITECT), query, limit=40)
    hits = [(row["url"], row["section"]) for row in rows]
    assert any(u.rstrip("/") == url.rstrip("/") and sec == section for u, sec in hits), hits


def test_gaps_point_at_the_one_gap_register():
    """ADR-0018: Changes' gaps are /enterprise/implementation/gap-analysis, never the ADM board's copy."""
    gaps = next(c for link in registry.WORK for c in link.children if c.label == "Gaps")
    assert gaps.endpoint == "enterprise.gap_analysis"
    by_type = {element_type: endpoint for _, _, element_type, endpoint, _, _ in registry.element_type_pages()}
    assert by_type.get("Gap", "enterprise.gap_analysis") == "enterprise.gap_analysis"
    assert not [e for e in by_type.values() if e.startswith("adm_kanban")]


def test_the_types_with_no_list_open_the_model_page():
    """Location and Grouping have no list page of their own yet: they open the architecture model page."""
    other = next(link for link in registry.MODEL if link.label == "Other")
    assert {c.label: c.endpoint for c in other.children} == {
        "Location": "archimate_crud.dashboard", "Grouping": "archimate_crud.dashboard"}
