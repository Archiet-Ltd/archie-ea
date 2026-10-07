"""
Role-Based Navigation Access Control (NS-006)

Defines which navigation sections each enterprise_role can access.
Used by admin_sidebar.html to filter navigation items.

Part of North Star Persona MVP implementation.
ADR Reference: docs/adr/0009-persona-based-navigation.md
"""

from typing import Dict, List, Set

from app.models.user import (
    ROLE_APPLICATION_MANAGER,
    ROLE_ARB_MEMBER,
    ROLE_BUSINESS_ARCHITECT,
    ROLE_CTO,
    ROLE_ENTERPRISE_ARCHITECT,
    ROLE_DATA_ARCHITECT,
    ROLE_PLATFORM_ADMIN,
    ROLE_SECURITY_ARCHITECT,
    ROLE_PORTFOLIO_MANAGER,
    ROLE_PROCUREMENT,
    ROLE_SOLUTION_ARCHITECT,
    ROLE_FINANCE,
    ROLE_COMPLIANCE,
    ROLE_RISK,
    ROLE_OPERATIONS,
    ROLE_NON_TECHNICAL_OWNER,
)


# Navigation sections defined in the sidebar
NAVIGATION_SECTIONS = [
    "home",
    "solutions",
    "portfolio",
    "architecture",
    "capabilities",
    "business_architecture",
    "roadmaps",
    "governance",
    "procurement",
    "my_applications",
    "data_integration",
    "administration",
    # G6 (register close, 1 Sep 2026): the regulatory-compliance surface
    # (RegulatoryFramework / ComplianceControl) had no section of its own, so no
    # role could be granted it. security_architect owns it; see its
    # ROLE_SECTION_ACCESS entry and the "Compliance" link in its _MY_WORK_LINKS.
    "compliance",
]

# Role to sections mapping
# Each role has a set of sections they can access
ROLE_SECTION_ACCESS: Dict[str, Set[str]] = {
    ROLE_SOLUTION_ARCHITECT: {
        "home",
        "solutions",
        "portfolio",
        "architecture",
        "capabilities",
        "roadmaps",
        "governance",
        "data_integration",
    },
    ROLE_ENTERPRISE_ARCHITECT: {
        "home",
        "solutions",
        "portfolio",
        "architecture",
        "capabilities",
        "business_architecture",
        "roadmaps",
        "governance",
        "data_integration",
    },
    ROLE_BUSINESS_ARCHITECT: {
        "home",
        "business_architecture",
        "capabilities",
        "architecture",
        "roadmaps",
        "governance",
        "portfolio",
        "solutions",
        "data_integration",
    },
    ROLE_ARB_MEMBER: {
        "home",
        "solutions",
        "portfolio",
        "governance",
    },
    ROLE_PORTFOLIO_MANAGER: {
        "home",
        "solutions",
        "portfolio",
        "capabilities",
        "roadmaps",
        "governance",
        "procurement",  # Read-only access to procurement for cost visibility
        # R1-B34 (TB-0135): owns the Formula Register (reviews/versions the
        # composite-score weights) -- see its _link() in this persona's zone
        # below.
        "portfolio_management",
    },
    ROLE_CTO: {
        "home",
        "solutions",
        "portfolio",
        "capabilities",
        "roadmaps",
        "governance",
    },
    ROLE_PROCUREMENT: {
        "home",
        "portfolio",  # Read-only for app-vendor context
        "procurement",
    },
    ROLE_APPLICATION_MANAGER: {
        "home",
        "solutions",  # Read-only for impact awareness
        "portfolio",  # Read-only for integration context
        "my_applications",
        "roadmaps",  # Read-only
    },
    ROLE_PLATFORM_ADMIN: {
        "home",
        "solutions",
        "portfolio",
        "architecture",
        "capabilities",
        "business_architecture",
        "roadmaps",
        "governance",
        "procurement",
        "my_applications",
        "data_integration",
        "administration",
        "portfolio_management",
    },
    # G6 (register close, 1 Sep 2026): security_architect and data_architect
    # were promoted to first-class roles (VALID_ROLES, own charters, own sidebar
    # zones) but were never added here, so can_access_section() / the module
    # directory treated them as zero-access — an empty directory for a role that
    # otherwise had a full sidebar. These sets are additive and strictly scoped:
    # neither carries "administration" (the platform_admin power) or the
    # procurement vendor/spend surface.
    ROLE_SECURITY_ARCHITECT: {
        "home",
        "governance",
        "capabilities",
        "architecture",
        "data_integration",
        "compliance",
    },
    ROLE_DATA_ARCHITECT: {
        "home",
        "architecture",
        "capabilities",
        "data_integration",
        "governance",
    },
    # R1-B36 (TB-0146): finance, compliance, risk, operations and
    # non_technical_owner promoted from unassignable to assignable.
    ROLE_FINANCE: {
        "home",
        "procurement",
        "portfolio",
    },
    ROLE_COMPLIANCE: {
        "home",
        "procurement",
        "governance",
        "compliance",
    },
    ROLE_RISK: {
        "home",
        "architecture",
        "governance",
    },
    ROLE_OPERATIONS: {
        "home",
    },
    ROLE_NON_TECHNICAL_OWNER: {
        "home",
        "portfolio",
    },
}

# Sections that require specific roles (exclusive access).
#
# Documentary only: can_access_section() below reads ROLE_SECTION_ACCESS (role
# -> set of sections), not this dict, and nothing in the codebase reads
# EXCLUSIVE_SECTIONS itself (confirmed by search) -- the actual gate for every
# section named here is its membership in ROLE_SECTION_ACCESS[role] above.
# Kept in the same role-list shape as a human-readable index of which
# sections are role-exclusive; if you are adding a new exclusive section,
# the line that must change is the role's entry in ROLE_SECTION_ACCESS, not
# this one.
EXCLUSIVE_SECTIONS: Dict[str, List[str]] = {
    "administration": [ROLE_PLATFORM_ADMIN],
    "procurement": [ROLE_PROCUREMENT, ROLE_PORTFOLIO_MANAGER, ROLE_PLATFORM_ADMIN],
    "my_applications": [ROLE_APPLICATION_MANAGER, ROLE_PLATFORM_ADMIN],
    # R1-B34 (TB-0135): Formula Register -- reviewed/versioned by
    # portfolio_manager; platform_admin sees everything.
    "portfolio_management": [ROLE_PORTFOLIO_MANAGER, ROLE_PLATFORM_ADMIN],
}

# Default role if user has no enterprise_role set
DEFAULT_ROLE = ROLE_SOLUTION_ARCHITECT


# One cost-visibility rule.  Every surface that redacts financial
# figures (cost, budget, TCO, licence unit cost) checks this single set.
# Previously each surface maintained its own copy of the same three roles;
# a fourth surface that forgot to update its copy would leak cost data.
# Import this constant — do not define a second list.
COST_VISIBILITY_ROLES: frozenset = frozenset({
    ROLE_CTO,
    ROLE_PORTFOLIO_MANAGER,
    ROLE_PLATFORM_ADMIN,
})


