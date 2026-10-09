"""Architecture Journey Routes — guided solution architecture generation.

Blueprint: architecture_journey_bp, url_prefix=/architecture-journey
"""

import logging
from functools import wraps

from flask import Blueprint, abort, current_app, jsonify, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, select

from app import db
from app.core.api.response import api_error, api_success
from app.models.solution_models import Solution
from app.models.architecture_journey import (
    ARCHITECTURE_LAYERS,
    JOURNEY_INTENTS,
    JOURNEY_STAGES,
    JOURNEY_STATUSES,
    OUTCOME_TYPES,
    ArchitectureJourney,
)
from app.models.solution_archimate_element import SolutionArchiMateElement
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship
from app.modules.codegen.models import CodegenGeneration
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

architecture_journey_bp = Blueprint("architecture_journey", __name__, url_prefix="/architecture-journey")

# Legacy alias — keeps imports in __init__.py working during rename
journey_v2_bp = architecture_journey_bp

from app.models.solution_models import Solution as _GuardSolution  # noqa: E402
from app.utils.route_guards import require_entity_json  # noqa: E402

JOURNEY_INTENT_OPTIONS = (
    ("business_transformation", "Transform how the business works", "Redesign capabilities, value streams, organisation and outcomes."),
    ("operating_model", "Shape an operating model", "Align people, process, information, locations and governance."),
    ("strategy_to_execution", "Turn strategy into execution", "Trace goals to capabilities, investments, work and measures."),
    ("portfolio_change", "Change a portfolio", "Assess application, data or technology change across the estate."),
    ("risk_and_compliance", "Respond to risk or regulation", "Frame obligations, controls, decisions and evidence."),
    ("architecture_assessment", "Assess an architecture", "Understand current state, gaps, dependencies and options."),
    ("solution_design", "Design a solution", "Use the specialised solution path through design and ARB review."),
)

JOURNEY_LAYER_OPTIONS = (
    ("motivation", "Motivation", "Why change"),
    ("strategy", "Strategy", "What direction"),
    ("business", "Business", "How value is created"),
    ("data", "Data", "What information matters"),
    ("application", "Application", "What enables the work"),
    ("technology", "Technology", "What the estate runs on"),
    ("implementation", "Implementation", "How change is delivered"),
    ("governance", "Governance", "How decisions are assured"),
)

JOURNEY_DELIVERABLE_OPTIONS = (
    ("architecture_definition", "Architecture definition document", "file-text"),
    ("capability_map", "Capability map", "map"),
    ("capability_maturity", "Capability maturity assessment", "thermometer"),
    ("value_stream", "Value-stream model", "waypoints"),
    ("product_service_catalogue", "Product and service catalogue", "package"),
    ("operating_model", "Operating-model assessment", "building-2"),
    ("stakeholder_map", "Stakeholder map", "users"),
    ("organisation_ownership", "Organisation and ownership model", "network"),
    ("information_map", "Information and data map", "database"),
    ("outcome_scorecard", "Outcome and KPI scorecard", "gauge"),
    ("strategy_traceability", "Strategy-to-execution traceability", "git-branch"),
    ("initiative_alignment", "Initiative and project alignment", "layout-dashboard"),
    ("options_assessment", "Options and trade-off assessment", "scale"),
    ("roadmap", "Transition roadmap", "milestone"),
    ("decision_record", "Architecture decision record", "gavel"),
    ("governance_pack", "Governance evidence pack", "shield-check"),
    ("business_case", "Business case", "briefcase-business"),
    ("solution_blueprint", "Solution blueprint", "boxes"),
)

DELIVERABLE_TOOL_ENDPOINTS = {
    "capability_map": "capability_map.index",
    "capability_maturity": "maturity_management.maturity_heatmap",
    "value_stream": "value_stream.index",
    "product_service_catalogue": "archimate_layers.business_products",
    "operating_model": "business_model.index",
    "stakeholder_map": "stakeholder_map.stakeholder_map_page",
    "organisation_ownership": "organization.index",
    "information_map": "data_architecture.data_architecture_dashboard",
    "outcome_scorecard": "dashboard.health_scorecard",
    "strategy_traceability": "enterprise.strategic_planning_dashboard",
    "initiative_alignment": "portfolio.index",
    "roadmap": "enterprise.gap_analysis",
    "decision_record": "arch_decisions.list_decisions",
    "governance_pack": "governance.dashboard",
    "business_case": "business_case.index",
}


def _available_deliverable_tools(journey=None):
    """Build only links whose optional blueprint registered successfully.

    E2E-3: "solution_blueprint" was a real, selectable deliverable
    (JOURNEY_DELIVERABLE_OPTIONS) with a real, working page behind it
    (solution_design.view_solution -- "Blueprint page ships ON by
    default") that was simply never added to this map, so every journey
    reported "Tool unavailable" for it unconditionally. It is not a
    plain index page like the others here, though -- it needs a
    solution_id, which an ArchitectureJourney only has once one is
    linked (see E2E-2's ArchitectureJourney/Solution split). Built
    separately from the endpoint-existence comprehension below rather
    than added to DELIVERABLE_TOOL_ENDPOINTS, since url_for() with no
    solution_id would raise BuildError and take out every other tool's
    lookup with it.
    """
    tools = {
        deliverable: url_for(endpoint)
        for deliverable, endpoint in DELIVERABLE_TOOL_ENDPOINTS.items()
        if endpoint in current_app.view_functions
    }
    if (
        journey is not None
        and journey.solution_id
        and "solution_design.view_solution" in current_app.view_functions
    ):
        tools["solution_blueprint"] = url_for(
            "solution_design.view_solution", solution_id=journey.solution_id
        )
    return tools


def _validate_evidence_manifest(value):
    from app.models.ai_chat_document import AIChatDocumentUpload

    if not isinstance(value, list) or len(value) > 50:
        return None, "Evidence must be a list of no more than 50 records"
    cleaned = []
    for item in value:
        if not isinstance(item, dict):
            return None, "Each evidence record must be an object"
        kind = item.get("kind")
        name = item.get("name")
        reference = item.get("reference")
        document_id = item.get("document_id")
        if kind not in {"document_reference", "observation", "model", "decision"}:
            return None, "Evidence kind is invalid"
        if not isinstance(name, str) or not name.strip() or len(name) > 255:
            return None, "Evidence name is required and must be 255 characters or fewer"
        if reference is not None and (not isinstance(reference, str) or len(reference) > 1000):
            return None, "Evidence reference must be 1000 characters or fewer"
        if document_id is not None and not isinstance(document_id, int):
            return None, "Evidence document_id must be an integer"
        if document_id is not None:
            document = AIChatDocumentUpload.query.filter_by(id=document_id).first()
            if document is None:
                return None, "Canonical evidence document was not found in this organization"
            if document.uploaded_by_id != current_user.id and not current_user.is_admin():
                return None, "Canonical evidence document is not available to this user"
        if not reference and document_id is None:
            return None, "Evidence needs a reference or canonical document_id"
        cleaned.append({
            "kind": kind,
            "name": name.strip(),
            "reference": reference.strip() if isinstance(reference, str) else None,
            "document_id": document_id,
        })
    return cleaned, None


def _validate_state(value):
    import json
    if not isinstance(value, dict):
        return "Journey state must be an object"
    if len(json.dumps(value)) > 65536:
        return "Journey state exceeds the 64 KB limit"
    return None


def _require_journey_owner(view):
    """Authorise access to a tenant-scoped journey owned by the current user."""
    @wraps(view)
    def decorated(journey_id, *args, **kwargs):
        journey = ArchitectureJourney.query.filter_by(
            id=journey_id, organization_id=current_user.organization_id
        ).first_or_404()
        if (
            journey.owner_id != current_user.id
            and not current_user.is_admin()
            and not getattr(current_user, "is_platform_admin", False)
        ):
            return api_error("Access denied: you do not own this journey", 403)
        return view(journey_id, *args, **kwargs)
    return decorated


def _require_journey_access(view):
    """Allow the tenant-scoped owner, an admin, or an explicit journey member."""
    @wraps(view)
    def decorated(journey_id, *args, **kwargs):
        from app.models.architecture_journey_link import ArchitectureJourneyMember

        journey = ArchitectureJourney.query.filter_by(
            id=journey_id, organization_id=current_user.organization_id
        ).first_or_404()
        is_admin = current_user.is_admin() or bool(
            getattr(current_user, "is_platform_admin", False)
        )
        if journey.owner_id != current_user.id and not is_admin:
            membership = ArchitectureJourneyMember.query.filter_by(
                journey_id=journey.id,
                user_id=current_user.id,
                organization_id=journey.organization_id,
            ).first()
            if membership is None:
                abort(404)
        return view(journey_id, *args, **kwargs)
    return decorated


_JOURNEY_EDITOR_ROLES = frozenset({
    "chief_architect", "enterprise_architect", "business_architect",
    "solution_architect", "application_architect", "data_architect",
    "technology_architect", "security_architect", "programme_architect",
    "contributor",
})


def _require_journey_editor(view):
    """Allow mutations only to the owner/admin or an explicit editing role."""
    @wraps(view)
    def decorated(journey_id, *args, **kwargs):
        from app.models.architecture_journey_link import ArchitectureJourneyMember

        journey = ArchitectureJourney.query.filter_by(
            id=journey_id, organization_id=current_user.organization_id
        ).first_or_404()
        is_admin = current_user.is_admin() or bool(
            getattr(current_user, "is_platform_admin", False)
        )
        if journey.owner_id != current_user.id and not is_admin:
            membership = ArchitectureJourneyMember.query.filter_by(
                journey_id=journey.id,
                user_id=current_user.id,
                organization_id=journey.organization_id,
            ).first()
            if membership is None:
                abort(404)
            if membership.role not in _JOURNEY_EDITOR_ROLES:
                return api_error("This journey role has read-only access", 403)
        return view(journey_id, *args, **kwargs)
    return decorated


def _require_solution_owner(f):
    """Guard: authenticated user must own the solution or be an admin.

    Wraps route functions with signature f(solution_id, ...).
    Returns HTTP 403 if the current user is not the creator.
    Must be placed AFTER @login_required so current_user is populated.
    """
    @wraps(f)
    def decorated(solution_id, *args, **kwargs):
        solution = Solution.query.get_or_404(solution_id)
        if solution.created_by_id != current_user.id and not current_user.is_admin():
            return api_error("Access denied: you do not own this solution", 403)
        return f(solution_id, *args, **kwargs)
    return decorated


