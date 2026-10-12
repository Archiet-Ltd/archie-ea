"""The intelligence module's query surfaces.

  POST /api/v1/intelligence/derivation/recompute
  GET  /api/v1/intelligence/derived/<derived_id>   (with its explanation)
  GET  /api/v1/intelligence/value-streams-at-risk
  GET  /api/v1/intelligence/impact/<element_id>
  GET  /api/v1/intelligence/risk/<element_id>
  GET  /api/v1/intelligence/portfolio/<element_id>
  GET  /api/v1/intelligence/programme/<element_id>
  GET  /api/v1/intelligence/strategy/<element_id>
  GET  /api/v1/intelligence/accountability/<element_id>
  GET  /api/v1/intelligence/data/<element_id>
  GET  /api/v1/intelligence/compliance/<element_id>
  GET  /api/v1/intelligence/traceability/<element_id>
  GET  /api/v1/intelligence/yield

Each new route was added to this EXISTING blueprint rather than a new
module/blueprint on the same URL prefix (ADR 0008 rule 3: two blueprints
on one prefix).

CSRF is covered by Flask-WTF's global ``CSRFProtect`` (see
``app/_bootstrap/extensions.py``); no per-route decorator is needed for a
standard session-authenticated POST, consistent with every other write route
in this codebase.
"""

from __future__ import annotations

from flask import Blueprint, current_app, request
from flask_login import current_user, login_required

from app.modules.intelligence.services.reason_codes import validate_reason_code
from app.utils.tenant import current_organization_id
from app.utils.api_response import error_response, not_found_response, success_response

# NEW-4 fix: these two DE-14 reason codes are structurally unreachable in the
# 200 ``reasons`` list this route returns, because both conditions they name
# are already intercepted by an early 400/404 return below, before
# ``IntelligenceQueryService.cross_layer_impact`` (the only place that would
# put them in a 200 body) is ever called. Rather than leave them as dead
# members only a direct unit test can reach, they are surfaced in the error
# body of the exact 400/404 responses that ARE their real, reachable home.
_NO_TENANT_CONTEXT_REASON = validate_reason_code("no_tenant_context")
_ELEMENT_NOT_FOUND_REASON = validate_reason_code("element_not_found")
_FINANCIAL_DATA_RESTRICTED_REASON = validate_reason_code("financial_data_restricted")

# The single cost-visibility rule lives in role_access.py so every
# surface that redacts financial figures shares one authority.  Import it;
# do not define a second list.
from app.utils.role_access import COST_VISIBILITY_ROLES, get_user_role

intelligence_api = Blueprint(
    "intelligence_api", __name__, url_prefix="/api/v1/intelligence"
)


def _redact_financial_fields(rows: list, fields: tuple[str, ...], reason_field: str) -> None:
    """Redacts *fields* in place on every dict in *rows* for a caller without
    budget authority (least-privilege on the one sensitive data category this
    codebase's EA surfaces gate today -- financial figures; matches how
    ROLE_SECTION_ACCESS already treats rationalization/TCO/procurement, per
    field here rather than per page, since only Strategy/Programme carry
    financial figures on an otherwise uniformly-visible lens).

    Redaction is honest, not silent: each redacted field becomes ``None`` and
    *reason_field* (e.g. ``"budget_reason"``) is set to
    ``financial_data_restricted`` -- distinct from ``not_costed``/
    ``no_budget_recorded``, which mean "nobody recorded this," not "you
    can't see this." A caller with budget authority sees the real value and
    this function is a no-op for them.
    """
    if get_user_role(current_user) in COST_VISIBILITY_ROLES:
        return
    for row in rows:
        for field in fields:
            row[field] = None
        row[reason_field] = _FINANCIAL_DATA_RESTRICTED_REASON


