"""Persona sidebar zone budgets and membership (shell-overhaul Wave 1, Task 2).

Pure unit tests against app.utils.role_access.SIDEBAR_ZONES / get_sidebar_zones —
no app/db fixtures needed, the structure is plain Python data.
"""

from collections import Counter

import pytest

from app.models.user import (
    ROLE_APPLICATION_MANAGER,
    ROLE_ARB_MEMBER,
    ROLE_BUSINESS_ARCHITECT,
    ROLE_CTO,
    ROLE_ENTERPRISE_ARCHITECT,
    ROLE_PLATFORM_ADMIN,
    ROLE_PORTFOLIO_MANAGER,
    ROLE_PROCUREMENT,
    ROLE_SOLUTION_ARCHITECT,
)
from app.utils.role_access import (
    SIDEBAR_LINK_BUDGET,
    SIDEBAR_ZONES,
    get_sidebar_zones,
)

ALL_ROLES = [
    ROLE_SOLUTION_ARCHITECT,
    ROLE_ENTERPRISE_ARCHITECT,
    ROLE_BUSINESS_ARCHITECT,
    ROLE_ARB_MEMBER,
    ROLE_PORTFOLIO_MANAGER,
    ROLE_CTO,
    ROLE_APPLICATION_MANAGER,
    ROLE_PROCUREMENT,
    ROLE_PLATFORM_ADMIN,
]

BOARD_ROLES = {ROLE_ENTERPRISE_ARCHITECT, ROLE_ARB_MEMBER, ROLE_CTO, ROLE_PLATFORM_ADMIN}


class _StubUser:
    def __init__(self, role, is_platform_admin=False):
        self.enterprise_role = role
        self.is_platform_admin = is_platform_admin

    def is_admin(self):
        return self.is_platform_admin

    def can(self, _permissions):
        # Stub users represent fully-authorised personas; every requires
        # guard passes so the zone comparison is unfiltered.
        return True


def _zone_names(role):
    return {zone["zone"] for zone in SIDEBAR_ZONES[role]}


def _all_links(role):
    links = []
    for zone in SIDEBAR_ZONES[role]:
        links.extend(zone["links"])
    return links


def test_sidebar_link_budget_is_31():
    """Raised 25 -> 26 in the Task 3 fix round (coordinator review of the
    sidebar rewrite): platform_admin's two review-mandated admin-zone links
    (Salesforce Integration, Power Platform) alone render exactly 25 visible
    links, leaving no headroom for the also-mandatory All-modules directory
    link.

    Lowered back 26 -> 25 in the evidence-review fix round: My-work's
    "Applications" link for platform_admin duplicated Library's — same
    endpoint, same label, twice in the sidebar — and removing it dropped the
    real count by exactly one. See app/utils/role_access.py's
    SIDEBAR_LINK_BUDGET comment and
    tests/test_sidebar_render.py::test_platform_admin_hits_the_link_budget_exactly.

    Raised 27 -> 28 (BA-A1, 21 Aug 2026): the Business Architecture landing page
    is the front door to all twelve BA outputs, and platform_admin is the default
    role for anyone who never picked one — so it is the role that most needs the
    link, and its only shared zone was already exactly on the ceiling. Retiring
    "Batch Import" to make room would have regressed the S-11 finding that put it
    there, trading one discoverability defect for another.

    S-11 (18 Aug 2026): raised 25 -> 26 for the one governance link added to
    surface Architecture Decisions, which platform_admin also renders.

    Phase 0 CI audit (fix/phase0-ci-and-audit): raised 28 -> 30 — see
    role_access.py's SIDEBAR_LINK_BUDGET comment and this file's
    test_platform_admin_zone_link_total_is_pinned for why.

    Raised 30 -> 31: "Ask a question" is one new link in every persona's My
    work, and platform_admin renders it like everyone else. The rendered-link
    test asserts the count EQUALS this number, so the ceiling moves with the
    link rather than leaving slack that does not exist.

    Canvas/framework UI fix, round 2 (25 Sep 2026): two new Library links
    ("Canvases", "Frameworks") were added for every role and the ceiling did
    not move — see role_access.py's SIDEBAR_LINK_BUDGET comment for the folds
    that paid for them.

    Approval Inbox (4 Oct 2026): raised 31 -> 32. The Approval Inbox is one
    new link in every persona's My work — the single queue for every pending
    change proposal. platform_admin (the role with zero headroom) renders it
    like every other role; no fold is available. Raising the budget by one is
    the honest cost of a genuinely new, intentional link.

    Agent Registry (R1-B56, 6 Oct 2026): raised 32 -> 33. One new
    platform_admin-only link (owner, charter and delegated limits per
    registered agent); no fold is available for the same reason as above.
    """
    assert SIDEBAR_LINK_BUDGET == 33


