"""Builds app/release_waves/wave_map.json: the wave of every registered route rule.

The wave plan's page list (wave_plan_pages.json, a copy of the release plan's
page-to-wave map) names the pages. A route rule that is not a page (a form post,
a data call) takes the wave of the page it belongs to. Every rule gets an entry
and a stated basis; a rule no page of the plan reaches is wave ``X`` ("not in any
planned wave"): it is closed in every wave, never open by default.

Basis values: ``page`` (the rule is a page of the plan), ``override`` (this module
names it), ``prefix`` (it sits under a page of the plan), ``api-prefix`` (the same
after the /api prefix is removed), ``blueprint`` (every mapped rule of its blueprint
is in one wave), ``unplanned`` (nothing maps it; closed).
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLAN_PATH = HERE / "wave_plan_pages.json"
MAP_PATH = HERE / "wave_map.json"

# Waves that are never gated by the switch: infrastructure (A), release foundation (W0) and
# operations (W6, platform administrators only through their own permission checks).
PASS_WAVES = ("A", "W0", "W6")

_PARAM = re.compile(r"<[^>]*>")


def norm(rule: str) -> str:
    """A route rule with every parameter written <x>, so <int:id> and <id> are one rule."""
    return _PARAM.sub("<x>", rule)


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


# Rules that are not pages of the plan but are named here, in priority order (first match wins).
# (prefix, wave, group, why)
PREFIX_OVERRIDES: tuple = (
    ("/static/", "A", "infra", "static files"),
    ("/health", "A", "infra", "health checks"),
    ("/version", "A", "infra", "build version"),
    ("/robots.txt", "A", "infra", "crawler rules"),
    ("/sitemap.xml", "A", "infra", "sitemap (lists released pages only)"),
    ("/llms.txt", "A", "infra", "llms.txt (lists released pages only)"),
    ("/llms-full.txt", "A", "infra", "llms-full.txt (lists released pages only)"),
    ("/favicon.ico", "A", "infra", "icon"),
    ("/status", "A", "infra", "service status"),
    ("/apispec.json", "W6", "PG-OPS", "API documentation, platform administrators"),
    ("/apidocs", "W6", "PG-OPS", "API documentation, platform administrators"),
    ("/flasgger_static", "W6", "PG-OPS", "API documentation assets"),
    ("/oauth2-redirect.html", "W6", "PG-OPS", "API documentation sign-in"),
    ("/api/client-error", "A", "infra", "browser error reports"),
    ("/csp-report", "A", "infra", "browser security reports"),
    ("/early-access", "A", "infra", "the early-access pages themselves"),
    ("/oauth/", "W1", "PG-CON", "assistant sign-in, consent, token and registration: the connector stays on at wave 1 for signed-in users"),
    ("/.well-known/", "W1", "PG-CON", "assistant discovery documents (connector stays on at wave 1)"),
    ("/mcp", "W1", "PG-CON", "the assistant connection (MCP): on at wave 1 for signed-in users, advertised from wave 2"),
    ("/opportunities", "W3", "PG-OPP", "the ranked list of opportunities a person types by name"),
    ("/recommendations", "W1", "PG-REC", "the recommendations drawn from the signed-in person's own model; a visitor who is not signed in still gets the waiting list"),
    ("/simulations", "W3", "PG-SIM", "the simulation scenarios a person types by name"),
    ("/strategic/api/scenarios", "W3", "PG-SIM", "the saved scenarios the simulation screen lists and keeps"),
    ("/strategic/api/simulations", "W3", "PG-SIM", "the call the simulation screen makes: it opens in the wave of the screen"),
    ("/architecture/decision-tables", "W1", "PG-BL", "decision tables open from the business process pages, which are in wave 1"),
    ("/roles", "W1", "PG-PUB", "public roles hub"),
    ("/connectors", "W1", "PG-PUB", "public connectors hub"),
    ("/admin/rate-limits", "W1", "PG-ORG", "the organisation's request allowances: the rules page, adding a rule and removing one, for administrators"),
    ("/admin/signups", "W6", "PG-OPS", "where sign-ups came from: the platform team only"),
    ("/intelligence/answers/element/", "W2", "PG-ANS", "the answer for one element, beside the other answer pages"),
    # Reads the assistant connection and token clients make (all GET): the connector is on at wave 1.
    ("/api/v1/elements", "W1", "PG-CON", "element reads for token clients and the assistant connection"),
    ("/archimate/api/elements/search$", "W1", "PG-CON", "element search the assistant connection calls"),
    ("/archimate/api/elements/<int:element_id>/detail$", "W1", "PG-CON", "element detail the assistant connection calls"),
    ("/api/v1/intelligence/accountability/", "W1", "PG-CON", "the accountability answer the assistant connection calls"),
    ("/api/v1/intelligence/compliance/", "W1", "PG-CON", "the compliance answer the assistant connection calls"),
    ("/api/v1/intelligence/data/", "W1", "PG-CON", "the data answer the assistant connection calls"),
    ("/api/v1/intelligence/impact/", "W1", "PG-CON", "the impact answer the assistant connection calls"),
    ("/api/v1/intelligence/portfolio/", "W1", "PG-CON", "the portfolio answer the assistant connection calls"),
    ("/api/v1/intelligence/programme/", "W1", "PG-CON", "the programme answer the assistant connection calls"),
    ("/api/v1/intelligence/risk/", "W1", "PG-CON", "the risk answer the assistant connection calls"),
    ("/api/v1/intelligence/strategy/", "W1", "PG-CON", "the strategy answer the assistant connection calls"),
    ("/api/v1/intelligence/answers", "W1", "PG-CON", "the stored answer read by the assistant connection and token clients"),
    # Pages and calls a person reaches from what sign-up loads: the capability map, canvases, frameworks and compliance.
    ("/dashboard/api/compliance/", "W1", "PG-GOV", "adopted frameworks, controls and regulatory changes on the compliance page"),
    ("/dashboard/api/templates/frameworks", "W1", "PG-FW", "the framework templates the frameworks pages list"),
    ("/business-architecture/processes", "W1", "PG-MAP", "business processes, their flow diagram and BPMN file: the business architect works in wave 1"),
    ("/capability-maturity/framework/", "W1", "PG-MAP", "a maturity framework and its domains"),
    ("/capability-maturity/frameworks-overview", "W1", "PG-MAP", "maturity frameworks overview address"),
    ("/capability-maturity/api/", "W1", "PG-MAP", "capability maturity reads"),
    ("/frameworks", "W1", "PG-FW", "the frameworks gallery"),
    ("/safe/portfolio", "W1", "PG-FW", "the SAFe portfolio"),
    ("/api/safe/portfolio", "W1", "PG-FW", "the SAFe portfolio data"),
    ("/api/system-settings", "W6", "PG-OPS", "platform settings page data, platform administrators"),
    # Calls a wave-1 screen makes (found by walking the wave-1 preview; "$" means this address only).
    ("/api/notifications", "W1", "PG-ACC", "the notification bell in the signed-in shell"),
    ("/api/bu-scope$", "W1", "PG-ACC", "business-unit scope read by the signed-in shell"),
    ("/api/archimate/viewpoints/<viewpoint_key>/diagram$", "W1", "PG-APP", "the application cooperation diagram on the applications page"),
    ("/dashboard/api/archimate-elements/<int:element_id>/correct$", "W1", "PG-APP", "correct an element's type from the application page"),
    ("/api/ea-workflows/instances$", "W1", "PG-APP", "workflow list shown on the application page"),
    ("/offers/inquire", "W1", "PG-PUB", "public enquiry and waiting-list form"),
    ("/t/plan-click", "W1", "PG-PUB", "public plan click counter"),
    ("/signup", "W1", "PG-ACC", "sign-up address"),
    ("/register", "W1", "PG-ACC", "sign-up address"),
    ("/login", "W1", "PG-ACC", "sign-in address"),
    ("/account/", "W1", "PG-ACC", "account, sign-in, two-factor and the first sit-down"),
    ("/admin/billing/", "W1", "PG-ORG", "plan checkout, cancel and billing"),
    ("/architecture/sample-company/", "W1", "PG-NEW", "start the sample company"),
    ("/dashboard/api/onboarding-complete", "W1", "PG-NEW", "first sit-down progress"),
)

# Rules whose path is ambiguous after normalisation (two rules read /<x>): named by endpoint.
ENDPOINT_OVERRIDES: dict = {
    "main.public_legal_page": ("W1", "PG-PUB", "legal pages"),
    "main.public_site_page": ("W1", "PG-PUB", "public site pages"),
    "main.indexnow_key_file": ("A", "infra", "search engine key file"),
    "main.index": ("W1", "PG-PUB", "home"),
    "main.public_module": ("W1", "PG-PUB", "module pages; the page's own wave decides what it shows"),
    "main.public_share": ("W1", "PG-PUB", "the page one module recording is shared from; opens with the module page it shows"),
    "risk.risk_loss_save": ("W1", "PG-RISK", "the yearly loss a risk is given, saved from the risk page that opens in wave 1"),
}

# Plan pages whose title names the screen, not an address: the address is in the table.
# Plan pages moved to wave 1, by address: the connected-assistants page and the consent page stay on with the
# assistant connection; a stored answer at its permanent address is what the assistant returns and cites; the
# compliance page and the industry-framework pages open what sign-up loads.
PAGE_WAVE_OVERRIDES: dict = {
    "/account/manage/connected-assistants": "W1",
    "/oauth/authorize": "W1",
    "/dashboard/compliance": "W1",
    "/industry-apqc": "W1",
    "/intelligence/answers/<address>": "W1",
}

# Page groups moved to wave 1 as a whole: what a customer chooses at sign-up is loaded and must open.
# Capability map, maturity and capability records; value streams and business model canvases;
# industry frameworks, enterprise-architecture workflows and the delivery boards.
WAVE_ONE_GROUPS: tuple = ("PG-MAP", "PG-VS", "PG-FW")
# Address prefixes (whole path segments) moved to wave 1 whatever their group: every lens of the capability map
# and the capability roadmap.
WAVE_ONE_PREFIXES: tuple = ("/capability-map", "/genome/roadmap")
# Waves a group or prefix move lifts to wave 1; a retired address (RT) keeps its state and its destination.
MOVABLE_WAVES = ("W2", "W3", "W4", "W5")

# Endpoints whose retired address answers under a name of its own, so the early-access page says what was clicked.
ENDPOINT_GROUPS: dict = {
    "archimate_crud.dashboard": "PG-BL",
    "business_case.index": "PG-BC",
}

# Closed addresses a buyer knows by name: each lands on the early-access page of its own capability, not the hub.
ADDRESS_GROUPS: dict = {
    "/vendors": "PG-VEN",
    "/vendors/": "PG-VEN",
    "/roadmaps": "PG-ROAD",
    "/technology-roadmap": "PG-ROAD",
    "/hybrid-roadmap": "PG-ROAD",
    "/integrations": "PG-CON",
}

# A staff address that only forwards to a staff page: closed like every other internal address, so a signed-out
# visitor reaches the hub instead of a sign-in form.
INTERNAL_CLOSED_ADDRESSES: dict = {
    "/usage-analytics/": "PG-OPS",
}

# Capabilities a buyer looks for by name whose address is a page of their own, with what a person reads there.
NAMED_GROUPS: dict = {
    "PG-OPP": ("Opportunities", "Rank the opportunities in your portfolio, such as applications that overlap or capabilities served twice, each traced to the applications and costs in your model."),
    "PG-REC": ("Recommendations", "Read the recommendations drawn from your own model, each with the applications, capabilities and costs it rests on, before you decide."),
    "PG-SIM": ("Simulation scenarios", "Try a change on a copy of your model and compare the scenarios side by side, so the cost and the risk of each option are visible before you commit."),
}

PAGE_ADDRESS_OVERRIDES: dict = {
    "Website reading progress and company profile confirm screen": "/account/welcome",
    "Start the sample company": "/architecture/sample-company/load",
    "Plan checkout and payment result": "/admin/billing/",
    "Cancel plan and its confirmation": "/admin/billing/cancel",
    "Shared error, no-permission and sign-in-again screens": "/account/welcome",
}

GROUP_FALLBACK = {
    "PG-RT": ("Moved addresses", ""),
    "PG-RTP": ("Moved addresses", ""),
    "PG-BL": ("Business Layer", "The Business Layer will show every business capability, process, service and actor of your enterprise in one place."),
    "PG-BC": ("Business Cases", "Business Cases will let you build the case for a change: its costs, benefits and options, ending in a recommendation, all linked to your model."),
    "PG-EA": ("Integrations, code generation and vendor selection", ""),
    "PG-OPS": ("Platform operations", ""),
    "infra": ("Platform plumbing", ""),
}


# What a customer reads for a group: plain capability names, never plan wording. Applied last, to every group.
GROUP_TEXT_OVERRIDES: dict = {
    "PG-CON": ("Connectors and integrations", None),
    "PG-ELE": (None, "Open any element and see what depends on it."),
    "PG-EA": ("Integrations, code generation and vendor selection", None),
    "PG-RT": ("Moved addresses", ""),
    "PG-RTP": ("Moved addresses", ""),
    "PG-NEW": ("Account set-up, connectors and assistants", ""),
    "PG-PUB": ("Public pages", ""),
    "PG-OPS": ("Platform tools", ""),
}


def _title_of(code: str, text: str) -> tuple[str, str]:
    head, _, tail = text.partition(":")
    if not tail:
        return GROUP_FALLBACK.get(code, (head.strip().rstrip("."), ""))
    tail = tail.strip()
    return head.strip(), tail[:1].upper() + tail[1:].rstrip(".") + "."


# Blueprints that are plumbing, not capabilities a customer buys: no early-access page. A closed address of one leads to the hub.
BLUEPRINT_INTERNAL: dict = {
    "api_login": "Sign-in for connected tools",
    "api_logout": "Sign-out for connected tools",
    "api_pipeline": "Import enrichment pipeline",
    "api_session_status": "Session status",
    "batch_processing": "Background batch jobs",
    "capabilities_redirect": "Business capabilities address",
    "csp_report": "Browser security reports",
    "dashboard": "Dashboard",
    "dashboard_pages": "Dashboard data calls",
    "dod_api": "Definition of done checks",
    "main": "Workspace data calls",
    "capability_archimate": "Capability coverage in the model",
    "maturity_management": "Maturity import",
    "metamodel_properties": "Metamodel properties",
    "restx_doc": "API documentation pages",
    "sidebar_api": "Navigation data",
    "suggestions": "Suggestion feed",
    "usage_analytics": "Usage analytics",
}

# A blueprint that speaks for a capability another group already names: its closed addresses lead to that group's page.
BLUEPRINT_ALIAS: dict = {
    "ai": "BP:unified_duplicate",
    "application_management": "BP:application_mgmt",
    "application_tags": "BP:application_mgmt",
    "apqc_hierarchy": "BP:apqc",
    "archimate_api": "BP:archimate",
    "codegen": "BP:code_generation",
    "confidence_review_dashboard": "BP:confidence_review",
    "connectors": "PG-CON",
    "governance": "PG-GOV",
    "intelligence_ui": "BP:intelligence_api",
    "sso": "PG-SSO",
    "unified_vendors": "PG-VEN",
    "unified_vendors_api": "PG-VEN",
    "vendor": "PG-VEN",
    "vendor_management": "PG-VEN",
    "vendors_api": "PG-VEN",
    "webhook_routes": "BP:webhook",
}

# What a customer reads as the name of a capability that has no page group of its own, by blueprint.
BLUEPRINT_NAMES: dict = {
    "_journey_v2_redirect": "Guided solution design",
    "acm": "Application capability mapping",
    "advanced_tco": "Total cost of ownership",
    "ai_assistance": "AI assistance",
    "ai_gap_detection": "AI gap detection",
    "ai_systems_genome": "AI systems register",
    "api_v1": "Programmatic access",
    "application_merging": "Merging duplicate applications",
    "application_mgmt": "Application management",
    "apqc": "APQC process classification",
    "arb_workflow": "Architecture review board workflow",
    "archimate": "ArchiMate model",
    "archimate_export": "ArchiMate export",
    "archimate_generation": "ArchiMate generation",
    "architect_ui": "Architect workspace",
    "business_architecture": "Business architecture",
    "capability_governance": "Capability governance",
    "capability_management": "Capability management",
    "capability_taxonomy": "Capability taxonomy",
    "code_generation": "Code generation",
    "communication_log": "Communication log",
    "confidence_review": "Confidence review",
    "consolidation": "Application consolidation",
    "coverage_matrix": "Coverage matrix",
    "ea_phase_summary": "Architecture phase summary",
    "enterprise_export": "Enterprise export",
    "epic_bp": "Epics and stories",
    "flow_analytics": "Flow analytics",
    "gdpr_bp": "Data protection requests",
    "governance_api": "AI model monitoring",
    "import_history": "Import history",
    "intelligence_api": "Enterprise intelligence",
    "motivation_api": "Motivation model",
    "phase_a": "Architecture vision phase",
    "phase_d": "Technology architecture phase",
    "phase_f": "Migration planning phase",
    "phase_g": "Implementation governance phase",
    "prioritisation": "Backlog prioritisation",
    "procurement": "Procurement",
    "review_queue": "Review queue",
    "risk": "Risks, assumptions, issues and dependencies",
    "roadmap_api": "Roadmap generation",
    "roadmap_builder": "Roadmap builder",
    "security": "Security controls",
    "solution_composer": "Solution composer",
    "sprint": "Sprints",
    "stakeholder_map_api": "Stakeholder map",
    "strategic": "Strategic planning",
    "strategic_risks_hardened": "Strategic risks",
    "transformation_api": "Transformation programmes",
    "unified_duplicate": "Duplicate detection",
    "unified_import": "Import",
    "vendor_analysis": "Vendor analysis",
    "vendor_discovery": "Vendor discovery",
    "vendor_mdm": "Vendor master data",
    "vendor_product": "Vendor products",
    "viewpoint_api": "Viewpoints",
    "webhook": "Webhooks",
    "wizard_ai": "AI-guided set-up",
    "workflow_optimization": "Workflow optimisation",
}

# What each of those does for the buyer and why it is better, in the words of the capability model. One page, one description.
BLUEPRINT_BLURBS: dict = {
    "_journey_v2_redirect": "Take a solution from a first idea to a reviewed design in one guided path, with every choice recorded against your own model instead of in a slide deck.",
    "acm": "Map each application to the business capabilities it supports and see at once where capabilities are covered twice or not at all.",
    "advanced_tco": "See the full cost of owning an application over its life, with benchmarks beside it, so a replacement is judged on the number that matters.",
    "ai_assistance": "Ask for help where you are working and get an answer built from your own architecture, with sensitive data masked before anything is sent.",
    "ai_gap_detection": "Find the capabilities that are thinly covered or covered by one ageing application, ranked by how much they matter to the business.",
    "ai_systems_genome": "Keep one register of every AI system in the enterprise, with a named owner for each, so nobody has to ask who answers for a model.",
    "api_v1": "Read and write your architecture from your own tools with a token, using the same rules and the same data as the product itself.",
    "application_merging": "Spot applications that are really the same product, review the match, and merge them without losing a single link to your model.",
    "application_mgmt": "Manage the whole application estate in one place: owners, lifecycle, documents, analysis history and the capabilities each application serves.",
    "apqc": "Classify your processes against the APQC framework and see how they line up with your capabilities, without retyping the framework by hand.",
    "arb_workflow": "Take a proposal through your review board with its transitions, checks and conditional approvals recorded, so every decision can be traced.",
    "archimate": "Work in a real ArchiMate model, with composer, relationships, audit log and derived views, rather than boxes drawn on a canvas.",
    "archimate_export": "Send your model out as ArchiMate, capability maps or cooperation views for the tools and people that need it, straight from the live model.",
    "archimate_generation": "Generate ArchiMate elements from your capabilities and vendors, so a model starts from what you already know instead of a blank page.",
    "architect_ui": "See an element's lifecycle, what it affects and the viewpoints it appears in, from one workspace built for architects.",
    "business_architecture": "Describe the business you run, from capabilities to the people and services around them, as one connected model.",
    "capability_governance": "Put a capability under governance: review it, attach the processes and workflows that run it, and see its status on one dashboard.",
    "capability_management": "Keep the capability list clean: find duplicates, merge them, and keep names consistent as the list grows.",
    "capability_taxonomy": "Define the domains, levels and rules your capability model follows, with an audit trail of every change to them.",
    "code_generation": "Turn an approved architecture into working code and templates for your applications, drawn from the same model your reviewers signed off.",
    "communication_log": "Record what was said to whom about a decision, so the conversation around an architecture is kept with the architecture.",
    "confidence_review": "Score how much to trust each suggested link or change, escalate the doubtful ones, and approve in bulk what is clear.",
    "consolidation": "Get a plan for retiring overlapping applications, built from your own portfolio and cost data.",
    "coverage_matrix": "See which capabilities each application covers in one matrix, with gaps and overlaps marked and the whole grid ready to export.",
    "ea_phase_summary": "See where each stage of your architecture method stands, with the ArchiMate elements and lineage behind it.",
    "enterprise_export": "Take your portfolio, solutions and model out as Excel, PDF, PowerPoint or ArchiMate files, produced from live data.",
    "epic_bp": "Break a change into epics, stories and subtasks and see the whole tree, tied to the architecture it delivers.",
    "flow_analytics": "Measure lead time, cycle time, throughput and work in progress for your delivery work, from the work itself.",
    "gdpr_bp": "Answer a data protection request by exporting or deleting a person's data and tracking the request to completion.",
    "governance_api": "Watch the accuracy and drift of the models that assist you, and see what is queued for retraining, before quality slips.",
    "import_history": "See every import you have run, what it changed and how far it got, with the option to export the record.",
    "intelligence_api": "Ask the enterprise a question and get an answer with the evidence behind it, and see how the answer changed over time.",
    "motivation_api": "Connect drivers to goals and assess them, so each change can be traced back to the reason you are making it.",
    "phase_a": "Capture the vision stage of your method: drivers, principles and the summary that explains why the work starts.",
    "phase_d": "Assess your technology stack for complexity and debt, and see which parts need attention first.",
    "phase_f": "Plan the migration: effort, sequence and roadmap, drawn from the architecture you have already modelled.",
    "phase_g": "Govern implementation: review board status, compliance checks and recorded derogations, kept with the project.",
    "prioritisation": "Rank the backlog with the scoring methods your teams already use, and see one ordered list.",
    "procurement": "Manage contracts, compliance findings and renewals in one place, with extraction and briefs prepared from the contract text.",
    "review_queue": "Work through everything waiting for your approval from a single queue, and approve or reject in bulk.",
    "risk": "Keep risks, assumptions, issues and dependencies in one log, each linked to the part of the model it touches.",
    "roadmap_api": "Generate a roadmap from your gaps and plateaus, preview it, and detect conflicts before anyone commits.",
    "roadmap_builder": "Build a roadmap with a critical path and dependency graph drawn from your plateaus and work.",
    "security": "See the audit trail of who did what, check its integrity, and scan and mask sensitive data.",
    "solution_composer": "Compose a solution on a canvas from the elements of your own model, import a method stage and keep every version.",
    "sprint": "Plan sprints with their cards and burndown, tied to the architecture work they deliver.",
    "stakeholder_map_api": "Map the people who matter to a change, with an engagement approach for each, so nobody important is surprised.",
    "strategic": "Link strategy to the review board and to capability status, so strategic decisions reach the portfolio.",
    "strategic_risks_hardened": "Track strategic risks with their detail and ownership, linked to the capabilities and decisions they threaten.",
    "transformation_api": "Run transformation programmes with workstreams and role assignments, tied to the model of what is changing.",
    "unified_duplicate": "Find duplicate applications and vendors across the estate, review each match, and approve the consolidation you want.",
    "unified_import": "Bring spreadsheets and exports into the model with analysis first, so you see what will change before it does.",
    "vendor_analysis": "Compare vendors side by side with the provenance of every figure and an export you can hand to a committee.",
    "vendor_discovery": "Discover vendors for a capability, compare them and estimate the cost of owning each before you shortlist.",
    "vendor_mdm": "Keep one clean record per vendor: find duplicates, normalise names and import mappings in one place.",
    "vendor_product": "Know the products each vendor sells, in a hierarchy, with categories extracted for you.",
    "viewpoint_api": "Build the views each stakeholder needs from one model, so a diagram is always a live view and never a copy.",
    "webhook": "Send events from Entelim to your other systems and receive theirs, with retries you can see.",
    "wizard_ai": "Fill in a long form with suggestions drawn from your own data, and accept or change each one.",
    "workflow_optimization": "Find the bottlenecks and automation opportunities in a workflow, with a report of what to fix first.",
}

# What a customer reads under each page group's name. Applied last, to every group that has a page.
GROUP_BLURBS: dict = {
    "PG-ANS": "Ask the model a question and get an answer with its evidence, see it on a map of your enterprise, and read how it has changed.",
    "PG-APP": "Know every application, who owns it, and what you personally are responsible for, in one place.",
    "PG-CHAT": "Work with an assistant that answers from your own architecture, shows what it will change, and waits for your approval first.",
    "PG-CON": "Connect Entelim to the tools your teams already use, and see what each connection reads and writes.",
    "PG-DATA": "See where your data lives, how it flows between systems and who governs it, drawn from the same model as everything else.",
    "PG-DIA": "Compose diagrams from your model, exchange it with other tools and trace any element to what it affects.",
    "PG-EA": "Generate code from approved designs, select vendors against your capabilities, and connect both to the model.",
    "PG-ELE": "Open any element and see what depends on it.",
    "PG-GOV": "Run your review board, record decisions, and keep policy, standards and compliance beside the architecture they govern.",
    "PG-OVR": "See the health of your architecture and how far to trust the model, from figures you can trace to their source.",
    "PG-PEOPLE": "Draw your organisation, assign who is responsible, accountable, consulted and informed, and map stakeholders to changes.",
    "PG-PLAN": "Close the gaps in your architecture with work packages, a portfolio view, rationalisation and investment cases in one plan.",
    "PG-RISK": "Keep a risk register linked to your applications and capabilities, and see what a risk would touch.",
    "PG-ROAD": "Show the roadmap in the shape each audience knows, from one set of plateaus and work.",
    "PG-SHR": "Share an answer or an artefact with someone outside your team, with control over who sees it.",
    "PG-SOL": "Take a solution from idea to approved design, and follow the programme that delivers it.",
    "PG-SSO": "Let your people sign in with your own identity provider, and manage it from the administration pages.",
    "PG-VEN": "Manage vendors, contracts, licences and spend together, and see the exposure each vendor brings.",
    "PG-ORG": "Invite your people, set their roles, read the audit log, and manage billing and settings for your organisation.",
}


# The one groups that answer a page with no entry of its own, so they always have a page.
ALWAYS_PAGE = ("PG-EA",)
# Groups that are not capabilities a customer buys: no early-access page, however their addresses are closed.
NO_PAGE_GROUPS = ("infra", "PG-PUB", "PG-OPS", "PG-RT", "PG-RTP", "PG-NEW")
GENERIC_TITLES = ("this page", "this capability", "capability", "page", "not available")
EARLIER_PATH = HERE / "earlier_addresses.json"
# Where an address that once led to a retired group now leads, when it is a page and not the hub.
RETIRED_GROUP_MOVES: dict = {"PG-RT": "/architecture/adrs/"}


def _opens_at_wave_one(entry: list) -> bool:
    """True when the address answers in the first release: open, or a moved address whose destination ships there."""
    if entry[0] in PASS_WAVES or entry[0] == "W1":
        return True
    return entry[0] == "RT" and bool(entry[3]) and entry[4] == "W1"


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def mark_pages(groups: dict, entries: dict) -> None:
    """Flag the groups with no early-access page: plumbing, moved addresses, and capabilities already open."""
    closed: dict = collections.Counter()
    for entry in entries.values():
        if not _opens_at_wave_one(entry):
            closed[entry[1]] += 1
    for code, info in groups.items():
        internal = code.startswith("BP:") and code[3:] in BLUEPRINT_INTERNAL
        if code in NO_PAGE_GROUPS or internal or (not closed[code] and code not in ALWAYS_PAGE):
            info["page"] = False
            info["blurb"] = ""


def check_groups(groups: dict) -> None:
    """The build fails on a page that would be headed generically, shares a name, an address or words."""
    titles: dict = {}
    blurbs: dict = {}
    for code, info in groups.items():
        if info.get("page") is False:
            continue
        title, blurb = info["title"], info["blurb"]
        if title.strip().lower() in GENERIC_TITLES or not _slug(title):
            raise ValueError(f"{code} is headed {title!r}: an early-access page needs the capability's own name")
        if _slug(title) in titles:
            raise ValueError(f"{code} and {titles[_slug(title)]} share the early-access address /early-access/{_slug(title)}")
        titles[_slug(title)] = code
        if not blurb.strip():
            raise ValueError(f"{code} ({title}) has no description")
        if blurb in blurbs:
            raise ValueError(f"{code} and {blurbs[blurb]} share one description")
        blurbs[blurb] = code


def moved_slugs(groups: dict) -> dict:
    """Every early-access address an earlier release served that is not a page now, and the page it moves to."""
    live = {_slug(info["title"]): code for code, info in groups.items() if info.get("page") is not False}
    earlier = json.loads(EARLIER_PATH.read_text(encoding="utf-8"))
    moved: dict = {}
    for slug, code in sorted(earlier.items()):
        if slug in live:
            continue
        alias = BLUEPRINT_ALIAS.get(code[3:]) if code.startswith("BP:") else None
        target = alias or code
        if code in RETIRED_GROUP_MOVES:
            moved[slug] = RETIRED_GROUP_MOVES[code]
        elif target in groups and groups[target].get("page") is not False:
            moved[slug] = f"/early-access/{_slug(groups[target]['title'])}"
        else:
            moved[slug] = "/early-access"
    return moved


def load_plan() -> dict:
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


def _address(item: dict) -> str | None:
    """The address a plan item names, or None when it names no address."""
    title = item["title"]
    if title in PAGE_ADDRESS_OVERRIDES:
        title = PAGE_ADDRESS_OVERRIDES[title]
    else:
        m = re.search(r"\((/[^)]*)\)\s*$", title)
        if m and not title.startswith("/"):
            title = m.group(1)
    if not title.startswith("/"):
        return None
    title = title.split(" (")[0]
    # A title written with a descriptive slug list stands for the public site pages; those are
    # named by endpoint above.
    if "<legal slug" in title or "<site slug" in title:
        return None
    return title


def _under(address: str, prefix: str) -> bool:
    segs, base = _segments(address), _segments(prefix)
    return len(segs) >= len(base) and segs[: len(base)] == base


def _released_at_one(item: dict) -> bool:
    """True when the plan item is an address, page group or prefix moved to wave 1."""
    address = _address(item) or ""
    if PAGE_WAVE_OVERRIDES.get(address.rstrip("/")) == "W1":
        return True
    if item["wave"] not in MOVABLE_WAVES:
        return False
    return item["reason"] in WAVE_ONE_GROUPS or any(_under(address, prefix) for prefix in WAVE_ONE_PREFIXES)


def _plan_pages(plan: dict) -> dict:
    """norm(address) -> plan item, for plan items that name an address."""
    pages: dict = {}
    for item in plan["items"]:
        address = _address(item)
        if address is not None:
            pages.setdefault(norm(address), item)
    return pages


def _same_page(address: str, pattern: str) -> bool:
    """True when ``address`` is the page ``pattern`` names, a <parameter> segment standing for any value."""
    a, p = _segments(address), _segments(pattern)
    return len(a) == len(p) and all(ps.startswith("<") or ps == s for ps, s in zip(p, a))


def build(rules: list[tuple[str, str]]) -> dict:
    """rules: [(rule string, endpoint)] for every rule of the url_map. Returns the map document."""
    plan = load_plan()
    pages = _plan_pages(plan)
    page_segs = {k: _segments(k) for k in pages}

    groups: dict = {}
    for code, text in plan["reason_codes"].items():
        title, blurb = _title_of(code, text)
        groups[code] = {"title": title, "blurb": blurb}
    for code, (title, blurb) in GROUP_FALLBACK.items():
        groups.setdefault(code, {"title": title, "blurb": blurb})

    for code, (title, blurb) in NAMED_GROUPS.items():
        groups[code] = {"title": title, "blurb": blurb}

    def longest_prefix(path: str) -> str | None:
        segs = _segments(path)
        best = None
        for key, ks in page_segs.items():
            if ks and len(ks) <= len(segs) and segs[: len(ks)] == ks and any(not s.startswith("<") for s in ks):
                if best is None or len(ks) > len(page_segs[best]):
                    best = key
        return best

    def from_item(item: dict, basis: str, inherit: bool = False) -> list:
        wave = item["wave"]
        to = item.get("to") if not inherit else None
        ships = item.get("ships") if not inherit else None
        group = item["reason"]
        if _released_at_one(item):
            return ["W1", group, basis, None, None]
        if wave == "RT" or to:
            dest = (item.get("to") or "").split(" (")[0].split("?")[0]
            dest_item = next((it for key, it in pages.items() if dest.startswith("/") and _same_page(dest, key)), None)
            if wave == "RT":
                group = dest_item["reason"] if dest_item and dest_item["reason"] != "PG-RT" else "PG-RT"
            if to:
                to = to.split(" (")[0]
            # a moved address leads to its destination from the wave the destination opens in
            if dest_item is not None and _released_at_one(dest_item):
                ships = "W1"
                wave = "RT"
        return [wave, group, basis, to, ships]

    entries: dict = {}
    unresolved: list = []
    resolved_bp: dict = collections.defaultdict(collections.Counter)

    def put(key: str, entry: list, endpoint: str) -> None:
        old = entries.get(key)
        if old is not None and old[0] != entry[0]:
            # two rules read alike but sit in different waves: the later wave closes both
            order = {"A": 0, "W0": 0, "W1": 1, "W2": 2, "W3": 3, "W4": 4, "W5": 5, "W6": 0, "EA": 7, "RT": 8, "X": 9}
            if order.get(old[0], 9) >= order.get(entry[0], 9):
                return
        entries[key] = entry

    for rule, endpoint in rules:
        key = norm(rule)
        blueprint = endpoint.split(".")[0]
        if endpoint in ENDPOINT_OVERRIDES:
            wave, group, _why = ENDPOINT_OVERRIDES[endpoint]
            entries[f"endpoint:{endpoint}"] = [wave, group, "override", None, None]
            continue
        item = pages.get(key)
        if item is not None:
            put(key, from_item(item, "page"), endpoint)
            resolved_bp[blueprint][item["wave"]] += 1
            continue
        hit = None
        for prefix, wave, group, _why in PREFIX_OVERRIDES:
            if prefix.endswith("$"):
                matched = rule == prefix[:-1]
            else:
                matched = rule == prefix or rule.startswith(prefix.rstrip("/") + "/")
            if matched:
                hit = (wave, group)
                break
        if hit:
            put(key, [hit[0], hit[1], "override", None, None], endpoint)
            resolved_bp[blueprint][hit[0]] += 1
            continue
        best = longest_prefix(key)
        basis = "prefix"
        if best is None:
            stripped = re.sub(r"^/api(/v\d+)?", "", key) or "/"
            if stripped != key:
                best = longest_prefix(stripped)
                basis = "api-prefix"
        if best is not None:
            put(key, from_item(pages[best], basis, inherit=True), endpoint)
            resolved_bp[blueprint][pages[best]["wave"]] += 1
            continue
        unresolved.append((rule, endpoint))

    for rule, endpoint in unresolved:
        key = norm(rule)
        blueprint = endpoint.split(".")[0]
        waves = set(resolved_bp.get(blueprint, {}))
        code = BLUEPRINT_ALIAS.get(blueprint, f"BP:{blueprint}")
        if len(waves) == 1:
            wave = next(iter(waves))
            put(key, [wave, code, "blueprint", None, None], endpoint)
        else:
            put(key, ["X", code, "unplanned", None, None], endpoint)
        if blueprint not in BLUEPRINT_ALIAS:
            name = BLUEPRINT_NAMES.get(blueprint) or BLUEPRINT_INTERNAL.get(blueprint)
            if name is None:
                raise ValueError(f"blueprint {blueprint!r} has no name: add it to BLUEPRINT_NAMES, BLUEPRINT_ALIAS or BLUEPRINT_INTERNAL")
            groups.setdefault(code, {"title": name, "blurb": BLUEPRINT_BLURBS.get(blueprint, "")})

    # a retired address that leads to a page is named for the page it leads to
    for key, entry in entries.items():
        if entry[1] == "PG-RTP" and entry[3]:
            dest = entry[3].split("?")[0]
            dest_item = next((it for pk, it in pages.items() if dest.startswith("/") and _same_page(dest, pk)), None)
            if dest_item is not None and dest_item["reason"] not in ("PG-RT", "PG-RTP"):
                entry[1] = dest_item["reason"]
        if entry[1] == "PG-RT" and _under(key, "/architecture/adrs"):
            entry[1] = "PG-GOV"

    for rule, endpoint in rules:
        if endpoint in ENDPOINT_GROUPS and norm(rule) in entries:
            entries[norm(rule)][1] = ENDPOINT_GROUPS[endpoint]

    for key, code in ADDRESS_GROUPS.items():
        if key in entries:
            entries[key][1] = code

    for key, code in INTERNAL_CLOSED_ADDRESSES.items():
        if key in entries:
            entries[key][0] = "X"
            entries[key][1] = code

    for code, (title, blurb) in GROUP_TEXT_OVERRIDES.items():
        if code in groups:
            if title is not None:
                groups[code]["title"] = title
            if blurb is not None:
                groups[code]["blurb"] = blurb

    for code, text in GROUP_BLURBS.items():
        if code in groups:
            groups[code]["blurb"] = text

    # a group a rule uses must exist
    for entry in entries.values():
        if entry[1] not in groups:
            raise ValueError(f"a rule uses the group {entry[1]!r}, which has no name")

    mark_pages(groups, entries)
    check_groups(groups)
    moved = moved_slugs(groups)

    return {
        "version": 1,
        "source": "wave_plan_pages.json, built by scripts/build_wave_map.py",
        "pass_waves": list(PASS_WAVES),
        "groups": {k: groups[k] for k in sorted(groups)},
        "moved_slugs": moved,
        "rules": {k: entries[k] for k in sorted(entries)},
    }


def dump(doc: dict) -> str:
    """The map as text: one rule per line, so a change to one rule is one line in a diff."""
    head = {k: v for k, v in doc.items() if k != "rules"}
    lines = ["{"]
    for key in sorted(head):
        lines.append(json.dumps(key) + ":" + json.dumps(head[key], sort_keys=True, ensure_ascii=False, separators=(",", ":")) + ",")
    lines.append('"rules":{')
    rules = doc["rules"]
    keys = sorted(rules)
    for i, key in enumerate(keys):
        comma = "," if i < len(keys) - 1 else ""
        lines.append(json.dumps(key, ensure_ascii=False) + ":" + json.dumps(rules[key], ensure_ascii=False, separators=(",", ":")) + comma)
    lines.append("}")
    lines.append("}")
    return "\n".join(lines) + "\n"