@intelligence_api.route("/derivation/recompute", methods=["POST"])
@login_required
def recompute_derivation():
    """API-7: on-demand recompute for the caller's own tenant.

    Body: ``{"scope": "tenant"}`` -- any other scope is rejected with a
    clear 4xx, never a silent tenant-wide or estate-wide run (constraint,
    task 03).
    """
    payload = request.get_json(silent=True) or {}
    scope = payload.get("scope")
    if scope != "tenant":
        return error_response(
            "scope must be \"tenant\" -- no other recompute scope is supported at L1",
            code="INVALID_SCOPE",
            status_code=400,
        )

    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request", code="NO_TENANT_CONTEXT", status_code=400
        )

    from app.modules.intelligence.services.recompute_job import (
        recompute_derived_facts_on_demand,
    )

    app = current_app._get_current_object()
    run = recompute_derived_facts_on_demand(app, organization_id)

    if run.skipped_locked:
        return error_response(
            "a recompute is already running for this tenant -- try again shortly",
            code="RECOMPUTE_LOCKED",
            status_code=409,
        )

    if not run.results:
        return error_response(
            "recompute did not run for this tenant", code="RECOMPUTE_NOT_RUN", status_code=500
        )

    result = run.results[0]
    if not result.ok:
        return error_response(
            f"recompute failed: {result.error}",
            code="RECOMPUTE_FAILED",
            status_code=500,
        )

    value = result.value or {}
    if value.get("skipped_locked"):
        return error_response(
            "a recompute is already running for this tenant -- try again shortly",
            code="RECOMPUTE_LOCKED",
            status_code=409,
        )

    return success_response(
        {
            "organization_id": organization_id,
            "explicit_count": value.get("explicit_count"),
            "derived_count": value.get("derived_count"),
            "ratio": value.get("ratio"),
            "duration_ms": value.get("duration_ms"),
            "engine_version": value.get("engine_version"),
        }
    )


@intelligence_api.route("/derived/<int:derived_id>", methods=["GET"])
@login_required
def get_derived_fact_provenance(derived_id: int):
    """API-2: expand one derived row's provenance chain to explicit relationships.

    Tenant-scoped: a cross-tenant id is invisible -- the ORM tenant-isolation
    listener (``app/middleware/tenant_isolation.py``) injects a
    ``WHERE organization_id = g.current_org_id`` predicate on the
    ``TenantMixin``-backed model this reads, and the explicit
    ``organization_id`` argument passed to ``get_derived_fact`` below
    double-scopes it -- so this 404s -- never 403, never a leak of another
    tenant's row existing.
    """
    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request", code="NO_TENANT_CONTEXT", status_code=400
        )

    from app.modules.intelligence.services.derived_facts import get_derived_fact

    fact = get_derived_fact(organization_id, derived_id, include_stale=True)
    if fact is None:
        return not_found_response("Derived relationship")

    # No tenant_scope() here (round-1 refuter finding D4): this route already
    # runs inside a request with g.current_org_id set by the normal request
    # lifecycle, and the existing do_orm_execute tenant-isolation listener
    # already filters this read -- tenant_scope() is a background-job
    # harness whose db.session.remove() calls destroy the REQUEST's own
    # session (detaching flask_login's cached current_user, clobbering
    # g.current_org for the rest of the request) when used inside a request.
    #
    # The chain is read once, by the explanation: each drawn link with its two
    # elements, who drew it and when, the rule, and the decisions recorded
    # against those elements -- all of this organisation. ``expanded_chain``
    # is the same links in the id-and-endpoint shape this route has always
    # returned, so the two cannot disagree. A chain link that no longer
    # resolves stays in both as an explicit unresolved marker (D6) rather than
    # silently shortening the chain.
    from app.modules.intelligence.services.explanation import expanded_chain, explain_fact

    explanation = explain_fact(organization_id, fact)
    fact_out = dict(fact)
    fact_out["expanded_chain"] = expanded_chain(explanation)
    fact_out["explanation"] = explanation
    return success_response(fact_out)


_TRUE_STRINGS = {"true", "1", "yes"}
_FALSE_STRINGS = {"false", "0", "no"}
_VALID_DIRECTIONS = {"downstream", "upstream", "both"}


