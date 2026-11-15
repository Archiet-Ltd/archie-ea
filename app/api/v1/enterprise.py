"""
API v1 Enterprise Endpoints

Standardized enterprise architecture API endpoints following PRD - 003.
"""

import logging

from flask import Blueprint
from flask_login import login_required


from app import db
from app.services.application_kind import is_application_expression
from app.utils.api_response import error_response, success_response

logger = logging.getLogger(__name__)

enterprise_bp = Blueprint("enterprise_v1", __name__)


@enterprise_bp.route("/canvas", methods=["GET"])
@login_required
def get_enterprise_canvas():
    """
    Get enterprise architecture canvas
    ---
    tags:
      - Enterprise
    summary: Get enterprise canvas
    description: Returns the enterprise architecture canvas data
    responses:
      200:
        description: Enterprise canvas data
    """
    try:
        from app.models.business_capabilities import BusinessCapability
        from app.models.application_portfolio import ApplicationComponent
        from app.models.strategic import StrategicInitiative

        capabilities = (
            BusinessCapability.query
            .filter(BusinessCapability.level == 1)
            .order_by(BusinessCapability.name)
            .limit(50)
            .all()
        )

        applications = (
            ApplicationComponent.query
            .order_by(ApplicationComponent.name)
            .limit(50)
            .all()
        )

        initiatives = (
            StrategicInitiative.query
            .order_by(StrategicInitiative.id.desc())
            .limit(20)
            .all()
        )

        canvas_data = {
            "business_capabilities": [
                {"id": c.id, "name": c.name, "domain": c.business_domain}
                for c in capabilities
            ],
            "application_portfolio": [
                {"id": a.id, "name": a.name, "type": a.application_type}
                for a in applications
            ],
            "technology_landscape": [],
            "strategic_initiatives": [
                {"id": i.id, "name": i.name}
                for i in initiatives
            ],
            "governance_structure": [],
        }

        return success_response(canvas_data)

    except Exception as e:
        logger.error("Failed to retrieve enterprise canvas: %s", e)
        return error_response(
            message="Failed to retrieve enterprise canvas",
            code="CANVAS_RETRIEVAL_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@enterprise_bp.route("/capabilities", methods=["GET"])
@login_required
def get_enterprise_capabilities():
    """
    Get enterprise capabilities overview
    ---
    tags:
      - Enterprise
    summary: Get enterprise capabilities
    description: Returns an overview of enterprise capabilities
    responses:
      200:
        description: Enterprise capabilities data
    """
    try:
        from app.models.business_capabilities import BusinessCapability
        from sqlalchemy import func

        total = db.session.query(func.count(BusinessCapability.id)).scalar() or 0

        by_domain_rows = (
            db.session.query(
                BusinessCapability.business_domain,
                func.count(BusinessCapability.id),
            )
            .group_by(BusinessCapability.business_domain)
            .all()
        )
        by_domain = {
            (domain or "unclassified"): count for domain, count in by_domain_rows
        }

        by_level_rows = (
            db.session.query(
                BusinessCapability.level,
                func.count(BusinessCapability.id),
            )
            .group_by(BusinessCapability.level)
            .all()
        )
        by_level = {str(level): count for level, count in by_level_rows}

        avg_maturity = (
            db.session.query(func.avg(BusinessCapability.current_maturity_level))
            .scalar()
        )

        capabilities_data = {
            "total_capabilities": total,
            "by_domain": by_domain,
            "by_level": by_level,
            "coverage_metrics": {
                "average_maturity": round(float(avg_maturity), 2) if avg_maturity else 0,
            },
            "gap_analysis": {},
        }

        return success_response(capabilities_data)

    except Exception as e:
        logger.error("Failed to retrieve enterprise capabilities: %s", e)
        return error_response(
            message="Failed to retrieve enterprise capabilities",
            code="ENTERPRISE_CAPABILITIES_RETRIEVAL_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )


@enterprise_bp.route("/metrics", methods=["GET"])
@login_required
def get_enterprise_metrics():
    """
    Get enterprise architecture metrics
    ---
    tags:
      - Enterprise
    summary: Get enterprise metrics
    description: Returns key enterprise architecture metrics and KPIs
    responses:
      200:
        description: Enterprise metrics data
    """
    try:
        from app.models.application_portfolio import ApplicationComponent
        from app.models.business_capabilities import BusinessCapability
        from sqlalchemy import func

        app_count = (
            db.session.query(func.count(ApplicationComponent.id)).filter(is_application_expression(ApplicationComponent)).scalar() or 0
        )

        cap_count = (
            db.session.query(func.count(BusinessCapability.id)).scalar() or 0
        )

        avg_maturity = (
            db.session.query(func.avg(BusinessCapability.current_maturity_level))
            .scalar()
        )

        metrics_data = {
            "application_count": app_count,
            "capability_count": cap_count,
            "architecture_maturity": round(float(avg_maturity), 2) if avg_maturity else 0,
            "capability_coverage": 0,
            "technical_debt_score": 0,
            "governance_compliance": 0,
            "strategic_alignment": 0,
            "data_status": "capability_coverage, technical_debt_score, governance_compliance, "
                           "and strategic_alignment require dedicated scoring pipelines",
        }

        return success_response(metrics_data)

    except Exception as e:
        logger.error("Failed to retrieve enterprise metrics: %s", e)
        return error_response(
            message="Failed to retrieve enterprise metrics",
            code="METRICS_RETRIEVAL_ERROR",
            details={"error": "See server logs for details"},
            status_code=500,
        )
