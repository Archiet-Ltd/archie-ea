"""Risk REST API and UI routes — TPM-013 risk heat map."""
import logging
from datetime import date

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from app import db
from app.models.raid_item import RaidItem, RaidKind, RaidStatus
from app.models.risk import Risk
from app.models.risk_entity_link import ENTITY_TYPES
from app.models.risk_score_history import SCORE_KINDS
from app.services import risk_service

logger = logging.getLogger(__name__)

risk_bp = Blueprint("risk", __name__)


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@risk_bp.route("/api/risks", methods=["GET"])
@login_required
def list_risks():
    """GET /api/risks?solution_id=N — list risks, optionally filtered."""
    solution_id = request.args.get("solution_id", type=int)
    q = Risk.query
    if solution_id is not None:
        q = q.filter_by(solution_id=solution_id)
    risks = q.order_by(Risk.id).all()
    return jsonify([r.to_dict() for r in risks]), 200


@risk_bp.route("/api/risks", methods=["POST"])
@login_required
def create_risk():
    """POST /api/risks — create a risk. Returns 201."""
    data = request.get_json(force=True) or {}
    required = ("title", "likelihood", "impact")
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
    try:
        risk = risk_service.create_risk(
            solution_id=data.get("solution_id"),
            title=data["title"],
            description=data.get("description"),
            likelihood=data["likelihood"],
            impact=data["impact"],
            owner=data.get("owner"),
            mitigation_plan=data.get("mitigation_plan"),
        )
    except (ValueError, KeyError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(risk.to_dict()), 201


@risk_bp.route("/api/risks/<int:risk_id>", methods=["PATCH"])
@login_required
def update_risk(risk_id):
    """PATCH /api/risks/<id> — update risk status, or a full field edit.

    Kept as one endpoint (status-only vs. full edit) rather than splitting
    into two routes: the table's inline status actions and the H2 slide-over's
    Edit form both PATCH here, and a caller sending only `status` gets the
    original narrow behaviour unchanged.
    """
    data = request.get_json(force=True) or {}
    status = data.get("status")
    other_fields = {k: v for k, v in data.items() if k != "status"}
    try:
        if other_fields:
            risk = risk_service.update_risk(risk_id, **other_fields)
            if status:
                risk = risk_service.update_risk_status(risk_id, status)
        elif status:
            risk = risk_service.update_risk_status(risk_id, status)
        else:
            return jsonify({"error": "status or an editable field is required"}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(serialize_risk_row(risk)), 200


@risk_bp.route("/api/risks/<int:risk_id>", methods=["GET"])
@login_required
def get_risk(risk_id):
    """GET /api/risks/<id> — single risk, for the H2 detail slide-over."""
    risk = Risk.query.get_or_404(risk_id)
    return jsonify(serialize_risk_row(risk)), 200


@risk_bp.route("/api/risks/<int:risk_id>", methods=["DELETE"])
@login_required
def delete_risk(risk_id):
    """DELETE /api/risks/<id> — H2: the register could create a risk but
    never remove one, even a duplicate or a mistake."""
    Risk.query.get_or_404(risk_id)
    risk_service.delete_risk(risk_id)
    return jsonify({"success": True}), 200


@risk_bp.route("/api/risks/<int:risk_id>/links", methods=["GET"])
@login_required
def list_risk_links(risk_id):
    """GET /api/risks/<id>/links — H1: entities this risk is mapped to."""
    Risk.query.get_or_404(risk_id)
    links = risk_service.list_risk_links(risk_id)
    return jsonify(
        [
            {**link.to_dict(), "entity_label": _entity_label(link.entity_type, link.entity_id)}
            for link in links
        ]
    ), 200


@risk_bp.route("/api/risks/<int:risk_id>/links", methods=["POST"])
@login_required
def add_risk_link(risk_id):
    """POST /api/risks/<id>/links — H1: map a risk to an Application, Solution
    or Programme. {"entity_type": "application"|"solution"|"programme",
    "entity_id": <int>}"""
    data = request.get_json(force=True) or {}
    entity_type = data.get("entity_type")
    entity_id = data.get("entity_id")
    if entity_type not in ENTITY_TYPES or not entity_id:
        return jsonify(
            {"error": f"entity_type (one of {ENTITY_TYPES}) and entity_id are required"}
        ), 400
    try:
        link = risk_service.add_risk_link(risk_id, entity_type, entity_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(
        {**link.to_dict(), "entity_label": _entity_label(link.entity_type, link.entity_id)}
    ), 201


@risk_bp.route("/api/risks/<int:risk_id>/links/<int:link_id>", methods=["DELETE"])
@login_required
def remove_risk_link(risk_id, link_id):
    """DELETE /api/risks/<id>/links/<link_id> — unmap a risk from an entity."""
    risk_service.remove_risk_link(risk_id, link_id)
    return jsonify({"success": True}), 200


@risk_bp.route("/api/risks/<int:risk_id>/scores", methods=["POST"])
@login_required
def set_risk_score(risk_id):
    """POST /api/risks/<id>/scores — record an inherent or residual
    likelihood/impact score, with history: the score is stored, not only
    displayed. The HTTP surface for risk_service.set_risk_score (the one
    writer), previously reachable only from the backfill command and from
    tests, never from a request. The browser journey that wires a screen to
    it is a later change.
    {"score_kind": "inherent"|"residual", "likelihood": 1-5, "impact": 1-5}
    """
    data = request.get_json(force=True) or {}
    score_kind = data.get("score_kind")
    likelihood = data.get("likelihood")
    impact = data.get("impact")
    if score_kind not in SCORE_KINDS or likelihood is None or impact is None:
        return jsonify({
            "error": "score_kind (one of %s), likelihood and impact are required" % (SCORE_KINDS,)
        }), 400
    try:
        risk = risk_service.set_risk_score(
            risk_id, score_kind, likelihood, impact,
            recorded_by_id=current_user.id,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(serialize_risk_row(risk)), 200


@risk_bp.route("/api/risks/<int:risk_id>/scores", methods=["GET"])
@login_required
def get_risk_score_history(risk_id):
    """GET /api/risks/<id>/scores?score_kind=inherent|residual — history rows,
    oldest first, for risk_service.risk_score_history -- the HTTP surface
    proving a score history row exists per change, not only the latest
    value, over the real HTTP surface rather than only at the service layer."""
    Risk.query.get_or_404(risk_id)
    score_kind = request.args.get("score_kind")
    rows = risk_service.risk_score_history(risk_id, score_kind)
    return jsonify([r.to_dict() for r in rows]), 200


@risk_bp.route("/api/entities/<entity_type>/<int:entity_id>/risks", methods=["GET"])
@login_required
def get_risks_for_entity(entity_type, entity_id):
    """GET /api/entities/<type>/<id>/risks — risks linked to an Application,
    Solution or Programme. The one reader for "which risks threaten this
    element" used by the programme screen, the solution risk tab and the
    risk register's detail view alike."""
    if entity_type not in ENTITY_TYPES:
        return jsonify({"error": f"entity_type must be one of {ENTITY_TYPES}"}), 400
    risks = risk_service.risks_linked_to(entity_type, entity_id)
    return jsonify([serialize_risk_row(r) for r in risks]), 200


@risk_bp.route("/api/programmes/search", methods=["GET"])
@login_required
def search_programmes():
    """GET /api/programmes/search?q=... — the H1 entity picker's Programme
    tab. "Programme" in the product vocabulary is StrategicInitiative in the
    schema (app/models/transformation_programme.py: ProgrammeWorkstream.programme_id
    -> strategic_initiatives.id) -- there was no existing search endpoint for
    it, unlike Applications (DESIGN.md's documented `/applications/api/list`).
    """
    from app.models.strategic import StrategicInitiative

    q = (request.args.get("q") or "").strip()
    query = StrategicInitiative.query
    if q:
        query = query.filter(StrategicInitiative.name.ilike(f"%{q}%"))
    rows = query.order_by(StrategicInitiative.name).limit(10).all()
    return jsonify([{"id": r.id, "name": r.name} for r in rows]), 200


@risk_bp.route("/api/solutions/search", methods=["GET"])
@login_required
def search_solutions_for_risk_link():
    """GET /api/solutions/search?q=... — the H1 entity picker's Solution tab."""
    from app.models.solution_models import Solution

    q = (request.args.get("q") or "").strip()
    query = Solution.query
    if q:
        query = query.filter(Solution.name.ilike(f"%{q}%"))
    rows = query.order_by(Solution.name).limit(10).all()
    return jsonify([{"id": r.id, "name": r.name} for r in rows]), 200


@risk_bp.route("/api/raid", methods=["GET"])
@login_required
def list_raid_items():
    """GET /api/raid?kind=issue|dependency&strategic_initiative_id=N — list RAID
    items, optionally filtered by kind and/or programme. Risk (the "R") lives
    at /api/risks and Assumption (the "A") at demand.Assumption, not here —
    see RaidItem's docstring for why this covers only Issue/Dependency."""
    kind = request.args.get("kind", "").strip()
    q = RaidItem.query
    if kind:
        try:
            q = q.filter_by(kind=RaidKind(kind))
        except ValueError:
            return jsonify({"error": f"Invalid kind: {kind}. Must be one of "
                            f"{[k.value for k in RaidKind]}"}), 400
    strategic_initiative_id = request.args.get("strategic_initiative_id", type=int)
    if strategic_initiative_id:
        q = q.filter_by(strategic_initiative_id=strategic_initiative_id)
    items = q.order_by(RaidItem.id).all()
    return jsonify([i.to_dict() for i in items]), 200


@risk_bp.route("/api/raid", methods=["POST"])
@login_required
def create_raid_item():
    """POST /api/raid — create an Issue/Dependency. Returns 201."""
    data = request.get_json(force=True) or {}
    required = ("kind", "title")
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
    try:
        kind = RaidKind(data["kind"])
    except ValueError:
        return jsonify({"error": f"Invalid kind: {data['kind']!r}. Must be one of "
                        f"{[k.value for k in RaidKind]}"}), 400
    target_date = None
    if data.get("target_date"):
        try:
            target_date = date.fromisoformat(data["target_date"])
        except ValueError:
            return jsonify({"error": "target_date must be YYYY-MM-DD"}), 400
    strategic_initiative_id = data.get("strategic_initiative_id")
    if strategic_initiative_id:
        from app.models.strategic import StrategicInitiative
        programme = db.session.get(StrategicInitiative, strategic_initiative_id)
        # Same canonical-programme gate as the picker itself and the
        # Portfolio module's Benefit linkage — enforced server-side too, so
        # a direct API call can't link to a StrategicInitiative row the UI
        # never offers.
        if programme is None or programme.record_kind != "transformation_programme":
            return jsonify({"error": "Programme not found"}), 400
    item = RaidItem(
        kind=kind,
        title=data["title"],
        description=data.get("description"),
        owner=data.get("owner"),
        target_date=target_date,
        strategic_initiative_id=strategic_initiative_id or None,
        programme_name=data.get("programme_name"),
    )
    db.session.add(item)
    db.session.commit()
    return jsonify(item.to_dict()), 201


@risk_bp.route("/api/raid/<int:item_id>", methods=["PATCH"])
@login_required
def update_raid_item(item_id):
    """PATCH /api/raid/<id> — update status and/or resolution notes."""
    data = request.get_json(force=True) or {}
    item = db.session.get(RaidItem, item_id)
    if item is None:
        return jsonify({"error": "RAID item not found"}), 404
    if "status" in data:
        try:
            item.status = RaidStatus(data["status"])
        except ValueError:
            return jsonify({"error": f"Invalid status: {data['status']!r}. Must be one of "
                            f"{[s.value for s in RaidStatus]}"}), 400
    if "resolution_notes" in data:
        item.resolution_notes = data["resolution_notes"]
    db.session.commit()
    return jsonify(item.to_dict()), 200


@risk_bp.route("/api/raid/<int:item_id>", methods=["DELETE"])
@login_required
def delete_raid_item(item_id):
    """DELETE /api/raid/<id> — remove a RAID item that was logged in error."""
    item = db.session.get(RaidItem, item_id)
    if item is None:
        return jsonify({"error": "RAID item not found"}), 404
    db.session.delete(item)
    db.session.commit()
    return jsonify({"deleted": True}), 200


@risk_bp.route("/api/risks/heat-map", methods=["GET"])
@login_required
def risk_heat_map_data():
    """GET /api/risks/heat-map?solution_id=N — 5×5 grid data."""
    solution_id = request.args.get("solution_id", type=int)
    data = risk_service.get_heat_map_data(solution_id=solution_id)
    return jsonify(data), 200


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

# T-14 (2 Sep 2026 audit): the risk register was one of six tables with no sort
# at all. Server-side, query-param driven sort — consistent with how a plain
# Jinja-rendered table (no Alpine reactive array backing it) sorts elsewhere in
# this codebase, and it survives a page reload/bookmark, which a client-only
# sort would not. Column keys map to real, indexed-or-cheap-to-sort columns
# only; an unrecognised key falls back to the existing default rather than
# raising, so a stale/hand-edited URL can't 500 the page.
_RISK_SORT_COLUMNS = {
    "title": Risk.title,
    "owner": Risk.owner,
    "likelihood": Risk.likelihood,
    "impact": Risk.impact,
    "status": Risk.status,
}


def _entity_label(entity_type, entity_id):
    """Human label for a linked Application/Solution/Programme -- the H2
    slide-over and H1's parent-side "Linked risks" sections both need a name,
    not just an id, and each entity type lives in a different model."""
    try:
        if entity_type == "application":
            from app.models.application_portfolio import ApplicationComponent
            row = ApplicationComponent.query.get(entity_id)
        elif entity_type == "solution":
            from app.models.solution_models import Solution
            row = Solution.query.get(entity_id)
        elif entity_type == "programme":
            from app.models.strategic import StrategicInitiative
            row = StrategicInitiative.query.get(entity_id)
        else:
            return None
        return getattr(row, "name", None) if row else None
    except Exception:  # noqa: BLE001 -- a stale/orphaned link must not break the page
        logger.warning("Could not resolve %s #%s for a risk link", entity_type, entity_id, exc_info=True)
        return None


def serialize_risk_row(risk):
    """The one place a Risk becomes a row for the shared data_table component.

    Shared with tests so a test asserting what the table shows builds its
    expectation from the same serialization the route actually renders,
    rather than a second, independently-maintained copy that can drift.
    """
    links = risk_service.list_risk_links(risk.id)
    return {
        "id": risk.id,
        "title": risk.title,
        "description": risk.description or "",
        "mitigation_plan": risk.mitigation_plan or "",
        "owner": risk.owner or "—",
        "likelihood": risk.likelihood,
        "impact": risk.impact,
        # Additive, so a caller can read a risk back and see both scores: no
        # existing key removed or renamed, so no current reader of this shape
        # (the H2 slide-over) is affected by their presence.
        "inherent_likelihood": risk.inherent_likelihood,
        "inherent_impact": risk.inherent_impact,
        "residual_likelihood": risk.residual_likelihood,
        "residual_impact": risk.residual_impact,
        "risk_score": risk.risk_score,
        "risk_level": risk.risk_level,
        "status": risk.status.value,
        "entity_links": [
            {
                "id": link.id,
                "entity_type": link.entity_type,
                "entity_id": link.entity_id,
                # None (not a fabricated placeholder) when the linked row is
                # gone -- the UI shows this distinctly, same rule as M4.
                "entity_label": _entity_label(link.entity_type, link.entity_id),
            }
            for link in links
        ],
    }


@risk_bp.route("/risks/")
@login_required
def risk_register():
    """Standalone Risk Register page — shows all risks across the enterprise."""
    sort_key = request.args.get("sort", "id")
    direction = request.args.get("dir", "asc")
    column = _RISK_SORT_COLUMNS.get(sort_key, Risk.id)
    order = column.desc() if direction == "desc" else column.asc()
    risks = Risk.query.order_by(order, Risk.id).all()
    heat_data = risk_service.get_heat_map_data(solution_id=None)
    # RAID (2 Sep 2026, Capgemini delivery-team dry-run): the register was
    # Risk-only — 1 of the 4 RAID categories. Loaded here so the same page
    # can show Issues/Dependencies without a separate navigation destination.
    # Risk (R) and Assumption (A, demand.Assumption) already have their own
    # homes; this covers only the two categories that had none.
    raid_items = RaidItem.query.order_by(RaidItem.kind, RaidItem.id).all()
    from app.models.strategic import StrategicInitiative
    # Same gate the Portfolio module already uses for "which programme owns
    # this Benefit" (portfolio_routes.detail): only StrategicInitiative rows
    # explicitly marked record_kind='transformation_programme' are canonical
    # programmes. Listing every StrategicInitiative here would surface
    # ordinary internal initiatives (upgrades, one-off projects) alongside
    # real cross-cutting programmes — a second, inconsistent definition of
    # "programme" on top of the two aggregate roots already in play.
    programmes = (
        StrategicInitiative.query
        .filter(StrategicInitiative.record_kind == "transformation_programme")
        .order_by(StrategicInitiative.name)
        .all()
    )
    risk_rows = [serialize_risk_row(r) for r in risks]
    return render_template(
        "governance/risk_register.html",
        risks=risks,
        risk_rows=risk_rows,
        grid=heat_data.get("grid", []),
        total=len(risks),
        current_sort=sort_key if sort_key in _RISK_SORT_COLUMNS else "id",
        current_dir=direction if direction in ("asc", "desc") else "asc",
        raid_items=raid_items,
        raid_kinds=[k.value for k in RaidKind],
        programmes=[{"id": p.id, "name": p.name} for p in programmes],
    )


@risk_bp.route("/solutions/<int:solution_id>/risks", methods=["GET"])
@login_required
def risk_heat_map_page(solution_id):
    """Render the risk heat map page for a solution."""
    data = risk_service.get_heat_map_data(solution_id=solution_id)
    return render_template(
        "solutions/risk_heat_map.html",
        solution_id=solution_id,
        grid=data["grid"],
        risks=data["risks"],
    )