def _parse_bool_param(raw: str | None, *, default: bool, param_name: str):
    """Strict boolean query-param parsing: any non-boolean-looking value is a
    400, never a silent default (task 02 constraint: "never a silent
    default").

    Returns ``(value, error_response_or_None)``.
    """
    if raw is None:
        return default, None
    lowered = raw.strip().lower()
    if lowered in _TRUE_STRINGS:
        return True, None
    if lowered in _FALSE_STRINGS:
        return False, None
    return None, error_response(
        f"{param_name} must be a boolean (true/false)",
        code="INVALID_PARAMETER",
        status_code=400,
    )


def _value_stream_or_404_response(value_stream_id: int, organization_id: int):
    """Resolve *value_stream_id* within *organization_id*'s tenant scope, or
    the not-found error response for it.

    Two predicates, not one: ``ValueStream`` carries ``TenantMixin``, so the
    ``do_orm_execute`` tenant-isolation listener already fences this select
    inside a request -- the same shape ``cross_layer_impact`` uses for
    ``element_id`` below. The explicit
    ``IntelligenceQueryService._value_stream_tenant_predicate`` carried on
    top of that is what keeps "does not exist" and
    "belongs to another tenant" indistinguishable even if the listener's
    ambient organisation and the caller-resolved *organization_id* were ever
    to diverge -- without it, a foreign id could return 200 with empty rows
    (via the listener) while a never-existed id 404s here (this resolver),
    an existence oracle. Reusing the same seam ``value_streams_at_risk``
    itself uses also means the item-10 mutation proof, which neuters that
    one function, now covers all three predicate call sites on this path,
    not just two of them.

    Isolated as its own seam, the same pattern as ``_parse_bool_param``
    above, so the indistinguishability mutation-proof test (T-S1 acceptance
    item 13) can monkeypatch exactly this function to diverge the message
    between the two cases and confirm the named test goes red, without
    editing source under test.

    Returns ``(value_stream, error_response_or_None)``.
    """
    from app.extensions import db
    from app.modules.intelligence.services.query_service import IntelligenceQueryService
    from app.models.unified_capability import ValueStream

    value_stream = db.session.execute(
        db.select(ValueStream).where(
            ValueStream.id == value_stream_id,
            IntelligenceQueryService._value_stream_tenant_predicate(
                ValueStream, organization_id
            ),
        )
    ).scalar_one_or_none()
    if value_stream is not None:
        return value_stream, None
    return None, error_response(
        "Value stream not found",
        code="VALUE_STREAM_NOT_FOUND",
        status_code=404,
    )


@intelligence_api.route("/value-streams-at-risk", methods=["GET"])
@login_required
def value_streams_at_risk():
    """T-S1 (DA-S1): US-2's "which value streams are at risk, and why" --
    the curated path only (no graph read; that is T-S3).

    Serialises ``IntelligenceQueryService.value_streams_at_risk`` through
    ``success_response`` -- no business logic here (task 02 constraint,
    T-S1 brief constraint 4).
    """
    threshold_raw = request.args.get("threshold")
    if threshold_raw is None:
        threshold = 3
    else:
        try:
            threshold = int(threshold_raw)
        except (TypeError, ValueError):
            return error_response(
                "threshold must be an integer between 1 and 5",
                code="INVALID_PARAMETER",
                status_code=400,
            )
        if not (1 <= threshold <= 5):
            return error_response(
                "threshold must be between 1 and 5",
                code="INVALID_PARAMETER",
                status_code=400,
            )

    value_stream_id_raw = request.args.get("value_stream_id")
    value_stream_id = None
    if value_stream_id_raw is not None:
        try:
            value_stream_id = int(value_stream_id_raw)
        except (TypeError, ValueError):
            return error_response(
                "value_stream_id must be a positive integer",
                code="INVALID_PARAMETER",
                status_code=400,
            )
        if value_stream_id <= 0:
            return error_response(
                "value_stream_id must be a positive integer",
                code="INVALID_PARAMETER",
                status_code=400,
            )

    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )

    if value_stream_id is not None:
        _value_stream, not_found_err = _value_stream_or_404_response(
            value_stream_id, organization_id
        )
        if not_found_err is not None:
            return not_found_err

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    result = IntelligenceQueryService.value_streams_at_risk(
        organization_id,
        threshold=threshold,
        value_stream_id=value_stream_id,
    )

    return success_response(result)