def get_user_role(user) -> str:
    """Get user's enterprise role with fallback to default.

    Must never raise. Since the shipped sidebar started calling
    can_access_section(), this runs while rendering EVERY authenticated page, so
    an exception here 500s the whole application rather than one feature.

    Reading the attribute can fail for reasons that have nothing to do with
    roles: a detached or expired instance re-fetches on access, and if the row
    has gone (or the session was rolled back mid-request) SQLAlchemy raises
    ObjectDeletedError. That is exactly what happened on the applications list
    error path - the view caught its own failure and re-rendered the template,
    and the sidebar then turned a handled error into an unhandled 500.

    `hasattr` does not protect against this: it only swallows AttributeError.
    """
    if not user:
        return DEFAULT_ROLE
    try:
        return getattr(user, "enterprise_role", None) or DEFAULT_ROLE
    except Exception:  # noqa: BLE001 - a nav gate must not be able to 500 a page
        return DEFAULT_ROLE


def can_access_section(user, section: str) -> bool:
    """
    Check if user can access a navigation section.

    Args:
        user: User object with enterprise_role attribute
        section: Navigation section identifier

    Returns:
        True if user can access the section
    """
    role = get_user_role(user)
    allowed_sections = ROLE_SECTION_ACCESS.get(role, set())
    return section in allowed_sections


def get_visible_sections(user) -> List[str]:
    """
    Get list of navigation sections visible to user.

    Args:
        user: User object with enterprise_role attribute

    Returns:
        List of section identifiers user can see
    """
    role = get_user_role(user)
    allowed_sections = ROLE_SECTION_ACCESS.get(role, set())
    # Return in defined order
    return [s for s in NAVIGATION_SECTIONS if s in allowed_sections]


def is_admin(user) -> bool:
    """Check if user has admin role.

    The system of record for "is an administrator" is
    Permission.ADMINISTER via user.is_admin().  This function delegates to it
    rather than re-deriving the answer from enterprise_role, so every caller
    that uses this accessor shares one authority.
    """
    try:
        return bool(user.is_admin())
    except Exception:  # noqa: BLE001 - a nav gate must not be able to 500 a page
        return False


# Roles whose job is to author the capability model. The capability pages used
# to gate their create/edit controls on `role_archetype`, which is an optional
# ONBOARDING answer and is NULL for every user who did not complete that flow --
# so a business_architect, the persona that exists to own this model, saw a
# read-only page and had no way to create a capability anywhere in the product.
CAPABILITY_EDITOR_ROLES: Set[str] = {
    ROLE_PLATFORM_ADMIN,
    ROLE_ENTERPRISE_ARCHITECT,
    ROLE_BUSINESS_ARCHITECT,
    ROLE_SOLUTION_ARCHITECT,
}


def can_edit_capabilities(user) -> bool:
    """True when the user's enterprise_role owns the capability model.

    Kept tolerant of the legacy signals (is_admin(), role_archetype) so users
    created before enterprise_role existed do not lose an ability they had.
    """
    if not user:
        return False
    if get_user_role(user) in CAPABILITY_EDITOR_ROLES:
        return True
    try:
        if user.is_admin():
            return True
    except Exception:  # noqa: BLE001 - a template gate must not 500 a page
        pass
    try:
        return (getattr(user, "role_archetype", None) or "") == "architect"
    except Exception:  # noqa: BLE001
        return False


def is_procurement(user) -> bool:
    """Check if user has procurement role."""
    return get_user_role(user) == ROLE_PROCUREMENT


def is_application_manager(user) -> bool:
    """Check if user has application manager role."""
    return get_user_role(user) == ROLE_APPLICATION_MANAGER


def get_role_display_name(role: str) -> str:
    """Get human-readable name for role."""
    from app.models.user import ROLE_DISPLAY_NAMES
    return ROLE_DISPLAY_NAMES.get(role, role.replace("_", " ").title())


def get_all_roles_with_access(section: str) -> List[str]:
    """Get all roles that can access a section."""
    return [
        role for role, sections in ROLE_SECTION_ACCESS.items()
        if section in sections
    ]


# ---------------------------------------------------------------------------
# Persona sidebar zones (shell-overhaul Wave 1)
#
# Single source of truth for the server-filtered sidebar: role -> ordered
# zones -> links. app/templates/components/admin_sidebar.html renders from
# get_sidebar_zones() only (Task 3) instead of the 1,062-line hand-maintained
# template that dimmed out-of-role links client-side. Endpoint strings below
# were resolved by grepping the pre-rewrite admin_sidebar.html for the real
# url_for(...) target of each spec surface; a few spec-named surfaces
# (Portfolio, Investment Analysis) have a real registered blueprint endpoint
# but no existing sidebar link — those are noted in the Task 2 report.
#
# Spec: docs/superpowers/specs/2026-08-12-shell-overhaul-design.md section 1.
#
# Fix round (Task 3 review): raised 25 -> 26. platform_admin's two new,
# review-mandated admin-zone links (Salesforce Integration, Power Platform)
# alone already render exactly 25 real links; the All-modules directory link
# (see _LIBRARY_LINKS_WITH_DIRECTORY below) is mandatory on every role's
# sidebar and platform_admin has no headroom left to absorb it without either
# dropping one of those two links or raising the budget by exactly the one
# link being added. Every other role stays well under 25 either way — see
# scripts/check_sidebar_links.py's per-role table.
#
# Fix round (evidence review, Wave 1 screenshot pass): lowered 26 -> 25.
# _MY_WORK_LINKS[ROLE_PLATFORM_ADMIN] carried an "Applications" link pointing
# at unified_applications.application_list — the exact same endpoint already
# in _LIBRARY_LINKS, so platform_admin rendered the label twice. Dropping the
# duplicate takes My work from 3 links to 2, so platform_admin's real link
# count (header + 22 zone links + footer All-modules + footer logout) is now
# 25, one below the old ceiling; the budget is lowered to match rather than
# left slack that would silently hide a future regression the same size.

