"""Navigation v3: one registry the sidebar, the search index and the breadcrumbs read.

Every entry a person sees in the signed-in shell is declared here once, with the job it
serves, who may see it, and the old and new names a person might type for it. Nothing in
a template decides what the sidebar shows; ``sidebar_for(user)`` does, from this data.

The structure (sentence case, the shadcn sidebar pattern, a group label, a collapsible item and
its sub-items: two levels at most):

- the organisation switcher, then one search box ("Search or ask...");
- TOP: Home, AI chat and Approvals (with the number of items waiting for the person);
- WORK: Systems, Impact, Changes, Spend and Compliance, each opening onto its pages;
- MODEL, shown to every role: one item per ArchiMate layer opening onto its element types
  (ADR-0018), then Frameworks and Composer. Frameworks are one gallery page (ADR-0017);
- SECONDARY at the bottom: Connections, Reports, Help;
- the user menu in the footer: Account, Billing, Team, Settings, Platform admin, Sign out.

The sidebar has one shape for every role; a person is shown an entry only when its page
opens for them. A page that a job entry and an element entry both open (Applications,
Application component) is marked current under both.

The long tail (every page the old sidebar and the All-modules directory listed) is the
catalogue: each page is filed under the entry whose job it serves, so the search index
labels it with that entry and the breadcrumb trail reads "<entry> / <page>".

A link whose route has a guard declares it in ``requires`` (the vocabulary of
``app.utils.role_access.link_requires_satisfied``), so nobody is shown a link the route
would refuse; ``fallback`` names the page offered instead when the guard fails.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# Jobs, in the words of the consolidation map.
JOBS = {
    "home": "Start here",
    "0": "Build the model from what you run",
    "1": "What we have and who owns it",
    "2": "What breaks if this fails",
    "3": "What it costs",
    "4": "What we are changing",
    "5": "Can we prove we comply",
    "6": "Ask and get a cited answer",
    "account": "Account and administration",
    "platform": "Platform administration",
}



@dataclass(frozen=True)
class NavLink:
    """One destination. ``roles`` None means every role; ``requires`` names the route's guard."""

    key: str
    label: str
    endpoint: str
    icon: str | None = None
    job: str = "home"
    roles: frozenset | None = None
    requires: str | None = None
    aliases: tuple = ()
    url_values: tuple = ()
    fallback: "NavLink | None" = None
    children: tuple = ()
    # Endpoints whose pages belong to this entry: the entry is marked current there and
    # heads the breadcrumb trail. An owner ending in "." or "_" is a prefix.
    owns: tuple = ()
    # Where the canonical page for this job is not built yet, what stands in for it.
    note: str | None = None
    # A collapsible item: it opens its sub-items and has no page of its own (its endpoint is
    # the first page it holds, used for the breadcrumb trail).
    toggle: bool = False
    # "work" or "model": which sidebar group a collapsible item sits in.
    group: str = ""
    # Name of the counter whose number is shown beside the entry (see _COUNTERS).
    count: str | None = None

    @property
    def values(self) -> dict:
        return dict(self.url_values)


def _l(key, label, endpoint, icon=None, **kw) -> NavLink:
    for name in ("aliases", "children", "owns"):
        if name in kw:
            kw[name] = tuple(kw[name])
    if "url_values" in kw:
        kw["url_values"] = tuple(sorted(kw["url_values"].items()))
    return NavLink(key=key, label=label, endpoint=endpoint, icon=icon, **kw)


# ── Top: the same three entries, in this order, for every role ──────────────────────────
TOP: tuple = (
    _l("home", "Home", "dashboard.overview", "home", job="home",
       aliases=("Overview", "Dashboard", "Dashboard overview", "Start", "Getting started"),
       owns=("dashboard.overview", "dashboard.index", "dashboards.")),
    _l("ai_chat", "AI chat", "unified_ai_chat.index", "message-square", job="6",
       aliases=("Ask", "Ask a question", "Assistant", "Chat", "Saved answers", "Question"),
       owns=("unified_ai_chat.", "intelligence_ui.ask", "intelligence_ui.answers")),
    # The one approvals inbox; its number is what the inbox lists for this person.
    _l("approvals", "Approvals", "unified_ai_chat.approval_inbox", "inbox", job="6", requires="general",
       aliases=("Approval inbox", "Pending changes", "Proposals", "Waiting for me"), count="approvals"),
)