@intelligence_api.route("/impact/<int:element_id>", methods=["GET"])
@login_required
def cross_layer_impact(element_id: int):
    """API-1 (DE-9): US-1's "if this fails, what stops and who owns it".

    Serialises ``IntelligenceQueryService.cross_layer_impact`` through
    ``success_response`` -- no business logic here (task 02 constraint).
    """
    include_derived, err = _parse_bool_param(
        request.args.get("include_derived"), default=False, param_name="include_derived"
    )
    if err is not None:
        return err

    include_stale, err = _parse_bool_param(
        request.args.get("include_stale"), default=False, param_name="include_stale"
    )
    if err is not None:
        return err

    with_owner, err = _parse_bool_param(
        request.args.get("with_owner"), default=True, param_name="with_owner"
    )
    if err is not None:
        return err

    max_depth_raw = request.args.get("max_depth")
    if max_depth_raw is None:
        max_depth = 3
    else:
        try:
            max_depth = int(max_depth_raw)
        except (TypeError, ValueError):
            return error_response(
                "max_depth must be an integer between 1 and 10",
                code="INVALID_PARAMETER",
                status_code=400,
            )
        if not (1 <= max_depth <= 10):
            return error_response(
                "max_depth must be between 1 and 10",
                code="INVALID_PARAMETER",
                status_code=400,
            )

    direction = request.args.get("direction", "downstream")
    if direction not in _VALID_DIRECTIONS:
        return error_response(
            f"direction must be one of {sorted(_VALID_DIRECTIONS)}",
            code="INVALID_PARAMETER",
            status_code=400,
        )

    layer = request.args.get("layer")

    cursor = None
    cursor_raw = request.args.get("cursor")
    if cursor_raw is not None:
        try:
            cursor = int(cursor_raw)
        except (TypeError, ValueError):
            return error_response(
                "cursor must be an integer",
                code="INVALID_PARAMETER",
                status_code=400,
            )
        if cursor < 0:
            return error_response(
                "cursor must be non-negative",
                code="INVALID_PARAMETER",
                status_code=400,
            )

    page_size = None
    page_size_raw = request.args.get("page_size")
    if page_size_raw is not None:
        try:
            page_size = int(page_size_raw)
        except (TypeError, ValueError):
            return error_response(
                "page_size must be an integer",
                code="INVALID_PARAMETER",
                status_code=400,
            )
        if not (1 <= page_size <= 200):
            return error_response(
                "page_size must be between 1 and 200",
                code="INVALID_PARAMETER",
                status_code=400,
            )

    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )

    from app.models import ArchiMateElement

    element = ArchiMateElement.query.filter_by(id=element_id).first()
    if element is None:
        # Same call site, same status/code/message for "does not exist" and
        # "belongs to another tenant" -- the tenant-isolation listener has
        # already made the two indistinguishable at the query level (task 02
        # acceptance item 4). ``details`` carries the same reason value on
        # both branches (there is only one branch), so D3 indistinguishability
        # is unaffected.
        return error_response(
            "Element not found",
            code="NOT_FOUND",
            details={"reason": _ELEMENT_NOT_FOUND_REASON},
            status_code=404,
        )

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    result = IntelligenceQueryService.cross_layer_impact(
        element_id,
        include_derived=include_derived,
        include_stale=include_stale,
        max_depth=max_depth,
        direction=direction,
        layer=layer,
        with_owner=with_owner,
        cursor=cursor,
        page_size=page_size,
    )

    if result.get("rows") is None:
        return not_found_response("Element")

    return success_response(
        {
            "rows": result["rows"],
            "summary": result["summary"],
            "reasons": result.get("reasons") or [],
            "elements": result.get("elements") or {},
            "maturity_flags": result.get("maturity_flags"),
            "total": result.get("total"),
            "next_cursor": result.get("next_cursor"),
        }
    )