# S-11 (discoverability wave, 18 Aug 2026): raised 25 -> 26. One governance
# link was added ("Decisions" -> arch_decisions.list_decisions), which is the
# only zone platform_admin — the role with zero headroom — shares. Its
# zone-only total goes 22 -> 23 and its rendered total 25 -> 26. The two
# Implementation & Migration links added in the same pass (Gap Analysis, Work
# Packages) land in the enterprise_architect My-work zone only, which has
# ample headroom, so they do not move this number.
#
# S-11 remainder (18 Aug 2026, QA Update 6/8): raised 26 -> 27. Ten modules
# were reachable only from /modules/, never from a sidebar zone. Nine of them
# landed in EA / business_architect / portfolio_manager My-work zones, all of
# which had headroom; the tenth ("Batch Import") landed in platform_admin's
# admin zone, the only zone that role shares, moving its zone-only total
# 23 -> 24 and rendered total 26 -> 27. The remaining three (my-applications
# list/health/roadmap) were nested as in-page tabs under the existing
# "My Applications" sidebar link rather than given zone entries of their own
# — see app/modules/my_applications/templates/my_applications/*.html.
#
# BA-A1 (21 Aug 2026): raised 27 -> 28. The Business Architecture landing page
# is the front door to all twelve business-architecture outputs, and an
# evaluating architect had concluded three of them did not exist because there
# was no such door. platform_admin is the default enterprise_role for anyone who
# never picked one, so it is the role that most needs the link, and its only
# shared zone (admin) was already exactly on the ceiling. The alternative was to
# retire "Batch Import", but that link exists to satisfy the S-11 finding above
# — trading one discoverability defect for another. Raising the budget by one is
# the honest cost of adding a front door.
#
# In-built error telemetry (10 Sep 2026, commit e7e36195) added platform_admin's
# "Errors" link (see this file's _ADMIN_LINKS) without raising this constant or
# the paired pinned test in tests/test_sidebar_budgets.py — both silently went
# stale and only surfaced as a CI failure during the Phase 0 archie-ea to-be
# plan's CI audit (fix/phase0-ci-and-audit). Measured directly rather than
# reconstructed from history (the exact link-by-link arithmetic in the comments
# above has a small pre-existing drift this fix does not attempt to unwind):
# platform_admin currently renders 30 links. Raising 28 -> 30 to match.
#
# The Ask page's front door ("Ask a question", _ASK_LINK below) is one link in
# every persona's My work, so platform_admin renders one more: 30 -> 31. The
# page is the entry point of the impact question for every persona and has no
# other route in; the Twin map is reached from the Ask page and has no link of
# its own, so this is the only link the two pages add.
#
# Canvas/framework UI fix, round 2 (25 Sep 2026): two new Library links
# ("Canvases", "Frameworks") are shared by every role. The ceiling stays 31 --
# the round-1 attempt raised it to 34 without first checking whether the
# addition actually needed headroom; it did not, once paired with folds. The
# two roles with none left (business_architect, platform_admin) and the one
# with a single link of headroom (enterprise_architect) each lose exactly one
# thing to pay for the two new links; see _MY_WORK_LINKS and _ADMIN_LINKS
# comments below for which link and where it is still reachable. Framework
# Management and Framework Configuration (platform_admin-only) are reachable
# from the admin dashboard page (app/templates/admin/index.html) instead of a
# third and fourth new Admin-zone sidebar entry, which is why they add zero to
# every role's rendered count.
#
# Approval Inbox (4 Oct 2026): raised 31 -> 32. The Approval Inbox is one new
# link in every persona's My work — the single queue for every pending change
# proposal, shared by every persona with GENERAL permission. platform_admin
# (the role with zero headroom) renders it like every other role, moving its
# zone-only total 28 -> 29 and its rendered total 31 -> 32. No fold is
# available: the Admin zone already shed four links in the canvas/framework
# round, and the My-work zone carries only four links (plus Ask). Raising the
# budget by one is the honest cost of adding a genuinely new, intentional link
# that every persona needs.
# R1-B56: Agent Registry (owner, charter, delegated limits per registered
# agent) is a new, genuinely needed platform_admin-only screen, not a
# duplicate of anything already in the Admin zone. No fold is available for
# the same reason as above; taking the budget 32 -> 33.
SIDEBAR_LINK_BUDGET = 33

_ZONE_TITLES = {
    "home": "Home",
    "my_work": "My work",
    "library": "Library",
    "governance": "Governance",
    "admin": "Admin",
}


def _zone(zone_key, links):
    return {"zone": zone_key, "title": _ZONE_TITLES[zone_key], "links": links}


def _link(label, endpoint, icon, requires=None, query_params=None):
    """A sidebar link. ``requires`` names the guard the route enforces so the
    sidebar can drop links the user cannot reach — a link that 403s is a dead
    end, and the 2 Sep 2026 browser audit found seven of them (F-11):
      "admin"          — route is @admin_required (Permission.ADMINISTER)
      "platform_admin" — route is @platform_admin_required (the cross-tenant
                         is_platform_admin super-admin flag)
      "data_subject_requests" — routes are @requires_role(DATA_SUBJECT_REQUEST_ROLES)
                         (security_architect, and platform_admin as always)
      "general"        — route requires Permission.GENERAL (require_roles()
                         only grants access when current_user.can(GENERAL)
                         holds), which a read-only Viewer role (permissions=0)
                         fails even though enterprise_role puts the link in
                         their zone — see Policy Monitoring below.
    Navigation must be driven by the same predicate the route checks, not by
    enterprise_role alone.

    ``query_params`` is an optional dict passed straight to
    ``url_for(endpoint, **query_params)`` in the sidebar templates — used by
    "ArchiMate Composer" to open directly into the layered viewpoint instead
    of a blank canvas (composer_page's own ``?viewpoint=`` mechanism)."""
    d = {"label": label, "endpoint": endpoint, "icon": icon}
    if requires:
        d["requires"] = requires
    if query_params:
        d["query_params"] = query_params
    return d


# The way in to the Ask page, first in every persona's My work so it sits in the
# same place for everyone. One shared definition: the label, endpoint and icon
# cannot drift apart between personas.
_ASK_LINK = _link("Ask a question", "intelligence_ui.ask", "search")

# Approval Inbox — one queue for every pending change proposal.
# Shared definition so the label, endpoint and icon cannot drift apart between
# personas. Requires GENERAL permission (write/approval access) so Viewer roles
# do not see a link that 403s.
_APPROVAL_INBOX_LINK = _link(
    "Approval Inbox", "unified_ai_chat.approval_inbox", "inbox", requires="general"
)

_HOME_LINKS = [
    _link("Dashboard Overview", "dashboard.overview", "layout-dashboard"),
    _link("Health Scorecard", "dashboard.health_scorecard", "heart-pulse"),
]

_LIBRARY_LINKS = [
    _link("Applications", "unified_applications.application_list", "list"),
    _link("Capabilities", "capability_map.index", "map"),
    _link("Canvases", "business_model.index", "layout-grid"),
    # "layers" collided with an existing link's icon in the same collapsed
    # menu for two roles -- portfolio_manager's "Consolidation List" and
    # data_architect's "Capability Map" both already used it, and this link
    # is shared into every role's Library zone, so it must not match any
    # icon used anywhere else in role_access.py. "library" is unused
    # elsewhere in this file and reads directly as "a library of frameworks".
    _link("Frameworks", "maturity_management.frameworks_overview", "library"),
    _link("Vendors", "unified_applications.vendors", "building"),
    _link("Architecture", "archimate_crud.dashboard", "table"),
    _link("Diagrams", "archimate.diagrams_library", "layout-panel-top"),
]

