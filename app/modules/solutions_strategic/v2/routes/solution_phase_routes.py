from app.services.archimate_backbone import sync_archimate_element
import csv
import io
import logging
from flask import abort, jsonify, request, url_for
from flask_login import current_user, login_required
from app import db
from app.models.solution_models import Solution
from app.decorators import audit_log
from .solution_design_routes import solution_design_bp

logger = logging.getLogger(__name__)


_PRIORITY_NAMES = {"critical": 1, "highest": 1, "high": 2, "medium": 3, "low": 4, "minor": 5}

_SCALE_NAMES = {
    "critical": 5, "very high": 5, "highest": 5, "high": 4,
    "medium": 3, "moderate": 3, "low": 2, "very low": 1, "minimal": 1,
}


def _coerce_scale(raw):
    """Coerce a 1-5 impact/urgency value (int or named level) to an int (5=highest).

    The driver UI/API sends either an int (1-5) or a named level ("high"/...), but
    impact_level/urgency are Integer columns, so a raw string 500s the insert
    ("invalid input syntax for type integer"). Returns None when unset/unparseable.
    """
    if isinstance(raw, str):
        raw = _SCALE_NAMES.get(raw.strip().lower(), raw.strip())
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _coerce_priority(raw):
    """Coerce a priority value to the integer the model expects (1=highest..5).

    Different UI paths send either an int (1-5) or a named level
    ("critical"/"high"/...), so pass either through without 500-ing the insert
    ("invalid input syntax for type integer"). Returns None when unset/unparseable.
    """
    if isinstance(raw, str):
        raw = _PRIORITY_NAMES.get(raw.strip().lower(), raw.strip())
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _validate_entity(data, required_fields):
    """Validate entity data. Returns (errors, 400) or (None, None)."""
    errors = []
    for field in required_fields:
        val = data.get(field, "").strip() if isinstance(data.get(field), str) else data.get(field)
        if not val:
            errors.append(f"{field} is required")
        elif isinstance(val, str) and len(val) > 200:
            errors.append(f"{field} must be 200 characters or less")
    if errors:
        return jsonify({"success": False, "errors": errors}), 400
    return None, None


# ═══════════════════════════════════════════════════════════════════════════════
# Solution Lifecycle CRUD — Risks, Metrics, TCO, Plateaus
# ═══════════════════════════════════════════════════════════════════════════════

# PR 316 fix round, one-risk-register consolidation: solution_risks is the
# superseded store -- the canonical register is `risks` (app/models/risk.py) plus its
# link table `risk_entity_links`, written only through
# app/services/risk_service.py (see that model's own docstring). The create,
# update, delete and CSV-import handlers below call risk_service instead of
# instantiating SolutionRisk directly, so no new row reaches the old table.
# GET above still reads solution_risks -- repointing that read, like the risk
# register and programme screens, is PR 2 scope (CLAUDE.md section 7).
_RISK_INT_TO_LEVEL = {1: "very_low", 2: "low", 3: "medium", 4: "high", 5: "critical"}


def _solution_risk_level_to_int(value, default=3):
    """The exact probability/impact scale app/commands/backfill_solution_risk_merge.py
    already reuses from solution_routes._level_to_int, imported lazily to
    avoid a module-load-time circular import between the two route modules --
    not a second, possibly-inconsistent mapping."""
    from app.modules.solutions_strategic.v2.routes.solution_routes import _level_to_int
    return _level_to_int(value, default)


def _solution_risk_level_from_int(value, default="medium"):
    """The inverse of the mapping above, for echoing an unchanged impact/
    probability string back in a PUT response when the caller does not send
    one. Approximate (4 and 5 both mapped from "high"/"very_high"/"critical"
    on the way in) -- adequate for a value the caller is not changing, and
    this route never claims more than that."""
    return _RISK_INT_TO_LEVEL.get(value, default)


def _resolve_canonical_solution_risk(solution_id, risk_id):
    """Resolve a /solutions/<id>/risks/<risk_id> id to its canonical Risk row.

    GET above still reads solution_risks (PR 2 repoints that reader), so a
    risk created before this fix is still listed under its old solution_risks
    id. That id resolves here via the pointer
    app/commands/backfill_solution_risk_merge.py already sets on every row it
    merges (retired_into_risk_id) -- reusing that pointer rather than adding a
    second lookup helper. A risk created after this fix has no solution_risks
    row at all (the create handler below no longer makes one), so its id
    already names a canonical Risk directly.
    """
    from app.models.risk import Risk
    from app.models.solution_lifecycle_models import SolutionRisk

    legacy = SolutionRisk.query.filter_by(id=risk_id, solution_id=solution_id).first()
    if legacy is not None and legacy.retired_into_risk_id is not None:
        risk = Risk.query.filter_by(
            id=legacy.retired_into_risk_id, solution_id=solution_id
        ).first()
        if risk is not None:
            return risk
    risk = Risk.query.filter_by(id=risk_id, solution_id=solution_id).first()
    if risk is None:
        abort(404)
    return risk


def _solution_risk_dict(risk, *, risk_name, risk_description, impact, probability, mitigation, owner):
    """SolutionRisk.to_dict()'s exact key set, built from the canonical Risk
    instead -- the response shape the existing solution risk tab
    (app/templates/solutions/partials/_edit_risks.html) already reads.
    retired_into_risk_id only ever applies to a superseded row, never to the
    canonical risk itself, so it is always None here."""
    return {
        "id": risk.id,
        "risk_name": risk_name,
        "risk_description": risk_description,
        "impact": impact,
        "probability": probability,
        "mitigation": mitigation,
        "status": risk.status.value,
        "owner": owner,
        "retired_into_risk_id": None,
    }


@solution_design_bp.route("/<int:solution_id>/risks", methods=["GET"])
@login_required
def get_solution_risks(solution_id):
    """List all risks for a solution.

    Reads both halves of the one-risk-register consolidation, so a risk
    created through the POST/CSV-import handlers below does not vanish from
    this same list: the canonical risks linked to this solution
    (risk_entity_links, entity_type="solution", via risk_service.risks_linked_to
    -- the same query the parent-side "Linked risks" reader already uses)
    plus any solution_risks row the backfill
    (app/commands/backfill_solution_risk_merge.py) has not yet merged
    (retired_into_risk_id IS NULL). A merged legacy row is excluded here --
    its replacement already exists on the canonical side, so it is never
    shown twice.
    """
    Solution.query.get_or_404(solution_id)
    from datetime import datetime as _datetime

    from app.models.solution_lifecycle_models import SolutionRisk
    from app.services import risk_service

    canonical_rows = [
        (risk.created_at or _datetime.min, _solution_risk_dict(
            risk,
            risk_name=risk.title,
            risk_description=risk.description,
            impact=_solution_risk_level_from_int(risk.impact),
            probability=_solution_risk_level_from_int(risk.likelihood),
            mitigation=risk.mitigation_plan,
            owner=risk.owner,
        ))
        for risk in risk_service.risks_linked_to("solution", solution_id)
    ]
    legacy_rows = [
        (risk.created_at or _datetime.min, risk.to_dict())
        for risk in SolutionRisk.query.filter_by(solution_id=solution_id)
        .filter(SolutionRisk.retired_into_risk_id.is_(None))
        .all()
    ]
    rows = sorted(canonical_rows + legacy_rows, key=lambda pair: pair[0], reverse=True)
    return jsonify({"success": True, "data": [row[1] for row in rows]})


@solution_design_bp.route("/<int:solution_id>/risks", methods=["POST"])
@login_required
def create_solution_risk(solution_id):
    """Add a risk to a solution.

    Writes through the canonical risk register (app/services/risk_service.py)
    -- the one writer per the one-risk-register consolidation -- rather than
    creating a SolutionRisk row directly. _solution_risk_dict keeps the response the same
    shape SolutionRisk.to_dict() already produced, so the existing screen
    (app/templates/solutions/partials/_edit_risks.html) is unaffected.

    Uses require_entity rather than Solution.query.get_or_404: the latter is
    Query.get() under the hood, which is tenant-scoped only on an identity-map
    MISS -- on a HIT (the solution already loaded elsewhere in this request's
    session, e.g. by an earlier call in the same test or request) it returns
    the cached row with no SQL and no tenant predicate, letting another
    organisation's id through to risk_service below instead of a clean 404.
    require_entity (app/utils/route_guards.py, already used by
    delete_solution_stakeholder in this file) always issues a real, tenant-
    filtered SELECT.
    """
    from app.utils.route_guards import require_entity
    require_entity(Solution, solution_id, description="Solution not found")
    from app.models.risk import RiskStatus
    from app.services import risk_service
    data = request.get_json()
    # Validate required fields — SD-004
    risk_name = (data.get("risk_name") or data.get("name") or "").strip()
    risk_desc = (data.get("risk_description") or "").strip()
    if not risk_name:
        return jsonify({"success": False, "error": "risk_name is required"}), 400
    if not risk_desc:
        return jsonify({"success": False, "error": "risk_description is required"}), 400
    impact = data.get("impact", "medium")
    probability = data.get("probability", "medium")
    mitigation = data.get("mitigation", "")
    owner = data.get("owner", "")
    status = data.get("status", "open")

    risk = risk_service.create_risk(
        solution_id=solution_id,
        title=risk_name,
        description=risk_desc,
        likelihood=_solution_risk_level_to_int(probability),
        impact=_solution_risk_level_to_int(impact),
        owner=owner,
        mitigation_plan=mitigation,
    )
    if status and status != RiskStatus.OPEN.value:
        try:
            risk = risk_service.update_risk_status(risk.id, status)
        except ValueError:
            return jsonify({"success": False, "error": f"Invalid status: {status!r}"}), 400
    risk_service.add_risk_link(risk.id, "solution", solution_id)

    if data.get("archimate_element_id"):
        ae, err = _link_existing_archimate_element(solution_id, data["archimate_element_id"], "Assessment", "Motivation")
        if err:
            return err
    else:
        _sync_archimate_element(solution_id, ae_type="Assessment", ae_layer="Motivation", name=risk_desc[:100], description=risk_desc)
    db.session.commit()
    return jsonify({"success": True, "data": _solution_risk_dict(
        risk, risk_name=risk_name, risk_description=risk_desc,
        impact=impact, probability=probability, mitigation=mitigation, owner=owner,
    )}), 201


@solution_design_bp.route("/<int:solution_id>/risks/<int:risk_id>", methods=["PUT"])
@login_required
def update_solution_risk(solution_id, risk_id):
    """Update a risk. Writes through risk_service -- see create_solution_risk."""
    from app.utils.route_guards import require_entity
    require_entity(Solution, solution_id, description="Solution not found")
    from app.services import risk_service
    risk = _resolve_canonical_solution_risk(solution_id, risk_id)
    data = request.get_json()

    edits = {}
    if "risk_name" in data:
        edits["title"] = data["risk_name"]
    if "risk_description" in data:
        edits["description"] = data["risk_description"]
    if "mitigation" in data:
        edits["mitigation_plan"] = data["mitigation"]
    if "owner" in data:
        edits["owner"] = data["owner"]
    if "probability" in data:
        edits["likelihood"] = _solution_risk_level_to_int(data["probability"])
    if "impact" in data:
        edits["impact"] = _solution_risk_level_to_int(data["impact"])
    if edits:
        risk = risk_service.update_risk(risk.id, **edits)
    if "status" in data and data["status"]:
        try:
            risk = risk_service.update_risk_status(risk.id, data["status"])
        except ValueError:
            return jsonify({"success": False, "error": f"Invalid status: {data['status']!r}"}), 400

    return jsonify({"success": True, "data": _solution_risk_dict(
        risk,
        risk_name=data.get("risk_name", risk.title),
        risk_description=data.get("risk_description", risk.description),
        impact=data.get("impact", _solution_risk_level_from_int(risk.impact)),
        probability=data.get("probability", _solution_risk_level_from_int(risk.likelihood)),
        mitigation=data.get("mitigation", risk.mitigation_plan),
        owner=data.get("owner", risk.owner),
    )})


@solution_design_bp.route("/<int:solution_id>/risks/<int:risk_id>", methods=["DELETE"])
@login_required
def delete_solution_risk(solution_id, risk_id):
    """Delete a risk. Writes through risk_service -- see create_solution_risk."""
    from app.utils.route_guards import require_entity
    require_entity(Solution, solution_id, description="Solution not found")
    from app.services import risk_service
    risk = _resolve_canonical_solution_risk(solution_id, risk_id)
    risk_service.delete_risk(risk.id)
    return jsonify({"success": True})


_VALID_IMPACTS = {"low", "medium", "high", "critical"}
_VALID_PROBABILITIES = {"low", "medium", "high"}
_VALID_STATUSES = {"open", "mitigated", "accepted", "closed"}


