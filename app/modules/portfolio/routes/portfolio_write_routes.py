"""Write paths for the portfolio: demand intake, benefits, assumptions.

Server-rendered POST forms rather than JSON. These are low-frequency actions, and
a form that works without JavaScript is the right default for an intake queue
people fill in from a link in an email. CSRF comes from the hidden token.

Every handler validates before writing and re-renders or redirects with an error
rather than silently discarding input. A form that reports success without
saving is the same class of lie as a dashboard that invents a number.
"""
from datetime import datetime

from flask import abort, flash, g, redirect, request, url_for
from flask_login import current_user, login_required

from app import db
from app.modules.portfolio.routes.portfolio_routes import portfolio_bp


def _int_or_none(raw):
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _decimal_or_none(raw):
    try:
        return float(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _date_or_none(raw):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date() if raw else None
    except (TypeError, ValueError):
        return None


def _uid():
    return getattr(current_user, "id", None)


def _require_org_id():
    """The signed-in organisation; 403 when it is not resolved.

    The automatic tenant filter does nothing without an organisation, so the
    demand routes state it explicitly and refuse to run without one.
    """
    org_id = getattr(g, "current_org_id", None)
    if org_id is None:
        abort(403)
    return org_id


# ── Demand ────────────────────────────────────────────────────────────────

@portfolio_bp.route("/demands")
@login_required
def demands():
    """The intake queue. Undecided first, longest-waiting at the top."""
    from flask import render_template

    from app.models.demand import Demand

    org_id = _require_org_id()
    rows = (
        db.session.query(Demand)
        .filter(Demand.organization_id == org_id)
        .order_by(Demand.id.desc())
        .all()
    )
    open_demands = [d for d in rows if not d.is_decided]
    open_demands.sort(key=lambda d: (d.days_awaiting_decision or 0), reverse=True)
    from app.models.demand import DEMAND_ROUTES
    from app.modules.portfolio.services.demand_triage import recommend_many

    by_capability = recommend_many([d.capability_id for d in open_demands])
    recommendations = {d.id: by_capability[d.capability_id] for d in open_demands}
    return render_template(
        "portfolio/demands.html",
        recommendations=recommendations,
        routes=DEMAND_ROUTES,
        open_demands=open_demands,
        decided=[d for d in rows if d.is_decided],
    )


@portfolio_bp.route("/demands/new", methods=["GET", "POST"])
@login_required
def demand_new():
    """Submit a demand — the front door that did not exist."""
    from flask import render_template

    from app.models.demand import DEMAND_SOURCES, Demand
    from app.modules.portfolio.services.demand_triage import visible_capabilities, visible_capability

    _require_org_id()
    capabilities = visible_capabilities()

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        raw_capability = (request.form.get("capability_id") or "").strip()
        capability_id = _int_or_none(raw_capability)
        if raw_capability and (capability_id is None or visible_capability(capability_id) is None):
            flash("That capability could not be found.", "error")
            return render_template(
                "portfolio/demand_form.html", sources=DEMAND_SOURCES, form=request.form,
                capabilities=capabilities,
            ), 400
        if not title:
            flash("A title is required.", "error")
            return render_template(
                "portfolio/demand_form.html", sources=DEMAND_SOURCES, form=request.form,
                capabilities=capabilities,
            ), 400

        demand = Demand(
            title=title,
            description=(request.form.get("description") or "").strip() or None,
            business_justification=(request.form.get("business_justification") or "").strip() or None,
            source=request.form.get("source") or None,
            business_value_score=_int_or_none(request.form.get("business_value_score")),
            urgency_score=_int_or_none(request.form.get("urgency_score")),
            effort_estimate_days=_int_or_none(request.form.get("effort_estimate_days")),
            estimated_cost=_decimal_or_none(request.form.get("estimated_cost")),
            requested_for_business_unit=(request.form.get("requested_for_business_unit") or "").strip() or None,
            needed_by_date=_date_or_none(request.form.get("needed_by_date")),
            requested_by_id=_uid(),
            capability_id=capability_id,
            status="submitted",
        )
        db.session.add(demand)
        db.session.commit()
        flash("Demand submitted.", "success")
        return redirect(url_for("portfolio.demands"))

    return render_template(
        "portfolio/demand_form.html", sources=DEMAND_SOURCES, form={}, capabilities=capabilities
    )


@portfolio_bp.route("/demands/<int:demand_id>/decide", methods=["POST"])
@login_required
def demand_decide(demand_id):
    """Record a triage decision.

    A decline with no rationale is refused. An unexplained decline is the one
    that returns next quarter, and this record is the only place the "we were
    never asked" argument can be settled.
    """
    from app.models.demand import Demand

    org_id = _require_org_id()
    demand = (
        db.session.query(Demand)
        .filter(Demand.id == demand_id, Demand.organization_id == org_id)
        .one_or_none()
    )
    if demand is None:
        abort(404)

    decision = request.form.get("status")
    if decision not in ("approved", "declined", "deferred", "withdrawn"):
        flash("Unrecognised decision.", "error")
        return redirect(url_for("portfolio.demands"))

    rationale = (request.form.get("decision_rationale") or "").strip()
    if decision == "declined" and not rationale:
        flash("A declined demand needs a rationale.", "error")
        return redirect(url_for("portfolio.demands"))

    from app.models.demand import DEMAND_ROUTES
    from app.modules.portfolio.services.demand_triage import recommend

    # Absent field = leave the recorded route alone (approving a deferred row
    # posts none); an explicit blank clears it.
    route_field = request.form.get("route")
    route = (route_field or "").strip() or None
    if route is not None and route not in DEMAND_ROUTES:
        flash("Unrecognised route.", "error")
        return redirect(url_for("portfolio.demands"))
    if route is not None:
        # Keep the coverage the person was looking at with the route they chose.
        demand.triage_route = route
        demand.triage_route_reasons = recommend(demand.capability_id)["reason"]
    elif route_field is not None:
        # An explicit blank route clears an earlier one, with its reasons.
        demand.triage_route = None
        demand.triage_route_reasons = None

    demand.status = decision
    demand.decision_rationale = rationale or None
    demand.decision_date = datetime.utcnow().date()
    demand.triaged_by_id = _uid()
    db.session.commit()
    flash("Decision recorded.", "success")
    return redirect(url_for("portfolio.demands"))


# ── Benefit ───────────────────────────────────────────────────────────────

@portfolio_bp.route("/initiatives/<int:initiative_id>/benefits", methods=["POST"])
@login_required
def benefit_create(initiative_id):
    """Legacy URL, now bridged (2 Sep 2026) — see EnterpriseInitiative.
    linked_strategic_initiative_id. EnterpriseInitiative and StrategicInitiative
    identifiers come from separate sequences and cannot safely be treated as
    interchangeable, so a benefit is only ever attached to the CONFIRMED
    canonical programme a human linked, never guessed from the matching ID.
    """
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    initiative = db.session.get(EnterpriseInitiative, initiative_id)
    if initiative is None or initiative.linked_strategic_initiative_id is None:
        flash(
            "This legacy initiative must be linked to a transformation programme "
            "before benefits can be added.",
            "error",
        )
        abort(409)
    return _create_programme_benefit(initiative.linked_strategic_initiative_id)


@portfolio_bp.route("/programmes/<int:programme_id>/benefits", methods=["POST"])
@login_required
def programme_benefit_create(programme_id):
    return _create_programme_benefit(programme_id)


@portfolio_bp.route("/programmes/benefits", methods=["POST"])
@login_required
def selected_programme_benefit_create():
    programme_id = _int_or_none(request.form.get("programme_id"))
    if programme_id is None:
        abort(400)
    return _create_programme_benefit(programme_id)


def _create_programme_benefit(programme_id):
    from app.models.benefit import Benefit
    from app.models.strategic import StrategicInitiative

    programme = db.session.scalar(
        db.select(StrategicInitiative).where(
            StrategicInitiative.id == programme_id,
            StrategicInitiative.organization_id == current_user.organization_id,
            StrategicInitiative.record_kind == "transformation_programme",
        )
    )
    if programme is None:
        abort(404)

    name = (request.form.get("name") or "").strip()
    if not name:
        flash("A benefit needs a name.", "error")
        return redirect(url_for("portfolio.index"))

    db.session.add(Benefit(
        name=name,
        strategic_initiative_id=programme.id,
        benefit_type=request.form.get("benefit_type") or "cost_saving",
        measure=(request.form.get("measure") or "").strip() or None,
        unit=(request.form.get("unit") or "").strip() or None,
        baseline_value=_decimal_or_none(request.form.get("baseline_value")),
        target_value=_decimal_or_none(request.form.get("target_value")),
        target_date=_date_or_none(request.form.get("target_date")),
        owner_id=_uid(),
        status="identified",
    ))
    db.session.commit()
    flash("Benefit added.", "success")
    return redirect(url_for("portfolio.index"))


@portfolio_bp.route("/benefits/<int:benefit_id>/measure", methods=["POST"])
@login_required
def benefit_measure(benefit_id):
    """Record a measured actual — what turns a claim into evidence.

    Goes through the room's outcome writer, the one place that appends the
    measurement history and projects it onto the benefit.
    """
    import uuid
    from datetime import timezone
    from decimal import Decimal, InvalidOperation

    from app.modules.transformation_room.domain import (
        NotAuthorised,
        NotFound,
        TransformationError,
    )
    from app.modules.transformation_room.outcome_service import OutcomeMeasurementService
    from app.modules.transformation_room.routes import actor_from_request

    try:
        actual = Decimal(str(request.form.get("actual_value") or "").strip())
        if not actual.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        flash("A measurement needs a value.", "error")
        return redirect(url_for("portfolio.index"))

    now = datetime.now(timezone.utc)
    observed_on = _date_or_none(request.form.get("actual_date"))
    observed_at = now if observed_on is None else now.replace(
        year=observed_on.year, month=observed_on.month, day=observed_on.day
    )
    try:
        OutcomeMeasurementService.record(
            actor=actor_from_request(),
            benefit_id=benefit_id,
            value=actual,
            unavailable_reason=None,
            observed_at=observed_at,
            source_identity="portfolio:benefit-form",
            source_version="1",
            command_key=str(uuid.uuid4()),
        )
    except NotFound:
        abort(404)
    except NotAuthorised:
        abort(403)
    except (TransformationError, ValueError) as error:
        flash(f"Measurement not recorded: {getattr(error, 'reason', error)}", "error")
        return redirect(url_for("portfolio.index"))
    flash("Measurement recorded.", "success")
    return redirect(url_for("portfolio.index"))


# ── Assumption ────────────────────────────────────────────────────────────

@portfolio_bp.route("/initiatives/<int:initiative_id>/assumptions", methods=["POST"])
@login_required
def assumption_create(initiative_id):
    from app.models.demand import Assumption
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    if db.session.get(EnterpriseInitiative, initiative_id) is None:
        abort(404)

    statement = (request.form.get("statement") or "").strip()
    if not statement:
        flash("An assumption needs a statement.", "error")
        return redirect(url_for("portfolio.detail", initiative_id=initiative_id))

    db.session.add(Assumption(
        statement=statement,
        initiative_id=initiative_id,
        rationale=(request.form.get("rationale") or "").strip() or None,
        impact_if_false=_int_or_none(request.form.get("impact_if_false")),
        confidence=_int_or_none(request.form.get("confidence")),
        validate_by_date=_date_or_none(request.form.get("validate_by_date")),
        validation_method=(request.form.get("validation_method") or "").strip() or None,
        owner_id=_uid(),
        status="open",
    ))
    db.session.commit()
    flash("Assumption logged.", "success")
    return redirect(url_for("portfolio.detail", initiative_id=initiative_id))


@portfolio_bp.route("/assumptions/<int:assumption_id>/resolve", methods=["POST"])
@login_required
def assumption_resolve(assumption_id):
    """Validate or invalidate an assumption.

    Invalidating keeps the record and the note rather than deleting it. An
    assumption that proved false is the most useful entry in the log, and it is
    the one that should have become a risk earlier.
    """
    from app.models.demand import Assumption

    assumption = db.session.get(Assumption, assumption_id)
    if assumption is None:
        abort(404)

    outcome = request.form.get("status")
    if outcome not in ("validated", "invalidated", "expired"):
        flash("Unrecognised outcome.", "error")
        return redirect(url_for("portfolio.detail", initiative_id=assumption.initiative_id))

    note = (request.form.get("note") or "").strip()
    if outcome == "invalidated" and not note:
        flash("Say what happened when invalidating an assumption.", "error")
        return redirect(url_for("portfolio.detail", initiative_id=assumption.initiative_id))

    assumption.status = outcome
    today = datetime.utcnow().date()
    if outcome == "validated":
        assumption.validated_date = today
    elif outcome == "invalidated":
        assumption.invalidated_date = today
        assumption.invalidated_note = note
    db.session.commit()
    flash("Assumption updated.", "success")
    return redirect(url_for("portfolio.detail", initiative_id=assumption.initiative_id))


@portfolio_bp.route("/initiatives/<int:initiative_id>/link-programme", methods=["POST"])
@login_required
def initiative_link_programme(initiative_id):
    """Tie this legacy EnterpriseInitiative to the StrategicInitiative row that
    describes the same real-world programme (ADR-0008 note on
    EnterpriseInitiative — this is the human-confirmed link, never an
    automated guess from matching names or dates). Once set, this legacy
    initiative's benefits are governed by the linked programme (see
    benefit_create above)."""
    from app.models.strategic import StrategicInitiative
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    initiative = db.session.get(EnterpriseInitiative, initiative_id)
    if initiative is None:
        abort(404)

    programme_id = _int_or_none(request.form.get("programme_id"))
    if programme_id is None:
        initiative.linked_strategic_initiative_id = None
        db.session.commit()
        flash("Programme link removed.", "success")
        return redirect(url_for("portfolio.detail", initiative_id=initiative_id))

    programme = db.session.scalar(
        db.select(StrategicInitiative).where(
            StrategicInitiative.id == programme_id,
            StrategicInitiative.organization_id == current_user.organization_id,
            StrategicInitiative.record_kind == "transformation_programme",
        )
    )
    if programme is None:
        flash("That programme could not be found.", "error")
        return redirect(url_for("portfolio.detail", initiative_id=initiative_id))

    initiative.linked_strategic_initiative_id = programme.id
    db.session.commit()
    flash(f"Linked to {programme.name}.", "success")
    return redirect(url_for("portfolio.detail", initiative_id=initiative_id))


# ── Strategic objectives an initiative serves ─────────────────────────────

@portfolio_bp.route("/initiatives/<int:initiative_id>/goals", methods=["POST"])
@login_required
def initiative_goal_add(initiative_id):
    from app.modules.portfolio.services.initiative_goal_links import (
        InitiativeGoalLinkError,
        add_link,
    )

    goal_id = _int_or_none(request.form.get("goal_id"))
    if goal_id is None:
        flash("Choose the goal this initiative serves.", "error")
        return redirect(url_for("portfolio.detail", initiative_id=initiative_id))
    try:
        goal = add_link(initiative_id, goal_id, request.form.get("contribution_level"))
        db.session.commit()
    except InitiativeGoalLinkError as exc:
        db.session.rollback()
        if exc.status == 404 and exc.message == "Initiative not found.":
            abort(404)
        flash(exc.message, "error")
    else:
        flash(f"Linked to {goal.name}.", "success")
    return redirect(url_for("portfolio.detail", initiative_id=initiative_id))


@portfolio_bp.route("/initiatives/<int:initiative_id>/goals/<int:goal_id>/remove", methods=["POST"])
@login_required
def initiative_goal_remove(initiative_id, goal_id):
    from app.modules.portfolio.services.initiative_goal_links import (
        InitiativeGoalLinkError,
        remove_link,
    )

    try:
        remove_link(initiative_id, goal_id)
        db.session.commit()
    except InitiativeGoalLinkError as exc:
        db.session.rollback()
        if exc.status == 404 and exc.message == "Initiative not found.":
            abort(404)
        flash(exc.message, "error")
    else:
        flash("Goal link removed.", "success")
    return redirect(url_for("portfolio.detail", initiative_id=initiative_id))
