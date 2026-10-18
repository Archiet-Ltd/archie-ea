"""
ACM Hybrid API Routes

REST API endpoints for ACM Technical Capability management using hybrid approach.
Provides comprehensive CRUD operations with validation and performance optimization.
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required

from app import db
from app.decorators import audit_log
from app.middleware.tenant_decorators import platform_admin_required
from app.models.technical_capability import ACMDomain
from app.services.acm_hybrid_manager import ACMHybridManager
from app.utils.pagination import safe_int_arg

# Create blueprint
acm_hybrid_bp = Blueprint("acm_hybrid", __name__, url_prefix="/api/acm-hybrid")


@acm_hybrid_bp.route("/seed", methods=["POST"])
@login_required
@platform_admin_required
@audit_log("acm_seed_capabilities")
def seed_capabilities():
    """
    Seed ACM capabilities using hybrid approach.

    Request Body:
    {
        "include_platform_specifics": true,
        "force_reseed": false
    }

    Response:
    {
        "success": true,
        "stage": "completed",
        "capabilities": {...},
        "performance": {...},
        "results": {...}
    }
    """
    try:
        data = request.get_json() or {}

        include_platform_specifics = data.get("include_platform_specifics", True)
        force_reseed = data.get("force_reseed", False)

        # Check existing capabilities if not forcing reseed
        if not force_reseed:
            existing_count = db.session.execute(
                db.text("SELECT COUNT(*) FROM technical_capabilities")
            ).scalar()

            if existing_count > 0:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f"Database already contains {existing_count} capabilities. Use force_reseed=true to overwrite.",
                        }
                    ),
                    400,
                )

        # Run seeding
        result = ACMHybridManager.seed_acm_capabilities(
            include_platform_specifics=include_platform_specifics
        )

        if result["success"]:
            return jsonify(result), 200
        else:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Seeding failed at stage: {result['stage']}",
                        "details": result,
                    }
                ),
                400,
            )

    except Exception:
        return jsonify({"success": False, "error": "Seeding operation failed"}), 500


@acm_hybrid_bp.route("/status", methods=["GET"])
@login_required
def get_seeding_status():
    """
    Get current seeding status and statistics.

    Response:
    {
        "success": true,
        "total_capabilities": 150,
        "platform_specific_count": 25,
        "domain_breakdown": {...},
        "last_updated": "2026 - 01 - 22T01:51:00"
    }
    """
    try:
        status = ACMHybridManager.get_seeding_status()

        if status["success"]:
            return jsonify(status), 200
        else:
            return jsonify({"success": False, "error": status["error"]}), 500

    except Exception:
        return jsonify({"success": False, "error": "Status check failed"}), 500


@acm_hybrid_bp.route("/domains", methods=["GET"])
@login_required
def get_domains():
    """
    Get all ACM domains with descriptions.

    Response:
    {
        "success": true,
        "domains": [
            {
                "code": "USER-EXPERIENCE",
                "name": "User Experience",
                "description": "Frontend interfaces, UI/UX design, accessibility..."
            }
        ]
    }
    """
    try:
        domains = []
        for domain in ACMDomain.ALL_DOMAINS:
            domains.append(
                {
                    "code": domain,
                    "name": domain.replace("-", " ").title(),
                    "description": ACMDomain.DOMAIN_DESCRIPTIONS.get(domain, ""),
                }
            )

        return jsonify({"success": True, "domains": domains}), 200

    except Exception:
        return jsonify({"success": False, "error": "Failed to get domains"}), 500


@acm_hybrid_bp.route("/capabilities", methods=["GET"])
@login_required
def get_capabilities():
    """
    Get capabilities with filtering and pagination.

    Query Parameters:
    - domain: Filter by ACM domain
    - level: Filter by level (L0 - L4)
    - platform_specific: Filter platform-specific capabilities
    - search: Search in name/description
    - page: Page number (default: 1)
    - per_page: Items per page (default: 50)

    Response:
    {
        "success": true,
        "capabilities": [...],
        "pagination": {
            "page": 1,
            "per_page": 50,
            "total": 150,
            "pages": 3
        }
    }
    """
    try:
        from app.models.technical_capability import TechnicalCapability

        # Get query parameters
        domain = request.args.get("domain")
        level = request.args.get("level")
        platform_specific = request.args.get("platform_specific")
        search = request.args.get("search")
        page = safe_int_arg('page', 1, minimum=1)
        per_page = min(safe_int_arg('per_page', 50, minimum=1, maximum=500), 100)  # Max 100 per page

        # Build query
        query = TechnicalCapability.query

        if domain:
            query = query.filter(TechnicalCapability.acm_domain == domain)

        if level:
            query = query.filter(TechnicalCapability.level == level)

        if platform_specific is not None:
            is_platform = platform_specific.lower() in ["true", "1", "yes"]
            query = query.filter(TechnicalCapability.platform_specific == is_platform)

        if search:
            search_term = f"%{search}%"
            query = query.filter(
                db.or_(
                    TechnicalCapability.name.ilike(search_term),
                    TechnicalCapability.description.ilike(search_term),
                    TechnicalCapability.code.ilike(search_term),
                )
            )

        # Get total count
        total = query.count()

        # Get paginated results
        capabilities = (
            query.order_by(
                TechnicalCapability.acm_domain,
                TechnicalCapability.level_number,
                TechnicalCapability.code,
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        # Convert to dict
        capabilities_data = [cap.to_dict() for cap in capabilities]

        return (
            jsonify(
                {
                    "success": True,
                    "capabilities": capabilities_data,
                    "pagination": {
                        "page": page,
                        "per_page": per_page,
                        "total": total,
                        "pages": (total + per_page - 1) // per_page,
                    },
                }
            ),
            200,
        )

    except Exception:
        return jsonify({"success": False, "error": "Failed to get capabilities"}), 500


@acm_hybrid_bp.route("/capabilities/<code>", methods=["GET"])
@login_required
def get_capability(code):
    """
    Get a specific capability by code.

    Response:
    {
        "success": true,
        "capability": {...}
    }
    """
    try:
        from app.models.technical_capability import TechnicalCapability

        capability = TechnicalCapability.query.filter_by(code=code).first()

        if not capability:
            return (
                jsonify({"success": False, "error": f"Capability with code '{code}' not found"}),
                404,
            )

        return jsonify({"success": True, "capability": capability.to_dict()}), 200

    except Exception:
        return jsonify({"success": False, "error": "Failed to get capability"}), 500


@acm_hybrid_bp.route("/capabilities/<code>", methods=["PUT"])
@login_required
@platform_admin_required
@audit_log("acm_update_capability")
def update_capability(code):
    """
    Update a specific capability.

    Request Body:
    {
        "name": "Updated Name",
        "description": "Updated description",
        "implementation_status": "complete"
    }

    Response:
    {
        "success": true,
        "capability": {...},
        "updated_fields": ["name", "description"]
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "error": "No update data provided"}), 400

        result = ACMHybridManager.update_capability(code, data)

        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify({"success": False, "error": result["error"]}), 400

    except Exception:
        return jsonify({"success": False, "error": "Failed to update capability"}), 500