@intelligence_api.route("/traceability/<int:element_id>", methods=["GET"])
@login_required
def traceability_check(element_id: int):
    """Does this element trace up to a capability and down to technology?

    Serialises ``TraceabilityCheckService.check``, which reads the same walk
    as the impact route above. An element outside the caller's organisation
    answers exactly as one that does not exist.
    """
    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )

    from app.modules.intelligence.services.traceability_check_service import TraceabilityCheckService

    result = TraceabilityCheckService.check(element_id, organization_id)
    if result.get("state") != "checked":
        return error_response(
            "Element not found",
            code="NOT_FOUND",
            details={"reason": _ELEMENT_NOT_FOUND_REASON},
            status_code=404,
        )
    return success_response(result)


@intelligence_api.route("/risk/<int:element_id>", methods=["GET"])
@login_required
def risk_for_element(element_id: int):
    """L6: "what could hurt <element>, and what does it touch?"

    Serialises ``IntelligenceQueryService.risk_for_element`` through
    ``success_response`` -- same shape/error-handling pattern as
    ``cross_layer_impact`` above, no business logic here.
    """
    include_derived, err = _parse_bool_param(
        request.args.get("include_derived"), default=True, param_name="include_derived"
    )
    if err is not None:
        return err

    max_depth_raw = request.args.get("max_depth")
    if max_depth_raw is None:
        max_depth = 3
    else:
        try:
            max_depth = int(max_depth_raw)
        except (TypeError, ValueError):
            return error_response(
                "max_depth must be an integer between 1 and 10",
                code="INVALID_PARAMETER",
                status_code=400,
            )
        if not (1 <= max_depth <= 10):
            return error_response(
                "max_depth must be between 1 and 10",
                code="INVALID_PARAMETER",
                status_code=400,
            )

    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )

    from app.models import ArchiMateElement

    element = ArchiMateElement.query.filter_by(id=element_id).first()
    if element is None:
        return error_response(
            "Element not found",
            code="NOT_FOUND",
            details={"reason": _ELEMENT_NOT_FOUND_REASON},
            status_code=404,
        )

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    result = IntelligenceQueryService.risk_for_element(
        element_id,
        max_depth=max_depth,
        include_derived=include_derived,
    )

    return success_response(
        {
            "risks": result["risks"],
            "reasons": result.get("reasons") or [],
            "elements": result.get("elements") or {},
        }
    )


# The component block's seven entered cost fields (matches
# IntelligenceQueryService.portfolio_component_for_element's ``cost`` key) --
# named once here so the redaction call below and any future caller share
# the one list rather than each spelling it out.
_SEVEN_COST_FIELDS = (
    "total_cost_of_ownership",
    "license_cost_annual",
    "maintenance_cost",
    "infrastructure_cost",
    "support_cost",
    "implementation_cost",
    "development_cost_annual",
)


@intelligence_api.route("/portfolio/<int:element_id>", methods=["GET"])
@login_required
def portfolio_component_for_element(element_id: int):
    """L3: resolves an element to its ApplicationComponent and the
    ``component`` block -- name, owner-recorded health, entered cost/TCO
    figures, latest fiscal-period cost row and licence position -- built by
    ``IntelligenceQueryService.portfolio_component_for_element`` off that
    same resolution. Financial figures in the block are redacted for a
    caller without budget authority, same role set and same
    ``_redact_financial_fields`` helper the Strategy/Programme routes
    already use; see the service method's own docstring for why
    duplicate-detection and TCO history are not offered here.
    """
    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )

    from app.models import ArchiMateElement

    element = ArchiMateElement.query.filter_by(id=element_id).first()
    if element is None:
        return error_response(
            "Element not found",
            code="NOT_FOUND",
            details={"reason": _ELEMENT_NOT_FOUND_REASON},
            status_code=404,
        )

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    result = IntelligenceQueryService.portfolio_component_for_element(element_id)

    component = result.get("component")
    if component is not None:
        _redact_financial_fields([component["cost"]], _SEVEN_COST_FIELDS, "access_reason")
        _redact_financial_fields(
            [component["cost_by_period"]],
            ("total_cost", "total_budget", "variance"),
            "access_reason",
        )
        _redact_financial_fields(component["licences"] or [], ("unit_cost",), "access_reason")

    return success_response(
        {
            "application_component_id": result.get("application_component_id"),
            "reasons": result.get("reasons") or [],
            "component": component,
        }
    )


