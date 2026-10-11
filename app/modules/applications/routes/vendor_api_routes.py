"""Vendor matching, analysis, process mapping, and dashboard API routes."""

from werkzeug.exceptions import HTTPException
import io
import logging
import time
from datetime import datetime

from flask import current_app, g, jsonify, redirect, request, send_file, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import text
from sqlalchemy.orm import joinedload

from app import db
from app.decorators import audit_log
from app.models.application_portfolio import ApplicationComponent
from app.utils.import_rate_limiter import add_rate_limit_headers

from . import unified_applications_bp
from ._helpers import calculate_match_confidence, get_matching_reason

logger = logging.getLogger(__name__)


@unified_applications_bp.route("/api/applications/match-vendors", methods=["POST"])
@login_required
@audit_log("vendor_match_run")
def match_applications_to_vendors():
    """
    Match applications to vendor products using various matching methods.

    Expected JSON payload:
    {
        "method": "name" | "capability" | "ai"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        method = data.get("method")
        if not method:
            return jsonify({"error": "Matching method is required"}), 400

        if method not in ["name", "capability", "ai"]:
            return jsonify({"error": "Invalid matching method"}), 400

        # Import required models
        from app.models.vendor.vendor_organization import (
            VendorOrganization,
            VendorProduct,
        )

        # Get applications (capped to prevent unbounded queries)
        applications = ApplicationComponent.query.limit(500).all()

        # Get all vendor products with eager loading to prevent N + 1 on vendor_organization access
        vendor_products = (
            db.session.query(VendorProduct)
            .options(joinedload(VendorProduct.vendor_organization))
            .join(VendorOrganization)
            .all()
        )

        matches = []

        for app in applications:
            best_match = None
            best_confidence = 0

            for vendor_product in vendor_products:
                confidence = calculate_match_confidence(app, vendor_product, method)

                if (
                    confidence > best_confidence and confidence >= 30
                ):  # Minimum confidence threshold
                    best_confidence = confidence
                    best_match = {
                        "application_id": app.id,
                        "application_name": app.name,
                        "application_description": app.description,
                        "vendor_id": vendor_product.vendor_organization_id,
                        "vendor_name": vendor_product.vendor_organization.name,
                        "product_id": vendor_product.id,
                        "product_name": vendor_product.name,
                        "confidence": confidence,
                        "matching_reason": get_matching_reason(
                            app, vendor_product, method
                        ),
                    }

            if best_match:
                matches.append(best_match)

        # Sort by confidence (highest first)
        matches.sort(key=lambda x: x["confidence"], reverse=True)

        return jsonify(
            {
                "success": True,
                "matches": matches,
                "total_applications": len(applications),
                "total_vendors": len(vendor_products),
                "method_used": method,
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error in vendor matching: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/api/applications/confirm-vendor-matches", methods=["POST"]
)
@login_required
@audit_log("vendor_match_confirm")
def confirm_vendor_matches():
    """
    Confirm vendor matches and create application-vendor relationships.

    Expected JSON payload:
    {
        "matches": [
            {
                "application_id": 1,
                "vendor_id": 1,
                "confidence": 85
            }
        ]
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        matches = data.get("matches", [])
        if not matches:
            return jsonify({"error": "No matches provided"}), 400

        # Import required models
        from app.models.vendor.vendor_organization import VendorProduct

        def _as_int_id(value):
            """Coerce a JSON match id to int, or None if it isn't one.

            request.get_json() ids may arrive as JSON strings ("5") rather
            than numbers. Passed straight into .in_() against an integer
            column, a string id reaches PostgreSQL as
            `id IN ('5'::VARCHAR)` under psycopg 3, which the database
            refuses to compare against an integer column (a 500) rather
            than coercing it. Treat a non-integer id as absent, the same as
            this route already does for a missing id.
            """
            if isinstance(value, bool) or value is None:
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        # Batch prefetch applications and vendor products
        _match_app_ids = [
            _id
            for _id in (_as_int_id(m.get("application_id")) for m in matches)
            if _id is not None
        ]
        _match_vendor_ids = [
            _id
            for _id in (_as_int_id(m.get("vendor_id")) for m in matches)
            if _id is not None
        ]
        _match_product_ids = [
            _id
            for _id in (_as_int_id(m.get("product_id")) for m in matches)
            if _id is not None
        ]

        _apps_by_id = {}
        if _match_app_ids:
            _apps_list = ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(_match_app_ids)
            ).all()
            _apps_by_id = {a.id: a for a in _apps_list}

        _vendor_products_by_org_id = {}
        _vendor_products_by_id = {}
        if _match_vendor_ids:
            _vp_list = VendorProduct.query.filter(
                VendorProduct.vendor_organization_id.in_(_match_vendor_ids)
            ).order_by(VendorProduct.id.asc()).all()
            for vp in _vp_list:
                _vendor_products_by_org_id.setdefault(vp.vendor_organization_id, vp)
                _vendor_products_by_id[vp.id] = vp
        if _match_product_ids:
            _vp_by_id_list = VendorProduct.query.filter(
                VendorProduct.id.in_(_match_product_ids)
            ).all()
            for vp in _vp_by_id_list:
                _vendor_products_by_id[vp.id] = vp

        # Prefetch existing vendor-product relationships from CORRECT table
        _existing_vendor_rels = set()
        if _match_app_ids:
            _rel_rows = db.session.execute(  # tenant-filtered: scoped via application_component_id FK
                text(
                    "SELECT application_component_id, vendor_product_id FROM application_component_vendor_products WHERE application_component_id IN :app_ids"
                ),
                {"app_ids": tuple(_match_app_ids) if _match_app_ids else (0,)},
            ).fetchall()
            _existing_vendor_rels = {(r[0], r[1]) for r in _rel_rows}

        confirmed_count = 0

        for match in matches:
            try:
                # Same edge-parse as the prefetch above -- keeps these ids
                # comparable (via ==, and as dict keys against the int-keyed
                # prefetch maps) to the integer ids SQLAlchemy returns.
                application_id = _as_int_id(match.get("application_id"))
                vendor_id = _as_int_id(match.get("vendor_id"))
                product_id = _as_int_id(match.get("product_id"))

                if not application_id or not vendor_id:
                    continue

                # Get the application and vendor product (using prefetched data)
                app = _apps_by_id.get(application_id)
                vendor_product = _vendor_products_by_id.get(product_id)
                if vendor_product is None:
                    vendor_product = _vendor_products_by_org_id.get(vendor_id)

                if not app or not vendor_product:
                    continue

                # Create application-vendor relationship in CORRECT table
                _rel_key = (application_id, vendor_product.id)
                if _rel_key not in _existing_vendor_rels:
                    db.session.execute(  # tenant-filtered: scoped via application_component_id FK
                        text(
                            "INSERT INTO application_component_vendor_products (application_component_id, vendor_product_id, deployment_type, criticality, relationship_type, created_at) VALUES (:app_id, :prod_id, :deploy, :crit, :rel_type, :created_at)"
                        ),
                        {
                            "app_id": application_id,
                            "prod_id": vendor_product.id,
                            "deploy": "production",
                            "crit": "business_critical",
                            "rel_type": "uses",
                            "created_at": datetime.utcnow(),
                        },
                    )
                    _existing_vendor_rels.add(_rel_key)
                    confirmed_count += 1

                # Also set vendor_product_id on the app (primary FK) if not set
                if app.vendor_product_id is None:
                    db.session.execute(
                        text(
                            "UPDATE application_components SET vendor_product_id = :prod_id "
                            "WHERE id = :app_id AND vendor_product_id IS NULL "
                            "AND organization_id = :org_id"  # defense-in-depth; app already org-scoped via prefetch
                        ),
                        {
                            "app_id": application_id,
                            "prod_id": vendor_product.id,
                            "org_id": getattr(g, "current_org_id", None),
                        },
                    )

            except HTTPException:

                raise

            except Exception as e:
                current_app.logger.error(
                    f"Error confirming match for application {match.get('application_id')}: {str(e)}"
                )
                continue

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "confirmed_count": confirmed_count,
                "message": f"Successfully confirmed {confirmed_count} vendor match(es)",
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error confirming vendor matches: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@unified_applications_bp.route("/architecture/<int:id>", strict_slashes=False)
@login_required
def application_architecture_detail(id):
    """
    Redirect to main application detail page with architecture tab.
    This route is maintained for backward compatibility.
    """
    return redirect(
        url_for("unified_applications.application_detail", id=id, tab="architecture")
    )