# ── Work: five jobs, each a collapsible item with the pages that do it ───────────────────
WORK: tuple = (
    _l("systems", "Systems", "unified_applications.application_list", "boxes", job="1", toggle=True, group="work",
       children=(
           _l("applications", "Applications", "unified_applications.application_list", job="1",
              aliases=("Application portfolio", "Services", "Owners", "Ownership", "Inventory", "Library")),
           _l("portfolio", "Portfolio", "unified_applications.rationalization_dashboard", job="1",
              aliases=("Rationalisation", "Rationalization", "Portfolio KPIs", "Application rationalisation",
                       "Consolidation", "Savings", "Gap findings"),
              owns=("unified_applications.rationalization_",)),
           _l("data_register", "Data register", "data_architecture.data_entity_catalog", job="1",
              aliases=("Data entities", "Data catalogue", "Data objects", "Data model")),
           _l("interfaces", "Interfaces", "interface_register.index", job="1", requires="section:data_integration",
              aliases=("Interface register", "Integrations register")),
           _l("tech_radar", "Technology radar", "tech_radar.index", job="1", aliases=("Tech radar", "Radar")),
       ),
       owns=("unified_applications.", "capability_map.", "application_mgmt.", "my_applications.",
             "capabilities.", "organization.", "teams.", "stakeholder_map.", "tech_radar.",
             "data_architecture.", "data_governance.", "interface_register.", "unified_duplicate.",
             "maturity_management.", "dashboard.health_scorecard", "consolidation_list.",
             "dashboard_pages.rationalization_scorecard")),
    _l("impact", "Impact", "enterprise.impact_analysis", "zap", job="2", toggle=True, group="work",
       children=(
           _l("impact_analysis", "Impact analysis", "enterprise.impact_analysis", job="2",
              aliases=("Impact", "What breaks", "Dependencies", "Blast radius")),
           _l("traceability", "Traceability", "architect_ui.traceability_matrix", job="2",
              aliases=("Traceability matrix", "Trace", "Requirements traceability", "Value streams at risk")),
       ),
       owns=("strategic.impact_analysis", "intelligence_ui.traceability", "intelligence_ui.value_streams_at_risk",
             "intelligence_ui.twin_map")),
    _l("changes", "Changes", "enterprise.gap_analysis", "route", job="4", toggle=True, group="work",
       children=(
           _l("gaps", "Gaps", "enterprise.gap_analysis", job="4", aliases=("Gap", "Gap analysis")),
           _l("solutions", "Solutions", "solution_design.list_solutions", job="4",
              aliases=("Solution designs", "Solution design", "Designs")),
           _l("options_analysis", "Options analysis", "application_mgmt.vendor_analysis_list", job="4",
              aliases=("Vendor options", "Vendor selection", "Vendor analysis", "Compare options")),
           _l("business_cases", "Business cases", "business_case.index", job="4",
              aliases=("Business case", "Case for change", "Investment case", "Cost and benefit")),
           _l("work_packages", "Work packages", "enterprise.work_packages", job="4",
              aliases=("Work package", "WorkPackage", "Projects")),
           _l("roadmap", "Roadmap", "genome_roadmap.index", job="4",
              aliases=("Roadmaps", "Transformation roadmap", "Capability roadmap", "Technology roadmap",
                       "Timeline", "Plateaus", "Plateau", "Target states", "Transition states")),
           _l("programmes", "Programmes", "solution_design.programmes_list", job="4",
              aliases=("Transformation programmes", "Programs", "Initiatives")),
           _l("canvases", "Canvases", "business_model.index", job="4",
              aliases=("Canvas", "Business model canvas", "Lean canvas", "Business models")),
       ),
       owns=("enterprise.plateaus", "genome_roadmap.", "main.capability_roadmap", "implementation_planning.",
             "adm_kanban_view.", "architecture_journey.", "portfolio.", "solution_design.", "business_model.",
             "business_case.", "application_mgmt.vendor_analysis_")),
    _l("spend", "Spend", "procurement.spend_analytics", "wallet", job="3", toggle=True, group="work",
       children=(
           _l("costs", "Costs", "procurement.spend_analytics", job="3", requires="spend_pages",
              aliases=("Cost", "Spend analytics", "Spend", "Procurement overview", "Vendor spend")),
           _l("vendors", "Vendors", "unified_applications.vendors", job="3", requires="spend_pages",
              aliases=("Suppliers", "Vendor list")),
           _l("vendor_catalogue", "Vendor catalogue", "unified_applications.vendor_catalogue", job="3",
              requires="spend_pages", aliases=("Catalogue", "Vendor products", "Product catalogue")),
           # Finance may open licences but not contracts: it is offered the licences page.
           _l("contracts", "Contracts and licences", "procurement.contracts_list", job="3", requires="costs",
              aliases=("Contracts", "Vendor contracts", "Licences", "Licenses", "Software licences"),
              fallback=_l("licences", "Licences", "procurement.licenses_list", job="3", requires="spend_pages",
                          aliases=("Licenses", "Software licences", "Contracts and licences"))),
           _l("renewals", "Renewals", "procurement.renewals_dashboard", job="3", requires="costs",
              aliases=("Contract renewals",)),
           _l("investments", "Investments", "enterprise.investment_matrix", job="3",
              aliases=("Investment", "Investment matrix", "Investment priorities", "Investment analysis")),
       ),
       owns=("procurement.", "formula_register.", "unified_applications.vendor_catalogue_product",
             "unified_applications.vendor_catalogue_link")),
    _l("compliance", "Compliance", "policy_monitoring.policy_dashboard", "shield-check", job="5", toggle=True,
       group="work",
       children=(
           _l("controls", "Controls", "policy_monitoring.policy_dashboard", job="5", requires="general",
              aliases=("Policy monitoring", "Policies", "Policy", "Standards")),
           _l("risks", "Risks", "risk.risk_register", job="5", aliases=("Risk register", "Risk")),
           _l("decisions", "Decisions", "arch_decisions.list_decisions", job="5",
              aliases=("Architecture decisions", "ADRs", "Decision log", "Board decisions")),
           _l("review_board", "Review board", "arb.dashboard", job="5",
              aliases=("ARB", "Architecture review board", "ARB dashboard", "Reviews", "Sessions")),
           _l("evidence", "Evidence", "intelligence_ui.history_changes_page", job="5",
              aliases=("Model changes", "Changes between dates", "What changed", "Change history", "Audit trail",
                       "Who changed what", "History", "Audit")),
       ),
       owns=("risk.", "arch_decisions.", "policy_monitoring.", "gdpr_bp.", "admin.audit_log_viewer", "arb.",
             "application_mgmt.compliance_frameworks_dashboard", "intelligence_ui.history_as_of_page",
             "intelligence_ui.history_changes_page"),
       note="Opens on the policy page; the compliance dashboard is held back until its views carry data."),
)

