"""API routes for framework adoption, harmonisation, applicability and regulatory change."""

from datetime import datetime

from flask import g, jsonify, request
from flask_login import current_user, login_required

from app import db
from app.application_mgmt import application_mgmt
from app.decorators import require_roles
from app.middleware.tenant_decorators import platform_admin_required
from app.models.compliance_models import RegulatoryFramework
from app.models.regulatory_change import RegulatoryChange
from app.models.regulatory_framework import FrameworkAdoption
from app.modules.compliance.services.applicability_service import ApplicabilityService


# ── Framework adoption ────────────────────────────────────────────────


@application_mgmt.route("/api/compliance/frameworks/<int:framework_id>/adopt", methods=["POST"])
@login_required
@require_roles("admin", "architect", "business_architect", "security_architect")
def adopt_framework(framework_id):
    """Adopt a framework for the current organisation, copying its controls."""
    org_id = getattr(g, "current_org_id", None)
    if org_id is None:
        return jsonify({"error": "No organisation context"}), 400

    try:
        adoption = ApplicabilityService.adopt_framework(
            organization_id=org_id,
            framework_id=framework_id,
            adopted_by_id=current_user.id,
        )
        control_count = adoption.adopted_controls.count()
        return jsonify({
            "success": True,
            "adoption_id": adoption.id,
            "framework_code": adoption.framework.code,
            "controls_adopted": control_count,
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/compliance/adoptions", methods=["GET"])
@login_required
def list_adoptions():
    """List frameworks adopted by the current organisation."""
    org_id = getattr(g, "current_org_id", None)
    if org_id is None:
        return jsonify({"adoptions": []})

    adoptions = FrameworkAdoption.query.filter_by(
        organization_id=org_id, status="active"
    ).all()

    return jsonify({
        "adoptions": [
            {
                "id": a.id,
                "framework_id": a.framework_id,
                "framework_code": a.framework.code,
                "framework_name": a.framework.name,
                "adopted_at": a.adopted_at.isoformat() if a.adopted_at else None,
                "control_count": a.adopted_controls.count(),
            }
            for a in adoptions
        ]
    })


@application_mgmt.route("/api/compliance/adoptions/<int:adoption_id>/controls", methods=["GET"])
@login_required
def list_adopted_controls(adoption_id):
    """List controls for an adopted framework, including evidence status."""
    org_id = getattr(g, "current_org_id", None)

    try:
        evidence_data = ApplicabilityService.evidence_status(org_id, adoption_id)
    except ValueError:
        return jsonify({"error": "Adoption not found"}), 404

    adoption = FrameworkAdoption.query.filter_by(
        id=adoption_id, organization_id=org_id
    ).first()

    return jsonify({
        "adoption_id": adoption.id,
        "framework_code": adoption.framework.code,
        "tailoring_notes": adoption.tailoring_notes,
        "controls": evidence_data,
    })


# ── Harmonisation ─────────────────────────────────────────────────────


@application_mgmt.route("/api/compliance/controls/<int:control_id>/harmonize", methods=["POST"])
@login_required
@platform_admin_required
def propose_harmonization(control_id):
    """Propose a cross-framework harmonisation between two controls."""
    data = request.get_json()
    if not data or "target_control_id" not in data:
        return jsonify({"error": "target_control_id required"}), 400

    try:
        control = ApplicabilityService.propose_harmonization(
            control_id=control_id,
            target_control_id=data["target_control_id"],
            notes=data.get("notes"),
        )
        return jsonify({
            "success": True,
            "control_id": control.id,
            "harmonized_control_id": control.harmonized_control_id,
            "harmonization_status": control.harmonization_status,
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/compliance/controls/<int:control_id>/harmonize/confirm", methods=["POST"])
@login_required
@platform_admin_required
def confirm_harmonization(control_id):
    """Confirm a proposed harmonisation."""
    try:
        control = ApplicabilityService.confirm_harmonization(control_id)
        return jsonify({
            "success": True,
            "control_id": control.id,
            "harmonization_status": control.harmonization_status,
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


# ── Applicability scoping ─────────────────────────────────────────────


@application_mgmt.route("/api/compliance/frameworks/<int:framework_id>/scope", methods=["GET"])
@login_required
def get_framework_scope(framework_id):
    """List systems in scope for a framework within the current organisation."""
    org_id = getattr(g, "current_org_id", None)
    if org_id is None:
        return jsonify({"nodes": []})

    framework = RegulatoryFramework.query.get_or_404(framework_id)
    nodes = ApplicabilityService.get_in_scope_nodes(org_id, framework.code)

    return jsonify({
        "framework_id": framework.id,
        "framework_code": framework.code,
        "nodes": nodes,
    })


# ── Regulatory change ─────────────────────────────────────────────────


@application_mgmt.route("/api/compliance/regulatory-changes", methods=["POST"])
@login_required
@require_roles("admin", "architect", "business_architect", "security_architect")
def record_regulatory_change():
    """Record a regulatory amendment and compute affected elements."""
    org_id = getattr(g, "current_org_id", None)
    if org_id is None:
        return jsonify({"error": "No organisation context"}), 400

    data = request.get_json()
    if not data or "framework_id" not in data or "title" not in data:
        return jsonify({"error": "framework_id and title required"}), 400

    effective_date = None
    if data.get("effective_date"):
        try:
            effective_date = datetime.strptime(data["effective_date"], "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "effective_date must be YYYY-MM-DD"}), 400

    try:
        change = ApplicabilityService.record_regulatory_change(
            organization_id=org_id,
            framework_id=data["framework_id"],
            title=data["title"],
            description=data.get("description", ""),
            change_type=data.get("change_type", "amendment"),
            effective_date=effective_date,
            recorded_by_id=current_user.id,
        )
        affected_count = change.affected_items.count()
        return jsonify({
            "success": True,
            "change_id": change.id,
            "affected_elements": affected_count,
        })
    except Exception:
        db.session.rollback()
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/compliance/regulatory-changes/<int:change_id>/affected", methods=["GET"])
@login_required
def get_affected_elements(change_id):
    """List elements affected by a regulatory change."""
    org_id = getattr(g, "current_org_id", None)
    if org_id is None:
        return jsonify({"affected": []})

    change = RegulatoryChange.query.filter_by(
        id=change_id, organization_id=org_id
    ).first_or_404()

    affected = change.affected_items.all()
    return jsonify({
        "change_id": change.id,
        "change_title": change.title,
        "affected": [
            {
                "id": a.id,
                "element_type": a.element_type,
                "element_id": a.element_id,
                "element_name": a.element_name,
                "impact_assessment": a.impact_assessment,
                "owner_id": a.owner_id,
            }
            for a in affected
        ],
    })


@application_mgmt.route("/api/compliance/regulatory-changes", methods=["GET"])
@login_required
def list_regulatory_changes():
    """List regulatory changes for the current organisation."""
    org_id = getattr(g, "current_org_id", None)
    if org_id is None:
        return jsonify({"changes": []})

    changes = RegulatoryChange.query.filter_by(
        organization_id=org_id
    ).order_by(RegulatoryChange.created_at.desc()).all()

    return jsonify({
        "changes": [
            {
                "id": c.id,
                "framework_id": c.framework_id,
                "framework_code": c.framework.code if c.framework else None,
                "change_type": c.change_type,
                "title": c.title,
                "description": c.description,
                "effective_date": c.effective_date.isoformat() if c.effective_date else None,
                "affected_count": c.affected_items.count(),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in changes
        ],
    })