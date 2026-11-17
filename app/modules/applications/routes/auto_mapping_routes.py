"""Semantic linking, APQC enrichment, and comprehensive auto-mapping routes."""

import json
import logging
from datetime import datetime

from flask import Response, current_app, flash, jsonify, request, stream_with_context
from flask_login import current_user, login_required

from app import db
from app.decorators import audit_log
from app.models.application_portfolio import ApplicationComponent
from app.services.ai_import_service import get_ai_import_service
from app.services.rate_limiter import rate_limit

from . import unified_applications_bp
from ._constants import DEFAULT_TOKEN_RATE_DIVISOR
from app.utils.route_guards import require_entity_json

logger = logging.getLogger(__name__)


@unified_applications_bp.route("/<int:id>/semantic-link-preview", methods=["GET"])
@login_required
def semantic_link_preview(id):
    """
    Generate semantic linking proposals for an application.
    Returns JSON for the UI to display in a modal.
    """
    _app, missing = require_entity_json(ApplicationComponent, id, label="Application")
    if missing:
        return missing

    try:
        from app.services.semantic_linking_service import SemanticLinkingService

        service = SemanticLinkingService()
        proposals = service.generate_linking_proposals(id)
        return jsonify(proposals)
    except Exception as e:
        current_app.logger.error(f"Error generating linking proposals: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


@unified_applications_bp.route("/<int:id>/apply-semantic-links", methods=["POST"])
@login_required
@audit_log("apply_semantic_links")
def apply_semantic_links(id):
    """
    Apply selected semantic links to an application.
    Expects JSON body: {'selected_links': {'capabilities': [1, 2], 'processes': [3]}}
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        from app.services.semantic_linking_service import SemanticLinkingService

        service = SemanticLinkingService()
        result = service.apply_links(id, data.get("selected_links", {}))

        if result.get("success"):
            flash(
                f"Successfully applied {result.get('applied_count')} semantic links.",
                "success",
            )
            return jsonify(result)
        else:
            return jsonify(result), 400

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error applying semantic links: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


@unified_applications_bp.route("/api/apqc-vendor-enriched-archimate", methods=["POST"])
@login_required
@audit_log("generate_apqc_vendor_archimate")
def apqc_vendor_enriched_archimate():
    """
    Generate APQC-vendor enriched ArchiMate elements.

    This endpoint creates intelligent BusinessProcess elements that combine:
    - APQC process mappings
    - Vendor product capabilities
    - Coverage analysis and gap assessment
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON data"}), 400

    try:
        from app.services.apqc_vendor_archimate_service import (
            APQCVendorArchiMateService,
        )

        application_id = data.get("application_id")
        if not application_id:
            return jsonify({"error": "application_id is required"}), 400

        result = APQCVendorArchiMateService.generate_apqc_vendor_enriched_archimate(
            application_id=application_id,
            created_by=current_user.email
            if current_user.is_authenticated
            else "system",
        )

        return jsonify(result)

    except Exception as e:
        current_app.logger.error(
            f"Error in APQC-vendor enriched ArchiMate generation: {e}"
        )
        return jsonify({"error": "An internal error occurred"}), 500


@unified_applications_bp.route("/api/bulk-apqc-vendor-archimate", methods=["POST"])
@login_required
@audit_log("bulk_generate_apqc_vendor_archimate")
def bulk_apqc_vendor_enriched_archimate():
    """
    Bulk generate APQC-vendor enriched ArchiMate elements for multiple applications.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON data"}), 400

    try:
        from app.services.apqc_vendor_archimate_service import (
            APQCVendorArchiMateService,
        )

        result = APQCVendorArchiMateService.bulk_generate_apqc_vendor_archimate(
            max_applications=data.get("max_applications", 50),
            only_with_apqc_mappings=data.get("only_with_apqc_mappings", True),
            created_by=current_user.email
            if current_user.is_authenticated
            else "system",
        )

        return jsonify(result)

    except Exception as e:
        current_app.logger.error(
            f"Error in bulk APQC-vendor enriched ArchiMate generation: {e}"
        )
        return jsonify({"error": "An internal error occurred"}), 500


@unified_applications_bp.route("/api/comprehensive-auto-map", methods=["POST"])
@login_required
@audit_log("comprehensive_auto_map")
@rate_limit(10, "1h")  # LLM-003: Limit expensive AI operations to 10/hour
def comprehensive_auto_map():
    """
    TRULY INTELLIGENT comprehensive auto-mapping endpoint using AI services.

    Replaces basic pattern matching with sophisticated AI analysis:
    - Semantic APQC classification using real embeddings
    - LLM-powered business capability mapping
    - ArchiMate element generation from application descriptions
    - Confidence scoring and user review workflow
    """
    # Check LLM availability FIRST (LLM-002: Graceful degradation)
    from app.services.llm_service import LLMService

    if not LLMService.is_available():
        return jsonify(
            {
                "success": False,
                "error": "service_unavailable",
                "message": "AI features are temporarily unavailable. LLM provider is not configured. "
                "Please contact your administrator to configure an AI provider.",
                "ai_enabled": False,
            }
        ), 503

    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON data"}), 400

    try:
        # NEW: Check LLM configuration BEFORE starting analysis
        from app.services.llm_service import LLMService

        config_status = LLMService.configuration_status()
        if not config_status["ready"]:
            return jsonify(
                {
                    "success": False,
                    "error": "LLM_NOT_CONFIGURED",
                    "message": "No LLM provider configured. Please configure an API key at /admin/api-settings",
                    "config_status": config_status,
                }
            ), 400

        # Use the new AI Import Service for truly intelligent mapping
        from app.services.ai_import_service import get_ai_import_service

        ai_service = get_ai_import_service()

        # preview_mode: true must never write, whatever auto_create says -
        # this flag was previously read and discarded, so a preview call
        # could still commit mappings the caller had not accepted.
        preview_mode = bool(data.get("preview_mode", False))

        # Vendor ArchiMate cloning happens inside bulk_ai_analyze itself
        # (see vendor_stats below); nothing here needs the flag directly.
        data.get("clone_vendor_archimate", True)

        # Perform bulk AI analysis. application_ids, when given, scopes the
        # source rows to exactly those applications (see bulk_ai_analyze) -
        # previously discarded, so a caller naming a specific import batch got
        # whichever applications were newest overall instead.
        application_ids = data.get("application_ids") or None
        _layer_targets = data.get("layer_targets", None)
        # generation_mode / layer_targets still have no effect: the service
        # has no concept of them yet. The API accepts them without error, but
        # they are not implemented.
        map_capabilities = data.get("map_capabilities", True)
        map_processes = data.get("map_processes", True)
        analysis_result = ai_service.bulk_ai_analyze(
            max_applications=data.get("max_applications", 50),
            confidence_threshold=data.get("confidence_threshold", 0.7),
            application_ids=application_ids,
        )

        # Auto-create mappings if requested. Counts are tracked per category -
        # previously summed into one "created" bucket and reported as
        # capability/process/archimate counts simultaneously, so any nonzero
        # write looked like all three categories had succeeded.
        creation_result = {
            "capability_mappings_created": 0,
            "process_mappings_created": 0,
            "archimate_elements_created": 0,
            "errors": [],
            "applications_processed": 0,
        }
        if data.get("auto_create", False) and not preview_mode:
            # NEW: Wrap in transaction with rollback on failure (P0 CRITICAL FIX)
            try:
                # Find high-confidence mappings to auto-create
                for app_result in analysis_result["applications"]:
                    if "error" in app_result:
                        continue  # Skip failed analyses

                    # Only auto-create high-confidence mappings, and only for
                    # categories the caller asked for.
                    high_conf_capabilities = [
                        m
                        for m in app_result.get("capability_mappings", [])
                        if map_capabilities
                        and m.get("confidence_score", 0)
                        >= data.get("confidence_threshold", 0.7)
                    ]

                    high_conf_processes = [
                        m
                        for m in app_result.get("process_mappings", [])
                        if map_processes
                        and m.get("similarity_score", 0)
                        >= data.get("confidence_threshold", 0.7)
                    ]

                    # Create mappings if any high-confidence ones found
                    if high_conf_capabilities or high_conf_processes:
                        creation = ai_service.create_ai_mappings(
                            application_id=app_result["application_id"],
                            capability_mappings=high_conf_capabilities,
                            process_mappings=high_conf_processes,
                            created_by=current_user.email
                            if current_user.is_authenticated
                            else "ai_auto",
                        )

                        creation_result["capability_mappings_created"] += creation[
                            "capability_mappings_created"
                        ]
                        creation_result["process_mappings_created"] += creation[
                            "process_mappings_created"
                        ]
                        creation_result["applications_processed"] += 1

                        if creation["errors"]:
                            creation_result["errors"].extend(creation["errors"])

                # Commit all successful creations
                db.session.commit()

            except Exception as e:
                # ROLLBACK: On any failure, revert all changes (all-or-nothing semantics)
                db.session.rollback()
                current_app.logger.error(
                    f"Auto-map transaction rolled back due to error: {e}"
                )
                creation_result["errors"].append(
                    f"Transaction failed and was rolled back: {str(e)}"
                )
                creation_result["capability_mappings_created"] = 0
                creation_result["process_mappings_created"] = 0
                creation_result["archimate_elements_created"] = 0
                creation_result["applications_processed"] = 0

        # Count vendor shared elements + relationships for statistics
        vendor_stats = analysis_result.get("vendor_stats", {})
        vendor_shared_elem_count = sum(
            c for c in (vendor_stats.get("shared_elements") or {}).values()
        )
        vendor_relationship_count = vendor_stats.get("relationships_created", 0)
        total_archimate = (
            analysis_result["archimate_elements_generated"] + vendor_shared_elem_count
        )

        # Build comprehensive response
        result = {
            "success": True,  # REQUIRED: JavaScript expects this flag
            # Statistics object (matches frontend renderAutoMapResults expectations)
            "statistics": {
                "applications_processed": analysis_result["total_analyzed"],
                "capabilities_mapped": analysis_result["capability_mappings_found"],
                "archimate_elements": total_archimate,
                "relationships": vendor_relationship_count,
            },
            # Flat keys kept for backward compatibility
            "total_analyzed": analysis_result["total_analyzed"],
            "capability_mappings_found": analysis_result["capability_mappings_found"],
            "process_mappings_found": analysis_result["process_mappings_found"],
            "archimate_elements_generated": total_archimate,
            "high_confidence_mappings": analysis_result["high_confidence_mappings"],
            # Creation results — per-category, truthful counts (see above)
            "capability_mappings_created": creation_result["capability_mappings_created"],
            "process_mappings_created": creation_result["process_mappings_created"],
            "archimate_elements_created": creation_result["archimate_elements_created"],
            # Processing metadata
            "processing_stats": analysis_result["processing_stats"],
            # Detailed application results (for UI display)
            "applications": analysis_result["applications"],
            # AI service information
            "ai_models_used": analysis_result["processing_stats"]["ai_models_used"],
            "avg_processing_time_ms": analysis_result["processing_stats"][
                "avg_processing_time_ms"
            ],
            # Vendor integration metrics
            "vendor_matches_found": sum(
                1 for a in analysis_result["applications"] if a.get("vendor_matched")
            ),
            "vendor_archimate_cloned": sum(
                a.get("mappings_created", {}).get("vendor_archimate", 0)
                for a in analysis_result["applications"]
            ),
        }

        # Add any creation errors
        if creation_result["errors"]:
            result["creation_errors"] = creation_result["errors"]

        # NEW: Confidence review integration (P1-6)
        requires_review_count = 0
        for app_result in analysis_result["applications"]:
            for mapping in app_result.get("capability_mappings", []):
                if mapping.get("confidence_score", 0) < data.get(
                    "confidence_threshold", 0.7
                ):
                    requires_review_count += 1

        # Add review queue indicator to response
        result["confidence_review"] = {
            "enabled": True,
            "low_confidence_mappings": requires_review_count,
            "requires_human_review": requires_review_count > 0,
            "review_url": "/reviews/confidence" if requires_review_count > 0 else None,
        }

        # Aggregate ArchiMate layer breakdown from generated elements
        layer_breakdown = {}
        for app_result in analysis_result["applications"]:
            for element in app_result.get("archimate_elements", []):
                layer = element.get("layer", "unknown").lower()
                layer_breakdown[layer] = layer_breakdown.get(layer, 0) + 1
        result["layer_breakdown"] = layer_breakdown

        # Include vendor stats from analysis
        if "vendor_stats" in analysis_result:
            result["vendor_stats"] = analysis_result["vendor_stats"]

        return jsonify(result)

    except Exception as e:
        current_app.logger.error(f"Error in comprehensive AI auto-map: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


@unified_applications_bp.route("/api/comprehensive-auto-map-stream", methods=["POST"])
@login_required
@audit_log("comprehensive_auto_map_stream")
@rate_limit(10, "1h")  # LLM-003: Limit expensive AI operations to 10/hour
def comprehensive_auto_map_stream():
    """
    SSE streaming version of comprehensive auto-map.

    Sends real-time progress events as each application is processed,
    so the frontend can show per-app status instead of a blank spinner.

    Event format:
        data: {"stage": "...", "message": "...", "progress": 0-100, ...}

    Stages: config_check, vendor_grouping, vendor_elements, analyzing,
            app_complete, finalizing, complete, error
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON data"}), 400

    # Check LLM availability before starting stream (LLM-002: Graceful degradation)
    from app.services.llm_service import LLMService

    if not LLMService.is_available():
        return jsonify(
            {
                "error": "AI features are temporarily unavailable. LLM provider is not configured. "
                "Please contact your administrator to configure an AI provider."
            }
        ), 503

    # Read config before entering generator (request context closes)
    max_applications = data.get("max_applications", 50)
    confidence_threshold = data.get("confidence_threshold", 0.7)
    application_ids = data.get("application_ids", None)
    generation_mode = data.get("generation_mode", "standard")
    layer_targets = data.get("layer_targets", None)

    def generate_progress():
        try:
            # Stage 1: Config check
            yield f"data: {json.dumps({'stage': 'config_check', 'message': 'Checking LLM configuration...', 'progress': 2})}\n\n"

            ai_service = get_ai_import_service()

            yield f"data: {json.dumps({'stage': 'config_check', 'message': 'LLM ready. Loading applications...', 'progress': 5})}\n\n"

            # Stage 2: Load applications
            if application_ids:
                apps = ApplicationComponent.query.filter(
                    ApplicationComponent.id.in_(application_ids)
                ).all()
            else:
                apps = (
                    ApplicationComponent.query.order_by(
                        ApplicationComponent.created_at.desc()
                    )
                    .limit(max_applications)
                    .all()
                )

            total_apps = len(apps)
            if total_apps == 0:
                yield f"data: {json.dumps({'stage': 'error', 'message': 'No applications found to analyze.', 'progress': 0})}\n\n"
                return

            yield f"data: {json.dumps({'stage': 'vendor_grouping', 'message': f'Found {total_apps} applications. Grouping by vendor...', 'progress': 8, 'total_apps': total_apps})}\n\n"

            # Stage 3: Vendor grouping
            from app.services.application_architecture_mapper import (
                ApplicationArchitectureMapperService,
            )

            mapper = ApplicationArchitectureMapperService

            vendor_groups = {}
            no_vendor_apps = []
            for app in apps:
                vendor_name = mapper.extract_vendor_from_application(app)
                if vendor_name:
                    vendor_groups.setdefault(vendor_name, []).append(app)
                else:
                    no_vendor_apps.append(app)

            vendor_count = len(vendor_groups)
            vendor_app_count = sum(len(v) for v in vendor_groups.values())

            yield f"data: {json.dumps({'stage': 'vendor_grouping', 'message': f'{vendor_count} vendors detected ({vendor_app_count} apps), {len(no_vendor_apps)} standalone', 'progress': 10})}\n\n"

            # Stage 4: Generate shared vendor elements
            vendor_shared_elements = {}
            vendor_linked_apps = {}
            total_relationships_created = 0

            for vi, (vendor_name, vendor_apps) in enumerate(vendor_groups.items()):
                try:
                    yield f"data: {json.dumps({'stage': 'vendor_elements', 'message': f'Processing vendor: {vendor_name} ({vi + 1}/{vendor_count})', 'progress': 10 + int(5 * (vi + 1) / max(vendor_count, 1))})}\n\n"

                    existing_elements = ai_service._find_vendor_shared_elements(
                        vendor_name
                    )
                    if existing_elements:
                        vendor_shared_elements[vendor_name] = existing_elements
                    else:
                        generated = ai_service._generate_vendor_shared_elements(
                            vendor_name, vendor_apps[0], generation_mode
                        )
                        if generated:
                            vendor_shared_elements[vendor_name] = generated

                    shared_ids = vendor_shared_elements.get(vendor_name, [])
                    if shared_ids:
                        for vapp in vendor_apps:
                            linked = ai_service._link_app_to_shared_elements(
                                vapp.id, shared_ids
                            )
                            if linked > 0:
                                vendor_linked_apps[vapp.id] = vendor_name
                                total_relationships_created += linked
                except Exception as e:
                    current_app.logger.warning(
                        f"Vendor processing failed for {vendor_name}: {e}"
                    )

            # Stage 5: Per-application AI analysis (the bulk of the work)
            # Progress range: 15% to 90% for this phase
            results = {
                "total_analyzed": 0,
                "capability_mappings_found": 0,
                "process_mappings_found": 0,
                "archimate_elements_generated": sum(
                    len(ids) for ids in vendor_shared_elements.values()
                ),
                "high_confidence_mappings": 0,
                "low_confidence_items_queued": 0,
                "applications": [],
                "processing_stats": {
                    "avg_processing_time_ms": 0,
                    "ai_models_used": set(),
                },
            }

            total_processing_time = 0

            for idx, app in enumerate(apps):
                app_progress = 15 + int(75 * (idx) / total_apps)
                app_name = app.name or f"App #{app.id}"

                yield f"data: {json.dumps({'stage': 'analyzing', 'message': f'Analyzing: {app_name}', 'progress': app_progress, 'current_app': idx + 1, 'total_apps': total_apps, 'app_name': app_name})}\n\n"

                try:
                    if app.id in vendor_linked_apps:
                        vendor_overrides = dict(layer_targets or {})
                        vendor_overrides.update(
                            {
                                "motivation": 0,
                                "strategy": 0,
                                "business": 0,
                                "application": 0,
                                "technology": 0,
                                "physical": 0,
                                "implementation": 0,
                            }
                        )
                        # Takes application_id only; generation_mode and
                        # layer_overrides are not implemented by the service.
                        ai_result = ai_service.analyze_application_for_ai_mapping(app.id)
                    else:
                        ai_result = ai_service.analyze_application_for_ai_mapping(app.id)

                    # Count high-confidence mappings
                    high_conf_count = 0
                    if ai_result.capability_mappings:
                        high_conf_count = sum(
                            1
                            for m in ai_result.capability_mappings
                            if m.get("confidence_score", 0) >= confidence_threshold
                        )

                    # Save high-confidence mappings immediately
                    mappings_saved = {"capabilities": 0, "processes": 0, "archimate": 0}
                    low_confidence_items_created = 0
                    try:
                        high_conf_capabilities = [
                            m
                            for m in ai_result.capability_mappings
                            if m.get("confidence_score", 0) >= confidence_threshold
                        ]
                        high_conf_processes = [
                            m
                            for m in ai_result.process_mappings
                            if m.get("similarity_score", 0) >= confidence_threshold
                        ]

                        # Get low-confidence mappings for review queue
                        low_conf_capabilities = [
                            m
                            for m in ai_result.capability_mappings
                            if m.get("confidence_score", 0) < confidence_threshold
                        ]
                        low_conf_processes = [
                            m
                            for m in ai_result.process_mappings
                            if m.get("similarity_score", 0) < confidence_threshold
                        ]

                        if high_conf_capabilities or high_conf_processes:
                            save_result = ai_service.create_ai_mappings(
                                application_id=app.id,
                                capability_mappings=high_conf_capabilities,
                                process_mappings=high_conf_processes,
                                archimate_elements=ai_result.archimate_elements,
                                created_by="auto_map",
                            )
                            mappings_saved["capabilities"] = save_result.get(
                                "capability_mappings_created", 0
                            )
                            mappings_saved["processes"] = save_result.get(
                                "process_mappings_created", 0
                            )
                            mappings_saved["archimate"] = save_result.get(
                                "archimate_elements_created", 0
                            )
                            db.session.commit()

                        # Add low-confidence items to review queue
                        if low_conf_capabilities or low_conf_processes:
                            from app.services.confidence_review_service import (
                                ConfidenceReviewService,
                                ReviewQueueItemData,
                            )

                            confidence_review = ConfidenceReviewService()

                            for mapping in low_conf_capabilities:
                                try:
                                    item_data = ReviewQueueItemData(
                                        item_type="capability_mapping",
                                        item_id=app.id,
                                        item_name=f"{app.name} → {mapping.get('capability_name', 'Unknown')}",
                                        item_data={
                                            "application_id": app.id,
                                            "application_name": app.name,
                                            "capability_id": mapping.get(
                                                "capability_id"
                                            ),
                                            "capability_name": mapping.get(
                                                "capability_name"
                                            ),
                                            "confidence_score": mapping.get(
                                                "confidence_score", 0
                                            ),
                                            "rationale": mapping.get("rationale", ""),
                                        },
                                        confidence_score=mapping.get(
                                            "confidence_score", 0
                                        ),
                                        confidence_factors={
                                            "similarity": mapping.get(
                                                "confidence_score", 0
                                            ),
                                            "context_match": mapping.get(
                                                "confidence_score", 0
                                            ),
                                        },
                                        ai_model_used=", ".join(
                                            ai_result.ai_models_used
                                        ),
                                        generation_timestamp=datetime.utcnow(),
                                        threshold_name="capability_mapping_auto",
                                        context_type="auto_map",
                                        context_value="comprehensive",
                                        domain="business",
                                    )

                                    # Evaluate and add to queue
                                    eval_result = (
                                        confidence_review.evaluate_confidence_threshold(
                                            item_data
                                        )
                                    )
                                    if eval_result.get("success") and eval_result.get(
                                        "requires_review"
                                    ):
                                        queue_result = (
                                            confidence_review.add_to_review_queue(
                                                item_data, eval_result
                                            )
                                        )
                                        if queue_result.get("success"):
                                            low_confidence_items_created += 1
                                except Exception as review_error:
                                    current_app.logger.warning(
                                        f"Failed to add capability to review queue: {review_error}"
                                    )

                            for mapping in low_conf_processes:
                                try:
                                    item_data = ReviewQueueItemData(
                                        item_type="process_mapping",
                                        item_id=app.id,
                                        item_name=f"{app.name} → {mapping.get('process_name', 'Unknown')}",
                                        item_data={
                                            "application_id": app.id,
                                            "application_name": app.name,
                                            "process_id": mapping.get("process_id"),
                                            "process_name": mapping.get("process_name"),
                                            "confidence_score": mapping.get(
                                                "similarity_score", 0
                                            ),
                                            "rationale": mapping.get("rationale", ""),
                                        },
                                        confidence_score=mapping.get(
                                            "similarity_score", 0
                                        ),
                                        confidence_factors={
                                            "similarity": mapping.get(
                                                "similarity_score", 0
                                            ),
                                            "process_alignment": mapping.get(
                                                "similarity_score", 0
                                            ),
                                        },
                                        ai_model_used=", ".join(
                                            ai_result.ai_models_used
                                        ),
                                        generation_timestamp=datetime.utcnow(),
                                        threshold_name="process_mapping_auto",
                                        context_type="auto_map",
                                        context_value="comprehensive",
                                        domain="business",
                                    )

                                    # Evaluate and add to queue
                                    eval_result = (
                                        confidence_review.evaluate_confidence_threshold(
                                            item_data
                                        )
                                    )
                                    if eval_result.get("success") and eval_result.get(
                                        "requires_review"
                                    ):
                                        queue_result = (
                                            confidence_review.add_to_review_queue(
                                                item_data, eval_result
                                            )
                                        )
                                        if queue_result.get("success"):
                                            low_confidence_items_created += 1
                                except Exception as review_error:
                                    current_app.logger.warning(
                                        f"Failed to add process to review queue: {review_error}"
                                    )

                            # Commit review queue items
                            db.session.commit()

                    except Exception as save_error:
                        current_app.logger.error(
                            f"Failed to save mappings for {app_name}: {save_error}"
                        )
                        db.session.rollback()

                    # Update cumulative stats
                    results["total_analyzed"] += 1
                    results["capability_mappings_found"] += len(
                        ai_result.capability_mappings
                    )
                    results["process_mappings_found"] += len(ai_result.process_mappings)
                    results["archimate_elements_generated"] += len(
                        ai_result.archimate_elements
                    )
                    results["high_confidence_mappings"] += high_conf_count
                    results["low_confidence_items_queued"] += (
                        low_confidence_items_created
                    )
                    total_processing_time += ai_result.processing_time_ms
                    results["processing_stats"]["ai_models_used"].update(
                        ai_result.ai_models_used
                    )

                    # Build vendor meta
                    vendor_meta = {}
                    if app.id in vendor_linked_apps:
                        vn = vendor_linked_apps[app.id]
                        vendor_meta = {
                            "vendor_linked": True,
                            "vendor_name": vn,
                            "shared_elements": len(vendor_shared_elements.get(vn, [])),
                        }

                    app_result_dict = {
                        "application_id": ai_result.application_id,
                        "application_name": ai_result.application_name,
                        "capability_mappings": ai_result.capability_mappings,
                        "process_mappings": ai_result.process_mappings,
                        "archimate_elements": ai_result.archimate_elements,
                        "avg_capability_confidence": ai_result.avg_capability_confidence,
                        "avg_process_confidence": ai_result.avg_process_confidence,
                        "high_confidence_mappings": high_conf_count,
                        "processing_time_ms": ai_result.processing_time_ms,
                        "warnings": ai_result.warnings,
                        "saved_to_db": mappings_saved,
                        "status": "saved"
                        if sum(mappings_saved.values()) > 0
                        else "analyzed_only",
                    }
                    if vendor_meta:
                        app_result_dict["vendor"] = vendor_meta
                    results["applications"].append(app_result_dict)

                    # Emit per-app completion event
                    done_progress = 15 + int(75 * (idx + 1) / total_apps)
                    caps = len(ai_result.capability_mappings)
                    elems = len(ai_result.archimate_elements)
                    status_label = (
                        "saved" if sum(mappings_saved.values()) > 0 else "analyzed"
                    )

                    yield f"data: {json.dumps({'stage': 'app_complete', 'message': f'{app_name}: {caps} capabilities, {elems} elements ({status_label})', 'progress': done_progress, 'current_app': idx + 1, 'total_apps': total_apps, 'app_name': app_name, 'capabilities': caps, 'elements': elems, 'status': status_label, 'processing_time_ms': ai_result.processing_time_ms})}\n\n"

                except Exception as e:
                    current_app.logger.error(f"Auto-map failed for app {app.id}: {e}")
                    results["applications"].append(
                        {
                            "application_id": app.id,
                            "application_name": app_name,
                            "error": str(e),
                            "warnings": [f"Analysis failed: {str(e)}"],
                        }
                    )
                    done_progress = 15 + int(75 * (idx + 1) / total_apps)
                    yield f"data: {json.dumps({'stage': 'app_complete', 'message': f'{app_name}: Error - {str(e)[:80]}', 'progress': done_progress, 'current_app': idx + 1, 'total_apps': total_apps, 'app_name': app_name, 'error': True})}\n\n"

            # Stage 6: Finalize
            yield f"data: {json.dumps({'stage': 'finalizing', 'message': 'Calculating statistics...', 'progress': 92})}\n\n"

            if results["total_analyzed"] > 0:
                results["processing_stats"]["avg_processing_time_ms"] = (
                    total_processing_time // results["total_analyzed"]
                )
            results["processing_stats"]["ai_models_used"] = list(
                results["processing_stats"]["ai_models_used"]
            )

            # Aggregate layer breakdown
            layer_breakdown = {}
            for app_result in results["applications"]:
                for element in app_result.get("archimate_elements", []):
                    layer = element.get("layer", "unknown").lower()
                    layer_breakdown[layer] = layer_breakdown.get(layer, 0) + 1

            # Confidence review
            requires_review_count = sum(
                1
                for app_result in results["applications"]
                for mapping in app_result.get("capability_mappings", [])
                if mapping.get("confidence_score", 0) < confidence_threshold
            )

            # Vendor stats
            vendor_stats = {
                "unique_vendors": len(vendor_groups),
                "apps_with_vendor": len(vendor_linked_apps),
                "apps_without_vendor": len(no_vendor_apps),
                "vendor_groups": {
                    name: len(vapps) for name, vapps in vendor_groups.items()
                },
                "shared_elements": {
                    name: len(ids) for name, ids in vendor_shared_elements.items()
                },
                "relationships_created": total_relationships_created,
            }

            # Build final response
            final_result = {
                "success": True,
                "statistics": {
                    "applications_processed": results["total_analyzed"],
                    "capabilities_mapped": results["capability_mappings_found"],
                    "archimate_elements": results["archimate_elements_generated"],
                    "relationships": total_relationships_created,
                },
                "applications": results["applications"],
                "layer_breakdown": layer_breakdown,
                "vendor_stats": vendor_stats,
                "processing_stats": results["processing_stats"],
                "confidence_review": {
                    "enabled": True,
                    "low_confidence_count": requires_review_count,
                    "items_queued": results["low_confidence_items_queued"],
                    "requires_human_review": results["low_confidence_items_queued"] > 0,
                },
            }

            yield f"data: {json.dumps({'stage': 'complete', 'message': 'Auto-mapping complete', 'progress': 100, 'results': final_result})}\n\n"

        except Exception as e:
            current_app.logger.error(f"Error in streaming auto-map: {e}")
            yield f"data: {json.dumps({'stage': 'error', 'message': f'An error occurred: {str(e)[:200]}', 'progress': 0})}\n\n"

    return Response(
        stream_with_context(generate_progress()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@unified_applications_bp.route("/api/comprehensive-auto-map/accept", methods=["POST"])
@login_required
@audit_log("accept_auto_map_results")
def accept_auto_map_results():
    """
    Accept and save mappings from a preview analysis.

    This endpoint creates actual database records from the preview data
    that was returned by the comprehensive-auto-map endpoint in preview mode.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON data"}), 400

    try:
        from app.services.ai_import_service import get_ai_import_service

        ai_service = get_ai_import_service()
        applications = data.get("applications", [])
        confidence_threshold = data.get("confidence_threshold", 0.7)

        creation_result = {"created": 0, "errors": [], "applications_processed": 0}

        for app_data in applications:
            app_id = app_data.get("application_id")
            if not app_id:
                continue

            # Get high-confidence mappings
            high_conf_capabilities = [
                m
                for m in app_data.get("capability_mappings", [])
                if m.get("confidence_score", 0) >= confidence_threshold
            ]
            high_conf_processes = [
                m
                for m in app_data.get("process_mappings", [])
                if m.get("similarity_score", 0) >= confidence_threshold
            ]
            archimate_elements = app_data.get("archimate_elements", [])[:5]

            if high_conf_capabilities or high_conf_processes or archimate_elements:
                creation = ai_service.create_ai_mappings(
                    application_id=app_id,
                    capability_mappings=high_conf_capabilities,
                    process_mappings=high_conf_processes,
                    archimate_elements=archimate_elements,
                    created_by=current_user.email
                    if current_user.is_authenticated
                    else "auto_map_accept",
                )

                creation_result["created"] += (
                    creation["capability_mappings_created"]
                    + creation["process_mappings_created"]
                    + creation["archimate_elements_created"]
                )
                creation_result["applications_processed"] += 1

                if creation["errors"]:
                    creation_result["errors"].extend(creation["errors"])

        return jsonify(
            {
                "success": True,
                "mappings_created": creation_result["created"],
                "applications_processed": creation_result["applications_processed"],
                "errors": creation_result["errors"]
                if creation_result["errors"]
                else None,
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error accepting auto-map results: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


@unified_applications_bp.route(
    "/api/comprehensive-auto-map/estimate-cost", methods=["POST"]
)
@login_required
@audit_log("estimate_auto_map_cost")
def estimate_auto_map_cost():
    """
    Estimate the cost of running comprehensive auto-mapping BEFORE execution.

    This allows users to make informed decisions about whether to proceed
    with auto-mapping based on expected LLM costs.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON data"}), 400

    try:
        from app.services.llm_cost_tracker import LLMCostTracker

        # Get parameters
        max_applications = data.get("max_applications", 50)
        map_capabilities = data.get("map_capabilities", True)
        generate_archimate = data.get("generate_archimate", True)
        application_ids = data.get("application_ids", None)
        generation_mode = data.get("generation_mode", "standard")

        # Load generation mode config from canonical source
        from app.services.archimate_layer_generators import GENERATION_MODES

        if generation_mode not in GENERATION_MODES:
            generation_mode = "standard"
        mode_config = GENERATION_MODES[generation_mode]

        # Count applications that would be analyzed
        from app.models import ApplicationComponent

        if application_ids:
            apps_to_analyze = len(application_ids)
            total_apps = ApplicationComponent.query.count()
        else:
            apps_query = ApplicationComponent.query.filter(
                db.or_(
                    ApplicationComponent.deployment_status.is_(None),
                    ApplicationComponent.deployment_status == "",
                    ApplicationComponent.deployment_status.in_(
                        ["active", "evaluating", "pilot", "production"]
                    ),
                )
            ).filter(
                ~ApplicationComponent.name.ilike("%legacy%"),
                ~ApplicationComponent.name.ilike("%retired%"),
                ~ApplicationComponent.name.ilike("%decommissioned%"),
            )
            total_apps = apps_query.count()
            apps_to_analyze = min(max_applications, total_apps)

        # Vendor grouping analysis for cost optimization
        vendor_groups = {}
        no_vendor_count = 0
        try:
            from app.services.application_architecture_mapper import (
                ApplicationArchitectureMapperService,
            )

            mapper_svc = ApplicationArchitectureMapperService

            if application_ids:
                target_apps = ApplicationComponent.query.filter(
                    ApplicationComponent.id.in_(application_ids)
                ).all()
            else:
                target_apps = apps_query.limit(max_applications).all()

            for tapp in target_apps:
                detected = mapper_svc.extract_vendor_from_application(tapp)
                if not detected and tapp.vendor_name:
                    # Fallback: use raw vendor_name field if pattern detection missed
                    detected = tapp.vendor_name.strip()
                if detected:
                    vendor_groups.setdefault(detected, []).append(
                        {"id": tapp.id, "name": tapp.name}
                    )
                else:
                    no_vendor_count += 1
        except Exception as vendor_err:
            current_app.logger.warning(f"Vendor grouping analysis failed: {vendor_err}")
            no_vendor_count = apps_to_analyze

        apps_with_vendor = apps_to_analyze - no_vendor_count

        # Helper: parse range string "15 - 25" → {min, max, avg}
        def parse_range(range_str):
            parts = range_str.split("-")
            lo = int(parts[0].strip())
            hi = int(parts[1].strip())
            return {"min": lo, "max": hi, "avg": int((lo + hi) / 2)}

        # Accept custom per-layer targets from user
        layer_targets = data.get("layer_targets", None)

        # Calculate estimated elements per layer from mode config
        elements_estimate = {}
        layer_names = [
            "motivation",
            "strategy",
            "business",
            "application",
            "technology",
            "physical",
            "implementation",
        ]

        enabled_layers = 0
        total_layer_elements = 0
        if generate_archimate:
            layer_estimates = {}
            layer_defaults = {}
            for layer in layer_names:
                parsed = parse_range(mode_config[layer])
                layer_defaults[layer] = parsed
                # Use custom target if provided, otherwise mode average
                if layer_targets and layer in layer_targets:
                    per_app = int(layer_targets[layer])
                else:
                    per_app = parsed["avg"]
                layer_estimates[layer] = apps_to_analyze * per_app
                if per_app > 0:
                    enabled_layers += 1
                total_layer_elements += apps_to_analyze * per_app
            elements_estimate["archimate_layers"] = layer_estimates
            elements_estimate["archimate_total"] = total_layer_elements
            # Expose per-app defaults (min/max/avg) for slider configuration
            elements_estimate["archimate_defaults"] = layer_defaults

        if map_capabilities:
            elements_estimate["capabilities"] = apps_to_analyze * 4
            elements_estimate["apqc_processes"] = apps_to_analyze * 5
            elements_estimate["apqc_parent_links"] = apps_to_analyze * 2

        # Token estimation: Vendor apps share ALL layers generated ONCE per vendor.
        # Same vendor product = identical ArchiMate across all 7 layers.
        # Vendor apps skip ALL ArchiMate generation (elements linked via relationships).
        unique_vendors = len(vendor_groups)
        if generate_archimate and apps_with_vendor > 0:
            vendor_shared_calls = (
                unique_vendors * enabled_layers
            )  # all layers, once per vendor
            vendor_app_calls = 0  # vendor apps skip ALL layers (linked, not generated)
            no_vendor_layer_calls = no_vendor_count * enabled_layers
            archimate_llm_calls = (
                vendor_shared_calls + vendor_app_calls + no_vendor_layer_calls
            )
        else:
            archimate_llm_calls = apps_to_analyze * enabled_layers

        # Capability calls: also shared per vendor (same product = same capabilities)
        if map_capabilities:
            vendor_cap_calls = unique_vendors * 2 if apps_with_vendor > 0 else 0
            no_vendor_cap_calls = no_vendor_count * 2
            cap_calls = vendor_cap_calls + no_vendor_cap_calls
        else:
            cap_calls = 0
        total_llm_calls = archimate_llm_calls + cap_calls

        avg_input_tokens = 2000
        # Output tokens proportional to elements for archimate layers
        total_input_tokens = total_llm_calls * avg_input_tokens
        total_output_tokens = total_layer_elements * 50  # ~50 tokens per element
        if map_capabilities:
            total_output_tokens += apps_to_analyze * 2 * 1000  # ~1000 per cap/apqc call

        # Time estimate: ~3 seconds per LLM call average
        estimated_seconds = total_llm_calls * 3
        estimated_minutes = round(estimated_seconds / 60, 1)

        # Calculate cost using LLM cost tracker
        cost_tracker = LLMCostTracker()
        estimated_cost = cost_tracker._calculate_cost(
            provider="openai",
            model="gpt-4o",
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        )

        # Derive per-token rates for client-side recalculation
        input_rate = (
            float(
                cost_tracker._calculate_cost(
                    provider="openai",
                    model="gpt-4o",
                    input_tokens=DEFAULT_TOKEN_RATE_DIVISOR,
                    output_tokens=0,
                )
            )
            / DEFAULT_TOKEN_RATE_DIVISOR
        )
        output_rate = (
            float(
                cost_tracker._calculate_cost(
                    provider="openai",
                    model="gpt-4o",
                    input_tokens=0,
                    output_tokens=DEFAULT_TOKEN_RATE_DIVISOR,
                )
            )
            / DEFAULT_TOKEN_RATE_DIVISOR
        )

        # Calculate range (±30% for uncertainty)
        from decimal import Decimal

        min_cost = estimated_cost * Decimal("0.7")
        max_cost = estimated_cost * Decimal("1.3")

        # Validation preview: simulate typical LLM output quality issues
        validation_preview = None
        if generate_archimate and apps_to_analyze > 0:
            from app.services.archimate_validator import ArchiMateValidator

            validator = ArchiMateValidator()

            # Simulate validation of typical LLM-generated elements
            sample_elements = [
                {"type": "BusinessProcess", "name": "Manage Customer Data"},
                {"type": "BusinessService", "name": "Customer Management Service"},
                {"type": "ApplicationComponent", "name": "CRM System"},
            ]

            total_violations = 0
            error_count = 0
            warning_count = 0

            for elem in sample_elements:
                result = validator.validate_element(
                    element_type=elem["type"], name=elem["name"]
                )
                total_violations += len(result.violations)
                error_count += len(result.get_errors())
                warning_count += len(result.get_warnings())

            # Estimate validation risk for full batch
            estimated_violations = total_violations * (
                apps_to_analyze / 3
            )  # scale by app count

            validation_preview = {
                "risk_level": "high"
                if estimated_violations > apps_to_analyze * 2
                else "medium"
                if estimated_violations > apps_to_analyze
                else "low",
                "estimated_violations": round(estimated_violations),
                "estimated_errors": round(error_count * (apps_to_analyze / 3)),
                "estimated_warnings": round(warning_count * (apps_to_analyze / 3)),
                "confidence_score": round(
                    max(0.5, 1.0 - (estimated_violations / (apps_to_analyze * 10))), 2
                ),
                "recommendation": "human_review"
                if estimated_violations > apps_to_analyze * 2
                else "proceed_with_caution"
                if estimated_violations > apps_to_analyze
                else "proceed",
            }

        # Check if using free provider
        from app.services.llm_service import LLMService

        config_status = LLMService.configuration_status()

        # Check if using paid provider (any provider in the list that charges API fees)
        paid_providers = ["openai", "anthropic", "azure", "gemini"]
        providers_list = config_status.get("providers", [])
        using_paid_provider = any(p in paid_providers for p in providers_list)

        # Convert Decimal to float for JSON serialization
        estimated_cost_float = float(estimated_cost)
        min_cost_float = float(min_cost)
        max_cost_float = float(max_cost)

        return jsonify(
            {
                "success": True,
                "estimated_cost_gbp": round(estimated_cost_float, 4),
                "cost_range": {
                    "min": round(min_cost_float, 4),
                    "max": round(max_cost_float, 4),
                },
                "applications_count": apps_to_analyze,
                "total_tokens": {
                    "input": total_input_tokens,
                    "output": total_output_tokens,
                    "total": total_input_tokens + total_output_tokens,
                },
                "total_llm_calls": total_llm_calls,
                "estimated_time": {
                    "seconds": estimated_seconds,
                    "minutes": estimated_minutes,
                    "display": f"~{estimated_minutes} min"
                    if estimated_minutes >= 1
                    else f"~{estimated_seconds} sec",
                },
                "generation_mode": generation_mode,
                "cost_per_application": round(estimated_cost_float / apps_to_analyze, 4)
                if apps_to_analyze > 0
                else 0,
                "using_paid_provider": using_paid_provider,
                "provider": providers_list[0] if providers_list else "none",
                "message": "Free (local models)"
                if not using_paid_provider
                else f"\u00a3{round(estimated_cost_float, 4)} GBP estimated",
                "elements_estimate": elements_estimate,
                "validation_preview": validation_preview,
                "token_rates": {
                    "input_per_token": input_rate,
                    "output_per_token": output_rate,
                    "input_per_call": avg_input_tokens,
                    "output_per_element": 50,
                    "seconds_per_call": 3,
                },
                "options": {
                    "map_capabilities": map_capabilities,
                    "generate_archimate": generate_archimate,
                },
                "vendor_analysis": {
                    "unique_vendors": unique_vendors,
                    "apps_with_vendor": apps_with_vendor,
                    "apps_without_vendor": no_vendor_count,
                    "vendor_groups": {
                        name: [a["name"] for a in vapps]
                        for name, vapps in vendor_groups.items()
                    },
                    "vendor_shared_calls": unique_vendors * enabled_layers,
                    "calls_without_optimization": apps_to_analyze * enabled_layers,
                    "calls_with_optimization": archimate_llm_calls,
                    "calls_saved": (apps_to_analyze * enabled_layers)
                    - archimate_llm_calls,
                },
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error estimating auto-map cost: {e}")
        return jsonify({"error": "An internal error occurred"}), 500