@acm_hybrid_bp.route("/capabilities/<code>", methods=["DELETE"])
@login_required
@platform_admin_required
@audit_log("acm_delete_capability")
def delete_capability(code):
    """
    Delete a specific capability.

    Cleans up all NO-ACTION FK child references before deleting the row so
    that the operation never silently fails due to a FK violation.

    Tables with NO-ACTION FK on technical_capabilities.id:
      - technical_capabilities.parent_id  (self-referential children)

    All other FKs on technical_capabilities are CASCADE and are handled
    automatically by the database.

    Response:
    {
        "success": true,
        "deleted_capability": {...}
    }
    """
    try:
        from app.models.technical_capability import TechnicalCapability

        capability = TechnicalCapability.query.filter_by(code=code).first()
        if not capability:
            return (
                jsonify({"success": False, "error": f"Capability with code '{code}' not found"}),
                404,
            )

        cap_dict = capability.to_dict()
        cap_id = capability.id

        sp = db.session.begin_nested()
        try:
            # ------------------------------------------------------------------
            # 1. Self-referential children: re-parent them to this capability's
            #    parent so the hierarchy is preserved where possible, or NULL
            #    the parent_id so they become roots.
            # ------------------------------------------------------------------
            db.session.execute(
                db.text(
                    "UPDATE technical_capabilities"
                    " SET parent_id = :parent"
                    " WHERE parent_id = :id"
                ),
                {"parent": capability.parent_id, "id": cap_id},
            )

            # ------------------------------------------------------------------
            # 2. Delete the capability row itself.  All remaining FK children
            #    (application_technical_capability_mapping,
            #     technical_capability_apqc_mapping,
            #     technical_capability_business_mapping,
            #     technical_capability_unified_mapping,
            #     technical_capability_vendor_mapping,
            #     technical_capability_vendor_mappings)
            #    are CASCADE and will be removed automatically.
            # ------------------------------------------------------------------
            db.session.execute(
                db.text("DELETE FROM technical_capabilities WHERE id = :id"),
                {"id": cap_id},
            )

            sp.commit()
        except Exception:
            sp.rollback()
            raise

        db.session.commit()

        return jsonify({"success": True, "deleted_capability": cap_dict}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": f"Failed to delete capability: {str(e)}"}), 500


@acm_hybrid_bp.route("/validate", methods=["POST"])
@login_required
@audit_log("acm_validate_capabilities")
def validate_capabilities():
    """
    Validate capability data without inserting.

    Request Body:
    {
        "capabilities": [
            {
                "name": "Test Capability",
                "code": "TEST - 01 - 01",
                "acm_domain": "USER-EXPERIENCE",
                "level": "L1",
                "level_number": 1
            }
        ]
    }

    Response:
    {
        "success": true,
        "validation": {
            "valid": true,
            "errors": [],
            "warnings": [],
            "total_checked": 1
        }
    }
    """
    try:
        data = request.get_json()

        if not data or "capabilities" not in data:
            return (
                jsonify({"success": False, "error": "No capabilities provided for validation"}),
                400,
            )

        capabilities = data["capabilities"]

        # Validate using ACM validator
        from app.services.acm_hybrid_manager import ACMValidator

        validation_result = ACMValidator.validate_all_capabilities(capabilities)

        return jsonify({"success": True, "validation": validation_result}), 200

    except Exception:
        return jsonify({"success": False, "error": "Validation failed"}), 500


@acm_hybrid_bp.route("/platform-specifics", methods=["GET"])
@login_required
def get_platform_specific_capabilities():
    """
    Get platform-specific capabilities only.

    Response:
    {
        "success": true,
        "capabilities": [...],
        "total": 25
    }
    """
    try:
        platform_caps = ACMHybridManager._get_platform_specific_capabilities()

        return (
            jsonify({"success": True, "capabilities": platform_caps, "total": len(platform_caps)}),
            200,
        )

    except Exception as e:
        return (
            jsonify(
                {
                    "success": False,
                    "error": f"Failed to get platform-specific capabilities: {str(e)}",
                }
            ),
            500,
        )


@acm_hybrid_bp.route("/statistics", methods=["GET"])
@login_required
def get_statistics():
    """
    Get comprehensive statistics about ACM capabilities.

    Response:
    {
        "success": true,
        "statistics": {
            "total_capabilities": 150,
            "domain_distribution": {...},
            "level_distribution": {...},
            "platform_specific_count": 25,
            "implementation_status_breakdown": {...}
        }
    }
    """
    try:
        from app.models.technical_capability import TechnicalCapability

        # Get total counts
        total_count = TechnicalCapability.query.count()
        platform_count = TechnicalCapability.query.filter_by(platform_specific=True).count()

        # Domain distribution — single GROUP BY instead of one COUNT per domain
        from sqlalchemy import func as _func
        domain_rows = (
            db.session.query(TechnicalCapability.acm_domain, _func.count(TechnicalCapability.id))
            .group_by(TechnicalCapability.acm_domain)
            .all()
        )
        domain_stats = {domain: 0 for domain in ACMDomain.ALL_DOMAINS}
        for domain_val, cnt in domain_rows:
            if domain_val in domain_stats:
                domain_stats[domain_val] = cnt

        # Level distribution — single GROUP BY instead of one COUNT per level
        level_rows = (
            db.session.query(TechnicalCapability.level, _func.count(TechnicalCapability.id))
            .group_by(TechnicalCapability.level)
            .all()
        )
        level_stats = {lvl: 0 for lvl in ["L0", "L1", "L2", "L3", "L4"]}
        for level_val, cnt in level_rows:
            if level_val in level_stats:
                level_stats[level_val] = cnt

        # Implementation status breakdown (for platform-specific)
        impl_status_stats = {}
        if platform_count > 0:
            _impl_sql = (
                "SELECT implementation_status, COUNT(*) as count "
                "FROM technical_capabilities "
                "WHERE platform_specific = true"
            )
            _impl_sql += " GROUP BY implementation_status"
            impl_results = db.session.execute(db.text(_impl_sql)).fetchall()

            for status, count in impl_results:
                impl_status_stats[status] = count

        return (
            jsonify(
                {
                    "success": True,
                    "statistics": {
                        "total_capabilities": total_count,
                        "platform_specific_count": platform_count,
                        "domain_distribution": domain_stats,
                        "level_distribution": level_stats,
                        "implementation_status_breakdown": impl_status_stats,
                    },
                }
            ),
            200,
        )

    except Exception:
        return jsonify({"success": False, "error": "Failed to get statistics"}), 500


@acm_hybrid_bp.route("/hierarchy", methods=["GET"])
@login_required
def get_hierarchy():
    """
    Get hierarchical structure of capabilities.

    Query Parameters:
    - domain: Filter by domain (optional)

    Response:
    {
        "success": true,
        "hierarchy": [
            {
                "id": 1,
                "name": "USER-EXPERIENCE",
                "code": "UX",
                "level": "L0",
                "children": [...]
            }
        ]
    }
    """
    try:
        from app.models.technical_capability import TechnicalCapability

        domain = request.args.get("domain")

        # Get root capabilities (L0 or capabilities without parent)
        query = TechnicalCapability.query.filter_by(parent_id=None)
        if domain:
            query = query.filter_by(acm_domain=domain)

        roots = query.order_by(TechnicalCapability.acm_domain, TechnicalCapability.code).all()

        def build_hierarchy(capability):
            """Recursively build capability hierarchy."""
            children = TechnicalCapability.query.filter_by(parent_id=capability.id).all()

            cap_data = capability.to_dict()
            cap_data["children"] = [build_hierarchy(child) for child in children]

            return cap_data

        hierarchy = [build_hierarchy(root) for root in roots]

        return jsonify({"success": True, "hierarchy": hierarchy}), 200

    except Exception:
        return jsonify({"success": False, "error": "Failed to get hierarchy"}), 500


# Error handlers
@acm_hybrid_bp.errorhandler(400)
def bad_request(error):
    return jsonify({"success": False, "error": "Bad request", "message": str(error)}), 400


@acm_hybrid_bp.errorhandler(404)
def not_found(error):
    return jsonify({"success": False, "error": "Resource not found", "message": str(error)}), 404


@acm_hybrid_bp.errorhandler(500)
def internal_error(error):
    return jsonify({"success": False, "error": "Internal server error", "message": str(error)}), 500