# ── Model: one collapsible item per ArchiMate layer, then Frameworks and Composer ───────
# (key, label, icon, layer key in the model pages, element types in the order the sidebar lists them)
_MODEL_LAYERS: tuple = (
    ("strategy", "Strategy", "compass", "strategy",
     ("Capability", "ValueStream", "Resource", "CourseOfAction")),
    ("business", "Business", "briefcase", "business",
     ("BusinessActor", "BusinessRole", "BusinessCollaboration", "BusinessInterface", "BusinessProcess",
      "BusinessFunction", "BusinessInteraction", "BusinessEvent", "BusinessService", "BusinessObject", "Contract",
      "Representation", "Product")),
    ("application", "Application", "app-window", "application",
     ("ApplicationComponent", "ApplicationCollaboration", "ApplicationInterface", "ApplicationFunction",
      "ApplicationInteraction", "ApplicationProcess", "ApplicationEvent", "ApplicationService", "DataObject")),
    ("technology", "Technology", "server", "technology",
     ("Node", "Device", "SystemSoftware", "TechnologyCollaboration", "TechnologyInterface", "Path",
      "CommunicationNetwork", "TechnologyFunction", "TechnologyProcess", "TechnologyInteraction", "TechnologyEvent",
      "TechnologyService", "Artifact")),
    ("physical", "Physical", "factory", "physical", ("Equipment", "Facility", "DistributionNetwork", "Material")),
    ("motivation", "Motivation", "target", "motivation",
     ("Stakeholder", "Driver", "Assessment", "Goal", "Outcome", "Principle", "Requirement", "Constraint", "Meaning",
      "Value")),
    ("implementation", "Implementation and migration", "rocket", "implementation",
     ("WorkPackage", "Deliverable", "ImplementationEvent", "Plateau", "Gap")),
    ("other", "Other", "shapes", "other", ("Location", "Grouping")),
)

# A type whose list already has a page of its own opens that page: one page, never two.
_CANONICAL_TYPE_PAGES = {
    "ApplicationComponent": "unified_applications.application_list",
    "DataObject": "data_architecture.data_entity_catalog",
    "Capability": "capability_map.index",
    "ValueStream": "value_stream.index",
    "Gap": "enterprise.gap_analysis",
    "WorkPackage": "enterprise.work_packages",
    "Plateau": "enterprise.plateaus",
}
# Types the model pages hold no list for: the architecture model page opens instead.
_MODEL_PAGE_ONLY = frozenset({"Location", "Grouping"})


def _type_label(element_type: str) -> str:
    import re as _re

    words = _re.sub(r"(?<=[a-z])(?=[A-Z])", " ", element_type).split()
    return " ".join([words[0]] + [w.lower() for w in words[1:]]) if words else element_type


def _element_link(layer_key: str, element_type: str) -> NavLink:
    key = "type_" + element_type
    if element_type in _CANONICAL_TYPE_PAGES:
        return _l(key, _type_label(element_type), _CANONICAL_TYPE_PAGES[element_type], job="1",
                  aliases=(element_type,))
    if element_type in _MODEL_PAGE_ONLY:
        return _l(key, _type_label(element_type), "archimate_crud.dashboard", job="1",
                  url_values={"element_type": element_type}, aliases=(element_type,))
    return _l(key, _type_label(element_type), "archimate_crud.list_elements", job="1",
              url_values={"layer": layer_key, "element_type": element_type}, aliases=(element_type,))


MODEL: tuple = tuple(
    _l("layer_" + key, label, "archimate_crud.dashboard", icon, job="1", toggle=True, group="model",
       children=tuple(_element_link(layer_key, t) for t in types))
    for key, label, icon, layer_key, types in _MODEL_LAYERS
) + (
    _l("frameworks", "Frameworks", "frameworks.gallery", "library", job="1",
       aliases=("Maturity frameworks", "Capability frameworks", "TOGAF", "APQC", "Industry frameworks",
                "Industry process frameworks", "Compliance frameworks"),
       owns=("frameworks.", "industry_apqc.", "maturity_management.frameworks_overview")),
    # Opens on the layered viewpoint, not a blank canvas.
    _l("composer", "Composer", "archimate.composer_page", "pen-tool", job="1",
       url_values={"viewpoint": "layered"},
       aliases=("ArchiMate composer", "Diagrams", "Diagram", "Views", "Viewpoints"),
       owns=("archimate.",)),
)