@solution_design_bp.route("/<int:solution_id>/risks/import", methods=["POST"])
@login_required
def import_solution_risks(solution_id):
    """Bulk-import risks from a CSV file.

    Expected columns (case-insensitive header row required):
      description   — required — risk description text
      name          — optional — short risk name
      impact        — optional — low|medium|high|critical (default: medium)
      probability   — optional — low|medium|high (default: medium)
      mitigation    — optional — mitigation text
      status        — optional — open|mitigated|accepted|closed (default: open)
      owner         — optional — owner name or email

    Returns: {created, skipped, errors: [{row, reason}]}
    Limits: 2 MB file, 500 rows.
    """
    from app.utils.route_guards import require_entity
    require_entity(Solution, solution_id, description="Solution not found")
    from app.services import risk_service

    file_storage = request.files.get("file")
    if not file_storage or not file_storage.filename:
        return jsonify({"success": False, "error": "No file provided"}), 400
    if not file_storage.filename.lower().endswith(".csv"):
        return jsonify({"success": False, "error": "Only CSV files are accepted"}), 400

    content = file_storage.read()
    if len(content) > 2 * 1024 * 1024:
        return jsonify({"success": False, "error": "File exceeds 2 MB limit"}), 400

    try:
        text = content.decode("utf-8-sig")  # strip BOM if present
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    # Normalise headers to lowercase stripped
    if reader.fieldnames is None:
        return jsonify({"success": False, "error": "CSV has no header row"}), 400
    reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]

    if "description" not in reader.fieldnames:
        return jsonify({"success": False, "error": "CSV must have a 'description' column"}), 400

    created = 0
    skipped = 0
    errors = []

    for idx, row in enumerate(reader, start=2):  # row 1 = header
        if idx > 501:
            errors.append({"row": idx, "reason": "Row limit (500) exceeded — remaining rows skipped"})
            break

        desc = (row.get("description") or "").strip()
        if not desc:
            skipped += 1
            continue

        impact = (row.get("impact") or "medium").strip().lower()
        if impact not in _VALID_IMPACTS:
            impact = "medium"

        probability = (row.get("probability") or "medium").strip().lower()
        if probability not in _VALID_PROBABILITIES:
            probability = "medium"

        status = (row.get("status") or "open").strip().lower()
        if status not in _VALID_STATUSES:
            status = "open"

        title = (row.get("name") or "").strip() or desc[:255]
        try:
            risk = risk_service.create_risk(
                solution_id=solution_id,
                title=title,
                description=desc,
                likelihood=_solution_risk_level_to_int(probability),
                impact=_solution_risk_level_to_int(impact),
                owner=(row.get("owner") or "").strip() or None,
                mitigation_plan=(row.get("mitigation") or "").strip() or None,
            )
            if status != "open":
                risk = risk_service.update_risk_status(risk.id, status)
            risk_service.add_risk_link(risk.id, "solution", solution_id)
            created += 1
        except Exception as e:
            db.session.rollback()
            errors.append({"row": idx, "reason": str(e)})

    return jsonify({
        "success": True,
        "created": created,
        "skipped": skipped,
        "errors": errors,
    }), 200


@solution_design_bp.route("/<int:solution_id>/metrics", methods=["GET"])
@login_required
def get_solution_metrics(solution_id):
    """List all success metrics for a solution."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_lifecycle_models import SolutionMetric
    metrics = SolutionMetric.query.filter_by(solution_id=solution_id).all()
    return jsonify({"success": True, "data": [m.to_dict() for m in metrics]})


@solution_design_bp.route("/<int:solution_id>/metrics", methods=["POST"])
@login_required
def create_solution_metric(solution_id):
    """Add a success metric."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_lifecycle_models import SolutionMetric
    data = request.get_json() or {}
    # FAR-004: Validate required fields
    err_response, err_status = _validate_entity(data, ["name"])
    if err_response:
        return err_response, err_status
    metric = SolutionMetric(
        solution_id=solution_id,
        name=data.get("name", ""),
        unit=data.get("unit", ""),
        baseline_value=data.get("baseline_value", ""),
        target_value=data.get("target_value", ""),
        actual_value=data.get("actual_value"),
        status=data.get("status", "not_measured"),
        notes=data.get("notes", ""),
    )
    db.session.add(metric)
    db.session.flush()
    if data.get("archimate_element_id"):
        ae, err = _link_existing_archimate_element(solution_id, data["archimate_element_id"], "Outcome", "Motivation")
        if err:
            db.session.rollback()
            return err
    else:
        _sync_archimate_element(solution_id, ae_type="Outcome", ae_layer="Motivation", name=metric.name, description=metric.notes or metric.name)
    db.session.commit()
    return jsonify({"success": True, "data": metric.to_dict()}), 201


@solution_design_bp.route("/<int:solution_id>/metrics/<int:metric_id>", methods=["PUT"])
@login_required
def update_solution_metric(solution_id, metric_id):
    """Update a success metric."""
    from app.models.solution_lifecycle_models import SolutionMetric
    metric = SolutionMetric.query.filter_by(id=metric_id, solution_id=solution_id).first_or_404()
    data = request.get_json()
    for field in ["name", "unit", "baseline_value", "target_value", "actual_value", "status", "notes"]:
        if field in data:
            setattr(metric, field, data[field])
    if "measurement_date" in data and data["measurement_date"]:
        from datetime import date
        try:
            metric.measurement_date = date.fromisoformat(data["measurement_date"])
        except (ValueError, TypeError):
            db.session.rollback()
            return jsonify({"success": False, "error": "measurement_date must be in YYYY-MM-DD format"}), 400
    db.session.commit()
    return jsonify({"success": True, "data": metric.to_dict()})


@solution_design_bp.route("/<int:solution_id>/metrics/<int:metric_id>", methods=["DELETE"])
@login_required
def delete_solution_metric(solution_id, metric_id):
    """Delete a metric."""
    from app.models.solution_lifecycle_models import SolutionMetric
    metric = SolutionMetric.query.filter_by(id=metric_id, solution_id=solution_id).first_or_404()
    db.session.delete(metric)
    db.session.commit()
    return jsonify({"success": True})


@solution_design_bp.route("/<int:solution_id>/tco", methods=["GET"])
@login_required
def get_solution_tco(solution_id):
    """Get all TCO line items for a solution."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_lifecycle_models import SolutionTCOItem
    items = SolutionTCOItem.query.filter_by(solution_id=solution_id).order_by(
        SolutionTCOItem.option_label, SolutionTCOItem.year, SolutionTCOItem.cost_category
    ).all()
    return jsonify({"success": True, "data": [i.to_dict() for i in items]})


@solution_design_bp.route("/<int:solution_id>/tco", methods=["POST"])
@login_required
def create_solution_tco_item(solution_id):
    """Add a TCO line item."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_lifecycle_models import SolutionTCOItem
    data = request.get_json() or {}
    # FAR-004: Validate required fields
    err_response, err_status = _validate_entity(data, ["cost_category"])
    if err_response:
        return err_response, err_status
    item = SolutionTCOItem(
        solution_id=solution_id,
        option_label=data.get("option_label", "Proposed"),
        cost_category=data.get("cost_category", ""),
        is_recurring=data.get("is_recurring", True),
        year=data.get("year", 1),
        amount=data.get("amount", 0),
        notes=data.get("notes", ""),
    )
    db.session.add(item)
    db.session.commit()
    return jsonify({"success": True, "data": item.to_dict()}), 201


@solution_design_bp.route("/<int:solution_id>/plateaus", methods=["GET"])
@login_required
def get_solution_plateaus(solution_id):
    """Get transition architecture plateaus."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_lifecycle_models import SolutionPlateau
    plateaus = SolutionPlateau.query.filter_by(solution_id=solution_id).order_by(
        SolutionPlateau.order
    ).all()
    return jsonify({"success": True, "data": [p.to_dict() for p in plateaus]})


@solution_design_bp.route("/<int:solution_id>/plateaus", methods=["POST"])
@login_required
def create_solution_plateau(solution_id):
    """Add a transition architecture plateau."""
    solution = Solution.query.get_or_404(solution_id)
    from app.models.solution_lifecycle_models import SolutionPlateau
    data = request.get_json() or {}
    # FAR-004: Validate required fields
    err_response, err_status = _validate_entity(data, ["name"])
    if err_response:
        return err_response, err_status
    plateau = SolutionPlateau(
        solution_id=solution_id,
        name=data.get("name", ""),
        description=data.get("description", ""),
        order=data.get("order", 0),
    )
    if data.get("target_date"):
        from datetime import date
        try:
            plateau.target_date = date.fromisoformat(data["target_date"])
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "target_date must be in YYYY-MM-DD format"}), 400
    db.session.add(plateau)
    db.session.flush()
    if data.get("archimate_element_id"):
        ae, err = _link_existing_archimate_element(solution_id, data["archimate_element_id"], "Plateau", "Implementation")
        if err:
            db.session.rollback()
            return err
    else:
        _sync_archimate_element(solution_id, ae_type="Plateau", ae_layer="Implementation", name=plateau.name, description=plateau.description)
        # JWIRE-002: Derive Gap + WorkPackage alongside every Plateau.
        # In ArchiMate 3.2 a Plateau is the target state reached by closing Gaps via WorkPackages.
        # Without these, gap_analysis / transition_roadmap / work_packages blueprint sections
        # always score 0% for J7 wizard solutions.
        _sync_archimate_element(solution_id, ae_type="Gap", ae_layer="Implementation", name=f"Gap: current state \u2192 {plateau.name}", description=f"Architecture gap to be closed by transition to {plateau.name}",)
        _sync_archimate_element(solution_id, ae_type="WorkPackage", ae_layer="Implementation", name=f"Implement {plateau.name}", description=f"Work package delivering the transition to {plateau.name}",)
    solution.section_scores = None  # invalidate cache so blueprint score reflects new plateau/gap/workpackage
    db.session.commit()
    return jsonify({"success": True, "data": plateau.to_dict()}), 201


@solution_design_bp.route("/<int:solution_id>/tco/<int:tco_id>", methods=["PUT"])
@login_required
def update_solution_tco_item(solution_id, tco_id):
    """Update a TCO line item."""
    # The child row carries no organisation of its own; the solution does. Load it
    # first so another organisation's solution id is a 404 before its rows are reached.
    Solution.query.get_or_404(solution_id)
    from app.models.solution_lifecycle_models import SolutionTCOItem
    item = SolutionTCOItem.query.filter_by(id=tco_id, solution_id=solution_id).first_or_404()
    data = request.get_json()
    for field in ["option_label", "cost_category", "is_recurring", "year", "amount", "notes"]:
        if field in data:
            setattr(item, field, data[field])
    db.session.commit()
    return jsonify({"success": True, "data": item.to_dict()})


@solution_design_bp.route("/<int:solution_id>/tco/<int:tco_id>", methods=["DELETE"])
@login_required
def delete_solution_tco_item(solution_id, tco_id):
    """Delete a TCO line item."""
    # The child row carries no organisation of its own; the solution does. Load it
    # first so another organisation's solution id is a 404 before its rows are reached.
    Solution.query.get_or_404(solution_id)
    from app.models.solution_lifecycle_models import SolutionTCOItem
    item = SolutionTCOItem.query.filter_by(id=tco_id, solution_id=solution_id).first_or_404()
    db.session.delete(item)
    db.session.commit()
    return jsonify({"success": True})


@solution_design_bp.route("/<int:solution_id>/plateaus/<int:plateau_id>", methods=["PUT"])
@login_required
def update_solution_plateau(solution_id, plateau_id):
    """Update a transition architecture plateau."""
    # The child row carries no organisation of its own; the solution does. Load it
    # first so another organisation's solution id is a 404 before its rows are reached.
    Solution.query.get_or_404(solution_id)
    from app.models.solution_lifecycle_models import SolutionPlateau
    plateau = SolutionPlateau.query.filter_by(id=plateau_id, solution_id=solution_id).first_or_404()
    data = request.get_json()
    for field in ["name", "description", "order"]:
        if field in data:
            setattr(plateau, field, data[field])
    if "target_date" in data:
        if data["target_date"]:
            from datetime import date
            try:
                plateau.target_date = date.fromisoformat(data["target_date"])
            except (ValueError, TypeError):
                db.session.rollback()
                return jsonify({"success": False, "error": "target_date must be in YYYY-MM-DD format"}), 400
        else:
            plateau.target_date = None
    db.session.commit()
    return jsonify({"success": True, "data": plateau.to_dict()})


@solution_design_bp.route("/<int:solution_id>/plateaus/<int:plateau_id>", methods=["DELETE"])
@login_required
def delete_solution_plateau(solution_id, plateau_id):
    """Delete a transition architecture plateau."""
    # The child row carries no organisation of its own; the solution does. Load it
    # first so another organisation's solution id is a 404 before its rows are reached.
    Solution.query.get_or_404(solution_id)
    from app.models.solution_lifecycle_models import SolutionPlateau
    plateau = SolutionPlateau.query.filter_by(id=plateau_id, solution_id=solution_id).first_or_404()
    db.session.delete(plateau)
    db.session.commit()
    return jsonify({"success": True})


# ═══════════════════════════════════════════════════════════════════════════════
# SAD Layer CRUD — Stakeholders, Business/App/Tech Elements, Quality Attrs, SLAs
# These are the ArchiMate 3.2 layer elements stored in solution_sad_models.
# ═══════════════════════════════════════════════════════════════════════════════


@solution_design_bp.route("/<int:solution_id>/stakeholders", methods=["GET"])
@login_required
def get_solution_stakeholders(solution_id):
    """List stakeholders for a solution (Phase A — Motivation layer)."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionStakeholderSAD
    rows = SolutionStakeholderSAD.query.filter_by(solution_id=solution_id).order_by(SolutionStakeholderSAD.id).all()
    return jsonify({"success": True, "data": [r.to_dict() for r in rows]})


@solution_design_bp.route("/<int:solution_id>/stakeholders", methods=["POST"])
@login_required
def create_solution_stakeholder(solution_id):
    """Add a stakeholder (Phase A — Motivation layer)."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionStakeholderSAD
    data = request.get_json() or {}
    err_response, err_status = _validate_entity(data, ["name"])
    if err_response:
        return err_response, err_status
    row = SolutionStakeholderSAD(
        solution_id=solution_id,
        name=data.get("name", ""),
        role=data.get("role", ""),
        organization=data.get("organization", ""),
        influence_level=data.get("influence_level", "medium"),
        interest_level=data.get("interest_level", "medium"),
        engagement_strategy=data.get("engagement_strategy", ""),
        notes=data.get("notes", ""),
        created_by_id=current_user.id,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"success": True, "data": row.to_dict()}), 201


@solution_design_bp.route("/<int:solution_id>/stakeholders/<int:row_id>", methods=["DELETE"])
@login_required
def delete_solution_stakeholder(solution_id, row_id):
    """Delete a stakeholder."""
    from app.models.solution_sad_models import SolutionStakeholderSAD
    from app.utils.route_guards import require_entity

    # The solution is tenant-fenced: a solution id from another organisation is a 404,
    # so its stakeholder rows are never reached through the URL.
    require_entity(Solution, solution_id, description="Solution not found")
    row = SolutionStakeholderSAD.query.filter_by(id=row_id, solution_id=solution_id).first_or_404()
    db.session.delete(row)
    db.session.commit()
    return jsonify({"success": True})


@solution_design_bp.route("/<int:solution_id>/business-elements", methods=["GET"])
@login_required
def get_solution_business_elements(solution_id):
    """List business layer elements (Phase B)."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionBusinessElement
    rows = SolutionBusinessElement.query.filter_by(solution_id=solution_id).order_by(SolutionBusinessElement.id).all()
    return jsonify({"success": True, "data": [r.to_dict() for r in rows]})


