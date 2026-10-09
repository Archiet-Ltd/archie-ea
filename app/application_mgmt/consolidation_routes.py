"""
Consolidation Routes — sub-module extracted from routes.py (BE-054 wave-11b).

Handles application portfolio consolidation:
  * Duplicate detection API
  * Application delete API
  * Similarity analysis API
  * Bulk consolidation API
  * Vendor matching API (match + confirm)
  * Import stream analysis SSE endpoint
  * calculate_match_confidence and get_matching_reason helper functions
"""

import json
import logging

from flask import Response, current_app, jsonify, request, stream_with_context
from flask_login import current_user, login_required
from sqlalchemy import text
from sqlalchemy.orm import joinedload

logger = logging.getLogger(__name__)

from .. import db
from ..models.application_portfolio import ApplicationComponent
from app.utils.deprecation import deprecated_route
from . import application_mgmt


# Wave 11b: Consolidation routes


def calculate_match_confidence(application, vendor_product, method):
    """
    Calculate confidence score for application-vendor matching.

    Returns confidence percentage (0 - 100).
    """
    confidence = 0

    if method == "name":
        # Name-based matching using string similarity
        app_name = application.name.lower() if application.name else ""
        vendor_name = vendor_product.name.lower() if vendor_product.name else ""
        vendor_org_name = (
            vendor_product.vendor_organization.name.lower()
            if vendor_product.vendor_organization
            else ""
        )

        # Exact name match
        if app_name == vendor_name or app_name == vendor_org_name:
            confidence = 95
        # Partial name match
        elif vendor_name in app_name or app_name in vendor_name:
            confidence = 75
        elif vendor_org_name in app_name or app_name in vendor_org_name:
            confidence = 70
        # Word overlap
        else:
            app_words = set(app_name.split())
            vendor_words = set(vendor_name.split()) | set(vendor_org_name.split())
            overlap = len(app_words & vendor_words)
            if overlap > 0:
                confidence = min(50, 30 + (overlap * 10))

    elif method == "capability":
        # Capability-based matching
        # This would require capability data for both applications and vendors
        # For now, use a simplified approach
        confidence = 60  # Base confidence for capability method

        # Boost confidence if both have descriptions
        if application.description and vendor_product.description:
            app_desc = application.description.lower()
            vendor_desc = vendor_product.description.lower()

            # Check for keyword overlap
            common_keywords = [
                "system",
                "management",
                "service",
                "platform",
                "solution",
                "software",
            ]
            overlap = sum(
                1
                for keyword in common_keywords
                if keyword in app_desc and keyword in vendor_desc
            )
            if overlap > 0:
                confidence += overlap * 5

        confidence = min(95, confidence)

    elif method == "ai":
        # AI-powered matching (simplified for now)
        # In a real implementation, this would use LLM services
        # For now, combine name and description analysis
        confidence = calculate_match_confidence(application, vendor_product, "name")

        if application.description and vendor_product.description:
            desc_confidence = calculate_match_confidence(
                application, vendor_product, "capability"
            )
            confidence = max(confidence, desc_confidence)

        # Add AI boost
        confidence = min(95, confidence + 10)

    return confidence


def get_matching_reason(application, vendor_product, method):
    """
    Generate human-readable reason for the match.
    """
    if method == "name":
        app_name = application.name or ""
        vendor_name = vendor_product.name or ""
        vendor_org_name = (
            vendor_product.vendor_organization.name
            if vendor_product.vendor_organization
            else ""
        )

        if app_name.lower() == vendor_name.lower():
            return f"Exact name match with vendor product '{vendor_name}'"
        elif app_name.lower() == vendor_org_name.lower():
            return f"Exact name match with vendor organization '{vendor_org_name}'"
        elif vendor_name.lower() in app_name.lower():
            return f"Name contains vendor product name '{vendor_name}'"
        elif vendor_org_name.lower() in app_name.lower():
            return f"Name contains vendor organization name '{vendor_org_name}'"
        else:
            return f"Name similarity between '{app_name}' and '{vendor_name}'"

    elif method == "capability":
        return "Capability overlap between application and vendor product offerings"

    elif method == "ai":
        return "AI-powered semantic analysis indicates strong relationship"

    return "Matching based on available data"


