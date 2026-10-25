"""Navigation v3: what each role's sidebar holds, read from the one registry.

The registry (app/navigation/registry.py) is the only place the sidebar, the search
index and the breadcrumbs learn what exists. These tests pin its structure for every
role: the same seven main entries, in the same order, with the same names; the extra
Architecture group only for the architecture roles; procurement pages as sub-items of
Spend only for the roles whose routes admit them; and exactly ten entries for a startup
CTO (any self-serve owner). tests/test_sidebar_render.py checks the rendered page and
follows every link.
"""

from __future__ import annotations

import re

import pytest

from app.models.user import (
    ROLE_ARB_MEMBER,
    ROLE_ENTERPRISE_ARCHITECT,
    ROLE_FINANCE,
    ROLE_PLATFORM_ADMIN,
    ROLE_PORTFOLIO_MANAGER,
    ROLE_PROCUREMENT,
    ROLE_SOLUTION_ARCHITECT,
    VALID_ROLES,
)
from app.navigation import registry
from app.utils.role_access import SIDEBAR_LINK_BUDGET

pytestmark = pytest.mark.usefixtures("db_session")

MAIN = ["Home", "AI chat", "Systems", "Impact", "Changes", "Spend", "Compliance"]
SECONDARY = ["Integrations", "Reports", "Help"]
ARCHITECTURE = ["Model", "Composer", "Review board", "Value streams"]
ARCHITECTURE_ROLES = {ROLE_ENTERPRISE_ARCHITECT, ROLE_SOLUTION_ARCHITECT, ROLE_ARB_MEMBER}


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


def test_main_entries_are_the_jobs_in_order():
    assert [link.label for link in registry.MAIN] == MAIN
    assert [link.label for link in registry.SECONDARY] == SECONDARY
    assert [link.label for link in registry.ARCHITECTURE] == ARCHITECTURE
    assert [link.label for link in registry.USER_MENU] == ["Account", "Billing", "Team", "Settings", "Platform admin"]


def test_main_entries_land_on_the_canonical_pages():
    """The consolidation map's kept page for each job (ADR-0015, ADR-0016)."""
    landing = {link.label: link.endpoint for link in registry.MAIN}
    assert landing == {
        "Home": "dashboard.overview",
        "AI chat": "unified_ai_chat.index",
        "Systems": "unified_applications.application_list",
        "Impact": "enterprise.impact_analysis",
        "Changes": "enterprise.gap_analysis",
        "Spend": "unified_applications.rationalization_dashboard",
        "Compliance": "risk.risk_register",
    }


@pytest.mark.parametrize("role", VALID_ROLES)
def test_every_role_sees_the_same_main_entries_in_the_same_order(ctx, role):
    nav = _nav(_Person(role))
    assert _labels(nav["main"]) == MAIN
    assert len(nav["main"]) <= 10
    assert _labels(nav["secondary"]) == SECONDARY


def test_a_startup_cto_sees_exactly_the_ten_entries(ctx):
    """A new self-serve owner: the default persona, administrator of their own organisation."""
    nav = _nav(_Person(ROLE_PLATFORM_ADMIN, admin=True, org_id=None))
    entries = nav["main"] + nav["architecture"] + nav["secondary"]
    assert _labels(entries) == MAIN + SECONDARY
    assert len(entries) == 10
    # The one thing nested for them is the page an organisation administrator may open
    # and the route admits: their organisation's data-subject requests, under Compliance.
    # No procurement pages are offered as sub-items (those follow the persona).
    assert {item.label: _labels(item.children) for item in entries if item.children} == {
        "Compliance": ["Data Subject Requests"]
    }
    # Connecting a source is theirs to do: Integrations opens the connectors page.
    integrations = nav["secondary"][0]
    assert integrations.href == "/admin/connectors"