@solution_design_bp.route("/<int:solution_id>/business-elements", methods=["POST"])
@login_required
def create_solution_business_element(solution_id):
    """Add a business layer element (Phase B)."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionBusinessElement
    data = request.get_json() or {}
    # FAR-004: Validate required fields
    err_response, err_status = _validate_entity(data, ["name"])
    if err_response:
        return err_response, err_status
    row = SolutionBusinessElement(
        solution_id=solution_id,
        element_type=data.get("element_type", "process"),
        name=data.get("name", ""),
        description=data.get("description", ""),
        owner=data.get("owner", ""),
        notes=data.get("notes", ""),
    )
    row.archimate_element_id = data.get('archimate_element_id')
    row.archimate_layer = data.get('archimate_layer')
    row.archimate_element_type = data.get('archimate_element_type')
    db.session.add(row)
    db.session.commit()
    return jsonify({"success": True, "data": row.to_dict()}), 201


@solution_design_bp.route("/<int:solution_id>/business-elements/<int:row_id>", methods=["DELETE"])
@login_required
def delete_solution_business_element(solution_id, row_id):
    """Delete a business layer element."""
    # The child row carries no organisation of its own; the solution does. Load it
    # first so another organisation's solution id is a 404 before its rows are reached.
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionBusinessElement
    row = SolutionBusinessElement.query.filter_by(id=row_id, solution_id=solution_id).first_or_404()
    db.session.delete(row)
    db.session.commit()
    return jsonify({"success": True})


@solution_design_bp.route("/<int:solution_id>/app-elements", methods=["GET"])
@login_required
def get_solution_app_elements(solution_id):
    """List application layer elements (Phase C)."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionAppElement
    rows = SolutionAppElement.query.filter_by(solution_id=solution_id).order_by(SolutionAppElement.id).all()
    return jsonify({"success": True, "data": [r.to_dict() for r in rows]})


@solution_design_bp.route("/<int:solution_id>/app-elements", methods=["POST"])
@login_required
def create_solution_app_element(solution_id):
    """Add an application layer element (Phase C)."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionAppElement
    data = request.get_json() or {}
    # FAR-004: Validate required fields
    err_response, err_status = _validate_entity(data, ["name"])
    if err_response:
        return err_response, err_status
    row = SolutionAppElement(
        solution_id=solution_id,
        element_type=data.get("element_type", "component"),
        name=data.get("name", ""),
        description=data.get("description", ""),
        technology=data.get("technology", ""),
        notes=data.get("notes", ""),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"success": True, "data": row.to_dict()}), 201


@solution_design_bp.route("/<int:solution_id>/app-elements/<int:row_id>", methods=["DELETE"])
@login_required
def delete_solution_app_element(solution_id, row_id):
    """Delete an application layer element."""
    # The child row carries no organisation of its own; the solution does. Load it
    # first so another organisation's solution id is a 404 before its rows are reached.
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionAppElement
    row = SolutionAppElement.query.filter_by(id=row_id, solution_id=solution_id).first_or_404()
    db.session.delete(row)
    db.session.commit()
    return jsonify({"success": True})


@solution_design_bp.route("/<int:solution_id>/tech-elements", methods=["GET"])
@login_required
def get_solution_tech_elements(solution_id):
    """List technology layer elements (Phase D)."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionTechElement
    rows = SolutionTechElement.query.filter_by(solution_id=solution_id).order_by(SolutionTechElement.id).all()
    return jsonify({"success": True, "data": [r.to_dict() for r in rows]})


@solution_design_bp.route("/<int:solution_id>/tech-elements", methods=["POST"])
@login_required
def create_solution_tech_element(solution_id):
    """Add a technology layer element (Phase D)."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionTechElement
    data = request.get_json() or {}
    # FAR-004: Validate required fields
    err_response, err_status = _validate_entity(data, ["name"])
    if err_response:
        return err_response, err_status
    row = SolutionTechElement(
        solution_id=solution_id,
        element_type=data.get("element_type", "node"),
        name=data.get("name", ""),
        description=data.get("description", ""),
        specification=data.get("specification", ""),
        notes=data.get("notes", ""),
    )
    row.archimate_element_id = data.get('archimate_element_id')
    row.archimate_layer = data.get('archimate_layer')
    row.archimate_element_type = data.get('archimate_element_type')
    db.session.add(row)
    db.session.commit()
    return jsonify({"success": True, "data": row.to_dict()}), 201


@solution_design_bp.route("/<int:solution_id>/tech-elements/<int:row_id>", methods=["DELETE"])
@login_required
def delete_solution_tech_element(solution_id, row_id):
    """Delete a technology layer element."""
    # The child row carries no organisation of its own; the solution does. Load it
    # first so another organisation's solution id is a 404 before its rows are reached.
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionTechElement
    row = SolutionTechElement.query.filter_by(id=row_id, solution_id=solution_id).first_or_404()
    db.session.delete(row)
    db.session.commit()
    return jsonify({"success": True})


@solution_design_bp.route("/<int:solution_id>/quality-attributes", methods=["GET"])
@login_required
def get_solution_quality_attributes(solution_id):
    """List quality attributes (Phase E)."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionQualityAttribute
    rows = SolutionQualityAttribute.query.filter_by(solution_id=solution_id).order_by(SolutionQualityAttribute.id).all()
    return jsonify({"success": True, "data": [r.to_dict() for r in rows]})


@solution_design_bp.route("/<int:solution_id>/quality-attributes", methods=["POST"])
@login_required
def create_solution_quality_attribute(solution_id):
    """Add a quality attribute (Phase E)."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionQualityAttribute
    data = request.get_json() or {}
    # FAR-004: Validate required fields
    err_response, err_status = _validate_entity(data, ["attribute_name"])
    if err_response:
        return err_response, err_status
    row = SolutionQualityAttribute(
        solution_id=solution_id,
        attribute_name=data.get("attribute_name", ""),
        attribute_type=data.get("attribute_type", "performance"),
        target_value=data.get("target_value", ""),
        verification_method=data.get("verification_method", ""),
        notes=data.get("notes", ""),
        created_by_id=current_user.id,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"success": True, "data": row.to_dict()}), 201


@solution_design_bp.route("/<int:solution_id>/quality-attributes/<int:row_id>", methods=["DELETE"])
@login_required
def delete_solution_quality_attribute(solution_id, row_id):
    """Delete a quality attribute."""
    # The child row carries no organisation of its own; the solution does. Load it
    # first so another organisation's solution id is a 404 before its rows are reached.
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionQualityAttribute
    row = SolutionQualityAttribute.query.filter_by(id=row_id, solution_id=solution_id).first_or_404()
    db.session.delete(row)
    db.session.commit()
    return jsonify({"success": True})


@solution_design_bp.route("/<int:solution_id>/slas", methods=["GET"])
@login_required
def get_solution_slas(solution_id):
    """List SLAs (Phase E)."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionSLA
    rows = SolutionSLA.query.filter_by(solution_id=solution_id).order_by(SolutionSLA.id).all()
    return jsonify({"success": True, "data": [r.to_dict() for r in rows]})


@solution_design_bp.route("/<int:solution_id>/slas", methods=["POST"])
@login_required
def create_solution_sla(solution_id):
    """Add an SLA (Phase E)."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionSLA
    data = request.get_json() or {}
    # FAR-004: Validate required fields
    err_response, err_status = _validate_entity(data, ["sla_name"])
    if err_response:
        return err_response, err_status
    row = SolutionSLA(
        solution_id=solution_id,
        sla_name=data.get("sla_name", ""),
        availability_target=data.get("availability_target"),
        response_time_ms=data.get("response_time_ms"),
        throughput_tps=data.get("throughput_tps"),
        rto_hours=data.get("rto_hours"),
        rpo_hours=data.get("rpo_hours"),
        support_hours=data.get("support_hours", ""),
        created_by_id=current_user.id,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"success": True, "data": row.to_dict()}), 201


@solution_design_bp.route("/<int:solution_id>/slas/<int:row_id>", methods=["DELETE"])
@login_required
def delete_solution_sla(solution_id, row_id):
    """Delete an SLA."""
    # The child row carries no organisation of its own; the solution does. Load it
    # first so another organisation's solution id is a 404 before its rows are reached.
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionSLA
    row = SolutionSLA.query.filter_by(id=row_id, solution_id=solution_id).first_or_404()
    db.session.delete(row)
    db.session.commit()
    return jsonify({"success": True})


# ═══════════════════════════════════════════════════════════════════════════════
# Solution Motivation Layer CRUD — Drivers, Goals, Constraints
# These live under SolutionProblemDefinition; helper auto-creates the chain.
# ═══════════════════════════════════════════════════════════════════════════════


def _get_or_create_problem_def(solution):
    """Return the SolutionProblemDefinition for a solution, creating the chain if needed."""
    from app.models.solution_architect_models import (
        SolutionAnalysisSession,
        SolutionProblemDefinition,
    )

    if solution.analysis_session_id:
        session_obj = SolutionAnalysisSession.query.get(solution.analysis_session_id)
        if session_obj and session_obj.problem_definition:
            return session_obj.problem_definition

    # Create analysis session + problem definition on the fly
    session_obj = SolutionAnalysisSession(
        name=f"{solution.name} Analysis",
        created_by_id=current_user.id,
    )
    db.session.add(session_obj)
    db.session.flush()

    pd = SolutionProblemDefinition(
        session_id=session_obj.id,
        problem_description=solution.description or solution.name,
    )
    db.session.add(pd)
    db.session.flush()

    solution.analysis_session_id = session_obj.id
    db.session.flush()
    return pd


def _get_reasoning_state_dict(solution_id: int) -> dict:
    """Return the most recent AI reasoning state for a solution as a display dict."""
    try:
        from app.models.solution_reasoning import SolutionAIReasoningState
        state = SolutionAIReasoningState.query.filter_by(
            solution_id=solution_id
        ).order_by(SolutionAIReasoningState.created_at.desc()).first()
        if not state:
            return None
        ctx = state.context_snapshot or {}
        trace = state.reasoning_trace or {}
        return {
            "id": state.id,
            "adm_phase": state.adm_phase,
            "created_at": state.created_at.strftime("%Y-%m-%d %H:%M UTC") if state.created_at else None,
            "confidence_pct": round((state.confidence_score_pct or 0) * 100) if (state.confidence_score_pct or 0) <= 1 else round(state.confidence_score_pct or 0),
            "llm_provider": ctx.get("llm_provider") or ctx.get("provider") or "AI",
            "entities_created": ctx.get("entities_created") or {},
            "data_sources": list((state.data_sources_used or {}).keys()),
            "steps_count": trace.get("total_steps") or len(trace.get("steps") or []),
            "execution_ms": trace.get("execution_time_ms"),
            "user_feedback": state.user_feedback,
        }
    except Exception as e:
        db.session.rollback()
        logger.debug(f"Could not fetch reasoning state: {e}")
        return None


def _driver_to_dict(d):
    return {
        "id": d.id,
        "name": d.name,
        "description": d.description,
        "driver_type": d.driver_type.value if d.driver_type else "internal",
        "impact_level": d.impact_level,
        "urgency": d.urgency,
        "source": d.source,
        "ai_generated": bool(d.ai_generated),
    }


def _goal_to_dict(g):
    return {
        "id": g.id,
        "name": g.name,
        "description": g.description,
        "priority": g.priority,
        "measurement_criteria": g.measurement_criteria,
        "ai_generated": bool(g.ai_generated),
    }


def _constraint_to_dict(c):
    return {
        "id": c.id,
        "name": c.name,
        "description": c.description,
        "constraint_type": c.constraint_type.value if c.constraint_type else "technical",
        "value": c.value,
        "severity": c.severity,
        "ai_generated": bool(c.ai_generated),
    }


def _requirement_to_dict(r):
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "requirement_type": r.requirement_type.value if r.requirement_type else "functional",
        "priority": r.priority,
        "is_mandatory": r.is_mandatory,
        "source": r.source,
        "rationale": r.rationale,
        "acceptance_criteria": r.acceptance_criteria,
        "ai_generated": r.ai_generated,
        # REQ-001: workflow / triage fields
        "status": r.status or "open",
        "owner": r.owner or "",
        "assumptions": r.assumptions or "",
        "dependencies_text": r.dependencies_text or "",
        "moscow_priority": r.moscow_priority or "",
        "togaf_phase": r.togaf_phase or "",
    }


def _recommendation_to_dict(r):
    out = {
        "id": r.id,
        "name": r.name,
        "option_type": r.option_type.value if r.option_type else "build",
        "is_recommended": bool(r.is_recommended) if r.is_recommended else False,
        "rank": r.rank,
        "score": float(r.score) if r.score else None,
        "confidence": float(r.confidence) if r.confidence else None,
        "estimated_cost_min": float(r.estimated_cost_min) if r.estimated_cost_min else None,
        "estimated_cost_max": float(r.estimated_cost_max) if r.estimated_cost_max else None,
        "cost_currency": r.cost_currency or "GBP",
        "timeline_months": r.timeline_months,
        "pros": r.pros or [],
        "cons": r.cons or [],
        "risks": r.risks or [],
        "next_steps": r.next_steps or [],
        "justification": r.justification,
        "vendor_products": r.vendor_products or [],
        "existing_apps": r.existing_apps or [],
        "generated_at": r.generated_at.isoformat() if r.generated_at else None,
    }
    if getattr(r, "data_sources", None) and isinstance(r.data_sources, dict):
        out["data_sources"] = r.data_sources
    return out


def _sync_archimate_element(solution_id, *, ae_type, ae_layer, name, description=None):
    """ARCH-LINK-1: create the ArchiMateElement and both junctions for a domain row.

    AGENTS.md: "ArchiMate is the backbone, not a view. Every backend CREATE for a
    motivation entity must call _sync_archimate_element() so a matching
    ArchiMateElement row exists ... the field IS the element." Traceability, impact
    analysis, line of sight and every capability lens read from that backbone, so a
    missing element is a silently incomplete answer on several screens at once.

    Idempotent on (solution_id, name, type); returns the element.

    Three defects fixed here, all of which made the rule unenforceable:

    *organization_id was never set.* ArchiMateElement carries TenantMixin, whose
    organization_id is NOT NULL and is populated by the tenant middleware's
    before_flush hook -- which only runs inside a request with g.current_org_id
    set. Outside one (CLI, scheduler, importer, seeder, test) every call raised
    NotNullViolation, the blanket except swallowed it, and all nine call sites
    discard the return value. So the domain row committed and its element silently
    did not. Worse, the failed flush aborts the transaction, so the next statement
    fails with InFailedSqlTransaction and one silent miss cascades. The
    organisation is now taken from the Solution being synced, which is correct
    both inside a request and outside one.

    *The existing-element lookup used Query.get().* AGENTS.md documents that
    Query.get()/Session.get() are tenant-scoped only on an identity-map MISS; on a
    hit they return the cached object with no SQL, so no tenant predicate is
    applied. In a function that resolves by id and hands the row to a caller, that
    is exactly the shape that can return another organisation's element.

    *The parameters were positional, and a second definition takes them in a
    different order.* solution_ai_orchestrator._sync_archimate_element is
    (solution_id, name, element_type, layer). Calling either with the other's
    convention silently transposes type and name -- a Driver named "Regulatory
    pressure" becomes a "Regulatory pressure" named "Driver". Nothing fails; the
    backbone is simply wrong and every downstream view inherits it. Keyword-only
    parameters make the mistake impossible to express.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_element import SolutionElement
    from app.models.solution_models import SolutionArchiMateElement

    _ELEMENT_TABLE_MAP = {
        "WorkPackage": "work_packages",
        "ImplementationEvent": "implementation_events",
        "AssessmentResult": "assessment_results",
    }

    from app.models.solution_models import Solution

    # The element belongs to the solution's organisation. Read it explicitly rather
    # than relying on the tenant middleware's before_flush hook, which is only
    # installed inside a request -- that dependency is what made every non-request
    # call fail.
    organization_id = db.session.execute(
        db.select(Solution.organization_id).where(Solution.id == solution_id)
    ).scalar_one_or_none()
    if organization_id is None:
        raise ValueError(
            f"cannot sync an ArchiMate element for solution {solution_id}: "
            "no such solution, or it has no organisation"
        )

    try:
        # Idempotency guard: check if this solution already has an element with (name, type).
        # The previous guard (filter_by element_id=ae.id after flush) was dead code — ae.id is
        # always a brand-new value at that point, so the check never fired.
        existing_sae = (
            db.session.query(SolutionArchiMateElement)
            .join(ArchiMateElement, ArchiMateElement.id == SolutionArchiMateElement.element_id)
            .filter(
                SolutionArchiMateElement.solution_id == solution_id,
                ArchiMateElement.name == name,
                ArchiMateElement.type == ae_type,
            )
            .first()
        )
        if existing_sae:
            # Explicit filtered query, never Query.get(): on an identity-map hit
            # .get() emits no SQL and so applies no tenant predicate.
            return db.session.execute(
                db.select(ArchiMateElement).where(
                    ArchiMateElement.id == existing_sae.element_id,
                    ArchiMateElement.organization_id == organization_id,
                )
            ).scalar_one_or_none()

        ae = ArchiMateElement(
            name=name,
            type=ae_type,
            layer=ae_layer,
            organization_id=organization_id,
            description=description or f"{ae_type}: {name}",
        )
        db.session.add(ae)
        db.session.flush()
        existing_se = SolutionElement.query.filter_by(
            solution_id=solution_id, archimate_element_id=ae.id
        ).first()
        if not existing_se:
            # No organization_id here: SolutionElement is a plain db.Model with no
            # TenantMixin. It is reachable only through solution_id and
            # archimate_element_id, both of which are tenant-scoped, so the junction
            # inherits their scoping -- but it is worth knowing it has none of its
            # own.
            db.session.add(SolutionElement(
                solution_id=solution_id,
                archimate_element_id=ae.id,
                layer=ae_layer,
            ))
        # SolutionArchiMateElement is what scoring queries — keep both junctions in
        # sync. A row present in one and absent from the other is worse than one
        # missing from both: the element shows on the layer views and vanishes from
        # scoring, and the discrepancy looks like a scoring bug.
        # Also not tenant-scoped in its own right; same reasoning as SolutionElement
        # above. Both junctions are reachable only through tenant-scoped parents.
        db.session.add(SolutionArchiMateElement(
            solution_id=solution_id,
            element_id=ae.id,
            layer_type=ae_layer.lower(),
            element_name=name,
            element_table=_ELEMENT_TABLE_MAP.get(ae_type, ae_type.lower() + 's'),
            is_new_element=True,
        ))
        return ae
    except Exception as exc:
        # Logged at ERROR, not WARNING, and re-raised. This used to return None,
        # and not one of the nine call sites checks the return value -- so a failure
        # left the domain row committed with no element and told nobody. The failed
        # flush also aborts the transaction, so the next statement dies with
        # InFailedSqlTransaction and the real cause is three frames upstream.
        # Raising keeps the record and its element atomic: either both exist or the
        # request fails loudly.
        logger.error(
            "ARCH-LINK-1 sync failed for %s/%s on solution %s: %s",
            ae_type, name, solution_id, exc,
        )
        raise