def test_every_role_is_defined():
    for role in ALL_ROLES:
        assert role in SIDEBAR_ZONES, f"missing SIDEBAR_ZONES entry for {role}"


def test_every_role_within_link_budget():
    for role in ALL_ROLES:
        total = len(_all_links(role))
        assert total <= SIDEBAR_LINK_BUDGET, (
            f"{role} has {total} sidebar links, budget is {SIDEBAR_LINK_BUDGET}"
        )


def test_every_role_has_home_my_work_library():
    for role in ALL_ROLES:
        zones = _zone_names(role)
        assert {"home", "my_work", "library"}.issubset(zones), (
            f"{role} is missing one of home/my_work/library, has {zones}"
        )


def test_only_board_roles_have_governance():
    for role in ALL_ROLES:
        zones = _zone_names(role)
        if role in BOARD_ROLES:
            assert "governance" in zones, f"{role} should have a governance zone"
        else:
            assert "governance" not in zones, f"{role} should not have a governance zone"


def test_only_platform_admin_has_admin_zone():
    for role in ALL_ROLES:
        zones = _zone_names(role)
        if role == ROLE_PLATFORM_ADMIN:
            assert "admin" in zones, "platform_admin should have an admin zone"
        else:
            assert "admin" not in zones, f"{role} should not have an admin zone"


def test_every_endpoint_is_a_dotted_string():
    for role in ALL_ROLES:
        for link in _all_links(role):
            endpoint = link["endpoint"]
            assert isinstance(endpoint, str), f"{role} link {link!r} endpoint is not a string"
            assert "." in endpoint, f"{role} link {link!r} endpoint is not dotted"


def test_ask_link_is_first_in_every_personas_my_work_and_is_the_same_link():
    """One front door to the Ask page, in the same place for every persona."""
    for role in SIDEBAR_ZONES:
        my_work = next(z for z in SIDEBAR_ZONES[role] if z["zone"] == "my_work")["links"]
        assert my_work[0] == {
            "label": "Ask a question",
            "endpoint": "intelligence_ui.ask",
            "icon": "search",
        }, f"{role}: the Ask link is not first in My work"
        assert [link["endpoint"] for link in _all_links(role)].count("intelligence_ui.ask") == 1


def test_twin_map_has_no_sidebar_link_of_its_own():
    """The Twin map is reached from the Ask page; it does not spend a second link."""
    for role in SIDEBAR_ZONES:
        assert "intelligence_ui.twin_map" not in [link["endpoint"] for link in _all_links(role)]


def _my_work_labels(role):
    for zone in SIDEBAR_ZONES[role]:
        if zone["zone"] == "my_work":
            return [link["label"] for link in zone["links"]]
    raise AssertionError(f"{role} has no my_work zone")