# Not drawn in the sidebar: the entry the model pages sit under for the breadcrumb trail.
_MODEL_ROOT = _l("archimate_model", "Model", "archimate_crud.dashboard", "network", job="1",
                 aliases=("ArchiMate model", "Architecture", "Architecture model", "Elements", "Catalogue",
                          "Catalogues", "Repository", "Layers"),
                 owns=("archimate_crud.", "archimate_layers.", "value_stream."))

# ── Value streams page: one entry under Model > Strategy (Value stream) ───────────────

# ── Secondary, at the bottom of the sidebar ─────────────────────────────────────────────
# Connecting a source is for the architects who author the model and for administrators
# (the connector pages' own guard); nobody else is shown the entry.
SECONDARY: tuple = (
    _l("connections", "Connections", "m365_connector.connectors_index", "plug", job="0", requires="connections",
       aliases=("Integrations", "Connectors", "Connect a source", "Connect GitHub", "Connect Jira", "Sources",
                "GitHub", "Jira", "Import", "Imports", "Batch import"),
       owns=("m365_connector.", "batch_import_view.", "admin.salesforce_integration",
             "admin.power_platform_integration", "admin.slack_integration", "admin.teams_meetings_integration",
             "admin.jira_settings", "admin.abacus_")),
    _l("reports", "Reports", "solution_design.ea_briefings", "file-text", job="6",
       aliases=("Briefings", "EA briefings", "Exports", "Export", "Architect briefing", "Report"),
       owns=("solution_design.architect_synthesis", "solution_design.ea_briefing_detail", "admin.report_builder", "cto_scorecard.")),
    _l("help", "Help", "main.public_site_page", "life-buoy", job="account", url_values={"slug": "docs"},
       aliases=("Documentation", "Docs", "Support", "Guide", "How do I"),
       note="Opens the documentation page; there is no in-product help centre yet."),
)

# ── The user menu in the sidebar footer (Sign out is always last) ───────────────────────
USER_MENU: tuple = (
    _l("account", "Account", "account.manage", "user", job="account",
       aliases=("Profile", "Account settings", "Password", "Change password", "Email")),
    _l("billing", "Billing", "billing.billing_index", "credit-card", job="account", requires="admin",
       aliases=("Plan", "Subscription", "Invoices", "Payment", "Cancel", "Upgrade")),
    _l("team", "Team", "team.team", "users", job="account", requires="org_or_platform_admin",
       aliases=("Invite your team", "Invite", "Members", "People", "Teammates")),
    _l("settings", "Settings", "admin.index", "settings", job="account", requires="admin",
       aliases=("Administration", "Workspace settings", "Command center", "Organisation settings", "Users",
                "API settings")),
    _l("platform_admin", "Platform admin", "platform_area.index", "shield", job="platform",
       requires="platform_admin", aliases=("Platform administration", "Admin area")),
)

# ── The admin area the Platform admin entry opens (platform_admin flag only) ────────────
PLATFORM_ADMIN_TOOLS: tuple = (
    _l("organisations", "Organisations", "admin.organizations_list", "building-2", job="platform",
       requires="platform_admin", aliases=("Organizations", "Tenants")),
    _l("ai_prompts", "AI prompts", "admin.solution_prompts_page", "sparkles", job="platform",
       requires="platform_admin", aliases=("Prompts", "Solution prompts")),
    _l("errors", "Errors", "error_events.errors_dashboard", "alert-triangle", job="platform",
       requires="platform_admin", aliases=("Error log", "Exceptions")),
    _l("signups", "Sign-ups", "platform_area.signups", "user-plus", job="platform",
       requires="platform_admin", aliases=("New accounts", "Registrations", "Sign ups")),
    _l("feature_flags", "Feature flags", "admin.feature_flags", "toggle-right", job="platform",
       requires="platform_admin", aliases=("Flags", "Features")),
    _l("platform_health", "Platform health", "service_status.status_page", "activity", job="platform",
       requires="platform_admin", aliases=("Service status", "Status", "Health")),
    # Its route admits every role with the administration section (can_access_section).
    _l("agent_registry", "Agent registry", "agent_registry.index", "bot", job="platform",
       requires="section:administration", aliases=("Agents", "Registered agents")),
    _l("framework_management", "Framework management", "main.framework_management.dashboard", "library",
       job="platform", requires="platform_admin", aliases=("Framework library",)),
    _l("framework_configuration", "Framework configuration", "framework_config_ui.framework_config_dashboard",
       "sliders-horizontal", job="platform", requires="platform_admin", aliases=("Framework config",)),
)