def _link_existing_archimate_element(solution_id, archimate_element_id, expected_type, expected_layer):
    """Link an existing ArchiMate element to a solution instead of creating a new one.
    Returns (element, error_response) — if error_response is not None, return it immediately."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_element import SolutionElement
    element = ArchiMateElement.query.get(archimate_element_id)
    if not element:
        return None, (jsonify({"success": False, "error": "ArchiMate element not found"}), 404)
    if element.type != expected_type:
        return None, (jsonify({"success": False, "error": f"Element type '{element.type}' does not match expected type '{expected_type}'"}), 400)
    existing_join = SolutionElement.query.filter_by(
        solution_id=solution_id, archimate_element_id=archimate_element_id
    ).first()
    if not existing_join:
        db.session.add(SolutionElement(
            solution_id=solution_id,
            archimate_element_id=archimate_element_id,
            layer=expected_layer,
        ))
    return element, None


# --- Drivers ---

@solution_design_bp.route("/<int:solution_id>/drivers", methods=["GET"])
@login_required
def get_solution_drivers(solution_id):
    """List all drivers for a solution."""
    solution = Solution.query.get_or_404(solution_id)
    from app.models.solution_architect_models import (
        SolutionAnalysisSession,
        SolutionDriver,
    )

    drivers = []
    if solution.analysis_session_id:
        session_obj = SolutionAnalysisSession.query.get(solution.analysis_session_id)
        if session_obj and session_obj.problem_definition:
            drivers = SolutionDriver.query.filter_by(
                problem_id=session_obj.problem_definition.id
            ).all()
    return jsonify({"success": True, "data": [_driver_to_dict(d) for d in drivers]})


@solution_design_bp.route("/<int:solution_id>/drivers", methods=["POST"])
@login_required
def create_solution_driver(solution_id):
    """Add a driver to a solution."""
    solution = Solution.query.get_or_404(solution_id)
    from app.models.solution_architect_models import SolutionDriver

    pd = _get_or_create_problem_def(solution)
    data = request.get_json() or {}
    err_response, err_status = _validate_entity(data, ["name"])
    if err_response:
        return err_response, err_status

    # Resolve driver_type enum
    from app.models.solution_architect_models import DriverType
    dtype_str = (data.get("driver_type") or "internal").lower()
    try:
        dtype = DriverType(dtype_str)
    except ValueError:
        dtype = DriverType.INTERNAL

    driver = SolutionDriver(
        problem_id=pd.id,
        name=data.get("name", ""),
        description=data.get("description", ""),
        driver_type=dtype,
        impact_level=_coerce_scale(data.get("impact_level")),
        urgency=_coerce_scale(data.get("urgency")),
        source=data.get("source", ""),
    )
    db.session.add(driver)
    db.session.flush()
    if data.get("archimate_element_id"):
        ae, err = _link_existing_archimate_element(solution_id, data["archimate_element_id"], "Driver", "Motivation")
        if err:
            db.session.rollback()
            return err
    else:
        _sync_archimate_element(solution_id, ae_type="Driver", ae_layer="Motivation", name=driver.name, description=driver.description)
    solution.section_scores = None  # invalidate cache so score reflects new entity on reload
    db.session.commit()
    return jsonify({"success": True, "data": _driver_to_dict(driver)}), 201


@solution_design_bp.route("/<int:solution_id>/drivers/<int:driver_id>", methods=["PUT"])
@login_required
def update_solution_driver(solution_id, driver_id):
    """Update a driver."""
    from app.models.solution_architect_models import SolutionDriver
    solution = Solution.query.get_or_404(solution_id)
    pd = _get_or_create_problem_def(solution)
    driver = SolutionDriver.query.filter_by(id=driver_id, problem_id=pd.id).first_or_404()
    data = request.get_json()
    for field in ["name", "description", "impact_level", "urgency", "source"]:
        if field in data:
            value = data[field]
            if field in ("impact_level", "urgency"):
                value = _coerce_scale(value)
            setattr(driver, field, value)
    if "driver_type" in data:
        from app.models.solution_architect_models import DriverType
        try:
            driver.driver_type = DriverType(data["driver_type"])
        except ValueError:
            logger.exception("Failed to compute driver.driver_type")
            pass
    db.session.commit()
    return jsonify({"success": True, "data": _driver_to_dict(driver)})


@solution_design_bp.route("/<int:solution_id>/drivers/<int:driver_id>", methods=["DELETE"])
@login_required
def delete_solution_driver(solution_id, driver_id):
    """Delete a driver."""
    from app.models.solution_architect_models import SolutionDriver
    solution = Solution.query.get_or_404(solution_id)
    pd = _get_or_create_problem_def(solution)
    driver = SolutionDriver.query.filter_by(id=driver_id, problem_id=pd.id).first_or_404()
    db.session.delete(driver)
    db.session.commit()
    return jsonify({"success": True})


# --- Goals ---

@solution_design_bp.route("/<int:solution_id>/goals", methods=["GET"])
@login_required
def get_solution_goals(solution_id):
    """List all goals for a solution."""
    solution = Solution.query.get_or_404(solution_id)
    from app.models.solution_architect_models import (
        SolutionAnalysisSession,
        SolutionGoal,
    )

    goals = []
    if solution.analysis_session_id:
        session_obj = SolutionAnalysisSession.query.get(solution.analysis_session_id)
        if session_obj and session_obj.problem_definition:
            goals = SolutionGoal.query.filter_by(
                problem_id=session_obj.problem_definition.id
            ).all()
    return jsonify({"success": True, "data": [_goal_to_dict(g) for g in goals]})


@solution_design_bp.route("/<int:solution_id>/goals", methods=["POST"])
@login_required
def create_solution_goal(solution_id):
    """Add a goal to a solution."""
    solution = Solution.query.get_or_404(solution_id)
    from app.models.solution_architect_models import SolutionGoal

    pd = _get_or_create_problem_def(solution)
    data = request.get_json() or {}
    err_response, err_status = _validate_entity(data, ["name"])
    if err_response:
        return err_response, err_status
    goal = SolutionGoal(
        problem_id=pd.id,
        name=data.get("name", ""),
        description=data.get("description", ""),
        priority=_coerce_priority(data.get("priority")),
        measurement_criteria=data.get("measurement_criteria", ""),
    )
    db.session.add(goal)
    db.session.flush()
    if data.get("archimate_element_id"):
        ae, err = _link_existing_archimate_element(solution_id, data["archimate_element_id"], "Goal", "Motivation")
        if err:
            db.session.rollback()
            return err
    else:
        _sync_archimate_element(solution_id, ae_type="Goal", ae_layer="Motivation", name=goal.name, description=goal.description)
    solution.section_scores = None  # invalidate cache so score reflects new entity on reload
    db.session.commit()
    return jsonify({"success": True, "data": _goal_to_dict(goal)}), 201


@solution_design_bp.route("/<int:solution_id>/goals/<int:goal_id>", methods=["PUT"])
@login_required
def update_solution_goal(solution_id, goal_id):
    """Update a goal."""
    from app.models.solution_architect_models import SolutionGoal
    solution = Solution.query.get_or_404(solution_id)
    pd = _get_or_create_problem_def(solution)
    goal = SolutionGoal.query.filter_by(id=goal_id, problem_id=pd.id).first_or_404()
    data = request.get_json()
    for field in ["name", "description", "priority", "measurement_criteria"]:
        if field in data:
            setattr(goal, field, data[field])
    db.session.commit()
    return jsonify({"success": True, "data": _goal_to_dict(goal)})


@solution_design_bp.route("/<int:solution_id>/goals/<int:goal_id>", methods=["DELETE"])
@login_required
def delete_solution_goal(solution_id, goal_id):
    """Delete a goal."""
    from app.models.solution_architect_models import SolutionGoal
    solution = Solution.query.get_or_404(solution_id)
    pd = _get_or_create_problem_def(solution)
    goal = SolutionGoal.query.filter_by(id=goal_id, problem_id=pd.id).first_or_404()
    db.session.delete(goal)
    db.session.commit()
    return jsonify({"success": True})


# --- Constraints ---

@solution_design_bp.route("/<int:solution_id>/constraints", methods=["GET"])
@login_required
def get_solution_constraints(solution_id):
    """List all constraints for a solution."""
    solution = Solution.query.get_or_404(solution_id)
    from app.models.solution_architect_models import (
        SolutionAnalysisSession,
        SolutionConstraint,
    )

    constraints = []
    if solution.analysis_session_id:
        session_obj = SolutionAnalysisSession.query.get(solution.analysis_session_id)
        if session_obj and session_obj.problem_definition:
            constraints = SolutionConstraint.query.filter_by(
                problem_id=session_obj.problem_definition.id
            ).all()
    return jsonify({"success": True, "data": [_constraint_to_dict(c) for c in constraints]})


@solution_design_bp.route("/<int:solution_id>/constraints", methods=["POST"])
@login_required
def create_solution_constraint(solution_id):
    """Add a constraint to a solution."""
    solution = Solution.query.get_or_404(solution_id)
    from app.models.solution_architect_models import SolutionConstraint

    pd = _get_or_create_problem_def(solution)
    data = request.get_json() or {}
    err_response, err_status = _validate_entity(data, ["name"])
    if err_response:
        return err_response, err_status

    from app.models.solution_architect_models import ConstraintType
    ctype_str = (data.get("constraint_type") or "technical").lower()
    try:
        ctype = ConstraintType(ctype_str)
    except ValueError:
        ctype = ConstraintType.TECHNICAL

    constraint = SolutionConstraint(
        problem_id=pd.id,
        name=data.get("name", ""),
        description=data.get("description", ""),
        constraint_type=ctype,
        value=data.get("value", ""),
        severity=data.get("severity") or None,
    )
    db.session.add(constraint)
    db.session.flush()
    if data.get("archimate_element_id"):
        ae, err = _link_existing_archimate_element(solution_id, data["archimate_element_id"], "Constraint", "Motivation")
        if err:
            db.session.rollback()
            return err
    else:
        _sync_archimate_element(solution_id, ae_type="Constraint", ae_layer="Motivation", name=constraint.name, description=constraint.description)
    solution.section_scores = None  # invalidate cache so score reflects new entity on reload
    db.session.commit()
    return jsonify({"success": True, "data": _constraint_to_dict(constraint)}), 201


@solution_design_bp.route("/<int:solution_id>/constraints/<int:constraint_id>", methods=["PUT"])
@login_required
def update_solution_constraint(solution_id, constraint_id):
    """Update a constraint."""
    from app.models.solution_architect_models import SolutionConstraint
    solution = Solution.query.get_or_404(solution_id)
    pd = _get_or_create_problem_def(solution)
    constraint = SolutionConstraint.query.filter_by(id=constraint_id, problem_id=pd.id).first_or_404()
    data = request.get_json()
    for field in ["name", "description", "value", "severity"]:
        if field in data:
            setattr(constraint, field, data[field])
    if "constraint_type" in data:
        from app.models.solution_architect_models import ConstraintType
        try:
            constraint.constraint_type = ConstraintType(data["constraint_type"])
        except ValueError:
            logger.exception("Failed to compute constraint.constraint_type")
            pass
    db.session.commit()
    return jsonify({"success": True, "data": _constraint_to_dict(constraint)})


@solution_design_bp.route("/<int:solution_id>/constraints/<int:constraint_id>", methods=["DELETE"])
@login_required
def delete_solution_constraint(solution_id, constraint_id):
    """Delete a constraint."""
    from app.models.solution_architect_models import SolutionConstraint
    solution = Solution.query.get_or_404(solution_id)
    pd = _get_or_create_problem_def(solution)
    constraint = SolutionConstraint.query.filter_by(id=constraint_id, problem_id=pd.id).first_or_404()
    db.session.delete(constraint)
    db.session.commit()
    return jsonify({"success": True})


# --- Requirements ---

@solution_design_bp.route("/<int:solution_id>/requirements", methods=["GET"])
@login_required
def get_solution_requirements(solution_id):
    """List all requirements for a solution."""
    solution = Solution.query.get_or_404(solution_id)
    from app.models.solution_architect_models import (
        SolutionAnalysisSession,
        SolutionRequirement,
    )

    requirements = []
    if solution.analysis_session_id:
        session_obj = SolutionAnalysisSession.query.get(solution.analysis_session_id)
        if session_obj and session_obj.problem_definition:
            requirements = SolutionRequirement.query.filter_by(
                problem_id=session_obj.problem_definition.id
            ).all()
    return jsonify({"success": True, "data": [_requirement_to_dict(r) for r in requirements]})


@solution_design_bp.route("/<int:solution_id>/requirements", methods=["POST"])
@login_required
def create_solution_requirement(solution_id):
    """Add a requirement to a solution."""
    solution = Solution.query.get_or_404(solution_id)
    from app.models.solution_architect_models import SolutionRequirement, RequirementType

    pd = _get_or_create_problem_def(solution)
    data = request.get_json() or {}
    err_response, err_status = _validate_entity(data, ["name", "description"])
    if err_response:
        return err_response, err_status

    rtype_str = (data.get("requirement_type") or "functional").lower()
    try:
        rtype = RequirementType(rtype_str)
    except ValueError:
        rtype = RequirementType.FUNCTIONAL

    requirement = SolutionRequirement(
        problem_id=pd.id,
        name=data.get("name", ""),
        description=data.get("description", ""),
        requirement_type=rtype,
        priority=_coerce_priority(data.get("priority")),
        is_mandatory=data.get("is_mandatory", False),
        source=data.get("source", ""),
        rationale=data.get("rationale", ""),
        acceptance_criteria=data.get("acceptance_criteria", ""),
    )
    db.session.add(requirement)
    db.session.flush()
    if data.get("archimate_element_id"):
        ae, err = _link_existing_archimate_element(solution_id, data["archimate_element_id"], "Requirement", "Motivation")
        if err:
            db.session.rollback()
            return err
    else:
        _sync_archimate_element(solution_id, ae_type="Requirement", ae_layer="Motivation", name=requirement.name, description=requirement.description)
    db.session.commit()
    return jsonify({"success": True, "data": _requirement_to_dict(requirement)}), 201


@solution_design_bp.route("/<int:solution_id>/requirements/<int:requirement_id>", methods=["PUT"])
@login_required
def update_solution_requirement(solution_id, requirement_id):
    """Update a requirement."""
    from app.models.solution_architect_models import SolutionRequirement, RequirementType
    solution = Solution.query.get_or_404(solution_id)
    pd = _get_or_create_problem_def(solution)
    requirement = SolutionRequirement.query.filter_by(id=requirement_id, problem_id=pd.id).first_or_404()
    data = request.get_json()
    # REQ-001: include workflow/triage fields alongside base fields
    for field in [
        "name", "description", "source", "rationale", "acceptance_criteria",
        "priority", "is_mandatory",
        "status", "owner", "assumptions", "dependencies_text",
        "moscow_priority", "togaf_phase",
    ]:
        if field in data:
            setattr(requirement, field, data[field])
    if "requirement_type" in data:
        try:
            requirement.requirement_type = RequirementType(data["requirement_type"])
        except ValueError:
            logger.exception("Failed to compute requirement.requirement_type")
            pass
    db.session.commit()
    return jsonify({"success": True, "data": _requirement_to_dict(requirement)})


@solution_design_bp.route("/<int:solution_id>/requirements/<int:requirement_id>", methods=["DELETE"])
@login_required
def delete_solution_requirement(solution_id, requirement_id):
    """Delete a requirement."""
    from app.models.solution_architect_models import SolutionRequirement
    solution = Solution.query.get_or_404(solution_id)
    pd = _get_or_create_problem_def(solution)
    requirement = SolutionRequirement.query.filter_by(id=requirement_id, problem_id=pd.id).first_or_404()
    db.session.delete(requirement)
    db.session.commit()
    return jsonify({"success": True})


# --- Solution Options (Recommendations) ---

@solution_design_bp.route("/<int:solution_id>/options", methods=["GET"])
@login_required
def get_solution_options(solution_id):
    """List all solution options/recommendations."""
    solution = Solution.query.get_or_404(solution_id)
    from app.models.solution_architect_models import (
        SolutionRecommendation,
    )

    options = []
    if solution.analysis_session_id:
        options = SolutionRecommendation.query.filter_by(
            session_id=solution.analysis_session_id
        ).order_by(SolutionRecommendation.rank).all()
    return jsonify({"success": True, "data": [_recommendation_to_dict(r) for r in options]})


@solution_design_bp.route("/<int:solution_id>/options", methods=["POST"])
@login_required
def create_solution_option(solution_id):
    """Manually add a solution option/recommendation."""
    solution = Solution.query.get_or_404(solution_id)
    from app.models.solution_architect_models import (
        SolutionAnalysisSession,
        SolutionRecommendation,
        RecommendationOptionType,
    )

    # Ensure analysis session exists
    if not solution.analysis_session_id:
        session_obj = SolutionAnalysisSession(
            name=f"Analysis for {solution.name}",
            description=f"Options analysis session for solution: {solution.name}",
            created_by_id=current_user.id,
        )
        db.session.add(session_obj)
        db.session.flush()
        solution.analysis_session_id = session_obj.id
    session_id = solution.analysis_session_id

    data = request.get_json() or {}
    err_response, err_status = _validate_entity(data, ["name", "justification"])
    if err_response:
        return err_response, err_status
    otype_str = (data.get("option_type") or "build").lower()
    try:
        otype = RecommendationOptionType(otype_str)
    except ValueError:
        otype = RecommendationOptionType.BUILD

    # Calculate next rank
    existing_count = SolutionRecommendation.query.filter_by(session_id=session_id).count()

    option = SolutionRecommendation(
        session_id=session_id,
        option_type=otype,
        rank=existing_count + 1,
        name=data.get("name") or None,
        is_recommended=bool(data.get("is_recommended")),
        score=data.get("score"),
        confidence=data.get("confidence"),
        estimated_cost_min=data.get("estimated_cost_min"),
        estimated_cost_max=data.get("estimated_cost_max"),
        cost_currency=data.get("cost_currency", "GBP"),
        timeline_months=data.get("timeline_months"),
        pros=data.get("pros", []),
        cons=data.get("cons", []),
        risks=data.get("risks", []),
        next_steps=data.get("next_steps", []),
        justification=data.get("justification", ""),
    )
    db.session.add(option)
    db.session.commit()
    return jsonify({"success": True, "data": _recommendation_to_dict(option)}), 201


@solution_design_bp.route("/<int:solution_id>/options/<int:option_id>", methods=["PUT"])
@login_required
def update_solution_option(solution_id, option_id):
    """Update a solution option."""
    from app.models.solution_architect_models import SolutionRecommendation, RecommendationOptionType
    solution = Solution.query.get_or_404(solution_id)
    if not solution.analysis_session_id:
        abort(404)
    option = SolutionRecommendation.query.filter_by(
        id=option_id, session_id=solution.analysis_session_id
    ).first_or_404()
    data = request.get_json()
    for field in ["name", "rank", "score", "confidence", "estimated_cost_min", "estimated_cost_max",
                  "cost_currency", "timeline_months", "justification"]:
        if field in data:
            setattr(option, field, data[field])
    if "is_recommended" in data:
        option.is_recommended = bool(data["is_recommended"])
    for json_field in ["pros", "cons", "risks", "next_steps"]:
        if json_field in data:
            setattr(option, json_field, data[json_field])
    if "option_type" in data:
        try:
            option.option_type = RecommendationOptionType(data["option_type"])
        except ValueError:
            logger.exception("Failed to compute option.option_type")
            pass
    db.session.commit()
    return jsonify({"success": True, "data": _recommendation_to_dict(option)})


@solution_design_bp.route("/<int:solution_id>/options/<int:option_id>", methods=["DELETE"])
@login_required
def delete_solution_option(solution_id, option_id):
    """Delete a solution option."""
    from app.models.solution_architect_models import SolutionRecommendation
    solution = Solution.query.get_or_404(solution_id)
    if not solution.analysis_session_id:
        abort(404)
    option = SolutionRecommendation.query.filter_by(
        id=option_id, session_id=solution.analysis_session_id
    ).first_or_404()
    db.session.delete(option)
    db.session.commit()
    return jsonify({"success": True})


@solution_design_bp.route("/<int:solution_id>/options/ai-analyze", methods=["POST"])
@login_required
def run_ai_options_analysis(solution_id):
    """Trigger AI-powered options analysis for this solution.
    Proxies to SolutionArchitectOrchestrator if available, with fallback to template options.
    """
    solution = Solution.query.get_or_404(solution_id)
    from app.models.solution_architect_models import (
        SolutionAnalysisSession,
        SolutionRecommendation,
        RecommendationOptionType,
    )

    # Ensure analysis session exists
    if not solution.analysis_session_id:
        session_obj = SolutionAnalysisSession(
            name=f"Analysis for {solution.name}",
            description=f"AI options analysis for solution: {solution.name}",
            created_by_id=current_user.id,
        )
        db.session.add(session_obj)
        db.session.flush()
        solution.analysis_session_id = session_obj.id
    session_id = solution.analysis_session_id

    # Try calling the existing AI orchestrator
    try:
        from app.modules.solutions_strategic.v2.services.solution_orchestration_service import (
            SolutionOrchestrationService,
        )
        orchestrator = SolutionOrchestrationService()
        result = orchestrator.run_options_analysis(session_id)
        if result and result.get("recommendations"):
            # Reload from DB after orchestrator persists
            options = SolutionRecommendation.query.filter_by(
                session_id=session_id
            ).order_by(SolutionRecommendation.rank).all()
            return jsonify({
                "success": True,
                "source": "ai",
                "data": [_recommendation_to_dict(r) for r in options],
            })
    except Exception as ai_err:
        logger.info(f"AI options analysis not available, using template: {ai_err}")

    # Fallback: generate template options based on solution metadata
    existing = SolutionRecommendation.query.filter_by(session_id=session_id).count()
    if existing > 0:
        # Already have options — return them
        options = SolutionRecommendation.query.filter_by(
            session_id=session_id
        ).order_by(SolutionRecommendation.rank).all()
        return jsonify({
            "success": True,
            "source": "existing",
            "data": [_recommendation_to_dict(r) for r in options],
        })

    # Generate 3 template options: Build, Buy, Reuse
    templates = [
        {
            "option_type": RecommendationOptionType.BUILD,
            "rank": 1,
            "justification": f"Custom-build a solution tailored to {solution.name} requirements.",
            "pros": ["Full control over features", "No vendor lock-in", "Tailored to exact needs"],
            "cons": ["Higher development cost", "Longer time to market", "Ongoing maintenance burden"],
            "risks": [{"description": "Development timeline overrun", "severity": "medium"}],
        },
        {
            "option_type": RecommendationOptionType.BUY,
            "rank": 2,
            "justification": f"Purchase a commercial product for {solution.name}.",
            "pros": ["Faster deployment", "Vendor support included", "Proven solution"],
            "cons": ["Licensing costs", "Limited customization", "Vendor dependency"],
            "risks": [{"description": "Vendor lock-in risk", "severity": "medium"}],
        },
        {
            "option_type": RecommendationOptionType.REUSE,
            "rank": 3,
            "justification": f"Leverage existing platform capabilities for {solution.name}.",
            "pros": ["Lowest cost", "Fastest delivery", "No new technology debt"],
            "cons": ["May not meet all requirements", "Existing system constraints", "Capacity limits"],
            "risks": [{"description": "Capability gap vs requirements", "severity": "low"}],
        },
    ]
    new_options = []
    for t in templates:
        opt = SolutionRecommendation(session_id=session_id, **t)
        db.session.add(opt)
        new_options.append(opt)
    db.session.commit()

    return jsonify({
        "success": True,
        "source": "template",
        "data": [_recommendation_to_dict(r) for r in new_options],
    }), 201


# =============================================================================
# PHASE E — OPTIONS ANALYSIS (TCO aggregation + MCDA scoring)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/options-analysis", methods=["GET"])
@login_required
def get_options_analysis(solution_id):
    """Compute and return aggregated TCO, risk, capability, and MCDA scores for a solution.

    Pulls from:
    - solution_tco_items (TCO by category/option_label)
    - solution_risks (risk severity counts)
    - solution_capability_mappings (coverage scores)
    - solution_recommendations (MCDA option scores)
    Returns a consolidated JSON payload for the Phase E options analysis panel.
    """
    solution = Solution.query.get_or_404(solution_id)
    from app.models.solution_lifecycle_models import SolutionTCOItem, SolutionRisk
    from app.models.solution_models import SolutionCapabilityMapping

    # --- TCO aggregation by category ---
    tco_items = SolutionTCOItem.query.filter_by(solution_id=solution_id).all()
    tco_by_category = {}
    tco_total = 0.0
    for item in tco_items:
        cat = item.cost_category or "Uncategorised"
        amt = float(item.amount or 0)
        if cat not in tco_by_category:
            tco_by_category[cat] = {"category": cat, "total": 0.0, "recurring": 0.0, "one_time": 0.0, "items": 0}
        tco_by_category[cat]["total"] += amt
        tco_by_category[cat]["items"] += 1
        if item.is_recurring:
            tco_by_category[cat]["recurring"] += amt
        else:
            tco_by_category[cat]["one_time"] += amt
        tco_total += amt
    tco_summary = sorted(tco_by_category.values(), key=lambda x: x["total"], reverse=True)

    # --- Risk summary ---
    risks = SolutionRisk.query.filter_by(solution_id=solution_id).all()
    risk_counts = {"high": 0, "medium": 0, "low": 0, "total": len(risks), "open": 0, "mitigated": 0}
    for r in risks:
        impact = (r.impact or "medium").lower()
        if impact in risk_counts:
            risk_counts[impact] += 1
        if (r.status or "open").lower() == "open":
            risk_counts["open"] += 1
        else:
            risk_counts["mitigated"] += 1

    # --- Capability coverage ---
    cap_mappings = SolutionCapabilityMapping.query.filter_by(solution_id=solution_id).all()
    capability_scores = []
    total_coverage = 0.0
    for cm in cap_mappings:
        cov = float(cm.coverage_percentage or 0)
        cap_name = cm.capability.name if cm.capability else f"Capability #{cm.capability_id}"
        capability_scores.append({
            "capability_id": cm.capability_id,
            "name": cap_name,
            "coverage": cov,
            "support_level": cm.support_level,
            "maturity_current": cm.maturity_current,
            "maturity_target": cm.maturity_target,
        })
        total_coverage += cov
    avg_coverage = round(total_coverage / max(1, len(cap_mappings)), 1)

    # --- MCDA scores from existing recommendations ---
    from app.models.solution_architect_models import SolutionRecommendation
    mcda_options = []
    if solution.analysis_session_id:
        recs = SolutionRecommendation.query.filter_by(
            session_id=solution.analysis_session_id
        ).order_by(SolutionRecommendation.rank).all()
        for rec in recs:
            mcda_options.append({
                "id": rec.id,
                "option_type": rec.option_type.value if rec.option_type else "build",
                "rank": rec.rank,
                "score": float(rec.score) if rec.score else None,
                "estimated_cost_min": float(rec.estimated_cost_min) if rec.estimated_cost_min else None,
                "estimated_cost_max": float(rec.estimated_cost_max) if rec.estimated_cost_max else None,
                "timeline_months": rec.timeline_months,
                "justification": rec.justification,
                "pros": rec.pros or [],
                "cons": rec.cons or [],
            })

    # --- MCDA criteria weights (stored in solution.metadata or defaults) ---
    default_criteria = [
        {"name": "Cost", "weight": 0.30, "description": "Total cost of ownership over 5 years"},
        {"name": "Risk", "weight": 0.20, "description": "Implementation and operational risk"},
        {"name": "Capability Fit", "weight": 0.25, "description": "Coverage of required capabilities"},
        {"name": "Time to Value", "weight": 0.15, "description": "Speed to deliver business value"},
        {"name": "Strategic Alignment", "weight": 0.10, "description": "Alignment with enterprise strategy"},
    ]
    stored_criteria = None
    if hasattr(solution, "metadata") and isinstance(solution.metadata, dict):
        stored_criteria = solution.metadata.get("mcda_criteria")
    criteria = stored_criteria if stored_criteria else default_criteria

    return jsonify({
        "success": True,
        "tco": {
            "total": tco_total,
            "by_category": tco_summary,
            "item_count": len(tco_items),
        },
        "risks": risk_counts,
        "capabilities": {
            "scores": capability_scores,
            "average_coverage": avg_coverage,
            "count": len(cap_mappings),
        },
        "mcda": {
            "options": mcda_options,
            "criteria": criteria,
        },
    })


@solution_design_bp.route("/<int:solution_id>/options-analysis/criteria", methods=["POST"])
@login_required
def save_mcda_criteria(solution_id):
    """Save MCDA criteria weights for this solution.

    Expects JSON body: { "criteria": [ { "name": "...", "weight": 0.3, "description": "..." }, ... ] }
    Weights must sum to 1.0 (tolerance 0.01).
    """
    solution = Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    criteria = data.get("criteria", [])

    if not criteria or not isinstance(criteria, list):
        return jsonify({"success": False, "error": "criteria must be a non-empty list"}), 400

    # Validate weights sum to ~1.0
    total_weight = sum(float(c.get("weight", 0)) for c in criteria)
    if abs(total_weight - 1.0) > 0.01:
        return jsonify({
            "success": False,
            "error": f"Criteria weights must sum to 1.0 (got {round(total_weight, 3)})"
        }), 400

    # Validate each criterion has name and weight
    cleaned = []
    for c in criteria:
        name = (c.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "Each criterion must have a name"}), 400
        cleaned.append({
            "name": name,
            "weight": round(float(c.get("weight", 0)), 3),
            "description": (c.get("description") or "").strip(),
        })

    # Store in solution metadata
    if not hasattr(solution, "metadata") or not isinstance(solution.metadata, dict):
        solution.metadata = {}
    solution.metadata = {**solution.metadata, "mcda_criteria": cleaned}
    db.session.commit()

    return jsonify({"success": True, "criteria": cleaned})


# =============================================================================
# PHASE H — VALUE REALIZATION & FEEDBACK LOOP
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/phase-h/review", methods=["GET"])
@login_required
def phase_h_review(solution_id: int):
    """
    Phase H review: compare metric actuals vs targets, cost variance, risk outcomes.
    Returns JSON data for the value realization dashboard.
    """
    solution = Solution.query.get_or_404(solution_id)
    from app.models.solution_lifecycle_models import (
        SolutionMetric, SolutionRisk, SolutionTCOItem,
    )

    # Metrics: baseline vs target vs actual with achievement %
    metrics = SolutionMetric.query.filter_by(solution_id=solution_id).all()
    metric_results = []
    for m in metrics:
        achievement = None
        if m.baseline_value is not None and m.target_value is not None and m.actual_value is not None:
            try:
                baseline = float(m.baseline_value)
                target = float(m.target_value)
                actual = float(m.actual_value)
                if target != baseline:
                    achievement = round(((actual - baseline) / (target - baseline)) * 100, 1)
            except (ValueError, TypeError, ZeroDivisionError):
                logger.exception("Failed to compute baseline")
                pass
        metric_results.append({
            "id": m.id,
            "name": m.name,
            "unit": m.unit,
            "baseline": m.baseline_value,
            "target": m.target_value,
            "actual": m.actual_value,
            "achievement_pct": achievement,
            "status": m.status,
            "measurement_date": m.measurement_date.isoformat() if m.measurement_date else None,
        })

    # Cost variance: estimated vs actual from TCO items
    tco_items = SolutionTCOItem.query.filter_by(solution_id=solution_id).all()
    estimated_total = sum(float(t.amount or 0) for t in tco_items if t.option_label == "estimated")
    actual_total = sum(float(t.amount or 0) for t in tco_items if t.option_label == "actual")
    cost_variance = {
        "estimated": estimated_total,
        "actual": actual_total,
        "variance": actual_total - estimated_total if estimated_total else None,
        "variance_pct": round(((actual_total - estimated_total) / estimated_total) * 100, 1) if estimated_total else None,
    }
    # Also use solution-level cost fields if available
    if solution.estimated_cost and solution.actual_cost:
        cost_variance["estimated"] = float(solution.estimated_cost)
        cost_variance["actual"] = float(solution.actual_cost)
        cost_variance["variance"] = float(solution.actual_cost) - float(solution.estimated_cost)
        if float(solution.estimated_cost) > 0:
            cost_variance["variance_pct"] = round(
                ((float(solution.actual_cost) - float(solution.estimated_cost))
                 / float(solution.estimated_cost)) * 100, 1
            )

    # Risk outcomes
    risks = SolutionRisk.query.filter_by(solution_id=solution_id).all()
    risk_outcomes = []
    for r in risks:
        risk_outcomes.append({
            "id": r.id,
            "description": r.risk_description,
            "impact": r.impact,
            "probability": r.probability,
            "status": r.status,
            "mitigation": r.mitigation,
            "owner": r.owner,
        })

    # Sync outcomes to ArchiMate
    archimate_outcomes = []
    try:
        from app.services.solution_archimate_sync_service import sync_phase_h_outcomes
        archimate_outcomes = sync_phase_h_outcomes(solution_id)
        db.session.commit()
    except Exception as e:
        logger.warning(f"Phase H outcome sync failed: {e}")

    return jsonify({
        "success": True,
        "metrics": metric_results,
        "cost_variance": cost_variance,
        "risk_outcomes": risk_outcomes,
        "archimate_outcomes": archimate_outcomes,
        "overall_achievement": (
            round(sum(m["achievement_pct"] for m in metric_results if m["achievement_pct"] is not None)
                  / max(1, len([m for m in metric_results if m["achievement_pct"] is not None])), 1)
            if any(m["achievement_pct"] is not None for m in metric_results) else None
        ),
    })


@solution_design_bp.route("/<int:solution_id>/phase-h/new-cycle", methods=["POST"])
@login_required
@audit_log("create_new_adm_cycle")
def phase_h_new_cycle(solution_id: int):
    """
    Create a new ADM cycle from Phase H findings.

    Creates a new Solution with adm_phase='A', links drivers from findings,
    sets parent_solution_id for traceability.
    """
    parent = Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}

    try:
        new_solution = Solution(
            name=data.get("name") or f"{parent.name} — Cycle 2",
            description=data.get("description") or f"New ADM cycle derived from Phase H review of '{parent.name}'",
            solution_type=parent.solution_type,
            business_domain=parent.business_domain,
            status="planned",
            governance_status="draft",
            adm_phase="A",
            parent_solution_id=parent.id,
            solution_owner=parent.solution_owner,
            business_sponsor=parent.business_sponsor,
            technical_lead=parent.technical_lead,
            created_by_id=current_user.id,
        )
        db.session.add(new_solution)
        db.session.flush()

        # Carry forward unresolved drivers as new SolutionDrivers
        driver_descriptions = data.get("new_drivers", [])
        if driver_descriptions:
            from app.models.solution_architect_models import (
                SolutionAnalysisSession, SolutionDriver, SolutionProblemDefinition,
                SolutionSessionStatus, DriverType,
            )

            session_record = SolutionAnalysisSession(
                name=f"Phase H Findings — {parent.name}",
                status=SolutionSessionStatus.DRAFT,
                created_by_id=current_user.id,
            )
            db.session.add(session_record)
            db.session.flush()

            problem_def = SolutionProblemDefinition(
                session_id=session_record.id,
                problem_description=f"Findings from Phase H review of '{parent.name}'",
            )
            db.session.add(problem_def)
            db.session.flush()

            new_solution.analysis_session_id = session_record.id

            for desc in driver_descriptions:
                driver = SolutionDriver(
                    problem_id=problem_def.id,
                    name=desc[:200] if isinstance(desc, str) else str(desc)[:200],
                    description=desc if isinstance(desc, str) else str(desc),
                    driver_type=DriverType.INTERNAL,
                    source="phase_h_review",
                )
                db.session.add(driver)
                sync_archimate_element(driver)

        db.session.commit()

        # Sync the new solution's elements to ArchiMate
        try:
            from app.services.solution_archimate_sync_service import sync_all_for_solution
            sync_all_for_solution(new_solution.id)
            db.session.commit()
        except Exception as sync_err:
            logger.warning(f"Sync failed for new cycle solution: {sync_err}")

        return jsonify({
            "success": True,
            "solution_id": new_solution.id,
            "redirect_url": url_for("solution_design.view_solution", solution_id=new_solution.id),
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating new ADM cycle: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to create new cycle"}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# ArchiMate Driver / Goal Linking — Phase A Vision Wiring (ENT-059)
# ═══════════════════════════════════════════════════════════════════════════════


@solution_design_bp.route("/<int:solution_id>/archimate-drivers", methods=["GET"])
@login_required
def get_archimate_drivers(solution_id):
    """Fetch ArchiMate Driver elements linked to this solution via SolutionArchiMateElement."""
    Solution.query.get_or_404(solution_id)
    try:
        from app.models.solution_models import SolutionArchiMateElement

        elements = SolutionArchiMateElement.query.filter_by(
            solution_id=solution_id
        ).all()
        # Filter to Driver-type elements (element_name may cache type info, but we
        # also query the archimate_elements table to confirm element_type)
        driver_elements = []
        for elem in elements:
            if elem.element_table == "archimate_elements" and elem.layer_type == "motivation":
                # Check actual element type from the repository
                try:
                    # tenancy-ok: elem comes from SolutionArchiMateElement rows
                    # for solution_id, and Solution.query.get_or_404(solution_id)
                    # above is a TenantMixin query, so a cross-org solution 404s
                    # before any element id is reachable here.
                    row = db.session.execute(
                        db.text("SELECT id, name, type, description FROM archimate_elements WHERE id = :eid"),
                        {"eid": elem.element_id},
                    ).fetchone()
                    if row and row[2] == "Driver":
                        driver_elements.append({
                            "mapping_id": elem.id,
                            "element_id": elem.element_id,
                            "name": row[1],
                            "element_type": row[2],
                            "description": row[3] or "",
                            "relationship_type": elem.relationship_type,
                            "notes": elem.notes,
                        })
                except Exception as e:
                    logger.warning(f"Skipping driver element {elem.id}: {e}")

        return jsonify({"success": True, "data": driver_elements})
    except Exception as e:
        logger.error(f"Error fetching archimate drivers for solution {solution_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_design_bp.route("/<int:solution_id>/link-archimate-driver", methods=["POST"])
@login_required
def link_archimate_driver(solution_id):
    """Link an existing ArchiMate Driver element to a solution.

    Also optionally creates a SolutionDriver record so the driver appears
    in the inline CRUD list alongside manually-created drivers.
    """
    solution = Solution.query.get_or_404(solution_id)
    data = request.get_json()
    element_id = data.get("element_id")
    if not element_id:
        return jsonify({"success": False, "error": "element_id is required"}), 400

    try:
        # Verify the element exists and is a Driver.
        # element_id arrives in the request body and nothing upstream constrains
        # it, so without the org predicate this both discloses another tenant's
        # element and links it into this tenant's solution.
        from flask import g as _g
        _org = getattr(_g, "current_org_id", None)
        _org_and = " AND organization_id = :org" if _org is not None else ""
        row = db.session.execute(
            db.text(
                "SELECT id, name, type, description FROM archimate_elements "
                f"WHERE id = :eid{_org_and}"
            ),
            {"eid": element_id, **({"org": _org} if _org is not None else {})},
        ).fetchone()
        if not row:
            return jsonify({"success": False, "error": "ArchiMate element not found"}), 404
        if row[2] != "Driver":
            return jsonify({"success": False, "error": f"Element is type '{row[2]}', expected 'Driver'"}), 400

        from app.models.solution_models import SolutionArchiMateElement

        # Check for existing mapping
        existing = SolutionArchiMateElement.query.filter_by(
            solution_id=solution_id,
            element_id=element_id,
            element_table="archimate_elements",
            layer_type="motivation",
        ).first()
        if existing:
            return jsonify({"success": False, "error": "Driver element already linked"}), 409

        mapping = SolutionArchiMateElement(
            solution_id=solution_id,
            element_id=element_id,
            element_table="archimate_elements",
            element_name=row[1],
            layer_type="motivation",
            relationship_type=data.get("relationship_type", "influences"),
            notes=data.get("notes", ""),
            is_new_element=False,
            created_by_id=current_user.id,
        )
        db.session.add(mapping)

        # Optionally create a SolutionDriver so it appears in the Phase A inline list
        if data.get("create_driver_record", True):
            from app.models.solution_architect_models import SolutionDriver, DriverType

            pd = _get_or_create_problem_def(solution)
            driver = SolutionDriver(
                problem_id=pd.id,
                name=row[1],
                description=row[3] or "",
                driver_type=DriverType.EXTERNAL,
                source=f"archimate:{element_id}",
                ai_generated=False,
            )
            db.session.add(driver)
            sync_archimate_element(driver)

        db.session.commit()
        return jsonify({
            "success": True,
            "mapping_id": mapping.id,
            "element_name": row[1],
        }), 201
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error linking archimate driver to solution {solution_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_design_bp.route("/<int:solution_id>/archimate-goals", methods=["GET"])
@login_required
def get_archimate_goals(solution_id):
    """Fetch ArchiMate Goal elements linked to this solution via SolutionArchiMateElement."""
    Solution.query.get_or_404(solution_id)
    try:
        from app.models.solution_models import SolutionArchiMateElement

        elements = SolutionArchiMateElement.query.filter_by(
            solution_id=solution_id
        ).all()
        goal_elements = []
        for elem in elements:
            if elem.element_table == "archimate_elements" and elem.layer_type == "motivation":
                try:
                    # tenancy-ok: elem comes from SolutionArchiMateElement rows
                    # for solution_id, and Solution.query.get_or_404(solution_id)
                    # above is a TenantMixin query, so a cross-org solution 404s
                    # before any element id is reachable here.
                    row = db.session.execute(
                        db.text("SELECT id, name, type, description FROM archimate_elements WHERE id = :eid"),
                        {"eid": elem.element_id},
                    ).fetchone()
                    if row and row[2] == "Goal":
                        goal_elements.append({
                            "mapping_id": elem.id,
                            "element_id": elem.element_id,
                            "name": row[1],
                            "element_type": row[2],
                            "description": row[3] or "",
                            "relationship_type": elem.relationship_type,
                            "notes": elem.notes,
                        })
                except Exception as e:
                    logger.warning(f"Skipping goal element {elem.id}: {e}")

        return jsonify({"success": True, "data": goal_elements})
    except Exception as e:
        logger.error(f"Error fetching archimate goals for solution {solution_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_design_bp.route("/<int:solution_id>/link-archimate-goal", methods=["POST"])
@login_required
def link_archimate_goal(solution_id):
    """Link an existing ArchiMate Goal element to a solution.

    Also optionally creates a SolutionGoal record so the goal appears
    in the inline CRUD list alongside manually-created goals.
    """
    solution = Solution.query.get_or_404(solution_id)
    data = request.get_json()
    element_id = data.get("element_id")
    if not element_id:
        return jsonify({"success": False, "error": "element_id is required"}), 400

    try:
        # Verify the element exists and is a Goal.
        # element_id arrives in the request body and nothing upstream constrains
        # it, so without the org predicate this both discloses another tenant's
        # element and links it into this tenant's solution.
        from flask import g as _g
        _org = getattr(_g, "current_org_id", None)
        _org_and = " AND organization_id = :org" if _org is not None else ""
        row = db.session.execute(
            db.text(
                "SELECT id, name, type, description FROM archimate_elements "
                f"WHERE id = :eid{_org_and}"
            ),
            {"eid": element_id, **({"org": _org} if _org is not None else {})},
        ).fetchone()
        if not row:
            return jsonify({"success": False, "error": "ArchiMate element not found"}), 404
        if row[2] != "Goal":
            return jsonify({"success": False, "error": f"Element is type '{row[2]}', expected 'Goal'"}), 400

        from app.models.solution_models import SolutionArchiMateElement

        # Check for existing mapping
        existing = SolutionArchiMateElement.query.filter_by(
            solution_id=solution_id,
            element_id=element_id,
            element_table="archimate_elements",
            layer_type="motivation",
        ).first()
        if existing:
            return jsonify({"success": False, "error": "Goal element already linked"}), 409

        mapping = SolutionArchiMateElement(
            solution_id=solution_id,
            element_id=element_id,
            element_table="archimate_elements",
            element_name=row[1],
            layer_type="motivation",
            relationship_type=data.get("relationship_type", "realizes"),
            notes=data.get("notes", ""),
            is_new_element=False,
            created_by_id=current_user.id,
        )
        db.session.add(mapping)

        # Optionally create a SolutionGoal so it appears in the Phase A inline list
        if data.get("create_goal_record", True):
            from app.models.solution_architect_models import SolutionGoal

            pd = _get_or_create_problem_def(solution)
            goal = SolutionGoal(
                problem_id=pd.id,
                name=row[1],
                description=row[3] or "",
                priority="medium",
                ai_generated=False,
            )
            db.session.add(goal)
            sync_archimate_element(goal)

        db.session.commit()
        return jsonify({
            "success": True,
            "mapping_id": mapping.id,
            "element_name": row[1],
        }), 201
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error linking archimate goal to solution {solution_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_design_bp.route("/<int:solution_id>/stakeholder-concerns", methods=["GET"])
@login_required
def get_solution_stakeholder_concerns(solution_id):
    """Fetch stakeholders and their concerns for a solution."""
    Solution.query.get_or_404(solution_id)
    try:
        from app.models.solution_stakeholder import (
            SolutionStakeholderMapping,
        )

        mappings = SolutionStakeholderMapping.query.filter_by(
            solution_id=solution_id
        ).all()

        result = []
        for m in mappings:
            sh = m.stakeholder
            if not sh:
                continue
            entry = sh.to_dict(include_details=True)
            entry["role"] = m.role.value if m.role else None
            entry["engagement_level"] = m.engagement_level.value if m.engagement_level else None
            # Include detailed concerns
            entry["detailed_concerns"] = [c.to_dict() for c in sh.detailed_concerns]
            result.append(entry)

        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error fetching stakeholder concerns for solution {solution_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# Phase B — Business Capability Mapping (ENT-060)
# ═══════════════════════════════════════════════════════════════════════════════


def _capability_mapping_to_dict(mapping):
    """Serialize a SolutionCapabilityMapping with its related BusinessCapability."""
    cap = mapping.capability
    return {
        "id": mapping.id,
        "solution_id": mapping.solution_id,
        "capability_id": mapping.capability_id,
        "capability_name": cap.name if cap else None,
        "capability_code": cap.code if cap else None,
        "capability_level": cap.level if cap else None,
        "capability_category": cap.category if cap else None,
        "support_level": mapping.support_level,
        "priority": mapping.priority,
        "coverage_percentage": mapping.coverage_percentage,
        "maturity_current": mapping.maturity_current,
        "maturity_target": mapping.maturity_target,
        "notes": mapping.notes,
        "rationale": mapping.rationale,
        "created_at": mapping.created_at.isoformat() if mapping.created_at else None,
    }


@solution_design_bp.route("/<int:solution_id>/capabilities/link", methods=["POST"])
@login_required
def create_solution_capability(solution_id):
    """Link a single business capability to a solution."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_models import SolutionCapabilityMapping
    from app.models.business_capabilities import BusinessCapability

    data = request.get_json()
    capability_id = data.get("capability_id")
    if not capability_id:
        return jsonify({"success": False, "error": "capability_id is required"}), 400

    # Verify capability exists
    cap = BusinessCapability.query.get(capability_id)
    if not cap:
        return jsonify({"success": False, "error": "Business capability not found"}), 404

    # Check for duplicate
    existing = SolutionCapabilityMapping.query.filter_by(
        solution_id=solution_id, capability_id=capability_id
    ).first()
    if existing:
        return jsonify({"success": False, "error": "Capability already linked to this solution"}), 409

    mapping = SolutionCapabilityMapping(
        solution_id=solution_id,
        capability_id=capability_id,
        support_level=data.get("support_level", "partial"),
        priority=data.get("priority", 0),
        coverage_percentage=data.get("coverage_percentage"),
        maturity_current=data.get("maturity_current"),
        maturity_target=data.get("maturity_target"),
        notes=data.get("notes", ""),
        rationale=data.get("rationale", ""),
        created_by_id=current_user.id,
    )
    db.session.add(mapping)
    db.session.commit()
    return jsonify({"success": True, "data": _capability_mapping_to_dict(mapping)}), 201


