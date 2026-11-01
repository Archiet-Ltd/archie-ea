"""DMN decision tables: the list, one decision's table, its rules, its link to a process, evaluate and export.

Decisions are model elements (see ``decision_table_service``), so reading is open to every signed-in
member of the organisation and every write needs the permission to change the model, the same answer
the other model screens use (``can_change_model``). Files go in and out through the existing model
import screen; this blueprint only serves the DMN download.
"""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.core.auth.permissions import can_change_model
from app.modules.architecture.services import decision_exchange_service as exchange
from app.modules.architecture.services import decision_table_service as tables
from app.modules.architecture.services.decision_table_service import DecisionError

decision_tables_bp = Blueprint("decision_tables", __name__, url_prefix="/architecture/decision-tables")


def model_changers_only(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not can_change_model(current_user):
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def _decision_or_404(decision_id: int):
    try:
        return tables.get_decision(decision_id)
    except DecisionError as exc:
        abort(exc.status_code)


def _back(decision_id: int, anchor: str = ""):
    return redirect(url_for("decision_tables.detail", decision_id=decision_id) + anchor)


def _guarded(decision_id: int, action, success: str, anchor: str = ""):
    """Run a write on one decision, commit, and answer with a message either way."""
    decision = _decision_or_404(decision_id)
    try:
        action(decision)
        db.session.commit()
        flash(success, "success")
    except DecisionError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _back(decision_id, anchor)


@decision_tables_bp.route("/", methods=["GET"])
@login_required
def index():
    org_id = tables.current_org_id()
    decisions = tables.list_decisions(org_id)
    counts = tables.rule_counts(org_id)
    rows = []
    for decision in decisions:
        d = tables.definition(decision)
        rows.append({
            "id": decision.id, "name": decision.name, "policy": d["hit_policy"],
            "policy_name": tables.HIT_POLICIES[d["hit_policy"]][1],
            "inputs": len(d["inputs"]), "outputs": len(d["outputs"]), "rules": counts.get(decision.id, 0),
            "processes": [p.name for p in tables.processes_of(decision)],
        })
    return render_template(
        "architecture/decision_tables.html", rows=rows, policies=tables.hit_policy_choices(),
        can_edit=can_change_model(current_user),
    )


@decision_tables_bp.route("/", methods=["POST"])
@model_changers_only
def create():
    try:
        decision = tables.create_decision(
            request.form.get("name"), request.form.get("hit_policy") or "U",
            description=request.form.get("description"),
        )
        db.session.commit()
    except DecisionError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("decision_tables.index"))
    flash("Decision created. Add its columns, then its rules.", "success")
    return _back(decision.id)


@decision_tables_bp.route("/<int:decision_id>", methods=["GET"])
@login_required
def detail(decision_id: int):
    return _render_detail(_decision_or_404(decision_id))


def _render_detail(decision, evaluation=None, tested=None, status: int = 200):
    d = tables.definition(decision)
    rules = tables.rules_of(decision)
    return render_template(
        "architecture/decision_table.html",
        decision=decision, definition=d, rules=rules, policies=tables.hit_policy_choices(),
        policy_name=tables.HIT_POLICIES[d["hit_policy"]][1],
        policy_meaning=tables.HIT_POLICIES[d["hit_policy"]][2],
        processes=tables.processes_of(decision), linkable=tables.linkable_processes(decision),
        evaluation=evaluation, tested=tested or [],
        matched=set(evaluation["matched"]) if evaluation else set(),
        display=tables.display_value, can_edit=can_change_model(current_user),
        type_labels={"string": "text", "number": "number", "boolean": "true/false"},
    ), status


@decision_tables_bp.route("/<int:decision_id>/settings", methods=["POST"])
@model_changers_only
def settings(decision_id: int):
    return _guarded(
        decision_id,
        lambda d: tables.update_decision(
            d, name=request.form.get("name"), hit_policy=request.form.get("hit_policy"),
            description=request.form.get("description", ""),
        ),
        "Decision saved.",
    )


@decision_tables_bp.route("/<int:decision_id>/columns", methods=["POST"])
@model_changers_only
def add_column(decision_id: int):
    return _guarded(
        decision_id,
        lambda d: tables.add_column(
            d, request.form.get("kind", ""), request.form.get("label"), request.form.get("type") or "string"
        ),
        "Column added.",
    )


@decision_tables_bp.route("/<int:decision_id>/columns/<kind>/<int:index>/remove", methods=["POST"])
@model_changers_only
def remove_column(decision_id: int, kind: str, index: int):
    return _guarded(decision_id, lambda d: tables.remove_column(d, kind, index), "Column removed.")


@decision_tables_bp.route("/<int:decision_id>/rules", methods=["POST"])
@model_changers_only
def save_rule(decision_id: int):
    raw = request.form.get("position", "").strip()
    position = int(raw) if raw.isdigit() else None
    return _guarded(
        decision_id,
        lambda d: tables.save_rule(
            d, request.form.getlist("input"), request.form.getlist("output"),
            request.form.get("annotation"), position,
        ),
        "Rule saved.",
    )


@decision_tables_bp.route("/<int:decision_id>/rules/<int:position>/delete", methods=["POST"])
@model_changers_only
def delete_rule(decision_id: int, position: int):
    return _guarded(decision_id, lambda d: tables.delete_rule(d, position), "Rule %d removed." % position)


@decision_tables_bp.route("/<int:decision_id>/processes", methods=["POST"])
@model_changers_only
def link_process(decision_id: int):
    try:
        process_id = int(request.form.get("process_id", ""))
    except ValueError:
        flash("Choose the business process that applies this decision.", "error")
        return _back(decision_id, "#processes")
    return _guarded(
        decision_id, lambda d: tables.link_process(d, process_id), "Linked to the business process.", "#processes"
    )


@decision_tables_bp.route("/<int:decision_id>/processes/<int:process_id>/remove", methods=["POST"])
@model_changers_only
def unlink_process(decision_id: int, process_id: int):
    return _guarded(
        decision_id, lambda d: tables.unlink_process(d, process_id), "Link removed.", "#processes"
    )


@decision_tables_bp.route("/<int:decision_id>/evaluate", methods=["POST"])
@login_required
def evaluate(decision_id: int):
    decision = _decision_or_404(decision_id)
    d = tables.definition(decision)
    tested = [request.form.get("value_%d" % i, "") for i in range(len(d["inputs"]))]
    try:
        result = tables.evaluate(decision, tested)
    except DecisionError as exc:
        result = {"policy": d["hit_policy"], "matched": [], "outputs": [], "error": str(exc), "message": ""}
    return _render_detail(decision, result, tested)


@decision_tables_bp.route("/<int:decision_id>/delete", methods=["POST"])
@model_changers_only
def delete(decision_id: int):
    decision = _decision_or_404(decision_id)
    name = decision.name
    tables.delete_decision(decision)
    db.session.commit()
    flash("%s was deleted." % name, "success")
    return redirect(url_for("decision_tables.index"))


@decision_tables_bp.route("/<int:decision_id>/export.dmn", methods=["GET"])
@login_required
def export(decision_id: int):
    decision = _decision_or_404(decision_id)
    return Response(
        exchange.export_dmn(decision), mimetype="application/xml",
        headers={"Content-Disposition": 'attachment; filename="%s"' % exchange.export_filename(decision)},
    )