@unified_applications_bp.route(
    "/api/applications/vendors/api/analyze", methods=["POST"]
)
@login_required
def vendor_analyze_api():
    """API endpoint for vendor analysis."""
    try:
        from app.services.vendor_analysis_service import VendorAnalysisService

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        service = VendorAnalysisService()
        service.init_app(current_app._get_current_object())

        # Perform vendor analysis based on request data
        analysis_results = service.analyze_vendors(
            vendor_ids=data.get("vendor_ids", []),
            capability_ids=data.get("capability_ids", []),
            product_families=data.get("product_families", []),
            deployment_models=data.get("deployment_models", []),
            contract_statuses=data.get("contract_statuses", []),
            min_readiness_score=data.get("min_readiness_score"),
            technology_stack=data.get("technology_stack"),
        )

        return jsonify({"success": True, "analysis": analysis_results})

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Vendor analysis error: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/api/vendor-analysis/<int:analysis_id>/results", methods=["GET"]
)
@login_required
def api_get_vendor_results(analysis_id):
    """Get vendor analysis results."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis

        analysis = OptionsAnalysis.query.get_or_404(analysis_id)

        return jsonify(
            {
                "success": True,
                "analysis": {
                    "id": analysis.id,
                    "name": analysis.name,
                    "status": analysis.status,
                    "results": analysis.results,
                    "created_at": analysis.created_at.isoformat()
                    if analysis.created_at
                    else None,
                    "completed_at": analysis.completed_at.isoformat()
                    if analysis.completed_at
                    else None,
                },
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting vendor analysis results: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/api/vendor-analysis/<int:analysis_id>/export", methods=["GET"]
)
@login_required
def api_export_vendor_analysis(analysis_id):
    """Export vendor analysis in CSV, PDF, or PPT format."""
    try:
        from app.models.vendor_analysis import OptionsAnalysis
        from app.services.vendor_analysis.export_service import ExportService

        analysis = OptionsAnalysis.query.get_or_404(analysis_id)
        export_format = request.args.get("format", "csv")

        if export_format not in ["csv", "pdf", "ppt"]:
            return jsonify({"error": "Invalid export format"}), 400

        export_service = ExportService()
        file_data, filename, mime_type = export_service.export_analysis(
            analysis, export_format, current_user
        )

        return send_file(
            io.BytesIO(file_data),
            mimetype=mime_type,
            as_attachment=True,
            download_name=filename,
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error exporting vendor analysis: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@unified_applications_bp.route("/api/vendor-process-mapping/generate", methods=["POST"])
@login_required
@audit_log("vendor_process_mapping_generate")
def generate_vendor_process_mappings():
    """Generate vendor-process mappings with confidence scores."""
    try:
        data = request.get_json() or {}
        _vendor_id = data.get("vendor_id")
        confidence_threshold = data.get("confidence_threshold", 30)

        from app.services.vendor_process_mapping_service import (
            VendorProcessMappingService,
        )

        service = VendorProcessMappingService()
        mappings = service.generate_vendor_process_mappings(
            confidence_threshold=confidence_threshold
        )

        # Format response for UI
        response_data = {
            "mapped_products": len(mappings),
            "mapped_applications": len(set(m.get("application_id") for m in mappings)),
            "avg_confidence": sum(m.get("confidence", 0) for m in mappings)
            // len(mappings)
            if mappings
            else 0,
            "mappings": [],
        }

        for mapping in mappings:
            response_data["mappings"].append(
                {
                    "id": mapping.get(
                        "id", f"mapping_{len(response_data['mappings'])}"
                    ),
                    "product_name": mapping.get("product_name", "Unknown Product"),
                    "product_family": mapping.get("product_family", ""),
                    "application_name": mapping.get(
                        "application_name", "Unknown Application"
                    ),
                    "application_domain": mapping.get("application_domain", ""),
                    "confidence": mapping.get("confidence", 0),
                    "status": mapping.get("status", "potential"),
                }
            )

        # Add rate limit headers
        response = jsonify(response_data)
        add_rate_limit_headers(response)
        return response

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error generating vendor-process mappings: {e}")
        return jsonify(
            {
                "success": False,
                "error": "Failed to generate vendor-process mappings. Please try again.",
                "mapped_products": 0,
                "mapped_applications": 0,
                "avg_confidence": 0,
                "mappings": [],
            }
        ), 500


@unified_applications_bp.route("/api/vendor-process-mapping/save", methods=["POST"])
@login_required
@audit_log("vendor_process_mapping_save")
def save_vendor_process_mappings():
    """Save generated vendor-process mappings to database."""
    try:
        data = request.get_json() or {}
        mappings = data.get("mappings", [])

        if not mappings:
            return jsonify({"success": False, "error": "No mappings provided"}), 400

        from app.services.vendor_process_mapping_service import (
            VendorProcessMappingService,
        )

        service = VendorProcessMappingService()
        result = service.save_mappings_batch(
            mappings=mappings,
            validated_by=current_user.id if current_user.is_authenticated else None,
        )

        return jsonify(
            {
                "success": True,
                "saved_count": result.get("saved_count", 0),
                "skipped_count": result.get("skipped_count", 0),
                "error_count": result.get("error_count", 0),
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error saving vendor-process mappings: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route("/api/vendor-process-mapping/coverage", methods=["GET"])
@login_required
def get_vendor_process_coverage():
    """Get process coverage analysis by category."""
    try:
        from app.services.vendor_process_mapping_service import (
            VendorProcessMappingService,
        )

        service = VendorProcessMappingService()
        coverage = service.get_process_coverage_analysis()

        return jsonify({"success": True, "coverage_analysis": coverage})

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting process coverage: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/api/vendor-process-mapping/vendor-analysis", methods=["GET"]
)
@login_required
def get_vendor_capability_analysis():
    """Get vendor capability analysis across processes."""
    try:
        vendor_id = request.args.get("vendor_id")

        from app.services.vendor_process_mapping_service import (
            VendorProcessMappingService,
        )
        from app.models.vendor.vendor_organization import VendorOrganization

        service = VendorProcessMappingService()
        analysis = service.get_vendor_capability_analysis()

        if vendor_id:
            vendor = VendorOrganization.query.get(vendor_id)
            vendor_name = vendor.name if vendor else None
            analysis = (
                {vendor_name: analysis.get(vendor_name, {})}
                if vendor_name
                else {}
            )

        return jsonify(analysis)

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting vendor analysis: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route("/dashboard/api/vendor-organizations", methods=["GET"])
@login_required
def get_vendor_organizations():
    """Get all vendor organizations for analysis."""
    try:
        from app.models.vendor.vendor_organization import VendorOrganization

        vendors = VendorOrganization.query.limit(500).all()
        vendor_list = []

        for vendor in vendors:
            vendor_list.append(
                {
                    "id": vendor.id,
                    "name": vendor.name,
                    "vendor_type": vendor.vendor_type,
                    "status": vendor.status,
                    "enterprise_readiness_score": vendor.enterprise_readiness_score,
                }
            )

        return jsonify(vendor_list)

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting vendor organizations: {e}")
        return jsonify([]), 500


@unified_applications_bp.route("/dashboard/api/capabilities", methods=["GET"])
@login_required
def get_capabilities():
    """Get all capabilities for analysis."""
    try:
        from app.models.unified_capability import UnifiedCapability

        capabilities = UnifiedCapability.query.limit(500).all()
        capability_list = []

        for capability in capabilities:
            capability_list.append(
                {
                    "id": capability.id,
                    "name": capability.name,
                    "description": getattr(capability, "description", None),
                    "level": getattr(capability, "level", None),
                }
            )

        return jsonify(capability_list)

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error getting capabilities: {e}")
        return jsonify([]), 500


@unified_applications_bp.route("/dashboard/api/vendor-analysis", methods=["POST"])
@login_required
@audit_log("vendor_analysis_create")
def create_vendor_analysis():
    """Create a new vendor analysis."""
    try:
        data = request.get_json()

        # Create analysis record
        analysis_id = f"analysis_{int(time.time())}"

        # Store in session or database (simplified for demo)
        session[f"vendor_analysis_{analysis_id}"] = {
            "id": analysis_id,
            "name": data.get("name", "Untitled Analysis"),
            "created_at": datetime.utcnow().isoformat(),
            "vendors": data.get("vendors", []),
            "capabilities": data.get("capabilities", []),
            "criteria_weights": data.get("criteria_weights", {}),
        }

        return jsonify(
            {
                "success": True,
                "analysis_id": analysis_id,
                "message": "Analysis created successfully",
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error creating vendor analysis: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route("/api/vendor-process-mapping/confirm", methods=["POST"])
@login_required
@audit_log("vendor_process_mapping_confirm")
def confirm_vendor_mapping():
    """Confirm a vendor-product mapping by setting the validated_by user."""
    try:
        data = request.get_json()
        mapping_id = data.get("mapping_id")

        if not mapping_id:
            return jsonify({"success": False, "error": "mapping_id is required"}), 400

        from app.models.process_data import VendorProcessMapping

        mapping = VendorProcessMapping.query.get(mapping_id)
        if not mapping:
            return jsonify(
                {"success": False, "error": f"Mapping {mapping_id} not found"}
            ), 404

        mapping.validated_by_id = (
            current_user.id if current_user.is_authenticated else None
        )
        mapping.updated_at = datetime.utcnow()
        db.session.commit()

        return jsonify(
            {"success": True, "message": f"Mapping {mapping_id} confirmed successfully"}
        )

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error confirming mapping: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/api/v1/applications/<string:id>/architectural-analysis", methods=["GET"]
)
@login_required
def get_architectural_analysis(id):
    """
    Get comprehensive architectural analysis for an application.
    Provides strategic insights, dependency mapping, integration complexity,
    and architectural recommendations for solution and software architects.
    """
    try:
        from app.models.unified_application_capability_mapping import (
            UnifiedApplicationCapabilityMapping,
        )
        from app.models.unified_capability import UnifiedCapability
        from app.models.vendor.vendor_organization import (
            VendorProduct,
            application_vendor_products,
        )

        # Parse at the edge, before any query. The route captures id as a
        # string; under psycopg 3, comparing that string directly against
        # application_components.id (an integer column) reaches PostgreSQL as
        # `id = '999999'::VARCHAR`, which the database refuses to compare
        # against an integer column rather than coercing it, turning a bad or
        # merely non-existent id into a 500 instead of a clean 404. A
        # non-integer id names no application that could exist, so it gets
        # the same 404 as a well-formed id that isn't found.
        try:
            application_id = int(id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Application not found"}), 404

        app = ApplicationComponent.query.filter_by(id=application_id).first()
        if not app:
            return jsonify({"success": False, "error": "Application not found"}), 404

        # Get capability mappings with enhanced data
        capability_mappings = (
            db.session.query(UnifiedApplicationCapabilityMapping, UnifiedCapability)
            .join(
                UnifiedCapability,
                UnifiedApplicationCapabilityMapping.unified_capability_id
                == UnifiedCapability.id,
            )
            .filter(
                UnifiedApplicationCapabilityMapping.application_component_id
                == application_id
            )
            .all()
        )

        # Calculate strategic alignment score (0 - 100)
        strategic_alignment_score = 0
        if capability_mappings:
            strategic_count = sum(1 for m, c in capability_mappings if m.is_strategic)
            primary_count = sum(
                1 for m, c in capability_mappings if m.support_level == "primary"
            )
            high_maturity = sum(
                1
                for m, c in capability_mappings
                if m.maturity_level and m.maturity_level >= 4
            )

            strategic_alignment_score = min(
                100,
                int(
                    (strategic_count / len(capability_mappings) * 40)
                    + (primary_count / len(capability_mappings) * 35)
                    + (high_maturity / len(capability_mappings) * 25)
                ),
            )

        # Calculate architecture maturity (1 - 5 scale)
        architecture_maturity = 1.0
        if capability_mappings:
            maturity_values = [
                m.maturity_level for m, c in capability_mappings if m.maturity_level
            ]
            if maturity_values:
                architecture_maturity = sum(maturity_values) / len(maturity_values)

        # Determine technical debt level
        technical_debt_level = "Low"
        if architecture_maturity < 2.5:
            technical_debt_level = "High"
        elif architecture_maturity < 3.5:
            technical_debt_level = "Medium"

        # Calculate gap coverage
        total_capabilities = UnifiedCapability.query.count()
        mapped_capabilities = len(capability_mappings)
        gap_coverage_percentage = (
            int((mapped_capabilities / total_capabilities * 100))
            if total_capabilities > 0
            else 0
        )

        # Analyze capability dependencies
        dependencies = []
        for mapping, capability in capability_mappings:
            if mapping.is_strategic or mapping.support_level == "primary":
                dependencies.append(
                    {
                        "name": capability.name,
                        "type": mapping.support_level or "supporting",
                        "criticality": "high"
                        if mapping.is_strategic
                        else "medium"
                        if mapping.support_level == "primary"
                        else "low",
                        "impact": f"{mapping.coverage_percentage}% coverage"
                        if mapping.coverage_percentage
                        else "Coverage TBD",
                    }
                )

        # Analyze integration complexity
        integration_patterns = []
        vendor_products = []
        if app.archimate_element_id:
            vendor_products = (
                db.session.query(VendorProduct.id)
                .join(
                    application_vendor_products,
                    application_vendor_products.c.vendor_product_id == VendorProduct.id,
                )
                .filter(
                    application_vendor_products.c.archimate_element_id
                    == app.archimate_element_id
                )
                .distinct()
                .all()
            )

        if len(vendor_products) > 5:
            integration_patterns.append(
                {
                    "name": "High Vendor Diversity",
                    "description": f"{len(vendor_products)} vendor products integrated",
                    "complexity": min(100, len(vendor_products) * 10),
                }
            )

        if len(capability_mappings) > 10:
            integration_patterns.append(
                {
                    "name": "Broad Capability Coverage",
                    "description": f"Supports {len(capability_mappings)} business capabilities",
                    "complexity": min(100, len(capability_mappings) * 5),
                }
            )

        # Generate strategic recommendations
        recommendations = []

        if strategic_alignment_score < 50:
            recommendations.append(
                {
                    "priority": "high",
                    "title": "Improve Strategic Alignment",
                    "description": "Current strategic alignment is below target. Consider prioritizing capabilities that directly support business goals.",
                    "benefits": [
                        "Better ROI",
                        "Clearer business value",
                        "Improved stakeholder satisfaction",
                    ],
                }
            )

        if architecture_maturity < 3.0:
            recommendations.append(
                {
                    "priority": "high",
                    "title": "Increase Architecture Maturity",
                    "description": "Focus on maturing architectural practices and establishing patterns for repeatability.",
                    "benefits": [
                        "Reduced technical debt",
                        "Faster delivery",
                        "Better maintainability",
                    ],
                }
            )

        if gap_coverage_percentage < 30:
            recommendations.append(
                {
                    "priority": "medium",
                    "title": "Address Capability Gaps",
                    "description": f"Only {gap_coverage_percentage}% of enterprise capabilities are mapped. Complete capability mapping for better portfolio visibility.",
                    "benefits": [
                        "Complete portfolio view",
                        "Better gap analysis",
                        "Informed investment decisions",
                    ],
                }
            )

        if len(vendor_products) > 8:
            recommendations.append(
                {
                    "priority": "medium",
                    "title": "Consolidate Vendor Stack",
                    "description": "High number of vendor products may increase integration complexity and costs.",
                    "benefits": [
                        "Reduced licensing costs",
                        "Simpler integration",
                        "Lower maintenance burden",
                    ],
                }
            )

        return jsonify(
            {
                "success": True,
                "data": {
                    "strategic_alignment_score": strategic_alignment_score,
                    "architecture_maturity": round(architecture_maturity, 2),
                    "technical_debt_level": technical_debt_level,
                    "gap_coverage_percentage": gap_coverage_percentage,
                    "dependencies": dependencies[:10],  # Limit to top 10
                    "integration": {
                        "patterns": integration_patterns,
                        "total_vendors": len(vendor_products),
                        "total_capabilities": len(capability_mappings),
                    },
                    "recommendations": recommendations,
                },
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        current_app.logger.error(f"Error in architectural analysis: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500