@solution_design_bp.route("/<int:solution_id>/capabilities/<int:mapping_id>", methods=["PUT"])
@login_required
def update_solution_capability(solution_id, mapping_id):
    """Update a capability mapping."""
    # The child row carries no organisation of its own; the solution does. Load it
    # first so another organisation's solution id is a 404 before its rows are reached.
    Solution.query.get_or_404(solution_id)
    from app.models.solution_models import SolutionCapabilityMapping

    mapping = SolutionCapabilityMapping.query.filter_by(
        id=mapping_id, solution_id=solution_id
    ).first_or_404()
    data = request.get_json()
    for field in ["support_level", "priority", "coverage_percentage",
                  "maturity_current", "maturity_target", "notes", "rationale"]:
        if field in data:
            setattr(mapping, field, data[field])
    db.session.commit()
    return jsonify({"success": True, "data": _capability_mapping_to_dict(mapping)})


# NOTE: delete_solution_capability lives in solution_design_routes.py — not duplicated here.


# ═══════════════════════════════════════════════════════════════════════════════
# Phase D — Technology Architecture (ArchiMate Technology Layer Elements)
# ═══════════════════════════════════════════════════════════════════════════════


@solution_design_bp.route("/<int:solution_id>/technology", methods=["GET"])
@login_required
def get_solution_technology_elements(solution_id):
    """List technology-layer ArchiMate elements linked to a solution."""
    Solution.query.get_or_404(solution_id)
    from app.modules.solutions_strategic.v2.services.solution_technology_service import (
        SolutionTechnologyService,
    )

    svc = SolutionTechnologyService()
    elements = svc.get_technology_elements(solution_id)
    summary = svc.get_technology_summary(solution_id)
    return jsonify({"success": True, "data": elements, "summary": summary})


