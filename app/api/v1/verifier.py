"""Verifier write-path API (ENT-V-1, ENT-V-5..8).

A single CSRF-exempt, key-authenticated blueprint. Every route is guarded by
@api_key_auth + @require_scope("verifier:write") + @require_org(68), so only the
Ifiok verifier key reaches it and every write is scoped to organisation 68.

Verdict writes exist ONLY here — there is no session route that sets a verdict —
so any other identity gets 401 (no key) or 403 (wrong scope/org); the attempt is
logged (ENT-V-5).

Section 36: this key may create requirements as *Proposed* only. Confirming a
requirement is a person's action on the Requirements page (ENT-V-2), never a
key's; there is deliberately no confirm route on this blueprint.
"""

import logging
from datetime import datetime

from flask import Blueprint, g, request

from app._bootstrap.build_info import get_build_id
from app.auth.api_key import api_key_auth, require_scope, require_verifier_org
from app.extensions import db
from app.utils.api_response import (
    error_response,
    not_found_response,
    success_response,
    validation_error_response,
)

logger = logging.getLogger(__name__)

IFIOK_ORG_ID = 68
VERIFIER_SCOPE = "verifier:write"
BUILDER_SCOPE = "builder:claim"

verifier_bp = Blueprint("verifier", __name__)


def _requirement_verified(element):
    """ENT-V-7: a requirement is verified only while a Verified verdict exists
    for the current served commit."""
    from app.models.verdict import Verdict

    served = get_build_id()
    latest = (
        Verdict.query.filter_by(
            requirement_element_id=element.id, served_commit=served, status=Verdict.VERIFIED
        )
        .order_by(Verdict.id.desc())
        .first()
    )
    return latest is not None


def _requirement_json(element):
    from app.models.verdict import Verdict

    latest = (
        Verdict.query.filter_by(requirement_element_id=element.id)
        .order_by(Verdict.id.desc())
        .first()
    )
    return {
        "id": element.id,
        "name": element.name,
        "status": element.status,  # Proposed | Confirmed | Rejected | ...
        "verified": _requirement_verified(element),  # against current served commit
        "latest_verdict": (
            {
                "status": latest.status,
                "served_commit": latest.served_commit,
                "result_line": latest.result_line,
                "unmet_parts": latest.unmet_parts,
                "recorded_by": latest.recorded_by,
            }
            if latest
            else None
        ),
        "source_kind": element.source_kind,
        "source_ref": element.source_ref,
    }


@verifier_bp.route("/requirements", methods=["POST"])
@api_key_auth
@require_scope(VERIFIER_SCOPE)
@require_verifier_org
def post_requirement():
    """ENT-V-1: store a verifier-posted requirement as a Proposed Requirement in
    org 68, awaiting a person's confirmation."""
    from app.models.models import ArchiMateElement

    data = request.get_json(silent=True) or {}
    sentence = (data.get("ears_sentence") or data.get("sentence") or "").strip()
    name = (data.get("name") or sentence).strip()
    if not name:
        return validation_error_response(
            {"name": "a requirement name or EARS sentence is required"}
        )

    # Keep the structured EARS parts on the element's properties for the page.
    properties = {
        "ears_sentence": sentence or None,
        "ears_pattern": data.get("pattern"),
        "stakeholder": data.get("stakeholder"),
        "screen": data.get("screen"),
        "test": data.get("test"),
        "source_element": data.get("source_element"),
    }

    element = ArchiMateElement(
        name=name[:500],
        type="Requirement",
        layer="motivation",
        description=sentence or data.get("description"),
        status="Proposed",
        source_kind="assistant",
        source_ref=str(data.get("source_ref") or g.api_key.label),
        source_label=g.api_key.label,
    )
    # properties is a JSON-in-Text column on this model.
    try:
        import json as _json

        element.properties = _json.dumps({k: v for k, v in properties.items() if v is not None})
    except Exception:  # pragma: no cover - never block a create on metadata
        pass

    # organization_id is auto-stamped to g.current_org_id (68) by the tenant
    # before_flush listener; set it explicitly too (defence in depth).
    element.organization_id = g.current_org_id
    db.session.add(element)
    db.session.commit()

    logger.info("verifier posted Proposed requirement id=%s org=%s", element.id, g.current_org_id)
    return success_response(_requirement_json(element), status_code=201)


@verifier_bp.route("/requirements", methods=["GET"])
@api_key_auth
@require_scope(VERIFIER_SCOPE)
@require_verifier_org
def list_requirements():
    """List org-68 Requirement elements with status + verified flag (ENT-V-7)."""
    from app.models.models import ArchiMateElement

    elements = (
        ArchiMateElement.query.filter_by(type="Requirement")
        .order_by(ArchiMateElement.id.desc())
        .limit(500)
        .all()
    )
    return success_response([_requirement_json(e) for e in elements])