def test_solution_architect_my_work_membership():
    """Task 3 fix round: Programmes (solution_design.programmes_list) added —
    a real, working route reachable from nowhere in the sidebar. Coordinator
    review of the sidebar rewrite; membership amended accordingly."""
    assert _my_work_labels(ROLE_SOLUTION_ARCHITECT) == [
        "Ask a question",
        "Architecture Journey",
        # Approval Inbox — one queue for every pending change proposal,
        # shared by every persona with GENERAL permission.
        "Approval Inbox",
        "Solutions",
        "AI Chat",
        "ADM Kanban",
        # 31 Aug 2026: the ARB dashboard was reachable from no persona's
        # sidebar, though the solution architect is the role that takes work
        # TO the board. Adding it closed a handoff that stopped mid-journey.
        "Review Board",
        "Programmes",
        # SAP S/4HANA Interface Register (Task 02, round 3 fix): a 7th
        # my_work link was added at role_access.py:469; this test asserted
        # exact equality and had gone red on main until this line was added.
        "Interface Register",
        # Reported problem: the platform's own "analyse the ripple effects of a
        # change" feature was reachable only from the 83-item All-modules page, so an architect
        # with no training had no discoverable path to it. Impact analysis is a primary job for
        # this persona. This is the 8th link, one past the spec table's "3-7"; the spec now says so.
        "Impact Analysis",
    ]


def test_enterprise_architect_my_work_membership():
    """Task 3 fix round: ArchiMate Composer and Traceability Matrix added —
    both real, working routes reachable from nowhere in the sidebar.
    Coordinator review of the sidebar rewrite; membership amended
    accordingly.

    Model as of and Changes added — the enterprise architect owns the
    capability model and needs to audit its evolution."""
    assert _my_work_labels(ROLE_ENTERPRISE_ARCHITECT) == [
        "Ask a question",
        "Transformation programmes",
        # Approval Inbox — one queue for every pending change proposal,
        # shared by every persona with GENERAL permission.
        "Approval Inbox",
        # BA-A3 (21 Aug 2026): "Business Architecture" is deliberately absent
        # here — this role renders 26 links, exactly the sidebar_links ratchet
        # baseline, so a 27th would trip the gate. See the comment on this
        # role's entry in app/utils/role_access.py.
        "Portfolio",
        # NAV-1 (27 Aug 2026): "Capability Map" removed — it pointed at
        # capability_map.index, the identical endpoint Library already renders
        # as "Capabilities" for every role, so this persona showed one page
        # twice. Rendered total 26 -> 25, which is what let the nav-coverage
        # links land elsewhere without any persona exceeding the budget.
        "Roadmaps",
        "ArchiMate Composer",
        "Traceability Matrix",
        # S-11: real Implementation & Migration pages that were reachable
        # from nowhere in the sidebar.
        "Gap Analysis",
        "Work Packages",
        # S-11 remainder: directory-only, never in a sidebar zone.
        "Impact Analysis",
        "Capability Health",
        # "Duplicate Detection" removed in the canvas/framework UI fix, round 2
        # (25 Sep 2026): this zone had one link of headroom and "Canvases"
        # plus "Frameworks" joining the shared Library zone needed two. Still
        # reachable via "All modules" (see role_access.py's comment on this
        # role's entry).
        # ARCH-123 / ARCH-124 (QA register closure, 18 Aug 2026): Data
        # Architecture (existing, previously undiscoverable) and Tech Radar
        # (new), folded into enterprise_architect's My work since there is
        # no dedicated Data Architect / Technical Architect role yet.
        "Data Architecture",
        "Tech Radar",
        # Model history: as-of snapshot and changes between dates.
        "Model as of",
        "Changes",
    ]