# ── Pages reached from a job page, under the names the consolidation map gives them ────
# Not sidebar entries: each is one click inside its entry's page, and searchable by its
# current name and the names it went by. They are listed ahead of the catalogue, so the
# search index offers "Capability map", not the old "Capabilities".
PAGES: tuple = (
    _l("capability_map", "Capability map", "capability_map.index", job="1",
       aliases=("Capabilities", "Capability heat map", "Capability health", "Business capabilities")),
    _l("health_scorecard", "Health scorecard", "dashboard.health_scorecard", job="1",
       aliases=("CTO scorecard", "Architecture health", "Model health", "Scorecard")),
    _l("org_chart", "Organisation units", "organization.index", job="1",
       aliases=("Org chart", "RACI", "Org chart and RACI", "Organization")),
    _l("data_architecture", "Data architecture", "data_architecture.data_architecture_dashboard", job="1",
       aliases=("Data model", "Data lineage")),
    _l("duplicates", "Duplicates", "unified_duplicate.simple_dashboard", job="1",
       aliases=("Duplicate detection", "Dedupe")),
    _l("history_as_of", "Model as of", "intelligence_ui.history_as_of_page", job="5",
       aliases=("Model as of a date", "Point in time", "As of")),
    _l("value_streams_at_risk", "Value streams at risk", "intelligence_ui.value_streams_at_risk", job="2",
       aliases=("At risk",)),
    _l("plateaus", "Plateaus", "enterprise.plateaus", job="4",
       aliases=("Plateau", "Target states", "Transition states")),
    _l("consolidation", "Consolidation", "consolidation_list.dashboard", job="3",
       aliases=("Consolidation list", "Retire", "Retirement")),
    _l("audit_log", "Audit log", "admin.audit_log_viewer", job="5", requires="admin",
       aliases=("Audit trail", "Activity log")),
    _l("data_subject_requests", "Data subject requests", "gdpr_bp.dsr_index", job="5",
       requires="data_subject_requests", aliases=("DSR", "GDPR requests")),
    _l("architect_briefing", "Architect briefing", "solution_design.ea_briefings", job="6",
       aliases=("Chief architect synthesis", "Weekly briefing")),
    _l("all_pages", "All modules", "modules_directory.index", job="account",
       aliases=("Every page", "Modules", "Directory", "Site map")),
)

# Endpoint prefix -> entry key, for catalogue pages no entry's ``owns`` covers.
_CATALOGUE_HOME = (
    ("modules_directory.", "help"),
    ("architecture.investment_priorities", "spend"),
    ("architect_ui.market_intelligence", "spend"),
    ("main.vendor_archimate_analysis", "spend"),
    ("architect_ui.motivation_view", "systems"),
    ("metamodel_properties.", "systems"),
    ("application_management", "systems"),
    ("dynamic_dashboards.", "systems"),
    ("main.hybrid_mapping_dashboard", "systems"),
    ("industry_apqc.", "systems"),
    ("architect_ui.roadmap_builder", "changes"),
    ("architect_ui.architecture_assistant", "changes"),
    ("main.ea_workflows_dashboard", "changes"),
    ("roadmap_outcome.", "changes"),
    ("main.archimate_roadmap", "changes"),
    ("integration.", "connections"),
    ("main.integrations", "connections"),
    ("main.settings", "platform_admin"),
    ("main.capability_framework", "platform_admin"),
    ("usage_analytics.", "settings"),
    ("solution_design.", "changes"),
    ("enterprise.", "changes"),
    ("strategic.", "impact"),
    ("intelligence_ui.", "impact"),
    ("main.framework_management", "platform_admin"),
    ("admin.", "settings"),
)

ALL_ENTRIES: tuple = TOP + WORK + MODEL + SECONDARY + USER_MENU + (_MODEL_ROOT,)


# ── Predicates ───────────────────────────────────────────────────────────────────────────
def _role(user) -> str:
    from app.utils.role_access import get_effective_role

    return get_effective_role(user)


def _registered(endpoint: str) -> bool:
    try:
        from flask import current_app

        return endpoint in current_app.view_functions
    except RuntimeError:  # no application context: data-only callers
        return True


def _allowed(user, link: NavLink, role: str) -> bool:
    from app.utils.role_access import is_hidden_not_ready, link_requires_satisfied

    if link.roles is not None and role not in link.roles:
        return False
    if not _registered(link.endpoint) or is_hidden_not_ready(link.endpoint):
        return False
    return link_requires_satisfied(user, link.requires)


def resolve(user, link: NavLink, role: str | None = None) -> NavLink | None:
    """The link this user is offered for ``link``: itself, its fallback, or nothing."""
    role = role or _role(user)
    if _allowed(user, link, role):
        return link
    if link.fallback is not None and _allowed(user, link.fallback, role):
        return link.fallback
    return None


def href(link: NavLink) -> str | None:
    from flask import url_for

    try:
        return url_for(link.endpoint, **link.values)
    except Exception:  # noqa: BLE001 - one bad entry costs one link, never the page
        return None


def _by_key(key: str) -> NavLink | None:
    for link in ALL_ENTRIES:
        if link.key == key:
            return link
    return None


def entry_for_endpoint(endpoint: str | None) -> NavLink | None:
    """The sidebar entry a page belongs to: the entry marked current and the head of its trail."""
    if not endpoint:
        return None
    # The entry's own page (or the page offered in its place) first. A collapsible item has no
    # page of its own: it answers for the pages it holds, below ...
    for link in ALL_ENTRIES:
        if link.toggle:
            continue
        if endpoint == link.endpoint or (link.fallback is not None and endpoint == link.fallback.endpoint):
            return link
    # ... then an exact owner, a sub-item, or a platform admin tool ...
    for link in ALL_ENTRIES:
        if endpoint in link.owns or (link.group != "model" and any(endpoint == c.endpoint for c in link.children)):
            return link
    if any(endpoint == tool.endpoint for tool in PLATFORM_ADMIN_TOOLS):
        return _by_key("platform_admin")
    # ... then the longest matching prefix owner.
    best, best_len = None, 0
    for link in ALL_ENTRIES:
        for owned in link.owns:
            if owned.endswith((".", "_")) and endpoint.startswith(owned) and len(owned) > best_len:
                best, best_len = link, len(owned)
    if best is not None:
        return best
    for prefix, key in _CATALOGUE_HOME:
        if endpoint.startswith(prefix):
            return _by_key(key)
    return None