@solution_design_bp.route(
    "/<int:solution_id>/technology/elements", methods=["POST"]
)
@login_required
def link_solution_technology_element(solution_id):
    """Link a technology ArchiMate element to a solution.

    Body: ``{"element_id": <int>, "element_role": "primary"}``
    """
    Solution.query.get_or_404(solution_id)
    from app.modules.solutions_strategic.v2.services.solution_technology_service import (
        SolutionTechnologyService,
    )

    data = request.get_json(silent=True) or {}
    element_id = data.get("element_id")
    if not element_id:
        return jsonify({"success": False, "error": "element_id is required"}), 400

    element_role = data.get("element_role", "primary")
    svc = SolutionTechnologyService()
    try:
        result = svc.link_technology_element(solution_id, element_id, element_role)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    status_code = 200 if result.get("already_linked") else 201
    return jsonify({"success": True, "data": result}), status_code


@solution_design_bp.route(
    "/<int:solution_id>/technology/elements/<int:element_id>",
    methods=["DELETE"],
)
@login_required
def unlink_solution_technology_element(solution_id, element_id):
    """Unlink a technology element from a solution."""
    Solution.query.get_or_404(solution_id)
    from app.modules.solutions_strategic.v2.services.solution_technology_service import (
        SolutionTechnologyService,
    )

    svc = SolutionTechnologyService()
    removed = svc.unlink_technology_element(solution_id, element_id)
    if not removed:
        return jsonify({"success": False, "error": "Element link not found"}), 404
    return jsonify({"success": True}), 200