def test_cto_my_work_membership():
    assert _my_work_labels(ROLE_CTO) == [
        "Ask a question",
        # 31 Aug 2026: main.capability_roadmap is the CTO's own planning
        # surface and was linked from nowhere. It belongs first -- it is the
        # screen this persona opens to answer "what are we doing next".
        "Roadmaps",
        # Approval Inbox — one queue for every pending change proposal,
        # shared by every persona with GENERAL permission.
        "Approval Inbox",
        "Transformation programmes",
        "Health Scorecard",
        "Rationalization",
        "Investment Analysis",
        # NAV-1: nav-coverage output 9 (KPI/metric dashboards) had routes but
        # no sidebar link in any persona.
        "Portfolio KPIs",
        # Level 10 walkthrough, 30 Aug 2026: the radar is the CTO's technology
        # direction instrument and /technology/radar/classify names "cto" in
        # its own require_roles list -- the persona was authorised to set the
        # rings and had no link to the page from anywhere.
        "Tech Radar",
        # Ownership coverage by business unit — CTO accountability.
        "Ownership Coverage",
        # R1-B85: supported-estate share, open exceptions, the store-
        # agreement disagreement finder.
        "CTO Scorecard",
    ]


def test_business_architect_my_work_membership():
    """Canvas/framework UI fix (24 Sep 2026): "Capability Frameworks" removed
    from this persona's My work — it is now in the shared Library zone as
    "Frameworks", so keeping it here would duplicate it.

    Round 2 (25 Sep 2026): "Capability Map" also removed — same endpoint as
    Library's "Capabilities" (capability_map.index), the exact duplicate
    enterprise_architect's My work had already dropped for the same reason.
    This zone had no headroom left once "Canvases" and "Frameworks" joined
    the shared Library zone; dropping a same-page duplicate loses nothing."""
    assert _my_work_labels(ROLE_BUSINESS_ARCHITECT) == [
        "Ask a question",
        "Architecture Journey",
        # Approval Inbox — one queue for every pending change proposal,
        # shared by every persona with GENERAL permission.
        "Approval Inbox",
        "Capability Maturity",
        "Value Streams",
        "Value Streams at Risk",
        "Stakeholder Map",
        "Gap Analysis",
        "Roadmaps",
        "Work Packages",
        "Traceability Matrix",
        "Capability Health",
        "Impact Analysis",
        "Data Architecture",
        "Data Lineage",
        "Motivation Model",
        "Products & Services",
        "Org Chart & RACI",
    ]


def test_portfolio_manager_my_work_membership():
    """S-11 remainder: Consolidation List was directory-only.

    Round 2 (25 Sep 2026): "Duplicate Detection" moved here from
    enterprise_architect's My work, which had no headroom left once
    "Canvases" and "Frameworks" joined the shared Library zone. This zone
    had ample headroom, and portfolio_manager already owns Rationalization,
    from which the page is reached in context."""
    assert _my_work_labels(ROLE_PORTFOLIO_MANAGER) == [
        "Ask a question",
        "Portfolio",
        # Approval Inbox — one queue for every pending change proposal,
        # shared by every persona with GENERAL permission.
        "Approval Inbox",
        "Rationalization",
        "Vendors",
        "Applications",
        "Consolidation List",
        # NAV-1: see test_cto_my_work_membership — same page, other owner.
        "Portfolio KPIs",
        "Duplicate Detection",
        # Ownership coverage by business unit — portfolio manager.
        "Ownership Coverage",
    ]


def test_procurement_my_work_membership():
    """Task 3 fix round: Overview, Licences and Compliance added — all real,
    working routes reachable from nowhere in the sidebar. Overview goes
    first per the coordinator's review of the sidebar rewrite."""
    assert _my_work_labels(ROLE_PROCUREMENT) == [
        "Ask a question",
        "Overview",
        # Approval Inbox — one queue for every pending change proposal,
        # shared by every persona with GENERAL permission.
        "Approval Inbox",
        "Vendors",
        "Contracts",
        "Renewals",
        "Spend",
        "Licences",
        "Compliance",
    ]