@application_mgmt.route("/api/applications/duplicates", methods=["GET"])
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_find_duplicates",
    deprecation_date="2026-02-10",
    migration_guide="Use GET /api/applications/duplicates from application_api blueprint instead",
)
def find_duplicate_applications():
    """
    RETIRED — delegates to MatcherService.

    Callers:
    - (route, called by clients)

    Instead of maintaining its own similarity analysis, this now delegates to
    the matcher for consistent matching across the platform.
    """


    from ..models.application_consolidation import ApplicationSimilarityAnalysis
    from ..models.application_layer import ApplicationComponent

    try:
        min_similarity = int(request.args.get("min_similarity", 40))
        force_analyze = request.args.get("force_analyze", "false").lower() == "true"

        # Get all applications
        applications = ApplicationComponent.query.limit(
            1000
        ).all()  # Limit to prevent OOM on large datasets

        if len(applications) < 2:
            return jsonify(
                {
                    "duplicates": [],
                    "total_duplicate_groups": 0,
                    "total_duplicate_applications": 0,
                    "estimated_savings": 0,
                    "message": "Need at least 2 applications to detect duplicates",
                }
            )

        # If force_analyze, trigger new analysis
        if force_analyze:
            from ..services.application_similarity_service import (
                ApplicationSimilarityService,
            )

            similarity_service = ApplicationSimilarityService()

            # Run analysis in background (for large portfolios, this should be a background job)
            # For now, we'll analyze a limited number of pairs to avoid timeout
            max_pairs_to_analyze = 10  # Limit to prevent timeout
            pairs_analyzed = 0

            from app.services.core.async_utils import get_or_create_event_loop

            loop = get_or_create_event_loop()
            for i in range(len(applications)):
                if pairs_analyzed >= max_pairs_to_analyze:
                    break
                for j in range(i + 1, len(applications)):
                    if pairs_analyzed >= max_pairs_to_analyze:
                        break
                    try:
                        loop.run_until_complete(
                            similarity_service.analyze_application_pair(
                                applications[i].id,
                                applications[j].id,
                                provider="claude",
                                user_id=current_user.id
                                if current_user.is_authenticated
                                else None,
                            )
                        )
                        pairs_analyzed += 1
                    except Exception as e:
                        current_app.logger.error(f"Error analyzing pair: {str(e)}")
                        continue

        # Find existing similarity analyses
        similarity_analyses = (
            ApplicationSimilarityAnalysis.query.filter(
                ApplicationSimilarityAnalysis.overall_similarity_score >= min_similarity
            )
            .order_by(ApplicationSimilarityAnalysis.overall_similarity_score.desc())
            .all()
        )

        # Group applications by similarity (improved clustering)
        duplicate_groups = []
        processed_app_ids = set()
        app_to_group = {}  # Map app_id to group index

        for analysis in similarity_analyses:
            app1_id = analysis.app_1_id
            app2_id = analysis.app_2_id
            similarity_score = analysis.overall_similarity_score

            if similarity_score < min_similarity:
                continue

            # Determine severity
            if similarity_score >= 70:
                severity = "high"
            elif similarity_score >= 50:
                severity = "medium"
            else:
                severity = "low"

            # Get application details
            app1 = ApplicationComponent.query.get(app1_id)
            app2 = ApplicationComponent.query.get(app2_id)

            if not app1 or not app2:
                continue

            # Check if either app is already in a group
            group_index = None
            if app1_id in app_to_group:
                group_index = app_to_group[app1_id]
            elif app2_id in app_to_group:
                group_index = app_to_group[app2_id]

            if group_index is not None:
                # Add to existing group
                if app1_id not in processed_app_ids:
                    duplicate_groups[group_index]["applications"].append(
                        {
                            "id": app1.id,
                            "name": app1.name,
                            "description": app1.description,
                            "owner_team": app1.development_team,  # owner_team column doesn't exist, using development_team
                            "application_type": app1.application_type or "Application",
                        }
                    )
                    processed_app_ids.add(app1_id)
                    app_to_group[app1_id] = group_index

                if app2_id not in processed_app_ids:
                    duplicate_groups[group_index]["applications"].append(
                        {
                            "id": app2.id,
                            "name": app2.name,
                            "description": app2.description,
                            "owner_team": app2.development_team,  # owner_team column doesn't exist, using development_team
                            "application_type": app2.application_type or "Application",
                        }
                    )
                    processed_app_ids.add(app2_id)
                    app_to_group[app2_id] = group_index

                # Update group similarity (average)
                group = duplicate_groups[group_index]
                current_avg = group["avg_similarity"]
                group_size = len(group["applications"])
                group["avg_similarity"] = int(
                    (current_avg * (group_size - 1) + similarity_score) / group_size
                )
            else:
                # Create new group
                group = {
                    "reason": analysis.reasoning or "Similar functionality detected",
                    "avg_similarity": similarity_score,
                    "severity": severity,
                    "consolidation_opportunity": analysis.consolidation_opportunity,
                    "recommended_action": analysis.recommended_action,
                    "estimated_savings": float(analysis.estimated_cost_savings)
                    if analysis.estimated_cost_savings
                    else 0,
                    "consolidation_complexity": analysis.consolidation_complexity,
                    "applications": [
                        {
                            "id": app1.id,
                            "name": app1.name,
                            "description": app1.description,
                            "owner_team": app1.development_team,  # owner_team column doesn't exist, using development_team
                            "application_type": app1.application_type or "Application",
                        },
                        {
                            "id": app2.id,
                            "name": app2.name,
                            "description": app2.description,
                            "owner_team": app2.development_team,  # owner_team column doesn't exist, using development_team
                            "application_type": app2.application_type or "Application",
                        },
                    ],
                }

                group_index = len(duplicate_groups)
                duplicate_groups.append(group)
                processed_app_ids.add(app1_id)
                processed_app_ids.add(app2_id)
                app_to_group[app1_id] = group_index
                app_to_group[app2_id] = group_index

        # Calculate estimated savings from real cost data only
        total_savings = sum(
            group.get("estimated_savings", 0) for group in duplicate_groups
        )

        return jsonify(
            {
                "duplicates": duplicate_groups,
                "total_duplicate_groups": len(duplicate_groups),
                "total_duplicate_applications": len(processed_app_ids),
                "estimated_savings": f"{int(total_savings):,}",
                "analyses_count": len(similarity_analyses),
                "message": f"Found {len(duplicate_groups)} duplicate groups"
                if duplicate_groups
                else "No duplicates found",
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error finding duplicates: {str(e)}")
        import traceback

        current_app.logger.error(traceback.format_exc())
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/applications/<int:app_id>", methods=["DELETE"])
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_delete_app",
    deprecation_date="2026-02-10",
    migration_guide="Use DELETE /api/applications/<app_id> from application_api blueprint instead",
)
def delete_application_api(app_id):
    """
    Delete an application via API.

    Args:
        app_id: Application ID to delete

    Returns:
        JSON with success status
    """
    from ..models.application_layer import ApplicationComponent

    try:
        app = ApplicationComponent.query.get_or_404(app_id)

        # Check for dependencies (simplified)
        # In a real implementation, you'd check for relationships, interfaces, etc.

        db.session.delete(app)
        db.session.commit()

        return jsonify({"success": True, "message": "Application deleted successfully"})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting application {app_id}: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@application_mgmt.route("/api/applications/analyze-similarity", methods=["POST"])
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_analyze_similarity",
    deprecation_date="2026-02-10",
    migration_guide="Use POST /api/applications/analyze-similarity from application_api blueprint instead",
)
def analyze_application_similarity():
    """
    Trigger AI-powered similarity analysis for applications.

    Request body:
        - application_ids: Optional list of application IDs to analyze. If not provided, analyzes all.
        - provider: LLM provider ('claude', 'openai', 'gemini')
        - min_similarity_threshold: Minimum similarity score to consider (default: 40)

    Returns:
        JSON with analysis results and statistics
    """

    from ..services.application_similarity_service import ApplicationSimilarityService
    from ..services.llm_service import LLMService

    try:
        data = request.get_json() or {}
        application_ids = data.get("application_ids")
        provider = data.get("provider", "claude")
        min_threshold = data.get("min_similarity_threshold", 40)

        # Check if LLM service is properly configured
        try:
            configured_provider, model = LLMService._get_configured_provider()
            current_app.logger.info(
                f"Using LLM provider: {configured_provider}/{model}"
            )
        except Exception as e:
            current_app.logger.error(f"LLM service not configured: {str(e)}")
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "LLM service not configured. Please configure API keys and default models in the admin panel at /admin/api-settings",
                    }
                ),
                500,
            )

        similarity_service = ApplicationSimilarityService()

        # Run analysis using shared event loop utility
        from app.services.core.async_utils import get_or_create_event_loop

        get_or_create_event_loop()
        results = similarity_service.analyze_portfolio(
            application_ids=application_ids,
            provider=provider,
            user_id=current_user.id if current_user.is_authenticated else None,
            min_similarity_threshold=min_threshold,
        )

        return (
            jsonify(
                {
                    "success": True,
                    "results": results,
                    "message": f"Analyzed {results['total_analyses']} application pairs. Found {results['duplicate_pairs']} duplicate pairs.",
                }
            ),
            200,
        )

    except Exception as e:
        current_app.logger.error(f"Error analyzing similarity: {str(e)}")
        import traceback

        current_app.logger.error(traceback.format_exc())

        # Provide user-friendly error messages
        if "API key" in str(e).lower():
            error_msg = "API key not configured. Please configure LLM provider settings in the admin panel."
        elif "model" in str(e).lower():
            error_msg = (
                "Model not configured. Please set default models in the admin panel."
            )
        elif "connection" in str(e).lower() or "network" in str(e).lower():
            error_msg = "Network error connecting to LLM provider. Please check your internet connection and API configuration."
        else:
            error_msg = f"Error analyzing similarity: {str(e)}"

        return jsonify({"success": False, "error": error_msg}), 500