# ═══════════════════════════════════════════════════════════════════════════════
# Phase Gate Validation (ENT-064)
# ═══════════════════════════════════════════════════════════════════════════════


@solution_design_bp.route("/<int:solution_id>/phase-gate", methods=["GET"])
@login_required
def get_phase_gate_checklist(solution_id):
    """Return phase gate checklist for the solution's current ADM phase.

    Query params:
        phase (optional): Override which phase to check (A-H).
                          Defaults to solution's current adm_phase.
    """
    from app.modules.solutions_strategic.v2.services.solution_phase_gate_service import (
        SolutionPhaseGateService,
    )

    phase_override = request.args.get("phase")
    svc = SolutionPhaseGateService()

    if phase_override:
        result = svc.check_gate(solution_id, phase_override)
    else:
        result = svc.get_gate_checklist(solution_id)

    return jsonify({"success": True, **result})


@solution_design_bp.route("/<int:solution_id>/phase-gate/all", methods=["GET"])
@login_required
def get_all_phase_gates(solution_id):
    """Return gate status for ALL phases (overview display)."""
    from app.modules.solutions_strategic.v2.services.solution_phase_gate_service import (
        SolutionPhaseGateService,
    )

    svc = SolutionPhaseGateService()
    phases = svc.get_all_phases_status(solution_id)
    return jsonify({"success": True, "phases": phases})