# ── What the sidebar renders ─────────────────────────────────────────────────────────────
@dataclass
class SidebarItem:
    key: str
    label: str
    href: str | None
    icon: str | None
    job: str
    active: bool = False
    current: bool = False
    children: list = field(default_factory=list)
    toggle: bool = False
    open: bool = False
    badge: int | None = None


def _waiting_approvals(user) -> int | None:
    """What the approvals inbox lists for this person: the number shown beside Approvals."""
    try:
        from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

        return AIChatApprovalService(getattr(user, "id", None)).count_approver_queue()
    except Exception:  # noqa: BLE001 - a count must not 500 a page
        return None


_COUNTERS = {"approvals": _waiting_approvals}


def _here(link: NavLink, endpoint: str | None, values: dict | None) -> bool:
    """True when the page being shown is this link's page: the endpoint, and the values that tell
    two links to one endpoint apart (the layer and element type of a list page)."""
    if endpoint != link.endpoint:
        return False
    values = values or {}
    return all(str(values.get(k)) == str(v) for k, v in link.values.items())


def _item(user, link: NavLink, role: str, endpoint: str | None, current_entry: NavLink | None,
          values: dict | None = None, remembered: dict | None = None):
    if link.toggle:
        children = []
        for child in link.children:
            child_link = resolve(user, child, role)
            child_url = href(child_link) if child_link else None
            if child_url:
                here = _here(child_link, endpoint, values)
                # a page the item holds (a Portfolio tab) lights the item; only its own page is current
                held = bool(child.owns) and endpoint is not None and _holds(child, endpoint)
                children.append(SidebarItem(child_link.key, child_link.label, child_url, None, child.job,
                                            active=here or held, current=here))
        if not children:
            return None
        in_entry = (current_entry is not None and current_entry.key == link.key) or any(c.current for c in children)
        # What the person last chose is kept; with no choice, the item holding the page is open.
        choice = (remembered or {}).get(link.key)
        return SidebarItem(link.key, link.label, None, link.icon, link.job, active=in_entry, current=False,
                           children=children, toggle=True, open=in_entry if choice is None else choice)
    chosen = resolve(user, link, role)
    if chosen is None:
        return None
    url = href(chosen)
    if url is None:
        return None
    counter = _COUNTERS.get(link.count) if link.count else None
    number = counter(user) if counter else None
    in_entry = current_entry is not None and current_entry.key == link.key
    return SidebarItem(link.key, link.label, url, link.icon, link.job, active=in_entry or _here(chosen, endpoint, values),
                       current=_here(chosen, endpoint, values), badge=number or None)


def sidebar_for(user, endpoint: str | None = None, values: dict | None = None,
                remembered: dict | None = None) -> dict:
    """{"main", "work", "model", "secondary", "user_menu"} -> [SidebarItem] for this user.

    ``values`` are the request's path and query values (they tell the list pages of two element
    types apart); ``remembered`` maps an item's key to True/False for the items this person has
    opened or closed."""
    role = _role(user)
    current_entry = entry_for_endpoint(endpoint)

    def build(links: Iterable[NavLink]):
        built = (_item(user, link, role, endpoint, current_entry, values, remembered) for link in links)
        return [i for i in built if i]

    return {
        "main": build(TOP),
        "work": build(WORK),
        "model": build(MODEL),
        "secondary": build(SECONDARY),
        "user_menu": build(USER_MENU),
    }


def remembered_cookie_name(user) -> str:
    """The cookie holding which sidebar items this person has opened or closed."""
    return "sidebar_items_%s" % (getattr(user, "id", None) or "guest")


def remembered_items(cookies, user) -> dict:
    """{item key: True/False} from the person's cookie ("systems:1,layer_business:0")."""
    from urllib.parse import unquote

    raw = unquote(cookies.get(remembered_cookie_name(user)) or "")  # the page script stores it URL-encoded
    out = {}
    for part in raw.split(","):
        key, _, state = part.partition(":")
        if key and state in ("0", "1") and key.replace("_", "").isalnum():
            out[key] = state == "1"
    return out


def platform_admin_tools(user) -> list:
    role = _role(user)
    return [i for i in (_item(user, t, role, None, None) for t in PLATFORM_ADMIN_TOOLS) if i]


def connect_url(user) -> str | None:
    """Where the connect card's one button goes: the connectors page, for a person who may
    connect a source. Nobody else is shown the card (it would promise what they cannot do)."""
    link = resolve(user, SECONDARY[0])
    return href(link) if link is SECONDARY[0] else None  # the Connections entry


# ── The search index ─────────────────────────────────────────────────────────────────────
_KEEP_CASE = {"AI", "ARB", "API", "ADM", "CTO", "EA", "KPIs", "RACI", "APQC", "ArchiMate", "GitHub", "Jira",
              "TOGAF", "SAFe", "Salesforce", "Power", "Platform", "Microsoft", "ServiceNow", "Slack", "Teams"}


