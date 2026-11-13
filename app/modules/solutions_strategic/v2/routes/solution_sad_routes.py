"""Solution Architecture Document (SAD) CRUD APIs.

Provides RESTful endpoints for 14 SAD gap models. Uses a generic CRUD
factory to minimize repetition. Each model gets:
  GET    /solutions/<id>/<resource>        -> list
  POST   /solutions/<id>/<resource>        -> create
  PUT    /solutions/<id>/<resource>/<rid>  -> update
  DELETE /solutions/<id>/<resource>/<rid>  -> delete

Design doc: docs/plans/2026-03-02-solution-sad-models-design.md
"""

import logging
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app import db
from app.models.truly_missing_models import Solution

logger = logging.getLogger(__name__)

solution_sad_bp = Blueprint("solution_sad", __name__, url_prefix="/solutions")


def _refuse_writes_by_readers():
    from app.modules.solutions_strategic.v2.routes.solution_design_routes import (
        refuse_writes_by_people_who_may_only_read,
    )

    return refuse_writes_by_people_who_may_only_read()


solution_sad_bp.before_request(_refuse_writes_by_readers)


def _get_solution_or_404(solution_id):
    solution = db.session.get(Solution, solution_id)
    if not solution:
        return None, (jsonify({"success": False, "error": "Solution not found"}), 404)
    return solution, None


# ── Generic CRUD helpers (reusable for all 14 models) ────────────────

def _list_generic(model_class, solution_id):
    solution, err = _get_solution_or_404(solution_id)
    if err:
        return err
    items = model_class.query.filter_by(solution_id=solution_id).order_by(model_class.id).all()
    return jsonify({"success": True, "items": [i.to_dict() for i in items]})