# Fix round: the design's stated long-tail fallback ("Ctrl-K search + one new
# 'All modules' directory page") didn't exist — Ctrl-K is a visual hint with
# no wired event, and there was no directory page. app/modules/modules_directory
# is that page; this is the last Library link for every role except
# platform_admin, which is already exactly at SIDEBAR_LINK_BUDGET once its two
# new admin-zone links are added (see _ADMIN_LINKS below) and would go over if
# a fifth Library link were added too. admin_sidebar.html renders this link in
# the sidebar footer instead for whichever role's SIDEBAR_ZONES don't already
# contain it — currently just platform_admin — so it is still reachable from
# every role's sidebar, just not always from the same zone.
_ALL_MODULES_LINK = _link("All modules", "modules_directory.index", "grid-3x3")
_LIBRARY_LINKS_WITH_DIRECTORY = _LIBRARY_LINKS + [_ALL_MODULES_LINK]

_GOVERNANCE_LINKS = [
    _link("ARB Dashboard", "arb.dashboard", "gavel"),
    _link("Reviews", "arb.reviews", "shield-check"),
    _link("Sessions", "arb.sessions", "calendar"),
    # S-11: architecture decisions were reachable from nowhere in the sidebar.
    # `arch_decisions.list_decisions` is the canonical of two listings over the
    # same `architecture_decisions` table — it is the tenant-scoped one (its
    # model carries TenantMixin) and the one every template links to. The
    # duplicate, `adrs.list_adrs`, now 302s here; see
    # app/modules/architecture/routes/adr_routes.py:list_adrs.
    _link("Decisions", "arch_decisions.list_decisions", "file-check-2"),
]

_ADMIN_LINKS = [
    _link("Command Center", "admin.index", "command"),
    _link("Users", "admin.registered_users", "users"),
    # Cross-tenant: the route is @platform_admin_required, not merely admin.
    _link("Organizations", "admin.organizations_list", "building-2", requires="platform_admin"),
    _link("API Settings", "admin.api_settings", "key", requires="org_admin"),
    # NAV-1 (27 Aug 2026): repointed from `solution_prompt_admin.
    # solution_prompts_page` to `admin.solution_prompts_page`. Both blueprints
    # register the SAME rule, /admin/solution-prompts, and the admin (v2,
    # guardrail-enabled) one wins the URL map — so the endpoint this link used
    # to name could never actually be served. url_for() resolved it, the page
    # looked fine, and the handler behind the name was dead code. Found by the
    # nav-verified gate: no test had ever exercised that endpoint, and none
    # could. The link now names the handler that actually runs.
    _link("AI Prompts", "admin.solution_prompts_page", "sparkles"),
    _link("Governance Gates", "admin.governance_gates", "badge-check", requires="admin"),
    # The organisation's audit trail (query, export in full, verify). It takes
    # Seed Management's place in this zone to hold the link budget; Seed
    # Management stays one click away as a tile on Command Center
    # (app/templates/admin/index.html).
    _link("Audit Log", "admin.audit_log_viewer", "scroll-text", requires="admin"),
    _link("Settings", "main.settings", "settings"),
    # Added in the Task 3 fix round (review finding: orphaned real routes —
    # both existed, worked, and had no sidebar link of any kind).
    _link("Salesforce Integration", "admin.salesforce_integration", "cloud"),
    _link("Power Platform", "admin.power_platform_integration", "blocks"),
    # Canvas/framework UI fix, round 2 (25 Sep 2026): "Import History" and
    # "Batch Import" (S-11 remainder, 18 Aug 2026) folded out of this zone --
    # platform_admin has zero headroom left once "Canvases" and "Frameworks"
    # join every role's Library zone. Both are tiles on the admin dashboard
    # page instead (app/templates/admin/index.html, "Frameworks & Data"
    # section), one click from Command Center rather than a direct sidebar
    # entry. Framework Management and Framework Configuration are tiles on
    # the same dashboard page rather than two more Admin-zone links, for the
    # same reason -- the platform admin still reaches all four from Command
    # Center, which is itself the first link in this zone.
    # In-built error telemetry (10 Sep 2026): the owner's "how do we know the
    # system has silently degraded" question, answered without a paid APM.
    # Cross-tenant like Organizations above -- the route is @platform_admin_required.
    _link("Errors", "error_events.errors_dashboard", "alert-triangle", requires="platform_admin"),
]