def test_application_manager_my_work_membership():
    """Task 3 fix round: My Applications added. my_applications.dashboard is
    a personally-scoped view (ApplicationOwner rows for the current user
    only) distinct from unified_applications.application_list's org-wide
    list — see app/modules/my_applications/routes.py:get_owned_apps — and
    was reachable from nowhere in the sidebar."""
    assert _my_work_labels(ROLE_APPLICATION_MANAGER) == [
        "Ask a question",
        "My Applications",
        # Approval Inbox — one queue for every pending change proposal,
        # shared by every persona with GENERAL permission.
        "Approval Inbox",
        "Applications",
        "Rationalization",
        "Vendors",
    ]


def test_platform_admin_zone_link_total_is_pinned():
    """Task 3 fix round: platform_admin's two new admin-zone links
    (Salesforce Integration, Power Platform) bring its zone-only link total
    (SIDEBAR_ZONES data, not counting the sidebar's own header/footer chrome)
    to exactly 23. All-modules is deliberately NOT one of platform_admin's
    zone links — see _LIBRARY_LINKS_WITH_DIRECTORY's comment in
    role_access.py — so it does not appear in this count; it is still
    reachable via the sidebar footer fallback, pinned instead by
    tests/test_sidebar_render.py::test_platform_admin_hits_the_link_budget_exactly
    (which renders the template and counts real visible links, 26 including
    that fallback plus the header logo and footer logout links). An
    equality assertion here, not <=, so a future zone edit that silently
    changes this number is caught rather than absorbed by budget headroom
    that does not actually exist.

    Evidence-review fix round: 23 -> 22. _MY_WORK_LINKS[ROLE_PLATFORM_ADMIN]'s
    "Applications" link duplicated the one already in Library (same
    endpoint, unified_applications.application_list); removing it drops the
    zone-only total by one, and the rendered total (see
    test_platform_admin_hits_the_link_budget_exactly) by the same one, to
    25.

    S-11 (18 Aug 2026): 22 -> 23. The governance zone gained "Decisions"
    (arch_decisions.list_decisions), which platform_admin shares; the two
    Implementation & Migration links added in the same pass are
    enterprise_architect-only and do not appear here.

    S-11 remainder (18 Aug 2026): 23 -> 24. "Batch Import" added to the admin
    zone — the only S-11-remainder module platform_admin shares; the other
    nine landed in EA / business_architect / portfolio_manager My-work zones.

    BA-A3 (21 Aug 2026): 24 -> 25. "Business Architecture" added to this
    role's My work. platform_admin is the default enterprise_role for any
    user who never picked one, so a page limited to the two architect roles
    would be invisible to most real accounts. Rendered total 25 -> 26, still
    under SIDEBAR_LINK_BUDGET (27).

    Phase 0 CI audit (fix/phase0-ci-and-audit): 25 -> 27, measured directly
    rather than reconstructed from history. This pinned assertion and
    role_access.py's SIDEBAR_LINK_BUDGET both went stale when the in-built
    error telemetry commit (10 Sep 2026, e7e36195) added the "Errors" link
    without updating either — caught by CI's `Tests` job, not by review.
    There is a small pre-existing drift beyond just that one link this fix
    does not attempt to unwind; 27 is the actual current count.

    27 -> 28: "Ask a question" added to every persona's My work, platform_admin
    included. It is the only link the Ask and Twin map pages add; the Twin map
    is reached from the Ask page.

    Canvas/framework UI fix, round 2 (25 Sep 2026): stays 28. "Canvases" and
    "Frameworks" join this role's Library zone (+2); "Import History" and
    "Batch Import" fold out of the Admin zone onto the admin dashboard page
    (-2), and Framework Management / Framework Configuration are added to
    that same dashboard page rather than the Admin zone, so they add zero
    here. Net zero.

    28 -> 29: "Approval Inbox" added to every persona's My work, platform_admin
    included. It is a genuinely new, intentional link — the one queue for every
    pending change proposal, shared by every persona with GENERAL permission.

    29 -> 30 (R1-B56, 6 Oct 2026): "Agent Registry" added to platform_admin's
    My work — the one registry recording each agent's owner, charter version
    and delegated limits, closest existing persona to the brief's
    "Organisation Administrator".
    """
    assert len(_all_links(ROLE_PLATFORM_ADMIN)) == 30


