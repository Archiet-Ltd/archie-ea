"""
DEPRECATED: This file is migrated to app/modules/architecture/.
Registration is now centralized via app.modules.architecture.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Architecture Review Board (ARB) Routes

Flask routes for ARB web interface and API endpoints.
Integrates with existing platform workflows and provides TOGAF-aligned governance.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib as _hashlib
import json as _json
import re as _re
from typing import Any
import uuid as _uuid

from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from app import db
from app.models.architecture_review_board import (
    ARBBoardMember,
    ARBCapabilityImpact,
    ARBGovernanceStandard,
    ARBReviewComment,
    ARBReviewItem,
    ArchitectureReviewBoard,
    ReviewType,
    TOGAFPhase,
)
from app.decorators import audit_log, require_roles
from app.services.arb_analytics_service import ARBAnalyticsService
from app.services.rate_limiter import rate_limit
from app.services.arb_governance_service import (
    ARBDecisionError,
    ARBGovernanceService,
    DECIDABLE_STATUSES,
    MissingApproverError,
    SelfApprovalError,
)
from app.modules.transformation_room.domain import (
    ActorContext,
    AuthenticationRequired,
    BlockedByEvidence,
    CommandConflict,
    KnownPreCommitTransient,
    NotAuthorised,
    NotFound,
    TransformationError,
)

arb_bp = Blueprint("arb", __name__, url_prefix="/arb")
arb_service = ARBGovernanceService()
arb_analytics = ARBAnalyticsService()


# V-03 (S1, 17 Aug 2026 QA register): a Viewer created two ARB reviews
# through two different creation endpoints (POST /arb/reviews/create and
# POST /arb/api/reviews) -- neither checked permission. Same root cause as
# V-02: protection here was route-by-route, not default. This hook is the
# blueprint-wide floor for every write under /arb (session, review, decision,
# comment, and every app/modules/architecture/routes/arb_*.py file, all of
# which share this one Blueprint instance) -- consistent with the
# blueprint-wide guard added to unified_applications_bp for the same finding
# class. Permission.GENERAL is the same bitfield ARBGovernanceService.record_
# decision already checks for the decider (85c2924); a Viewer (permissions=0)
# fails it here before a request ever reaches a route function, so record_
# decision's own check is now defence-in-depth rather than the only line.
@arb_bp.before_request
def _default_deny_unauthorized_arb_writes():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if not current_user.is_authenticated:
        return None
    from app.models.user import Permission

    if current_user.can(Permission.GENERAL):
        return None
    return jsonify({
        "success": False,
        "error": "Your role does not have write access to ARB governance.",
        "code": "PERMISSION_DENIED",
    }), 403


# =========================================================================
# TYPED ARB DECISION BOUNDARY (Lane L1)
#
# Every terminal decision on a typed ARB review cycle is routed through
# TypedARBDecisionService. This adapter is the only place a route builds the
# trusted command inputs: the actor comes from the authenticated session user
# and the resolved tenant, never from the request body, and cycle/review rows
# are resolved with explicit (id, organization_id) predicates rather than
# Query.get(), which is tenant-scoped only on an identity-map miss (AGENTS.md).
# =========================================================================

_COMMAND_KEY_PATTERN = _re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,199}\Z")

# Reason codes safe to hand back to a caller: none names another tenant's row,
# and none is raw exception text.
_SAFE_DECISION_CONFLICT_REASONS = frozenset(
    {
        "arb_cycle_already_terminal",
        "arb_cycle_review_projection_mismatch",
        "historical_unverified_cycle_not_decidable",
        "arb_decision_command_mismatch",
    }
)
_SAFE_DECISION_AUTHZ_REASONS = frozenset(
    {
        "arb_decision_separation_of_duties",
        "arb_decision_not_authorised",
    }
)

# Legacy vocabulary -> canonical typed outcome.
_TYPED_OUTCOME_ALIASES = {
    "approve": "approved",
    "approved": "approved",
    "approve_with_conditions": "approved_with_conditions",
    "approved_with_conditions": "approved_with_conditions",
    "request_changes": "approved_with_conditions",
    "request-changes": "approved_with_conditions",
    "reject": "rejected",
    "rejected": "rejected",
    "return_for_evidence": "returned_for_evidence",
    "returned_for_evidence": "returned_for_evidence",
    "return_for_options": "returned_for_options",
    "returned_for_options": "returned_for_options",
}


@dataclass
class TypedARBDecisionOutcome:
    """Route-facing result of a typed ARB decision command."""

    success: bool
    http_status: int = 200
    reason_codes: list = field(default_factory=list)
    missing_evidence: list = field(default_factory=list)
    request_id: str = ""
    review_cycle_id: Any = None
    review_item_id: Any = None
    decision_event_id: Any = None
    condition_ids: list = field(default_factory=list)
    conditions: Any = None
    status: Any = None
    outcome: Any = None
    idempotent: bool = False
    canonical_url: Any = None

    def success_fields(self):
        """Canonical identifiers added to every legacy success envelope."""
        return {
            "review_cycle_id": self.review_cycle_id,
            "review_item_id": self.review_item_id,
            "decision_event_id": self.decision_event_id,
            "condition_ids": list(self.condition_ids or []),
            "canonical_url": self.canonical_url,
            "status": self.status,
            "outcome": self.outcome,
            "idempotent": self.idempotent,
        }

    def failure_payload(self):
        return {
            "success": False,
            "reason_codes": list(self.reason_codes),
            "missing_evidence": list(self.missing_evidence),
            "request_id": self.request_id,
        }


class TypedARBDecisionAdapter:
    """Trusted caller boundary for typed ARB terminal decisions."""

    @staticmethod
    def normalize_outcome(value):
        if not isinstance(value, str):
            raise ValueError("decision outcome is required")
        outcome = _TYPED_OUTCOME_ALIASES.get(value.strip().lower())
        if outcome is None:
            raise ValueError("unsupported ARB decision outcome")
        return outcome

    @staticmethod
    def canonical_conditions(lines):
        """Turn legacy free-text condition lines into canonical objects.

        No due date and no ``pending`` state is invented here: the previous
        form parser manufactured ``utcnow() + 30 days``, which is fabricated
        data the reader cannot distinguish from a real board-set date.
        """
        conditions = []
        for ordinal, raw in enumerate(lines or (), start=1):
            if isinstance(raw, dict):
                description = (
                    raw.get("description") or raw.get("text") or raw.get("condition")
                )
                number = raw.get("condition_number") or raw.get("code") or f"COND-{ordinal}"
                category = raw.get("category")
                due_date = raw.get("due_date")
            else:
                description = raw
                number = f"COND-{ordinal}"
                category = None
                due_date = None
            if isinstance(description, str):
                description = description.strip()
            if not description:
                continue
            conditions.append(
                {
                    "condition_number": number,
                    "description": description,
                    "category": category,
                    "due_date": due_date,
                }
            )
        return conditions

    @staticmethod
    def current_organization_id():
        organization_id = getattr(g, "current_org_id", None)
        if not isinstance(organization_id, int) or organization_id <= 0:
            return None
        return organization_id

    @classmethod
    def actor(cls):
        """Build ActorContext from the session user and resolved tenant only.

        No caller-supplied actor, role, ``decided_by_id`` or
        ``organization_id`` is consulted here or anywhere downstream.
        """
        if not current_user.is_authenticated:
            raise AuthenticationRequired("not_authenticated")
        organization_id = cls.current_organization_id()
        if organization_id is None:
            raise NotAuthorised("arb_decision_not_authorised")
        from app.models.user import User

        user = db.session.execute(
            db.select(User).where(
                User.id == current_user.id,
                User.organization_id == organization_id,
            )
        ).scalar_one_or_none()
        if user is None:
            raise NotAuthorised("arb_decision_not_authorised")
        roles = {
            role
            for role in (
                user.enterprise_role,
                "organization_admin" if user.is_org_admin else None,
                "platform_admin" if user.is_platform_admin else None,
            )
            if role
        }
        return ActorContext(
            user_id=user.id,
            organization_id=organization_id,
            roles=frozenset(roles),
            request_id=request.headers.get("X-Request-ID") or str(_uuid.uuid4()),
        )

    @classmethod
    def load_review(cls, review_item_id, *, organization_id=None):
        """Resolve one review item with an explicit (id, organization_id) predicate."""
        organization_id = (
            organization_id
            if organization_id is not None
            else cls.current_organization_id()
        )
        if organization_id is None:
            return None
        return db.session.execute(
            db.select(ARBReviewItem).where(
                ARBReviewItem.id == review_item_id,
                ARBReviewItem.organization_id == organization_id,
            )
        ).scalar_one_or_none()

    @classmethod
    def typed_cycle_for_review(cls, review, *, organization_id=None):
        """Return the typed cycle owning ``review``, or None for a legacy row.

        The cycle is re-read by (id, organization_id): a review row is never
        trusted to name a cycle belonging to another tenant.
        """
        if review is None or getattr(review, "review_cycle_id", None) is None:
            return None
        from app.models.architecture_review_board import ARBReviewCycle

        organization_id = (
            organization_id
            if organization_id is not None
            else cls.current_organization_id()
        )
        if organization_id is None:
            return None
        return db.session.execute(
            db.select(ARBReviewCycle).where(
                ARBReviewCycle.id == review.review_cycle_id,
                ARBReviewCycle.organization_id == organization_id,
            )
        ).scalar_one_or_none()

    @classmethod
    def typed_cycles_for_solution(cls, solution_id, *, organization_id=None):
        from app.models.architecture_review_board import ARBReviewCycle

        organization_id = (
            organization_id
            if organization_id is not None
            else cls.current_organization_id()
        )
        if organization_id is None:
            return []
        return list(
            db.session.execute(
                db.select(ARBReviewCycle)
                .where(
                    ARBReviewCycle.organization_id == organization_id,
                    ARBReviewCycle.subject_type == "solution",
                    ARBReviewCycle.subject_id == solution_id,
                )
                .order_by(ARBReviewCycle.cycle_number.desc(), ARBReviewCycle.id.desc())
            ).scalars()
        )

    @classmethod
    def open_typed_cycle_for_solution(cls, solution_id, *, organization_id=None):
        for cycle in cls.typed_cycles_for_solution(
            solution_id, organization_id=organization_id
        ):
            if cycle.closed_at is None:
                return cycle
        return None

    @staticmethod
    def canonical_url(cycle):
        if cycle is None:
            return None
        subject_type = cycle.subject_type
        subject_id = cycle.subject_id
        if subject_type == "solution" and subject_id:
            return f"/solutions/{subject_id}?tab=governance"
        if subject_type == "adr" and subject_id:
            # E2E-H: was /architecture/adrs/records/<id>, a JSON-only
            # endpoint reading ArchitectureDecisionRecord (0 rows in
            # production). The real page is arch_decisions.view_decision.
            return f"/architecture/decisions/{subject_id}"
        if subject_type == "architecture_model":
            return "/architecture/models"
        return None

    @classmethod
    def command_key(cls, supplied, *, actor, cycle_id, outcome, rationale, conditions):
        if supplied is not None:
            if not isinstance(supplied, str) or not _COMMAND_KEY_PATTERN.fullmatch(
                supplied
            ):
                raise ValueError("invalid idempotency key")
            return supplied
        identity = _json.dumps(
            {
                "organization_id": actor.organization_id,
                "user_id": actor.user_id,
                "cycle_id": cycle_id,
                "outcome": outcome,
                "rationale": rationale,
                "conditions": conditions,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return f"arb-decision-{_hashlib.sha256(identity).hexdigest()}"

    @staticmethod
    def supplied_command_key():
        """A command key may arrive only by header or an explicit field.

        A browser-selected ``review_item_id`` is never an idempotency token.
        """
        key = request.headers.get("Idempotency-Key")
        if key:
            return key
        if request.is_json:
            body = request.get_json(silent=True) or {}
            if isinstance(body, dict) and body.get("idempotency_key"):
                return body.get("idempotency_key")
        return request.form.get("idempotency_key") or None

    @classmethod
    def decide(cls, *, cycle, outcome, rationale, conditions=None):
        from app.modules.transformation_room.arb_decision_service import (
            TypedARBDecisionService,
        )

        request_id = ""
        try:
            actor = cls.actor()
            request_id = actor.request_id
            canonical = cls.canonical_conditions(conditions)
            command_key = cls.command_key(
                cls.supplied_command_key(),
                actor=actor,
                cycle_id=cycle.id,
                outcome=outcome,
                rationale=rationale,
                conditions=canonical,
            )
            result = TypedARBDecisionService.decide(
                actor=actor,
                command_key=command_key,
                cycle_id=cycle.id,
                outcome=outcome,
                rationale=rationale,
                conditions=canonical or None,
            )
        except ValueError as error:
            reason = (
                "invalid_idempotency_key"
                if "idempotency" in str(error).lower()
                else "invalid_decision_request"
            )
            return TypedARBDecisionOutcome(False, 400, [reason], request_id=request_id)
        except TransformationError as error:
            return cls._failure(error, request_id=request_id)
        except Exception:
            current_app.logger.exception(
                "Typed ARB decision adapter failed for cycle %s",
                getattr(cycle, "id", None),
            )
            return TypedARBDecisionOutcome(
                False, 500, ["decision_failed"], request_id=request_id
            )

        response = dict(result.response)
        object_ids = dict(result.object_ids)
        return TypedARBDecisionOutcome(
            True,
            200,
            request_id=request_id,
            review_cycle_id=response.get("review_cycle_id")
            or object_ids.get("review_cycle_id"),
            review_item_id=response.get("review_item_id")
            or object_ids.get("review_item_id"),
            decision_event_id=response.get("decision_event_id")
            or object_ids.get("decision_event_id"),
            condition_ids=list(
                response.get("condition_ids") or object_ids.get("condition_ids") or []
            ),
            conditions=response.get("conditions"),
            status=response.get("status") or outcome,
            outcome=response.get("outcome") or outcome,
            idempotent=bool(result.idempotent),
            canonical_url=cls.canonical_url(cycle),
        )

    @staticmethod
    def _failure(error, *, request_id):
        if isinstance(error, AuthenticationRequired):
            return TypedARBDecisionOutcome(
                False, 401, ["not_authenticated"], request_id=request_id
            )
        if isinstance(error, NotFound):
            return TypedARBDecisionOutcome(
                False, 404, ["arb_review_cycle_not_found"], request_id=request_id
            )
        if isinstance(error, NotAuthorised):
            reason = (
                error.reason
                if error.reason in _SAFE_DECISION_AUTHZ_REASONS
                else "actor_not_authorized"
            )
            return TypedARBDecisionOutcome(False, 403, [reason], request_id=request_id)
        if isinstance(error, BlockedByEvidence):
            reason_codes = error.details.get("reason_codes")
            missing = error.details.get("missing_evidence")
            return TypedARBDecisionOutcome(
                False,
                422,
                list(reason_codes)
                if isinstance(reason_codes, list)
                else ["arb_subject_not_ready"],
                list(missing) if isinstance(missing, list) else [],
                request_id=request_id,
            )
        if isinstance(error, CommandConflict):
            reason = (
                error.reason
                if error.reason in _SAFE_DECISION_CONFLICT_REASONS
                else "decision_conflict"
            )
            return TypedARBDecisionOutcome(False, 409, [reason], request_id=request_id)
        if isinstance(error, KnownPreCommitTransient):
            return TypedARBDecisionOutcome(
                False, 503, ["decision_unconfirmed"], request_id=request_id
            )
        return TypedARBDecisionOutcome(
            False, 500, ["decision_failed"], request_id=request_id
        )


# Safe, status-specific copy for the HTML decision form. Exception text and
# any other tenant's identity are never surfaced.
_TYPED_DECISION_MESSAGES = {
    400: "That decision request could not be read. Check the outcome and rationale.",
    401: "Sign in again to record this decision.",
    403: (
        "You are not authorised to decide this review. A submitter cannot "
        "decide their own review."
    ),
    404: "Review item not found.",
    409: (
        "This review changed before your action was recorded. Reload the "
        "current review and try again."
    ),
    422: "This review is blocked by outstanding evidence and cannot be decided yet.",
    500: "The decision was not recorded.",
    503: "The command was not confirmed. Retry the decision.",
}


def _typed_operation_blocked(reason_code, message):
    """Uniform refusal for an operation typed services deliberately omit."""
    return jsonify(
        {
            "success": False,
            "error": message,
            "reason_codes": [reason_code],
            "missing_evidence": [],
            "request_id": str(_uuid.uuid4()),
        }
    ), 409


# =========================================================================
# ARB SESSION MANAGEMENT ROUTES
# =========================================================================


def _typed_decision_json(result, *, extra=None):
    if not result.success:
        return jsonify({
            "success": False,
            "reason_codes": result.reason_codes,
            "missing_evidence": result.missing_evidence,
            "request_id": request.headers.get("X-Request-ID") or str(_uuid.uuid4()),
        }), result.http_status
    payload = {
        "success": True,
        "item_id": result.review_item_id,
        "redirect_url": url_for("arb.review_detail", id=result.review_item_id),
        "review_item_id": result.review_item_id,
        "review_cycle_id": result.review_cycle_id,
        "decision_event_id": result.decision_event_id,
        "condition_ids": result.condition_ids,
        "status": result.status,
        "outcome": result.outcome,
        "conditions": result.conditions,
        "idempotent": result.idempotent,
        "canonical_url": result.canonical_url,
    }
    if extra:
        payload.update(extra)
    return jsonify(payload), 200


# ── typed ARB governance workspace wiring ────────────────────────────────────
# The typed read model, the typed partials and the Alpine component were each
# landed correctly, but nothing joined them: no view called the read model and
# no view passed `typed_queue`/`typed_review`, so arb/dashboard.html and
# arb/review_detail.html always fell through to their legacy branch and the
# whole typed workspace was unreachable in a browser. These two helpers are that
# join. A typed read failure is an explicit failed state; silently rendering the
# legacy body would make a broken release look healthy and expose stale actions.


def _typed_actor():
    """ActorContext from the session ONLY — never from the request.

    Mirrors arb_condition_routes._actor. An authenticated principal with no
    tenant cannot address a tenant row, so it gets no typed view at all.
    """
    from app.modules.transformation_room.domain import ActorContext

    if not getattr(current_user, "is_authenticated", False):
        return None
    user_id = getattr(current_user, "id", None)
    organization_id = getattr(current_user, "organization_id", None)
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        return None
    if (
        not isinstance(organization_id, int)
        or isinstance(organization_id, bool)
        or organization_id <= 0
    ):
        return None
    role = getattr(current_user, "enterprise_role", None)
    roles = frozenset({role}) if isinstance(role, str) and role else frozenset()
    return ActorContext(user_id, organization_id, roles, _uuid.uuid4().hex)


def _typed_queue_context():
    actor = _typed_actor()
    if actor is None:
        return None
    try:
        from app.modules.transformation_room.arb_read_models import (
            typed_arb_queue_view,
        )

        return typed_arb_queue_view(
            actor=actor,
            filters={
                "state": request.args.get("state"),
                "subject_type": request.args.get("subject_type"),
                "q": request.args.get("q"),
            },
            page=request.args.get("page", 1, type=int) or 1,
        )
    except Exception:
        current_app.logger.exception("typed ARB queue view failed")
        return {
            "state": "failed",
            "reason": "arb_queue_unavailable",
            "filters": {},
            "filter_options": {},
            "items": [],
            "page": None,
            "page_size": None,
            "total_items": None,
            "total_pages": None,
        }


def _typed_review_context(review_item_id):
    actor = _typed_actor()
    if actor is None:
        return None
    try:
        from app.modules.transformation_room.arb_read_models import (
            typed_arb_review_view,
        )

        return typed_arb_review_view(actor=actor, review_item_id=review_item_id)
    except Exception:
        current_app.logger.exception("typed ARB review view failed")
        return None


@arb_bp.route("/dashboard")
@login_required
def dashboard_redirect():
    """Redirect /arb/dashboard to canonical /arb/ URL.

    The `@arb_bp.route("/dashboard")` decorator used to sit above the block
    comment that introduces the typed-workspace helpers, so Flask bound the
    URL to the *next* function definition — `_typed_actor` — and GET
    /arb/dashboard returned an ActorContext instead of a response
    ("view function did not return a valid response ... it was a
    ActorContext"). The decorator belongs here, on the redirect it names.
    """
    return redirect(url_for("arb.dashboard"))


@arb_bp.route("/")
@login_required
def dashboard():
    """ARB Dashboard - Overview of all governance activities."""
    try:
        dashboard_data = arb_service.get_governance_dashboard()

        # Get user's specific items
        my_submitted = (
            ARBReviewItem.query.filter_by(submitter_id=current_user.id)
            .order_by(ARBReviewItem.created_at.desc())
            .limit(5)
            .all()
        )

        my_reviews = (
            ARBReviewItem.query.filter_by(reviewer_id=current_user.id)
            .order_by(ARBReviewItem.review_started_at.desc())
            .limit(5)
            .all()
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error loading ARB dashboard: {e}")
        dashboard_data = {
            "metrics": {
                "total_items": 0,
                "pending_items": 0,
                "approved_items": 0,
                "rejected_items": 0,
                "approval_rate": 0,
            },
            "recent_reviews": [],
            "upcoming_sessions": [],
            "review_types": [],
            "togaf_phases": [],
        }
        my_submitted = []
        my_reviews = []

    # Decision analytics data
    try:
        analytics_trends = arb_analytics.get_approval_trends(12)
        cycle_time = arb_analytics.get_cycle_time_analytics(90)
        standards_summary = arb_analytics.get_standard_compliance_summary()

        # Overdue items: submitted/under_review older than priority thresholds
        now = datetime.utcnow()
        overdue_thresholds = {"critical": 7, "high": 14, "medium": 21, "low": 30}
        overdue_items = []
        for priority_val, days_threshold in overdue_thresholds.items():
            cutoff = now - timedelta(days=days_threshold)
            items = (
                ARBReviewItem.query.filter(
                    ARBReviewItem.status.in_(["submitted", "under_review"]),
                    ARBReviewItem.priority == priority_val,
                    ARBReviewItem.submitted_at.isnot(None),
                    ARBReviewItem.submitted_at <= cutoff,
                )
                .order_by(ARBReviewItem.submitted_at.asc())
                .all()
            )
            for item in items:
                overdue_items.append(
                    {
                        "review": item,
                        "days_overdue": (now - item.submitted_at).days,
                        "threshold": days_threshold,
                    }
                )
        overdue_items.sort(
            key=lambda x: (
                {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(
                    x["review"].priority, 4
                ),
                -x["days_overdue"],
            )
        )

        # Capability impact summary
        from sqlalchemy import func as sa_func

        capability_impacts_raw = (
            db.session.query(
                ARBCapabilityImpact.capability_id,
                sa_func.count(ARBCapabilityImpact.id).label("review_count"),
            )
            .group_by(ARBCapabilityImpact.capability_id)
            .order_by(sa_func.count(ARBCapabilityImpact.id).desc())
            .limit(20)
            .all()
        )
        capability_impacts = []
        for cap_id, review_count in capability_impacts_raw:
            cap_impact = ARBCapabilityImpact.query.filter_by(
                capability_id=cap_id
            ).first()
            cap_name = (
                cap_impact.capability.name
                if cap_impact and cap_impact.capability
                else f"Capability {cap_id}"
            )
            # Count by impact level
            high_count = ARBCapabilityImpact.query.filter_by(
                capability_id=cap_id, impact_level="high"
            ).count()
            medium_count = ARBCapabilityImpact.query.filter_by(
                capability_id=cap_id, impact_level="medium"
            ).count()
            low_count = ARBCapabilityImpact.query.filter_by(
                capability_id=cap_id, impact_level="low"
            ).count()
            capability_impacts.append(
                {
                    "capability_id": cap_id,
                    "name": cap_name,
                    "review_count": review_count,
                    "high": high_count,
                    "medium": medium_count,
                    "low": low_count,
                }
            )
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error loading ARB analytics: {e}")
        analytics_trends = {"trends": []}
        cycle_time = {"avg_days": 0, "min_days": 0, "max_days": 0, "median_days": 0}
        standards_summary = {
            "total_standards": 0,
            "mandatory_count": 0,
            "standards": [],
        }
        overdue_items = []
        capability_impacts = []

    # Decisions list (recent decisions with recorded decisions) - WITH FILTERS
    try:
        # Get filter parameters
        search_query = request.args.get("search", "").strip()
        capability_id = request.args.get("capability_id", type=int)
        date_from = request.args.get("date_from")
        date_to = request.args.get("date_to")

        # Build query with filters
        query = ARBReviewItem.query.filter(ARBReviewItem.decision.isnot(None))

        # Apply search filter
        if search_query:
            query = query.filter(ARBReviewItem.title.ilike(f"%{search_query}%"))

        # Apply capability filter
        if capability_id:
            query = query.join(ARBReviewItem.capability_links).filter(
                ARBCapabilityImpact.capability_id == capability_id
            )

        # Apply date range filters
        if date_from:
            try:
                date_from_obj = datetime.strptime(date_from, "%Y-%m-%d")
                query = query.filter(ARBReviewItem.decision_date >= date_from_obj)
            except ValueError:
                pass  # Ignore invalid date format

        if date_to:
            try:
                date_to_obj = datetime.strptime(date_to, "%Y-%m-%d")
                # Add 1 day to include the entire end date
                date_to_obj = date_to_obj + timedelta(days=1)
                query = query.filter(ARBReviewItem.decision_date < date_to_obj)
            except ValueError:
                pass  # Ignore invalid date format

        # Apply eager loading and ordering
        decisions = (
            query.options(
                joinedload(ARBReviewItem.capability_links).joinedload(
                    ARBCapabilityImpact.capability
                ),
                joinedload(ARBReviewItem.solution),
                joinedload(ARBReviewItem.architecture_model),
                joinedload(ARBReviewItem.decided_by),
            )
            .order_by(ARBReviewItem.decision_date.desc())
            .limit(200)
            .all()
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error loading decisions: {e}")
        decisions = []

    # Resolve application names from capability_impacts.application_ids for each decision
    application_names_by_review = {}
    if decisions:
        from app.models.application_portfolio import ApplicationComponent
        all_app_ids = set()
        for rev in decisions:
            cap = rev.capability_impacts if isinstance(rev.capability_impacts, dict) else {}
            ids = cap.get("application_ids") or []
            for aid in ids:
                all_app_ids.add(int(aid))
        if all_app_ids:
            apps = ApplicationComponent.query.filter(ApplicationComponent.id.in_(all_app_ids)).all()
            app_id_to_name = {a.id: a.name for a in apps if a.name}
            for rev in decisions:
                cap = rev.capability_impacts if isinstance(rev.capability_impacts, dict) else {}
                ids = cap.get("application_ids") or []
                names = [app_id_to_name[int(aid)] for aid in ids if int(aid) in app_id_to_name]
                if names:
                    application_names_by_review[rev.id] = names

    # Get recent ARB sessions for dashboard
    try:
        page = request.args.get("page", 1, type=int)
        recent_sessions = ArchitectureReviewBoard.query.order_by(
            ArchitectureReviewBoard.scheduled_date.desc()
        ).paginate(page=page, per_page=10, error_out=False)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error loading recent ARB sessions: {e}")

        # Create a mock pagination-like object with empty items
        class EmptyPagination:
            items = []
            pages = 0
            page = 1
            has_next = False
            has_prev = False

        recent_sessions = EmptyPagination()

    # Pending reviews queue for the dashboard table
    try:
        pending_status = request.args.get("pending_status", "all")
        pending_query = ARBReviewItem.query.filter(
            ARBReviewItem.status.in_(["submitted", "pending", "under_review", "draft"])
        )
        if pending_status != "all":
            pending_query = pending_query.filter(ARBReviewItem.status == pending_status)
        pending_reviews = (
            pending_query
            .options(joinedload(ARBReviewItem.solution))
            .order_by(ARBReviewItem.created_at.desc())
            .limit(15)
            .all()
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error loading pending reviews: {e}")
        pending_reviews = []
        pending_status = "all"

    # ARB-101: compute per-row SLA badge data for decisions table
    _SLA_THRESHOLDS = {"critical": 7, "high": 14, "medium": 21, "low": 30}
    sla_data_by_review = {}
    _now = datetime.utcnow()
    for _rev in decisions:
        if _rev.submitted_at and _rev.priority:
            _sla_days = _SLA_THRESHOLDS.get(_rev.priority, 30)
            _days_pending = (_now - _rev.submitted_at).days
            _days_left = _sla_days - _days_pending
            _pct = max(0.0, _days_left / _sla_days * 100) if _sla_days else 0.0
            if _pct > 50:
                _cls = "bg-emerald-100 text-emerald-800"
                _txt = f"{max(0, _days_left)}d remaining"
            elif _pct >= 25:
                _cls = "bg-amber-100 text-amber-800"
                _txt = f"{max(0, _days_left)}d remaining"
            else:
                _cls = "bg-red-100 text-red-800"
                _txt = f"{abs(_days_left)}d overdue" if _days_left < 0 else f"{_days_left}d remaining"
            sla_data_by_review[_rev.id] = {"cls": _cls, "txt": _txt}

    typed_queue = _typed_queue_context()

    # ADR-0008 (store agreement). The KPI tiles above count arb_review_items
    # while the queue below reads the typed ARBReviewCycle graph, so a tenant
    # whose reviews predate typed submission saw "Total reviews 6 / Pending 2"
    # printed directly above "No typed ARB reviews yet" -- two queries answering
    # the same question with different answers on one screen.
    #
    # The tiles are not wrong: those rows exist. The queue is not wrong either:
    # they are not typed. So the list renders the rows the tiles counted,
    # labelled for what they are, instead of claiming there are none. Nothing is
    # repointed and no count is invented.
    generic_reviews = []
    if typed_queue and typed_queue.get("state") == "empty":
        try:
            generic_reviews = (
                ARBReviewItem.query.options(joinedload(ARBReviewItem.submitter))
                .order_by(ARBReviewItem.created_at.desc())
                .limit(15)
                .all()
            )
        except Exception:
            # Leave the list empty rather than fabricating rows; the tiles above
            # still show the counts and the failure is logged.
            db.session.rollback()
            current_app.logger.exception("ARB dashboard generic review list failed")

    response_status = 503 if typed_queue and typed_queue.get("state") == "failed" else 200
    return render_template(
        "arb/dashboard.html",
        # The dispatcher renders the typed queue whenever an actor exists. A
        # failed read remains typed and visible; it never resurrects legacy UI.
        typed_queue=typed_queue,
        generic_reviews=generic_reviews,
        sessions=recent_sessions,
        status=request.args.get("status", "all"),
        pending_reviews=pending_reviews,
        pending_status=pending_status,
        dashboard_data=dashboard_data,
        my_submitted=my_submitted,
        my_reviews=my_reviews,
        analytics_trends=analytics_trends,
        cycle_time=cycle_time,
        standards_summary=standards_summary,
        overdue_items=overdue_items,
        capability_impacts=capability_impacts,
        decisions=decisions,
        application_names_by_review=application_names_by_review,
        sla_data_by_review=sla_data_by_review,
    ), response_status


def _chair_candidates():
    """Active users for the session chair/secretary pickers.

    arb/sessions.html iterates `users` to populate the Schedule-Session modal;
    without it the required Chair select is empty and no session can be created.
    """
    try:
        from app.models.user import User
        return (
            User.query.filter_by(confirmed=True, organization_id=g.current_org_id)
            .order_by(User.first_name, User.last_name)
            .limit(200)
            .all()
        )
    except Exception as exc:
        current_app.logger.error("ARB chair candidates load failed: %s", exc)
        db.session.rollback()
        return []


@arb_bp.route("/sessions")
@login_required
def sessions():
    """List all ARB sessions."""
    page = request.args.get("page", 1, type=int)
    status = request.args.get("status", "all")

    try:
        query = ArchitectureReviewBoard.query

        if status != "all":
            query = query.filter_by(status=status)

        sessions = query.order_by(
            ArchitectureReviewBoard.scheduled_date.desc()
        ).paginate(page=page, per_page=20, error_out=False)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error loading ARB sessions: {e}")

        # Create a mock pagination-like object with empty items
        class EmptyPagination:
            items = []
            pages = 0
            page = 1
            has_next = False
            has_prev = False

        sessions = EmptyPagination()

    return render_template(
        "arb/sessions.html", sessions=sessions, status=status, users=_chair_candidates()
    )


@arb_bp.route("/sessions/create", methods=["GET", "POST"])
@login_required
def create_session():
    """Create a new ARB session."""
    if request.method == "POST":
        is_json = request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        try:
            data = request.get_json() if request.is_json else request.form.to_dict()

            if not data.get("name"):
                if is_json:
                    return jsonify({"success": False, "error": "Session name is required", "errors": {"name": "Session name is required"}}), 400
                flash("Session name is required.", "error")
                return redirect(url_for("arb.create_session"))

            if not data.get("scheduled_date"):
                if is_json:
                    return jsonify({"success": False, "error": "Scheduled date is required", "errors": {"scheduled_date": "Scheduled date is required"}}), 400
                flash("Scheduled date is required.", "error")
                return redirect(url_for("arb.create_session"))

            if not data.get("chair_id"):
                if is_json:
                    return jsonify({"success": False, "error": "Chair is required", "errors": {"chair_id": "Chair is required"}}), 400
                flash("Chair is required.", "error")
                return redirect(url_for("arb.create_session"))

            # Parse scheduled date — support both datetime-local (T separator) and space separator
            raw_date = data.get("scheduled_date", "").replace("T", " ")
            scheduled_date = datetime.strptime(raw_date, "%Y-%m-%d %H:%M")

            arb = arb_service.create_arb_session(
                name=data.get("name"),
                scheduled_date=scheduled_date,
                chair_id=int(data.get("chair_id")),
                description=data.get("description"),
                duration_minutes=int(data.get("duration_minutes") or 120),
                location=data.get("location"),
                meeting_link=data.get("meeting_link"),
                secretary_id=int(data.get("secretary_id"))
                if data.get("secretary_id")
                else None,
            )

            if is_json:
                return jsonify({"success": True, "id": arb.id, "board_number": arb.board_number}), 201

            flash(f"ARB session {arb.board_number} created successfully", "success")
            return redirect(url_for("arb.session_detail", id=arb.id))

        except ValueError:
            # The only user-correctable parse failure here is the date format.
            db.session.rollback()
            message = (
                "Scheduled date must be a date and time, for example 2026-09-15 14:00."
            )
            if is_json:
                return jsonify(
                    {"success": False, "error": message, "errors": {"scheduled_date": message}}
                ), 400
            flash(message, "error")
            return redirect(url_for("arb.sessions"))

        except Exception as e:
            # A failure here used to reach the user as nothing at all: the JSON
            # branch answered with an `errors` key the modal only read on a 400,
            # and the HTML branch flashed a message and then fell through to the
            # GET redirect below, which the browser followed away from the page
            # before anything rendered. Both branches now carry the reason, and
            # the HTML branch returns immediately so the flash survives as a
            # toast on /arb/sessions.
            current_app.logger.exception("Error creating ARB session")
            db.session.rollback()
            if is_json:
                return jsonify(
                    {
                        "success": False,
                        "error": "The ARB session could not be created.",
                        "errors": {"general": str(e)},
                    }
                ), 500
            flash(f"The ARB session could not be created: {e}", "error")
            return redirect(url_for("arb.sessions"))

    # GET — redirect to sessions list (modal handles creation inline)
    return redirect(url_for("arb.sessions"))


@arb_bp.route("/sessions/<int:id>")
@login_required
def session_detail(id):
    """View ARB session details."""
    session = ArchitectureReviewBoard.query.options(
        joinedload(ArchitectureReviewBoard.review_items),
        joinedload(ArchitectureReviewBoard.board_members).joinedload(
            ARBBoardMember.user
        ),
        joinedload(ArchitectureReviewBoard.chair),
        joinedload(ArchitectureReviewBoard.secretary),
    ).get_or_404(id)

    return render_template("arb/session_detail.html", session=session)


@arb_bp.route("/sessions/<int:id>/complete", methods=["POST"])
@login_required
@audit_log("arb_session_complete")
def complete_session(id):
    """Complete an ARB session."""
    try:
        # AUDIT-ARB-002: Quorum validation - require at least 3 board members
        arb_session = ArchitectureReviewBoard.query.get_or_404(id)
        member_count = (
            len(arb_session.board_members) if arb_session.board_members else 0
        )
        minimum_quorum = 3

        if member_count < minimum_quorum:
            flash(
                f"Cannot complete session: quorum not met. "
                f"At least {minimum_quorum} board members are required, "
                f"but only {member_count} member(s) are assigned.",
                "error",
            )
            return redirect(url_for("arb.session_detail", id=id))

        minutes = request.form.get("minutes")
        session = arb_service.complete_session(id, minutes)
        flash(f"ARB session {session.board_number} completed successfully", "success")
        return redirect(url_for("arb.session_detail", id=id))
    except Exception as e:
        current_app.logger.error(f"Error completing ARB session: {e}")
        flash("Error completing session. Please try again.", "error")
        return redirect(url_for("arb.session_detail", id=id))


@arb_bp.route("/sessions/<int:id>/cancel", methods=["POST"])
@login_required
@audit_log("arb_session_cancel")
def cancel_session(id):
    """Cancel a scheduled/draft ARB session.

    session_detail.html has rendered a Cancel button via
    url_for('arb.cancel_session') since PLT-era — the endpoint was never
    implemented, so every detail page for scheduled/draft sessions 500'd
    on the url_for BuildError.
    """
    try:
        arb_session = ArchitectureReviewBoard.query.get_or_404(id)
        if arb_session.status not in ("scheduled", "draft"):
            flash("Only scheduled or draft sessions can be cancelled.", "error")
            return redirect(url_for("arb.session_detail", id=id))
        arb_session.status = "cancelled"
        db.session.commit()
        flash(f"ARB session {arb_session.board_number} cancelled.", "success")
        return redirect(url_for("arb.sessions"))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error cancelling ARB session: {e}")
        flash("Error cancelling session. Please try again.", "error")
        return redirect(url_for("arb.session_detail", id=id))


@arb_bp.route("/sessions/<int:session_id>/add_member", methods=["POST"])
@login_required
@require_roles("admin", "enterprise_architect")
@audit_log("arb_member_add")
def add_board_member(session_id):
    """Add a member to an ARB session."""
    try:
        data = request.form.to_dict()
        (arb_service.add_board_member(
            arb_session_id=session_id,
            user_id=int(data.get("user_id")),
            role=data.get("role"),
            voting_member=data.get("voting_member") == "on",
        ))
        flash("Board member added successfully", "success")
    except Exception as e:
        current_app.logger.error(f"Error adding board member: {e}")
        flash("Error adding member. Please try again.", "error")

    return redirect(url_for("arb.session_detail", id=session_id))


# =========================================================================
# REVIEW ITEM MANAGEMENT ROUTES
# =========================================================================


@arb_bp.route("/reviews")
@login_required
def reviews():
    """List all review items."""
    page = request.args.get("page", 1, type=int)
    status = request.args.get("status", "all")
    review_type = request.args.get("review_type", "all")

    query = ARBReviewItem.query

    if status != "all":
        query = query.filter_by(status=status)

    if review_type != "all":
        query = query.filter_by(review_type=review_type)

    pagination = query.order_by(ARBReviewItem.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )

    # Pass is_reviews_view=True so the template renders review-specific
    # labels and links (review_detail instead of session_detail).
    return render_template(
        "arb/sessions.html",
        sessions=pagination,
        reviews=pagination.items,
        status=status,
        review_type=review_type,
        is_reviews_view=True,
        users=_chair_candidates(),
    )


@arb_bp.route("/review/new")
@login_required
def review_new_redirect():
    """Redirect legacy /arb/review/new to the canonical /arb/reviews/create."""
    return redirect(url_for("arb.create_review"))


class _ReviewValidationError(ValueError):
    """Raised by _create_arb_review_item for a 400-shaped validation failure."""

    def __init__(self, field: str, message: str, *, code: str | None = None):
        super().__init__(message)
        self.field = field
        self.message = message
        self.code = code


class _TypedSubmissionError(RuntimeError):
    def __init__(self, result):
        super().__init__("Typed ARB submission was rejected")
        self.result = result


def _typed_submission_payload(result):
    return {
        "review_id": result.review_item_id,
        "review_item_id": result.review_item_id,
        "review_number": result.review_number,
        "snapshot_id": result.snapshot_id,
        "review_cycle_id": result.review_cycle_id,
        "canonical_url": result.canonical_url,
        "idempotent": result.idempotent,
    }


def _typed_submission_error_payload(result):
    return {
        "success": False,
        "reason_codes": result.reason_codes,
        "missing_evidence": result.missing_evidence,
    }


# V-03: the single place review-creation payloads are parsed and validated.
# POST /arb/reviews/create (create_review, the HTML/modal path) and
# POST /arb/api/reviews (api_create_review, the JSON API path) used to each
# hand-roll an ~80-line copy of this logic — two independently maintained
# implementations of the same validation rules, which is how the two-different-
# creation-endpoints finding could exist without anyone noticing they'd
# drifted. Both routes now call this one function; they differ only in how
# they format their own success/error responses (redirect+flash vs JSON),
# which is a legitimate difference between an HTML-form endpoint and a JSON
# API endpoint, not a reason to keep two copies of the business logic.
def _create_arb_review_item(data: dict) -> ARBReviewItem:
    # Solution reviews have a stronger evidence contract than generic ADR/model
    # reviews.  This modal cannot collect or preserve that dossier, so it must
    # never manufacture a solution-linked review item.
    selected_subjects = [
        (subject_type, field_name, data.get(field_name))
        for subject_type, field_name in (
            ("solution", "solution_id"),
            ("adr", "adr_id"),
            ("architecture_model", "architecture_model_id"),
        )
        if data.get(field_name) not in (None, "")
    ]
    if len(selected_subjects) > 1:
        raise _ReviewValidationError(
            "subject",
            "Select exactly one governed subject for an ARB submission.",
            code="exactly_one_subject_required",
        )
    if selected_subjects and selected_subjects[0][0] == "solution":
        raise _ReviewValidationError(
            "solution_id",
            "Submit solutions through the canonical evidence-gated submission endpoint.",
        )
    if selected_subjects:
        from app.modules.transformation_room.arb_submission_adapter import (
            TypedARBSubmissionAdapter,
        )

        subject_type, _field_name, raw_subject_id = selected_subjects[0]
        try:
            subject_id = int(raw_subject_id)
        except (TypeError, ValueError) as error:
            raise _ReviewValidationError(
                "subject", "Governed subject ID must be a positive integer."
            ) from error
        if subject_id <= 0:
            raise _ReviewValidationError(
                "subject", "Governed subject ID must be a positive integer."
            )
        result = TypedARBSubmissionAdapter.submit_subject_from_request(
            subject_type=subject_type,
            subject_id=subject_id,
            payload=data,
        )
        if not result.success:
            raise _TypedSubmissionError(result)
        return result
    review_type = data.get("review_type")
    capability_required_types = ["solution_design", "capability_implementation", "technology_selection"]

    decision_sought_val = (data.get("decision_sought") or "").strip()
    if not decision_sought_val:
        raise _ReviewValidationError("decision_sought", "Decision sought is required.")

    capability_impacts = []
    raw_impacts = data.get("capability_impacts")
    if raw_impacts and isinstance(raw_impacts, list):
        for imp in raw_impacts:
            cap_id = imp.get("capability_id") if isinstance(imp, dict) else imp
            if cap_id:
                raw_impact = imp.get("impact_type", "modifies") if isinstance(imp, dict) else "modifies"
                capability_impacts.append({
                    "capability_id": int(cap_id),
                    "impact_type": _normalize_impact_type(raw_impact),
                    "impact_level": imp.get("impact_level", "medium") if isinstance(imp, dict) else "medium",
                    "level": imp.get("level") if isinstance(imp, dict) else None,
                })
    if not capability_impacts and data.get("capability_ids"):
        raw = data.get("capability_ids")
        ids = [int(i) for i in raw] if isinstance(raw, list) else [int(i.strip()) for i in str(raw).split(",") if str(i).strip()]
        default_impact = data.get("capability_impact_type") or "modifies"
        for cap_id in ids:
            capability_impacts.append({"capability_id": cap_id, "impact_type": _normalize_impact_type(default_impact), "impact_level": "medium"})

    if review_type in capability_required_types and not capability_impacts:
        raise _ReviewValidationError(
            "capability_ids",
            f"At least one capability is required for {review_type.replace('_', ' ')} reviews.",
        )

    application_ids = []
    if data.get("application_ids"):
        raw = data.get("application_ids")
        application_ids = [int(i) for i in raw] if isinstance(raw, list) else [int(i.strip()) for i in str(raw).split(",") if str(i).strip()]

    return arb_service.submit_for_review(
        title=data.get("title"),
        description=data.get("description"),
        review_type=review_type,
        submitter_id=current_user.id,
        togaf_phase=data.get("togaf_phase") or None,
        archimate_layer=data.get("archimate_layer") or None,
        solution_id=int(data.get("solution_id")) if data.get("solution_id") else None,
        adr_id=int(data.get("adr_id")) if data.get("adr_id") else None,
        architecture_model_id=int(data.get("architecture_model_id")) if data.get("architecture_model_id") else None,
        priority=data.get("priority", "medium"),
        business_impact=data.get("business_impact", "medium"),
        estimated_effort=data.get("estimated_effort", "medium"),
        capability_ids=None,
        decision_sought=decision_sought_val or None,
        alternatives_considered=data.get("alternatives_considered") or None,
        application_ids=application_ids or None,
        capability_impacts=capability_impacts if capability_impacts else None,
    )


@arb_bp.route("/reviews/create", methods=["GET", "POST"])
@login_required
@audit_log("arb_review_create")
@rate_limit(10, "1h")
def create_review():
    """Create a new review item."""
    if request.method == "POST":
        is_json = request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        try:
            data = request.get_json() if request.is_json else request.form.to_dict()
            review_item = _create_arb_review_item(data)

            if is_json:
                if hasattr(review_item, "review_item_id"):
                    typed = _typed_submission_payload(review_item)
                    return jsonify(
                        {"success": True, "id": review_item.review_item_id, **typed}
                    ), 201
                return jsonify({"success": True, "id": review_item.id, "review_number": review_item.review_number}), 201

            review_id = getattr(review_item, "review_item_id", None) or review_item.id
            review_number = getattr(review_item, "review_number", None)
            flash(
                f"Review item {review_number} created successfully",
                "success",
            )
            return redirect(url_for("arb.review_detail", id=review_id))

        except _ReviewValidationError as e:
            if is_json:
                payload = {"success": False, "errors": {e.field: e.message}}
                if e.code:
                    payload["reason_codes"] = [e.code]
                return jsonify(payload), 400
            flash(e.message, "error")
            return redirect(url_for("arb.dashboard"))

        except _TypedSubmissionError as error:
            if is_json:
                return jsonify(
                    _typed_submission_error_payload(error.result)
                ), error.result.http_status
            flash("The governed subject is not ready for ARB submission.", "error")
            return redirect(url_for("arb.dashboard"))

        except Exception as e:
            current_app.logger.error(f"Error creating review item: {e}")
            if is_json:
                return jsonify({"success": False, "errors": {"general": str(e)}}), 500
            flash("Error creating review. Please try again.", "error")

    # GET — no standalone create page exists; creation is an inline modal on the
    # dashboard. A bare redirect looked like the empty-state CTA "did nothing"
    # (QA 01 Sep 2026), so tell the user where the form is and open it.
    flash(
        "Use the “Create Review Item” button to submit a new ARB review.",
        "info",
    )
    return redirect(url_for("arb.dashboard"))


@arb_bp.route("/reviews/<int:id>")
@login_required
def review_detail(id):
    """View review item details."""
    review = ARBReviewItem.query.options(
        joinedload(ARBReviewItem.submitter),
        joinedload(ARBReviewItem.reviewer),
        joinedload(ARBReviewItem.decided_by),
        joinedload(ARBReviewItem.solution),
        joinedload(ARBReviewItem.adr),
        joinedload(ARBReviewItem.architecture_model),
        joinedload(ARBReviewItem.capability_links).joinedload(
            ARBCapabilityImpact.capability
        ),
        joinedload(ARBReviewItem.comments).joinedload(ARBReviewComment.user),
    ).get_or_404(id)

    # Resolve application names from capability_impacts.application_ids
    application_names = []
    cap_impacts = review.capability_impacts if isinstance(review.capability_impacts, dict) else {}
    app_ids = cap_impacts.get("application_ids") or []
    if app_ids:
        from app.models.application_portfolio import ApplicationComponent
        apps = ApplicationComponent.query.filter(ApplicationComponent.id.in_(app_ids)).all()
        application_names = [a.name for a in apps if a.name]

    # IA-012: enrich with canonical impact analysis for first app in scope
    canonical_impact = None
    if app_ids:
        try:
            from app.modules.ai_chat.services.ai_impact_analysis_service import AIImpactAnalysisService
            raw = AIImpactAnalysisService().analyze_application_impact(
                app_id=app_ids[0], scenario="modification"
            )
            if raw:
                ra = raw.get("risk_assessment") or {}
                # None, not "LOW", and not 0. This renders on a page where a
                # reviewer signs off risk, and an unassessed risk presented as
                # LOW is indistinguishable from an assessed one. The previous
                # `or` chain collapsed None and "" to "LOW" as well as a missing
                # key. Templates render None as an em dash.
                canonical_impact = {
                    "risk_level": ra.get("risk_level") or raw.get("risk_level"),
                    "total_score": ra.get("total_score"),
                    "breakdown": ra.get("breakdown") or {},
                    "app_count": len(app_ids),
                }
        except Exception:
            # The enrichment is best-effort, but its ABSENCE must be visible:
            # canonical_impact stays as it was rather than being filled with
            # confident-looking defaults.
            logger.exception("ARB impact enrichment failed for app_id=%s", app_ids[0])

    # ARB-101: compute SLA banner info for review detail
    _SLA_THRESHOLDS = {"critical": 7, "high": 14, "medium": 21, "low": 30}
    sla_info = None
    if review.submitted_at and review.status in ("submitted", "under_review", "pending_info"):
        _sla_days = _SLA_THRESHOLDS.get(review.priority or "low", 30)
        _days_pending = (datetime.utcnow() - review.submitted_at).days
        _days_left = _sla_days - _days_pending
        _pct = max(0.0, _days_left / _sla_days * 100) if _sla_days else 0.0
        if _pct > 50:
            _bg = "bg-emerald-50 border-emerald-200"
            _icon = "text-emerald-600"
            _txt = f"{max(0, _days_left)} days remaining"
        elif _pct >= 25:
            _bg = "bg-amber-50 border-amber-200"
            _icon = "text-amber-600"
            _txt = f"{max(0, _days_left)} days remaining"
        else:
            _bg = "bg-red-50 border-red-200"
            _icon = "text-red-600"
            _txt = f"{abs(_days_left)} days overdue" if _days_left < 0 else f"{_days_left} days remaining"
        sla_info = {
            "bg": _bg, "icon": _icon, "txt": _txt,
            "sla_days": _sla_days, "days_pending": _days_pending,
            "priority": review.priority or "low",
        }

    # ARB-102: build conditions_with_flags for approved_with_conditions tracker
    conditions_with_flags = []
    if review.decision == "approved_with_conditions" and review.conditions:
        today = datetime.utcnow().date().isoformat()
        for c in (review.conditions if isinstance(review.conditions, list) else []):
            entry = dict(c) if isinstance(c, dict) else {"condition": str(c)}
            due = entry.get("due_date")
            entry["overdue"] = bool(
                due and entry.get("status") != "done" and due < today
            )
            conditions_with_flags.append(entry)

    # Bug 2: fetch active architecture principles to display in review detail
    applicable_principles = []
    try:
        from app.models.models import Principle
        applicable_principles = (
            Principle.query
            .filter(Principle.status == "approved")
            .order_by(Principle.category, Principle.name)
            .limit(20)
            .all()
        )
    except Exception:
        pass  # principles table may not yet be populated — degrade gracefully

    # ARCH-092: surface the immutable audit trail on the review itself, not
    # just in a service nobody calls. Best-effort — the audit trail is a
    # secondary view and must never 500 the primary review page.
    audit_trail = []
    try:
        from app.services.arb_audit_service import ARBAuditService

        audit_trail = ARBAuditService().get_entity_history("review_item", id, limit=200)
    except Exception:
        current_app.logger.exception(f"Failed to load audit trail for review {id}")

    typed_review = _typed_review_context(id)
    response_status = 503 if typed_review and typed_review.get("state") == "failed" else 200
    return render_template(
        "arb/review_detail.html",
        # Same dispatch contract as the queue: typed workspace when the read
        # model resolves this review for the current tenant, legacy otherwise.
        typed_review=typed_review,
        review=review,
        application_names=application_names,
        canonical_impact=canonical_impact,
        sla_info=sla_info,
        conditions_with_flags=conditions_with_flags,
        applicable_principles=applicable_principles,
        audit_trail=audit_trail,
        # The template must not re-derive which states can be decided: the
        # service owns that set, and the two drifted (the page offered a
        # decision on `pending_info`, which record_decision refuses, and
        # withheld it on `deferred`, which it accepts).
        decidable_statuses=sorted(DECIDABLE_STATUSES),
    ), response_status


@arb_bp.route("/reviews/<int:id>/audit-trail.csv")
@login_required
def review_audit_trail_csv(id):
    """ARCH-092: export the immutable audit trail for one review item as CSV.

    Reads the same stored ARBAuditLog rows the review detail page shows —
    a read-only export, it writes nothing and cannot alter the review.
    """
    import csv
    import io

    from flask import Response

    review = ARBReviewItem.query.get_or_404(id)

    from app.services.arb_audit_service import ARBAuditService

    logs = ARBAuditService().get_entity_history("review_item", id, limit=1000)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["timestamp_utc", "action", "actor_email", "old_value", "new_value", "description"]
    )
    for log in logs:
        writer.writerow(
            [
                log.timestamp.isoformat() if log.timestamp else "",
                log.action,
                log.user_email or "",
                log.old_value or "",
                log.new_value or "",
                log.action_description or "",
            ]
        )

    filename = f"{review.review_number}-audit-trail.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@arb_bp.route("/reviews/<int:id>/submit", methods=["POST"])
@login_required
@audit_log("arb_review_submit")
@rate_limit(10, "1h")
def submit_review(id):
    """Submit a draft review item for ARB consideration."""
    try:
        review = ARBReviewItem.query.get_or_404(id)
        if review.solution_id or review.adr_id or review.architecture_model_id:
            flash(
                "Typed reviews must be submitted from their governed evidence dossier.",
                "error",
            )
            return redirect(url_for("arb.review_detail", id=id))
        review = arb_service.submit_item(id)
        flash(f"Review item {review.review_number} submitted successfully", "success")
        return redirect(url_for("arb.review_detail", id=id))
    except Exception as e:
        current_app.logger.error(f"Error submitting review: {e}")
        flash("Error submitting review. Please try again.", "error")
        return redirect(url_for("arb.review_detail", id=id))


@arb_bp.route("/reviews/<int:id>/assign", methods=["POST"])
@login_required
@audit_log("arb_review_assign")
def assign_to_session(id):
    """Assign review item to an ARB session."""
    try:
        data = request.form.to_dict()
        arb_service.assign_to_session(id, int(data.get("arb_session_id")))
        flash("Review item assigned to ARB session", "success")
        return redirect(url_for("arb.review_detail", id=id))
    except Exception as e:
        current_app.logger.error(f"Error assigning to session: {e}")
        flash("Error assigning to session. Please try again.", "error")
        return redirect(url_for("arb.review_detail", id=id))


@arb_bp.route("/reviews/<int:id>/decision", methods=["POST"])
@login_required
@audit_log("arb_decision_record")
def record_decision(id):
    """Record ARB decision for a review item.

    A typed review cycle is decided only by ``TypedARBDecisionService``; the
    legacy branch below stays for generic, untyped historical review rows.
    """
    try:
        data = request.form.to_dict()

        from app.modules.transformation_room.arb_decision_adapter import (
            TypedARBDecisionAdapter,
        )

        typed_result = TypedARBDecisionAdapter.decide_review_from_request(
            review_item_id=id,
            payload=data,
        )
        if typed_result.typed:
            if typed_result.success:
                flash("ARB decision recorded against the pinned review evidence.", "success")
                return redirect(url_for("arb.review_detail", id=id))
            flash("The typed ARB decision could not be recorded.", "error")
            return redirect(url_for("arb.review_detail", id=id)), typed_result.http_status

        # Preserve the legacy ingress without fabricating a due date.
        conditions = [
            {"condition": line, "status": "pending", "due_date": None}
            for line in (data.get("conditions") or "").splitlines()
            if line.strip()
        ]

        # The date the board looks at this decision again. Optional: blank
        # stays blank ("—" on the page), never a date the board did not set.
        raw_review_date = (data.get("review_date") or "").strip()
        review_date = None
        if raw_review_date:
            try:
                review_date = datetime.strptime(raw_review_date, "%Y-%m-%d").date()
            except ValueError:
                flash("The review date must be a date (YYYY-MM-DD).", "error")
                return redirect(url_for("arb.review_detail", id=id)), 400

        try:
            review = arb_service.record_decision(
                review_item_id=id,
                decision=data.get("decision"),
                rationale=data.get("rationale"),
                decided_by_id=current_user.id,
                conditions=conditions if conditions else None,
                review_date=review_date,
            )
        except SelfApprovalError as e:
            current_app.logger.warning(
                f"Self-approval attempt blocked on review {id} by user {current_user.id}: {e}"
            )
            flash(
                "You submitted this review, so you cannot also record its decision. "
                "A separate approver must decide it.",
                "error",
            )
            return redirect(url_for("arb.review_detail", id=id)), 403
        except MissingApproverError as e:
            current_app.logger.warning(f"Decision refused on review {id}: {e}")
            flash("Decision refused: no approver could be identified.", "error")
            return redirect(url_for("arb.review_detail", id=id)), 403
        except ARBDecisionError as e:
            current_app.logger.warning(f"Decision refused on review {id}: {e}")
            flash(str(e), "error")
            return redirect(url_for("arb.review_detail", id=id)), 403

        # Sync ARB decision to capability (if this is a capability review)
        try:
            from app.services.arb_integration_service import ARBIntegrationService

            integration_service = ARBIntegrationService()
            capability = integration_service.sync_arb_decision_to_capability(id)
            if capability:
                flash(f"ARB decision synced to capability: {capability.name}", "info")
        except Exception as sync_error:
            current_app.logger.warning(
                f"Failed to sync ARB decision to capability: {sync_error}"
            )
            # Don't fail the entire request if sync fails

        # Sync ARB decision back to linked solutions
        try:
            from app.models.truly_missing_models import Solution

            decision_value = data.get("decision")
            linked_solutions = Solution.query.filter_by(arb_review_item_id=id).all()
            for sol in linked_solutions:
                if decision_value == "approved":
                    sol.governance_status = "approved"
                    sol.arb_approval_date = datetime.utcnow()
                elif decision_value == "rejected":
                    sol.governance_status = "rejected"
                    sol.arb_rejection_reason = data.get("rationale", "")
                elif decision_value == "deferred":
                    sol.governance_status = "proposed"
                elif decision_value == "approved_with_conditions":
                    sol.governance_status = "approved"
                    sol.arb_approval_date = datetime.utcnow()
            if linked_solutions:
                db.session.commit()
                current_app.logger.info(
                    f"Synced ARB decision '{decision_value}' to {len(linked_solutions)} solution(s)"
                )
        except Exception as sol_sync_error:
            current_app.logger.warning(
                f"Failed to sync ARB decision to solutions: {sol_sync_error}"
            )

        flash(f"Decision recorded for review item {review.review_number}", "success")
        return redirect(url_for("arb.review_detail", id=id))

    except Exception as e:
        current_app.logger.error(f"Error recording decision: {e}")
        flash("Error recording decision. Please try again.", "error")
        return redirect(url_for("arb.review_detail", id=id))


@arb_bp.route("/reviews/<int:id>/reopen", methods=["POST"])
@login_required
@audit_log("arb_decision_reopen")
def reopen_decision(id):
    """Reopen a previously recorded ARB decision.

    Allows the original decision maker or an admin to revert a decision
    back to 'under_review' status. Creates an audit log entry recording
    who reopened the decision and why.
    """
    try:
        from app.modules.transformation_room.arb_decision_adapter import (
            TypedARBDecisionAdapter as CommandDecisionAdapter,
        )
        from app.modules.transformation_room.domain import NotFound

        try:
            if CommandDecisionAdapter.review_is_typed(id):
                flash(
                    "Typed ARB decisions are append-only and cannot be reopened.",
                    "error",
                )
                return redirect(url_for("arb.review_detail", id=id)), 409
        except NotFound:
            pass

        from app.models.architecture_review_board import ARBAuditAction, ARBAuditLog

        # The module-local adapter owns the legacy tenant-scoped read helpers;
        # the command adapter above deliberately exposes only typed commands.
        LegacyReviewAdapter = TypedARBDecisionAdapter

        # Explicit (id, organization_id) predicate: Session.get() is scoped
        # only on an identity-map miss, so it is not a tenancy boundary.
        review = LegacyReviewAdapter.load_review(id)
        if not review:
            flash("Review item not found.", "error")
            return redirect(url_for("arb.reviews")), 404

        # Typed decision events are append-only: no typed service exposes a
        # reopen command, so a typed cycle can never be reverted here.
        if LegacyReviewAdapter.typed_cycle_for_review(review) is not None:
            flash(
                "This review is governed by a typed ARB cycle. Typed decisions "
                "are append-only and cannot be reopened; submit a new review "
                "cycle instead.",
                "error",
            )
            return redirect(url_for("arb.review_detail", id=id)), 409

        # Only allow reopen if a decision has been recorded
        if not review.decision:
            flash("No decision has been recorded for this review item.", "warning")
            return redirect(url_for("arb.review_detail", id=id))

        # Authorization: only the original decision maker or admin can reopen
        is_decision_maker = review.decided_by_id == current_user.id
        is_admin = (
            getattr(current_user, "is_admin", False)
            or getattr(current_user, "role", "") == "admin"
        )

        if not is_decision_maker and not is_admin:
            flash(
                "Only the original decision maker or an admin can reopen a decision.",
                "error",
            )
            return redirect(url_for("arb.review_detail", id=id))

        reason = request.form.get("reopen_reason", "").strip()
        if not reason:
            flash("A reason for reopening the decision is required.", "warning")
            return redirect(url_for("arb.review_detail", id=id))

        # Capture previous state for audit trail
        previous_decision = review.decision
        previous_status = review.status
        previous_rationale = review.decision_rationale

        # Revert the review item to under_review status
        review.status = "under_review"
        review.decision = None
        review.decision_rationale = None
        review.decision_date = None
        review.decided_by_id = None
        review.conditions = None
        review.review_completed_at = None

        # Create audit log entry
        audit_entry = ARBAuditLog(
            entity_type="ARBReviewItem",
            entity_id=review.id,
            entity_reference=review.review_number,
            action=ARBAuditAction.DECISION_REOPEN.value,
            action_description=(
                f"Decision reopened by {current_user.email}. "
                f"Previous decision: {previous_decision} "
                f"(status: {previous_status}). Reason: {reason}"
            ),
            old_value={
                "decision": previous_decision,
                "status": previous_status,
                "rationale": previous_rationale,
            },
            new_value={
                "decision": None,
                "status": "under_review",
                "reopen_reason": reason,
            },
            changed_fields=[
                "decision",
                "status",
                "decision_rationale",
                "decision_date",
                "decided_by_id",
            ],
            user_id=current_user.id,
            user_email=getattr(current_user, "email", None),
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent", "")[:500],
        )
        db.session.add(audit_entry)

        db.session.commit()
        flash(
            f"Decision for {review.review_number} has been reopened. "
            f"Previous decision ({previous_decision}) has been reverted.",
            "success",
        )
        return redirect(url_for("arb.review_detail", id=id))

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error reopening decision for review {id}: {e}")
        flash("Error reopening decision. Please try again.", "error")
        return redirect(url_for("arb.review_detail", id=id))


@arb_bp.route("/reviews/<int:id>/comment", methods=["POST"])
@login_required
@audit_log("arb_comment_add")
def add_comment(id):
    """Add comment to review item."""
    try:
        data = request.form.to_dict()

        comment = ARBReviewComment(
            review_item_id=id,
            user_id=current_user.id,
            comment_type=data.get("comment_type", "general"),
            content=data.get("content"),
        )

        db.session.add(comment)
        db.session.commit()

        flash("Comment added successfully", "success")

    except Exception as e:
        current_app.logger.error(f"Error adding comment: {e}")
        flash("Error adding comment. Please try again.", "error")

    return redirect(url_for("arb.review_detail", id=id))


# =========================================================================
# GOVERNANCE STANDARDS ROUTES
# =========================================================================


@arb_bp.route("/standards")
@login_required
def standards():
    """List governance standards."""
    category = request.args.get("category", "all")

    standards = arb_service.get_governance_standards(category)

    return render_template("arb/standards.html", standards=standards, category=category)


@arb_bp.route("/standards/<int:id>")
@login_required
def standard_detail(id):
    """View governance standard details."""
    standard = ARBGovernanceStandard.query.get_or_404(id)
    return render_template("arb/standard_detail.html", standard=standard)


@arb_bp.route("/decisions")
@login_required
def decision_register_page():
    """ARBU-001: Capability-based decision register page."""
    from app.models.architecture_decision import (
        ArchitectureDecision,
        VALID_HORIZONS, VALID_AUTHORITY_LEVELS,
        VALID_STATUSES, VALID_DECISION_TYPES
    )
    try:
        decisions = ArchitectureDecision.query.order_by(
            ArchitectureDecision.created_at.desc()
        ).limit(200).all()
    except Exception:
        decisions = []
    return render_template(
        'arb/decisions.html',
        decisions=decisions,
        valid_horizons=VALID_HORIZONS,
        valid_authority_levels=VALID_AUTHORITY_LEVELS,
        valid_statuses=VALID_STATUSES,
        valid_decision_types=VALID_DECISION_TYPES,
    )


@arb_bp.route("/change-requests")
@login_required
def change_request_list():
    """List Phase H change requests (wires arb/change_requests.html)."""
    from app.models.architecture_decision import ArchitectureChangeRequest
    change_requests = ArchitectureChangeRequest.query.order_by(
        ArchitectureChangeRequest.raised_at.desc()
    ).all()
    return render_template("arb/change_requests.html", change_requests=change_requests)


@arb_bp.route("/change-requests/<int:cr_id>")
@login_required
def change_request_detail(cr_id):
    """Detail view for a single change request (wires arb/change_request_detail.html)."""
    from app.models.architecture_decision import ArchitectureChangeRequest
    change_request = ArchitectureChangeRequest.query.get_or_404(cr_id)
    # The template refers to this as `cr` throughout; passing it as
    # `change_request` raised UndefinedError, so every row of the change-request
    # list at arb/change_requests.html:106 linked to a 500.
    return render_template("arb/change_request_detail.html", cr=change_request)


@arb_bp.route("/change-requests/new", methods=["GET", "POST"])
@login_required
def change_request_new():
    """New change request form (wires arb/change_request_form.html)."""
    from app.models.architecture_decision import (
        ArchitectureChangeRequest,
        VALID_TRIGGER_TYPES,
    )
    if request.method == "POST":
        data = request.form or request.get_json(silent=True) or {}
        title = data.get("title") or ""
        description = data.get("description") or ""
        trigger_type = data.get("trigger_type") or "reactive"
        if trigger_type not in VALID_TRIGGER_TYPES:
            trigger_type = "reactive"
        cr = ArchitectureChangeRequest(
            acr_reference=ArchitectureChangeRequest.next_acr_reference(),
            title=title,
            description=description,
            trigger_type=trigger_type,
            status="open",
        )
        db.session.add(cr)
        db.session.commit()
        return redirect(url_for("arb.change_request_detail", cr_id=cr.id))
    return render_template(
        "arb/change_request_form.html",
        change_request=None,
        valid_trigger_types=VALID_TRIGGER_TYPES,
    )


@arb_bp.route("/capabilities/<int:capability_id>/governance")
@login_required
def capability_governance_page(capability_id):
    """Governance panel page for a capability (wires arb/capability_governance.html)."""
    from app.models.unified_capability import UnifiedCapability
    cap = UnifiedCapability.query.get_or_404(capability_id)
    return render_template(
        "arb/capability_governance.html",
        capability_id=capability_id,
        capability_name=cap.name or "Capability",
        capability_description=getattr(cap, "description", None) or "",
    )


# =========================================================================
# API ENDPOINTS
# =========================================================================


@arb_bp.route("/api/reviews/<int:id>/assess")
@login_required
def api_assess_review(id):
    """API endpoint to assess review item scores."""
    try:
        compliance_score = arb_service.assess_compliance(id)
        risk_score = arb_service.assess_risk(id)
        quality_score = arb_service.assess_quality(id)
        overall_score = arb_service.calculate_overall_score(id)

        return jsonify(
            {
                "success": True,
                "compliance_score": compliance_score,
                "risk_score": risk_score,
                "quality_score": quality_score,
                "overall_score": overall_score,
            }
        )

    except ValueError as e:
        # service raises ValueError("Review item <id> not found") for a missing review
        return jsonify({"success": False, "error": str(e)}), 404
    except Exception as e:
        current_app.logger.error(f"Error assessing review: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_bp.route("/api/reviews/<int:id>/checklist", methods=["POST"])
@login_required
@audit_log("arb_checklist_update")
def api_update_checklist(id):
    """API endpoint to update governance checklist."""
    try:
        data = request.get_json()
        checklist_items = data.get("checklist", {})

        review = db.session.get(ARBReviewItem, id)
        if not review:
            return jsonify({"success": False, "error": "Review not found"}), 404

        # Update checklist
        if not review.governance_checklist:
            review.governance_checklist = {}

        review.governance_checklist.update(checklist_items)
        db.session.commit()

        # Recalculate scores
        compliance_score = arb_service.assess_compliance(id)
        overall_score = arb_service.calculate_overall_score(id)

        return jsonify(
            {
                "success": True,
                "compliance_score": compliance_score,
                "overall_score": overall_score,
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error updating checklist: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_bp.route("/api/capability/<int:capability_id>/reviews")
@login_required
def api_capability_reviews(capability_id):
    """API endpoint to get reviews affecting a capability."""
    try:
        # A capability that does not exist must 404, not answer with an empty
        # list. This was the last of 78 routes that returned 200 for id
        # 999999999: {"success": true, "reviews": []} is indistinguishable from
        # "this capability has no reviews", so a caller cannot tell a typo from
        # a clean governance record -- the fabricated-data rule, arriving as an
        # empty collection instead of a wrong number.
        from app.models.business_capabilities import BusinessCapability
        from app.utils.route_guards import require_entity_json

        _capability, missing = require_entity_json(
            BusinessCapability, capability_id, label="Capability"
        )
        if missing:
            return missing

        reviews = arb_service.get_pending_reviews_by_capability(capability_id)
        return jsonify(
            {
                "success": True,
                "reviews": [
                    review.to_dict(include_details=False) for review in reviews
                ],
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error getting capability reviews: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_bp.route("/api/dashboard")
@login_required
def api_dashboard():
    """API endpoint for dashboard data."""
    try:
        dashboard_data = arb_service.get_governance_dashboard()
        return jsonify({"success": True, "data": dashboard_data})
    except Exception as e:
        current_app.logger.error(f"Error getting dashboard data: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_bp.route("/api/solution/<int:solution_id>/submit_review", methods=["POST"])
@login_required
@audit_log("arb_solution_review_submit")
def api_submit_solution_review(solution_id):
    """API endpoint to auto-submit solution for ARB review."""
    from app.modules.transformation_room.arb_submission_adapter import (
        TypedARBSubmissionAdapter,
    )

    data = request.get_json(silent=True) or {}
    result = TypedARBSubmissionAdapter.submit_solution_from_request(
        solution_id=solution_id,
        payload=data,
    )
    if not result.success:
        return jsonify({"success": False, "reason_codes": result.reason_codes,
                        "missing_evidence": result.missing_evidence}), result.http_status
    return jsonify({"success": True, "review_id": result.review_item_id,
                    "review_item_id": result.review_item_id,
                    "review_number": result.review_number, "snapshot_id": result.snapshot_id,
                    "idempotent": result.idempotent,
                    "review_cycle_id": result.review_cycle_id,
                    "canonical_url": result.canonical_url})


@arb_bp.route("/api/adr/<int:adr_id>/submit_review", methods=["POST"])
@login_required
@audit_log("arb_adr_review_submit")
def api_submit_adr_review(adr_id):
    """API endpoint to auto-submit ADR for ARB review."""
    from app.modules.transformation_room.arb_submission_adapter import (
        TypedARBSubmissionAdapter,
    )

    result = TypedARBSubmissionAdapter.submit_subject_from_request(
        subject_type="adr",
        subject_id=adr_id,
        payload=request.get_json(silent=True) or {},
    )
    if not result.success:
        return jsonify(_typed_submission_error_payload(result)), result.http_status
    return jsonify({"success": True, **_typed_submission_payload(result)})


# =========================================================================
# JSON API FOR MODAL FORM
# =========================================================================


@arb_bp.route("/api/reviews", methods=["GET"])
@login_required
def api_list_reviews():
    """API endpoint to list review items with optional filtering."""
    try:
        page = request.args.get("page", 1, type=int)
        per_page = min(request.args.get("per_page", 20, type=int), 100)
        status = request.args.get("status")
        review_type = request.args.get("review_type")

        query = ARBReviewItem.query

        if status and status != "all":
            query = query.filter_by(status=status)
        if review_type and review_type != "all":
            query = query.filter_by(review_type=review_type)

        pagination = query.order_by(ARBReviewItem.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

        return jsonify(
            {
                "success": True,
                "reviews": [r.to_dict() for r in pagination.items],
                "total": pagination.total,
                "page": page,
                "per_page": per_page,
                "pages": pagination.pages,
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error listing reviews via API: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_bp.route("/api/reviews", methods=["POST"])
@login_required
@audit_log("arb_review_create_api")
def api_create_review():
    """API endpoint to create a review via modal form.

    V-03: thin JSON wrapper around the same _create_arb_review_item used by
    the HTML form path (create_review, above) -- this used to be an
    independent ~80-line copy of that parsing/validation logic. Response
    shape (review_id/redirect_url, versus create_review's id/review_number)
    is kept exactly as it was, since the frontend JS that calls this endpoint
    depends on it.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        review_item = _create_arb_review_item(data)

        if hasattr(review_item, "review_item_id"):
            typed = _typed_submission_payload(review_item)
            return jsonify(
                {
                    "success": True,
                    **typed,
                    "redirect_url": url_for(
                        "arb.review_detail", id=review_item.review_item_id
                    ),
                }
            )

        return jsonify(
            {
                "success": True,
                "review_id": review_item.id,
                "review_number": review_item.review_number,
                "redirect_url": url_for("arb.review_detail", id=review_item.id),
            }
        )

    except _ReviewValidationError as e:
        payload = {"success": False, "errors": {e.field: e.message}}
        if e.code:
            payload["reason_codes"] = [e.code]
        return jsonify(payload), 400

    except _TypedSubmissionError as error:
        return jsonify(
            _typed_submission_error_payload(error.result)
        ), error.result.http_status

    except Exception as e:
        current_app.logger.error(f"Error creating review via API: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# Decision types the ARB can be asked to approve (capability-based governance)
ARB_DECISION_TYPES = [
    {"value": "approve_vendor_selection", "label": "Approve Vendor/Product Selection"},
    {"value": "approve_new_application", "label": "Approve New Application/Capability"},
    {"value": "approve_enhancement", "label": "Approve Enhancement/Investment"},
    {"value": "approve_retirement", "label": "Approve Retirement/Consolidation"},
    {"value": "approve_exception", "label": "Approve Exception to Standard"},
    {"value": "approve_migration", "label": "Approve Migration Plan"},
    {"value": "approve_integration_pattern", "label": "Approve Integration Pattern"},
    {"value": "other", "label": "Other (describe in justification)"},
]

# Allowed impact_type values (validate and default to modifies if invalid)
ARB_IMPACT_TYPE_VALUES = {"enhances", "replaces", "deprecates", "new_implementation", "modifies"}


def _normalize_impact_type(val):
    """Return val if valid, else 'modifies'."""
    return val if val in ARB_IMPACT_TYPE_VALUES else "modifies"


# Capability impact types (ARBCapabilityImpact.impact_type)
ARB_IMPACT_TYPES = [
    {"value": "enhances", "label": "Enhances"},
    {"value": "replaces", "label": "Replaces"},
    {"value": "deprecates", "label": "Deprecates"},
    {"value": "new_implementation", "label": "New Implementation"},
    {"value": "modifies", "label": "Modifies"},
]


@arb_bp.route("/api/form-data")
@login_required
def api_form_data():
    """API endpoint to get form data for create review modal."""
    try:
        from app.models.architecture_decision import ArchitectureDecision
        from app.models.application_portfolio import ApplicationComponent
        from app.models.models import ArchitectureModel
        from app.models.truly_missing_models import Solution
        from app.models.unified_capability import UnifiedCapability

        solutions = Solution.query.order_by(Solution.name).limit(200).all()
        # E2E-H: this dropdown read ArchitectureDecisionRecord, a model with
        # 0 rows in production -- the Decision Register a user actually
        # reaches (/architecture/decisions/new) writes ArchitectureDecision.
        # Same class of defect as the risk-rollup fix earlier this session
        # (029f59a9): two models answering "what is an ADR", one live, one
        # dead. Confirmed 0 ArchitectureDecisionRecord / 7 ArchitectureDecision
        # rows in production before switching, so nothing is orphaned.
        adrs = (
            ArchitectureDecision.query.order_by(
                ArchitectureDecision.created_at.desc()
            )
            .limit(50)
            .all()
        )
        architecture_models = (
            ArchitectureModel.query.order_by(ArchitectureModel.name).limit(200).all()
        )
        capabilities = (
            UnifiedCapability.query.order_by(UnifiedCapability.name).limit(500).all()
        )
        applications = (
            ApplicationComponent.query.order_by(ApplicationComponent.name)
            .limit(300)
            .all()
        )

        return jsonify(
            {
                "success": True,
                "solutions": [{"id": s.id, "name": s.name} for s in solutions],
                "adrs": [
                    {"id": a.id, "adr_number": a.decision_id, "title": a.title}
                    for a in adrs
                ],
                "architecture_models": [
                    {"id": m.id, "name": m.name} for m in architecture_models
                ],
                "capabilities": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "level": getattr(c, "level", None),
                        "specialization_type": getattr(c, "specialization_type", None),
                    }
                    for c in capabilities
                ],
                "applications": [
                    {"id": a.id, "name": a.name} for a in applications
                ],
                "review_types": [
                    {"value": t.value, "label": t.value.replace("_", " ").title()}
                    for t in ReviewType
                ],
                "togaf_phases": [
                    {"value": p.value, "label": p.value.replace("_", " ").title()}
                    for p in TOGAFPhase
                ],
                "decision_types": ARB_DECISION_TYPES,
                "impact_types": ARB_IMPACT_TYPES,
                "capability_required_review_types": [
                    "solution_design",
                    "capability_implementation",
                    "technology_selection",
                ],
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error getting form data: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =========================================================================
# ANALYTICS API
# =========================================================================


@arb_bp.route("/api/decision-analytics")
@login_required
def api_decision_analytics():
    """API endpoint for comprehensive decision analytics data."""
    try:
        period = request.args.get("period", 90, type=int)
        report = arb_analytics.generate_comprehensive_report(period)
        return jsonify({"success": True, "data": report})
    except Exception as e:
        current_app.logger.error(f"Error getting decision analytics: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =========================================================================
# ADM KANBAN API
# =========================================================================


@arb_bp.route("/api/adm-kanban")
@login_required
def api_adm_kanban():
    """API endpoint returning review items grouped by TOGAF ADM phase for Kanban board."""
    try:
        # Define ADM phases in order
        adm_phases = [
            {
                "code": "preliminary",
                "name": "Preliminary",
                "short": "Prelim",
                "order": 0,
            },
            {
                "code": "phase_a_vision",
                "name": "Phase A: Architecture Vision",
                "short": "A",
                "order": 1,
            },
            {
                "code": "phase_b_business",
                "name": "Phase B: Business Architecture",
                "short": "B",
                "order": 2,
            },
            {
                "code": "phase_c_information_systems",
                "name": "Phase C: Information Systems",
                "short": "C",
                "order": 3,
            },
            {
                "code": "phase_d_technology",
                "name": "Phase D: Technology Architecture",
                "short": "D",
                "order": 4,
            },
            {
                "code": "phase_e_opportunities",
                "name": "Phase E: Opportunities & Solutions",
                "short": "E",
                "order": 5,
            },
            {
                "code": "phase_f_migration",
                "name": "Phase F: Migration Planning",
                "short": "F",
                "order": 6,
            },
            {
                "code": "phase_g_implementation",
                "name": "Phase G: Implementation Governance",
                "short": "G",
                "order": 7,
            },
            {
                "code": "phase_h_change_management",
                "name": "Phase H: Change Management",
                "short": "H",
                "order": 8,
            },
            {
                "code": "requirements_management",
                "name": "Requirements Management",
                "short": "REQ",
                "order": 9,
            },
        ]

        # Build columns with review items
        columns = []
        for phase in adm_phases:
            items = (
                ARBReviewItem.query.filter_by(togaf_phase=phase["code"])
                .order_by(
                    db.case(
                        (ARBReviewItem.priority == "critical", 0),
                        (ARBReviewItem.priority == "high", 1),
                        (ARBReviewItem.priority == "medium", 2),
                        (ARBReviewItem.priority == "low", 3),
                        else_=4,
                    ),
                    ARBReviewItem.created_at.desc(),
                )
                .all()
            )

            total = len(items)
            completed = sum(
                1 for i in items if i.status in ("approved", "approved_with_conditions")
            )
            in_review = sum(1 for i in items if i.status == "under_review")

            cards = []
            for item in items:
                cards.append(
                    {
                        "id": item.id,
                        "review_number": item.review_number,
                        "title": item.title,
                        "status": item.status,
                        "priority": item.priority,
                        "review_type": item.review_type,
                        "submitter": (
                            f"{item.submitter.first_name} {item.submitter.last_name}"
                            if item.submitter
                            else "Unknown"
                        ),
                        "created_at": item.created_at.isoformat()
                        if item.created_at
                        else None,
                        "overall_score": item.overall_score,
                    }
                )

            columns.append(
                {
                    "phase_code": phase["code"],
                    "phase_name": phase["name"],
                    "phase_short": phase["short"],
                    "order": phase["order"],
                    "total": total,
                    "completed": completed,
                    "in_review": in_review,
                    "cards": cards,
                }
            )

        # Summary stats
        total_items = sum(c["total"] for c in columns)
        total_completed = sum(c["completed"] for c in columns)
        phases_with_items = sum(1 for c in columns if c["total"] > 0)

        return jsonify(
            {
                "success": True,
                "columns": columns,
                "summary": {
                    "total_items": total_items,
                    "total_completed": total_completed,
                    "phases_with_items": phases_with_items,
                    "completion_rate": round(total_completed / total_items * 100, 1)
                    if total_items > 0
                    else 0,
                },
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error loading ADM Kanban data: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_bp.route("/api/adm-kanban/move-card", methods=["POST"])
@login_required
@audit_log("adm_kanban_move_card")
def api_adm_kanban_move_card():
    """API endpoint to move a review item to a different TOGAF ADM phase (drag-and-drop)."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        review_id = data.get("review_id")
        target_phase = data.get("target_phase")

        if not review_id or not target_phase:
            return jsonify(
                {"success": False, "error": "Missing review_id or target_phase"}
            ), 400

        # Validate phase code
        valid_phases = {p.value for p in TOGAFPhase}
        if target_phase not in valid_phases:
            return jsonify(
                {"success": False, "error": f"Invalid phase: {target_phase}"}
            ), 400

        review = db.session.get(ARBReviewItem, review_id)
        if not review:
            return jsonify({"success": False, "error": "Review item not found"}), 404

        old_phase = review.togaf_phase
        review.togaf_phase = target_phase
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "review_id": review.id,
                "old_phase": old_phase,
                "new_phase": target_phase,
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error moving Kanban card: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =========================================================================
# DELETE ROUTES
# =========================================================================


@arb_bp.route("/sessions/<int:id>/delete", methods=["POST"])
@login_required
@audit_log("arb_session_delete")
def delete_session(id):
    """Delete an ARB session. Only draft/cancelled sessions can be deleted."""
    try:
        session = ArchitectureReviewBoard.query.get_or_404(id)

        # Only allow deletion of draft or cancelled sessions
        if session.status not in ("draft", "cancelled"):
            flash(
                "Only draft or cancelled sessions can be deleted. "
                "Complete or cancel the session first.",
                "error",
            )
            return redirect(url_for("arb.session_detail", id=id))

        # Check if session has review items assigned
        review_count = ARBReviewItem.query.filter_by(arb_session_id=id).count()
        if review_count > 0:
            flash(
                f"Cannot delete session with {review_count} review item(s) assigned. "
                "Remove review items first.",
                "error",
            )
            return redirect(url_for("arb.session_detail", id=id))

        # Remove board members first (cascade may not cover all cases)
        ARBBoardMember.query.filter_by(arb_session_id=id).delete()

        board_number = session.board_number
        db.session.delete(session)
        db.session.commit()

        current_app.logger.info(
            f"ARB session {board_number} (id={id}) deleted by user {current_user.id}"
        )
        flash(f"ARB session {board_number} deleted successfully", "success")
        return redirect(url_for("arb.sessions"))

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting ARB session {id}: {e}")
        flash("Error deleting session. Please try again.", "error")
        return redirect(url_for("arb.session_detail", id=id))


@arb_bp.route("/reviews/<int:id>/delete", methods=["POST"])
@login_required
@audit_log("arb_review_delete")
def delete_review(id):
    """Delete an ARB review item. Only draft/withdrawn items can be deleted."""
    try:
        review = ARBReviewItem.query.get_or_404(id)

        # Only allow deletion of draft or withdrawn reviews
        if review.status not in ("draft", "withdrawn"):
            flash(
                "Only draft or withdrawn review items can be deleted. "
                "Withdraw the review first.",
                "error",
            )
            return redirect(url_for("arb.review_detail", id=id))

        # Only submitter can delete their own review
        if review.submitter_id != current_user.id:
            flash("Only the original submitter can delete a review item.", "error")
            return redirect(url_for("arb.review_detail", id=id))

        # Remove related records
        ARBCapabilityImpact.query.filter_by(review_item_id=id).delete()
        ARBReviewComment.query.filter_by(review_item_id=id).delete()

        review_number = review.review_number
        db.session.delete(review)
        db.session.commit()

        current_app.logger.info(
            f"ARB review {review_number} (id={id}) deleted by user {current_user.id}"
        )
        flash(f"Review item {review_number} deleted successfully", "success")
        return redirect(url_for("arb.reviews"))

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting ARB review {id}: {e}")
        flash("Error deleting review. Please try again.", "error")
        return redirect(url_for("arb.review_detail", id=id))


# =========================================================================
# UTILITY ROUTES
# =========================================================================

# Import decision CRUD/register routes — adds them to arb_bp before registration (side-effect import)
from app.modules.architecture.routes import arb_decision_routes  # noqa: F401  # dead-code-ok
# Import document attachment routes — adds upload/download endpoints to arb_bp (side-effect import)
from app.modules.architecture.routes import arb_document_routes  # noqa: F401  # dead-code-ok
# Import reviewer-side AI pre-brief route — adds POST /api/reviews/<id>/ai-prebrief (side-effect import)
from app.modules.architecture.routes import arb_review_ai_routes  # noqa: F401  # dead-code-ok
# Import queue-clerk AI routes — adds queue triage + session agenda/minutes draft endpoints (side-effect import)
from app.modules.architecture.routes import arb_queue_ai_routes  # noqa: F401  # dead-code-ok
import logging
logger = logging.getLogger(__name__)


@arb_bp.route("/initialize_standards", methods=["POST"])
@login_required
def initialize_standards():
    """Initialize default governance standards."""
    try:
        arb_service.initialize_governance_standards()
        flash("Governance standards initialized successfully", "success")
    except Exception as e:
        current_app.logger.error(f"Error initializing standards: {e}")
        flash("Error initializing standards. Please try again.", "error")

    return redirect(url_for("arb.standards"))


# =========================================================================
# ENH-020: ARB Review Item API Lifecycle Endpoints
# POST /api/arb/<id>/review  - move to under_review
# POST /api/arb/<id>/approve - approve review item
# POST /api/arb/<id>/reject  - reject review item
# =========================================================================


def _typed_api_item(item_id: int):
    """Resolve a review item and its typed cycle, both tenant-scoped.

    Returns ``(item, cycle, error_response)``. A row in another tenant is
    indistinguishable from a missing row: both are a bare 404.
    """
    item = TypedARBDecisionAdapter.load_review(item_id)
    if item is None:
        return None, None, (
            jsonify(
                {
                    "success": False,
                    "error": "Review item not found.",
                    "reason_codes": ["arb_review_item_not_found"],
                    "missing_evidence": [],
                    "request_id": str(_uuid.uuid4()),
                }
            ),
            404,
        )
    return item, TypedARBDecisionAdapter.typed_cycle_for_review(item), None


def _typed_decision_api_response(result, item, *, legacy_fields=None):
    """One envelope shape for every typed decision API caller."""
    if not result.success:
        return jsonify(result.failure_payload()), result.http_status
    payload = {
        "success": True,
        "item_id": item.id,
        "redirect_url": url_for("arb.review_detail", id=item.id),
        **(legacy_fields or {}),
        **result.success_fields(),
    }
    return jsonify(payload), 200


@arb_bp.route("/api/arb/<int:item_id>/review", methods=["POST"])
@login_required
def api_arb_begin_review(item_id: int):
    """ENH-020: Transition ARBReviewItem to under_review status."""
    from app.modules.transformation_room.arb_decision_adapter import (
        TypedARBDecisionAdapter,
    )

    typed_result = TypedARBDecisionAdapter.begin_review_from_request(
        review_item_id=item_id
    )
    if typed_result.typed:
        return _typed_decision_json(typed_result)

    item = ARBReviewItem.query.get_or_404(item_id)
    current_status = item.status or "draft"
    if current_status not in ("submitted", "draft", "pending"):
        return jsonify({
            "success": False,
            "error": f"Cannot begin review from status '{current_status}'.",
        }), 409

    request.get_json() or {}
    try:
        item.status = "under_review"
        item.reviewer_id = current_user.id
        item.review_started_at = datetime.utcnow()
        db.session.commit()
        return jsonify({
            "success": True,
            "item_id": item.id,
            "status": item.status,
        })
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("ENH-020: Error beginning ARB review %s: %s", item_id, exc)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_bp.route("/api/arb/<int:item_id>/approve", methods=["POST"])
@login_required
def api_arb_approve(item_id: int):
    """ENH-020: Approve an ARBReviewItem that is under_review."""
    from app.modules.transformation_room.arb_decision_adapter import (
        TypedARBDecisionAdapter,
    )

    data = request.get_json(silent=True) or {}
    typed_result = TypedARBDecisionAdapter.decide_review_from_request(
        review_item_id=item_id,
        payload=data,
        outcome="approved",
    )
    if typed_result.typed:
        return _typed_decision_json(typed_result)

    item = ARBReviewItem.query.get_or_404(item_id)
    current_status = item.status or "draft"
    if current_status != "under_review":
        return jsonify({
            "success": False,
            "error": f"Cannot approve from status '{current_status}'. Item must be under_review.",
        }), 409

    data = request.get_json() or {}
    conditions = data.get("conditions")
    try:
        if conditions:
            item.status = "approved_with_conditions"
        else:
            item.status = "approved"
        item.decision_date = datetime.utcnow()
        item.decision_notes = data.get("notes", "")
        item.decided_by_id = current_user.id

        # Propagate approval to the linked solution
        if item.solution_id:
            from app.models.solution_models import Solution
            solution = Solution.query.get(item.solution_id)
            if solution:
                solution.governance_status = "approved"
                solution.arb_approval_date = datetime.utcnow()

        db.session.commit()
        return jsonify({
            "success": True,
            "item_id": item.id,
            "status": item.status,
            "conditions": conditions,
        })
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("ENH-020: Error approving ARB item %s: %s", item_id, exc)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_bp.route("/api/arb/<int:item_id>/reject", methods=["POST"])
@login_required
def api_arb_reject(item_id: int):
    """ENH-020: Reject an ARBReviewItem that is under_review."""
    from app.modules.transformation_room.arb_decision_adapter import (
        TypedARBDecisionAdapter,
    )

    data = request.get_json(silent=True) or {}
    typed_result = TypedARBDecisionAdapter.decide_review_from_request(
        review_item_id=item_id,
        payload=data,
        outcome="rejected",
    )
    if typed_result.typed:
        return _typed_decision_json(
            typed_result,
            extra={"rejection_reason": data.get("reason")},
        )

    item = ARBReviewItem.query.get_or_404(item_id)
    current_status = item.status or "draft"
    if current_status != "under_review":
        return jsonify({
            "success": False,
            "error": f"Cannot reject from status '{current_status}'. Item must be under_review.",
        }), 409

    data = request.get_json() or {}
    reason = (data.get("reason") or "").strip()
    if not reason:
        return jsonify({"success": False, "error": "reason is required to reject"}), 400

    try:
        item.status = "rejected"
        item.rejection_reason = reason
        item.decision_date = datetime.utcnow()
        item.decided_by_id = current_user.id

        # Propagate rejection to the linked solution
        if item.solution_id:
            from app.models.solution_models import Solution
            solution = Solution.query.get(item.solution_id)
            if solution:
                solution.governance_status = "rejected"
                solution.arb_rejection_reason = reason

        db.session.commit()
        return jsonify({
            "success": True,
            "item_id": item.id,
            "status": item.status,
            "rejection_reason": reason,
        })
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("ENH-020: Error rejecting ARB item %s: %s", item_id, exc)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_bp.route("/api/arb/<int:item_id>/request-changes", methods=["POST"])
@login_required
def api_arb_request_changes(item_id: int):
    """ENH-020: Request changes on an ARBReviewItem under review.

    Sets status to approved_with_conditions and records conditions.

    Request Body:
        { "conditions": ["Fix security issue", ...], "notes": "Optional notes" }
    """
    from app.modules.transformation_room.arb_decision_adapter import (
        TypedARBDecisionAdapter,
    )

    data = request.get_json(silent=True) or {}
    typed_result = TypedARBDecisionAdapter.decide_review_from_request(
        review_item_id=item_id,
        payload=data,
        outcome="approved_with_conditions",
    )
    if typed_result.typed:
        return _typed_decision_json(typed_result)

    item = ARBReviewItem.query.get_or_404(item_id)
    current_status = item.status or "draft"
    if current_status != "under_review":
        return jsonify({
            "success": False,
            "error": f"Cannot request changes from status '{current_status}'. "
                     "Item must be under_review.",
        }), 409

    data = request.get_json() or {}
    conditions = data.get("conditions")
    if not conditions or not isinstance(conditions, list) or len(conditions) == 0:
        return jsonify({"success": False, "error": "At least one condition is required"}), 400

    try:
        item.status = "approved_with_conditions"
        item.decision = "approved_with_conditions"
        item.conditions = [
            {"condition": c, "status": "pending", "due_date": None}
            for c in conditions
            if isinstance(c, str) and c.strip()
        ]
        item.decision_rationale = data.get("notes", "")
        item.decision_date = datetime.utcnow()
        item.decided_by_id = current_user.id
        db.session.commit()
        return jsonify({
            "success": True,
            "item_id": item.id,
            "status": item.status,
            "conditions": item.conditions,
        })
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            "ENH-020: Error requesting changes for ARB item %s: %s", item_id, exc
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@arb_bp.route("/api/arb/<int:item_id>/implementation-status", methods=["GET"])
@login_required
def api_arb_get_implementation_status(item_id: int):
    """ENH-020: Get implementation status for an approved ARB review item."""
    from app.modules.transformation_room.arb_decision_adapter import (
        TypedARBDecisionAdapter,
    )
    from app.modules.transformation_room.domain import NotFound

    try:
        if TypedARBDecisionAdapter.review_is_typed(item_id):
            return jsonify({
                "success": False,
                "reason_codes": ["typed_implementation_status_not_supported"],
            }), 409
    except NotFound:
        return jsonify({"success": False, "reason_codes": ["review_not_found"]}), 404

    item = ARBReviewItem.query.get_or_404(item_id)
    return jsonify({
        "success": True,
        "item_id": item.id,
        "review_number": item.review_number,
        "status": item.status,
        "implementation_status": item.implementation_status or "not_started",
        "implementation_notes": item.implementation_notes,
        "implementation_started_at": item.implementation_started_at.isoformat()
        if item.implementation_started_at
        else None,
        "implementation_completed_at": item.implementation_completed_at.isoformat()
        if item.implementation_completed_at
        else None,
        "conditions": item.conditions,
        "conditions_response": item.conditions_response,
    })


@arb_bp.route("/api/arb/<int:item_id>/implementation-status", methods=["PATCH"])
@login_required
def api_arb_update_implementation_status(item_id: int):
    """ENH-020: Update implementation status for an approved ARB review item.

    Request Body:
        {
            "implementation_status": "in_progress|completed|blocked|deferred",
            "implementation_notes": "Optional notes",
            "conditions_response": {"0": "Evidence for condition 0", ...}
        }
    """
    from app.modules.transformation_room.arb_decision_adapter import (
        TypedARBDecisionAdapter,
    )
    from app.modules.transformation_room.domain import NotFound

    try:
        if TypedARBDecisionAdapter.review_is_typed(item_id):
            return jsonify({
                "success": False,
                "reason_codes": ["typed_cycle_implementation_status_not_writable"],
            }), 409
    except NotFound:
        return jsonify({"success": False, "reason_codes": ["review_not_found"]}), 404

    item = ARBReviewItem.query.get_or_404(item_id)
    if item.status not in ("approved", "approved_with_conditions"):
        return jsonify({
            "success": False,
            "error": f"Cannot update implementation for status '{item.status}'. "
                     "Item must be approved.",
        }), 409

    data = request.get_json() or {}
    valid_impl_statuses = {
        "not_started", "in_progress", "completed", "blocked", "deferred",
    }
    new_status = data.get("implementation_status")
    if new_status and new_status not in valid_impl_statuses:
        return jsonify({
            "success": False,
            "error": "Invalid implementation_status. Must be one of: "
                     + ", ".join(sorted(valid_impl_statuses)),
        }), 400

    try:
        if new_status:
            item.implementation_status = new_status
            if new_status == "in_progress" and not item.implementation_started_at:
                item.implementation_started_at = datetime.utcnow()
            elif new_status == "completed":
                item.implementation_completed_at = datetime.utcnow()
        if "implementation_notes" in data:
            item.implementation_notes = data["implementation_notes"]
        if "conditions_response" in data:
            item.conditions_response = data["conditions_response"]
        db.session.commit()
        return jsonify({
            "success": True,
            "item_id": item.id,
            "implementation_status": item.implementation_status,
            "implementation_notes": item.implementation_notes,
        })
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            "ENH-020: Error updating implementation status for ARB item %s: %s",
            item_id, exc,
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ===================================================================
# FRAG-037: ARB exception management routes
# ===================================================================

@arb_bp.route("/api/exceptions")
@login_required
def api_list_exceptions():
    """FRAG-037: List ARB exceptions."""
    try:
        from app.services.arb_exception_service import ARBExceptionService
        service = ARBExceptionService()
        exceptions = service.list_exceptions()
        return jsonify({"success": True, "exceptions": exceptions})
    except Exception as e:
        current_app.logger.error(f"List exceptions error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@arb_bp.route("/api/exceptions", methods=["POST"])
@login_required
def api_create_exception():
    """FRAG-037: Create ARB exception request."""
    try:
        from app.services.arb_exception_service import ARBExceptionService
        service = ARBExceptionService()
        data = request.get_json()
        result = service.create_exception_request(**data)
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"Create exception error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@arb_bp.route("/api/exceptions/<int:exception_id>/approve", methods=["PUT"])
@login_required
def api_approve_exception(exception_id):
    """FRAG-037: Approve ARB exception."""
    try:
        from app.services.arb_exception_service import ARBExceptionService
        service = ARBExceptionService()
        data = request.get_json() or {}
        result = service.approve_exception(exception_id, **data)
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"Approve exception error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