@intelligence_api.route("/programme/<int:element_id>", methods=["GET"])
@login_required
def programme_for_element(element_id: int):
    """L5: "what are we changing, is it on time and on budget, what does it
    touch?" Serialises ``IntelligenceQueryService.programme_for_element``
    through ``success_response`` -- same shape/error-handling pattern as
    ``risk_for_element`` above, no business logic here.
    """
    include_derived, err = _parse_bool_param(
        request.args.get("include_derived"), default=True, param_name="include_derived"
    )
    if err is not None:
        return err

    max_depth_raw = request.args.get("max_depth")
    if max_depth_raw is None:
        max_depth = 3
    else:
        try:
            max_depth = int(max_depth_raw)
        except (TypeError, ValueError):
            return error_response(
                "max_depth must be an integer between 1 and 10",
                code="INVALID_PARAMETER",
                status_code=400,
            )
        if not (1 <= max_depth <= 10):
            return error_response(
                "max_depth must be between 1 and 10",
                code="INVALID_PARAMETER",
                status_code=400,
            )

    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )

    from app.models import ArchiMateElement

    element = ArchiMateElement.query.filter_by(id=element_id).first()
    if element is None:
        return error_response(
            "Element not found",
            code="NOT_FOUND",
            details={"reason": _ELEMENT_NOT_FOUND_REASON},
            status_code=404,
        )

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    result = IntelligenceQueryService.programme_for_element(
        element_id,
        max_depth=max_depth,
        include_derived=include_derived,
    )

    work_packages = result["work_packages"]
    _redact_financial_fields(work_packages, ("cost_variance_pct",), "cost_reason")
    _redact_financial_fields(
        [wp["gap"] for wp in work_packages], ("estimated_cost",), "access_reason"
    )

    return success_response(
        {
            "work_packages": work_packages,
            "reasons": result.get("reasons") or [],
            "elements": result.get("elements") or {},
        }
    )


@intelligence_api.route("/strategy/<int:element_id>", methods=["GET"])
@login_required
def strategy_for_element(element_id: int):
    """L2: "what are we trying to achieve, and how's it tracking?"
    Serialises ``IntelligenceQueryService.strategy_for_element`` through
    ``success_response`` -- same shape/error-handling pattern as
    ``programme_for_element`` above, no business logic here.
    """
    include_derived, err = _parse_bool_param(
        request.args.get("include_derived"), default=True, param_name="include_derived"
    )
    if err is not None:
        return err

    max_depth_raw = request.args.get("max_depth")
    if max_depth_raw is None:
        max_depth = 3
    else:
        try:
            max_depth = int(max_depth_raw)
        except (TypeError, ValueError):
            return error_response(
                "max_depth must be an integer between 1 and 10",
                code="INVALID_PARAMETER",
                status_code=400,
            )
        if not (1 <= max_depth <= 10):
            return error_response(
                "max_depth must be between 1 and 10",
                code="INVALID_PARAMETER",
                status_code=400,
            )

    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )

    from app.models import ArchiMateElement

    element = ArchiMateElement.query.filter_by(id=element_id).first()
    if element is None:
        return error_response(
            "Element not found",
            code="NOT_FOUND",
            details={"reason": _ELEMENT_NOT_FOUND_REASON},
            status_code=404,
        )

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    result = IntelligenceQueryService.strategy_for_element(
        element_id,
        max_depth=max_depth,
        include_derived=include_derived,
    )

    initiatives = result["initiatives"]
    _redact_financial_fields(initiatives, ("budget_variance_pct",), "budget_reason")

    return success_response(
        {
            "initiatives": initiatives,
            "reasons": result.get("reasons") or [],
            "elements": result.get("elements") or {},
        }
    )