# ═══════════════════════════════════════════════════════════════════════════════
# Jira Bidirectional Sync — ENT-073
# ═══════════════════════════════════════════════════════════════════════════════


@solution_design_bp.route("/<int:solution_id>/jira/pull", methods=["POST"])
@login_required
def jira_pull_issues(solution_id):
    """Pull Jira issues into local SolutionRequirement records.

    Expects JSON body: {"jira_project_key": "EA"}
    """
    from app.services.jira_integration_service import pull_issues

    Solution.query.get_or_404(solution_id)
    data = request.get_json(silent=True) or {}
    project_key = data.get("jira_project_key")
    if not project_key:
        return jsonify({"success": False, "error": "jira_project_key is required"}), 400

    result = pull_issues(solution_id, project_key)
    return jsonify({"success": True, "data": result.as_dict()})


@solution_design_bp.route("/<int:solution_id>/jira/sync-status", methods=["GET"])
@login_required
def jira_sync_status(solution_id):
    """Return Jira sync summary for this solution's requirements."""
    from app.services.jira_integration_service import get_sync_status

    Solution.query.get_or_404(solution_id)
    status = get_sync_status(solution_id)
    return jsonify({"success": True, "data": status})


@solution_design_bp.route("/<int:solution_id>/jira/drift", methods=["GET"])
@login_required
def jira_drift_report(solution_id):
    """Compare local requirements with Jira state and return drift items."""
    from app.services.jira_integration_service import detect_drift

    Solution.query.get_or_404(solution_id)
    drift_items = detect_drift(solution_id)
    return jsonify({
        "success": True,
        "data": [item.as_dict() for item in drift_items],
        "total_drift": len(drift_items),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Traceability Matrix — end-to-end from drivers to technology
# ═══════════════════════════════════════════════════════════════════════════════


@solution_design_bp.route("/<int:solution_id>/traceability", methods=["GET"])
@login_required
def solution_traceability_matrix(solution_id):
    """Render the traceability matrix page or return JSON data."""
    from app.models.solution_models import (
        Solution,
        SolutionArchiMateElement,
        SolutionCapabilityMapping,
    )
    from app.models.solution_architect_models import SolutionRequirement
    from app.modules.solutions_strategic.v2.services.traceability_matrix_service import (
        TraceabilityMatrixService,
    )

    solution = Solution.query.get_or_404(solution_id)
    svc = TraceabilityMatrixService()
    matrix = svc.get_matrix(solution_id)

    # Build capability coverage rows for the capability traceability section
    cap_mappings = SolutionCapabilityMapping.query.filter_by(
        solution_id=solution_id
    ).all()

    has_requirements = SolutionRequirement.query.filter_by(
        solution_id=solution_id
    ).first() is not None
    from sqlalchemy import text as sa_text
    has_applications = (
        db.session.execute(  # tenant-filtered: scoped via parent FK (solution_id)
            sa_text("SELECT 1 FROM solution_applications WHERE solution_id = :sid LIMIT 1"),
            {"sid": solution_id},
        ).first() is not None
    )
    has_archimate = SolutionArchiMateElement.query.filter_by(
        solution_id=solution_id
    ).first() is not None

    capability_rows = []
    for mapping in cap_mappings:
        cap = mapping.capability
        if cap is None:
            continue
        capability_rows.append({
            "capability_name": cap.name,
            "capability_level": cap.level if cap.level is not None else 0,
            "has_requirements": has_requirements,
            "has_applications": has_applications,
            "has_archimate": has_archimate,
        })

    if request.accept_mimetypes.best == "application/json" or request.args.get("format") == "json":
        rows = matrix.get("rows") or []

        def _present(value):
            return value is not None and value != ""

        uncovered_requirements = sum(
            1
            for row in rows
            if _present(row.get("requirement"))
            and not any(_present(row.get(key)) for key in ("application", "vendor_product", "technology"))
        )
        uncovered_capabilities = sum(
            1
            for row in rows
            if _present(row.get("capability"))
            and not any(_present(row.get(key)) for key in ("application", "vendor_product", "technology"))
        )

        summary = dict(matrix.get("summary") or {})
        summary.update(
            {
                "coverage_pct": (matrix.get("coverage") or {}).get("overall", 0),
                "row_count": len(rows),
                "uncovered_requirements": uncovered_requirements,
                "uncovered_capabilities": uncovered_capabilities,
            }
        )
        matrix["summary"] = summary

        return jsonify({"success": True, "data": matrix, "capability_rows": capability_rows})

    from flask import render_template

    return render_template(
        "solutions/traceability_matrix.html",
        solution=solution,
        matrix=matrix,
    )


@solution_design_bp.route("/<int:solution_id>/traceability/export", methods=["GET"])
@login_required
def solution_traceability_export(solution_id):
    """Export the traceability matrix as CSV."""
    import csv
    import io

    from flask import Response

    from app.models.solution_models import Solution
    from app.modules.solutions_strategic.v2.services.traceability_matrix_service import (
        TraceabilityMatrixService,
    )

    solution = Solution.query.get_or_404(solution_id)
    svc = TraceabilityMatrixService()
    matrix = svc.get_matrix(solution_id)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(matrix["columns"])

    for row in matrix["rows"]:
        writer.writerow([
            row["driver"]["name"] if row["driver"] else "",
            row["goal"]["name"] if row["goal"] else "",
            row["capability"]["name"] if row["capability"] else "",
            row["requirement"]["name"] if row["requirement"] else "",
            row["application"]["name"] if row["application"] else "",
            row["vendor_product"]["name"] if row["vendor_product"] else "",
            row["technology"]["name"] if row["technology"] else "",
        ])

    safe_name = solution.name.replace(" ", "_")[:30]
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=traceability_{safe_name}.csv"},
    )


# =============================================================================
# ENH-013: Technology Roadmap Initiatives API
# =============================================================================


@solution_design_bp.route("/api/roadmap/initiatives", methods=["GET"])
@login_required
def list_roadmap_initiatives():
    """ENH-013: List technology roadmap initiatives, optionally filtered by year."""
    from flask import g

    from app.models.archimate_core import ArchitectureModel
    from app.models.implementation_migration import TechnologyRoadmapInitiative
    from app.models.solution_models import Solution

    # Ensure table exists (no migrations)
    try:
        db.session.execute(db.text(  # tenant-exempt: system table (DDL)
            """
            CREATE TABLE IF NOT EXISTS technology_roadmap_initiatives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                fiscal_year_start INTEGER NOT NULL,
                fiscal_year_end INTEGER NOT NULL,
                investment_budget NUMERIC(14,2),
                status VARCHAR(30) NOT NULL DEFAULT 'planned',
                category VARCHAR(100),
                owner VARCHAR(200),
                architecture_id INTEGER REFERENCES architecture_models(id) ON DELETE SET NULL,
                solution_id INTEGER REFERENCES solutions(id) ON DELETE SET NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        db.session.commit()
    except Exception:
        db.session.rollback()

    org_id = getattr(g, "current_org_id", None)
    if org_id is None:
        return jsonify({"success": True, "data": [], "count": 0})
    year = request.args.get("year", type=int)
    query = TechnologyRoadmapInitiative.query
    if org_id is not None:
        arch_sub = (
            db.session.query(ArchitectureModel.id)
            .filter(ArchitectureModel.organization_id == org_id)
            .subquery()
        )
        sol_sub = (
            db.session.query(Solution.id)
            .filter(Solution.organization_id == org_id)
            .subquery()
        )
        from sqlalchemy import or_
        query = query.filter(
            or_(
                TechnologyRoadmapInitiative.architecture_id.in_(
                    db.session.query(arch_sub.c.id)
                ),
                TechnologyRoadmapInitiative.solution_id.in_(
                    db.session.query(sol_sub.c.id)
                ),
            )
        )
    if year:
        query = query.filter(
            TechnologyRoadmapInitiative.fiscal_year_start <= year,
            TechnologyRoadmapInitiative.fiscal_year_end >= year,
        )
    initiatives = query.order_by(
        TechnologyRoadmapInitiative.fiscal_year_start,
        TechnologyRoadmapInitiative.name,
    ).all()
    return jsonify({
        "success": True,
        "data": [i.to_dict() for i in initiatives],
        "count": len(initiatives),
    })


# --- Principles ---

@solution_design_bp.route("/<int:solution_id>/principles", methods=["GET"])
@login_required
def get_solution_principles(solution_id):
    """List all architecture principles for a solution."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionPrincipleSAD
    principles = SolutionPrincipleSAD.query.filter_by(solution_id=solution_id).all()
    return jsonify({"success": True, "data": [p.to_dict() for p in principles]})


@solution_design_bp.route("/<int:solution_id>/principles", methods=["POST"])
@login_required
def create_solution_principle(solution_id):
    """Add an architecture principle to a solution."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionPrincipleSAD
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "name is required"}), 400
    principle = SolutionPrincipleSAD(
        solution_id=solution_id,
        name=name,
        statement=(data.get("statement") or "").strip(),
        rationale=(data.get("rationale") or "").strip(),
        implications=(data.get("implications") or "").strip(),
    )
    db.session.add(principle)
    db.session.commit()
    return jsonify({"success": True, "data": principle.to_dict()}), 201


# --- Assessments ---

@solution_design_bp.route("/<int:solution_id>/assessments", methods=["GET"])
@login_required
def get_solution_assessments(solution_id):
    """List all architecture assessments for a solution."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionAssessmentSAD
    assessments = SolutionAssessmentSAD.query.filter_by(solution_id=solution_id).all()
    return jsonify({"success": True, "data": [a.to_dict() for a in assessments]})


@solution_design_bp.route("/<int:solution_id>/assessments", methods=["POST"])
@login_required
def create_solution_assessment(solution_id):
    """Add an architecture assessment to a solution."""
    Solution.query.get_or_404(solution_id)
    from app.models.solution_sad_models import SolutionAssessmentSAD
    data = request.get_json(silent=True) or {}
    finding = (data.get("finding") or "").strip()
    if not finding:
        return jsonify({"success": False, "error": "finding is required"}), 400
    assessment = SolutionAssessmentSAD(
        solution_id=solution_id,
        assessment_type=(data.get("assessment_type") or "gap").strip(),
        finding=finding,
        severity=(data.get("severity") or "medium").strip(),
        recommendation=(data.get("recommendation") or "").strip(),
    )
    db.session.add(assessment)
    db.session.commit()
    return jsonify({"success": True, "data": assessment.to_dict()}), 201


@solution_design_bp.route("/api/roadmap/initiatives", methods=["POST"])
@login_required
def create_roadmap_initiative():
    """ENH-013: Create a new technology roadmap initiative."""
    from app.models.implementation_migration import TechnologyRoadmapInitiative

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    fy_start = data.get("fiscal_year_start")
    fy_end = data.get("fiscal_year_end")

    if not name or not fy_start or not fy_end:
        return jsonify({"success": False, "error": "name, fiscal_year_start, fiscal_year_end required"}), 400

    try:
        fy_start = int(fy_start)
        fy_end = int(fy_end)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "fiscal_year_start and fiscal_year_end must be integers"}), 400

    if fy_end < fy_start:
        return jsonify({"success": False, "error": "fiscal_year_end must be >= fiscal_year_start"}), 400

    initiative = TechnologyRoadmapInitiative(
        name=name,
        description=(data.get("description") or "").strip(),
        fiscal_year_start=fy_start,
        fiscal_year_end=fy_end,
        investment_budget=data.get("investment_budget"),
        status=data.get("status", "planned"),
        category=(data.get("category") or "").strip() or None,
        owner=(data.get("owner") or "").strip() or None,
        solution_id=data.get("solution_id"),
    )
    db.session.add(initiative)
    db.session.commit()
    return jsonify({"success": True, "data": initiative.to_dict()}), 201
