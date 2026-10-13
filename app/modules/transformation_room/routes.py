"""Stable server-rendered Transformation Room routes.

Routes attach to the established ``solution_design`` blueprint. Browser
mutations delegate to operation-specific public services and never call the
command service directly.
"""

from __future__ import annotations

import uuid

from flask import abort, g, redirect, render_template, request
from flask_login import current_user, login_required

from app.modules.transformation_room.domain import ActorContext, TransformationError
from app.modules.transformation_room.programme_service import TransformationProgrammeService
from app.modules.transformation_room.read_models import STAGE_ROUTES, TransformationRoomReadModel


def actor_from_request() -> ActorContext:
    organization_id = getattr(g, "current_org_id", None)
    if organization_id is None:
        from app.modules.transformation_room.domain import AuthenticationRequired

        raise AuthenticationRequired("active_organization_required")
    roles = {
        role
        for role in (
            getattr(current_user, "enterprise_role", None),
            "organization_admin" if getattr(current_user, "is_org_admin", False) else None,
            "platform_admin" if getattr(current_user, "is_platform_admin", False) else None,
        )
        if role
    }
    return ActorContext(
        user_id=current_user.id,
        organization_id=organization_id,
        roles=frozenset(roles),
        request_id=request.headers.get("X-Request-ID") or str(uuid.uuid4()),
    )


def _render_error(error: TransformationError):
    if error.http_status == 404:
        return render_template("errors/404.html"), 404
    if error.http_status == 403:
        return render_template("errors/403.html"), 403
    return render_template("errors/500.html"), error.http_status


def _lead_options(programme_id: int):
    from app.models.user import User

    organization_id = getattr(g, "current_org_id", None)
    if organization_id is None:
        return ()
    users = (
        User.query.filter(User.organization_id == organization_id)
        .order_by(User.email.asc(), User.id.asc())
        .all()
    )
    return tuple({"id": u.id, "label": f"{u.full_name()} — {u.email}"} for u in users)


def _room(programme_id: int, workstream_id: int | None = None, stage: str | None = None):
    room = TransformationRoomReadModel.room(
        actor=actor_from_request(),
        programme_id=programme_id,
        workstream_id=workstream_id,
        stage=stage,
    )
    room["command_key"] = request.form.get("command_key") or str(uuid.uuid4())
    return room