def _create_generic(model_class, solution_id, required_fields):
    solution, err = _get_solution_or_404(solution_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    for field in required_fields:
        val = data.get(field)
        if val is None or (isinstance(val, str) and not val.strip()):
            return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400
    data["solution_id"] = solution_id
    if hasattr(model_class, "created_by_id"):
        data["created_by_id"] = current_user.id if current_user.is_authenticated else None
    valid_cols = {c.name for c in model_class.__table__.columns}
    filtered = {k: v for k, v in data.items() if k in valid_cols and k != "id"}
    item = model_class(**filtered)
    db.session.add(item)
    db.session.commit()
    return jsonify({"success": True, "item": item.to_dict()}), 201


def _update_generic(model_class, solution_id, item_id):
    solution, err = _get_solution_or_404(solution_id)
    if err:
        return err
    item = db.session.get(model_class, item_id)
    if not item or item.solution_id != solution_id:
        return jsonify({"success": False, "error": "Item not found"}), 404
    data = request.get_json(silent=True) or {}
    valid_cols = {c.name for c in model_class.__table__.columns}
    for k, v in data.items():
        if k in valid_cols and k not in ("id", "solution_id", "created_by_id", "created_at"):
            setattr(item, k, v)
    db.session.commit()
    return jsonify({"success": True, "item": item.to_dict()})


def _delete_generic(model_class, solution_id, item_id):
    solution, err = _get_solution_or_404(solution_id)
    if err:
        return err
    item = db.session.get(model_class, item_id)
    if not item or item.solution_id != solution_id:
        return jsonify({"success": False, "error": "Item not found"}), 404
    db.session.delete(item)
    db.session.commit()
    return jsonify({"success": True})


def _register_crud(model_class, resource_name, required_fields=None):
    """Register list/create/update/delete endpoints for a SAD model."""
    required_fields = required_fields or []
    ep_prefix = f"sad_{resource_name.replace('-', '_')}"

    url_base = f"/<int:solution_id>/{resource_name}"
    url_item = f"/<int:solution_id>/{resource_name}/<int:item_id>"

    # Capture model_class and required_fields in closures
    mc = model_class
    rf = list(required_fields)

    solution_sad_bp.add_url_rule(
        url_base, f"{ep_prefix}_list",
        login_required(lambda solution_id, _mc=mc: _list_generic(_mc, solution_id)),
        methods=["GET"],
    )
    solution_sad_bp.add_url_rule(
        url_base, f"{ep_prefix}_create",
        login_required(lambda solution_id, _mc=mc, _rf=rf: _create_generic(_mc, solution_id, _rf)),
        methods=["POST"],
    )
    solution_sad_bp.add_url_rule(
        url_item, f"{ep_prefix}_update",
        login_required(lambda solution_id, item_id, _mc=mc: _update_generic(_mc, solution_id, item_id)),
        methods=["PUT"],
    )
    solution_sad_bp.add_url_rule(
        url_item, f"{ep_prefix}_delete",
        login_required(lambda solution_id, item_id, _mc=mc: _delete_generic(_mc, solution_id, item_id)),
        methods=["DELETE"],
    )


# ── Register 13 standard models ─────────────────────────────────────

from app.models.solution_sad_models import (  # noqa: E402
    SolutionIntegrationFlow, SolutionComposition, RiskSnapshot,
    SolutionQualityAttribute, SolutionSLA, MigrationDependency,
    SolutionInvestmentPhase, SolutionGovernanceException,
    SolutionComplianceMapping, SolutionChangeRequest,
    SolutionFeasibilityReview, SolutionBenefitRealization,
    SolutionOrgImpact, SolutionLessonLearned,
)

# IntegrationFlow — list/update/delete via factory; custom POST below
solution_sad_bp.add_url_rule(
    "/<int:solution_id>/integration-flows", "sad_integration_flows_list",
    login_required(lambda solution_id: _list_generic(SolutionIntegrationFlow, solution_id)),
    methods=["GET"],
)
solution_sad_bp.add_url_rule(
    "/<int:solution_id>/integration-flows/<int:item_id>", "sad_integration_flows_update",
    login_required(lambda solution_id, item_id: _update_generic(SolutionIntegrationFlow, solution_id, item_id)),
    methods=["PUT"],
)
solution_sad_bp.add_url_rule(
    "/<int:solution_id>/integration-flows/<int:item_id>", "sad_integration_flows_delete",
    login_required(lambda solution_id, item_id: _delete_generic(SolutionIntegrationFlow, solution_id, item_id)),
    methods=["DELETE"],
)

_register_crud(SolutionComposition, "composition", ["component_type", "component_id", "component_name"])
_register_crud(RiskSnapshot, "risk-snapshots", ["risk_name"])
_register_crud(SolutionQualityAttribute, "quality-attributes", ["attribute_name"])
_register_crud(SolutionSLA, "slas", ["sla_name"])
_register_crud(MigrationDependency, "migration-dependencies", ["from_plateau_id", "to_plateau_id"])
_register_crud(SolutionInvestmentPhase, "investment-phases", ["phase_name"])
_register_crud(SolutionGovernanceException, "governance-exceptions", ["exception_description"])
_register_crud(SolutionComplianceMapping, "compliance-mappings", ["framework", "control_id"])
_register_crud(SolutionChangeRequest, "change-requests", ["change_type", "title"])
_register_crud(SolutionFeasibilityReview, "feasibility-reviews", ["review_type"])
_register_crud(SolutionBenefitRealization, "benefit-realizations", ["benefit_name"])
_register_crud(SolutionOrgImpact, "org-impacts", ["impact_area"])
_register_crud(SolutionLessonLearned, "lessons-learned", ["title"])


# ── Custom POST for Integration Flows (ArchiMate auto-creation) ──────

@solution_sad_bp.route("/<int:solution_id>/integration-flows", methods=["POST"],
                       endpoint="sad_integration_flows_create")
@login_required
def create_integration_flow_with_archimate(solution_id):
    """Create integration flow with auto-created ArchiMate flow relationship."""
    solution, err = _get_solution_or_404(solution_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    for field in ["flow_name", "source_app_id", "target_app_id"]:
        if not data.get(field):
            return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

    from app.models.application_portfolio import ApplicationComponent
    source_app = db.session.get(ApplicationComponent, data["source_app_id"])
    target_app = db.session.get(ApplicationComponent, data["target_app_id"])
    if not source_app or not target_app:
        return jsonify({"success": False, "error": "Source or target application not found"}), 404

    # Auto-create ArchiMate flow relationship if both apps have ArchiMate elements
    archimate_flow_id = None
    source_ae_id = getattr(source_app, "archimate_element_id", None)
    target_ae_id = getattr(target_app, "archimate_element_id", None)

    if source_ae_id and target_ae_id:
        try:
            from app.models.archimate_core import ArchiMateRelationship
            flow_rel = ArchiMateRelationship(
                type="flow",
                source_id=source_ae_id,
                target_id=target_ae_id,
            )
            db.session.add(flow_rel)
            db.session.flush()
            archimate_flow_id = flow_rel.id

            from app.models.truly_missing_models import SolutionArchiMateElement
            junction = SolutionArchiMateElement(
                solution_id=solution_id,
                layer_type="application",
                element_id=flow_rel.id,
                element_table="archimate_relationships",
                element_name=data["flow_name"].strip()[:255],
                relationship_type="flow",
                is_new_element=True,
                notes="Auto-created from integration flow",
                created_by_id=current_user.id if current_user.is_authenticated else None,
            )
            db.session.add(junction)
        except Exception as e:
            logger.warning("ArchiMate auto-creation failed for integration flow: %s", e)

    valid_cols = {c.name for c in SolutionIntegrationFlow.__table__.columns}
    filtered = {k: v for k, v in data.items() if k in valid_cols and k not in ("id",)}
    filtered["solution_id"] = solution_id
    filtered["archimate_flow_id"] = archimate_flow_id
    filtered["created_by_id"] = current_user.id if current_user.is_authenticated else None

    flow = SolutionIntegrationFlow(**filtered)
    db.session.add(flow)
    db.session.commit()
    return jsonify({"success": True, "item": flow.to_dict()}), 201


# ── PATCH communication type properties (RUNTIME-01) ────────────────

VALID_COMMUNICATION_TYPES = {"sync_rest", "sync_grpc", "async_event", "async_queue", "batch_file"}
VALID_PROTOCOLS = {"http", "grpc", "kafka", "rabbitmq", "sqs", "sftp", "s3"}
VALID_MESSAGE_FORMATS = {"json", "avro", "protobuf", "xml"}
VALID_AUTH_METHODS = {"none", "api_key", "oauth2", "sasl_ssl", "mtls"}

COMMUNICATION_FIELDS = {
    "communication_type", "protocol", "message_format",
    "auth_method", "topic_or_queue", "dlq_enabled",
}


@solution_sad_bp.route(
    "/<int:solution_id>/integration-flows/<int:flow_id>/communication",
    methods=["PATCH"],
    endpoint="sad_integration_flow_communication",
)
@login_required
def update_flow_communication(solution_id, flow_id):
    """Update communication type properties on an integration flow.

    PATCH /solutions/<id>/integration-flows/<flow_id>/communication
    Body: {"communication_type": "async_event", "protocol": "kafka", ...}
    """
    solution, err = _get_solution_or_404(solution_id)
    if err:
        return err

    flow = SolutionIntegrationFlow.query.filter_by(
        id=flow_id, solution_id=solution_id,
    ).first()
    if not flow:
        return jsonify({"success": False, "error": "Integration flow not found"}), 404

    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    errors = []

    if "communication_type" in data and data["communication_type"] not in VALID_COMMUNICATION_TYPES:
        errors.append(f"Invalid communication_type. Must be one of: {sorted(VALID_COMMUNICATION_TYPES)}")
    if "protocol" in data and data["protocol"] not in VALID_PROTOCOLS:
        errors.append(f"Invalid protocol. Must be one of: {sorted(VALID_PROTOCOLS)}")
    if "message_format" in data and data["message_format"] not in VALID_MESSAGE_FORMATS:
        errors.append(f"Invalid message_format. Must be one of: {sorted(VALID_MESSAGE_FORMATS)}")
    if "auth_method" in data and data["auth_method"] not in VALID_AUTH_METHODS:
        errors.append(f"Invalid auth_method. Must be one of: {sorted(VALID_AUTH_METHODS)}")
    if "topic_or_queue" in data and isinstance(data["topic_or_queue"], str) and len(data["topic_or_queue"]) > 200:
        errors.append("topic_or_queue must be 200 characters or fewer")

    if errors:
        return jsonify({"success": False, "errors": errors}), 400

    for field_name in COMMUNICATION_FIELDS:
        if field_name in data:
            setattr(flow, field_name, data[field_name])

    flow.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({"success": True, "item": flow.to_dict()})