@intelligence_api.route("/accountability/<int:element_id>", methods=["GET"])
@login_required
def accountability_for_element(element_id: int):
    """L4: "who's accountable for ___, and can they take on more?"
    Serialises ``IntelligenceQueryService.accountability_for_element``
    through ``success_response`` -- same error-handling pattern as the
    other lenses, no business logic here. No max_depth/include_derived
    params: this lens is a pure ownership lookup, not a blast-radius
    traversal, unlike every other lens on this blueprint.

    The ownership read itself is currently WITHDRAWN -- see the service
    method's own docstring (a real tenant-isolation gap found in external
    review of the original PR; no shared, tenant-safe reader exists yet).
    This route's element/tenant pre-checks are unchanged and still real;
    only the body of the answer is a permanent honest empty state until
    that reader exists.
    """
    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )

    from app.models import ArchiMateElement

    element = ArchiMateElement.query.filter_by(id=element_id).first()
    if element is None:
        return error_response(
            "Element not found",
            code="NOT_FOUND",
            details={"reason": _ELEMENT_NOT_FOUND_REASON},
            status_code=404,
        )

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    result = IntelligenceQueryService.accountability_for_element(element_id)

    payload = {
        "owners": result["owners"],
        "capacity_not_available": result.get("capacity_not_available", True),
        "reasons": result.get("reasons") or [],
        "as_of": result.get("as_of"),
    }
    if "raci" in result:
        payload["raci"] = result["raci"]

    return success_response(payload)


@intelligence_api.route("/data/<int:element_id>", methods=["GET"])
@login_required
def data_for_element(element_id: int):
    """L7: "what data does this hold or produce, who stewards it, and where does it flow?"
    Serialises ``IntelligenceQueryService.data_for_element`` through ``success_response``
    -- same error-handling pattern as the other lenses, no business logic here. The
    element/tenant pre-checks are real: no tenant context is 400, an element that is not
    this organisation's (or does not exist) is the same 404, so a foreign id cannot be told
    from a missing one.
    """
    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )

    from app.models import ArchiMateElement

    element = ArchiMateElement.query.filter_by(id=element_id).first()
    if element is None:
        return error_response(
            "Element not found",
            code="NOT_FOUND",
            details={"reason": _ELEMENT_NOT_FOUND_REASON},
            status_code=404,
        )

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    result = IntelligenceQueryService.data_for_element(element_id)

    return success_response(
        {
            "data_objects": result["data_objects"],
            "flows": result["flows"],
            "elements": result.get("elements") or {},
            "reasons": result.get("reasons") or [],
            "as_of": result.get("as_of"),
        }
    )


@intelligence_api.route("/compliance/<int:element_id>", methods=["GET"])
@login_required
def compliance_for_element(element_id: int):
    """Compliance (under L6): "which regulations and controls apply to this, and which
    controls have no evidence of being met?" Serialises
    ``IntelligenceQueryService.compliance_for_element`` through ``success_response``.
    No tenant context is 400; an element that is not this organisation's (or does not
    exist) is the same 404, so a foreign id cannot be told from a missing one.
    """
    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )

    from app.models import ArchiMateElement

    element = ArchiMateElement.query.filter_by(id=element_id).first()
    if element is None:
        return error_response(
            "Element not found",
            code="NOT_FOUND",
            details={"reason": _ELEMENT_NOT_FOUND_REASON},
            status_code=404,
        )

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    result = IntelligenceQueryService.compliance_for_element(element_id)

    return success_response(
        {
            "controls": result["controls"],
            "open_violations": result["open_violations"],
            "last_scan_at": result.get("last_scan_at"),
            "reasons": result.get("reasons") or [],
            "as_of": result.get("as_of"),
        }
    )


@intelligence_api.route("/yield", methods=["GET"])
@login_required
def derivation_yield():
    """API-5 (DE-11): US-5's "how much does derivation add", for the
    caller's own tenant. Serialises
    ``IntelligenceQueryService.derivation_yield`` through
    ``success_response`` -- no business logic here (task 02 constraint;
    view function name is deliberately ``derivation_yield``, not
    ``cross_layer_impact``, which is already taken in this file).
    """
    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    result = IntelligenceQueryService.derivation_yield(organization_id)
    return success_response(result)