# Per-role "My work" — the persona's primary surface, 3-6 items.
# SA / EA / CTO membership below is pinned verbatim to the spec's zone table
# (docs/superpowers/specs/2026-08-12-shell-overhaul-design.md section 1) and
# asserted exactly by tests/test_sidebar_budgets.py.
_MY_WORK_LINKS = {
    ROLE_SOLUTION_ARCHITECT: [
        _link("Architecture Journey", "architecture_journey.index", "compass"),
        _APPROVAL_INBOX_LINK,
        _link("Solutions", "solution_design.list_solutions", "wrench"),
        _link("AI Chat", "unified_ai_chat.index", "message-square"),
        _link("ADM Kanban", "adm_kanban_view.index", "kanban"),
        # Fix round: Programmes was reachable from nowhere in the sidebar.
        # A solution architect whose job is to take designs through
        # governance had no route to the review board from their own
        # sidebar. Found 31 Aug 2026 by the task-completion walkthrough:
        # every endpoint worked and every journey test passed, because
        # they address the ARB by URL. The persona could not find it.
        _link("Review Board", "arb.dashboard", "gavel"),
        _link("Programmes", "solution_design.programmes_list", "git-merge"),
        # SAP S/4HANA Interface Register (Task 02) — label deliberately avoids
        # "Integrations", which already names the outbound-connector admin
        # surface; "cable" is distinct from the git-merge/git-branch/waypoints/
        # milestone icons already in this zone.
        _link("Interface Register", "interface_register.index", "cable"),
        # Reported problem: the full Impact Analysis page was reachable only from
        # the All-modules page. "Ask a question" already answers one cross-layer
        # ripple-effect question from the top of My work, but not the full
        # analysis surface this links to. Same endpoint and icon as
        # enterprise_architect's link, which already had it (S-11, 18 Aug 2026).
        _link("Impact Analysis", "strategic.impact_analysis", "crosshair"),
    ],
    ROLE_ENTERPRISE_ARCHITECT: [
        _link("Transformation programmes", "solution_design.programmes_list", "waypoints"),
        _APPROVAL_INBOX_LINK,
        # BA-A3 (21 Aug 2026, re-measured 27 Aug 2026): the
        # /business-architecture landing page is deliberately NOT here.
        # enterprise_architect renders 25 sidebar links, which is the
        # `sidebar_links` ratchet's value exactly — adding a 26th raises the
        # ratchet, which is a regression, not a cleanup. The page is not lost to
        # this persona: EA's zones already carry Capabilities (Library), Gap
        # Analysis, Work Packages, Traceability Matrix, Capability Health,
        # Roadmaps and Data Architecture directly, which is most of what the
        # landing page fronts, and platform_admin — the default enterprise_role
        # for any user who never picked one — does carry the link. Give EA this
        # link only in the same change that retires one of its existing 12
        # My-work links.
        _link("Portfolio", "portfolio.index", "briefcase"),
        # NAV-1 (27 Aug 2026): "Capability Map" was removed from this zone. It
        # pointed at `capability_map.index` — the exact endpoint Library already
        # carries as "Capabilities" for every role — so this persona rendered the
        # same page twice under two labels, the same defect the platform_admin
        # "Applications" duplicate had. Nothing is lost: the page is still one
        # click away in Library. Removing it took the rendered total 26 -> 25 and
        # is what paid for the nav-coverage links added below without any persona
        # losing a feature.
        _link("Roadmaps", "main.capability_roadmap", "milestone"),
        # Opens directly into the enterprise-wide Layered viewpoint instead
        # of a blank "Unsaved diagram" canvas.
        _link("ArchiMate Composer", "archimate.composer_page", "pen-tool",
              query_params={"viewpoint": "layered"}),
        _link("Traceability Matrix", "architect_ui.traceability_matrix", "git-branch"),
        # S-11: both are real, working Implementation & Migration pages that
        # were reachable from nowhere in the sidebar. Gap Analysis here is
        # `enterprise.gap_analysis` (the ArchiMate `Gap` register), not
        # `adm_kanban_view.gap_analysis` (KanbanCard rows on the ADM board,
        # already linked from that board). Work Packages is an Alpine table
        # over /enterprise/api/work-packages.
        _link("Gap Analysis", "enterprise.gap_analysis", "git-compare"),
        _link("Work Packages", "enterprise.work_packages", "package"),
        # S-11 remainder (18 Aug 2026, QA Update 6/8): these were
        # directory-only — reachable from /modules/ but from no sidebar zone
        # of any role. Both are EA-shaped working pages.
        # crosshair, not git-branch: Traceability Matrix in this same zone
        # already uses git-branch, and collapsed to a 4rem rail two entries
        # behind one glyph are the same button.
        _link("Impact Analysis", "strategic.impact_analysis", "crosshair"),
        _link("Capability Health", "strategic.capability_health", "activity"),
        # "Duplicate Detection" (unified_duplicate.simple_dashboard) moved out
        # of this zone in the canvas/framework UI fix, round 2 (25 Sep 2026):
        # this zone has one link of headroom and "Canvases" plus "Frameworks"
        # joining every role's Library zone need two. It moved rather than
        # dropped -- see ROLE_PORTFOLIO_MANAGER below, the persona that owns
        # the Rationalization workflow it is reached from in context
        # (app/templates/applications/rationalization.html); it stays in a
        # sidebar zone, just not this one, so it is not directory-only again.
        # ARCH-123 / ARCH-124 (QA register closure, 18 Aug 2026): the Data
        # Architect and Technical Architect personas the register flagged as
        # underserved are folded into enterprise_architect here — there is no
        # dedicated role for either yet. Data Architecture already existed
        # (models + dashboard) but was reachable from nowhere in the
        # sidebar; Tech Radar is new. Both are now linked.
        _link("Data Architecture", "data_architecture.data_architecture_dashboard", "workflow"),
        _link("Tech Radar", "tech_radar.index", "radar"),
        # Model history: as-of snapshot and changes between dates. Enterprise
        # architect is the persona that owns the capability model and needs
        # to audit its evolution.
        _link("Model as of", "intelligence_ui.history_as_of_page", "clock"),
        _link("Changes", "intelligence_ui.history_changes_page", "history"),
    ],
    ROLE_CTO: [
        # A CTO with no route to a roadmap from their own sidebar. Found
        # 31 Aug 2026 by the task-completion walkthrough: the page exists and
        # every journey test passes, because those address it by URL. This
        # persona could not find it from their landing page.
        _link("Roadmaps", "main.capability_roadmap", "milestone"),
        _APPROVAL_INBOX_LINK,
        _link("Transformation programmes", "solution_design.programmes_list", "waypoints"),
        _link("Health Scorecard", "dashboard.health_scorecard", "heart-pulse"),
        _link("Rationalization", "unified_applications.rationalization_dashboard", "git-merge"),
        _link("Investment Analysis", "architecture.investment_priorities", "target"),
        # NAV-1 (27 Aug 2026, nav-coverage output 9 — KPI/metric dashboards).
        # /dashboard/rationalization/scorecard is a real executive KPI page
        # (TCO coverage, cost tiers, rationalization posture) that no persona's
        # sidebar linked to. Given to the two roles whose job it is.
        _link("Portfolio KPIs", "dashboard_pages.rationalization_scorecard", "gauge"),
        # Level 10 walkthrough, 30 Aug 2026: the radar is the CTO's technology
        # direction instrument, and /technology/radar/classify names "cto" in
        # its own require_roles list -- so the persona was authorised to set
        # adopt/trial/assess/hold and had no link to the page from anywhere in
        # its sidebar. 28 nav links on the CTO dashboard, none of them this.
        # Finding a page by grepping the source is not finding it.
        _link("Tech Radar", "tech_radar.index", "radar"),
        # Ownership coverage by business unit — CTO accountability.
        _link("Ownership Coverage", "unified_applications.ownership_coverage", "users", requires="cto_or_portfolio_manager"),
        # R1-B03 PR 2: the one ownership record now also covers capabilities.
        # Ample headroom in this zone (10 links against SIDEBAR_LINK_BUDGET 32).
        _link("Capabilities With No Owner", "capability_map.capabilities_no_owner", "user-x", requires="cto_or_portfolio_manager"),
        # R1-B85: supported-estate share, open exceptions, the store-
        # agreement disagreement finder.
        _link("CTO Scorecard", "cto_scorecard.index", "clipboard-list"),
    ],
    ROLE_BUSINESS_ARCHITECT: [
        # BA-A1/A2. This persona had 4 links against a budget of 27 while
        # enterprise_architect had 13, so most of what a business architect
        # needs existed and was reachable only by typing a URL. An evaluating
        # architect concluded outright that capability maturity, gap analysis
        # and strategy-to-execution were not built. They are; 350 routes serve
        # them. Nothing below is a new page — every endpoint already ships and
        # is already in another persona's zones.
        #
        # BA-A3. The front door, deliberately first: the persona's problem was
        # never that a page was missing, it was that twelve outputs were spread
        # over five generic zones with no page that presents them as one
        # practice. /business-architecture is that page.
        _link("Architecture Journey", "architecture_journey.index", "compass"),
        _APPROVAL_INBOX_LINK,
        # "Capability Map" folded out in the canvas/framework UI fix, round 2
        # (25 Sep 2026): it pointed at capability_map.index, the exact
        # endpoint Library already carries as "Capabilities" for every role
        # (see the enterprise_architect NAV-1 note above, which made the same
        # fix for the same reason) -- this persona rendered the identical page
        # under two labels. This zone has no headroom left once "Canvases" and
        # "Frameworks" join the shared Library zone; dropping a same-page
        # duplicate loses nothing.
        # Points at the heatmap, NOT frameworks_overview. That was the only
        # maturity link this persona had, it is labelled "Frameworks" rather
        # than "Maturity", and it lands on the one maturity page that renders
        # near-empty (the framework taxonomy does not match the categories the
        # data actually carries — BA-12). Clicking the single maturity link and
        # finding nothing is precisely why maturity was reported as missing.
        _link("Capability Maturity", "maturity_management.maturity_heatmap", "thermometer"),
        _link("Value Streams", "value_stream.index", "waypoints"),
        # The value streams that depend on a capability below a maturity
        # threshold, answered by the intelligence API. Sits under Value
        # Streams, the page where the capability links it reads are made.
        # 28 -> 29 rendered links, within SIDEBAR_LINK_BUDGET (31).
        _link("Value Streams at Risk", "intelligence_ui.value_streams_at_risk", "trending-down"),
        _link("Stakeholder Map", "stakeholder_map.stakeholder_map_page", "users"),
        _link("Gap Analysis", "enterprise.gap_analysis", "search-x"),
        _link("Roadmaps", "main.capability_roadmap", "milestone"),
        _link("Work Packages", "enterprise.work_packages", "package"),
        _link("Traceability Matrix", "architect_ui.traceability_matrix", "git-compare"),
        _link("Capability Health", "strategic.capability_health", "activity"),
        # Same link and icon as enterprise_architect's.
        _link("Impact Analysis", "strategic.impact_analysis", "crosshair"),
        _link("Data Architecture", "data_architecture.data_architecture_dashboard", "database"),
        # NAV-1 (27 Aug 2026, nav-coverage gate 4 -> 0). Three of Iain's twelve
        # business-architecture outputs had working routes and no sidebar link
        # anywhere, in any persona — which is why an evaluating architect read
        # them as absent. All three land on pages that already ship; none is new.
        #
        # Output 5, Information/data maps: field-level lineage over the
        # DataObject catalogue. Distinct from "Data Architecture" above (the
        # domain/steward dashboard) — this is the map itself.
        _link("Data Lineage", "data_architecture.data_lineage_view", "git-fork"),
        # Output 6, Strategy-to-execution: the motivation layer — drivers,
        # goals, outcomes, principles, requirements — is the ArchiMate backbone
        # that connects strategy to the work packages already linked above.
        _link("Motivation Model", "architect_ui.motivation_view", "target"),
        # Output 10, Products & services: the Product register in the business
        # layer of the ArchiMate element browser.
        _link("Products & Services", "archimate_layers.business_products", "package-open"),
        # Wave 4 nav audit: organization.routes' own module docstring claims
        # "linked from the sidebar by the orchestrator post-merge" -- it never
        # was. Org chart + RACI (capabilities x stakeholders) is a business
        # architecture output with real CRUD (raci_cell_save/delete) and had no
        # sidebar entry in ANY persona's zone, reachable only via /modules or a
        # typed URL. business_architect is the persona whose remit this is and
        # had headroom (24 -> 25, budget 28).
        _link("Org Chart & RACI", "organization.index", "users-round"),
    ],
    ROLE_PORTFOLIO_MANAGER: [
        # S-11 / ARCH-122: /portfolio/ is a complete programme-management
        # module (initiative, phase, RAG health, budget/spend/variance,
        # completion, benefits, sponsor) that the portfolio_manager persona —
        # the one whose whole job it is — had no sidebar link to. It is
        # already in the enterprise_architect / arb_member / platform_admin
        # zones; this is the missing one.
        _link("Portfolio", "portfolio.index", "briefcase"),
        _APPROVAL_INBOX_LINK,
        _link("Rationalization", "unified_applications.rationalization_dashboard", "git-merge"),
        # R1-B34 (TB-0135): the reviewer of a composite score's weights is
        # this persona -- the rationalization scorecard's own number now
        # names a formula version, so the page that edits it belongs next
        # to the dashboard that reads it.
        _link("Formula Register", "formula_register.index", "calculator"),
        _link("Vendors", "unified_applications.vendors", "building"),
        _link("Applications", "unified_applications.application_list", "list"),
        # S-11 remainder: directory-only, never in a sidebar zone.
        _link("Consolidation List", "consolidation_list.dashboard", "layers"),
        # NAV-1: see the CTO entry above — same page, the other owning persona.
        _link("Portfolio KPIs", "dashboard_pages.rationalization_scorecard", "gauge"),
        # Moved from enterprise_architect in the canvas/framework UI fix,
        # round 2 (25 Sep 2026): that zone had no headroom left once
        # "Canvases" and "Frameworks" joined the shared Library zone. This
        # zone has ample headroom, and portfolio_manager already owns
        # Rationalization above, from which this page is reached in context.
        _link("Duplicate Detection", "unified_duplicate.simple_dashboard", "copy"),
        # Ownership coverage by business unit — portfolio manager accountability.
        _link("Ownership Coverage", "unified_applications.ownership_coverage", "users", requires="cto_or_portfolio_manager"),
        # R1-B03 PR 2: the one ownership record now also covers capabilities.
        # Ample headroom in this zone (9 links against SIDEBAR_LINK_BUDGET 32).
        _link("Capabilities With No Owner", "capability_map.capabilities_no_owner", "user-x", requires="cto_or_portfolio_manager"),
    ],
    ROLE_PROCUREMENT: [
        # Fix round: Overview, Licences and Compliance were reachable from
        # nowhere in the sidebar despite having working, guarded routes.
        _link("Overview", "procurement.index", "shopping-cart"),
        _APPROVAL_INBOX_LINK,
        _link("Vendors", "unified_applications.vendors", "building"),
        _link("Contracts", "procurement.contracts_list", "file-text"),
        _link("Renewals", "procurement.renewals_dashboard", "history"),
        _link("Spend", "procurement.spend_analytics", "bar-chart-3"),
        _link("Licences", "procurement.licenses_list", "key-round"),
        _link("Compliance", "procurement.compliance_dashboard", "clipboard-check"),
    ],
    ROLE_APPLICATION_MANAGER: [
        # Fix round: my_applications.dashboard is a personally-scoped view
        # (ApplicationOwner rows for current_user only — see
        # app/modules/my_applications/routes.py:get_owned_apps) distinct from
        # unified_applications.application_list's org-wide paginated list; it
        # was reachable from nowhere in the sidebar.
        _link("My Applications", "my_applications.dashboard", "layout-dashboard"),
        _APPROVAL_INBOX_LINK,
        _link("Applications", "unified_applications.application_list", "list"),
        _link("Rationalization", "unified_applications.rationalization_dashboard", "git-merge"),
        _link("Vendors", "unified_applications.vendors", "building"),
    ],
    # Not enumerated in the spec's My-work column; ARB member's primary work
    # is governance review, backed by its own zone below. My work here mirrors
    # its existing legacy ROLE_SECTION_ACCESS scope (solutions, portfolio).
    ROLE_ARB_MEMBER: [
        _link("Solutions", "solution_design.list_solutions", "wrench"),
        _APPROVAL_INBOX_LINK,
        _link("Portfolio", "portfolio.index", "briefcase"),
    ],
    # Also not enumerated in the spec; platform_admin gets a working set that
    # mirrors its legacy full-access scope, distinct from the Admin zone below.
    # Fix round (evidence review): "Applications" used to be listed here too,
    # pointing at unified_applications.application_list — the exact same
    # endpoint already listed under Library (_LIBRARY_LINKS above), so
    # platform_admin saw the identical "Applications" label twice. Library's
    # copy is the one every other role gets, so it stays; this duplicate is
    # dropped rather than relabelled, since there is no second, distinct view
    # to relabel it as.
    ROLE_PLATFORM_ADMIN: [
        _link("Solutions", "solution_design.list_solutions", "wrench"),
        _link("Portfolio", "portfolio.index", "briefcase"),
        _APPROVAL_INBOX_LINK,
        # BA-A3. platform_admin is the default enterprise_role for every user
        # who has not picked one during onboarding (see the column comment in
        # app/models/user.py), so a page that exists only for the two architect
        # roles is invisible to most real accounts. Rendered total for this
        # role goes 25 -> 26, still under SIDEBAR_LINK_BUDGET (27).
        _link("Architecture Journey", "architecture_journey.index", "compass"),
        # R1-B56: agent owner/charter/lifecycle registry.
        _link("Agent Registry", "agent_registry.index", "bot"),
    ],
    # Promoted from charter-only, 31 Aug 2026. The blueprint scores a Security
    # Viewpoint as one of its fifteen sections and nobody owned it; every link
    # here is a page that already ships, previously reachable only from another
    # persona's zone or from the module catalogue.
    ROLE_SECURITY_ARCHITECT: [
        # Points at the real governance dashboard (monitoring_data +
        # compliance_report), not unified_low_priority.policy_monitoring_dashboard
        # -- that one renders the same template with no context at all, an empty
        # shell that happened to survive because it carried no role gate. The
        # governance blueprint is registered by default (USE_GOVERNANCE_GUARDRAILS
        # defaults on), and require_roles("admin", "architect", "compliance_officer")
        # already accepts every `*_architect` enterprise_role, so this persona
        # reaches it directly.
        _link("Policy Monitoring",
              "policy_monitoring.policy_dashboard", "shield-alert",
              requires="general"),
        _APPROVAL_INBOX_LINK,
        # Security architects have read-only access to the page and list API;
        # mutation endpoints remain ADMINISTER-only.
        _link("Governance Gates", "admin.governance_gates", "shield-check"),
        _link("Risk Register", "risk.risk_register", "alert-triangle"),
        # G6 (register close, 1 Sep 2026): was procurement.compliance_dashboard,
        # which is the LICENSE-compliance page and is guarded by
        # @requires_procurement — a guaranteed 403 for this role, and the wrong
        # compliance besides. Repointed to the regulatory-framework dashboard
        # that surfaces the built-but-orphaned RegulatoryFramework /
        # ComplianceControl model. The sidebar drops any link whose endpoint is
        # unregistered (admin_sidebar.html selectattr on view_functions), so
        # this degrades safely if application_mgmt fails to import.
        _link("Compliance", "application_mgmt.compliance_frameworks_dashboard",
              "clipboard-check"),
        # The Data Protection Officer's work: scope data-subject requests,
        # assign the searches, and run access and erasure with evidence.
        _link("Data Subject Requests", "gdpr_bp.dsr_index", "user-x",
              requires="data_subject_requests"),
        _link("Applications", "unified_applications.application_list", "list"),
        _link("Data Architecture", "data_architecture.data_architecture_dashboard", "database"),
        _link("Traceability Matrix", "architect_ui.traceability_matrix", "git-compare"),
        _link("Tech Radar", "tech_radar.index", "radar"),
        _link("Interface Register", "interface_register.index", "cable"),
        # Read access to the organisation's audit trail: export and verify.
        _link("Audit Log", "admin.audit_log_viewer", "scroll-text"),
    ],
    # ARCH-123 folded this into enterprise_architect with the note "no dedicated
    # role for either yet". These three surfaces ship and are the whole of the
    # persona's remit, so the fold is now unnecessary rather than pragmatic.
    ROLE_DATA_ARCHITECT: [
        _link("Data Architecture", "data_architecture.data_architecture_dashboard", "database"),
        _APPROVAL_INBOX_LINK,
        _link("Data Lineage", "data_architecture.data_lineage_view", "git-fork"),
        _link("Data Stewardship", "solution_design.data_stewardship", "shield"),
        _link("System of Record", "data_governance.entities", "database-zap"),
        _link("Master Data Domains", "data_governance.domains", "folder-tree"),
        _link("Architecture Model", "archimate_crud.dashboard", "boxes"),
        _link("Applications", "unified_applications.application_list", "list"),
        _link("Capability Map", "capability_map.index", "layers"),
        _link("Traceability Matrix", "architect_ui.traceability_matrix", "git-compare"),
        _link("Interface Register", "interface_register.index", "cable"),
    ],
    # R1-B36 (TB-0146): finance, compliance, risk, operations and
    # non_technical_owner promoted from unassignable to assignable, each
    # given the real pages their own section access already names.
    ROLE_FINANCE: [
        _link("Spend", "procurement.spend_analytics", "bar-chart-3"),
        _link("Licences", "procurement.licenses_list", "key-round"),
        _APPROVAL_INBOX_LINK,
    ],
    ROLE_COMPLIANCE: [
        _link("Compliance", "application_mgmt.compliance_frameworks_dashboard", "clipboard-check"),
        _APPROVAL_INBOX_LINK,
    ],
    ROLE_RISK: [
        _link("Risk Register", "risk.risk_register", "alert-triangle"),
        _APPROVAL_INBOX_LINK,
    ],
    ROLE_OPERATIONS: [
        _link("Service Status", "service_status.status_page", "activity"),
        _APPROVAL_INBOX_LINK,
    ],
    ROLE_NON_TECHNICAL_OWNER: [
        _link("Applications", "unified_applications.application_list", "list"),
        _APPROVAL_INBOX_LINK,
    ],
}