def sentence_case(label: str) -> str:
    """'Traceability Matrix' -> 'Traceability matrix'; acronyms and product names keep their case."""
    out = []
    for i, word in enumerate(label.split(" ")):
        if i == 0 or word in _KEEP_CASE or not word[:1].isupper() or not word[1:].islower():
            out.append(word)
        else:
            out.append(word[:1].lower() + word[1:])
    return " ".join(out)


def _catalogue() -> list:
    """Every page the All-modules directory lists for the signed-in person."""
    from app.modules.modules_directory.routes import visible_module_links

    return visible_module_links()


# ── ArchiMate element types (ADR-0018) ──────────────────────────────────────────────────
# Every element type has one list page. The sidebar's Model group holds the types of each layer;
# the index also offers the plural names ("Business actors") and the types the sidebar does not list.
_ELEMENT_TYPE_HOME = {"implementation": "changes", "application": "systems", "technology": "systems",
                      "physical": "systems", "strategy": "systems", "business": "systems", "motivation": "systems"}
_COMPLIANCE_TYPES = {"Requirement", "Constraint", "Principle", "Assessment"}


def element_type_pages() -> list:
    """(label, layer, element type, endpoint, url values, entry key) for every ArchiMate type with a list page."""
    try:
        from app.models import archimate_element_types as types
        from app.modules.architecture.routes.archimate_crud.routes import MODEL_REGISTRY
    except Exception:  # noqa: BLE001 - the element pages are optional for search
        return []
    rows = []
    for layer in ("strategy", "business", "application", "technology", "physical", "motivation", "implementation"):
        for element_type in getattr(types, layer.upper() + "_ELEMENT_TYPES", []):
            # "Application" is the portfolio record itself: its list is the Applications page.
            if element_type not in MODEL_REGISTRY or element_type == "Application":
                continue
            home = "compliance" if element_type in _COMPLIANCE_TYPES else _ELEMENT_TYPE_HOME[layer]
            if element_type in _CANONICAL_TYPE_PAGES:
                rows.append((_type_label(element_type), layer, element_type, _CANONICAL_TYPE_PAGES[element_type], {}, home))
            else:
                rows.append((_type_label(element_type), layer, element_type, "archimate_crud.list_elements",
                             {"layer": layer, "element_type": element_type}, home))
    return rows


def search_index(user) -> list:
    """Every page this user may open, once, with the names a person may type for it.

    Each row: label, url, endpoint, section (where it sits in the sidebar) and terms (label plus
    aliases). A page two entries open (Applications, Application component) is one row that answers
    to both names. Sidebar entries first, then the platform tools, the named pages (PAGES) and the
    catalogue of every other page.
    """
    from flask import url_for

    role = _role(user)
    rows: list = []
    by_url: dict = {}

    def add(label, endpoint, url, section, terms):
        if not url:
            return
        row = by_url.get(url)
        if row is not None:
            # The same page under another name: that name finds it too.
            for term in (label, *terms):
                if term not in row["terms"]:
                    row["terms"].append(term)
            return
        row = {"label": label, "endpoint": endpoint, "url": url, "section": section,
               "terms": [label, *[t for t in terms if t != label]]}
        by_url[url] = row
        rows.append(row)

    for links in (TOP, WORK, MODEL, SECONDARY, USER_MENU, PLATFORM_ADMIN_TOOLS, PAGES):
        for link in links:
            if link.toggle:
                for child in link.children:
                    child_link = resolve(user, child, role)
                    if child_link is not None:
                        section = link.label if link.group == "work" else "Model, " + link.label
                        add(child.label, child_link.endpoint, href(child_link), section,
                            list(child.aliases) + (list(child_link.aliases) if child_link is not child else []))
                continue
            chosen = resolve(user, link, role)
            if chosen is None:
                continue
            terms = list(link.aliases) + (list(chosen.aliases) if chosen is not link else [])
            owner = entry_for_endpoint(chosen.endpoint)
            if link in MODEL:
                section = "Model"
            else:
                section = owner.label if owner is not None and owner.key != link.key else JOBS.get(link.job, "")
            add(link.label, chosen.endpoint, href(chosen), section, terms)

    for label, layer, element_type, endpoint, values, home in element_type_pages():
        if not _registered(endpoint):
            continue
        try:
            url = url_for(endpoint, **values)
        except Exception:  # noqa: BLE001
            continue
        plural = label + ("es" if label.endswith(("s", "x", "ch")) else "s")
        add(plural, endpoint, url, "Model, " + layer.capitalize(), [label, element_type, f"{layer} {label}"])

    try:
        catalogue = _catalogue()
    except Exception:  # noqa: BLE001 - outside a signed-in request there is no catalogue
        catalogue = []
    from app.utils.search_text import normalise_search_text

    # A catalogue page whose name is already a name (or old name) of a page above is the
    # same idea under its old name, merged into that page (ADR-0015): offered once.
    known = {normalise_search_text(term) for row in rows for term in row["terms"]}
    for page in catalogue:
        endpoint = page["endpoint"]
        if not _registered(endpoint) or normalise_search_text(page["label"]) in known:
            continue
        try:
            url = url_for(endpoint, **(page.get("query_params") or {}))
        except Exception:  # noqa: BLE001
            continue
        owner = entry_for_endpoint(endpoint)
        label = sentence_case(page["label"])
        add(label, endpoint, url, owner.label if owner is not None else "", [page["label"]])
    return rows