def register_transformation_room_routes(blueprint) -> None:
    @blueprint.route("/programmes/<int:programme_id>/overview", methods=["GET"])
    @login_required
    def transformation_programme_overview(programme_id):
        try:
            room = _room(programme_id)
        except TransformationError as error:
            return _render_error(error)
        return render_template("solutions/transformation_room/overview.html", room=room)

    @blueprint.route("/programmes/<int:programme_id>/workstreams", methods=["GET", "POST"])
    @login_required
    def transformation_programme_workstreams(programme_id):
        """DEF-010, Capgemini dry-run: this page had no create control at all
        — the new-programme wizard could create exactly one workstream and
        there was no way to add another, so every programme was permanently
        limited to its founding workstream."""
        from app.models.transformation_programme import WORKSTREAM_TYPES
        from app.modules.transformation_room.programme_service import TransformationProgrammeService

        if request.method == "POST":
            try:
                actor = actor_from_request()
                # Prove the URL programme is real and readable before the
                # create command is allowed to touch it.
                _room(programme_id)
                lead_id = int(request.form.get("lead_id") or 0)
                TransformationProgrammeService.create_workstream(
                    actor=actor,
                    programme_id=programme_id,
                    workstream_type=request.form.get("workstream_type", ""),
                    objective=request.form.get("objective", ""),
                    scope_expression={
                        "business_units": [
                            value.strip()
                            for value in request.form.get("scope_expression", "").split(",")
                            if value.strip()
                        ]
                    },
                    target_date=request.form.get("target_date") or None,
                    target_date_unavailable_reason=request.form.get("target_date_unavailable_reason") or None,
                    lead_id=lead_id,
                    command_key=request.form.get("command_key", "").strip() or str(uuid.uuid4()),
                )
                return redirect(request.path, code=303)
            except (TypeError, ValueError) as error:
                try:
                    room = _room(programme_id)
                except TransformationError as room_error:
                    return _render_error(room_error)
                room["stage_label"] = "Workstreams"
                room["form_error"] = str(error)
                room["lead_options"] = _lead_options(programme_id)
                return render_template("solutions/transformation_room/workstreams.html", room=room), 400
            except TransformationError as error:
                if error.http_status not in {403, 404}:
                    try:
                        room = _room(programme_id)
                    except TransformationError as room_error:
                        return _render_error(room_error)
                    room["stage_label"] = "Workstreams"
                    room["form_error"] = error.reason
                    room["lead_options"] = _lead_options(programme_id)
                    return render_template("solutions/transformation_room/workstreams.html", room=room), error.http_status
                return _render_error(error)

        try:
            room = _room(programme_id)
        except TransformationError as error:
            return _render_error(error)
        room["stage_label"] = "Workstreams"
        room["workstream_types"] = WORKSTREAM_TYPES
        room["lead_options"] = _lead_options(programme_id)
        return render_template("solutions/transformation_room/workstreams.html", room=room)

    @blueprint.route(
        "/programmes/<int:programme_id>/workstreams/<int:workstream_id>/<stage>",
        methods=["GET", "POST"],
    )
    @login_required
    def transformation_workstream_stage(programme_id, workstream_id, stage):
        if stage not in STAGE_ROUTES:
            return render_template("errors/404.html"), 404
        try:
            actor = actor_from_request()
            if request.method == "POST":
                if stage != "objective":
                    abort(405)
                # Prove that the URL programme owns this workstream before the
                # operation-specific service is allowed to mutate it.
                _room(programme_id, workstream_id, stage)
                result = TransformationProgrammeService.update_objective(
                    actor=actor,
                    workstream_id=workstream_id,
                    objective=request.form.get("objective", ""),
                    scope_expression={
                        "business_units": [
                            value.strip()
                            for value in request.form.get("scope_expression", "").split(",")
                            if value.strip()
                        ]
                    },
                    expected_revision=int(request.form.get("expected_revision", "0")),
                    command_key=request.form.get("command_key", "").strip(),
                )
                if not result.object_ids.get("workstream_id"):
                    raise ValueError("Objective update returned no canonical workstream ID.")
                return redirect(request.path, code=303)
            room = _room(programme_id, workstream_id, stage)
        except (TypeError, ValueError) as error:
            try:
                room = _room(programme_id, workstream_id, stage)
            except TransformationError as room_error:
                return _render_error(room_error)
            room["form_error"] = str(error)
            return render_template(f"solutions/transformation_room/{stage}.html", room=room), 400
        except TransformationError as error:
            if request.method == "POST" and error.http_status not in {403, 404}:
                try:
                    room = _room(programme_id, workstream_id, stage)
                except TransformationError as room_error:
                    return _render_error(room_error)
                room["form_error"] = error.reason
                return render_template(
                    f"solutions/transformation_room/{stage}.html", room=room
                ), error.http_status
            return _render_error(error)
        return render_template(f"solutions/transformation_room/{stage}.html", room=room)

    @blueprint.route(
        "/programmes/<int:programme_id>/workstreams/<int:workstream_id>/<stage>/advance",
        methods=["POST"],
    )
    @login_required
    def transformation_workstream_advance(programme_id, workstream_id, stage):
        """Advance a workstream to its next lifecycle stage.

        F-07, Capgemini dry-run: TransformationGateService.transition (the
        real, gate-checked state machine) and its command-service plumbing
        already existed; nothing in this module ever called it from a
        server-rendered POST — every stage past Objective was permanently
        read-only as a result. Mirrors the existing "objective" POST branch's
        error handling above.
        """
        from app.modules.transformation_room.gate_service import TransformationGateService

        if stage not in STAGE_ROUTES:
            return render_template("errors/404.html"), 404
        target_stage = TransformationGateService.NEXT_STAGE.get(stage)
        if target_stage is None:
            abort(404)
        try:
            actor = actor_from_request()
            # Prove that the URL programme owns this workstream before the
            # gate service is allowed to mutate it.
            _room(programme_id, workstream_id, stage)
            TransformationGateService.transition(
                actor=actor,
                workstream_id=workstream_id,
                target_stage=target_stage,
                expected_revision=int(request.form.get("expected_revision", "0")),
                command_key=request.form.get("command_key", "").strip() or str(uuid.uuid4()),
            )
            return redirect(
                f"/solutions/programmes/{programme_id}/workstreams/{workstream_id}/{target_stage}",
                code=303,
            )
        except (TypeError, ValueError) as error:
            try:
                room = _room(programme_id, workstream_id, stage)
            except TransformationError as room_error:
                return _render_error(room_error)
            room["form_error"] = str(error)
            return render_template(f"solutions/transformation_room/{stage}.html", room=room), 400
        except TransformationError as error:
            if error.http_status not in {403, 404}:
                try:
                    room = _room(programme_id, workstream_id, stage)
                except TransformationError as room_error:
                    return _render_error(room_error)
                room["form_error"] = error.reason
                return render_template(
                    f"solutions/transformation_room/{stage}.html", room=room
                ), error.http_status
            return _render_error(error)

    @blueprint.route(
        "/programmes/<int:programme_id>/workstreams/<int:workstream_id>/archimate-element",
        methods=["POST"],
    )
    @login_required
    def transformation_workstream_add_to_model(programme_id, workstream_id):
        """R1-05 (US-11): give an existing workstream its ArchiMate WorkPackage
        element. CSRF-covered form post; idempotent."""
        try:
            actor = actor_from_request()
            TransformationProgrammeService.add_workstream_to_model(
                actor=actor,
                programme_id=programme_id,
                workstream_id=workstream_id,
                command_key=request.form.get("command_key", "").strip() or str(uuid.uuid4()),
            )
            return redirect(
                f"/solutions/programmes/{programme_id}/workstreams/{workstream_id}/objective",
                code=303,
            )
        except (TypeError, ValueError) as error:
            try:
                room = _room(programme_id, workstream_id, "objective")
            except TransformationError as room_error:
                return _render_error(room_error)
            room["form_error"] = str(error)
            return render_template("solutions/transformation_room/objective.html", room=room), 400
        except TransformationError as error:
            if error.http_status not in {403, 404}:
                try:
                    room = _room(programme_id, workstream_id, "objective")
                except TransformationError as room_error:
                    return _render_error(room_error)
                room["form_error"] = error.reason
                return render_template(
                    "solutions/transformation_room/objective.html", room=room
                ), error.http_status
            return _render_error(error)

    @blueprint.route("/programmes/<int:programme_id>/governance", methods=["GET"])
    @login_required
    def transformation_programme_governance(programme_id):
        try:
            room = _room(programme_id)
        except TransformationError as error:
            return _render_error(error)
        room["stage_label"] = "Governance"
        return render_template("solutions/transformation_room/governance.html", room=room)

    @blueprint.route("/programmes/<int:programme_id>/roadmap", methods=["GET"])
    @login_required
    def transformation_programme_roadmap(programme_id):
        try:
            room = _room(programme_id)
        except TransformationError as error:
            return _render_error(error)
        room["stage_label"] = "Roadmap"
        return render_template("solutions/transformation_room/roadmap.html", room=room)


__all__ = ["actor_from_request", "register_transformation_room_routes"]