_BOARD_ROLES = {
    ROLE_ENTERPRISE_ARCHITECT,
    ROLE_ARB_MEMBER,
    ROLE_CTO,
    ROLE_PLATFORM_ADMIN,
}


def _build_zones(role: str) -> List[Dict]:
    # platform_admin has no headroom left for a 5th library link once its two
    # admin-zone additions are counted (23 zone links -> 25 rendered, exactly
    # at SIDEBAR_LINK_BUDGET) — see _LIBRARY_LINKS_WITH_DIRECTORY's comment.
    # Value Streams at Risk is a business_architect-only My-work link. To keep
    # the rendered sidebar within the existing ratchet (28) rather than raising
    # verification_baseline.json, that persona's Home zone keeps Dashboard
    # Overview and drops Health Scorecard, which remains reachable from the
    # dashboard itself and from the personas that actively work from it.
    home_links = (
        _HOME_LINKS[:1] if role == ROLE_BUSINESS_ARCHITECT else _HOME_LINKS
    )
    library_links = (
        _LIBRARY_LINKS if role == ROLE_PLATFORM_ADMIN else _LIBRARY_LINKS_WITH_DIRECTORY
    )
    zones = [
        _zone("home", home_links),
        _zone("my_work", [_ASK_LINK] + _MY_WORK_LINKS[role]),
        _zone("library", library_links),
    ]
    if role in _BOARD_ROLES:
        zones.append(_zone("governance", _GOVERNANCE_LINKS))
    if role == ROLE_PLATFORM_ADMIN:
        zones.append(_zone("admin", _ADMIN_LINKS))
    return zones