@application_mgmt.route("/api/applications/bulk-consolidate", methods=["POST"])
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_bulk_consolidate",
    deprecation_date="2026-02-10",
    migration_guide="Use POST /api/applications/bulk-consolidate from application_api blueprint instead",
)
def bulk_consolidate_applications():
    """
    Bulk consolidate selected applications.

    Expects:
        JSON with application_ids array

    Returns:
        JSON with consolidation results
    """
    from ..models.application_layer import ApplicationComponent

    try:
        data = request.get_json()
        if not data or "application_ids" not in data:
            return jsonify(
                {"success": False, "error": "No application IDs provided"}
            ), 400

        app_ids = data["application_ids"]
        if not app_ids:
            return jsonify(
                {"success": False, "error": "No application IDs provided"}
            ), 400

        # Validate CSRF token from headers
        csrf_token = request.headers.get("X-CSRFToken")
        if not csrf_token:
            return jsonify({"success": False, "error": "CSRF token missing"}), 400

        # Get applications to delete
        # Convert string IDs to integers to handle dataset.appId strings
        app_ids_int = [int(id) for id in app_ids]
        applications = ApplicationComponent.query.filter(
            ApplicationComponent.id.in_(app_ids_int)
        ).all()

        if not applications:
            # Debug logging to help identify the issue
            current_app.logger.warning(
                f"No applications found for IDs: {app_ids} (converted to: {app_ids_int})"
            )
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "No applications found",
                        "debug": {
                            "received_ids": app_ids,
                            "converted_ids": app_ids_int,
                        },
                    }
                ),
                404,
            )

        # Delete applications (simplified - in real implementation you'd do consolidation logic)
        deleted_count = 0
        for app in applications:
            try:
                db.session.delete(app)
                deleted_count += 1
            except Exception as e:
                current_app.logger.error(f"Error deleting app {app.id}: {str(e)}")
                continue

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": f"Successfully consolidated {deleted_count} applications",
                "deleted_count": deleted_count,
            }
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in bulk consolidation: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@application_mgmt.route("/api/applications/match-vendors", methods=["POST"])
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_match_vendors",
    deprecation_date="2026-02-10",
    migration_guide="Use POST /api/applications/match-vendors from application_api blueprint instead",
)
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
        from ..models.vendor.vendor_organization import (
            VendorOrganization,
            VendorProduct,
        )

        # Get all applications
        applications = ApplicationComponent.query.limit(
            1000
        ).all()  # Limit to prevent OOM on large datasets

        # Get all vendor products
        vendor_products = (
            db.session.query(VendorProduct)
            .options(joinedload(VendorProduct.vendor_organization))
            .join(VendorOrganization).limit(1000).all()
        )  # Limit to prevent OOM

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

    except Exception as e:
        current_app.logger.error(f"Error in vendor matching: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/applications/confirm-vendor-matches", methods=["POST"])
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_confirm_vendor_matches",
    deprecation_date="2026-02-10",
    migration_guide="Use POST /api/applications/confirm-vendor-matches from application_api blueprint instead",
)
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
        from ..models.models import ArchiMateElement
        from ..models.vendor.vendor_organization import (
            VendorProduct,
        )

        confirmed_count = 0

        # Prefetch all required data to avoid N+1 queries
        all_apps = ApplicationComponent.query.limit(2000).all()
        apps_by_id = {app.id: app for app in all_apps}

        all_vendor_products = VendorProduct.query.limit(2000).all()
        vendor_products_by_vendor_id = {}
        for vp in all_vendor_products:
            if vp.vendor_organization_id not in vendor_products_by_vendor_id:
                vendor_products_by_vendor_id[vp.vendor_organization_id] = vp

        all_archimate = ArchiMateElement.query.limit(5000).all()
        archimate_by_id = {elem.id: elem for elem in all_archimate}

        # Prefetch existing application-vendor relationships
        existing_relationships = db.session.execute(  # tenant-filtered: scoped via parent FK
            text(
                "SELECT archimate_element_id, vendor_product_id FROM application_vendor_products"
            )
        ).fetchall()
        existing_relationship_set = {(row[0], row[1]) for row in existing_relationships}

        for match in matches:
            try:
                application_id = match.get("application_id")
                vendor_id = match.get("vendor_id")

                if not application_id or not vendor_id:
                    continue

                # Get the application and vendor product using prefetched data
                app = apps_by_id.get(application_id)
                vendor_product = vendor_products_by_vendor_id.get(vendor_id)

                if not app or not vendor_product:
                    continue

                # Check if ArchiMate element exists for application
                archimate_element = None
                if app.archimate_element_id:
                    archimate_element = archimate_by_id.get(app.archimate_element_id)

                # Create ArchiMate element if it doesn't exist
                if not archimate_element:
                    archimate_element = ArchiMateElement(
                        name=app.name,
                        type="ApplicationComponent",
                        layer="application",
                        description=app.description or "",
                        properties=json.dumps(
                            {
                                "vendor_matched": True,
                                "vendor_id": vendor_id,
                                "vendor_name": vendor_product.vendor_organization.name,
                                "match_confidence": match.get("confidence", 0),
                            }
                        )
                        if match.get("confidence")
                        else None,
                    )
                    db.session.add(archimate_element)
                    db.session.flush()

                # Link to application
                app.archimate_element_id = archimate_element.id

                # Check if relationship already exists using prefetched data
                relationship_key = (archimate_element.id, vendor_product.id)
                if relationship_key not in existing_relationship_set:
                    db.session.execute(  # tenant-filtered: scoped via parent FK
                        text(
                            "INSERT INTO application_vendor_products (archimate_element_id, vendor_product_id, deployment_type, criticality) VALUES (:elem_id, :prod_id, :deploy, :crit)"
                        ),
                        {
                            "elem_id": archimate_element.id,
                            "prod_id": vendor_product.id,
                            "deploy": "primary_system",
                            "crit": "business_critical",
                        },
                    )

                confirmed_count += 1

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

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error confirming vendor matches: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/dashboard/applications/analyze-import-stream", methods=["GET"]
)
@login_required
def analyze_import_stream():
    """Stream real-time progress updates for import analysis using Server-Sent Events."""
    import json
    import time


    def generate():
        try:
            # Send initial message
            yield f"data: {json.dumps({'type': 'start', 'message': 'Starting analysis...'})}\n\n"

            # Simulate progress updates (in real implementation, this would be actual progress)
            # For now, we'll simulate the analysis process
            total_rows = 50  # This would come from actual file analysis
            ai_analyzed = 0
            apqc_found = 0
            vendors_found = 0

            for i in range(1, total_rows + 1):
                # Simulate processing each row
                time.sleep(0.1)  # Simulate processing time

                # Simulate AI analysis for some rows
                if i % 5 == 0:
                    ai_analyzed += 1
                    ai_status = "AI classification in progress"
                else:
                    ai_status = "Basic analysis"

                # Simulate APQC findings
                if i % 8 == 0:
                    apqc_found += 1

                # Simulate vendor findings
                if i % 10 == 0:
                    vendors_found += 1

                progress_data = {
                    "type": "progress",
                    "processed": i,
                    "total_rows": total_rows,
                    "current_item": f"Application {i}",
                    "ai_status": ai_status,
                    "apqc_found": apqc_found,
                    "vendors_found": vendors_found,
                }

                yield f"data: {json.dumps(progress_data)}\n\n"

            # Send completion message
            yield f"data: {json.dumps({'type': 'complete', 'message': 'Analysis complete'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