@intelligence_api.route("/catalogue", methods=["GET"])
@login_required
def query_catalogue_list():
    """R1-B39: list the named questions a Portfolio Manager or Business
    Owner can ask or run directly."""
    from app.modules.intelligence.services.query_catalogue import list_entries

    return success_response({"entries": list_entries()})


@intelligence_api.route("/catalogue/<string:entry_id>", methods=["GET"])
@login_required
def query_catalogue_run(entry_id):
    """R1-B39: run one catalogue entry by id, with its declared parameters
    taken from the query string. Serves both the screen and this API with
    the same rows (TB-0107) -- the entry itself is the only query engine."""
    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )

    from app.modules.intelligence.services.query_catalogue import CATALOGUE, run_entry

    entry = CATALOGUE.get(entry_id)
    if entry is None:
        return not_found_response(f"no catalogue entry named '{entry_id}'")

    params = {name: request.args.get(name) for name in entry.params if request.args.get(name) is not None}
    result = run_entry(entry_id, organization_id, **params)
    return success_response({"entry_id": entry_id, "title": entry.title, "params": params, **result})


@intelligence_api.route("/ask", methods=["POST"])
@login_required
def ask_nl_question():
    """R1-B39: a plain-language question, interpreted onto one catalogue
    entry and run. The interpretation (which entry, which parameters) is
    always returned alongside the answer so the caller can show it and
    let the user correct a misread parameter by re-POSTing with
    ``entry_id``/``params`` set directly (TB-0108)."""
    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )

    body = request.get_json(silent=True) or {}
    question = body.get("question", "")

    from app.modules.intelligence.services.query_catalogue import CATALOGUE, run_entry
    from app.modules.intelligence.services.nl_query_interpreter import interpret

    if body.get("entry_id"):
        # The user corrected the interpretation -- run exactly what they
        # chose. ``entry_id`` and ``params`` come straight off the request
        # body, so both are checked before anything touches them: an
        # unhashable ``entry_id`` (a list/dict) would raise at the first
        # ``in CATALOGUE`` lookup below, and a non-dict ``params`` would
        # raise on the ``**`` spread into ``run_entry`` further down.
        raw_entry_id = body["entry_id"]
        if not isinstance(raw_entry_id, str):
            return error_response(
                "entry_id must be a string",
                code="INVALID_ENTRY_ID",
                status_code=400,
            )

        raw_params = body.get("params")
        if raw_params is not None and not isinstance(raw_params, dict):
            return error_response(
                "params must be an object",
                code="INVALID_PARAMS",
                status_code=400,
            )
        caller_params = raw_params or {}

        # Same filtering the GET /catalogue/<entry_id> route already does:
        # only the entry's own declared parameter names, and only string
        # values, ever reach ``run_entry`` -- this is what keeps a caller
        # from smuggling ``organization_id``/``entry_id`` (or anything else)
        # into the ``**params`` spread below. An unknown entry_id simply
        # yields no params; the existing "not in CATALOGUE" check further
        # down is what turns that into the honest "could not map" response.
        entry = CATALOGUE.get(raw_entry_id)
        safe_params = (
            {
                name: caller_params[name]
                for name in entry.params
                if isinstance(caller_params.get(name), str)
            }
            if entry is not None
            else {}
        )

        interpretation = {
            "entry_id": raw_entry_id,
            "params": safe_params,
            "confidence": 1.0,
            "method": "corrected",
            "title": entry.title if entry is not None else None,
        }
    else:
        interpretation = interpret(question)

    entry_id = interpretation["entry_id"]
    if entry_id is None or entry_id not in CATALOGUE:
        return success_response(
            {
                "question": question,
                "interpretation": interpretation,
                "answer": None,
                "reason": "could not map this question to a known catalogue entry",
            }
        )

    result = run_entry(entry_id, organization_id, **interpretation["params"])
    return success_response({"question": question, "interpretation": interpretation, **result})


__all__ = ["intelligence_api"]