# role -> ordered zones. Built once at import time; zone dicts are shared
# (read-only) across roles where content is identical (home/library).
SIDEBAR_ZONES: Dict[str, List[Dict]] = {
    role: _build_zones(role) for role in _MY_WORK_LINKS
}


def persona_preview(persona: str, org_role: str) -> Dict:
    """R1-B88: what someone invited as ``persona`` with ``org_role`` will see.

    Built from the same SIDEBAR_ZONES the sidebar renders from, so the preview
    cannot drift from what the invitee is shown. Links a person with this
    organisation role could not open are left out. Raises ``ValueError`` for an
    unknown persona or organisation role.
    """
    from app.models.org_role import VALID_ORG_ROLES
    from app.models.user import ROLE_PLAIN_DESCRIPTIONS

    if persona not in SIDEBAR_ZONES or persona not in ROLE_PLAIN_DESCRIPTIONS:
        raise ValueError("Unknown persona '{}'.".format(persona))
    if org_role not in VALID_ORG_ROLES:
        raise ValueError("Unknown role '{}'.".format(org_role))

    can_change = org_role in ("architect", "org_admin")
    can_administer = org_role == "org_admin"

    def _shown(link):
        requires = link.get("requires")
        if requires is None:
            return True
        if requires == "general":
            return can_change
        if requires == "admin":
            return can_administer
        return False

    will_see = []
    for zone in SIDEBAR_ZONES[persona]:
        labels = [link["label"] for link in zone["links"] if _shown(link)]
        if labels:
            will_see.append({"zone": zone["title"], "links": labels})

    if can_administer:
        summary = (
            "Can add and change records, and can manage the team and settings."
        )
    elif can_change:
        summary = "Can add and change records, but cannot manage the team or settings."
    else:
        summary = "Can look at everything listed here but cannot change records."

    return {
        "persona": persona,
        "display_name": get_role_display_name(persona),
        "description": ROLE_PLAIN_DESCRIPTIONS[persona],
        "will_see": will_see,
        "can_change": can_change,
        "can_administer": can_administer,
        "summary": summary,
    }