@verifier_bp.route("/verdicts", methods=["POST"])
@api_key_auth
@require_scope(VERIFIER_SCOPE)
@require_verifier_org
def post_verdict():
    """ENT-V-6: record the verifier's verdict (Verified|Failed) on a requirement,
    with the served commit, command, result line and unmet parts."""
    from app.models.models import ArchiMateElement
    from app.models.verdict import Verdict

    data = request.get_json(silent=True) or {}
    req_id = data.get("requirement_element_id")
    status = (data.get("status") or "").strip().capitalize()
    if status not in (Verdict.VERIFIED, Verdict.FAILED):
        return validation_error_response(
            {"status": f"must be '{Verdict.VERIFIED}' or '{Verdict.FAILED}'"}
        )
    if not req_id:
        return validation_error_response({"requirement_element_id": "required"})

    # The tenant filter already scopes this to org 68; a requirement in another
    # org is invisible here and returns not-found.
    element = ArchiMateElement.query.filter_by(id=req_id, type="Requirement").first()
    if element is None:
        return not_found_response("Requirement")

    verdict = Verdict(
        requirement_element_id=element.id,
        status=status,
        served_commit=get_build_id(),
        command=data.get("command"),
        result_line=data.get("result_line"),
        unmet_parts=data.get("unmet_parts"),
        recorded_by=g.api_key.label,
    )
    verdict.organization_id = g.current_org_id
    db.session.add(verdict)

    # ENT-V-6: a Failed verdict reopens the requirement's task to its builder,
    # with the unmet parts, so the builder sees what to fix.
    if status == Verdict.FAILED:
        from app.models.requirement_task import RequirementTask

        task = (
            RequirementTask.query.filter_by(requirement_element_id=element.id)
            .order_by(RequirementTask.id.desc())
            .first()
        )
        if task is not None:
            task.status = RequirementTask.REOPENED
            task.unmet_parts = data.get("unmet_parts")

    db.session.commit()

    logger.info(
        "verifier recorded %s verdict req=%s commit=%s", status, element.id, verdict.served_commit
    )
    return success_response(
        {
            "verdict_id": verdict.id,
            "requirement": _requirement_json(element),
        },
        status_code=201,
    )


@verifier_bp.route("/work-packages", methods=["POST"])
@api_key_auth
@require_scope(BUILDER_SCOPE)
@require_verifier_org
def claim_work_package():
    """ENT-V-3: a builder identity claims a Confirmed requirement, creating a task
    that shows its holder and the time claimed."""
    from app.models.models import ArchiMateElement
    from app.models.requirement_task import RequirementTask

    data = request.get_json(silent=True) or {}
    req_id = data.get("requirement_element_id")
    if not req_id:
        return validation_error_response({"requirement_element_id": "required"})

    element = ArchiMateElement.query.filter_by(id=req_id, type="Requirement").first()
    if element is None:
        return not_found_response("Requirement")
    if element.status != "Confirmed":
        return error_response(
            f"Only a Confirmed requirement can be claimed (this one is {element.status}).",
            status_code=409,
        )

    now = datetime.utcnow()
    task = RequirementTask(
        requirement_element_id=element.id,
        holder=g.api_key.label,
        status=RequirementTask.CLAIMED,
        claimed_at=now,
    )
    task.organization_id = g.current_org_id
    db.session.add(task)
    db.session.commit()
    logger.info("builder %s claimed requirement %s (task %s)", g.api_key.label, element.id, task.id)
    return success_response(
        {
            "task_id": task.id,
            "requirement_element_id": element.id,
            "holder": task.holder,
            "status": task.status,
            "claimed_at": task.claimed_at.isoformat() + "Z",
        },
        status_code=201,
    )


@verifier_bp.route("/work-packages/<int:task_id>/deliver", methods=["POST"])
@api_key_auth
@require_scope(BUILDER_SCOPE)
@require_verifier_org
def deliver_work_package(task_id):
    """ENT-V-4: a builder reports a delivery; the task then shows
    'Delivered, awaiting verification', never Done."""
    from app.models.requirement_task import RequirementTask

    task = RequirementTask.query.filter_by(id=task_id).first()
    if task is None:
        return not_found_response("Task")

    data = request.get_json(silent=True) or {}
    now = datetime.utcnow()
    task.status = RequirementTask.DELIVERED
    task.delivered_at = now
    task.commit = data.get("commit")
    task.files = data.get("files")
    task.test = data.get("test")
    task.command = data.get("command")
    task.result_line = data.get("result_line")
    db.session.commit()
    logger.info("builder %s delivered task %s", g.api_key.label, task.id)
    return success_response(
        {
            "task_id": task.id,
            "status": task.status,  # "Delivered, awaiting verification"
            "delivered_at": task.delivered_at.isoformat() + "Z",
        }
    )