def test_platform_admin_collapsed_sidebar_icons_are_unambiguous():
    """A collapsed rail must not use one glyph for different destinations."""
    icons = [link["icon"] for link in _all_links(ROLE_PLATFORM_ADMIN)]
    duplicates = {icon: count for icon, count in Counter(icons).items() if count > 1}
    assert duplicates == {}


def test_get_sidebar_zones_resolves_role():
    user = _StubUser(ROLE_SOLUTION_ARCHITECT)
    zones = get_sidebar_zones(user)
    assert zones == SIDEBAR_ZONES[ROLE_SOLUTION_ARCHITECT]


def test_get_sidebar_zones_defaults_for_unknown_role():
    user = _StubUser("not_a_real_role")
    zones = get_sidebar_zones(user)
    assert zones == SIDEBAR_ZONES[ROLE_SOLUTION_ARCHITECT]


def test_get_sidebar_zones_never_raises_for_none_user():
    zones = get_sidebar_zones(None)
    # Must not raise and must return the default role's zone structure.
    assert len(zones) >= 3  # home, my_work, library at minimum
    zone_names = {z["zone"] for z in zones}
    assert zone_names >= {"home", "my_work", "library"}


def test_admin_zone_hidden_without_is_platform_admin():
    """13 Aug 2026 QA finding: enterprise_role defaults to the string
    "platform_admin" for every user (backward-compat column default), which
    put the whole Admin nav zone — including /admin/organizations, a
    genuinely cross-tenant route — in front of ordinary users who never
    explicitly picked a role. The zone must now track the real
    is_platform_admin boolean (defaults False), not the string default.
    """
    user = _StubUser(ROLE_PLATFORM_ADMIN, is_platform_admin=False)
    zones = get_sidebar_zones(user)
    assert "admin" not in {z["zone"] for z in zones}


def test_admin_zone_shown_for_real_platform_admin():
    user = _StubUser(ROLE_PLATFORM_ADMIN, is_platform_admin=True)
    zones = get_sidebar_zones(user)
    assert "admin" in {z["zone"] for z in zones}


IMPACT_PERSONAS = {ROLE_SOLUTION_ARCHITECT, ROLE_ENTERPRISE_ARCHITECT, ROLE_BUSINESS_ARCHITECT}


@pytest.mark.parametrize("role", sorted(SIDEBAR_ZONES))
def test_impact_analysis_is_in_my_work_for_exactly_the_architect_personas(role):
    """The reported problem: the three architect personas get it under My work; nobody else gains it."""
    assert ("Impact Analysis" in _my_work_labels(role)) == (role in IMPACT_PERSONAS)


@pytest.mark.parametrize("role", sorted(IMPACT_PERSONAS))
def test_impact_analysis_link_is_the_same_link_and_icon_for_every_persona(role):
    link = next(entry for entry in _all_links(role) if entry["label"] == "Impact Analysis")
    assert link["endpoint"] == "strategic.impact_analysis"
    assert link["icon"] == "crosshair"


@pytest.mark.parametrize("role", sorted(IMPACT_PERSONAS))
def test_architect_collapsed_sidebar_icons_are_unambiguous(role):
    """A collapsed rail must not use one glyph for different destinations (adding a link must not break this).

    Judged per destination, not per link: business_architect's My work "Capability Map" and Library
    "Capabilities" are the same endpoint with the same glyph, which is a duplicate link (a separate,
    product-level question) rather than two buttons that look alike but go to different pages."""
    endpoints_by_icon = {}
    for link in _all_links(role):
        endpoints_by_icon.setdefault(link["icon"], set()).add(link["endpoint"])
    assert {i: sorted(e) for i, e in endpoints_by_icon.items() if len(e) > 1} == {}