def get_sidebar_zones(user) -> List[Dict]:
    """Resolve the current user's role and return their ordered sidebar zones.

    Must never raise for the same reason as get_user_role: this renders on
    every authenticated page. Falls back to the default role's zones for an
    unrecognized/legacy role value.

    The "admin" zone is filtered per-request on the real `is_platform_admin`
    boolean (defaults False, purpose-built for this), not on the cached
    per-role lookup above — `enterprise_role` (which selects that lookup)
    defaults to the string "platform_admin" for EVERY user for backward
    compatibility (see the column comment in app/models/user.py), so keying
    zone visibility off it alone showed the whole Admin section, full of
    routes guarded by a completely different check, to ordinary users who
    haven't explicitly picked a role during onboarding. Most of those routes
    correctly 403 them anyway (admin_required checks the Administrator role,
    unaffected by this), but /admin/organizations is guarded by
    platform_admin_required — the one route where this default actually
    controls data access, not just link visibility.
    """
    role = get_user_role(user)
    zones = SIDEBAR_ZONES.get(role, SIDEBAR_ZONES[DEFAULT_ROLE])

    # Permission-driven navigation (F-11, 2 Sep 2026 audit). Every admin-zone
    # route is @admin_required, i.e. Permission.ADMINISTER — so the zone is shown
    # only when user.is_admin() holds, which is exactly what those routes check.
    # Keying it off is_platform_admin (as before) showed a zone full of 403s to a
    # super-admin-flag holder whose Role was Architect. Individual links then
    # declare the guard they need via _link(requires=...) and are dropped when
    # the user cannot satisfy it. New dicts are built — the module-level zone
    # constants are shared and must never be mutated per request.
    try:
        is_admin = bool(user.is_admin())
    except Exception:  # anonymous / unexpected user object
        is_admin = False

    visible = []
    for z in zones:
        if z["zone"] == "admin" and not is_admin:
            continue
        links = [link for link in z["links"] if link_requires_satisfied(user, link.get("requires"))]
        visible.append({**z, "links": links})
    return visible


def link_requires_satisfied(user, requires):
    """True when `user` satisfies a sidebar link's `requires` guard (see `_link`'s docstring), or
    `requires` is None. The one predicate `get_sidebar_zones` uses per-link, pulled out so any other
    surface that lists the same links (the All-modules directory, sidebar/global search) filters them
    identically instead of re-deriving its own, partial version of the same rule."""
    if requires is None:
        return True
    if requires == "admin":
        try:
            return bool(user.is_admin())
        except Exception:  # anonymous / unexpected user object
            return False
    if requires == "org_admin":
        return bool(getattr(user, "is_org_admin", False))
    if requires == "platform_admin":
        return bool(getattr(user, "is_platform_admin", False))
    if requires == "data_subject_requests":
        try:
            from app.decorators.requires_role import may_handle_data_subject_requests

            return may_handle_data_subject_requests(user)
        except Exception:  # anonymous / unexpected user object
            return False
    if requires == "general":
        try:
            from app.models.user import Permission

            return bool(user.can(Permission.GENERAL))
        except Exception:  # anonymous / unexpected user object
            return False
    if requires == "cto_or_portfolio_manager":
        # Matches the route guard on Ownership Coverage and Capabilities
        # With No Owner (@role_required(ROLE_CTO, ROLE_PORTFOLIO_MANAGER),
        # which also falls back to is_admin()) -- these are enterprise_role
        # checks, not a Permission bit, so neither "admin" nor "general"
        # above covers them. R1-B03 PR 2: found both links already leaking
        # into every persona's /modules/ directory as dead 403 rows, since
        # no requires= guard existed for an enterprise_role predicate before
        # this one.
        try:
            if hasattr(user, "is_admin") and user.is_admin():
                return True
            return getattr(user, "enterprise_role", None) in (ROLE_CTO, ROLE_PORTFOLIO_MANAGER)
        except Exception:  # anonymous / unexpected user object
            return False
    return False


# Context processor for templates
def role_access_context_processor():
    """
    Provide role access functions to Jinja2 templates.

    Usage in template:
        {% if can_access_section(current_user, 'administration') %}
    """
    return {
        "can_access_section": can_access_section,
        "can_edit_capabilities": can_edit_capabilities,
        "get_visible_sections": get_visible_sections,
        "is_admin": is_admin,
        "is_procurement": is_procurement,
        "is_application_manager": is_application_manager,
        "get_role_display_name": get_role_display_name,
    }