def search_pages(user, query: str, limit: int = 12) -> list:
    """Pages whose name or an alias contains the query, spelling-blind and typo-tolerant, best match first."""
    from app.utils.search_text import normalise_search_text, fuzzy_match_score

    q = normalise_search_text(query or "")
    if not q:
        return []
    scored = []
    for order, row in enumerate(search_index(user)):
        best_exact = -1
        best_fuzzy = -1
        whole = False
        for rank, term in enumerate(row["terms"]):
            t = normalise_search_text(term)
            exact, fuzzy = fuzzy_match_score(q, t)
            if exact == 0 and fuzzy == 0:
                continue
            # a name or alias that is the whole query ("Integrations") beats one that only holds it
            whole = whole or t == q
            # Exact matches rank first (higher exact = better), then fuzzy matches
            # Within same match type, prefer earlier terms (lower rank)
            score = (-exact, -fuzzy, rank)
            if best_exact == -1 or score < ( -best_exact, -best_fuzzy, 999):
                best_exact, best_fuzzy = exact, fuzzy
        if best_exact != -1:
            scored.append((-best_exact, not whole, -best_fuzzy, order, row))
    scored.sort(key=lambda item: item[:4])
    return [item[-1] for item in scored[:limit]]


# ── Breadcrumbs ──────────────────────────────────────────────────────────────────────────
def trail(user, endpoint: str | None) -> list:
    """[{"label", "href"}] for the sidebar entry a page sits under ("Systems" above "Capability map").

    Empty for Home and for a page that is itself a sidebar entry's landing page.
    """
    entry = entry_for_endpoint(endpoint)
    if entry is None or entry.key == "home":
        return []
    if entry.toggle:
        # A collapsible item has no page of its own: the trail leads to the first page it holds
        # that this person may open.
        chosen = next((c for c in (resolve(user, child) for child in entry.children) if c is not None), None)
    else:
        chosen = resolve(user, entry)
    if chosen is not None and chosen.endpoint == endpoint:
        return []
    return [{"label": entry.label, "href": href(chosen) if chosen is not None else None}]



def _holds(link: NavLink, endpoint: str) -> bool:
    # an entry that is a filtered view of a shared page (a layer of the model) does not own that page
    if endpoint == link.endpoint and not link.url_values or (link.fallback is not None and endpoint == link.fallback.endpoint):
        return True
    return endpoint in link.owns or any(o.endswith((".", "_")) and endpoint.startswith(o) for o in link.owns)


def filter_keys() -> set:
    """The query keys a sidebar item filters a shared page by (layer, element type, viewpoint ...)."""
    keys = set()

    def walk(links):
        for link in links:
            keys.update(k for k, _ in link.url_values)
            walk(link.children)

    walk(ALL_ENTRIES)
    return keys


def page_trail(user, endpoint: str | None, args=None) -> list:
    """[(label, href)] for where a page sits in the sidebar: its section, then its entry.

    The page header puts Home in front and the page itself (or the record it shows) at the end,
    so every breadcrumb reads the way the sidebar is laid out. A section such as Spend has no
    page of its own, so it carries no link. Empty for Home and for pages the sidebar does not hold.
    """
    # A filtered view of a shared page (a layer of the model) sits where the sidebar item with that
    # filter sits, not under the entry that owns the unfiltered page.
    if args:
        for parent in ALL_ENTRIES:
            for c in parent.children if parent.toggle else ():
                if c.url_values and c.endpoint == endpoint and all(args.get(k) == str(v) for k, v in c.url_values):
                    chosen = resolve(user, c)
                    return [(sentence_case(parent.label), None),
                            (sentence_case(c.label), href(chosen) if chosen is not None else None)]
    entry = entry_for_endpoint(endpoint)
    if entry is None or entry.key == "home":
        return []
    if entry is _MODEL_ROOT:
        # a model page the sidebar draws under a layer (Strategy > Value stream) sits there
        for layer in MODEL:
            child = next((c for c in layer.children if _holds(c, endpoint)), None) if layer.toggle else None
            if child is not None:
                chosen = resolve(user, child)
                return [(sentence_case(layer.label), None),
                        (sentence_case(child.label), href(chosen) if chosen is not None else None)]
    if not entry.toggle:
        chosen = resolve(user, entry)
        return [(sentence_case(entry.label), href(chosen) if chosen is not None else None)]
    crumbs = [(sentence_case(entry.label), None)]
    child = next((c for c in entry.children if _holds(c, endpoint)), None)
    if child is not None:
        chosen = resolve(user, child)
        crumbs.append((sentence_case(child.label), href(chosen) if chosen is not None else None))
    return crumbs

# ── Stage awareness ──────────────────────────────────────────────────────────────────────
def has_connected_source(org_id) -> bool | None:
    """True once the organisation has connected a source; None when that cannot be told."""
    if not org_id:
        return None
    try:
        from app.extensions import db
        from app.models.connector_config import ConnectorConfig

        query = ConnectorConfig.query.filter(ConnectorConfig.organization_id == org_id)
        return bool(db.session.query(query.exists()).scalar())
    except Exception:  # noqa: BLE001 - a sidebar card must not 500 a page
        return None