@pytest.mark.parametrize("role", VALID_ROLES)
def test_only_the_architecture_roles_get_the_architecture_group(ctx, role):
    nav = _nav(_Person(role))
    if role in ARCHITECTURE_ROLES:
        assert _labels(nav["architecture"]) == ARCHITECTURE
    else:
        assert nav["architecture"] == []


@pytest.mark.parametrize(
    "role,expected",
    [
        (ROLE_PROCUREMENT, ["Procurement", "Contracts", "Licences", "Renewals", "Spend analytics"]),
        (ROLE_PORTFOLIO_MANAGER, ["Procurement", "Contracts", "Licences", "Renewals", "Spend analytics"]),
        # Finance may open licences and spend only (requires_procurement_or_finance).
        (ROLE_FINANCE, ["Licences", "Spend analytics"]),
    ],
)
def test_procurement_pages_are_sub_items_of_spend_for_the_roles_they_admit(ctx, role, expected):
    nav = _nav(_Person(role))
    spend = next(item for item in nav["main"] if item.key == "spend")
    assert spend.label == "Spend"
    assert _labels(spend.children) == expected
    others = [item for item in nav["main"] if item.key != "spend"]
    assert all(not item.children for item in others)


@pytest.mark.parametrize("role", [r for r in VALID_ROLES if r not in (ROLE_PROCUREMENT, ROLE_FINANCE, ROLE_PORTFOLIO_MANAGER)])
def test_no_other_role_gets_procurement_sub_items(ctx, role):
    nav = _nav(_Person(role, admin=True))
    spend = next(item for item in nav["main"] if item.key == "spend")
    assert not spend.children
    # An administrator is also offered the data-subject requests page, under Compliance.
    assert {item.label: _labels(item.children) for item in nav["main"] if item.children} == {
        "Compliance": ["Data Subject Requests"]
    }


def test_platform_admin_is_one_user_menu_entry_for_the_platform_flag_only(ctx):
    owner = _nav(_Person(ROLE_PLATFORM_ADMIN, admin=True))
    platform = _nav(_Person(ROLE_PLATFORM_ADMIN, admin=True, platform=True))
    assert "Platform admin" not in _labels(owner["user_menu"])
    assert _labels(platform["user_menu"])[-1] == "Platform admin"
    tools = {tool.endpoint for tool in registry.PLATFORM_ADMIN_TOOLS}
    for nav in (owner, platform):
        sidebar = nav["main"] + nav["architecture"] + nav["secondary"]
        assert not [item for item in sidebar if registry._by_key(item.key).endpoint in tools]


def test_billing_team_and_settings_are_for_administrators(ctx):
    member = _nav(_Person(ROLE_ENTERPRISE_ARCHITECT))
    admin = _nav(_Person(ROLE_PLATFORM_ADMIN, admin=True))
    assert _labels(member["user_menu"]) == ["Account"]
    assert _labels(admin["user_menu"])[:2] == ["Account", "Billing"]
    assert "Settings" in _labels(admin["user_menu"])


def test_a_person_who_cannot_connect_a_source_is_offered_the_imports(ctx):
    nav = _nav(_Person(ROLE_ENTERPRISE_ARCHITECT))
    integrations = nav["secondary"][0]
    assert integrations.label == "Integrations"
    assert integrations.href == "/batch-import/"


@pytest.mark.parametrize("person", [
    _Person(ROLE_PLATFORM_ADMIN, admin=True, platform=True),
    _Person(ROLE_ENTERPRISE_ARCHITECT),
    _Person(ROLE_PROCUREMENT),
])
def test_nothing_points_at_the_retired_ask_page_or_all_modules(ctx, person):
    nav = registry.sidebar_for(person, None)
    every = [i for group in ("main", "architecture", "secondary", "user_menu") for i in nav[group]]
    every += [c for i in every for c in i.children]
    assert not [i for i in every if "/intelligence/ask" in i.href]
    assert not [i for i in every if i.label == "All modules" or i.href.startswith("/modules")]
    assert not [row for row in registry.search_index(person) if row["url"].startswith("/intelligence/ask")]