def _require_solution_org_view(f):
    """Guard for READ routes: any authenticated user in the solution's ORG may
    view it — architects govern and review solutions they do not own, so an
    owner-only gate on a GET produced a 403 (and a broken, console-erroring data
    fetch) whenever a non-owner opened a solution page. Solution is TenantMixin,
    so Solution.query.get_or_404 is org-scoped and a foreign-org solution 404s;
    this simply drops the owner restriction for reads while tenant isolation is
    preserved by the mixin. WRITES keep the stricter _require_solution_owner.
    """
    @wraps(f)
    def decorated(solution_id, *args, **kwargs):
        Solution.query.get_or_404(solution_id)  # tenant-scoped: same-org or 404
        return f(solution_id, *args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Journey state machine helper — advances state through intermediate steps
# ---------------------------------------------------------------------------

def _get_problem_id(solution_id):
    """Get the SolutionProblemDefinition ID for a solution, if one exists."""
    try:
        from app.models.solution_architect_models import SolutionAnalysisSession, SolutionProblemDefinition
        session = SolutionAnalysisSession.query.filter_by(solution_id=solution_id).first()
        if session:
            prob = SolutionProblemDefinition.query.filter_by(session_id=session.id).first()
            return prob.id if prob else None
    except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
        pass
    return None


def _advance_journey_state(solution_id, target_state_value):
    """Advance the journey state machine to *target_state_value*.

    Steps through each intermediate state so the transition graph is respected.
    Best-effort: never crashes the calling request if something goes wrong.
    """
    try:
        from app.modules.solutions_strategic.v2.services.journey_reasoning_orchestrator import (
            JourneyReasoningOrchestrator, JourneyState,
        )
        orch = JourneyReasoningOrchestrator(solution_id)
        target = JourneyState(target_state_value)
        current = orch._get_state()

        all_states = list(JourneyState)
        current_idx = all_states.index(current)
        target_idx = all_states.index(target)

        if current_idx >= target_idx:
            return  # already at or past target

        # Step through each intermediate state
        for i in range(current_idx + 1, target_idx + 1):
            orch._transition(all_states[i])

        logger.info(
            "Journey state advanced: %s → %s (solution %d)",
            current.value, target.value, solution_id,
        )
    except Exception as e:
        logger.warning(
            "Failed to advance journey state to %s for solution %d: %s",
            target_state_value, solution_id, e,
        )


@journey_v2_bp.route("/")
@login_required
def index():
    """Purpose-led landing page — start or resume any architecture journey."""
    # The list used to be `.limit(8)` on active journeys with no count, no filter
    # and no pagination. A user with nine journeys saw eight and was told nothing
    # about the ninth: a truncated list that does not admit to being truncated is a
    # lie of omission on a screen whose whole job is "resume your work". And the
    # hardcoded status="active" made a completed journey unreachable by any route.
    #
    # State lives in the URL and the server is the source of truth, following the
    # applications list. An unknown filter value is refused rather than ignored,
    # because ignoring it renders the full list under a filter the user believes is
    # applied.
    filter_intent = request.args.get("intent_filter") or None
    filter_stage = request.args.get("stage") or None
    filter_status = request.args.get("status") or "active"

    if filter_intent is not None and filter_intent not in JOURNEY_INTENTS:
        return api_error("Unknown journey intent filter", 400)
    if filter_stage is not None and filter_stage not in JOURNEY_STAGES:
        return api_error("Unknown journey stage filter", 400)
    if filter_status not in JOURNEY_STATUSES:
        return api_error("Unknown journey status filter", 400)

    try:
        page = max(1, safe_int_arg('page', 1, minimum=1))
    except (TypeError, ValueError):
        page = 1
    page_size = 9

    from app.models.architecture_journey_link import ArchitectureJourneyMember

    member_journeys = select(ArchitectureJourneyMember.journey_id).where(
        ArchitectureJourneyMember.user_id == current_user.id,
        ArchitectureJourneyMember.organization_id == current_user.organization_id,
    )
    journey_query = ArchitectureJourney.query.filter(
        ArchitectureJourney.organization_id == current_user.organization_id,
        ArchitectureJourney.status == filter_status,
        or_(
            ArchitectureJourney.owner_id == current_user.id,
            ArchitectureJourney.id.in_(member_journeys),
        ),
    )
    if filter_intent:
        journey_query = journey_query.filter_by(intent=filter_intent)
    if filter_stage:
        journey_query = journey_query.filter_by(current_stage=filter_stage)

    journey_total = journey_query.count()
    journey_pages = max(1, (journey_total + page_size - 1) // page_size)
    journeys = (
        journey_query.order_by(ArchitectureJourney.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    journey_filters_active = bool(filter_intent or filter_stage or filter_status != "active")
    try:
        in_progress = (
            # The three ~ilike exclusions that used to sit here filtered out
            # solutions named 'J1-AutoTest-%', 'J7-E2E-Test%' and '%-AutoTest-%'.
            # Those are this repository's own test fixture names, and they were
            # being excluded from what a real user sees in production.
            #
            # It is the wrong place to solve that problem twice over: a customer
            # who legitimately names a solution "Migration-AutoTest-Rig" would have
            # it silently vanish from their own hub, and a test suite that leaves
            # rows behind should be fixed in the suite rather than hidden from the
            # product. Test data does not belong in a production predicate.
            Solution.query.filter(
                Solution.created_by_id == current_user.id,
                Solution.governance_status.in_(["draft", "proposed", "in_progress"]),
                # Fixture-name exclusions deliberately absent: they hid this
                # repository's own test rows from a real user's hub, and would have
                # hidden a customer's legitimately-named solution too. Purge test
                # rows; do not filter them out of the product.
            )
            .order_by(Solution.updated_at.desc())
            .limit(5)
            .all()
        )
    except Exception as e:
        logger.warning("Failed to load in-progress solutions: %s", e)
        db.session.rollback()
        in_progress = []
    return render_template(
        "architecture_assistant/architecture_journey_hub.html",
        architecture_journeys=journeys,
        journey_total=journey_total,
        journey_page=page,
        journey_pages=journey_pages,
        journey_filters_active=journey_filters_active,
        filter_intent=filter_intent,
        filter_stage=filter_stage,
        filter_status=filter_status,
        journey_stages=JOURNEY_STAGES,
        journey_statuses=JOURNEY_STATUSES,
        intent_options=JOURNEY_INTENT_OPTIONS,
        layer_options=JOURNEY_LAYER_OPTIONS,
        deliverable_options=JOURNEY_DELIVERABLE_OPTIONS,
        requested_intent=(request.args.get("intent") or "").replace("-", "_"),
        solutions=in_progress,
        solution_id=None,
        has_acm_domains=False,
    )


@journey_v2_bp.route("/start-architecture", methods=["POST"])
@login_required
def start_architecture_journey():
    """Create a durable journey without creating a Solution."""
    data = request.get_json(silent=True) or {}
    title_value = data.get("title")
    intent_value = data.get("intent")
    outcome_value = data.get("outcome_type", "undecided")
    title = title_value.strip() if isinstance(title_value, str) else ""
    intent = intent_value.strip().replace("-", "_") if isinstance(intent_value, str) else ""
    outcome_type = outcome_value.strip() if isinstance(outcome_value, str) else ""
    raw_layers = data.get("selected_layers")
    raw_deliverables = data.get("selected_deliverables", [])
    if (
        not isinstance(raw_layers, list)
        or len(raw_layers) > len(ARCHITECTURE_LAYERS)
        or any(not isinstance(item, str) for item in raw_layers)
    ):
        return api_error("Architecture scope must be a bounded list", 400)
    if (
        not isinstance(raw_deliverables, list)
        or len(raw_deliverables) > len(JOURNEY_DELIVERABLE_OPTIONS)
        or any(not isinstance(item, str) for item in raw_deliverables)
    ):
        return api_error("Deliverables must be a bounded list", 400)
    layers = list(dict.fromkeys(raw_layers))
    deliverables = list(dict.fromkeys(raw_deliverables))

    valid_deliverables = {item[0] for item in JOURNEY_DELIVERABLE_OPTIONS}
    if not title or len(title) > 255:
        return api_error("Give this journey a clear purpose", 400)
    if intent not in JOURNEY_INTENTS:
        return api_error("Choose a valid architecture purpose", 400)
    if not layers or any(layer not in ARCHITECTURE_LAYERS for layer in layers):
        return api_error("Choose at least one valid architecture scope", 400)
    if any(item not in valid_deliverables for item in deliverables):
        return api_error("Choose valid journey deliverables", 400)
    if outcome_type not in OUTCOME_TYPES:
        return api_error("Choose a valid outcome", 400)

    journey = ArchitectureJourney(
        owner_id=current_user.id,
        organization_id=current_user.organization_id,
        title=title,
        intent=intent,
        selected_layers=layers,
        selected_deliverables=deliverables,
        outcome_type=outcome_type,
        evidence_manifest=[],
        journey_state={"framing": {"purpose": title}},
    )
    db.session.add(journey)
    db.session.commit()
    return api_success(data={"journey_id": journey.id, "redirect": journey.resume_path}, status_code=201)


@journey_v2_bp.route("/work/<int:journey_id>")
@login_required
@_require_journey_access
def architecture_journey_workspace(journey_id):
    journey = ArchitectureJourney.query.filter_by(id=journey_id).first_or_404()

    from app.modules.solutions_strategic.v2.services.journey_home import (
        journey_home_view,
    )

    home = journey_home_view(journey_id=journey.id, actor_user=current_user)
    if home is None:
        # The guard above already resolved this journey for this tenant, so the
        # read model refusing it means the two disagree -- which is a fault, not a
        # permission answer. Do not fall through to a page rendered from partial
        # context: that is how a screen ends up quietly showing someone a journey
        # with none of its records and no indication anything is missing.
        abort(404)

    from app.models.architecture_journey_link import (
        JOURNEY_LINK_ENTITY_TYPES,
        JOURNEY_LINK_RELATIONS,
    )

    return render_template(
        "architecture_assistant/architecture_journey_workspace.html",
        journey=journey,
        home=home,
        link_entity_types=JOURNEY_LINK_ENTITY_TYPES,
        link_relations=JOURNEY_LINK_RELATIONS,
        intent_options=JOURNEY_INTENT_OPTIONS,
        layer_options=JOURNEY_LAYER_OPTIONS,
        deliverable_options=JOURNEY_DELIVERABLE_OPTIONS,
        deliverable_tool_urls=_available_deliverable_tools(journey),
    )


# ── journey edges: linking records, and adding people ────────────────────────
#
# The journey home counts participants, decisions, risks and governance. Without
# these write paths those counts are permanently zero and the screen is decorative
# -- a worse failure than showing nothing, because a reader sees "0 risks" and
# concludes the journey is clean when nothing could ever have been recorded.
#
# Bodies are strictly allow-listed. organization_id and the author come from the
# session and are never accepted from the request: a caller who can name the tenant
# can write into someone else's.


def _link_payload_error(data):
    """Validate a link body. Returns an error message, or None when acceptable."""
    from app.models.architecture_journey_link import (
        JOURNEY_LINK_ENTITY_TYPES,
        JOURNEY_LINK_RELATIONS,
    )

    allowed = {"entity_type", "entity_id", "relation", "note"}
    unknown = set(data) - allowed
    if unknown:
        # Named explicitly rather than ignored: silently dropping a field the
        # caller believed was saved is how a client ends up trusting a value the
        # server never stored.
        return f"Unsupported link fields: {', '.join(sorted(unknown))}"

    entity_type = data.get("entity_type")
    if entity_type not in JOURNEY_LINK_ENTITY_TYPES:
        return "Unknown record type for a journey link"

    entity_id = data.get("entity_id")
    if isinstance(entity_id, bool) or not isinstance(entity_id, int) or entity_id <= 0:
        # A 0 or a negative id points at no row, and would render as a broken
        # reference that looks like a real one.
        return "A journey link needs the id of an existing record"

    relation = data.get("relation", "references")
    if relation not in JOURNEY_LINK_RELATIONS:
        return "Unknown relation for a journey link"

    note = data.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > 2000):
        return "A link note must be text of at most 2000 characters"

    return None


@journey_v2_bp.route("/work/<int:journey_id>/links", methods=["POST"])
@login_required
@_require_journey_editor
def create_journey_link(journey_id):
    """Link this journey to a record that already exists elsewhere."""
    from app.models.architecture_journey_link import ArchitectureJourneyLink

    journey = ArchitectureJourney.query.filter_by(id=journey_id).first_or_404()
    data = request.get_json(silent=True) or {}

    error = _link_payload_error(data)
    if error:
        return api_error(error, 400)

    from app.modules.solutions_strategic.v2.services.journey_home import (
        resolve_journey_link_target,
    )
    target = resolve_journey_link_target(
        data["entity_type"], data["entity_id"], journey.organization_id
    )
    if target is None:
        # Missing and foreign records are deliberately indistinguishable.
        return api_error("No such record in this organisation", 404)

    relation = data.get("relation", "references")
    existing = ArchitectureJourneyLink.query.filter_by(
        journey_id=journey.id,
        entity_type=data["entity_type"],
        entity_id=data["entity_id"],
        relation=relation,
    ).first()
    if existing is not None:
        # The same record linked twice with the same relation is a duplicate, not
        # a second fact.
        return api_error("That record is already linked to this journey", 409)

    link = ArchitectureJourneyLink(
        journey_id=journey.id,
        organization_id=journey.organization_id,
        entity_type=data["entity_type"],
        entity_id=data["entity_id"],
        relation=relation,
        note=(data.get("note") or None),
        created_by_id=current_user.id,
    )
    db.session.add(link)
    db.session.commit()
    return api_success(
        data={
            "id": link.id,
            "entity_type": link.entity_type,
            "entity_id": link.entity_id,
            "relation": link.relation,
            "label": target["label"],
            "status": target["status"],
        },
        status_code=201,
    )


@journey_v2_bp.route("/work/<int:journey_id>/links/<int:link_id>", methods=["DELETE"])
@login_required
@_require_journey_owner
def delete_journey_link(journey_id, link_id):
    """Unlink a record. The record itself is untouched -- this owns the edge only."""
    from app.models.architecture_journey_link import ArchitectureJourneyLink

    journey = ArchitectureJourney.query.filter_by(id=journey_id).first_or_404()
    link = ArchitectureJourneyLink.query.filter_by(
        id=link_id, journey_id=journey.id
    ).first()
    if link is None:
        return api_error("No such link on this journey", 404)

    db.session.delete(link)
    db.session.commit()
    return api_success(data={"id": link_id})


@journey_v2_bp.route("/work/<int:journey_id>/members", methods=["POST"])
@login_required
@_require_journey_owner
def add_journey_member(journey_id):
    """Put a person on this journey, by user id."""
    from app.models.architecture_journey_link import (
        JOURNEY_MEMBER_ROLES,
        ArchitectureJourneyMember,
    )
    from app.models.user import User

    journey = ArchitectureJourney.query.filter_by(id=journey_id).first_or_404()
    data = request.get_json(silent=True) or {}

    unknown = set(data) - {"user_id", "role"}
    if unknown:
        return api_error(f"Unsupported member fields: {', '.join(sorted(unknown))}", 400)

    user_id = data.get("user_id")
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        return api_error("A journey member needs a user id", 400)

    role = data.get("role", "contributor")
    if role not in JOURNEY_MEMBER_ROLES:
        return api_error("Unknown role for a journey member", 400)

    # Resolved with an explicit organisation predicate. Adding a user from another
    # tenant would put a name on a journey that person cannot open, and would leak
    # the existence of that user to this one.
    member_user = User.query.filter_by(
        id=user_id, organization_id=journey.organization_id
    ).first()
    if member_user is None:
        return api_error("No such user in this organisation", 400)

    existing = ArchitectureJourneyMember.query.filter_by(
        journey_id=journey.id, user_id=member_user.id
    ).first()
    if existing is not None:
        return api_error("That person is already on this journey", 409)

    member = ArchitectureJourneyMember(
        journey_id=journey.id,
        organization_id=journey.organization_id,
        user_id=member_user.id,
        role=role,
        added_by_id=current_user.id,
    )
    db.session.add(member)
    db.session.commit()
    return api_success(
        data={"id": member.id, "user_id": member.user_id, "role": member.role},
        status_code=201,
    )


@journey_v2_bp.route("/work/<int:journey_id>/state", methods=["PATCH"])
@login_required
@_require_journey_editor
def update_architecture_journey(journey_id):
    """Persist framing, evidence, deliverables and resume position."""
    journey = ArchitectureJourney.query.filter_by(id=journey_id).first_or_404()
    data = request.get_json(silent=True) or {}
    allowed = {
        "title", "selected_layers", "selected_deliverables", "outcome_type",
        "evidence_manifest", "journey_state", "current_stage", "status",
    }
    unknown = set(data) - allowed
    if unknown:
        return api_error(f"Unsupported journey fields: {', '.join(sorted(unknown))}", 400)
    if "title" in data and (
        not isinstance(data["title"], str)
        or not data["title"].strip()
        or len(data["title"].strip()) > 255
    ):
        return api_error("Journey title cannot be empty", 400)
    if "selected_layers" in data:
        if (
            not isinstance(data["selected_layers"], list)
            or len(data["selected_layers"]) > len(ARCHITECTURE_LAYERS)
            or any(not isinstance(item, str) for item in data["selected_layers"])
        ):
            return api_error("Architecture scope must be a bounded list", 400)
        layers = list(dict.fromkeys(data["selected_layers"] or []))
        if not layers or any(layer not in ARCHITECTURE_LAYERS for layer in layers):
            return api_error("Choose at least one valid architecture scope", 400)
        journey.selected_layers = layers
    if "selected_deliverables" in data:
        if (
            not isinstance(data["selected_deliverables"], list)
            or len(data["selected_deliverables"]) > len(JOURNEY_DELIVERABLE_OPTIONS)
            or any(not isinstance(item, str) for item in data["selected_deliverables"])
        ):
            return api_error("Deliverables must be a bounded list", 400)
        deliverables = list(dict.fromkeys(data["selected_deliverables"] or []))
        valid_deliverables = {item[0] for item in JOURNEY_DELIVERABLE_OPTIONS}
        if any(item not in valid_deliverables for item in deliverables):
            return api_error("Choose valid journey deliverables", 400)
        journey.selected_deliverables = deliverables
    if "outcome_type" in data and data["outcome_type"] not in OUTCOME_TYPES:
        return api_error("Choose a valid outcome", 400)
    if "current_stage" in data and data["current_stage"] not in JOURNEY_STAGES:
        return api_error("Choose a valid journey stage", 400)
    if "status" in data:
        # E2E-2: the "deliver" stage's "Journey ready to close" button fired
        # a 200 with no terminal state -- there was no route accepting a
        # status transition at all, so a journey could never actually be
        # marked done. Restricted to the one real transition this closes:
        # only "completed", and only once the journey has genuinely reached
        # its final stage -- a journey that skipped Decide/Deliver has
        # nothing to close.
        target_status = data["status"]
        if target_status != "completed":
            return api_error("A journey can only be closed as completed here", 400)
        if journey.current_stage != "deliver":
            return api_error(
                "Complete the Deliver stage before closing this journey", 400,
            )
        journey.status = target_status
    if "journey_state" in data:
        state_error = _validate_state(data["journey_state"])
        if state_error:
            return api_error(state_error, 400)
    if "evidence_manifest" in data:
        evidence, evidence_error = _validate_evidence_manifest(data["evidence_manifest"])
        if evidence_error:
            return api_error(evidence_error, 400)
        journey.evidence_manifest = evidence
    for field in allowed - {"selected_layers", "selected_deliverables", "evidence_manifest", "status"}:
        if field in data:
            setattr(journey, field, str(data[field]).strip() if field == "title" else data[field])
    db.session.commit()
    return api_success(data={"journey_id": journey.id, "resume_path": journey.resume_path})


@journey_v2_bp.route("/start", methods=["POST"])
@login_required
def start_journey():
    """Create a new Solution and redirect to the journey page."""
    from datetime import datetime

    from app import db

    # Distinguishable default name — a generic "New Solution (Draft)" made
    # every unnamed draft collapse into identical rows on the solutions list.
    # Journey step 1 (Clarify Problem) renames this from the problem statement.
    default_name = f"Untitled Solution · {datetime.utcnow():%b %d, %H:%M}"
    solution = Solution(
        name=default_name,
        description="",
        created_by_id=current_user.id,
        governance_status="draft",
        status="draft",
        has_acm_domains=True,
    )
    db.session.add(solution)
    db.session.commit()
    logger.info("Created solution %d for journey v2", solution.id)
    return api_success(data={"solution_id": solution.id, "redirect": f"/architecture-journey/{solution.id}"})


def _build_template_seed(template_name: str, domain: str, entities: dict) -> str:
    """Construct a Step 1 problem statement seed from a template's static entities.

    The seed is written into solution.description so journey_v2.js reads it into
    problemStatement at Step 1 init — giving the LLM rich context to generate
    meaningful clarifying questions and ArchiMate elements instead of starting blank.
    """
    parts = [f"{template_name} architecture."]
    if domain:
        parts.append(f"Domain: {domain}.")

    def names(items):
        return ", ".join(i["name"] for i in items if i.get("name"))

    if entities.get("drivers"):
        parts.append(f"Drivers: {names(entities['drivers'])}.")
    if entities.get("goals"):
        parts.append(f"Goals: {names(entities['goals'])}.")
    if entities.get("constraints"):
        parts.append(f"Constraints: {names(entities['constraints'])}.")
    if entities.get("requirements"):
        parts.append(f"Key requirements: {names(entities['requirements'])}.")
    if entities.get("principles"):
        parts.append(f"Design principles: {names(entities['principles'])}.")

    return " ".join(parts)


@journey_v2_bp.route("/start-from-template", methods=["POST"])
@login_required
def start_from_template():
    """Create a solution seeded from a template and launch the Architecture Journey.

    Unlike /solutions/create-from-template (which creates a static solution and stops),
    this route seeds the solution with the template's domain knowledge and redirects to
    the Architecture Journey wizard — so LLM inference, ArchiMate element creation, and
    blueprint scoring all run normally from Step 1 onward.

    The template's drivers/goals/constraints/requirements are written to the DB as a
    head-start, and its domain knowledge is written to solution.description so Step 1
    arrives pre-filled with a meaningful problem statement context.
    """
    import json as _json
    from app.models.solution_governance import SolutionTemplate
    from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import SolutionAIOrchestrator

    data = request.get_json() or {}
    template_id = data.get("template_id")
    if not template_id:
        return api_error("template_id is required", 400)

    template = SolutionTemplate.query.get(template_id)
    if not template:
        return api_error("Template not found", 404)

    try:
        entities = _json.loads(template.template_json) if isinstance(template.template_json, str) else (template.template_json or {})
    except Exception as e:
        logger.warning("Invalid template_json for template %s: %s", template_id, e)
        return api_error("Invalid template data", 400)

    seed = _build_template_seed(template.name or "Solution", template.domain or "", entities)

    solution = Solution(
        name=f"{template.name} (Draft)" if template.name else "New Solution (Draft)",
        description=seed,
        business_domain=template.domain,
        created_by_id=current_user.id,
        governance_status="draft",
        status="draft",
        has_acm_domains=True,
    )
    db.session.add(solution)
    db.session.flush()

    try:
        orchestrator = SolutionAIOrchestrator()
        orchestrator._create_entities_from_draft(solution, entities, current_user.id)
    except Exception as e:
        logger.warning("Template entity pre-population failed for solution %d: %s", solution.id, e)
        # Non-fatal — the journey can still run without pre-populated entities

    template.usage_count = (template.usage_count or 0) + 1
    db.session.commit()

    logger.info(
        "Created solution %d from template %d (%s) — launching Architecture Journey",
        solution.id, template_id, template.name,
    )
    from flask import jsonify as _jsonify
    return _jsonify({"success": True, "solution_id": solution.id, "redirect": f"/architecture-journey/{solution.id}"})


@journey_v2_bp.route("/<int:solution_id>")
@login_required
@_require_solution_owner
def journey_page(solution_id):
    """Main journey page for a specific solution."""
    solution = Solution.query.get_or_404(solution_id)
    import json as _json
    try:
        journey_state_json = _json.dumps(solution.journey_state) if solution.journey_state else "null"
    except (TypeError, ValueError) as e:
        logger.warning("Failed to serialize journey_state for solution %d: %s", solution_id, e)
        journey_state_json = "null"
    return render_template(
        "architecture_assistant/journey_v3.html",
        solution=solution,
        solution_id=solution_id,
        journey_state_json=journey_state_json,
        has_acm_domains=getattr(solution, 'has_acm_domains', False) or False,
    )


# ── Session Persistence ──────────────────────────────────────────

@journey_v2_bp.route("/<int:solution_id>/save-state", methods=["POST"])
@login_required
@_require_solution_owner
def save_state(solution_id):
    """Save wizard navigation state."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        data = request.get_json() or {}
        orch = JourneyOrchestrator(solution_id)
        result = orch.save_state(data)
        return api_success(data=result)
    except Exception as e:
        logger.error("Save state failed: %s", e, exc_info=True)
        return api_error("Failed to save state", 500)


@journey_v2_bp.route("/<int:solution_id>/update-name", methods=["PATCH"])
@login_required
@_require_solution_owner
def update_solution_name(solution_id):
    """Update the solution name from the wizard Step 1 name field."""
    try:
        from app.models.solution_models import Solution
        from app import db
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        if not name:
            return api_error("Name cannot be empty", 400)
        solution = Solution.query.get_or_404(solution_id)
        solution.name = name
        db.session.commit()
        return api_success(data={"name": solution.name})
    except Exception as e:
        logger.error("Update solution name failed: %s", e, exc_info=True)
        return api_error("Failed to update name", 500)


@journey_v2_bp.route("/<int:solution_id>/journey-state", methods=["PATCH"])
@login_required
@_require_solution_owner
def patch_journey_state(solution_id):
    """Merge fields into the solution's journey_state JSON.

    Used by the wizard to persist capabilities, preferences, and other
    state that doesn't have its own dedicated endpoint.
    """
    try:
        from app.models.solution_models import Solution
        from app import db

        solution = Solution.query.get_or_404(solution_id)
        data = request.get_json(silent=True) or {}
        if not data:
            return api_error("No data provided", 400)

        state = solution.journey_state or {}
        if not isinstance(state, dict):
            # `_json` is imported as `import json as _json` inside a DIFFERENT
            # function in this module, so it was never bound here. The except below
            # catches Exception and would have swallowed the resulting NameError,
            # silently discarding a valid string state as {}.
            import json as _json

            try:
                state = _json.loads(state) if isinstance(state, str) else {}
            except Exception:
                state = {}

        # Merge top-level keys from request into journey_state
        for key, value in data.items():
            state[key] = value

        solution.journey_state = state
        db.session.commit()
        return api_success(data={"updated_keys": list(data.keys())})
    except Exception as e:
        logger.error("Patch journey state failed: %s", e, exc_info=True)
        return api_error("Failed to update journey state", 500)


@journey_v2_bp.route("/<int:solution_id>/load-state", methods=["GET"])
@login_required
@_require_solution_owner
def load_state(solution_id):
    """Load wizard state + architecture from DB."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        result = orch.load_state()
        return api_success(data=result)
    except Exception as e:
        logger.error("Load state failed: %s", e, exc_info=True)
        return api_error("Failed to load state", 500)


# ── Document Ingestion ────────────────────────────────────────────

def _extract_text_from_bytes(raw_bytes, ext):
    """Extract plain text from raw file bytes. Returns (text, error_msg)."""
    import io
    try:
        if ext in ("txt", "md"):
            return raw_bytes.decode("utf-8", errors="replace"), None
        elif ext == "pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(raw_bytes))
                return "\n".join(page.extract_text() or "" for page in reader.pages[:200]), None
            except ImportError:
                return None, "pypdf not installed — upload a .txt or .docx file instead"
        elif ext == "docx":
            try:
                import docx as _docx
                doc = _docx.Document(io.BytesIO(raw_bytes))
                return "\n".join(p.text for p in doc.paragraphs if p.text.strip()), None
            except ImportError:
                return None, "python-docx not installed — upload a .txt or .pdf file instead"
        else:
            return raw_bytes.decode("utf-8", errors="replace"), None
    except Exception as e:
        return None, str(e)


def _save_solution_document(app, solution_id, filename, raw_bytes):
    """Persist the source document file to uploads/solution_documents/<solution_id>/.

    Returns the stored file path relative to app root, or None on failure.
    The filename is sanitised but kept human-readable (no UUID) because
    SolutionBlueprintProposal.source_doc_name must match it for provenance.
    """
    import re
    import os
    safe_name = re.sub(r"[^\w.\-]", "_", filename)[:200]
    upload_dir = os.path.join(app.root_path, "uploads", "solution_documents", str(solution_id))
    os.makedirs(upload_dir, exist_ok=True)
    dest = os.path.join(upload_dir, safe_name)
    try:
        with open(dest, "wb") as fh:
            fh.write(raw_bytes)
        return os.path.join("uploads", "solution_documents", str(solution_id), safe_name)
    except Exception as e:
        logger.warning("Could not save solution document %s: %s", dest, e)
        return None


def _ingest_text_background(app_ctx, solution_id, text, filename):
    """Run DocumentIngestionService.extract_from_text in a background thread.

    Needs the Flask app context because it makes DB writes.
    """
    import threading

    def _run():
        with app_ctx:
            try:
                from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
                orch = JourneyOrchestrator(solution_id)
                orch.ingest_text(text, source_name=filename)
            except Exception as e:
                logger.error("Background ingestion failed for solution %s / %s: %s", solution_id, filename, e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()


_BRIEF_PROMPT = """You are a solution architect reading a document to understand the core problem being solved.

Read the document below and write a concise problem statement (3-5 sentences) that captures:
- The business problem or opportunity being addressed
- Who is affected (personas, teams, customers)
- What the desired outcome is
- Any key constraints or context that shapes the solution

Write in plain English, first person plural ("We need to...") or third person. No bullet points. No headings. Just a clear, dense paragraph that a solution architect would use as the starting point for an architecture blueprint.

DOCUMENT:
{text}

PROBLEM STATEMENT:"""


def _summarise_document_brief(full_text):
    """Call LLM to produce a concise problem statement from document text.

    Falls back to raw first-2000-chars if LLM is unavailable or times out,
    so the endpoint remains useful even without LLM credentials.
    """
    fallback = full_text[:10000]
    try:
        from app.modules.ai_chat.services.llm_service import LLMService
        provider, model = LLMService._get_configured_provider()
        # Truncate input — 50 000 chars ≈ 37 500 tokens, within modern model context windows
        excerpt = full_text[:50000]
        prompt = _BRIEF_PROMPT.format(text=excerpt)
        raw, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)
        if raw and len(raw.strip()) > 50:
            return raw.strip()
    except Exception as e:
        logger.warning("LLM brief summarisation failed (%s) — falling back to raw text", e)
    return fallback


@journey_v2_bp.route("/<int:solution_id>/extract-brief", methods=["POST"])
@login_required
@_require_solution_owner
def extract_brief(solution_id):
    """Dual-pipeline document intake for Wizard Step 1.

    Accepts one or more files (field name "file", repeated for each).
    For multiple files, texts are concatenated before LLM summarisation so the
    problem statement reflects the full document set.

    Per file:
      1. Extracts plain text → combined across all files
      2. LLM summarises combined text → returned as problemStatement seed
      3. Persists brief to solution.description so it survives page refresh
      4. Stores each source file in uploads/solution_documents/<id>/
      5. Fires ingest_text() in a background thread per file → SolutionBlueprintProposal records

    The browser gets the brief text in < 1 s; proposals appear on the Blueprint
    page by the time the user reaches Step 4.
    """
    from flask import current_app

    files = request.files.getlist("file")
    if not files or not any(f.filename for f in files):
        return api_error("No file provided", 400)

    app_obj = current_app._get_current_object()
    combined_parts = []
    filenames = []
    ingestion_started = False

    for f in files:
        filename = f.filename or "document"
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        try:
            raw_bytes = f.read()
        except Exception:
            logger.warning("Failed to read uploaded file %s — skipping", filename)
            continue

        text, err = _extract_text_from_bytes(raw_bytes, ext)
        if err:
            logger.warning("Text extraction failed for %s: %s — skipping", filename, err)
            continue

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        normalised = " ".join(lines)
        if not normalised.strip():
            logger.warning("No readable text in %s — skipping", filename)
            continue

        combined_parts.append(f"[Document: {filename}]\n{normalised}")
        filenames.append(filename)

        # Store each source file for provenance
        _save_solution_document(app_obj, solution_id, filename, raw_bytes)

        # Fire background ingestion per file
        if len(normalised) >= 50:
            _ingest_text_background(app_obj.app_context(), solution_id, normalised, filename)
            ingestion_started = True

    if not combined_parts:
        return api_error("Could not extract readable text from the uploaded file(s)", 422)

    combined_text = "\n\n".join(combined_parts)

    # ── Summarise combined text via LLM ───────────────────────────────────
    brief = _summarise_document_brief(combined_text)

    # ── Persist brief to solution so it survives page refresh ─────────────
    try:
        sol = Solution.query.get(solution_id)
        if sol and not sol.description:
            sol.description = brief
            db.session.commit()
    except Exception as e:
        logger.warning("Could not persist brief to solution %s: %s", solution_id, e)
        db.session.rollback()

    primary_filename = filenames[0] if len(filenames) == 1 else f"{len(filenames)} documents"
    return api_success(data={"brief": brief, "filename": primary_filename, "ingestion_started": ingestion_started})


@journey_v2_bp.route("/<int:solution_id>/source-documents", methods=["GET"])
@login_required
@_require_solution_owner
def list_source_documents(solution_id):
    """List source documents stored for a solution (uploaded via extract-brief)."""
    import os
    from flask import current_app
    upload_dir = os.path.join(current_app.root_path, "uploads", "solution_documents", str(solution_id))
    if not os.path.isdir(upload_dir):
        return api_success(data={"documents": []})
    files = [
        {"filename": fn, "url": f"/architecture-journey/{solution_id}/source-documents/{fn}"}
        for fn in sorted(os.listdir(upload_dir))
        if os.path.isfile(os.path.join(upload_dir, fn))
    ]
    return api_success(data={"documents": files})


@journey_v2_bp.route("/<int:solution_id>/source-documents/<path:filename>", methods=["GET"])
@login_required
@_require_solution_owner
def download_source_document(solution_id, filename):
    """Download a stored source document by filename."""
    import os
    from flask import current_app, send_from_directory
    upload_dir = os.path.join(current_app.root_path, "uploads", "solution_documents", str(solution_id))
    return send_from_directory(upload_dir, filename, as_attachment=True)


@journey_v2_bp.route("/<int:solution_id>/upload-documents", methods=["POST"])
@login_required
@_require_solution_owner
def upload_documents(solution_id):
    """Upload documents for architecture element extraction."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)

        files = request.files.getlist("files")
        if not files:
            # Try text body fallback
            data = request.get_json() or {}
            text = data.get("text", "")
            if text:
                result = orch.ingest_text(text, source_name=data.get("source_name", "pasted text"))
                return api_success(data=result)
            return api_error("No files or text provided", 400)

        all_proposals = []
        total_created = 0
        for f in files:
            result = orch.ingest_document(f)
            total_created += result.get("proposals_created", 0)
            all_proposals.extend(result.get("proposals", []))

        return api_success(data={
            "proposals_created": total_created,
            "proposals": all_proposals,
        })
    except Exception as e:
        logger.error("Document upload failed: %s", e, exc_info=True)
        return api_error("Failed to process documents", 500)


@journey_v2_bp.route("/<int:solution_id>/proposals", methods=["GET"])
@login_required
@_require_solution_org_view
def list_proposals(solution_id):
    """List blueprint proposals (readable by any member of the solution's org)."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        status = request.args.get("status")
        orch = JourneyOrchestrator(solution_id)
        proposals = orch.list_proposals(status)
        return api_success(data={"proposals": proposals})
    except Exception as e:
        logger.error("List proposals failed: %s", e, exc_info=True)
        return api_error("Failed to list proposals", 500)


@journey_v2_bp.route("/<int:solution_id>/proposals/<int:proposal_id>/accept", methods=["POST"])
@login_required
@_require_solution_owner
def accept_proposal(solution_id, proposal_id):
    """Accept a single proposal — create element + inference chain."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        result = orch.accept_proposal(proposal_id)
        return api_success(data=result)
    except Exception as e:
        logger.error("Accept proposal failed: %s", e, exc_info=True)
        return api_error("Failed to accept proposal", 500)


@journey_v2_bp.route("/<int:solution_id>/proposals/<int:proposal_id>/reject", methods=["POST"])
@login_required
@_require_solution_owner
def reject_proposal(solution_id, proposal_id):
    """Reject a proposal."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        result = orch.reject_proposal(proposal_id)
        return api_success(data=result)
    except Exception as e:
        logger.error("Reject proposal failed: %s", e, exc_info=True)
        return api_error("Failed to reject proposal", 500)


# Wizard ACM domain code → TechnicalCapability.acm_domain values
_WIZARD_DOMAIN_TO_ACM = {
    "AI":   "AI-ANALYTICS",
    "APP":  "APPLICATION-SERVICES",
    "COM":  "COMMUNICATION",
    "DATA": "DATA-STORAGE",
    "DEV":  "DEVOPS-PLATFORM",
    "SEC":  "SECURITY-IDENTITY",
    "UX":   "USER-EXPERIENCE",
}


def _get_domain_capability_context(domain_code: str) -> dict:
    """Return technical capability summary for a wizard ACM domain code.

    Used to enrich the confirm_domain() response so the UI can surface
    'what technical capabilities does this domain need to address?'
    Returns empty dict on any failure — always non-blocking.
    """
    try:
        from app.models.technical_capability import TechnicalCapability
        import json as _json

        acm_domain = _WIZARD_DOMAIN_TO_ACM.get((domain_code or "").upper())
        if not acm_domain:
            return {}

        caps = TechnicalCapability.by_domain(acm_domain)
        if not caps:
            return {}

        foundational = [c for c in caps if c.is_foundational]
        differentiating = [c for c in caps if c.is_differentiating]

        # Collect sample technologies from foundational capabilities
        technologies = []
        seen: set = set()
        for c in foundational[:5]:
            try:
                techs = _json.loads(c.common_technologies) if c.common_technologies else []
                for t in (techs if isinstance(techs, list) else [])[:3]:
                    if t and t not in seen:
                        technologies.append(t)
                        seen.add(t)
            except Exception as exc:
                logger.debug("suppressed error in _get_domain_capability_context (app/modules/solutions_strategic/v2/routes/journey_v2_routes.py): %s", exc)

        return {
            "acm_domain": acm_domain,
            "total": len(caps),
            "foundational_count": len(foundational),
            "differentiating_count": len(differentiating),
            "foundational": [
                {"name": c.name, "complexity": c.complexity or ""}
                for c in foundational[:5]
            ],
            "differentiating": [{"name": c.name} for c in differentiating[:5]],
            "sample_technologies": technologies[:8],
        }
    except Exception as e:
        logger.debug("Capability context lookup failed for domain %s: %s", domain_code, e)
        return {}


@journey_v2_bp.route("/<int:solution_id>/proposals/batch-accept", methods=["POST"])
@login_required
@_require_solution_owner
def batch_accept_proposals(solution_id):
    """Batch accept multiple proposals — marks status as 'accepted'.

    Element creation and inference chains are handled downstream by
    confirm_domain() → DomainPromotionService.  This endpoint only sets
    the proposal status so that the promotion step has accepted proposals
    to work with.  Avoids the JourneyGraph code path that was crashing (JPL-001).
    """
    try:
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

        data = request.get_json() or {}
        proposal_ids = data.get("proposal_ids", [])

        # Validate input — 400 for bad data, not 500
        if not isinstance(proposal_ids, list):
            return api_error("proposal_ids must be a list", 400)
        if len(proposal_ids) == 0:
            return api_error("proposal_ids cannot be empty", 400)
        if not all(isinstance(pid, int) for pid in proposal_ids):
            return api_error("All proposal_ids must be integers", 400)

        accepted = 0
        errors = []
        domain_codes_to_confirm = set()

        # Single batch query replaces N individual queries (Fix 4 / D11)
        proposals_in = SolutionBlueprintProposal.query.filter(
            SolutionBlueprintProposal.id.in_(proposal_ids),
            SolutionBlueprintProposal.solution_id == solution_id,
        ).all()

        found_ids = {p.id for p in proposals_in}
        for pid in proposal_ids:
            if pid not in found_ids:
                errors.append({"proposal_id": pid, "error": "Not found"})

        for proposal in proposals_in:
            if proposal.status not in ("accepted", "promoted"):
                proposal.status = "accepted"
                accepted += 1
            if proposal.acm_domain:
                domain_codes_to_confirm.add(proposal.acm_domain)

        db.session.commit()

        # Spawn background domain promotion — non-blocking, own subprocess + session.
        # Avoids synchronous LLM calls in the HTTP request (D12).
        # Worker is module-level in app/tasks/rebuild_tasks.py (PicklingError fix).
        _promotion_scheduled = False
        if domain_codes_to_confirm:
            try:
                import multiprocessing as _mp
                import os as _os
                from app.tasks.rebuild_tasks import promote_domains_worker
                _app_root = _os.path.dirname(
                    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
                )
                _ctx = _mp.get_context("spawn")
                _proc = _ctx.Process(
                    target=promote_domains_worker,
                    args=(_app_root, solution_id, list(domain_codes_to_confirm)),
                    daemon=False,
                )
                _proc.start()
                _proc.join(timeout=0)  # non-blocking reap
                _promotion_scheduled = True
                logger.info(
                    "Spawned domain promotion PID=%d for solution %d domains=%s",
                    _proc.pid, solution_id, list(domain_codes_to_confirm),
                )
            except Exception as _spawn_err:
                logger.warning(
                    "Could not spawn domain promotion for solution %d: %s",
                    solution_id, _spawn_err,
                )

        return api_success(data={
            "accepted": accepted,
            "errors": errors,
            "elements_created": 0,  # async — not available synchronously
            "relationships_created": 0,
            "promotion_scheduled": _promotion_scheduled,
            "domains_queued": list(domain_codes_to_confirm),
        })
    except Exception as e:
        logger.error("Batch accept failed for solution %d: %s", solution_id, e, exc_info=True)
        return api_error("Failed to batch accept", 500)


@journey_v2_bp.route("/<int:solution_id>/proposals/batch-reject", methods=["POST"])
@login_required
@_require_solution_owner
def batch_reject_proposals(solution_id):
    """Batch reject multiple proposals — marks status as 'rejected'."""
    try:
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

        data = request.get_json() or {}
        proposal_ids = data.get("proposal_ids", [])

        if not isinstance(proposal_ids, list):
            return api_error("proposal_ids must be a list", 400)
        if len(proposal_ids) == 0:
            return api_error("proposal_ids cannot be empty", 400)
        if not all(isinstance(pid, int) for pid in proposal_ids):
            return api_error("All proposal_ids must be integers", 400)

        rejected = 0
        errors = []
        for pid in proposal_ids:
            try:
                proposal = SolutionBlueprintProposal.query.filter_by(
                    id=pid, solution_id=solution_id,
                ).first()
                if not proposal:
                    errors.append({"proposal_id": pid, "error": "Not found"})
                    continue
                if proposal.status not in ("rejected",):
                    proposal.status = "rejected"
                    rejected += 1
            except Exception as e:
                errors.append({"proposal_id": pid, "error": str(e)})

        db.session.commit()
        return api_success(data={"rejected": rejected, "errors": errors})
    except Exception as e:
        logger.error("Batch reject failed for solution %d: %s", solution_id, e, exc_info=True)
        return api_error("Failed to batch reject", 500)


# ── Phase-Level Generation ────────────────────────────────────────

@journey_v2_bp.route("/<int:solution_id>/generate-phase", methods=["POST"])
@login_required
@_require_solution_owner
def generate_phase(solution_id):
    """Generate missing elements for a TOGAF phase (dry-run or execute)."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        data = request.get_json() or {}
        orch = JourneyOrchestrator(solution_id)
        result = orch.generate_phase(
            phase=data.get("phase", "B"),
            dry_run=data.get("dry_run", True),
        )
        return api_success(data=result)
    except Exception as e:
        logger.error("Phase generation failed: %s", e, exc_info=True)
        return api_error("Failed to generate phase", 500)


@journey_v2_bp.route("/<int:solution_id>/phase-status", methods=["GET"])
@login_required
@_require_solution_owner
def phase_status(solution_id):
    """Get completeness status for all TOGAF phases."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        result = orch.get_phase_status()
        return api_success(data=result)
    except Exception as e:
        logger.error("Phase status failed: %s", e, exc_info=True)
        return api_error("Failed to get phase status", 500)


# ── Inline Element Editing ────────────────────────────────────────

@journey_v2_bp.route("/<int:solution_id>/element/<int:element_id>", methods=["PATCH"])
@login_required
@_require_solution_owner
def update_element(solution_id, element_id):
    """Update an element's name, description, type, layer, or acm_properties."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        data = request.get_json() or {}
        orch = JourneyOrchestrator(solution_id)
        result = orch.update_element(element_id, data)
        if result.get("error"):
            return api_error(result["error"], result.get("status_code", 400))
        return api_success(data=result)
    except Exception as e:
        logger.error("Element update failed: %s", e, exc_info=True)
        return api_error("Failed to update element", 500)


@journey_v2_bp.route("/<int:solution_id>/proposal/<int:proposal_id>/business-rules", methods=["PATCH"])
@login_required
@_require_solution_owner
def update_proposal_business_rules(solution_id, proposal_id):
    """Save business rules on a SolutionBlueprintProposal's acm_properties.

    Body: {"rules": [{"name": str, "condition": str, "severity": "must|should|may"}]}
    Merges into acm_properties["business_rules"] — idempotent.
    """
    from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
    try:
        proposal = SolutionBlueprintProposal.query.filter_by(
            id=proposal_id, solution_id=solution_id
        ).first()
        if not proposal:
            return api_error("Proposal not found", 404)
        body = request.get_json(silent=True) or {}
        rules = body.get("rules", [])
        props = dict(proposal.acm_properties or {})
        props["business_rules"] = rules
        proposal.acm_properties = props
        db.session.commit()
        return api_success(data={"proposal_id": proposal_id, "rules": rules})
    except Exception as e:
        logger.error("Business rules update failed: %s", e, exc_info=True)
        return api_error("Failed to save business rules", 500)


@journey_v2_bp.route("/<int:solution_id>/element/<int:element_id>", methods=["DELETE"])
@login_required
@_require_solution_owner
def delete_element(solution_id, element_id):
    """Delete an ArchiMate element and its relationships from the journey."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        result = orch.delete_element(element_id)
        return api_success(data=result)
    except Exception as e:
        logger.error("Element delete failed: %s", e, exc_info=True)
        return api_error("Failed to delete element", 500)


@journey_v2_bp.route("/<int:solution_id>/element", methods=["POST"])
@login_required
@_require_solution_owner
def create_element(solution_id):
    """Create a new ArchiMate element in the journey architecture."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        data = request.get_json() or {}
        if not data.get("name") or not data.get("type") or not data.get("layer"):
            return api_error("name, type, and layer are required", 400)
        orch = JourneyOrchestrator(solution_id)
        result = orch.create_element(data)
        return api_success(data=result)
    except Exception as e:
        logger.error("Element create failed: %s", e, exc_info=True)
        return api_error("Failed to create element", 500)


@journey_v2_bp.route("/<int:solution_id>/relationship", methods=["POST"])
@login_required
@_require_solution_owner
def create_relationship(solution_id):
    """Create an ArchiMate relationship between two journey elements."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        data = request.get_json() or {}
        source_id = data.get("source_id")
        target_id = data.get("target_id")
        rel_type = data.get("type", "realization")
        if not source_id or not target_id:
            return api_error("source_id and target_id are required", 400)
        orch = JourneyOrchestrator(solution_id)
        result = orch.create_relationship(source_id, target_id, rel_type)
        return api_success(data=result)
    except Exception as e:
        logger.error("Relationship create failed: %s", e, exc_info=True)
        return api_error("Failed to create relationship", 500)


# ── Step 1: Clarification ────────────────────────────────────────

_MAX_BRIEF_CHARS = 50_000  # Raised to preserve full document context for LLM


def _sanitize_llm_input(text: str, max_chars: int = _MAX_BRIEF_CHARS) -> str:
    """Strip control chars and truncate to prevent prompt injection / token DoS."""
    import re
    # Remove null bytes, form feeds, vertical tabs, and other C0/C1 controls
    # (keep newlines and tabs which are legitimate in a brief)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text[:max_chars]


@journey_v2_bp.route("/<int:solution_id>/clarify", methods=["POST"])
@login_required
@_require_solution_owner
def clarify(solution_id):
    """Generate clarifying questions from the problem brief."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        data = request.get_json() or {}
        problem = _sanitize_llm_input(data.get("problem_statement", "") or "")
        if not problem:
            return api_error("Problem statement is required", 400)

        orch = JourneyOrchestrator(solution_id)
        result = orch.generate_clarifying_questions(problem)

        # JPL-003/JPL-008: Advance state machine — problem is now defined,
        # solution is ready for capability discovery regardless of whether
        # the user goes through Q&A or skips directly to the pipeline.
        _advance_journey_state(solution_id, "capability_discovery")

        return api_success(data=result)
    except Exception as e:
        logger.error("Clarification failed: %s", e, exc_info=True)
        return api_error("Failed to generate questions", 500)


@journey_v2_bp.route("/<int:solution_id>/clarify-answers", methods=["POST"])
@login_required
@_require_solution_owner
def clarify_answers(solution_id):
    """Merge clarification answers into enriched brief."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        data = request.get_json() or {}
        original = _sanitize_llm_input(data.get("original_brief") or data.get("problem_statement", "") or "")
        raw_answers = data.get("answers", [])
        # Sanitize each answer string in the list
        answers = [
            _sanitize_llm_input(a, max_chars=5_000) if isinstance(a, str) else a
            for a in raw_answers
        ]

        orch = JourneyOrchestrator(solution_id)
        result = orch.merge_clarification_answers(original, answers)

        # JPL-008: Advance state machine after clarification is complete
        _advance_journey_state(solution_id, "capability_discovery")

        return api_success(data=result)
    except Exception as e:
        logger.error("Answer merge failed: %s", e, exc_info=True)
        return api_error("Failed to merge answers", 500)


@journey_v2_bp.route("/<int:solution_id>/link-clarify-entities", methods=["POST"])
@login_required
@_require_solution_owner
def link_clarify_entities(solution_id):
    """Link entities selected during clarification to the solution."""
    from app import db

    data = request.get_json(silent=True) or {}
    entities = data.get('entities', {})
    linked = {}

    # Applications → solution_applications junction (column is application_component_id)
    app_ids = entities.get('applications', [])
    if app_ids:
        try:
            from app.models.solution_models import solution_applications
            existing = set(
                r[0] for r in db.session.query(solution_applications.c.application_component_id)
                .filter(solution_applications.c.solution_id == solution_id).all()
            )
            new_ids = [aid for aid in app_ids if aid not in existing]
            for aid in new_ids:
                db.session.execute(solution_applications.insert().values(  # tenant-filtered: scoped via parent FK (solution_id)
                    solution_id=solution_id, application_component_id=aid
                ))
            linked['applications'] = len(new_ids)
        except Exception as e:
            logger.warning('Failed to link applications for solution %s: %s', solution_id, e)

    # ArchiMate elements → solution_archimate_elements junction
    # SolutionArchiMateElement has NOT NULL: layer_type, element_table
    elem_ids = entities.get('archimate_elements', [])
    if elem_ids:
        try:
            from app.models.solution_models import SolutionArchiMateElement
            from app.models.archimate_core import ArchiMateElement
            existing = set(
                r.element_id for r in SolutionArchiMateElement.query
                .filter_by(solution_id=solution_id).all()
            )
            new_ids = [eid for eid in elem_ids if eid not in existing]
            if new_ids:
                elements = {e.id: e for e in ArchiMateElement.query.filter(
                    ArchiMateElement.id.in_(new_ids)).all()}
                for eid in new_ids:
                    elem = elements.get(eid)
                    if elem:
                        db.session.add(SolutionArchiMateElement(
                            solution_id=solution_id,
                            element_id=eid,
                            layer_type=elem.layer or 'application',
                            element_table=getattr(elem, '__tablename__', 'archimate_elements'),
                            element_name=elem.name
                        ))
            linked['archimate_elements'] = len(new_ids)
        except Exception as e:
            logger.warning('Failed to link ArchiMate elements for solution %s: %s', solution_id, e)

    # Capabilities → solution_capability_mappings junction
    cap_ids = entities.get('capabilities', [])
    if cap_ids:
        try:
            from app.models.solution_models import SolutionCapabilityMapping
            existing = set(
                r.capability_id for r in SolutionCapabilityMapping.query
                .filter_by(solution_id=solution_id).all()
            )
            for cid in cap_ids:
                if cid not in existing:
                    db.session.add(SolutionCapabilityMapping(
                        solution_id=solution_id, capability_id=cid
                    ))
            linked['capabilities'] = len([c for c in cap_ids if c not in existing])
        except Exception as e:
            logger.warning('Failed to link capabilities for solution %s: %s', solution_id, e)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error('Failed to commit entity links for solution %s: %s', solution_id, e)
        return api_error('Failed to link entities', 500)

    return api_success(data={'linked': linked})


# ── Step 2: Capability Derivation ────────────────────────────────

@journey_v2_bp.route("/<int:solution_id>/derive-capabilities", methods=["POST"])
@login_required
@_require_solution_owner
def derive_capabilities(solution_id):
    """Derive business capabilities from problem description + structured context."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        data = request.get_json() or {}

        # Build enriched description from structured context if available
        description = _sanitize_llm_input(data.get("description") or data.get("enriched_brief", "") or "")
        structured = data.get("structured_context", {})
        if structured:
            # Append structured constraints to the description so the LLM considers them
            context_parts = []
            if structured.get("business_domain"):
                context_parts.append(f"Industry: {structured['business_domain']}")
            if structured.get("compliance_frameworks"):
                context_parts.append(f"Compliance: {', '.join(structured['compliance_frameworks'])}")
            if structured.get("tech_constraints"):
                for tc in structured["tech_constraints"]:
                    context_parts.append(f"Technology constraint: {tc.get('type', '')} {tc.get('technology', '')}")
            if structured.get("nfrs"):
                for nfr in structured["nfrs"]:
                    context_parts.append(f"NFR ({nfr.get('priority', 'must')}): {nfr.get('type', '')} {nfr.get('target', '')}")
            if context_parts:
                description = description + "\n\nStructured requirements:\n- " + "\n- ".join(context_parts)

        orch = JourneyOrchestrator(solution_id)
        result = orch.derive_capabilities(
            problem_description=description,
            motivation_elements=data.get("motivation_elements", []),
        )

        # Post-process: add acm_domain for any capability missing it.
        # JourneyOrchestrator returns BusinessCapability catalog matches without
        # acm_domain; the keyword scorer below mirrors JourneyReasoningOrchestrator.
        try:
            from app.modules.solutions_strategic.v2.services.journey_reasoning_orchestrator import (
                JourneyReasoningOrchestrator,
            )
            _scorer = JourneyReasoningOrchestrator.__new__(JourneyReasoningOrchestrator)
            for cap in result.get("capabilities", []):
                if not cap.get("acm_domain"):
                    cap["acm_domain"] = _scorer._infer_acm_domain(
                        cap.get("name", ""), cap.get("description", "")
                    )
        except Exception as _e:
            logger.warning("acm_domain inference skipped: %s", _e)

        # Persist derived capabilities to DB so generation survives page refresh
        try:
            from app.models.solution_capability import SolutionCapability
            SolutionCapability.query.filter_by(
                solution_id=solution_id, source="ai_derived"
            ).delete()
            for cap in result.get("capabilities", []):
                sc = SolutionCapability(
                    solution_id=solution_id,
                    name=cap.get("name", ""),
                    description=cap.get("description", ""),
                    category="required",
                    source="ai_derived",
                    match_type=cap.get("match_type", "novel"),
                    match_score=cap.get("match_score"),
                    closest_match_id=cap.get("id") if isinstance(cap.get("id"), int) else None,
                )
                db.session.add(sc)
            db.session.commit()
        except Exception as _save_err:
            logger.warning("Could not persist derived capabilities: %s", _save_err)
            db.session.rollback()

        # TRAC-002: write Goal→Capability + Driver→Capability inference relationships
        try:
            _sync_capability_realization_links(solution_id)
        except Exception as _trac_err:
            logger.warning("TRAC-002 realization sync failed (non-fatal): %s", _trac_err)

        return api_success(data=result)
    except Exception as e:
        logger.error("Capability derivation failed: %s", e, exc_info=True)
        return api_error("Failed to derive capabilities", 500)


@journey_v2_bp.route("/<int:solution_id>/capability-details/<int:capability_id>", methods=["GET"])
@login_required
@_require_solution_owner
def capability_details(solution_id, capability_id):
    """Get technical caps, coverage, compliance, APQC for one capability."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        cap_name = request.args.get("name", "")
        domain = request.args.get("domain", "")

        orch = JourneyOrchestrator(solution_id)
        result = orch.get_capability_details(capability_id, cap_name, domain)
        return api_success(data=result)
    except Exception as e:
        logger.error("Capability details failed: %s", e, exc_info=True)
        return api_error("Failed to load capability details", 500)


# ── ACM Domain-Driven Step 2 ────────────────────────────────────

@journey_v2_bp.route("/<int:solution_id>/populate-domains", methods=["POST"])
@login_required
@_require_solution_owner
def populate_domains(solution_id):
    """Populate all 7 ACM domains with baselines + LLM suggestions."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        data = request.get_json() or {}
        orch = JourneyOrchestrator(solution_id)
        result = orch.populate_domains(
            enriched_brief=data.get("enriched_brief", ""),
            industry_overlay=data.get("industry_overlay"),
        )

        # JPL-008: Advance state to capability_confirmation after domains populated
        _advance_journey_state(solution_id, "capability_confirmation")

        return api_success(data=result)
    except Exception as e:
        logger.error("Domain population failed: %s", e, exc_info=True)
        return api_error("Failed to populate domains", 500)


@journey_v2_bp.route("/<int:solution_id>/load-domains", methods=["GET"])
@login_required
@_require_solution_owner
def load_domains(solution_id):
    """Reload domain data from DB (for session restore when domainsPopulated=true)."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        result = orch.load_domains()
        return api_success(data=result)
    except Exception as e:
        logger.error("Load domains failed: %s", e, exc_info=True)
        return api_error("Failed to load domains", 500)


@journey_v2_bp.route("/<int:solution_id>/promoted-elements", methods=["GET"])
@login_required
@_require_solution_owner
def promoted_elements(solution_id):
    """Return all promoted/accepted elements grouped by ArchiMate layer."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        result = orch.get_promoted_elements()
        return api_success(data=result)
    except Exception as e:
        logger.error("Promoted elements failed: %s", e, exc_info=True)
        return api_error("Failed to load promoted elements", 500)


@journey_v2_bp.route("/<int:solution_id>/domain/<domain_code>/confirm", methods=["POST"])
@login_required
@_require_solution_owner
def confirm_domain(solution_id, domain_code):
    """Confirm a domain — promotes accepted proposals to ArchiMate elements."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        result = orch.confirm_domain(domain_code)
        # If promotion returned a property_coverage error, surface it as 422
        if result.get("error") == "property_coverage_below_threshold":
            return api_error(result["message"], 422)

        # JPL-008: Advance state to landscape_mapping after domain confirmation
        # (idempotent — if already at landscape_mapping, this is a no-op)
        _advance_journey_state(solution_id, "landscape_mapping")

        # Enrich response with technical capability context for this domain so the
        # UI can surface 'what does this domain need to deliver technically?'
        result["capability_context"] = _get_domain_capability_context(domain_code)

        # Best-effort: pre-fill roadmap properties for all promoted elements so
        # Step 5 opens with Build/Buy/Effort pre-populated instead of blank.
        try:
            from app.models.solution_models import Solution as _Sol
            _sol = _Sol.query.get(solution_id)
            _problem_summary = ""
            if _sol:
                _problem_summary = _sol.problem_clarification or _sol.description or _sol.name or ""
            orch.auto_fill_roadmap_properties(problem_summary=_problem_summary)
        except Exception as _af_err:
            logger.warning("auto_fill_roadmap_properties skipped after confirm_domain: %s", _af_err)

        # Spawn background ARM relationship rebuild so scorer picks up wizard-generated
        # relationships (written to AIR by domain_promotion.py, not ARM).
        # Worker is module-level in app/tasks/rebuild_tasks.py — required for pickle-safe spawn.
        _rebuild_scheduled = False
        try:
            import multiprocessing as _mp
            import os as _os
            from app.tasks.rebuild_tasks import rebuild_relationships_worker
            _app_root = _os.path.dirname(
                _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
            )
            _ctx = _mp.get_context("spawn")
            _proc = _ctx.Process(
                target=rebuild_relationships_worker,
                args=(_app_root, solution_id),
                daemon=False,
            )
            _proc.start()
            _proc.join(timeout=0)  # non-blocking reap to prevent zombie accumulation
            _rebuild_scheduled = True
            logger.info("Spawned rebuild_relationships PID=%d for solution %d", _proc.pid, solution_id)
        except Exception as _spawn_err:
            logger.warning(
                "Could not spawn rebuild_relationships for solution %d: %s",
                solution_id, _spawn_err,
            )

        result["rebuild_scheduled"] = _rebuild_scheduled
        return api_success(data=result)
    except Exception as e:
        logger.error("Domain confirmation failed: %s", e, exc_info=True)
        return api_error("Failed to confirm domain", 500)


@journey_v2_bp.route("/<int:solution_id>/domain/<domain_code>/status", methods=["POST"])
@login_required
@_require_solution_owner
def update_domain_status(solution_id, domain_code):
    """Update domain status (tier, not_applicable, etc.)."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        data = request.get_json() or {}
        orch = JourneyOrchestrator(solution_id)
        result = orch.update_domain_status(
            domain_code=domain_code,
            status=data.get("status"),
            justification=data.get("justification"),
            tier=data.get("tier"),
        )
        return api_success(data=result)
    except Exception as e:
        logger.error("Domain status update failed: %s", e, exc_info=True)
        return api_error("Failed to update domain status", 500)


@journey_v2_bp.route("/<int:solution_id>/cross-domain-check", methods=["POST"])
@login_required
@_require_solution_owner
def cross_domain_check(solution_id):
    """Evaluate cross-domain dependencies for an element."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        data = request.get_json() or {}
        orch = JourneyOrchestrator(solution_id)
        result = orch.check_cross_domain(
            domain=data.get("domain", ""),
            archimate_type=data.get("archimate_type", ""),
            element_name=data.get("element_name", ""),
        )
        return api_success(data=result)
    except Exception as e:
        logger.error("Cross-domain check failed: %s", e, exc_info=True)
        return api_error("Failed to check cross-domain dependencies", 500)


@journey_v2_bp.route("/<int:solution_id>/domain-completeness", methods=["GET"])
@login_required
@_require_solution_owner
def domain_completeness(solution_id):
    """Get completeness scores and blockers."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        result = orch.get_domain_completeness()
        return api_success(data=result)
    except Exception as e:
        logger.error("Completeness check failed: %s", e, exc_info=True)
        return api_error("Failed to get completeness", 500)


@journey_v2_bp.route("/<int:solution_id>/property-templates/<archimate_type>", methods=["GET"])
@login_required
@_require_solution_owner
def get_property_templates(solution_id, archimate_type):
    """Get property templates for an element type, filtered by tier."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        domain = request.args.get("domain")
        tier = request.args.get("tier", "standard")
        orch = JourneyOrchestrator(solution_id)
        result = orch.get_property_templates(archimate_type, domain, tier)
        return api_success(data={"properties": result})
    except Exception as e:
        logger.error("Property templates failed: %s", e, exc_info=True)
        return api_error("Failed to get property templates", 500)


@journey_v2_bp.route("/<int:solution_id>/proposals/<int:proposal_id>/properties", methods=["PATCH"])
@login_required
@_require_solution_owner
def update_proposal_properties(solution_id, proposal_id):
    """Update properties on a proposal. Sets source to 'user'."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        data = request.get_json() or {}
        orch = JourneyOrchestrator(solution_id)
        result = orch.update_proposal_properties(proposal_id, data.get("properties", {}))
        if result.get("error"):
            return api_error(result["error"], result.get("status_code", 400))
        return api_success(data=result)
    except Exception as e:
        logger.error("Property update failed: %s", e, exc_info=True)
        return api_error("Failed to update properties", 500)


# ── Domain Property Generation ───────────────────────────────

@journey_v2_bp.route("/<int:solution_id>/domain/<domain_code>/generate-properties", methods=["POST"])
@login_required
@_require_solution_owner
def generate_domain_properties(solution_id, domain_code):
    """LLM-populate properties for all proposals in a domain.

    Uses solution context to suggest specific values for properties still at
    generic defaults or empty. Never overwrites user-edited properties.
    """
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        data = request.get_json() or {}
        problem_summary = data.get("problem_summary", "")
        orch = JourneyOrchestrator(solution_id)
        result = orch.generate_domain_properties(domain_code, problem_summary)
        if result.get("error"):
            return api_error(result["error"], 500)
        return api_success(data=result)
    except Exception as e:
        logger.error("Domain property generation failed for %s: %s", domain_code, e, exc_info=True)
        return api_error("Failed to generate properties", 500)


@journey_v2_bp.route("/<int:solution_id>/domain/<domain_code>/apply-default-properties", methods=["POST"])
@login_required
@_require_solution_owner
def apply_default_domain_properties(solution_id, domain_code):
    """Pre-fill empty ACM slots from DB templates + safe heuristics (no LLM, no compliance IDs)."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator

        orch = JourneyOrchestrator(solution_id)
        result = orch.apply_default_properties_to_domain(domain_code)
        return api_success(data=result)
    except Exception as e:
        logger.error("apply-default-properties failed for %s: %s", domain_code, e, exc_info=True)
        return api_error("Failed to apply default properties", 500)


@journey_v2_bp.route("/<int:solution_id>/auto-fill-roadmap", methods=["POST"])
@login_required
@_require_solution_owner
def auto_fill_roadmap(solution_id):
    """LLM-populate build_or_buy, estimated_effort, and implementation_status for all promoted elements."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        from app.models.solution_models import Solution

        sol = Solution.query.get(solution_id)
        problem_summary = ""
        if sol:
            problem_summary = sol.problem_clarification or sol.description or sol.name or ""
        orch = JourneyOrchestrator(solution_id)
        result = orch.auto_fill_roadmap_properties(problem_summary=problem_summary)
        return api_success(data=result)
    except Exception as e:
        logger.error("auto-fill-roadmap failed for solution %s: %s", solution_id, e, exc_info=True)
        return api_error("Failed to auto-fill roadmap properties", 500)


# ── Domain Unconfirm ─────────────────────────────────────────

@journey_v2_bp.route("/<int:solution_id>/domain/<domain_code>/unconfirm", methods=["POST"])
@login_required
@_require_solution_owner
def unconfirm_domain(solution_id, domain_code):
    """Revert a confirmed domain to pending, removing promoted elements."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        result = orch.unconfirm_domain(domain_code)
        if result.get("error"):
            return api_error(result["error"], 422)
        return api_success(data=result)
    except Exception as e:
        logger.error("Domain unconfirm failed: %s", e, exc_info=True)
        return api_error("Failed to unconfirm domain", 500)


# ── Step 4: Decision Points (data-driven) ───────────────────

@journey_v2_bp.route("/<int:solution_id>/decision-points", methods=["GET"])
@login_required
@_require_solution_owner
def decision_points(solution_id):
    """Return decision points derived from element properties.

    JPL-009: Hardened to gracefully return empty when no proposals exist
    or the SolutionBlueprintProposal table has issues. Also surfaces gap
    data from the v2 reasoning orchestrator when available.
    """
    decisions_result = {"decision_points": [], "total_elements": 0}

    # 1. Try the old orchestrator (SolutionBlueprintProposal-based decisions)
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        decisions_result = orch.get_decision_points()
    except Exception as e:
        logger.warning("Old decision_points path failed for solution %d: %s", solution_id, e)

    # 2. Supplement with v2 reasoning gaps if available
    try:
        from app.modules.solutions_strategic.v2.services.journey_reasoning_orchestrator import (
            JourneyReasoningOrchestrator,
        )
        v2_orch = JourneyReasoningOrchestrator(solution_id)
        journey_data = v2_orch._get_journey_data()
        gaps = journey_data.get("gaps", [])
        if gaps:
            gap_decisions = []
            for g in gaps:
                gap_decisions.append({
                    "id": g.get("gap_id", ""),
                    "name": g.get("entity_name", "Unknown"),
                    "type": g.get("gap_type", "unknown"),
                    "severity": g.get("severity", "medium"),
                    "rationale": g.get("rationale", ""),
                    "recommended_mitigation": g.get("recommended_mitigation", ""),
                })
            if gap_decisions:
                decisions_result.setdefault("gaps", []).extend(gap_decisions)
    except Exception as e:
        logger.debug("V2 gap supplement failed for solution %d: %s", solution_id, e)

    # Synthesise architecture_decisions narrative so blueprint scorer can measure it.
    # section_narratives['architecture_decisions'] is narrative_only scored (100 word threshold).
    # Decision points are derived data — writing the narrative here is idempotent and safe.
    try:
        _write_arch_decisions_narrative(solution_id, decisions_result)
    except Exception as _nd_err:
        logger.debug("arch_decisions narrative write failed (non-fatal): %s", _nd_err)

    return api_success(data=decisions_result)


# ── Step 5: Roadmap Data (data-driven) ──────────────────────

@journey_v2_bp.route("/<int:solution_id>/roadmap-data", methods=["GET"])
@login_required
@_require_solution_owner
def roadmap_data(solution_id):
    """Return phased roadmap derived from element properties."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        result = orch.get_roadmap_data()
        return api_success(data=result)
    except Exception as e:
        logger.error("Roadmap data failed: %s", e, exc_info=True)
        return api_error("Failed to load roadmap data", 500)


# ── Step 6: ARB Package ─────────────────────────────────────

@journey_v2_bp.route("/<int:solution_id>/arb-package", methods=["GET"])
@login_required
@_require_solution_owner
def arb_package(solution_id):
    """Return complete ARB submission package."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        result = orch.get_arb_package()
        return api_success(data=result)
    except Exception as e:
        logger.error("ARB package failed: %s", e, exc_info=True)
        return api_error("Failed to load ARB package", 500)


# ── Step 4B: LLM Variant Generation ─────────────────────────

@journey_v2_bp.route("/<int:solution_id>/generate-variants", methods=["POST"])
@login_required
@_require_solution_owner
def generate_variants(solution_id):
    """Generate LLM architecture option variants for Step 4 decision cards."""
    try:
        from app.modules.architecture_assistant.variant_generator import VariantGeneratorService
        data = request.get_json() or {}

        raw_elements = data.get("architecture_elements", [])
        capabilities = data.get("capabilities", [])
        problem_summary = data.get("problem_summary", "")

        # Convert flat list [{id, name, type, layer}] → dict {layer: [elements]}
        elements: dict = {}
        for el in raw_elements:
            layer = el.get("layer", "application")
            elements.setdefault(layer, []).append(el)

        # Enrich with structured context if provided
        structured = data.get("structured_context", {})
        if structured:
            context_parts = []
            if structured.get("business_domain"):
                context_parts.append(f"Industry/domain: {structured['business_domain']}")
            if structured.get("timeline_months"):
                context_parts.append(f"Delivery timeline: {structured['timeline_months']} months")
            bmin = structured.get("budget_min")
            bmax = structured.get("budget_max")
            if bmin or bmax:
                parts = []
                if bmin:
                    parts.append(f"min £{int(bmin):,}")
                if bmax:
                    parts.append(f"max £{int(bmax):,}")
                context_parts.append(f"Budget: {', '.join(parts)}")
            if structured.get("organization_size"):
                context_parts.append(f"Organisation size: {structured['organization_size']}")
            if structured.get("compliance_frameworks"):
                context_parts.append(f"Compliance: {', '.join(structured['compliance_frameworks'])}")
            if context_parts:
                problem_summary = (
                    problem_summary + "\n\nArchitecture constraints:\n- " + "\n- ".join(context_parts)
                )

        svc = VariantGeneratorService()
        result = svc.generate_variants(
            architecture_elements=elements,
            capabilities=capabilities,
            problem_summary=problem_summary,
            solution_id=solution_id,
        )
        return api_success(data=result)
    except Exception as e:
        logger.error("Variant generation failed: %s", e, exc_info=True)
        return api_error("Failed to generate variants", 500)


@journey_v2_bp.route("/<int:solution_id>/select-variant", methods=["POST"])
@login_required
@_require_solution_owner
def select_variant(solution_id):
    """Persist a selected architecture variant option and create associated risk records."""
    try:
        from app.modules.architecture_assistant.variant_generator import VariantGeneratorService
        data = request.get_json() or {}

        decision_point_id = data.get("decision_point_id", "")
        option_id = data.get("option_id", "")
        option_name = data.get("option_name", "")
        approach = data.get("approach", "")
        affected_elements = data.get("affected_elements", [])

        # Build a minimal decision_points list sufficient for select_variant
        decision_points = [{
            "id": decision_point_id,
            "options": [{
                "id": option_id,
                "name": option_name,
                "approach": approach,
                "risks": data.get("risks", []),
            }],
            "affected_elements": affected_elements,
        }]

        svc = VariantGeneratorService()
        result = svc.select_variant(
            solution_id=solution_id,
            decision_point_id=decision_point_id,
            option_id=option_id,
            decision_points=decision_points,
        )
        return api_success(data=result)
    except Exception as e:
        logger.error("Variant selection failed: %s", e, exc_info=True)
        return api_error("Failed to save variant selection", 500)


# ── Step 6 Exports ────────────────────────────────────────────

@journey_v2_bp.route("/<int:solution_id>/export/roadmap.csv")
@login_required
@_require_solution_owner
def export_roadmap_csv(solution_id):
    """Download implementation roadmap as CSV."""
    import csv
    import io
    from flask import Response
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        roadmap = orch.get_roadmap_data()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Phase", "Phase Name", "Element Name", "Element Type", "Layer",
                         "Effort Weeks", "Dependencies", "Status"])
        for phase in roadmap.get("phases", []):
            phase_num = phase.get("phase_number", "")
            phase_name = phase.get("name", "")
            for el in phase.get("elements", []):
                writer.writerow([
                    phase_num,
                    phase_name,
                    el.get("name", ""),
                    el.get("archimate_type", ""),
                    el.get("layer", ""),
                    el.get("effort_weeks", ""),
                    "; ".join(el.get("dependencies", [])),
                    el.get("status", ""),
                ])

        csv_data = output.getvalue()
        solution = Solution.query.get_or_404(solution_id)
        filename = f"roadmap_{solution.name.replace(' ', '_')}.csv" if solution.name else f"roadmap_{solution_id}.csv"
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        logger.error("Roadmap CSV export failed: %s", e, exc_info=True)
        return api_error("Failed to export roadmap", 500)


@journey_v2_bp.route("/<int:solution_id>/export/arb-package")
@login_required
@_require_solution_owner
def export_arb_package(solution_id):
    """Return a printable HTML ARB package for PDF generation."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        package = orch.get_arb_package()
        solution = Solution.query.get_or_404(solution_id)
        return render_template(
            "architecture_assistant/arb_package_print.html",
            package=package,
            solution=solution,
        )
    except Exception as e:
        logger.error("ARB package export failed: %s", e, exc_info=True)
        return api_error("Failed to export ARB package", 500)


@journey_v2_bp.route("/<int:solution_id>/rebuild-relationships", methods=["POST"])
@login_required
@_require_solution_owner
def rebuild_relationships(solution_id):
    """Re-run Pass 2 LLM relationship generation for existing elements."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        result = orch.rebuild_relationships()
        return api_success(data=result)
    except Exception as e:
        logger.error("Rebuild relationships failed: %s", e, exc_info=True)
        return api_error("Failed to rebuild relationships", 500)


@journey_v2_bp.route("/<int:solution_id>/decisions/generate", methods=["POST"])
@login_required
@_require_solution_owner
def generate_decision_rationale(solution_id):
    """Fire-and-forget: spawn LLM decision generation for TBD build/buy proposals.

    Returns immediately (≤ 1s). LLM calls run in a background subprocess.
    Poll GET /decisions/status to detect completion.
    """
    try:
        import multiprocessing as _mp
        import os as _os
        from app.tasks.rebuild_tasks import generate_decision_rationale_worker
        _app_root = _os.path.dirname(
            _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        )
        _ctx = _mp.get_context("spawn")
        _proc = _ctx.Process(
            target=generate_decision_rationale_worker,
            args=(_app_root, solution_id),
            daemon=False,
        )
        _proc.start()
        _proc.join(timeout=0)  # non-blocking reap
        logger.info(
            "Spawned generate_decision_rationale PID=%d sol=%d", _proc.pid, solution_id
        )
        return api_success(data={"job_started": True, "pid": _proc.pid})
    except Exception as e:
        logger.error("generate_decision_rationale spawn failed sol=%d: %s", solution_id, e)
        return api_error("Failed to start decision generation", 500)


@journey_v2_bp.route("/<int:solution_id>/decisions/status", methods=["GET"])
@login_required
@_require_solution_owner
def decision_rationale_status(solution_id):
    """Return count of proposals still pending TBD resolution.

    'complete' = no proposals remain with TBD build_or_buy AND null decision_rationale.
    Counts TBD-only proposals (not all proposals) — the worker skips already-clean
    proposals which never get a rationale write, so total != resolved for clean solutions.
    """
    try:
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
        total = SolutionBlueprintProposal.query.filter_by(solution_id=solution_id).count()
        pending = SolutionBlueprintProposal.query.filter(
            SolutionBlueprintProposal.solution_id == solution_id,
            SolutionBlueprintProposal.acm_properties["build_or_buy"].as_string().contains("TBD"),
            SolutionBlueprintProposal.decision_rationale.is_(None),
        ).count()
        return api_success(data={
            "total": total,
            "pending_tbd": pending,
            "complete": pending == 0,
        })
    except Exception as e:
        logger.error("decision_rationale_status failed sol=%d: %s", solution_id, e)
        return api_error("Failed to get decision status", 500)


# ── Backfill: Add missing templates + relationships ──────────

@journey_v2_bp.route("/<int:solution_id>/backfill-domains", methods=["POST"])
@login_required
@_require_solution_owner
def backfill_domains(solution_id):
    """One-time backfill: add missing baseline proposals from new templates,
    promote them, and create default relationships for all promoted elements."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        result = orch.backfill_domain_elements()
        return api_success(data=result)
    except Exception as e:
        logger.error("Backfill failed: %s", e, exc_info=True)
        return api_error("Failed to backfill", 500)


# ── Step 3: Architecture Generation ──────────────────────────────

def _build_arch_gen_args(data, solution_id):
    """Extract and enrich generate-architecture arguments from request data + DB.

    Returns (problem_summary, capabilities, compliance_constraints).
    """
    problem_summary = data.get("problem_summary", "")
    if not problem_summary:
        sol = Solution.query.get(solution_id)
        if sol:
            problem_summary = sol.problem_clarification or sol.description or ""

    structured = data.get("structured_context", {})
    if structured:
        context_parts = []
        if structured.get("business_domain"):
            context_parts.append(f"Industry/domain: {structured['business_domain']}")
        if structured.get("timeline_months"):
            context_parts.append(f"Delivery timeline: {structured['timeline_months']} months")
        bmin = structured.get("budget_min")
        bmax = structured.get("budget_max")
        if bmin or bmax:
            parts = []
            if bmin:
                parts.append(f"min £{bmin:,}")
            if bmax:
                parts.append(f"max £{bmax:,}")
            context_parts.append(f"Budget: {', '.join(parts)}")
        if structured.get("organization_size"):
            context_parts.append(f"Organization size: {structured['organization_size']}")
        if structured.get("geographic_scope"):
            context_parts.append(f"Geographic scope: {structured['geographic_scope']}")
        if structured.get("compliance_frameworks"):
            context_parts.append(
                f"Compliance requirements: {', '.join(structured['compliance_frameworks'])}"
            )
        for nfr in (structured.get("nfrs") or []):
            if nfr.get("target"):
                context_parts.append(
                    f"NFR ({nfr.get('priority', 'must')} — {nfr.get('type', '')}): {nfr['target']}"
                )
        if context_parts:
            problem_summary = (
                problem_summary
                + "\n\nArchitecture constraints from structured intake:\n- "
                + "\n- ".join(context_parts)
            )

    compliance_constraints = data.get("compliance_constraints", [])
    if not compliance_constraints:
        try:
            from app.models.solution_architect_models import SolutionConstraint, SolutionRequirement
            db_constraints = SolutionConstraint.query.filter_by(
                problem_id=_get_problem_id(solution_id)
            ).all()
            for c in db_constraints:
                if c.constraint_type and c.constraint_type.value == "compliance":
                    compliance_constraints.append(c.name)
            db_nfrs = SolutionRequirement.query.filter_by(
                solution_id=solution_id, req_type="non_functional"
            ).all()
            for nfr in db_nfrs:
                if nfr.name and nfr.description:
                    compliance_constraints.append(f"{nfr.name}: {nfr.description}")
        except Exception as _nfr_exc:
            logger.debug("Could not load compliance constraints from DB: %s", _nfr_exc)

    enriched = _enrich_capabilities_with_technical_data(data.get("capabilities", []))
    return problem_summary, enriched, compliance_constraints


def _enrich_capabilities_with_technical_data(capabilities: list) -> list:
    """Attach TechnicalCapability and ApplicationCapabilityMapping data to each capability.

    Queries ACM technical capabilities by keyword match on capability name, and queries
    ApplicationCapabilityMapping for gap analysis data. Both degrade gracefully on failure.
    """
    import json as _json
    from flask import g
    try:
        from app.models.technical_capability import TechnicalCapability
        from app.models.application_capability import ApplicationCapabilityMapping
        from app.models.application_layer import ApplicationComponent
        from app.models.business_capabilities import BusinessCapability
    except Exception:
        return capabilities

    enriched = []
    for cap in capabilities:
        result = dict(cap)
        cap_id = cap.get("id")
        cap_name = cap.get("name", "")

        # ── Technical capability ACM matches ──────────────────────────────
        try:
            keyword = cap_name.split()[0] if cap_name else ""
            tech_caps = (
                TechnicalCapability.query
                .filter(TechnicalCapability.name.ilike(f"%{keyword}%"))
                .order_by(TechnicalCapability.is_differentiating.desc(),
                          TechnicalCapability.is_foundational.desc())
                .limit(4)
                .all()
            )
            tc_list = []
            for tc in tech_caps:
                patterns = []
                try:
                    patterns = _json.loads(tc.technology_patterns) if tc.technology_patterns else []
                except Exception as exc:
                    logger.debug("suppressed error in _enrich_capabilities_with_technical_data (app/modules/solutions_strategic/v2/routes/journey_v2_routes.py): %s", exc)
                techs = []
                try:
                    techs = _json.loads(tc.common_technologies) if tc.common_technologies else []
                except Exception as exc:
                    logger.debug("suppressed error in _enrich_capabilities_with_technical_data (app/modules/solutions_strategic/v2/routes/journey_v2_routes.py): %s", exc)
                tc_list.append({
                    "name": tc.name,
                    "acm_domain": tc.acm_domain or "",
                    "technology_patterns": patterns,
                    "common_technologies": techs,
                    "is_differentiating": bool(tc.is_differentiating),
                    "is_foundational": bool(tc.is_foundational),
                    "industry_maturity": tc.industry_maturity or "",
                    "complexity": tc.complexity or "",
                })
            result["technical_capabilities"] = tc_list
        except Exception:
            result["technical_capabilities"] = []

        # ── Application coverage gap data ─────────────────────────────────
        if cap_id:
            try:
                # cap_id comes straight from the request JSON body (not an
                # org-scoped DB load), and ApplicationCapabilityMapping is not
                # TenantMixin (its organization_id is NULL in prod), so it gets
                # none of the automatic do_orm_execute filtering. Scope through
                # the TenantMixin FK parent BusinessCapability instead, mirroring
                # the honest ACM sites in rationalization_scoring_service.py.
                mappings = (
                    ApplicationCapabilityMapping.query
                    .join(
                        BusinessCapability,
                        BusinessCapability.id == ApplicationCapabilityMapping.business_capability_id,
                    )
                    .filter(
                        ApplicationCapabilityMapping.business_capability_id == cap_id,
                        BusinessCapability.organization_id == getattr(g, "current_org_id", None),
                    )
                    .order_by(ApplicationCapabilityMapping.coverage_percentage.desc())
                    .limit(5)
                    .all()
                )
                app_ids = [m.application_component_id for m in mappings]
                apps_by_id = {}
                if app_ids:
                    apps = ApplicationComponent.query.filter(
                        ApplicationComponent.id.in_(app_ids)
                    ).all()
                    apps_by_id = {a.id: a.name for a in apps}

                result["coverage_gaps"] = [{
                    "application_name": apps_by_id.get(m.application_component_id, f"App {m.application_component_id}"),
                    "coverage_percentage": m.coverage_percentage or 0,
                    "support_level": m.support_level or "",
                    "gap_status": m.gap_status or "",
                    "replacement_priority": m.replacement_priority or "",
                    "gap_severity": m.gap_severity or "",
                    "replacement_approach": m.replacement_approach or "",
                    "technical_debt_score": m.technical_debt_score,
                } for m in mappings]
            except Exception:
                result["coverage_gaps"] = []
        else:
            result["coverage_gaps"] = []

        enriched.append(result)

    return enriched


def _generate_architecture_background(app_ctx, solution_id, capabilities, problem_summary, compliance_constraints):
    """Run architecture generation in a background thread.

    Stores progress and result in solution.notes_json["_arch_gen"] so all
    gunicorn workers can read the status via the polling endpoint.
    """
    import threading
    from sqlalchemy.orm.attributes import flag_modified

    def _set_status(status, result=None, error=None):
        """Write job status to journey_state["_arch_gen"] — callable from background thread."""
        try:
            sol = Solution.query.get(solution_id)
            if not sol:
                return
            state = sol.journey_state if isinstance(sol.journey_state, dict) else {}
            state["_arch_gen"] = {"status": status, "result": result, "error": error}
            sol.journey_state = state
            flag_modified(sol, "journey_state")
            db.session.commit()
        except Exception as exc:
            logger.error("_set_status failed for solution %d: %s", solution_id, exc)

    def _run():
        with app_ctx:
            _set_status("running")
            try:
                from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
                orch = JourneyOrchestrator(solution_id)
                result = orch.generate_architecture(
                    accepted_capabilities=capabilities,
                    problem_summary=problem_summary,
                    compliance_constraints=compliance_constraints,
                )
                # Guard: if the LLM produced zero elements across all layers, mark as failed
                # rather than "done" — a "done" with 0 elements is a silent failure that
                # leaves the user with an empty architecture and no indication of failure.
                _total_generated = sum(
                    len(v) for v in result.get("elements_by_layer", {}).values()
                )
                if result.get("error") or _total_generated == 0:
                    _err = result.get("error") or "LLM returned 0 elements across all layers"
                    logger.error(
                        "Architecture generation produced no elements for solution %d: %s",
                        solution_id, _err,
                    )
                    _set_status("failed", error=_err, result=result)
                else:
                    _advance_journey_state(solution_id, "gap_analysis")
                    _set_status("done", result=result)
                    logger.info("Background architecture generation complete for solution %d", solution_id)
                    # Kick off semantic critique in the same thread (non-blocking — fails silently)
                    _run_semantic_critique(solution_id, result)
            except Exception as e:
                logger.error("Background architecture generation failed for solution %d: %s", solution_id, e, exc_info=True)
                _set_status("failed", error=str(e))

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def _read_arch_gen_status(solution_id):
    """Read architecture generation job status from journey_state."""
    sol = Solution.query.get(solution_id)
    if not sol or not isinstance(sol.journey_state, dict):
        return None
    return sol.journey_state.get("_arch_gen")


def _run_semantic_critique(solution_id, generation_result):
    """Run LLM self-critique on generated elements after generation completes.

    Called inline within the generation background thread (already inside app context).
    Stores results in solution.journey_state["_arch_critique"].
    Fails silently — critique is advisory, never blocks the architect.
    """
    import json as _json
    import re as _re
    from sqlalchemy.orm.attributes import flag_modified

    def _set_critique(status, flags=None, error=None):
        try:
            sol = Solution.query.get(solution_id)
            if not sol:
                return
            state = sol.journey_state if isinstance(sol.journey_state, dict) else {}
            state["_arch_critique"] = {"status": status, "flags": flags or [], "error": error}
            sol.journey_state = state
            flag_modified(sol, "journey_state")
            db.session.commit()
        except Exception as exc:
            logger.error("_set_critique failed for solution %d: %s", solution_id, exc)

    try:
        from app.modules.ai_chat.services.llm_service import LLMService

        _set_critique("running")

        # Build compact element list — name + type only (no descriptions to save tokens)
        elements_by_layer = (generation_result or {}).get("elements_by_layer", {})
        elements = []
        layer_priority = ["motivation", "business", "application", "technology", "strategy", "implementation"]
        for lp in layer_priority:
            for el in (elements_by_layer.get(lp) or []):
                name = el.get("name", "")
                el_type = el.get("type", "")
                if name and el_type:
                    elements.append({"name": name, "type": el_type, "layer": lp})
        # Append any remaining layers
        for layer, layer_els in elements_by_layer.items():
            if layer not in layer_priority:
                for el in (layer_els or []):
                    name = el.get("name", "")
                    el_type = el.get("type", "")
                    if name and el_type:
                        elements.append({"name": name, "type": el_type, "layer": layer})

        if len(elements) < 3:
            _set_critique("done", flags=[])
            return

        # Cap at 80 elements to keep cost low
        elements = elements[:80]
        elements_text = "\n".join(
            f"- {e['name']} [{e['type']}] (layer: {e['layer']})"
            for e in elements
        )

        prompt = (
            "You are an ArchiMate 3.2 reviewer. The following elements were generated for a solution architecture.\n"
            "Identify elements where the NAME does not semantically match the ArchiMate TYPE.\n\n"
            "Key rules:\n"
            "- Capability: high-level enterprise ability (e.g. 'Customer Management Capability')\n"
            "- BusinessProcess: an enterprise process (e.g. 'Process Customer Order')\n"
            "- ApplicationComponent: a software application (e.g. 'CRM System', 'Payment Gateway')\n"
            "- ApplicationInterface: an API or user-facing interface (e.g. 'REST API', 'Customer Portal')\n"
            "- TechnologyService: infrastructure service (e.g. 'Database Service', 'Auth Service')\n"
            "- Goal: aspirational outcome (e.g. 'Enable Seamless Customer Experience')\n"
            "- Requirement: a 'shall' statement (e.g. 'The system shall process payments')\n"
            "- BusinessFunction: an ongoing enterprise function (e.g. 'Financial Management')\n"
            "- BusinessActor: a person, org, or system actor (e.g. 'Customer', 'Finance Department')\n"
            "- BusinessRole: a functional role (e.g. 'Account Manager')\n\n"
            f"Generated elements:\n{elements_text}\n\n"
            "Return ONLY a JSON array of flags for elements with clear semantic mismatches.\n"
            'Each flag: {"name": "...", "type": "...", "suggested_type": "...", "reason": "..."}\n'
            "Omit correctly-typed elements. If all are correct, return [].\n"
            "JSON array only, no prose:"
        )

        provider, model = LLMService._get_configured_provider()
        raw_text, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)

        flags = []
        json_match = _re.search(r'\[.*\]', raw_text, _re.DOTALL)
        if json_match:
            try:
                parsed = _json.loads(json_match.group())
                if isinstance(parsed, list):
                    flags = [
                        {
                            "name": str(f.get("name", "")),
                            "type": str(f.get("type", "")),
                            "suggested_type": str(f.get("suggested_type", "")),
                            "reason": str(f.get("reason", "")),
                        }
                        for f in parsed
                        if isinstance(f, dict) and f.get("name") and f.get("suggested_type")
                    ]
            except (_json.JSONDecodeError, TypeError):
                logger.warning("LLM critique returned unparseable JSON for solution %d", solution_id)

        _set_critique("done", flags=flags)
        logger.info("Semantic critique complete for solution %d: %d flags", solution_id, len(flags))

    except Exception as exc:
        logger.error("Semantic critique failed for solution %d: %s", solution_id, exc, exc_info=True)
        _set_critique("done", flags=[])  # Non-blocking: fail silently with empty flags


@journey_v2_bp.route("/<int:solution_id>/generation-readiness", methods=["GET"])
@login_required
@_require_solution_owner
def generation_readiness(solution_id):
    """Pre-generation readiness check for Step 7.

    Returns element counts, layer breakdown, estimated file output, and any
    warnings so the user knows exactly what they're generating before clicking
    the button. Runs the journey bridge if no application elements exist yet
    so the count is always accurate.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_models import SolutionArchiMateElement as _SAE
    from app.models.solution_capability import SolutionCapability

    try:
        # Run bridge so counts reflect what codegen will actually see
        from app.modules.codegen.routes._helpers import _ensure_archimate_elements_from_journey
        _ensure_archimate_elements_from_journey(solution_id)
    except Exception as _be:
        logger.warning("generation_readiness bridge failed for %d: %s", solution_id, _be)

    try:
        links = _SAE.query.filter_by(solution_id=solution_id).all()
        element_ids = [item.element_id for item in links if item.element_id]

        layer_counts = {}
        app_element_names = []
        if element_ids:
            elements = ArchiMateElement.query.filter(
                ArchiMateElement.id.in_(element_ids)
            ).all()
            for e in elements:
                layer = (e.layer or "unknown").lower()
                layer_counts[layer] = layer_counts.get(layer, 0) + 1
                if e.type in {"ApplicationComponent", "ApplicationService",
                               "DataObject", "BusinessObject", "BusinessService"}:
                    app_element_names.append(e.name)

        total_elements = len(element_ids)
        app_elements = layer_counts.get("application", 0) + layer_counts.get("business", 0)

        # File estimate: ~8-12 files per application-layer entity
        estimated_files = max(app_elements * 9, 3) if app_elements else 3

        warnings = []
        arch_gen_status = None
        sol = Solution.query.get(solution_id)
        if sol and isinstance(sol.journey_state, dict):
            arch_gen = sol.journey_state.get("_arch_gen", {})
            arch_gen_status = arch_gen.get("status") if arch_gen else None

        if arch_gen_status == "failed":
            warnings.append({
                "level": "warning",
                "code": "arch_gen_failed",
                "message": "Architecture generation (Step 3) failed — using capabilities from Step 2 instead. "
                           "Code will be generated from your capability names without LLM enrichment.",
            })
        elif arch_gen_status is None and total_elements > 0:
            cap_count = SolutionCapability.query.filter_by(solution_id=solution_id).count()
            if cap_count > 0 and app_elements == cap_count:
                warnings.append({
                    "level": "info",
                    "code": "journey_derived",
                    "message": f"Architecture was derived from {cap_count} capabilities. "
                               "Fields are inferred from domain patterns — you can refine them in the Code Workbench.",
                })

        if total_elements == 0:
            warnings.append({
                "level": "error",
                "code": "no_elements",
                "message": "No architecture elements found. Complete Step 2 (Capabilities) to enable code generation.",
            })

        return api_success(data={
            "ready": total_elements > 0,
            "total_elements": total_elements,
            "app_elements": app_elements,
            "layer_counts": layer_counts,
            "app_element_names": app_element_names[:10],
            "estimated_files": estimated_files,
            "arch_gen_status": arch_gen_status,
            "warnings": warnings,
        })
    except Exception as e:
        logger.error("generation_readiness failed for solution %d: %s", solution_id, e, exc_info=True)
        return api_error("Failed to check generation readiness", 500)


@journey_v2_bp.route("/<int:solution_id>/generate-architecture", methods=["POST"])
@login_required
@_require_solution_owner
def generate_architecture(solution_id):
    """Start async architecture generation. Returns immediately with status=running.

    The LLM call can take 3-8 minutes. Poll GET /generate-architecture/status for progress.
    If a job is already running, returns its current status without starting a new one.
    """
    try:
        from flask import current_app
        data = request.get_json() or {}

        # Idempotency: don't start a second job if one is already running
        current = _read_arch_gen_status(solution_id)
        if current and current.get("status") == "running":
            return api_success(data={"status": "running", "message": "Architecture generation already in progress"})

        problem_summary, capabilities, compliance_constraints = _build_arch_gen_args(data, solution_id)

        _generate_architecture_background(
            current_app._get_current_object().app_context(),
            solution_id,
            capabilities,
            problem_summary,
            compliance_constraints,
        )

        return api_success(data={"status": "running", "message": "Architecture generation started"})
    except Exception as e:
        logger.error("Failed to start architecture generation: %s", e, exc_info=True)
        return api_error("Failed to start architecture generation", 500)


@journey_v2_bp.route("/<int:solution_id>/generate-architecture/status", methods=["GET"])
@login_required
@_require_solution_owner
def generate_architecture_status(solution_id):
    """Poll architecture generation job status.

    Returns:
      {status: "running"}              — still generating
      {status: "done", data: <result>} — complete, includes elements_by_layer etc.
      {status: "failed", error: "..."}  — generation failed
      {status: "idle"}                 — no job started yet
    """
    try:
        job = _read_arch_gen_status(solution_id)
        if not job:
            return api_success(data={"status": "idle"})
        if job["status"] == "done":
            return api_success(data={"status": "done", "result": job.get("result")})
        if job["status"] == "failed":
            return api_success(data={"status": "failed", "error": job.get("error", "Unknown error")})
        return api_success(data={"status": "running"})
    except Exception as e:
        logger.error("Status check failed for solution %d: %s", solution_id, e)
        return api_error("Status check failed", 500)


@journey_v2_bp.route("/<int:solution_id>/generate-architecture/critique", methods=["GET"])
@login_required
@_require_solution_owner
def generate_architecture_critique(solution_id):
    """Poll semantic self-critique status.

    Returns:
      {status: "idle"}                   — generation not yet started or critique not triggered
      {status: "running"}                — critique in progress (runs after generation done)
      {status: "done", flags: [...]}     — complete; flags is [] when all elements are correct
    """
    try:
        sol = Solution.query.get(solution_id)
        if not sol or not isinstance(sol.journey_state, dict):
            return api_success(data={"status": "idle", "flags": []})
        critique = sol.journey_state.get("_arch_critique")
        if not critique:
            return api_success(data={"status": "idle", "flags": []})
        return api_success(data={
            "status": critique.get("status", "idle"),
            "flags": critique.get("flags", []),
        })
    except Exception as e:
        logger.error("Critique status check failed for solution %d: %s", solution_id, e)
        return api_error("Critique status check failed", 500)


# ── Step 3B: Regenerate thin layer ─────────────────────────────────

@journey_v2_bp.route("/<int:solution_id>/regenerate-layer", methods=["POST"])
@login_required
@_require_solution_owner
def regenerate_layer(solution_id):
    """Regenerate a single thin ArchiMate layer without full pipeline re-run."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        from app.models.solution_models import Solution

        data = request.get_json() or {}
        layer = data.get("layer")
        if not layer:
            return api_error("layer is required (e.g., 'strategy', 'implementation')", 400)

        problem_summary = data.get("problem_summary", "")
        if not problem_summary:
            sol = Solution.query.get(solution_id)
            problem_summary = (sol.description or sol.name or "") if sol else ""

        capabilities = data.get("capabilities", [])
        if not capabilities:
            # Fall back to journey_state capabilities
            sol = Solution.query.get(solution_id)
            if sol and sol.journey_state:
                state = sol.journey_state if isinstance(sol.journey_state, dict) else {}
                capabilities = state.get("acceptedCapabilities", [])

        orch = JourneyOrchestrator(solution_id)
        result = orch.regenerate_layer(layer, problem_summary, capabilities)

        if "error" in result:
            return api_error(result["error"], 400)
        return api_success(data=result)
    except Exception as e:
        logger.error("Layer regeneration failed: %s", e, exc_info=True)
        return api_error("Failed to regenerate layer", 500)


# ── Step 6: Validation ───────────────────────────────────────────

@journey_v2_bp.route("/<int:solution_id>/validate", methods=["GET"])
@login_required
@_require_solution_owner
def validate(solution_id):
    """Run validation pass on the architecture graph."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        orch = JourneyOrchestrator(solution_id)
        result = orch.validate()
        return api_success(data=result)
    except Exception as e:
        logger.error("Validation failed: %s", e, exc_info=True)
        return api_error("Failed to validate architecture", 500)


# ── Step 5: Migration Planning ────────────────────────────────────

@journey_v2_bp.route("/<int:solution_id>/generate-migration", methods=["POST"])
@login_required
@_require_solution_owner
def generate_migration(solution_id):
    """Generate phased migration plan."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        data = request.get_json() or {}

        orch = JourneyOrchestrator(solution_id)
        result = orch.generate_migration_plan(
            architecture_elements=data.get("architecture_elements", {}),
            problem_summary=data.get("problem_summary", ""),
            constraints=data.get("constraints"),
        )
        return api_success(data=result)
    except Exception as e:
        logger.error("Migration planning failed: %s", e, exc_info=True)
        return api_error("Failed to generate migration plan", 500)


# ── Step 6: Full Validation + ARB ─────────────────────────────────

@journey_v2_bp.route("/<int:solution_id>/full-validate", methods=["POST"])
@login_required
@_require_solution_owner
def full_validate(solution_id):
    """Run comprehensive validation with compliance, governance, traceability."""
    try:
        from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator
        data = request.get_json() or {}

        orch = JourneyOrchestrator(solution_id)
        result = orch.get_full_validation(
            architecture_elements=data.get("architecture_elements", {}),
            capabilities=data.get("capabilities", []),
            migration_plan=data.get("migration_plan"),
        )
        return api_success(data=result)
    except Exception as e:
        logger.error("Full validation failed: %s", e, exc_info=True)
        return api_error("Failed to run validation", 500)


@journey_v2_bp.route("/<int:solution_id>/submit-arb", methods=["POST"])
@login_required
@_require_solution_owner
def submit_arb(solution_id):
    """Submit through the canonical, evidence-gated ARB service."""
    from app.modules.transformation_room.arb_submission_adapter import (
        TypedARBSubmissionAdapter,
    )

    data = request.get_json() or {}
    result = TypedARBSubmissionAdapter.submit_solution_from_request(
        solution_id=solution_id,
        payload=data,
    )
    if not result.success:
        return jsonify({"success": False, "reason_codes": result.reason_codes,
                        "missing_evidence": result.missing_evidence}), result.http_status
    return api_success(data={"review_item_id": result.review_item_id,
                             "review_number": result.review_number,
                             "snapshot_id": result.snapshot_id,
                             "idempotent": result.idempotent,
                             "review_cycle_id": result.review_cycle_id,
                             "canonical_url": result.canonical_url})


# ── Spec Inference Hooks (post-generation triggers) ──────────────

@journey_v2_bp.route("/<int:solution_id>/infer-component-specs", methods=["POST"])
@login_required
@_require_solution_owner
def infer_component_specs(solution_id):
    """Auto-trigger field inference for ApplicationComponent elements, batched to avoid timeout.

    Accepts optional JSON body: {"batch_size": 3}
    Processes up to batch_size elements that don't yet have spec_data.
    Returns remaining count so the frontend can call again.
    """
    from app.models.solution_archimate_element import SolutionArchiMateElement
    from app.models.archimate_core import ArchiMateElement
    from app.modules.solutions_strategic.v2.services.component_spec_service import ComponentSpecService
    from app.models.solution_sad_models import SolutionIntegrationFlow, SolutionSLA

    body = request.get_json(silent=True) or {}
    try:
        batch_size = min(int(body.get("batch_size", 3)), 10)
    except (ValueError, TypeError):
        return api_error("batch_size must be an integer", 400)

    # ── Load junctions; empty set is a valid state (no elements linked yet) ──
    try:
        junctions = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
    except Exception as e:
        logger.error("Failed to query SolutionArchiMateElement for solution %s: %s", solution_id, e, exc_info=True)
        return api_error("Failed to query architecture elements", 500)

    if not junctions:
        logger.info("infer-component-specs: no ArchiMate elements for solution %s — returning empty", solution_id)
        return api_success(data={
            "results": {},
            "processed": 0,
            "remaining": 0,
            "total": 0,
        })

    # ── Check LLM availability before processing ────────────────────────
    try:
        from app.modules.ai_chat.services.llm_service import LLMService
        LLMService._get_configured_provider()
    except Exception as e:
        logger.warning("LLM not configured for infer-component-specs: %s", e)
        return api_error("LLM not configured", 503)

    # ── Load supporting data (flows/slas are optional context) ─────────
    from app.modules.solutions_strategic.v2.services.code_spec_inference import infer_code_spec
    svc = ComponentSpecService()
    flows = []
    slas = []
    try:
        flows = SolutionIntegrationFlow.query.filter_by(solution_id=solution_id).all()
    except Exception as e:
        db.session.rollback()
        logger.warning("Failed to load integration flows for solution %s: %s", solution_id, e)
    try:
        slas = SolutionSLA.query.filter_by(solution_id=solution_id).all()
    except Exception as e:
        db.session.rollback()
        logger.warning("Failed to load SLAs for solution %s: %s", solution_id, e)

    requirements = []
    try:
        from app.models.solution_architect_models import SolutionRequirement
        requirements = SolutionRequirement.query.filter_by(
            solution_id=solution_id
        ).filter(SolutionRequirement.deleted_at.is_(None)).all()
    except Exception as e:
        db.session.rollback()
        logger.warning("Failed to load requirements for solution %s: %s", solution_id, e)

    # ── Separate into pending (needs inference) vs already done ──────────
    pending = []
    results = {}
    for j in junctions:
        element = ArchiMateElement.query.get(j.element_id)
        if not element or element.type not in ("ApplicationComponent", "ApplicationService", "DataObject", "BusinessObject"):
            continue
        if j.spec_data and j.spec_data.get("fields"):
            results[j.element_id] = {"status": "already_has_spec"}
            continue
        pending.append((j, element))

    # ── Process only batch_size elements ─────────────────────────────────
    processed = 0
    for j, element in pending[:batch_size]:
        elem_proxy = type("ElemProxy", (), {
            "name": element.name,
            "element_type": element.type,
            "description": element.description,
            "technology": getattr(element, "technology", None),
        })()

        try:
            result = infer_code_spec(elem_proxy, requirements, flows, slas, solution_id)
        except Exception as e:
            logger.error("infer_code_spec failed for element %s (junction %s): %s", element.name, j.id, e, exc_info=True)
            results[j.element_id] = {"status": "failed", "error": str(e)}
            processed += 1
            continue

        if result and result.get("fields"):
            try:
                svc.save_fields(j.id, result["fields"], user_id=current_user.id, generated_by="llm", model_used=result.get("model"))
                results[j.element_id] = {"status": "proposed", "field_count": len(result["fields"])}
            except Exception as e:
                logger.error("save_fields failed for junction %s: %s", j.id, e, exc_info=True)
                results[j.element_id] = {"status": "failed", "error": "Failed to save inferred fields"}
        else:
            results[j.element_id] = {"status": "failed", "error": "LLM returned no fields"}
        processed += 1

    remaining = len(pending) - processed
    return api_success(data={
        "results": results,
        "processed": processed,
        "remaining": remaining,
        "total": len(junctions),
    })


@journey_v2_bp.route("/<int:solution_id>/suggest-integration-contracts", methods=["POST"])
@login_required
@_require_solution_owner
def suggest_integration_contracts(solution_id):
    """Auto-suggest integration contracts from ArchiMate relationships after variant selection."""
    from app.models.solution_archimate_element import SolutionArchiMateElement
    from app.models.archimate_core import ArchiMateRelationship
    from app.modules.solutions_strategic.v2.services.integration_contract_service import IntegrationContractService

    junctions = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
    element_ids = {j.element_id for j in junctions}
    junction_by_elem = {j.element_id: j for j in junctions}

    rels = ArchiMateRelationship.query.filter(
        ArchiMateRelationship.source_id.in_(element_ids),
        ArchiMateRelationship.target_id.in_(element_ids),
    ).all()

    svc = IntegrationContractService()
    results = {}
    for rel in rels:
        source_j = junction_by_elem.get(rel.source_id)
        if not source_j:
            continue

        suggestion = svc.suggest_from_relationship(source_j.id, rel)
        svc.save_contract(
            source_j.id,
            str(rel.target_id),
            suggestion,
            user_id=current_user.id,
            generated_by="llm",
        )
        results["{0}->{1}".format(rel.source_id, rel.target_id)] = {"status": "proposed"}

    return api_success(data={"results": results})


@journey_v2_bp.route("/<int:solution_id>/suggest-deployment-specs", methods=["POST"])
@login_required
@_require_solution_owner
def suggest_deployment_specs(solution_id):
    """Auto-suggest deployment specs from Node/TechnologyService elements after migration generation."""
    from app.models.solution_archimate_element import SolutionArchiMateElement
    from app.models.archimate_core import ArchiMateElement
    from app.modules.solutions_strategic.v2.services.deployment_spec_service import DeploymentSpecService

    junctions = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
    svc = DeploymentSpecService()
    results = {}
    for j in junctions:
        element = ArchiMateElement.query.get(j.element_id)
        if not element or element.type not in ("Node", "Device", "SystemSoftware", "TechnologyService"):
            continue

        if j.spec_data and j.spec_data.get("deployment_status") == "confirmed":
            results[j.element_id] = {"status": "already_confirmed"}
            continue

        suggestion = svc.suggest_from_element(element)
        svc.save_deployment(j.id, suggestion, user_id=current_user.id, generated_by="llm")
        results[j.element_id] = {"status": "proposed"}

    return api_success(data={"results": results})


# ── Zero-to-Hero Reasoning Pipeline ──────────────────────────────
# Spec: docs/2026-03-22-zero-to-hero-journey-spec-v2.md
# Service: journey_reasoning_orchestrator.py


@journey_v2_bp.route("/<int:solution_id>/reasoning/discover-capabilities", methods=["POST"])
@login_required
@_require_solution_owner
def reasoning_discover_capabilities(solution_id):
    """CONNECT-1: Discover capabilities from problem text."""
    try:
        from app.modules.solutions_strategic.v2.services.journey_reasoning_orchestrator import (
            JourneyReasoningOrchestrator,
        )
        data = request.get_json() or {}
        problem_text = data.get("problem_text", "")
        if not problem_text:
            return api_error("problem_text is required", 400)

        orch = JourneyReasoningOrchestrator(solution_id)
        capabilities = orch.discover_capabilities(problem_text)
        return api_success(data={
            "capabilities": [
                {
                    "capability_id": c.capability_id,
                    "name": c.name,
                    "description": c.description,
                    "level": c.level,
                    "strategic_importance": c.strategic_importance,
                    "confidence": c.confidence,
                    "rationale": c.rationale,
                    "acm_domain": c.acm_domain,
                }
                for c in capabilities
            ],
            "count": len(capabilities),
        })
    except ValueError as e:
        return api_error(str(e), 409)
    except Exception as e:
        logger.error("Capability discovery failed for solution %d: %s", solution_id, e, exc_info=True)
        return api_error(f"Capability discovery failed: {e}", 500)


@journey_v2_bp.route("/<int:solution_id>/reasoning/confirm-capabilities", methods=["POST"])
@login_required
@_require_solution_owner
def reasoning_confirm_capabilities(solution_id):
    """CONNECT-1 completion: Confirm/reject discovered capabilities."""
    try:
        from app.modules.solutions_strategic.v2.services.journey_reasoning_orchestrator import (
            JourneyReasoningOrchestrator,
        )
        data = request.get_json() or {}
        confirmed_ids = data.get("confirmed_ids", [])
        rejected_ids = data.get("rejected_ids", [])

        if not confirmed_ids:
            return api_error("confirmed_ids is required (at least 1 capability)", 400)

        orch = JourneyReasoningOrchestrator(solution_id)
        confirmed = orch.confirm_capabilities(confirmed_ids, rejected_ids)

        # PLT-040 / Gap-6: Persist confirmed capabilities as ArchiMate Strategy layer elements
        persisted_cap_count = 0
        try:
            from app.models.archimate_core import ArchiMateElement, ArchitectureModel
            from app.models.solution_archimate_element import SolutionArchiMateElement

            arch_model = ArchitectureModel.query.filter_by(solution_id=solution_id).first()
            if not arch_model:
                arch_model = ArchitectureModel(
                    name=f"Journey Architecture (Solution {solution_id})",
                    version="1.0",
                    solution_id=solution_id,
                )
                db.session.add(arch_model)
                db.session.flush()

            for cap in confirmed:
                cap_name = cap.name
                # Check if Capability element already exists
                existing = ArchiMateElement.query.filter_by(
                    name=cap_name, type="Capability"
                ).first()
                if not existing:
                    element = ArchiMateElement(
                        name=cap_name,
                        type="Capability",
                        layer="strategy",
                        description=getattr(cap, "description", "") or f"Business capability: {cap_name}",
                        scope="application",
                        acm_domain=getattr(cap, "acm_domain", "APP") or "APP",
                        acm_source="journey",
                        architecture_id=arch_model.id,
                    )
                    db.session.add(element)
                    db.session.flush()
                else:
                    element = existing

                # Create junction if not exists
                existing_junction = SolutionArchiMateElement.query.filter_by(
                    solution_id=solution_id, element_id=element.id,
                ).first()
                if not existing_junction:
                    db.session.add(SolutionArchiMateElement(
                        solution_id=solution_id,
                        element_id=element.id,
                        layer_type="strategy",
                        element_table="archimate_elements",
                        element_name=cap_name,
                        element_role="primary",
                        is_new_element=(existing is None),
                        spec_data={"fields_status": "confirmed"},
                    ))
                persisted_cap_count += 1

            db.session.commit()
            logger.info("Persisted %d capabilities as Strategy layer elements for solution %d",
                        persisted_cap_count, solution_id)
        except Exception as cap_err:
            db.session.rollback()
            logger.warning("Capability persistence to ArchiMate failed (non-fatal): %s", cap_err)

        acm_coverage = orch.compute_acm_coverage([
            {"name": c.name, "description": c.description, "acm_domain": c.acm_domain}
            for c in confirmed
        ])
        return api_success(data={
            "confirmed": [
                {"capability_id": c.capability_id, "name": c.name, "acm_domain": c.acm_domain}
                for c in confirmed
            ],
            "count": len(confirmed),
            "archimate_persisted": persisted_cap_count,
            "acm_coverage": acm_coverage,
        })
    except ValueError as e:
        return api_error(str(e), 409)
    except Exception as e:
        logger.error("Capability confirmation failed for solution %d: %s", solution_id, e, exc_info=True)
        return api_error(f"Capability confirmation failed: {e}", 500)


@journey_v2_bp.route("/<int:solution_id>/reasoning/map-landscape", methods=["POST"])
@login_required
@_require_solution_owner
def reasoning_map_landscape(solution_id):
    """CONNECT-2: Map application landscape for confirmed capabilities."""
    try:
        from app.modules.solutions_strategic.v2.services.journey_reasoning_orchestrator import (
            JourneyReasoningOrchestrator,
        )
        orch = JourneyReasoningOrchestrator(solution_id)
        landscape = orch.map_landscape()

        # Include landscape_message from journey data when landscape is empty
        response_data = {
            "applications": [
                {
                    "app_id": a.app_id,
                    "app_name": a.app_name,
                    "capability_name": a.capability_name,
                    "lifecycle_status": a.lifecycle_status,
                    "evidence_level": a.evidence_level,
                    "is_decommissioning": a.is_decommissioning,
                    "vendor_product_name": a.vendor_product_name,
                    "vendor_name": a.vendor_name,
                    "annual_license_cost": a.annual_license_cost,
                    "implementation_cost": a.implementation_cost,
                }
                for a in landscape
            ],
            "count": len(landscape),
            "decommissioning_count": sum(1 for a in landscape if a.is_decommissioning),
            "costed_count": sum(1 for a in landscape if a.annual_license_cost is not None),
        }

        if not landscape:
            journey_data = orch._get_journey_data()
            response_data["message"] = journey_data.get(
                "landscape_message",
                "No applications found for the confirmed capabilities.",
            )

        return api_success(data=response_data)
    except ValueError as e:
        return api_error(str(e), 409)
    except Exception as e:
        logger.error("Landscape mapping failed for solution %d: %s", solution_id, e, exc_info=True)
        return api_error(f"Landscape mapping encountered an error: {e}", 500)


@journey_v2_bp.route("/<int:solution_id>/reasoning/estimate-costs", methods=["POST"])
@login_required
@_require_solution_owner
def reasoning_estimate_costs(solution_id):
    """COST: Estimate costs from vendor products in the landscape."""
    try:
        from app.modules.solutions_strategic.v2.services.journey_reasoning_orchestrator import (
            JourneyReasoningOrchestrator,
        )
        orch = JourneyReasoningOrchestrator(solution_id)
        cost_summary = orch.estimate_costs()
        return api_success(data=cost_summary)
    except ValueError as e:
        return api_error(str(e), 409)
    except Exception as e:
        logger.error("Cost estimation failed for solution %d: %s", solution_id, e, exc_info=True)
        return api_error(f"Cost estimation failed: {e}", 500)


@journey_v2_bp.route("/<int:solution_id>/reasoning/run-inference", methods=["POST"])
@login_required
@_require_solution_owner
def reasoning_run_inference(solution_id):
    """CONNECT-3: Run inference engine on solution's element set."""
    try:
        from app.modules.solutions_strategic.v2.services.journey_reasoning_orchestrator import (
            JourneyReasoningOrchestrator,
        )
        orch = JourneyReasoningOrchestrator(solution_id)
        result = orch.run_inference()
        return api_success(data=result)
    except Exception as e:
        logger.error("Inference failed for solution %d: %s", solution_id, e, exc_info=True)
        return api_error(f"Inference engine failed: {e}", 500)


@journey_v2_bp.route("/<int:solution_id>/reasoning/detect-gaps", methods=["POST"])
@login_required
@_require_solution_owner
def reasoning_detect_gaps(solution_id):
    """REASON-2: Detect architectural gaps."""
    try:
        from app.modules.solutions_strategic.v2.services.journey_reasoning_orchestrator import (
            JourneyReasoningOrchestrator,
        )
        orch = JourneyReasoningOrchestrator(solution_id)
        gaps = orch.detect_gaps()
        return api_success(data={
            "gaps": [
                {
                    "gap_id": g.gap_id,
                    "gap_type": g.gap_type,
                    "entity_name": g.entity_name,
                    "severity": g.severity,
                    "rationale": g.rationale,
                    "recommended_mitigation": g.recommended_mitigation,
                }
                for g in gaps
            ],
            "count": len(gaps),
            "critical_count": sum(1 for g in gaps if g.severity == "critical"),
            "high_count": sum(1 for g in gaps if g.severity == "high"),
        })
    except ValueError as e:
        return api_error(str(e), 409)
    except Exception as e:
        logger.error("Gap detection failed for solution %d: %s", solution_id, e, exc_info=True)
        return api_error(f"Gap detection failed: {e}", 500)


@journey_v2_bp.route("/<int:solution_id>/reasoning/generate-options", methods=["POST"])
@login_required
@_require_solution_owner
def reasoning_generate_options(solution_id):
    """REASON-3: Generate solution options (buy/build/hybrid)."""
    try:
        from app.modules.solutions_strategic.v2.services.journey_reasoning_orchestrator import (
            JourneyReasoningOrchestrator,
        )
        data = request.get_json() or {}
        constraints = data.get("constraints", {})

        orch = JourneyReasoningOrchestrator(solution_id)
        options = orch.generate_options(constraints)
        return api_success(data={
            "options": [
                {
                    "option_id": o.option_id,
                    "option_type": o.option_type,
                    "title": o.title,
                    "description": o.description,
                    "cost_estimate": o.cost_estimate,
                    "risk_score": o.risk_score,
                    "time_estimate": o.time_estimate,
                }
                for o in options
            ],
            "count": len(options),
        })
    except ValueError as e:
        return api_error(str(e), 409)
    except Exception as e:
        logger.error("Option generation failed for solution %d: %s", solution_id, e, exc_info=True)
        return api_error(f"Option generation failed: {e}", 500)


@journey_v2_bp.route("/<int:solution_id>/reasoning/select-recommendation", methods=["POST"])
@login_required
@_require_solution_owner
def reasoning_select_recommendation(solution_id):
    """REASON-4: Select preferred option."""
    try:
        from app.modules.solutions_strategic.v2.services.journey_reasoning_orchestrator import (
            JourneyReasoningOrchestrator,
        )
        data = request.get_json() or {}
        option_id = data.get("option_id")
        if not option_id:
            return api_error("option_id is required", 400)

        orch = JourneyReasoningOrchestrator(solution_id)
        recommendation = orch.select_recommendation(option_id)
        return api_success(data=recommendation)
    except ValueError as e:
        return api_error(str(e), 409)
    except Exception as e:
        logger.error("Recommendation selection failed for solution %d: %s", solution_id, e, exc_info=True)
        return api_error(f"Recommendation selection failed: {e}", 500)


@journey_v2_bp.route("/<int:solution_id>/reasoning/populate-blueprint", methods=["POST"])
@login_required
@_require_solution_owner
def reasoning_populate_blueprint(solution_id):
    """PRODUCE-1: Auto-populate blueprint from reasoning output."""
    try:
        from app.modules.solutions_strategic.v2.services.journey_reasoning_orchestrator import (
            JourneyReasoningOrchestrator,
        )
        orch = JourneyReasoningOrchestrator(solution_id)
        result = orch.populate_blueprint()
        return api_success(data={
            **result,
            "redirect": f"/solutions/{solution_id}",
        })
    except ValueError as e:
        return api_error(str(e), 409)
    except Exception as e:
        logger.error("Blueprint population failed for solution %d: %s", solution_id, e, exc_info=True)
        return api_error(f"Blueprint population failed: {e}", 500)


@journey_v2_bp.route("/<int:solution_id>/reasoning/analysis", methods=["GET"])
@login_required
@_require_solution_owner
def reasoning_get_analysis(solution_id):
    """Get full analysis state for display."""
    try:
        from dataclasses import asdict
        from app.modules.solutions_strategic.v2.services.journey_reasoning_orchestrator import (
            JourneyReasoningOrchestrator,
        )
        orch = JourneyReasoningOrchestrator(solution_id)
        analysis = orch.get_analysis()
        return api_success(data=asdict(analysis))
    except Exception as e:
        logger.error("Analysis retrieval failed for solution %d: %s", solution_id, e, exc_info=True)
        return api_error(f"Failed to retrieve analysis: {e}", 500)


# ── Structured Intake (Step 1 data persistence) ─────────────────

_MOSCOW_MAP = {"must": "MUST", "should": "SHOULD", "could": "COULD", "wont": "WONT"}


@journey_v2_bp.route("/<int:solution_id>/structured-intake", methods=["POST"])
@login_required
@_require_solution_owner
def save_structured_intake(solution_id):
    """Save structured problem definition, drivers, constraints, NFRs from Step 1."""
    from app.models.solution_architect_models import (
        ConstraintType,
        DriverType,
        RequirementType,
        SolutionAnalysisSession,
        SolutionConstraint,
        SolutionDriver,
        SolutionProblemDefinition,
        SolutionRequirement,
        SolutionSessionStatus,
    )
    from app.models.solution_models import solution_applications

    solution = Solution.query.get_or_404(solution_id)
    data = request.get_json(silent=True) or {}

    try:
        # 1. Get or create SolutionAnalysisSession
        session = SolutionAnalysisSession.query.filter_by(
            name=f"Journey Intake \u2014 Solution {solution_id}"
        ).first()
        if not session:
            session = SolutionAnalysisSession(
                name=f"Journey Intake \u2014 Solution {solution_id}",
                description="Auto-created by structured intake wizard",
                status=SolutionSessionStatus.IN_PROGRESS,
                created_by_id=current_user.id,
            )
            db.session.add(session)
            db.session.flush()  # get session.id

        # 2. Get or create SolutionProblemDefinition
        problem_def = session.problem_definition
        if not problem_def:
            problem_def = SolutionProblemDefinition(
                session_id=session.id,
                problem_description="; ".join(data.get("pain_points", [])) or "Structured intake",
            )
            db.session.add(problem_def)
            db.session.flush()
        else:
            # Update problem_description if pain_points provided
            pain_points = data.get("pain_points", [])
            if pain_points:
                problem_def.problem_description = "; ".join(pain_points)

        # 3. Update SolutionProblemDefinition fields
        pain_points = data.get("pain_points", [])
        if pain_points:
            problem_def.business_context = "; ".join(pain_points)

        if data.get("budget_min") is not None:
            problem_def.budget_min = data["budget_min"]
        if data.get("budget_max") is not None:
            problem_def.budget_max = data["budget_max"]
        problem_def.budget_currency = "GBP"

        if data.get("timeline_months") is not None:
            problem_def.timeline_months = data["timeline_months"]
        if data.get("organization_size"):
            problem_def.organization_size = data["organization_size"]
        if data.get("business_domain"):
            problem_def.industry_vertical = data["business_domain"]
        if data.get("geographic_scope"):
            problem_def.geographic_scope = data["geographic_scope"]
        if data.get("user_count") is not None:
            problem_def.user_count = data["user_count"]
        if data.get("transaction_volume") is not None:
            problem_def.transaction_volume = data["transaction_volume"]
        if data.get("data_volume_gb") is not None:
            problem_def.data_volume_gb = data["data_volume_gb"]

        compliance_frameworks = data.get("compliance_frameworks", [])
        if compliance_frameworks:
            problem_def.compliance_requirements = compliance_frameworks

        integration_systems = data.get("integration_systems", [])
        if integration_systems:
            problem_def.integration_requirements = integration_systems

        tech_constraints = data.get("tech_constraints", [])
        must_use = [tc["technology"] for tc in tech_constraints if tc.get("type") == "must_use"]
        if must_use:
            problem_def.existing_technology_stack = must_use

        # 4. Update Solution record
        if data.get("business_domain"):
            solution.business_domain = data["business_domain"]
        in_scope_apps = data.get("in_scope_apps", [])
        if in_scope_apps:
            solution.in_scope_applications = in_scope_apps
        if data.get("budget_max") is not None:
            solution.estimated_cost = data["budget_max"]

        # 5. Create SolutionDriver records
        drivers_data = data.get("drivers", [])
        counts = {"drivers": 0, "constraints": 0, "requirements": 0, "applications": 0}

        # Clear existing AI-generated drivers for this problem, keep user ones
        SolutionDriver.query.filter_by(
            problem_id=problem_def.id, ai_generated=True
        ).delete(synchronize_session="fetch")

        driver_type_map = {
            "technology": DriverType.TECHNOLOGY,
            "stakeholder": DriverType.STAKEHOLDER,
            "external": DriverType.EXTERNAL,
            "internal": DriverType.INTERNAL,
        }
        for d in drivers_data:
            driver = SolutionDriver(
                problem_id=problem_def.id,
                name=d.get("name", "Unknown driver"),
                driver_type=driver_type_map.get(d.get("type", "internal"), DriverType.INTERNAL),
                urgency=d.get("urgency"),
                ai_generated=False,
            )
            db.session.add(driver)
            counts["drivers"] += 1

        # 6. Create SolutionConstraint records
        # Clear existing constraints for this problem first
        SolutionConstraint.query.filter_by(problem_id=problem_def.id).delete(
            synchronize_session="fetch"
        )

        for tc in tech_constraints:
            constraint = SolutionConstraint(
                problem_id=problem_def.id,
                constraint_type=ConstraintType.TECHNICAL,
                name=tc.get("technology", "Unknown"),
                description=f"Technology constraint: {tc.get('type', 'unknown')} \u2014 {tc.get('technology', '')}",
                value=tc.get("type", ""),
                ai_generated=False,
            )
            db.session.add(constraint)
            counts["constraints"] += 1

        # JWIRE-003: Classify tech constraints as TechnologyService / Node / SystemSoftware
        # ArchiMate elements so that deployment_view and network_communication blueprint
        # sections have elements to score against (currently always 0% for J7 solutions).
        _TECH_NODE_KEYWORDS = {
            "kubernetes", "k8s", "ecs", "container", "docker", "pod", "eks", "gke", "aks",
        }
        _TECH_SERVICE_KEYWORDS = {
            "aws", "azure", "gcp", "cloud", "platform", "api", "gateway", "queue", "bus",
            "kafka", "rabbitmq", "sqs", "sns", "pubsub", "event hub", "service mesh",
            "istio", "apigee", "kong",
        }
        _TECH_SOFTWARE_KEYWORDS = {
            "database", " db", "postgres", "mysql", "oracle", "sql server", "sqlserver",
            "mongodb", "redis", "elasticsearch", "cassandra", "dynamodb",
        }
        _TECH_SERVER_KEYWORDS = {
            "server", "host", "machine", "vm", "virtual", "bare metal", "on-prem",
            "on-premise", "datacenter", "data center",
        }

        try:
            from app.modules.solutions_strategic.v2.routes.solution_phase_routes import (
                _sync_archimate_element as _jwire_tech_sync,
            )
            from app.models.solution_models import SolutionArchiMateElement as _SAE_tech

            for tc in tech_constraints:
                tech_name = (tc.get("technology") or "").strip()
                if not tech_name:
                    continue
                name_lower = tech_name.lower()
                if any(kw in name_lower for kw in _TECH_NODE_KEYWORDS):
                    ae_type = "Node"
                elif any(kw in name_lower for kw in _TECH_SOFTWARE_KEYWORDS):
                    ae_type = "SystemSoftware"
                elif any(kw in name_lower for kw in _TECH_SERVER_KEYWORDS):
                    ae_type = "Node"
                elif any(kw in name_lower for kw in _TECH_SERVICE_KEYWORDS):
                    ae_type = "TechnologyService"
                else:
                    continue  # generic constraint — no technology-layer element
                # Idempotent: skip if an element with this name already exists for this solution
                existing = _SAE_tech.query.filter_by(
                    solution_id=solution_id,
                    element_name=tech_name,
                ).first()
                if not existing:
                    _jwire_tech_sync(
                        solution_id,
                        ae_type=ae_type,
                        ae_layer="Technology",
                        name=tech_name,
                        description=f"Technology constraint ({tc.get('type','constraint')}): {tech_name}",
                    )
                    counts["archimate_elements"] = counts.get("archimate_elements", 0) + 1
        except Exception as _jwire3_err:
            logger.warning("JWIRE-003 tech element classification failed: %s", _jwire3_err)

        if data.get("budget_min") is not None or data.get("budget_max") is not None:
            budget_min = data.get("budget_min", 0)
            budget_max = data.get("budget_max", 0)
            constraint = SolutionConstraint(
                problem_id=problem_def.id,
                constraint_type=ConstraintType.BUDGET,
                name="Budget range",
                description=f"Budget constraint: \u00a3{budget_min:,}-\u00a3{budget_max:,}",
                value=f"\u00a3{budget_min}-{budget_max}",
                ai_generated=False,
            )
            db.session.add(constraint)
            counts["constraints"] += 1

        if data.get("timeline_months") is not None:
            constraint = SolutionConstraint(
                problem_id=problem_def.id,
                constraint_type=ConstraintType.TIMELINE,
                name="Timeline",
                description=f"Timeline constraint: {data['timeline_months']} months",
                value=f"{data['timeline_months']} months",
                ai_generated=False,
            )
            db.session.add(constraint)
            counts["constraints"] += 1

        for fw in compliance_frameworks:
            constraint = SolutionConstraint(
                problem_id=problem_def.id,
                constraint_type=ConstraintType.COMPLIANCE,
                name=fw,
                description=f"Compliance requirement: {fw}",
                value=fw,
                ai_generated=False,
            )
            db.session.add(constraint)
            counts["constraints"] += 1

        # 7. Create SolutionRequirement records for NFRs
        nfrs = data.get("nfrs", [])
        # Clear existing NFR requirements linked to this problem
        SolutionRequirement.query.filter_by(
            problem_id=problem_def.id, req_type="non_functional"
        ).delete(synchronize_session="fetch")

        for nfr in nfrs:
            priority_key = (nfr.get("priority") or "").lower()
            moscow = _MOSCOW_MAP.get(priority_key, "SHOULD")
            req_type_enum = (
                RequirementType.CONSTRAINT if moscow == "MUST" else RequirementType.QUALITY
            )
            nfr_ac = nfr.get("acceptance_criteria") or ""
            nfr_comp_ref = (nfr.get("compliance_reference") or "").strip()
            nfr_vm = nfr.get("verification_method") or ""
            requirement = SolutionRequirement(
                problem_id=problem_def.id,
                solution_id=solution_id,
                name=(nfr.get("type") or "NFR").capitalize(),
                description=nfr.get("target", ""),
                requirement_type=req_type_enum,
                moscow_priority=moscow,
                layer="CrossCutting",
                req_type="non_functional",
                ai_generated=False,
                source="intake_nfr",
                acceptance_criteria=nfr_ac or None,
                compliance_tags=[nfr_comp_ref] if nfr_comp_ref else [],
                verification_method=nfr_vm or None,
            )
            db.session.add(requirement)
            counts["requirements"] += 1

        # 8. Link in_scope_apps to solution via junction table
        if in_scope_apps:
            existing_app_ids = set(
                r[0]
                for r in db.session.query(solution_applications.c.application_component_id)
                .filter(solution_applications.c.solution_id == solution_id)
                .all()
            )
            for app_entry in in_scope_apps:
                app_id = app_entry.get("id") if isinstance(app_entry, dict) else app_entry
                if app_id and app_id not in existing_app_ids:
                    db.session.execute(
                        solution_applications.insert().values(  # tenant-filtered: scoped via parent FK (solution_id)
                            solution_id=solution_id,
                            application_component_id=app_id,
                        )
                    )
                    counts["applications"] += 1

        # JWIRE-001+: Create ArchiMate Motivation elements from BA's explicit input.
        # If the BA defined stakeholders/drivers/goals/constraints in the Motivation
        # sub-phase, create real ArchiMate elements. Otherwise fall back to inference.
        try:
            from app.modules.solutions_strategic.v2.routes.solution_phase_routes import (
                _sync_archimate_element as _jwire_sync,
            )
            from app.models.solution_models import SolutionArchiMateElement as _SAE
            from app.models.archimate_core import ArchiMateElement as _AE

            def _has_ae(sol_id, ae_type, name):
                """True if solution already has a linked element of this type+name."""
                return db.session.query(_SAE.id).join(
                    _AE, _SAE.element_id == _AE.id
                ).filter(
                    _SAE.solution_id == sol_id, _AE.type == ae_type, _AE.name == name
                ).first() is not None

            motivation = data.get("motivation", {})
            _ae_count = 0

            # BA-defined Stakeholders → ArchiMate Stakeholder elements
            for s in (motivation.get("stakeholders") or []):
                name = (s.get("name") or "").strip()
                if name and not _has_ae(solution_id, "Stakeholder", name):
                    _jwire_sync(
                        solution_id,
                        ae_type="Stakeholder",
                        ae_layer="Motivation",
                        name=name,
                        description=s.get("description") or f"Stakeholder: {name}",
                    )
                    _ae_count += 1

            # BA-defined Drivers → ArchiMate Driver elements
            for d in (motivation.get("drivers") or []):
                name = (d.get("name") or "").strip()
                if name and not _has_ae(solution_id, "Driver", name):
                    _jwire_sync(
                        solution_id,
                        ae_type="Driver",
                        ae_layer="Motivation",
                        name=name,
                        description=d.get("description") or f"Business driver: {name}",
                    )
                    _ae_count += 1

            # BA-defined Goals → ArchiMate Goal elements
            for g in (motivation.get("goals") or []):
                name = (g.get("name") or "").strip()
                if name and not _has_ae(solution_id, "Goal", name):
                    _jwire_sync(
                        solution_id,
                        ae_type="Goal",
                        ae_layer="Motivation",
                        name=name,
                        description=g.get("description") or f"Goal: {name}",
                    )
                    _ae_count += 1

            # BA-defined Constraints → ArchiMate Constraint elements
            for c in (motivation.get("constraints") or []):
                name = (c.get("name") or "").strip()
                if name and not _has_ae(solution_id, "Constraint", name):
                    _jwire_sync(
                        solution_id,
                        ae_type="Constraint",
                        ae_layer="Motivation",
                        name=name,
                        description=c.get("source") or f"Constraint: {name}",
                    )
                    _ae_count += 1

            # Fallback: if BA didn't define any motivation, infer one Stakeholder + one Goal
            if _ae_count == 0:
                def _has_ae_type(sol_id, ae_type):
                    return db.session.query(_SAE.id).join(
                        _AE, _SAE.element_id == _AE.id
                    ).filter(
                        _SAE.solution_id == sol_id, _AE.type == ae_type
                    ).first() is not None

                if not _has_ae_type(solution_id, "Stakeholder"):
                    org_size = data.get("organization_size", "")
                    biz_domain = data.get("business_domain", "")
                    stakeholder_name = (
                        " ".join(filter(None, [org_size, biz_domain, "Stakeholder"])).strip()
                        or "Organizational Stakeholder"
                    )
                    _jwire_sync(
                        solution_id,
                        ae_type="Stakeholder",
                        ae_layer="Motivation",
                        name=stakeholder_name,
                        description="Inferred stakeholder from business context",
                    )
                    _ae_count += 1

                if not _has_ae_type(solution_id, "Goal"):
                    if solution.description and len(solution.description) > 10:
                        goal_name = solution.description[:80]
                    elif drivers_data:
                        goal_name = drivers_data[0].get("name", "Strategic Objective")[:80]
                    else:
                        goal_name = f"Strategic Goal for {solution.name or 'Solution'}"
                    _jwire_sync(
                        solution_id,
                        ae_type="Goal",
                        ae_layer="Motivation",
                        name=goal_name,
                        description="Inferred goal from problem statement",
                    )
                    _ae_count += 1

            counts["archimate_elements"] = counts.get("archimate_elements", 0) + _ae_count

            # JWIRE-002: Create cross-layer alignment relationships between Motivation elements.
            # ArchiMate 3.2 canonical chains:
            #   Stakeholder -[association]-> Driver
            #   Driver -[influence]-> Goal
            #   Goal -[realization]-> Requirement
            #   Constraint -[realization]-> Requirement (constraint restricts via requirement)
            from app.models.archimate_core import ArchiMateRelationship

            def _get_elements_by_type(sol_id, ae_type):
                return [
                    _AE.query.get(sae.element_id)
                    for sae in _SAE.query.join(_AE, _SAE.element_id == _AE.id).filter(
                        _SAE.solution_id == sol_id, _AE.type == ae_type
                    ).all()
                ]

            def _create_rel_if_missing(source_id, target_id, rel_type, desc):
                if not source_id or not target_id or source_id == target_id:
                    return False
                exists = ArchiMateRelationship.query.filter_by(
                    source_id=source_id, target_id=target_id, type=rel_type
                ).first()
                if not exists:
                    db.session.add(ArchiMateRelationship(
                        source_id=source_id, target_id=target_id,
                        type=rel_type, description=desc,
                    ))
                    return True
                return False

            _rel_count = 0
            stakeholder_els = _get_elements_by_type(solution_id, "Stakeholder")
            driver_els = _get_elements_by_type(solution_id, "Driver")
            goal_els = _get_elements_by_type(solution_id, "Goal")
            req_els = _get_elements_by_type(solution_id, "Requirement")
            constraint_els = _get_elements_by_type(solution_id, "Constraint")

            # Stakeholder -[association]-> Driver (each stakeholder associated with each driver)
            for s in stakeholder_els:
                for d in driver_els:
                    if _create_rel_if_missing(s.id, d.id, "association",
                                              f"{s.name} is associated with {d.name}"):
                        _rel_count += 1

            # Driver -[influence]-> Goal (each driver influences each goal)
            for d in driver_els:
                for g in goal_els:
                    if _create_rel_if_missing(d.id, g.id, "influence",
                                              f"{d.name} influences {g.name}"):
                        _rel_count += 1

            # Goal -[realization]-> Requirement (goals realized by requirements)
            for g in goal_els:
                for r in req_els:
                    if _create_rel_if_missing(g.id, r.id, "realization",
                                              f"{g.name} is realized by {r.name}"):
                        _rel_count += 1

            # Constraint -[association]-> Requirement (constraints restrict via requirements)
            for c in constraint_els:
                for r in req_els:
                    if _create_rel_if_missing(c.id, r.id, "association",
                                              f"Constraint {c.name} restricts {r.name}"):
                        _rel_count += 1

            if _rel_count:
                counts["archimate_relationships"] = _rel_count
                logger.info("JWIRE-002: Created %d cross-layer motivation relationships", _rel_count)

        except Exception as _jwire_err:
            # logger.exception, not logger.warning: this block is the only thing that
            # turns the Motivation tab's on-screen promise ("drivers, goals and
            # constraints you define here become ArchiMate Motivation elements") into
            # rows, and every call in it raised TypeError for as long as
            # _sync_archimate_element has been keyword-only — the callers here were
            # never updated. A one-line warning with no traceback is why that survived:
            # the intake still returned 200 and the journey still advanced, so nothing
            # visible said the architecture backbone had not been written.
            logger.exception("JWIRE-001+ motivation element creation failed: %s", _jwire_err)
            counts["archimate_elements_failed"] = True

        solution.section_scores = None  # invalidate blueprint score cache so vision_motivation reflects intake elements
        db.session.commit()

        logger.info(
            "Structured intake saved for solution %d: %s",
            solution_id,
            counts,
        )
        return api_success(data={
            "session_id": session.id,
            "problem_definition_id": problem_def.id,
            "counts": counts,
        })

    except Exception as e:
        db.session.rollback()
        logger.error(
            "Structured intake failed for solution %d: %s", solution_id, e, exc_info=True
        )
        return api_error(f"Failed to save structured intake: {e}", 500)


@journey_v2_bp.route("/landscape-search", methods=["POST"])
@login_required
def landscape_search():
    """Return enterprise entities matching a problem statement for Landscape Discovery.

    Used by Step 1 of the wizard to show the user what already exists in their
    platform before they answer any clarifying questions.
    """
    data = request.get_json() or {}
    problem_statement = (data.get("problem_statement") or "").strip()
    if len(problem_statement) < 20:
        return api_error("Problem statement too short", 400)

    from app.modules.architecture_assistant.clarification_service import ClarificationService
    svc = ClarificationService()
    keywords = svc._extract_keywords(problem_statement)
    if not keywords:
        return api_success(data={
            "capabilities": [], "applications": [], "elements": [],
            "keywords": [], "total_matches": 0, "is_greenfield": True,
        })

    capabilities, applications, elements = [], [], []

    def _score(name: str, description: str) -> int:
        """Keyword relevance score: 2 pts per keyword in name, 1 pt per keyword in description.
        Minimum score of 2 required to be included — prevents single generic-word matches."""
        name_l = (name or "").lower()
        desc_l = (description or "").lower()
        return sum(2 if kw in name_l else (1 if kw in desc_l else 0) for kw in keywords)

    try:
        from app.models.capability_models import BusinessCapability
        name_f = [BusinessCapability.name.ilike(f'%{kw}%') for kw in keywords]
        desc_f = [BusinessCapability.description.ilike(f'%{kw}%') for kw in keywords]
        caps_raw = BusinessCapability.query.filter(db.or_(*name_f, *desc_f)).limit(30).all()
        scored = sorted(
            [c for c in caps_raw if _score(c.name, c.description or "") >= 2],
            key=lambda c: _score(c.name, c.description or ""), reverse=True,
        )
        capabilities = [{"name": c.name, "description": (c.description or "")[:120]} for c in scored[:8]]
    except Exception as e:
        logger.warning("Landscape search: capability query failed: %s", e)

    try:
        from app.models.archimate_core import ArchiMateElement
        name_f = [ArchiMateElement.name.ilike(f'%{kw}%') for kw in keywords]
        desc_f = [ArchiMateElement.description.ilike(f'%{kw}%') for kw in keywords]
        elems_raw = ArchiMateElement.query.filter(db.or_(*name_f, *desc_f)).limit(30).all()
        scored_elems = sorted(
            [e for e in elems_raw if _score(e.name, e.description or "") >= 2],
            key=lambda e: _score(e.name, e.description or ""), reverse=True,
        )
        elements = [{"name": e.name, "type": getattr(e, 'type', ''), "layer": getattr(e, 'layer', '')} for e in scored_elems[:8]]
    except Exception as e:
        logger.warning("Landscape search: element query failed: %s", e)

    try:
        from app.models.application_portfolio import ApplicationComponent
        name_f = [ApplicationComponent.name.ilike(f'%{kw}%') for kw in keywords]
        apps_raw = db.session.query(ApplicationComponent).filter(db.or_(*name_f)).limit(30).all()
        scored_apps = sorted(
            [a for a in apps_raw if _score(a.name, getattr(a, 'description', '') or "") >= 2],
            key=lambda a: _score(a.name, getattr(a, 'description', '') or ""), reverse=True,
        )
        applications = [{"name": a.name, "description": (getattr(a, 'description', '') or "")[:120]} for a in scored_apps[:8]]
    except Exception as e:
        logger.warning("Landscape search: application query failed: %s", e)

    total = len(capabilities) + len(applications) + len(elements)
    return api_success(data={
        "capabilities": capabilities,
        "applications": applications,
        "elements": elements,
        "keywords": keywords[:6],
        "total_matches": total,
        "is_greenfield": total <= 2,
    })


@journey_v2_bp.route("/<int:solution_id>/structured-intake", methods=["GET"])
@login_required
@_require_solution_owner
def load_structured_intake(solution_id):
    """Load structured intake data from existing models."""
    from app.models.solution_architect_models import (
        SolutionAnalysisSession,
        SolutionConstraint,
        SolutionDriver,
        SolutionRequirement,
    )
    from app.models.solution_models import solution_applications

    solution = Solution.query.get_or_404(solution_id)

    try:
        # Find the intake session for this solution
        session = SolutionAnalysisSession.query.filter_by(
            name=f"Journey Intake \u2014 Solution {solution_id}"
        ).first()

        if not session or not session.problem_definition:
            # Return empty structure — first time through wizard
            return api_success(data={
                "business_domain": solution.business_domain or "",
                "timeline_months": None,
                "budget_min": None,
                "budget_max": None,
                "organization_size": "",
                "geographic_scope": "",
                "user_count": None,
                "transaction_volume": None,
                "data_volume_gb": None,
                "compliance_frameworks": [],
                "nfrs": [],
                "tech_constraints": [],
                "drivers": [],
                "in_scope_apps": solution.in_scope_applications or [],
                "integration_systems": [],
                "pain_points": [],
            })

        pd = session.problem_definition

        # Reconstruct pain_points from business_context
        pain_points = []
        if pd.business_context:
            pain_points = [p.strip() for p in pd.business_context.split(";") if p.strip()]

        # Reconstruct compliance_frameworks
        compliance_frameworks = pd.compliance_requirements or []

        # Reconstruct integration_systems
        integration_systems = pd.integration_requirements or []

        # Reconstruct tech_constraints from constraint records
        tech_constraints = []
        constraints = SolutionConstraint.query.filter_by(problem_id=pd.id).all()
        for c in constraints:
            if c.constraint_type and c.constraint_type.value == "technical":
                tech_constraints.append({
                    "type": c.value or "must_use",
                    "technology": c.name,
                })

        # Reconstruct NFRs from requirements
        nfrs = []
        requirements = SolutionRequirement.query.filter_by(
            problem_id=pd.id, req_type="non_functional"
        ).all()
        reverse_moscow = {"MUST": "must", "SHOULD": "should", "COULD": "could", "WONT": "wont"}
        for req in requirements:
            comp_tags = req.compliance_tags or []
            nfrs.append({
                "type": (req.name or "").lower(),
                "target": req.description or "",
                "priority": reverse_moscow.get(req.moscow_priority, "should"),
                "acceptance_criteria": req.acceptance_criteria or "",
                "compliance_reference": comp_tags[0] if comp_tags else "",
                "verification_method": req.verification_method or "",
            })

        # Reconstruct drivers
        drivers = []
        driver_records = SolutionDriver.query.filter_by(problem_id=pd.id).all()
        for d in driver_records:
            drivers.append({
                "name": d.name,
                "type": d.driver_type.value if d.driver_type else "internal",
                "urgency": d.urgency,
            })

        # Load linked applications from junction table
        linked_app_rows = (
            db.session.query(solution_applications)
            .filter(solution_applications.c.solution_id == solution_id)
            .all()
        )
        in_scope_apps = []
        if linked_app_rows:
            from app.models.application_portfolio import ApplicationComponent

            app_ids = [row.application_component_id for row in linked_app_rows]
            apps = ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(app_ids)
            ).all()
            app_map = {a.id: a for a in apps}
            for aid in app_ids:
                app_obj = app_map.get(aid)
                in_scope_apps.append({
                    "id": aid,
                    "name": getattr(app_obj, "name", f"App {aid}") if app_obj else f"App {aid}",
                })

        # Build budget from numeric fields
        budget_min = float(pd.budget_min) if pd.budget_min is not None else None
        budget_max = float(pd.budget_max) if pd.budget_max is not None else None

        return api_success(data={
            "business_domain": pd.industry_vertical or solution.business_domain or "",
            "timeline_months": pd.timeline_months,
            "budget_min": budget_min,
            "budget_max": budget_max,
            "organization_size": pd.organization_size or "",
            "geographic_scope": pd.geographic_scope or "",
            "user_count": pd.user_count,
            "transaction_volume": pd.transaction_volume,
            "data_volume_gb": pd.data_volume_gb,
            "compliance_frameworks": compliance_frameworks,
            "nfrs": nfrs,
            "tech_constraints": tech_constraints,
            "drivers": drivers,
            "in_scope_apps": in_scope_apps,
            "integration_systems": integration_systems,
            "pain_points": pain_points,
        })

    except Exception as e:
        logger.error(
            "Load structured intake failed for solution %d: %s", solution_id, e, exc_info=True
        )
        return api_error(f"Failed to load structured intake: {e}", 500)


# ─────────────────────────────────────────────────────────────────────────────
# TRAC-001: Chain health check at wizard step transitions
# Returns warnings and explicit chain_complete state for steps 2-5.
# ─────────────────────────────────────────────────────────────────────────────

@journey_v2_bp.route("/<int:solution_id>/validate-step/<int:step_num>", methods=["POST"])
@login_required
@_require_solution_owner
def validate_step(solution_id, step_num):
    """Chain health check for a wizard step transition.

    Returns warnings when ArchiMate traceability chain is incomplete:
    - Step 2: accepted capabilities with no Goal/Driver parent
    - Step 3: any of the 6 ArchiMate layers has 0 elements
    - Step 4: no influence relationships exist (decision provenance missing)
    - Step 5: work packages with no plateau assignment
    """
    from app.models.solution_models import SolutionArchiMateElement
    from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship

    warnings = []

    try:
        if step_num == 2:
            # Check: accepted capabilities have at least one Goal or Driver realizing them
            cap_elements = SolutionArchiMateElement.query.filter_by(
                solution_id=solution_id
            ).filter(
                SolutionArchiMateElement.element_table.in_([
                    "business_capabilities", "technical_capabilities",
                    "capabilities", "solution_capabilities",
                ])
            ).all()

            if cap_elements:
                # Count how many have no inbound realization in inference table
                # architecture_id on the inference table maps to solution_id via convention
                capped_ids = {e.element_id for e in cap_elements}
                linked_ids = {
                    r.target_id
                    for r in ArchitectureInferenceRelationship.query.filter_by(
                        architecture_id=solution_id,
                        rel_type="realization",
                        target_type="Capability",
                    ).all()
                }
                orphaned = len(capped_ids - linked_ids)
                if orphaned > 0:
                    warnings.append({
                        "code": "no_driver",
                        "count": orphaned,
                        "message": (
                            f"{orphaned} {'capability' if orphaned == 1 else 'capabilities'} "
                            f"{'has' if orphaned == 1 else 'have'} no driver or goal linked. "
                            "Add drivers in Step 1 to improve blueprint completeness."
                        ),
                    })

        elif step_num == 3:
            # Check: each of the 6 ArchiMate layers has at least 1 element
            LAYERS = ["motivation", "strategy", "business", "application", "technology", "implementation"]
            counts_by_layer = {}
            rows = (
                db.session.query(
                    SolutionArchiMateElement.layer_type,
                    db.func.count(SolutionArchiMateElement.id).label("cnt"),
                )
                .filter_by(solution_id=solution_id)
                .filter(SolutionArchiMateElement.layer_type.isnot(None))
                .group_by(SolutionArchiMateElement.layer_type)
                .all()
            )
            counts_by_layer = {r.layer_type: r.cnt for r in rows}
            empty_layers = [item for item in LAYERS if counts_by_layer.get(item, 0) == 0]
            if empty_layers:
                labels = ", ".join(item.capitalize() for item in empty_layers)
                warnings.append({
                    "code": "empty_layers",
                    "count": len(empty_layers),
                    "empty": empty_layers,
                    "message": (
                        f"{'Layer' if len(empty_layers) == 1 else 'Layers'} with no elements: {labels}. "
                        "Use the Regenerate button to fill gaps."
                    ),
                })

        elif step_num == 4:
            # Check: at least one influence relationship exists for this solution's decisions
            influence_count = ArchitectureInferenceRelationship.query.filter_by(
                architecture_id=solution_id,
                rel_type="influence",
                target_type="ArchitectureDecision",
            ).count()
            if influence_count == 0:
                # Only warn if there ARE elements to trace from
                element_count = SolutionArchiMateElement.query.filter_by(
                    solution_id=solution_id
                ).count()
                if element_count > 0:
                    warnings.append({
                        "code": "no_decision_trace",
                        "count": 0,
                        "message": (
                            "Architecture decisions have no traced element sources. "
                            "Traceability will improve after decision points are loaded."
                        ),
                    })

        elif step_num == 5:
            # Check: all work packages linked to this solution have a plateau assigned
            from app.models.solution_models import solution_work_packages
            from app.models.implementation_migration import WorkPackage

            linked_wp_ids = [
                row.work_package_id
                for row in db.session.query(solution_work_packages).filter(
                    solution_work_packages.c.solution_id == solution_id
                ).all()
            ]
            if linked_wp_ids:
                orphaned_wps = WorkPackage.query.filter(
                    WorkPackage.id.in_(linked_wp_ids),
                    WorkPackage.plateau_id.is_(None),
                ).count()
                if orphaned_wps > 0:
                    warnings.append({
                        "code": "wp_no_plateau",
                        "count": orphaned_wps,
                        "message": (
                            f"{orphaned_wps} work "
                            f"{'package' if orphaned_wps == 1 else 'packages'} "
                            f"{'has' if orphaned_wps == 1 else 'have'} no transition plateau. "
                            "Assign each work package to a plateau in the roadmap."
                        ),
                    })

        return api_success(data={
            "warnings": warnings,
            "chain_complete": len(warnings) == 0,
        })

    except Exception as e:
        logger.warning("validate_step failed for solution %d step %d: %s", solution_id, step_num, e)
        return api_success(data={
            "warnings": [{
                "code": "validation_failed",
                "count": 1,
                "message": "Validation failed internally; generation is blocked until retry succeeds.",
            }],
            "chain_complete": False,
            "error": str(e),
        })


# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# Architecture decisions narrative synthesis (Step 4 → blueprint scorer bridge)
# ─────────────────────────────────────────────────────────────────────────────

def _write_arch_decisions_narrative(solution_id, decisions_result):
    """Synthesise a narrative from decision_points data and persist to
    solution.section_narratives['architecture_decisions'].

    The blueprint completeness scorer uses narrative_only scoring for this
    section (threshold: 100 words). Without this bridge the section always
    scores 0% even when the architect has populated Step 4 decision cards.

    Idempotent: only overwrites if the new narrative is longer than what's
    already stored, so re-fetching Step 4 never degrades a manually edited
    narrative.
    """
    sol = Solution.query.get(solution_id)
    if not sol:
        return

    decision_points = decisions_result.get("decision_points", [])
    if not decision_points:
        return

    lines = ["Architecture Decisions\n"]
    for dp in decision_points:
        name = dp.get("name") or dp.get("type", "Decision")
        lines.append(f"\n{name}")
        elements = dp.get("elements", [])
        for el in elements[:5]:  # cap at 5 elements per decision to avoid bloat
            el_name = el.get("name", "")
            value = el.get("value", "")
            detail = el.get("detail", "") or el.get("availability", "")
            effort = el.get("effort", "")
            lines.append(f"  - {el_name}: {value}" + (f" ({detail})" if detail else "") + (f", effort: {effort}" if effort else ""))
        rationale = dp.get("rationale", "")
        if rationale:
            lines.append(f"  Rationale: {rationale}")

    narrative = "\n".join(lines)

    existing = (sol.section_narratives or {}).get("architecture_decisions", "")
    if len(narrative.split()) <= len((existing or "").split()):
        return  # never shrink an existing narrative

    narratives = dict(sol.section_narratives or {})
    narratives["architecture_decisions"] = narrative
    sol.section_narratives = narratives
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


# TRAC-002: Store Goal→Capability realization rows at capability acceptance
# Called after derive_capabilities saves accepted capabilities.
# ─────────────────────────────────────────────────────────────────────────────

def _sync_capability_realization_links(solution_id):
    """Write Goal→Capability InferenceRelationship rows for all accepted capabilities.

    Idempotent — uses the unique constraint on (architecture_id, source_type,
    source_id, target_type, target_id, rel_type) to skip duplicates.

    Queries via solution_archimate_elements + archimate_elements.type since the
    wizard stores all elements with element_table='archimate_elements' and uses
    the type column to distinguish Goals/Capabilities/Drivers.
    """
    from app.models.solution_models import SolutionArchiMateElement
    from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship
    from app.models.archimate_core import ArchiMateElement

    # Capability elements: strategy layer, type='Capability'
    cap_elements = (
        db.session.query(SolutionArchiMateElement)
        .join(ArchiMateElement, ArchiMateElement.id == SolutionArchiMateElement.element_id)
        .filter(
            SolutionArchiMateElement.solution_id == solution_id,
            ArchiMateElement.type == "Capability",
        )
        .all()
    )

    if not cap_elements:
        return 0

    # Goal elements: motivation layer, type='Goal'
    goal_elements = (
        db.session.query(SolutionArchiMateElement)
        .join(ArchiMateElement, ArchiMateElement.id == SolutionArchiMateElement.element_id)
        .filter(
            SolutionArchiMateElement.solution_id == solution_id,
            ArchiMateElement.type == "Goal",
        )
        .all()
    )

    # Driver elements: motivation layer, type='Driver'
    driver_elements = (
        db.session.query(SolutionArchiMateElement)
        .join(ArchiMateElement, ArchiMateElement.id == SolutionArchiMateElement.element_id)
        .filter(
            SolutionArchiMateElement.solution_id == solution_id,
            ArchiMateElement.type == "Driver",
        )
        .all()
    )

    # Use element_id as the source_id/target_id (stable FK into archimate_elements)
    goals = goal_elements
    drivers = driver_elements

    inserted = 0
    for cap in cap_elements:
        # Goal → Capability realization (primary chain link)
        for goal in goals:
            try:
                rel = ArchitectureInferenceRelationship(
                    architecture_id=solution_id,
                    source_type="Goal",
                    source_id=goal.element_id,
                    target_type="Capability",
                    target_id=cap.element_id,
                    rel_type="realization",
                    source_tag="wizard_intake",
                    confidence=0.9,
                    inference_pass=1,
                    rule_name="TRAC-002:goal_capability_realization",
                )
                db.session.add(rel)
                db.session.flush()
                inserted += 1
            except Exception:
                db.session.rollback()  # unique constraint hit — already exists
                db.session.begin_nested()

        # Driver → Capability influence (secondary link for completeness scoring)
        for driver in drivers:
            try:
                rel = ArchitectureInferenceRelationship(
                    architecture_id=solution_id,
                    source_type="Driver",
                    source_id=driver.element_id,
                    target_type="Capability",
                    target_id=cap.element_id,
                    rel_type="influence",
                    source_tag="wizard_intake",
                    confidence=0.8,
                    inference_pass=1,
                    rule_name="TRAC-002:driver_capability_influence",
                )
                db.session.add(rel)
                db.session.flush()
                inserted += 1
            except Exception:
                db.session.rollback()
                db.session.begin_nested()

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

    return inserted


# ---------------------------------------------------------------------------
# Traceability Sankey flow API
# ---------------------------------------------------------------------------

@journey_v2_bp.route("/<int:solution_id>/traceability-flow", methods=["GET"])
@login_required
def traceability_flow(solution_id):
    """Return D3 Sankey data: ArchiMate layers → code. Nodes annotated with layer/column/has_spec/has_code.
    Links filtered to left-to-right cross-layer only (Sankey DAG constraint).
    """
    _sol, _missing = require_entity_json(_GuardSolution, solution_id, label="Solution")
    if _missing:
        return _missing

    LAYER_COLUMN = {
        "motivation": 0,
        "strategy": 1,
        "business": 2,
        "application": 3,
        "technology": 4,
        "implementation": 5,
    }

    saes = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
    el_ids = [sae.element_id for sae in saes if sae.element_id]

    if not el_ids:
        return jsonify({"nodes": [], "links": [], "has_code": False,
                        "total_elements": 0, "total_links": 0})

    elements = ArchiMateElement.query.filter(ArchiMateElement.id.in_(el_ids)).all()
    sae_by_el_id = {sae.element_id: sae for sae in saes}

    nodes = []
    node_index = {}  # element_id -> position in nodes list

    for el in elements:
        layer = (el.layer or "").lower()
        col = LAYER_COLUMN.get(layer)
        if col is None:
            continue
        sae = sae_by_el_id.get(el.id)
        spec_data = (sae.spec_data or {}) if sae else {}
        has_spec = bool(spec_data.get("fields") or spec_data.get("api_contract"))
        spec_fields = len(spec_data.get("fields") or {}) if has_spec and isinstance(spec_data.get("fields"), dict) else 0
        health = (
            (1 if el.description else 0)
            + (1 if el.status else 0)
            + (1 if has_spec else 0)
            + (1 if el.priority else 0)
        )
        origin = (sae.element_role or "primary") if sae else "primary"
        node_index[el.id] = len(nodes)
        nodes.append({
            "id": f"el_{el.id}",
            "name": el.name or f"Element {el.id}",
            "type": el.type or "",
            "layer": layer,
            "column": col,
            "has_spec": has_spec,
            "has_code": False,
            "description": el.description or "",
            "status": el.status or "",
            "priority": el.priority or "",
            "dependency_level": el.dependency_level or "",
            "critical_path": bool(el.critical_path_member),
            "spec_fields": spec_fields,
            "health": health,
            "origin": origin,
        })

    # Mark nodes that have generated code referencing their name
    try:
        gen = (CodegenGeneration.query
               .filter_by(solution_id=solution_id)
               .order_by(CodegenGeneration.id.desc())
               .first())
        if gen and gen.generated_files:
            code_keys = {k.lower() for k in gen.generated_files}
            for node in nodes:
                slug = node["name"].lower().replace(" ", "_").replace("-", "_")
                node["has_code"] = any(slug in k for k in code_keys)
    except Exception as exc:
        logger.debug("suppressed error in traceability_flow (app/modules/solutions_strategic/v2/routes/journey_v2_routes.py): %s", exc)

    # Relationships — left-to-right cross-layer only (Sankey constraint).
    # Query both ArchiMateRelationship (explicit) and ArchitectureInferenceRelationship
    # (inference engine output) so wizard-generated solutions show real traceability.
    rels = (ArchiMateRelationship.query
            .filter(ArchiMateRelationship.source_id.in_(el_ids))
            .filter(ArchiMateRelationship.target_id.in_(el_ids))
            .all())

    links = []
    seen = set()
    for rel in rels:
        src_idx = node_index.get(rel.source_id)
        tgt_idx = node_index.get(rel.target_id)
        if src_idx is None or tgt_idx is None:
            continue
        if nodes[src_idx]["column"] >= nodes[tgt_idx]["column"]:
            continue  # drop same-layer and reverse links
        key = (src_idx, tgt_idx)
        if key in seen:
            continue
        seen.add(key)
        links.append({"source": src_idx, "target": tgt_idx,
                      "value": 1, "type": rel.type or "association"})

    # Supplement with inference engine relationships (source_id/target_id are ArchiMate element IDs)
    try:
        air_rels = (ArchitectureInferenceRelationship.query
                    .filter(ArchitectureInferenceRelationship.source_id.in_(el_ids))
                    .filter(ArchitectureInferenceRelationship.target_id.in_(el_ids))
                    .all())
        for air in air_rels:
            src_idx = node_index.get(air.source_id)
            tgt_idx = node_index.get(air.target_id)
            if src_idx is None or tgt_idx is None:
                continue
            if nodes[src_idx]["column"] >= nodes[tgt_idx]["column"]:
                continue
            key = (src_idx, tgt_idx)
            if key in seen:
                continue
            seen.add(key)
            links.append({"source": src_idx, "target": tgt_idx,
                          "value": 1, "type": air.rel_type or "inference"})
    except Exception as exc:
        logger.debug("suppressed error in traceability_flow (app/modules/solutions_strategic/v2/routes/journey_v2_routes.py): %s", exc)  # table may not exist on older deployments

    # Synthesize canonical chain links when no explicit relationships exist.
    # Wizard-generated solutions have elements across all layers but ArchiMateRelationship
    # rows are only created when the inference engine runs.  Without links the Sankey
    # cannot render.  We pair adjacent-layer elements round-robin so the full chain
    # (Motivation→Strategy→Business→Application→Technology→Implementation) is visible
    # even before inference wires specific relationships.
    synthesized = False
    if not links and len(nodes) > 1:
        by_col = {}
        for i, n in enumerate(nodes):
            by_col.setdefault(n["column"], []).append(i)
        for col in sorted(by_col):
            if col + 1 not in by_col:
                continue
            src_list = by_col[col]
            tgt_list = by_col[col + 1]
            for i, src_idx in enumerate(src_list):
                tgt_idx = tgt_list[i % len(tgt_list)]
                key = (src_idx, tgt_idx)
                if key not in seen:
                    seen.add(key)
                    links.append({"source": src_idx, "target": tgt_idx,
                                  "value": 1, "type": "synthesized"})
        synthesized = bool(links)

    # ── Canonical chain coverage ──────────────────────────────────────
    # Check what fraction of CANONICAL_CHAIN required links are covered by actual
    # (non-synthesized) relationships.  Synthesized links are layer-order guesses and
    # do not count as real traceability — they are visual scaffolding only.
    chain_coverage_pct = None
    chain_missing_pairs = []
    try:
        from app.modules.architecture.services.inference_rules_registry import CANONICAL_CHAIN
        # Build (source_type, target_type) set from real (non-synthesized) links only
        _type_pairs: set[tuple[str, str]] = set()
        for lnk in links:
            if lnk.get("type") == "synthesized":
                continue
            src_n = nodes[lnk["source"]] if isinstance(lnk["source"], int) else None
            tgt_n = nodes[lnk["target"]] if isinstance(lnk["target"], int) else None
            if src_n and tgt_n:
                _type_pairs.add((src_n["type"], tgt_n["type"]))

        required_chain = [(p, c) for p, c, m in CANONICAL_CHAIN if m.get("required")]
        if required_chain:
            covered = sum(1 for p, c in required_chain if (p, c) in _type_pairs)
            chain_coverage_pct = round(100 * covered / len(required_chain))
            chain_missing_pairs = [
                {"from": p, "to": c}
                for p, c in required_chain
                if (p, c) not in _type_pairs
            ]
    except Exception as exc:
        logger.debug("suppressed error in traceability_flow (app/modules/solutions_strategic/v2/routes/journey_v2_routes.py): %s", exc)  # non-fatal

    return jsonify({
        "nodes": nodes,
        "links": links,
        "has_code": any(n["has_code"] for n in nodes),
        "total_elements": len(nodes),
        "total_links": len(links),
        "synthesized": synthesized,
        "chain_coverage_pct": chain_coverage_pct,
        "chain_missing_pairs": chain_missing_pairs,
    })


# ---------------------------------------------------------------------------
# Intra-layer Sankey flow  — elements within a single ArchiMate layer
# ---------------------------------------------------------------------------

# ArchiMate Active / Behavioral / Passive aspect columns per layer
_INTRA_LAYER_TYPE_COL = {
    "motivation": {
        "Stakeholder": 0,
        "Driver": 1, "Assessment": 1,
        "Goal": 2, "Outcome": 2, "Principle": 2, "Requirement": 2,
        "Constraint": 2, "Meaning": 2, "Value": 2,
    },
    "strategy": {
        "Resource": 0,
        "Capability": 1, "ValueStream": 1,
        "CourseOfAction": 2,
    },
    "business": {
        "BusinessActor": 0, "BusinessRole": 0, "BusinessCollaboration": 0,
        "BusinessProcess": 1, "BusinessFunction": 1, "BusinessInteraction": 1,
        "BusinessEvent": 1, "BusinessInterface": 1,
        "BusinessService": 2, "BusinessObject": 2, "Contract": 2,
        "Product": 2, "Representation": 2,
    },
    "application": {
        "ApplicationComponent": 0, "ApplicationCollaboration": 0,
        "ApplicationFunction": 1, "ApplicationInteraction": 1,
        "ApplicationProcess": 1, "ApplicationEvent": 1, "ApplicationInterface": 1,
        "ApplicationService": 2, "DataObject": 2,
    },
    "technology": {
        "Node": 0, "Device": 0, "SystemSoftware": 0, "TechnologyCollaboration": 0,
        "TechnologyFunction": 1, "TechnologyProcess": 1, "TechnologyInteraction": 1,
        "TechnologyEvent": 1, "TechnologyInterface": 1,
        "Path": 1, "CommunicationNetwork": 1,
        "TechnologyService": 2, "Artifact": 2,
    },
    "implementation": {
        "WorkPackage": 0, "Gap": 0,
        "Deliverable": 1, "ImplementationEvent": 1,
        "Plateau": 2,
    },
}

_INTRA_LAYER_COL_LABELS = {
    "motivation":     [{"name": "Stakeholders"}, {"name": "Drivers"}, {"name": "Goals & Principles"}],
    "strategy":       [{"name": "Resources"}, {"name": "Capabilities"}, {"name": "Actions"}],
    "business":       [{"name": "Active Structure"}, {"name": "Behavior"}, {"name": "Passive Structure"}],
    "application":    [{"name": "Components"}, {"name": "Behavior"}, {"name": "Services & Data"}],
    "technology":     [{"name": "Infrastructure"}, {"name": "Functions"}, {"name": "Services & Artifacts"}],
    "implementation": [{"name": "Work Items"}, {"name": "Deliverables"}, {"name": "Plateaus"}],
}


@journey_v2_bp.route("/<int:solution_id>/layer-flow/<layer>", methods=["GET"])
@login_required
def layer_flow(solution_id, layer):
    """Return D3 Sankey data for a single ArchiMate layer.
    Nodes are elements in that layer; columns are Active/Behavioral/Passive aspects.
    Links are intra-layer relationships only (both endpoints in this layer).
    """
    _sol, _missing = require_entity_json(_GuardSolution, solution_id, label="Solution")
    if _missing:
        return _missing

    layer = layer.lower()
    type_col = _INTRA_LAYER_TYPE_COL.get(layer, {})
    col_labels = _INTRA_LAYER_COL_LABELS.get(layer, [{"name": "Active"}, {"name": "Behavioral"}, {"name": "Passive"}])

    saes = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
    el_ids = [sae.element_id for sae in saes if sae.element_id]
    if not el_ids:
        return jsonify({"nodes": [], "links": [], "column_labels": col_labels})

    elements = (ArchiMateElement.query
                .filter(ArchiMateElement.id.in_(el_ids))
                .filter(db.func.lower(ArchiMateElement.layer) == layer)
                .all())
    sae_by_el_id = {sae.element_id: sae for sae in saes}

    nodes = []
    node_index = {}

    for el in elements:
        col = type_col.get(el.type, 1)  # default: behavioral column
        sae = sae_by_el_id.get(el.id)
        spec_data = (sae.spec_data or {}) if sae else {}
        has_spec = bool(spec_data.get("fields") or spec_data.get("api_contract"))
        spec_fields = len(spec_data.get("fields") or {}) if has_spec and isinstance(spec_data.get("fields"), dict) else 0
        health = (
            (1 if el.description else 0)
            + (1 if el.status else 0)
            + (1 if has_spec else 0)
            + (1 if el.priority else 0)
        )
        origin = (sae.element_role or "primary") if sae else "primary"
        node_index[el.id] = len(nodes)
        nodes.append({
            "id": f"el_{el.id}",
            "name": el.name or f"Element {el.id}",
            "type": el.type or "",
            "layer": layer,
            "column": col,
            "has_spec": has_spec,
            "has_code": False,
            "description": el.description or "",
            "status": el.status or "",
            "priority": el.priority or "",
            "dependency_level": el.dependency_level or "",
            "critical_path": bool(el.critical_path_member),
            "spec_fields": spec_fields,
            "health": health,
            "origin": origin,
        })

    layer_el_ids = [el.id for el in elements]

    # Collect intra-layer relationships from both relationship tables
    links = []
    seen = set()

    for rel_cls, src_attr, tgt_attr in [
        (ArchiMateRelationship, "source_id", "target_id"),
        (ArchitectureInferenceRelationship, "source_id", "target_id"),
    ]:
        try:
            rels = (rel_cls.query
                    .filter(getattr(rel_cls, src_attr).in_(layer_el_ids))
                    .filter(getattr(rel_cls, tgt_attr).in_(layer_el_ids))
                    .all())
            for rel in rels:
                src_idx = node_index.get(getattr(rel, src_attr))
                tgt_idx = node_index.get(getattr(rel, tgt_attr))
                if src_idx is None or tgt_idx is None or src_idx == tgt_idx:
                    continue
                if nodes[src_idx]["column"] >= nodes[tgt_idx]["column"]:
                    continue  # Sankey DAG constraint
                key = (src_idx, tgt_idx)
                if key in seen:
                    continue
                seen.add(key)
                rel_type = getattr(rel, "type", None) or getattr(rel, "rel_type", None) or "association"
                links.append({"source": src_idx, "target": tgt_idx, "value": 1, "type": rel_type})
        except Exception as exc:
            logger.debug("suppressed error in layer_flow (app/modules/solutions_strategic/v2/routes/journey_v2_routes.py): %s", exc)

    return jsonify({
        "nodes": nodes,
        "links": links,
        "column_labels": col_labels,
        "has_code": False,
        "synthesized": False,
        "total_elements": len(nodes),
        "total_links": len(links),
    })


# ---------------------------------------------------------------------------
# Architecture accuracy endpoint  (TRAC-004 / accuracy service)
# ---------------------------------------------------------------------------

_AM32_LAYER = {
    'Stakeholder':'motivation','Driver':'motivation','Assessment':'motivation',
    'Goal':'motivation','Outcome':'motivation','Principle':'motivation',
    'Requirement':'motivation','Constraint':'motivation','Meaning':'motivation','Value':'motivation',
    'Resource':'strategy','Capability':'strategy','ValueStream':'strategy','CourseOfAction':'strategy',
    'BusinessActor':'business','BusinessRole':'business','BusinessProcess':'business',
    'BusinessFunction':'business','BusinessService':'business','BusinessObject':'business',
    'BusinessInterface':'business','BusinessCollaboration':'business',
    'BusinessInteraction':'business','BusinessEvent':'business',
    'Contract':'business','Representation':'business','Product':'business',
    'ApplicationComponent':'application','ApplicationCollaboration':'application',
    'ApplicationInterface':'application','ApplicationFunction':'application',
    'ApplicationProcess':'application','ApplicationInteraction':'application',
    'ApplicationEvent':'application','ApplicationService':'application','DataObject':'application',
    'Node':'technology','Device':'technology','SystemSoftware':'technology',
    'TechnologyCollaboration':'technology','TechnologyInterface':'technology','Path':'technology',
    'CommunicationNetwork':'technology','TechnologyFunction':'technology',
    'TechnologyProcess':'technology','TechnologyInteraction':'technology',
    'TechnologyEvent':'technology','TechnologyService':'technology','Artifact':'technology',
    'Equipment':'physical','Facility':'physical','DistributionNetwork':'physical','Material':'physical',
    'WorkPackage':'implementation','Deliverable':'implementation',
    'ImplementationEvent':'implementation','Plateau':'implementation','Gap':'implementation',
}
_AM32_REL_TYPES = frozenset({
    'composition','aggregation','assignment','realization','serving',
    'access','influence','triggering','flow','specialization','association',
})
_CHAIN_LAYERS = ['motivation', 'strategy', 'business', 'application', 'technology', 'implementation']


@journey_v2_bp.route("/<int:solution_id>/architecture-accuracy", methods=["GET"])
@login_required
def architecture_accuracy(solution_id):
    """Return a per-solution ArchiMate 3.2 accuracy report across 5 deterministic dimensions.

    Dimensions:
      1. type_validity       — element type ∈ 62-type ArchiMate 3.2 catalog
      2. layer_placement     — element in canonical layer for its type
      3. chain_completeness  — which of 6 Motivation→Implementation layers populated
      4. traceability        — % elements with ≥1 AIR or AR upstream/downstream relationship
      5. rel_type_validity   — relationship types ∈ ArchiMate 3.2 standard (11 types)

    No LLM calls — fully deterministic. Safe to call on every page load.
    """
    _sol, _missing = require_entity_json(_GuardSolution, solution_id, label="Solution")
    if _missing:
        return _missing

    saes = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
    if not saes:
        return jsonify({"solution_id": solution_id, "total_elements": 0,
                        "overall_score": 0, "dimensions": {}, "elements": []})

    el_ids = [sae.element_id for sae in saes if sae.element_id]
    elements = ArchiMateElement.query.filter(ArchiMateElement.id.in_(el_ids)).all()
    el_by_id = {e.id: e for e in elements}

    # ── Dimension 1 + 2: type validity and layer placement ──────────────
    type_invalid, layer_wrong, element_reports = [], [], []
    for sae in saes:
        el = el_by_id.get(sae.element_id)
        if not el:
            continue
        el_type = el.type or ""
        el_layer = (el.layer or "").lower()
        type_ok = el_type in _AM32_LAYER
        expected_layer = _AM32_LAYER.get(el_type)
        layer_ok = type_ok and (expected_layer == el_layer)
        if not type_ok:
            type_invalid.append({"id": el.id, "type": el_type, "name": el.name})
        if type_ok and not layer_ok:
            layer_wrong.append({
                "id": el.id, "type": el_type, "name": el.name,
                "stored_layer": el_layer, "correct_layer": expected_layer,
            })
        element_reports.append({
            "id": el.id, "name": el.name or "", "type": el_type,
            "layer": el_layer, "type_valid": type_ok, "layer_valid": layer_ok,
            "expected_layer": expected_layer,
            "has_upstream": False, "has_downstream": False,  # filled below
        })

    # ── Dimension 3: chain completeness ──────────────────────────────────
    covered_layers = {_AM32_LAYER[e.type] for e in elements if e.type in _AM32_LAYER}
    missing_layers = [item for item in _CHAIN_LAYERS if item not in covered_layers]
    chain_score = int(100 * len(covered_layers & set(_CHAIN_LAYERS)) / len(_CHAIN_LAYERS))

    # ── Dimension 4: traceability (AIR + AR) ─────────────────────────────
    el_id_set = set(el_ids)
    upstream_ids, downstream_ids = set(), set()

    ar_src = {r.source_id for r in ArchiMateRelationship.query
              .filter(ArchiMateRelationship.source_id.in_(el_ids),
                      ArchiMateRelationship.target_id.in_(el_ids)).all()}
    ar_tgt = {r.target_id for r in ArchiMateRelationship.query
              .filter(ArchiMateRelationship.source_id.in_(el_ids),
                      ArchiMateRelationship.target_id.in_(el_ids)).all()}
    upstream_ids |= ar_tgt
    downstream_ids |= ar_src

    try:
        air_rows = (ArchitectureInferenceRelationship.query
                    .filter(ArchitectureInferenceRelationship.source_id.in_(el_ids))
                    .filter(ArchitectureInferenceRelationship.target_id.in_(el_ids))
                    .all())
        for air in air_rows:
            downstream_ids.add(air.source_id)
            upstream_ids.add(air.target_id)
    except Exception as exc:
        logger.debug("suppressed error in architecture_accuracy (app/modules/solutions_strategic/v2/routes/journey_v2_routes.py): %s", exc)

    traced_ids = upstream_ids | downstream_ids
    orphan_ids = el_id_set - traced_ids
    trace_score = int(100 * len(traced_ids & el_id_set) / len(el_ids)) if el_ids else 0

    # Back-fill has_upstream / has_downstream on element reports
    el_report_by_id = {r["id"]: r for r in element_reports}
    for eid in downstream_ids & el_id_set:
        if eid in el_report_by_id:
            el_report_by_id[eid]["has_downstream"] = True
    for eid in upstream_ids & el_id_set:
        if eid in el_report_by_id:
            el_report_by_id[eid]["has_upstream"] = True

    # ── Dimension 5: relationship type validity ───────────────────────────
    invalid_rel_types = []
    try:
        rel_type_rows = (db.session.query(
            ArchitectureInferenceRelationship.rel_type,
            db.func.count().label("cnt"))
            .filter(ArchitectureInferenceRelationship.source_id.in_(el_ids))
            .group_by(ArchitectureInferenceRelationship.rel_type)
            .all())
        invalid_rel_types = [
            {"rel_type": r.rel_type, "count": r.cnt}
            for r in rel_type_rows if r.rel_type.lower() not in _AM32_REL_TYPES
        ]
    except Exception as exc:
        logger.debug("suppressed error in architecture_accuracy (app/modules/solutions_strategic/v2/routes/journey_v2_routes.py): %s", exc)

    rel_total = sum(r["count"] for r in invalid_rel_types)
    rel_score = 100 if not invalid_rel_types else max(
        0, int(100 * (1 - rel_total / max(1, sum(r.cnt for r in rel_type_rows))))
    )

    # ── Overall score (weighted average) ─────────────────────────────────
    type_score = int(100 * (len(elements) - len(type_invalid)) / max(1, len(elements)))
    layer_score = int(100 * (len(elements) - len(layer_wrong)) / max(1, len(elements)))
    overall = int(0.15 * type_score + 0.20 * layer_score + 0.25 * chain_score
                  + 0.30 * trace_score + 0.10 * rel_score)

    # ── Dimension 6: semantic coherence ──────────────────────────────────
    # Keyword overlap between each element name and the solution's description.
    # Flags elements whose vocabulary has zero overlap with the solution domain
    # (e.g. IoT/manufacturing terms generated for an email-processing solution).
    # Fully deterministic — no LLM. Overlap = |name_words ∩ domain_words| > 0.
    import re as _re
    _STOP = frozenset({
        'a','an','the','and','or','of','to','in','for','with','by','at','on',
        'is','are','be','as','this','that','its','it','new','system','service',
        'data','platform','solution','management','process','processing',
        'capability','component','interface','application','api','layer',
    })
    def _tokens(text):
        return {w for w in _re.split(r'[^a-z]+', (text or '').lower()) if len(w) > 2 and w not in _STOP}

    from flask import g as _g
    _org = getattr(_g, "current_org_id", None)
    _oc = " AND organization_id = :org" if _org is not None else ""
    _sp = {'s': solution_id, 'org': _org} if _org is not None else {'s': solution_id}
    sol_row = db.session.execute(
        db.text(f'SELECT name, description FROM solutions WHERE id = :s{_oc}'), _sp
    ).fetchone()
    _sol_name = sol_row[0] if sol_row else ""
    _sol_desc = sol_row[1] if sol_row else ""
    _domain_tokens = _tokens(_sol_name + " " + _sol_desc)

    irrelevant = []
    for r in element_reports:
        el = el_by_id.get(r["id"])
        if not el:
            continue
        sae = next((s for s in saes if s.element_id == r["id"]), None)
        # Only check journey-generated elements — document/baseline are grounded
        source = (el.acm_source or "")
        r["source"] = source
        if "journey" in source and _domain_tokens:
            el_tokens = _tokens(r["name"])
            overlap = el_tokens & _domain_tokens
            r["domain_overlap"] = len(overlap)
            r["domain_relevant"] = len(overlap) > 0
            if not overlap:
                irrelevant.append({"id": r["id"], "name": r["name"], "type": r["type"], "source": source})
        else:
            r["domain_overlap"] = None
            r["domain_relevant"] = None

    journey_count = sum(1 for r in element_reports if "journey" in (r.get("source") or ""))
    irrelevant_count = len(irrelevant)
    coherence_score = int(100 * (journey_count - irrelevant_count) / max(1, journey_count)) if journey_count else 100

    # ── Revised overall (6 dimensions) ───────────────────────────────────
    overall = int(0.10 * type_score + 0.15 * layer_score + 0.20 * chain_score
                  + 0.25 * trace_score + 0.10 * rel_score + 0.20 * coherence_score)

    return jsonify({
        "solution_id": solution_id,
        "total_elements": len(elements),
        "overall_score": overall,
        "dimensions": {
            "type_validity": {
                "score": type_score,
                "invalid_count": len(type_invalid),
                "issues": type_invalid[:20],
            },
            "layer_placement": {
                "score": layer_score,
                "misplaced_count": len(layer_wrong),
                "issues": layer_wrong[:20],
            },
            "chain_completeness": {
                "score": chain_score,
                "covered_layers": sorted(covered_layers & set(_CHAIN_LAYERS)),
                "missing_layers": missing_layers,
            },
            "traceability": {
                "score": trace_score,
                "traced_count": len(traced_ids & el_id_set),
                "orphan_count": len(orphan_ids),
                "orphans": [
                    {"id": i, "name": el_by_id[i].name, "type": el_by_id[i].type}
                    for i in list(orphan_ids)[:20] if i in el_by_id
                ],
            },
            "rel_type_validity": {
                "score": rel_score,
                "invalid_types": invalid_rel_types,
            },
            "semantic_coherence": {
                "score": coherence_score,
                "journey_elements_checked": journey_count,
                "irrelevant_count": irrelevant_count,
                "irrelevant": irrelevant[:40],
            },
        },
        "elements": list(el_report_by_id.values()),
    })


@journey_v2_bp.route("/<int:solution_id>/elements/<int:element_id>", methods=["DELETE"])
@login_required
def remove_solution_element(solution_id, element_id):
    """Remove one element from a solution (deletes junction + its AIR rows for this solution).
    Does NOT delete the shared ArchiMateElement record — it may belong to other solutions.
    """
    sae = SolutionArchiMateElement.query.filter_by(
        solution_id=solution_id, element_id=element_id
    ).first()
    if not sae:
        return jsonify({"error": "Element not linked to this solution"}), 404

    # Remove AIR relationships scoped to this solution's element set
    try:
        sol_el_ids = [s.element_id for s in SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()]
        ArchitectureInferenceRelationship.query.filter(
            db.or_(
                db.and_(
                    ArchitectureInferenceRelationship.source_id == element_id,
                    ArchitectureInferenceRelationship.target_id.in_(sol_el_ids),
                ),
                db.and_(
                    ArchitectureInferenceRelationship.target_id == element_id,
                    ArchitectureInferenceRelationship.source_id.in_(sol_el_ids),
                ),
            )
        ).delete(synchronize_session=False)
    except Exception as exc:
        logger.debug("suppressed error in remove_solution_element (app/modules/solutions_strategic/v2/routes/journey_v2_routes.py): %s", exc)

    db.session.delete(sae)
    db.session.commit()
    return jsonify({"deleted": element_id, "solution_id": solution_id}), 200


@journey_v2_bp.route("/<int:solution_id>/elements/bulk-delete", methods=["POST"])
@login_required
def bulk_remove_solution_elements(solution_id):
    """Remove multiple elements from a solution in one call.
    Body: {"element_ids": [1, 2, 3, ...]}
    """
    body = request.get_json(silent=True) or {}
    element_ids = body.get("element_ids", [])
    if not element_ids or not isinstance(element_ids, list):
        return jsonify({"error": "element_ids list required"}), 400

    deleted, skipped = [], []
    sol_el_ids = [s.element_id for s in SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()]

    for eid in element_ids:
        sae = SolutionArchiMateElement.query.filter_by(
            solution_id=solution_id, element_id=eid
        ).first()
        if not sae:
            skipped.append(eid)
            continue
        try:
            ArchitectureInferenceRelationship.query.filter(
                db.or_(
                    db.and_(
                        ArchitectureInferenceRelationship.source_id == eid,
                        ArchitectureInferenceRelationship.target_id.in_(sol_el_ids),
                    ),
                    db.and_(
                        ArchitectureInferenceRelationship.target_id == eid,
                        ArchitectureInferenceRelationship.source_id.in_(sol_el_ids),
                    ),
                )
            ).delete(synchronize_session=False)
        except Exception as exc:
            logger.debug("suppressed error in bulk_remove_solution_elements (app/modules/solutions_strategic/v2/routes/journey_v2_routes.py): %s", exc)
        db.session.delete(sae)
        deleted.append(eid)

    db.session.commit()
    return jsonify({"deleted": deleted, "skipped": skipped, "solution_id": solution_id}), 200


# ── Field Confirmation (Data Model Review) ────────────────────────────────────

_DATA_ELEMENT_TYPES = {
    "DataObject", "BusinessObject",
    "ApplicationComponent", "ApplicationService", "ApplicationFunction",
    "BusinessService",
}


@journey_v2_bp.route("/<int:solution_id>/element-fields", methods=["GET"])
@login_required
def get_element_fields(solution_id):
    """Return data-bearing elements with their spec_data.fields for the Data Model Review panel.

    Only returns elements whose type can carry a field schema (DataObject, ApplicationComponent, etc.).
    Summary counts: total / confirmed / ai_inferred / pending.
    """
    _sol, _missing = require_entity_json(_GuardSolution, solution_id, label="Solution")
    if _missing:
        return _missing

    from app.models.archimate_core import ArchiMateElement

    links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
    if not links:
        return jsonify({"elements": [], "summary": {"total": 0, "confirmed": 0, "ai_inferred": 0, "pending": 0}})

    element_ids = [item.element_id for item in links]
    elements = {
        e.id: e
        for e in ArchiMateElement.query.filter(ArchiMateElement.id.in_(element_ids)).all()
        if e.type in _DATA_ELEMENT_TYPES
    }
    junction_by_el = {item.element_id: item for item in links}

    result = []
    summary = {"total": 0, "confirmed": 0, "vendor_seeded": 0, "schema_imported": 0, "ai_inferred": 0, "pending": 0}

    for el_id, el in elements.items():
        junction = junction_by_el.get(el_id)
        sd = (junction.spec_data or {}) if junction else {}
        fields = sd.get("fields") or []
        status = sd.get("fields_status") or "pending"

        summary["total"] += 1
        if status == "confirmed":
            summary["confirmed"] += 1
        elif status == "vendor_seeded":
            summary["vendor_seeded"] += 1
        elif status == "schema_imported":
            summary["schema_imported"] += 1
        elif status in ("ai_inferred", "auto_accepted", "proposed"):
            summary["ai_inferred"] += 1
        else:
            summary["pending"] += 1

        result.append({
            "element_id": el_id,
            "name": el.name or f"Element {el_id}",
            "type": el.type,
            "layer": el.layer or "",
            "description": el.description or "",
            "fields_status": status,
            "fields": fields,
        })

    # Sort: ai_inferred first (need review), then vendor_seeded/schema_imported (spot-check), then confirmed (done), then pending
    _STATUS_ORDER = {"ai_inferred": 0, "proposed": 0, "auto_accepted": 0,
                     "vendor_seeded": 1, "schema_imported": 1,
                     "confirmed": 2, "pending": 3}
    result.sort(key=lambda x: (_STATUS_ORDER.get(x["fields_status"], 3), x["name"]))
    return jsonify({"elements": result, "summary": summary})


@journey_v2_bp.route("/<int:solution_id>/element/<int:element_id>/fields", methods=["PATCH"])
@login_required
@_require_solution_owner
def confirm_element_fields(solution_id, element_id):
    """Save architect-reviewed fields to spec_data with fields_status='confirmed'.

    Body: {"fields": [...], "status": "confirmed"|"reset"}
    - "confirmed": stores fields as architect-approved ground truth; codegen will use them verbatim.
    - "reset": clears confirmation, reverts to pending so next enrichment can re-infer.
    """
    body = request.get_json(silent=True) or {}
    new_status = body.get("status", "confirmed")
    if new_status not in ("confirmed", "reset"):
        return api_error("status must be 'confirmed' or 'reset'", 400)

    junction = SolutionArchiMateElement.query.filter_by(
        solution_id=solution_id, element_id=element_id
    ).first()
    if not junction:
        return api_error("Element not linked to this solution", 404)

    existing = dict(junction.spec_data or {})

    if new_status == "reset":
        existing["fields_status"] = "pending"
        existing.pop("fields", None)
    else:
        fields = body.get("fields")
        if not isinstance(fields, list):
            return api_error("fields must be a list", 400)
        # Validate each field has at minimum a name
        for f in fields:
            if not isinstance(f, dict) or not f.get("name"):
                return api_error("Each field must have a 'name'", 400)
        existing["fields"] = fields
        existing["fields_status"] = "confirmed"
        existing.setdefault("fields_version", 0)
        existing["fields_version"] = (existing.get("fields_version") or 0) + 1
        existing["confirmed_by"] = "architect"

    junction.spec_data = existing
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error("confirm_element_fields commit failed: %s", e)
        return api_error("Failed to save fields", 500)

    return api_success(data={
        "element_id": element_id,
        "fields_status": existing["fields_status"],
        "field_count": len(existing.get("fields") or []),
    })


# ── Schema Import (DDL / OpenAPI / CDS → confirmed fields) ───────────────────

def _normalize_name(s: str) -> str:
    """Strip non-alphanumeric chars and lowercase for fuzzy matching."""
    import re
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def _match_entity_to_element(entity_name: str, el_names: list) -> str | None:
    """Return the element name whose normalized form best matches entity_name."""
    norm = _normalize_name(entity_name)
    # Exact normalized match first
    for name in el_names:
        if _normalize_name(name) == norm:
            return name
    # Substring match (entity is contained in element name or vice versa)
    for name in el_names:
        n = _normalize_name(name)
        if norm and (norm in n or n in norm):
            return name
    return None


def _parse_sql_ddl(content: str) -> dict:
    """Parse CREATE TABLE DDL into {table_name: [{name, type, required, description}]}."""
    import re
    TYPE_MAP = {
        'int': 'integer', 'integer': 'integer', 'bigint': 'integer', 'smallint': 'integer',
        'tinyint': 'integer', 'mediumint': 'integer', 'serial': 'integer',
        'float': 'float', 'double': 'float', 'real': 'float',
        'numeric': 'decimal', 'decimal': 'decimal', 'money': 'decimal',
        'varchar': 'string', 'nvarchar': 'string', 'char': 'char',
        'nchar': 'string', 'clob': 'text', 'text': 'text', 'ntext': 'text',
        'boolean': 'boolean', 'bool': 'boolean', 'bit': 'boolean',
        'date': 'date', 'datetime': 'datetime', 'datetime2': 'datetime',
        'timestamp': 'datetime', 'timestamptz': 'datetime', 'time': 'string',
        'uuid': 'uuid', 'uniqueidentifier': 'uuid',
        'json': 'json', 'jsonb': 'json', 'xml': 'text', 'blob': 'text',
    }
    SKIP_NAMES = {'id', 'createdat', 'createdby', 'modifiedat', 'modifiedby', 'updatedat', 'updatedby'}
    result = {}
    create_re = re.compile(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\[]?(\w+)[`"\]]?\s*\(([^;]+?)\)\s*;',
        re.IGNORECASE | re.DOTALL,
    )
    for m in create_re.finditer(content):
        table_name = m.group(1)
        body = m.group(2)
        fields = []
        for line in re.split(r',\s*\n', body):
            line = line.strip().rstrip(',')
            if not line:
                continue
            if re.match(r'(PRIMARY|FOREIGN|UNIQUE|CHECK|KEY|INDEX|CONSTRAINT)\b', line, re.IGNORECASE):
                continue
            col_m = re.match(r'[`"\[]?(\w+)[`"\]]?\s+(\w+)', line)
            if not col_m:
                continue
            col_name, sql_type = col_m.group(1), col_m.group(2).lower()
            if _normalize_name(col_name) in SKIP_NAMES:
                continue
            field_type = TYPE_MAP.get(sql_type, 'string')
            required = bool(re.search(r'\bNOT\s+NULL\b', line, re.IGNORECASE))
            comment_m = re.search(r"COMMENT\s+'([^']*)'", line, re.IGNORECASE)
            description = comment_m.group(1) if comment_m else ''
            fields.append({'name': col_name, 'type': field_type, 'required': required, 'description': description})
        if fields:
            result[table_name] = fields
    return result


def _parse_openapi(content: str) -> dict:
    """Parse OpenAPI 2/3 YAML or JSON into {schema_name: [{name, type, required, description}]}.

    A parse failure propagates. The caller (`import_schema`) already catches it,
    logs it and answers "Parse failed: <reason>" with a 400. Returning {} sent
    the user "No entity or field definitions found in the provided schema"
    instead — telling them their file was empty when it was in fact unreadable.
    """
    import json as _json
    try:
        import yaml as _yaml
        spec = _yaml.safe_load(content)
    except Exception:
        spec = None
    if spec is None:
        spec = _json.loads(content)
    schemas = ((spec.get('components') or {}).get('schemas')
               or spec.get('definitions') or {})
    FORMAT_MAP = {'date-time': 'datetime', 'date': 'date', 'uuid': 'uuid', 'float': 'float', 'double': 'float'}
    TYPE_MAP = {'string': 'string', 'integer': 'integer', 'number': 'float', 'boolean': 'boolean'}
    SKIP = {'id', 'createdat', 'created_at', 'updatedat', 'updated_at', 'createdby', 'updatedby'}
    result = {}
    for schema_name, schema in schemas.items():
        if not isinstance(schema, dict):
            continue
        props = schema.get('properties') or {}
        if not props:
            continue
        required_set = set(schema.get('required') or [])
        fields = []
        for prop_name, prop in props.items():
            if not isinstance(prop, dict):
                continue
            if _normalize_name(prop_name) in SKIP:
                continue
            fmt = prop.get('format') or ''
            field_type = FORMAT_MAP.get(fmt) or TYPE_MAP.get(prop.get('type') or 'string') or 'string'
            fields.append({
                'name': prop_name,
                'type': field_type,
                'required': prop_name in required_set,
                'description': prop.get('description') or prop.get('title') or '',
            })
        if fields:
            result[schema_name] = fields
    return result


def _parse_cds(content: str) -> dict:
    """Parse SAP CDS entity definitions into {entity_name: [{name, type, required, description}]}."""
    import re
    CDS_TYPE_MAP = {
        'String': 'string', 'LargeString': 'text',
        'Integer': 'integer', 'Integer64': 'integer',
        'Decimal': 'decimal', 'Double': 'float', 'Decimal64': 'float',
        'Boolean': 'boolean',
        'Date': 'date', 'DateTime': 'datetime', 'Timestamp': 'datetime',
        'UUID': 'uuid', 'Binary': 'text',
    }
    SKIP = {'id', 'createdat', 'createdby', 'modifiedat', 'modifiedby', 'updatedat', 'updatedby'}
    result = {}
    entity_re = re.compile(r'entity\s+(\w+)(?:\s*:\s*[\w.]+)?\s*\{([^}]+)\}', re.DOTALL)
    for m in entity_re.finditer(content):
        entity_name, body = m.group(1), m.group(2)
        fields = []
        for stmt in body.split(';'):
            stmt = stmt.strip()
            if not stmt:
                continue
            field_m = re.match(r'(?:key\s+)?(\w+)\s*:\s*([\w(),.]+)', stmt)
            if not field_m:
                continue
            field_name = field_m.group(1)
            if _normalize_name(field_name) in SKIP:
                continue
            cds_type = field_m.group(2).split('(')[0]
            field_type = CDS_TYPE_MAP.get(cds_type, 'string')
            comment_m = re.search(r'//\s*(.+)$', stmt)
            description = comment_m.group(1).strip() if comment_m else ''
            fields.append({'name': field_name, 'type': field_type, 'required': False, 'description': description})
        if fields:
            result[entity_name] = fields
    return result


@journey_v2_bp.route("/<int:solution_id>/import-schema", methods=["POST"])
@login_required
@_require_solution_owner
def import_schema(solution_id):
    """Bulk-import field schemas from SQL DDL, OpenAPI YAML/JSON, or SAP CDS.

    Body: {"format": "sql_ddl"|"openapi"|"cds", "content": "<text>"}

    Parses entity/field definitions, fuzzy-matches entity names to linked
    ArchiMate data elements, and bulk-writes as confirmed fields. The LLM
    will not invent fields for confirmed elements — the imported schema is
    ground truth.

    Returns: {imported: N, matched: [...], unmatched: [...], total_parsed: N}
    """
    from app.models.archimate_core import ArchiMateElement
    body = request.get_json(silent=True) or {}
    fmt = (body.get("format") or "sql_ddl").strip()
    content = (body.get("content") or "").strip()

    if not content:
        return api_error("content is required", 400)
    if fmt not in ("sql_ddl", "openapi", "cds"):
        return api_error("format must be sql_ddl, openapi, or cds", 400)

    try:
        if fmt == "sql_ddl":
            parsed = _parse_sql_ddl(content)
        elif fmt == "openapi":
            parsed = _parse_openapi(content)
        else:
            parsed = _parse_cds(content)
    except Exception as e:
        logger.exception("Schema import parse error for solution %s", solution_id)
        return api_error(f"Parse failed: {e}", 400)

    if not parsed:
        return api_error("No entity or field definitions found in the provided schema", 400)

    links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
    if not links:
        return api_error("No ArchiMate elements linked to this solution yet — generate architecture first", 400)

    el_ids = [item.element_id for item in links]
    elements = {
        e.id: e
        for e in ArchiMateElement.query.filter(ArchiMateElement.id.in_(el_ids)).all()
        if e.type in _DATA_ELEMENT_TYPES
    }
    junction_by_el_id = {item.element_id: item for item in links}
    el_name_to_id = {e.name: e.id for e in elements.values()}

    matched, unmatched = [], []

    for entity_name, fields in parsed.items():
        matched_name = _match_entity_to_element(entity_name, list(el_name_to_id.keys()))
        if not matched_name:
            unmatched.append(entity_name)
            continue
        el_id = el_name_to_id[matched_name]
        junction = junction_by_el_id.get(el_id)
        if not junction:
            unmatched.append(entity_name)
            continue
        existing = dict(junction.spec_data or {})
        existing["fields"] = fields
        existing["fields_status"] = "schema_imported"
        existing["import_source"] = f"schema_import:{fmt}"
        existing["fields_version"] = (existing.get("fields_version") or 0) + 1
        junction.spec_data = existing
        matched.append({"entity": entity_name, "element": matched_name, "field_count": len(fields)})

    if matched:
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error("import_schema commit failed for solution %s: %s", solution_id, e)
            return api_error("Failed to save imported schemas", 500)

    return api_success(data={
        "imported": len(matched),
        "matched": matched,
        "unmatched": unmatched,
        "total_parsed": len(parsed),
    })


# ── Business Process Step Editor ──────────────────────────────────────────────

_BUSINESS_PROCESS_TYPES = frozenset({
    "BusinessProcess", "BusinessService", "BusinessFunction", "BusinessEvent",
})


@journey_v2_bp.route("/<int:solution_id>/element-steps", methods=["GET"])
@login_required
@_require_solution_owner
def list_element_steps(solution_id):
    """Return business-layer elements with their current process_steps for the Step Editor panel."""
    links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
    if not links:
        return jsonify({"elements": [], "summary": {"total": 0, "with_steps": 0, "empty": 0}})

    element_ids = [lk.element_id for lk in links]
    elements = ArchiMateElement.query.filter(
        ArchiMateElement.id.in_(element_ids),
        ArchiMateElement.type.in_(list(_BUSINESS_PROCESS_TYPES))
    ).all()

    link_map = {lk.element_id: lk for lk in links}
    result = []
    summary = {"total": 0, "with_steps": 0, "empty": 0}

    for el in elements:
        lk = link_map.get(el.id)
        spec = (lk.spec_data or {}) if lk else {}
        steps = spec.get("process_steps") or []
        summary["total"] += 1
        if steps:
            summary["with_steps"] += 1
        else:
            summary["empty"] += 1
        result.append({
            "element_id": el.id,
            "name": el.name or f"Element {el.id}",
            "type": el.type,
            "description": el.description or "",
            "process_steps": steps,
        })

    result.sort(key=lambda x: (not x["process_steps"], x["name"]))
    return jsonify({"elements": result, "summary": summary})


@journey_v2_bp.route("/<int:solution_id>/element/<int:element_id>/steps", methods=["PATCH"])
@login_required
@_require_solution_owner
def save_element_steps(solution_id, element_id):
    """Save architect-defined process steps to spec_data.process_steps.

    Body: {"steps": [{"actor": "...", "action": "...", "target": "...", "validation": "..."}, ...]}
    Each step must have a non-empty 'action'. Actor, target, validation are optional.
    """
    body = request.get_json(silent=True) or {}
    steps = body.get("steps")
    if not isinstance(steps, list):
        return api_error("steps must be a list", 400)
    for s in steps:
        if not isinstance(s, dict) or not s.get("action", "").strip():
            return api_error("Each step must have a non-empty 'action'", 400)

    el = ArchiMateElement.query.get(element_id)
    if not el or el.type not in _BUSINESS_PROCESS_TYPES:
        return api_error("Element is not a business process type", 400)

    junction = SolutionArchiMateElement.query.filter_by(
        solution_id=solution_id, element_id=element_id
    ).first()
    if not junction:
        return api_error("Element not linked to this solution", 404)

    existing = dict(junction.spec_data or {})
    existing["process_steps"] = steps
    junction.spec_data = existing
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error("save_element_steps commit failed: %s", e)
        return api_error("Failed to save steps", 500)

    return api_success(data={"element_id": element_id, "step_count": len(steps)})


@journey_v2_bp.route("/<int:solution_id>/ux-enrichment", methods=["GET"])
@login_required
@_require_solution_owner
def get_ux_enrichment(solution_id: int):
    """
    Return auto-inferred UX enrichment data for a solution.
    Read-only. Merges saved architect overrides on top of auto-inferred data.
    """
    from app.modules.codegen.services.ux_enrichment_service import build_ux_enrichment

    solution = Solution.query.get_or_404(solution_id)
    saved_prefs = dict(solution.ux_preferences or {})

    try:
        enrichment = build_ux_enrichment(solution_id)
        # Overlay architect overrides
        if saved_prefs.get("navigation", {}).get("tab_order"):
            enrichment["navigation"]["architect_tab_order"] = saved_prefs["navigation"]["tab_order"]
        if saved_prefs.get("design_system", {}).get("primary_color"):
            enrichment["design_system"]["primary_color"] = saved_prefs["design_system"]["primary_color"]
        if saved_prefs.get("security", {}).get("require_auth"):
            enrichment.setdefault("security", {})["require_auth"] = saved_prefs["security"]["require_auth"]
    except Exception as e:
        logger.error("get_ux_enrichment failed for solution %s: %s", solution_id, e)
        return api_error("Failed to build UX enrichment", 500)

    return jsonify(enrichment)


@journey_v2_bp.route("/<int:solution_id>/save-ux-preferences", methods=["POST"])
@login_required
@_require_solution_owner
def save_ux_preferences(solution_id: int):
    """
    Persist architect-confirmed UX preferences to Solution.ux_preferences.
    Accepts partial updates — only keys present in body are written.
    """
    solution = Solution.query.get_or_404(solution_id)

    data = request.get_json(silent=True) or {}
    prefs = dict(solution.ux_preferences or {})

    for key in ("navigation", "design_system", "security", "field_overrides"):
        if key in data:
            prefs[key] = data[key]

    solution.ux_preferences = prefs
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error("save_ux_preferences commit failed: %s", e)
        return api_error("Failed to save UX preferences", 500)

    return api_success(data={"ux_preferences": prefs}, message="UX preferences saved")