def test_labels_are_sentence_case():
    allowed_capitals = {"AI", "ArchiMate"}
    links = list(registry.ALL_ENTRIES) + list(registry.PLATFORM_ADMIN_TOOLS)
    links += [c for link in registry.MAIN for c in link.children]
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
    ("unified_applications.vendors", "systems"),
    ("genome_roadmap.index", "changes"),
    ("enterprise.work_packages", "changes"),
    ("procurement.contracts_list", "spend"),
    ("arch_decisions.list_decisions", "compliance"),
    ("strategic.impact_analysis", "impact"),
    ("intelligence_ui.ask", "ai_chat"),
    ("admin.organizations_list", "platform_admin"),
])
def test_pages_are_filed_under_the_job_they_serve(endpoint, entry):
    assert registry.entry_for_endpoint(endpoint).key == entry


def test_old_names_find_the_new_entries(ctx):
    person = _Person(ROLE_PLATFORM_ADMIN, admin=True)
    found = {q: [row["label"] for row in registry.search_pages(person, q)] for q in (
        "ask a question", "applications", "gap analysis", "rationalization", "risk register", "connectors",
    )}
    assert found["ask a question"][0] == "AI chat"
    assert found["applications"][0] in ("Systems", "Applications")
    assert "Changes" in found["gap analysis"]
    assert found["rationalization"][0] == "Spend"
    assert found["risk register"][0] == "Compliance"
    assert found["connectors"][0] == "Integrations"


def test_link_budget_is_the_v3_ceiling():
    """Logo-free v3 shell: 7 main + 4 architecture + 5 procurement sub-items + 3 secondary
    + the connect button + the user menu (6) + the Ask row of the search box stays far
    below the old 35; the ceiling falls to 25 (navigation standard rule 4)."""
    assert SIDEBAR_LINK_BUDGET == 25


def test_icons_are_distinct_within_the_sidebar():
    icons = [link.icon for link in registry.MAIN + registry.ARCHITECTURE + registry.SECONDARY]
    assert len(icons) == len(set(icons)), icons
    for icon in icons:
        assert re.fullmatch(r"[a-z0-9-]+", icon)


@pytest.mark.parametrize("query,url,section", [
    ("business process", "/architecture/business/BusinessProcess", "Systems"),
    ("application component", "/architecture/application/ApplicationComponent", "Systems"),
    ("node", "/architecture/technology/Node", "Systems"),
    ("requirement", "/architecture/motivation/Requirement", "Compliance"),
    ("principle", "/architecture/motivation/Principle", "Compliance"),
    ("deliverable", "/architecture/implementation/Deliverable", "Changes"),
    ("plateau", "/enterprise/implementation/plateaus", "Changes"),
    ("work package", "/enterprise/implementation/work-packages", "Changes"),
])
def test_every_element_type_is_found_by_name_under_its_job(ctx, query, url, section):
    """ADR-0018: one list page per ArchiMate element type, filed under its job, no sidebar entry."""
    rows = registry.search_pages(_Person(ROLE_ENTERPRISE_ARCHITECT), query, limit=40)
    hits = [(row["url"], row["section"]) for row in rows]
    assert (url, section) in hits, hits


def test_gaps_point_at_the_one_gap_register():
    """ADR-0018: Changes' gaps are /enterprise/implementation/gap-analysis, never the ADM board's copy."""
    changes = registry._by_key("changes")
    assert changes.endpoint == "enterprise.gap_analysis"
    by_type = {element_type: endpoint for _, _, element_type, endpoint, _, _ in registry.element_type_pages()}
    assert by_type.get("Gap", "enterprise.gap_analysis") == "enterprise.gap_analysis"
    assert not [e for e in by_type.values() if e.startswith("adm_kanban")]
