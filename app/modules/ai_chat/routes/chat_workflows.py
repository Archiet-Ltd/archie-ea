"""
Chat-driven architect workflow routes (chat/* URL prefix).

Routes: chat/generate-archimate, chat/generate-archimate-description,
        chat/map-apqc, chat/save-insights,
        chat/bulk-process, chat/gap-analysis, chat/discover-vendors.
"""

import logging
from datetime import datetime

from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from app import db
from app.decorators import audit_log
from app.services.rate_limiter import rate_limit
from app.models.application_portfolio import ApplicationComponent
from . import unified_ai_chat_bp

# Baseline annual infrastructure cost used for rough vendor TCO estimates when
# calculate_tco is requested (discover-vendors). Mirrors the constant in
# document_routes.py.
DEFAULT_BASE_INFRASTRUCTURE_COST = 100000  # fabricated-ok: declared estimation baseline constant, not a measured value

logger = logging.getLogger(__name__)

@unified_ai_chat_bp.route("/chat/generate-archimate", methods=["POST"])
@login_required
@rate_limit(20, "1h")
@audit_log("generate_archimate_for_application")
def generate_archimate_for_application():
    """
    Generate ArchiMate 3.2 elements for an application via chat command.

    Expected JSON:
    {
        "application_id": 123,
        "preview_only": true,  // If true, returns suggestions without saving
        "element_types": ["ApplicationComponent", "ApplicationService", "BusinessProcess"]
    }
    """
    try:
        from app.services.application_architecture_mapper import (
            ApplicationArchitectureMapperService,
        )

        data = request.json or {}
        application_id = data.get("application_id")
        preview_only = data.get("preview_only", True)

        if not application_id:
            return jsonify(
                {"success": False, "error": "application_id is required"}
            ), 400

        # Get application
        app = ApplicationComponent.query.get(application_id)
        if not app:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Application {application_id} not found",
                    }
                ),
                404,
            )

        if preview_only:
            # Return AI analysis suggestions without saving
            analysis = (
                ApplicationArchitectureMapperService.analyze_application_comprehensive(
                    application_id=application_id,
                    map_capabilities=True,
                    map_processes=True,
                    generate_archimate=True,
                )
            )

            # Format for chat display with confidence scores
            elements = []
            for elem in analysis.get("archimate_suggestions", []):
                elements.append(
                    {
                        "type": elem.get("type", "ApplicationComponent"),
                        "name": elem.get("name", ""),
                        "description": elem.get("description", ""),
                        "confidence": 0.85,  # Default confidence
                        "reasoning": elem.get("reasoning", ""),
                        "status": "suggested",
                    }
                )

            return jsonify(
                {
                    "success": True,
                    "application_name": app.name,
                    "preview": True,
                    "elements": elements,
                    "capability_suggestions": analysis.get(
                        "capability_suggestions", []
                    ),
                    "process_suggestions": analysis.get("process_suggestions", []),
                    "message": f"Generated {len(elements)} ArchiMate element suggestions for {app.name}. Click 'Apply' to save.",
                }
            )
        else:
            # Actually generate and save elements
            result = (
                ApplicationArchitectureMapperService.generate_archimate_for_application(
                    application_id=application_id,
                    created_by=current_user.email if current_user else None,
                )
            )

            return jsonify(
                {
                    "success": True,
                    "application_name": app.name,
                    "preview": False,
                    "elements_created": result.get("elements_created", 0),
                    "elements": result.get("elements", []),
                    "architecture_id": result.get("architecture_id"),
                    "message": f"Created {result.get('elements_created', 0)} ArchiMate elements for {app.name}",
                }
            )

    except Exception as e:
        current_app.logger.error(f"Error generating ArchiMate: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/chat/map-apqc", methods=["POST"])
@login_required
@rate_limit(20, "1h")
@audit_log("map_apqc_for_application")
def map_apqc_for_application():
    """
    Map APQC PCF processes for an application via chat command.

    Expected JSON:
    {
        "application_id": 123,
        "preview_only": true,
        "apply_high_confidence": false  // Auto-apply matches > 85%
    }
    """
    try:
        from app.services.application_architecture_mapper import (
            ApplicationArchitectureMapperService,
        )
        from app.services.apqc_classification_service import classify_apqc_text

        data = request.json or {}
        application_id = data.get("application_id")
        preview_only = data.get("preview_only", True)
        apply_high_confidence = data.get("apply_high_confidence", False)

        if not application_id:
            return jsonify(
                {"success": False, "error": "application_id is required"}
            ), 400

        app = ApplicationComponent.query.get(application_id)
        if not app:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Application {application_id} not found",
                    }
                ),
                404,
            )

        # Build searchable text from application
        search_text = " ".join(
            filter(
                None,
                [
                    app.name,
                    app.description,
                    app.imported_capabilities or "",
                    app.business_domain or "",
                    app.business_purpose or "",
                ],
            )
        )

        # Get APQC classifications
        apqc_results = classify_apqc_text(search_text, max_results=10)

        # Also get keyword-based mappings
        keyword_codes = ApplicationArchitectureMapperService.extract_apqc_processes_from_application(
            app
        )

        # Combine results
        all_mappings = []
        seen_codes = set()

        # Add semantic matches
        for result in apqc_results:
            code = result.get("process_code", "")
            if code and code not in seen_codes:
                seen_codes.add(code)
                all_mappings.append(
                    {
                        "process_code": code,
                        "process_name": result.get("process_name", ""),
                        "confidence": result.get("score", 0.7),
                        "source": result.get("source", "semantic"),
                        "status": "suggested",
                    }
                )

        # Add keyword matches - batch prefetch all matching APQC processes
        from app.models.apqc_process import APQCProcess

        # Build a single query for all keyword codes using OR conditions
        unseen_keyword_codes = [c for c in keyword_codes if c not in seen_codes]
        keyword_process_map = {}
        if unseen_keyword_codes:
            from sqlalchemy import or_ as sql_or

            code_filters = [
                APQCProcess.process_code.like(f"{c}%") for c in unseen_keyword_codes
            ]
            matching_processes = APQCProcess.query.filter(sql_or(*code_filters)).all()
            # Map each code to the first matching process
            for c in unseen_keyword_codes:
                for p in matching_processes:
                    if p.process_code.startswith(c):
                        keyword_process_map[c] = p
                        break

        for code in keyword_codes:
            if code not in seen_codes:
                seen_codes.add(code)
                process = keyword_process_map.get(code)
                if process:
                    all_mappings.append(
                        {
                            "process_code": process.process_code,
                            "process_name": process.process_name,
                            "confidence": 0.8,
                            "source": "keyword_analysis",
                            "status": "suggested",
                        }
                    )

        # Sort by confidence
        all_mappings.sort(key=lambda x: x.get("confidence", 0), reverse=True)

        created_count = 0
        if not preview_only or apply_high_confidence:
            # Apply mappings
            threshold = 0.85 if apply_high_confidence else 0.0
            mappings_to_apply = [
                m for m in all_mappings if m.get("confidence", 0) >= threshold
            ]

            if mappings_to_apply:
                codes_str = ", ".join([m["process_code"] for m in mappings_to_apply])
                created = ApplicationArchitectureMapperService.map_apqc_from_codes(
                    application_id=application_id,
                    apqc_codes=codes_str,
                    created_by=current_user.email if current_user else None,
                )
                created_count = len(created)

                # Update status
                for m in all_mappings:
                    if m["process_code"] in codes_str:
                        m["status"] = "applied"

        return jsonify(
            {
                "success": True,
                "application_name": app.name,
                "preview": preview_only and not apply_high_confidence,
                "mappings": all_mappings,
                "mappings_applied": created_count,
                "high_confidence_count": len(
                    [m for m in all_mappings if m.get("confidence", 0) >= 0.85]
                ),
                "message": f"Found {len(all_mappings)} APQC process matches for {app.name}. {created_count} applied.",
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error mapping APQC: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/chat/save-insights", methods=["POST"])
@login_required
@audit_log("save_chat_insights_to_application")
def save_chat_insights_to_application():
    """
    Save AI chat insights to an application record.

    Expected JSON:
    {
        "application_id": 123,
        "insights": {
            "description": "Enhanced description from chat",
            "imported_capabilities": "Capability 1, Capability 2",
            "technology_stack": "Python, PostgreSQL",
            "business_domain": "Finance"
        }
    }
    """
    try:
        data = request.json or {}
        application_id = data.get("application_id")
        insights = data.get("insights", {})

        if not application_id:
            return jsonify(
                {"success": False, "error": "application_id is required"}
            ), 400

        if not insights:
            return jsonify(
                {"success": False, "error": "insights object is required"}
            ), 400

        app = ApplicationComponent.query.get(application_id)
        if not app:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Application {application_id} not found",
                    }
                ),
                404,
            )

        # Allowed fields to update
        allowed_fields = [
            "description",
            "imported_capabilities",
            "technology_stack",
            "business_domain",
            "business_purpose",
            "integration_notes",
            "notes",
            "application_services",
            "application_functions_text",
        ]

        updated_fields = []
        for field, value in insights.items():
            if (
                field in allowed_fields and hasattr(app, field)
            ):  # model-safety-ok: allowed_fields includes integration_notes which may not exist on ApplicationComponent
                setattr(app, field, value)
                updated_fields.append(field)

        if updated_fields:
            app.updated_at = datetime.utcnow()
            db.session.commit()

        return jsonify(
            {
                "success": True,
                "application_id": application_id,
                "application_name": app.name,
                "fields_updated": updated_fields,
                "message": f"Updated {len(updated_fields)} fields for {app.name}",
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error saving insights: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/chat/bulk-process", methods=["POST"])
@login_required
@rate_limit(5, "1h")
@audit_log("bulk_process_applications")
def bulk_process_applications():
    """
    Bulk process multiple applications for ArchiMate and APQC mapping.

    Expected JSON:
    {
        "application_ids": [1, 2, 3],  // Or omit to process all
        "max_applications": 50,
        "map_capabilities": true,
        "map_processes": true,
        "generate_archimate": false,
        "confidence_threshold": 0.7,
        "auto_create": false
    }
    """
    try:
        from app.services.application_architecture_mapper import (
            ApplicationArchitectureMapperService,
        )

        data = request.json or {}
        data.get("application_ids")
        max_applications = data.get("max_applications", 50)

        result = ApplicationArchitectureMapperService.bulk_auto_map(
            max_applications=max_applications,
            map_capabilities=data.get("map_capabilities", True),
            map_processes=data.get("map_processes", True),
            generate_archimate=data.get("generate_archimate", False),
            clone_vendor_archimate=data.get("clone_vendor_archimate", False),
            confidence_threshold=data.get("confidence_threshold", 0.7),
            auto_create=data.get("auto_create", False),
            created_by=current_user.email if current_user else None,
        )

        return jsonify(
            {
                "success": True,
                "total_processed": result.get("total_analyzed", 0),
                "capability_mappings_created": result.get(
                    "capability_mappings_created", 0
                ),
                "process_mappings_created": result.get("process_mappings_created", 0),
                "archimate_elements_created": result.get(
                    "archimate_elements_created", 0
                ),
                "applications": result.get("applications", [])[
                    :10
                ],  # Limit response size
                "message": f"Processed {result.get('total_analyzed', 0)} applications",
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error in bulk processing: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/chat/gap-analysis", methods=["POST"])
@login_required
@rate_limit(20, "1h")
@audit_log("actionable_gap_analysis")
def actionable_gap_analysis():
    """
    Perform gap analysis with actionable outputs.

    Expected JSON:
    {
        "analysis_type": "capability",  // capability, vendor, or process
        "create_roadmap_items": false
    }
    """
    try:
        data = request.json or {}
        analysis_type = data.get("analysis_type", "capability")
        data.get("create_roadmap_items", False)

        gaps = []

        if analysis_type == "capability":
            # Find capabilities without supporting applications
            from app.models.unified_application_capability_mapping import (
                UnifiedApplicationCapabilityMapping,
            )
            from app.models.unified_capability import UnifiedCapability

            # Don't filter by status="defined": production capabilities carry a
            # NULL status, so that filter silently returned 0 capabilities and the
            # analysis always reported "0 gaps" despite ~65 genuinely uncovered
            # capabilities. Analyse every capability that isn't explicitly retired.
            all_caps = UnifiedCapability.query.filter(
                db.or_(
                    UnifiedCapability.status.is_(None),
                    ~UnifiedCapability.status.in_(["retired", "deprecated", "archived"]),
                )
            ).all()
            mapped_cap_ids = set(
                m.unified_capability_id
                for m in UnifiedApplicationCapabilityMapping.query.all()
            )

            for cap in all_caps:
                if cap.id not in mapped_cap_ids:
                    gaps.append(
                        {
                            "type": "capability_gap",
                            "severity": "high"
                            if cap.business_criticality in ("mission_critical", "critical")
                            else "medium",
                            "name": cap.name,
                            "capability_id": cap.id,
                            "description": f"No application supports capability: {cap.name}",
                            "recommendation": "Evaluate build vs. buy options or vendor solutions",
                            "actions": [
                                "create_roadmap_item",
                                "find_vendors",
                                "evaluate_options",
                            ],
                        }
                    )

        elif analysis_type == "vendor":
            # Find single-vendor dependencies
            from sqlalchemy import func

            vendor_counts = (
                db.session.query(
                    ApplicationComponent.vendor_name,
                    func.count(ApplicationComponent.id).label("app_count"),
                )
                .filter(ApplicationComponent.vendor_name.isnot(None))
                .group_by(ApplicationComponent.vendor_name)
                .having(func.count(ApplicationComponent.id) > 5)
                .all()
            )

            for vendor_name, count in vendor_counts:
                if vendor_name:
                    gaps.append(
                        {
                            "type": "vendor_concentration",
                            "severity": "high" if count > 10 else "medium",
                            "name": f"High dependency on {vendor_name}",
                            "vendor_name": vendor_name,
                            "application_count": count,
                            "description": f"{count} applications depend on {vendor_name}",
                            "recommendation": "Consider diversifying vendor portfolio",
                            "actions": [
                                "create_roadmap_item",
                                "find_alternatives",
                                "risk_assessment",
                            ],
                        }
                    )

        elif analysis_type == "process":
            # Find APQC processes without application support
            from app.models.apqc_process import APQCProcess

            # Hierarchy level <= 3 in SQL terms: process_code has at most 3
            # dot-separated segments (level 4+ has 3+ dots, e.g. "4.1.1.1").
            # APQCProcess.level is a computed property (no column), so it can't
            # be used in a query filter directly.
            all_processes = (
                APQCProcess.query.filter(
                    APQCProcess.process_code.isnot(None),
                    ~APQCProcess.process_code.like("%.%.%.%"),
                )
                .limit(100)
                .all()
            )
            # ProcessApplicationMapping has no organization_id of its own --
            # fence through ApplicationComponent (its owning application),
            # or every organisation's mapped processes count as "mapped"
            # here, hiding real gaps for the caller's own organisation
            # (pr306-v1 review, DEFECT D4).
            from app.services.apqc_mapping_tenant_fence import (
                fenced_application_mappings_query,
            )

            mapped_process_ids = set(
                m.apqc_process_id for m in fenced_application_mappings_query().all()
            )

            for proc in all_processes:
                if proc.id not in mapped_process_ids:
                    gaps.append(
                        {
                            "type": "process_gap",
                            "severity": "medium",
                            "name": proc.process_name,
                            "process_code": proc.process_code,
                            "process_id": proc.id,
                            "description": f"No application supports process: {proc.process_code} {proc.process_name}",
                            "recommendation": "Identify or implement supporting application",
                            "actions": [
                                "create_roadmap_item",
                                "find_vendors",
                                "evaluate_options",
                            ],
                        }
                    )

        # Limit and sort by severity
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        gaps.sort(key=lambda x: severity_order.get(x.get("severity", "low"), 3))
        gaps = gaps[:20]  # Limit to top 20 gaps

        return jsonify(
            {
                "success": True,
                "analysis_type": analysis_type,
                "total_gaps": len(gaps),
                "critical_gaps": len(
                    [g for g in gaps if g.get("severity") in ["critical", "high"]]
                ),
                "gaps": gaps,
                "message": f"Found {len(gaps)} gaps in {analysis_type} analysis",
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error in gap analysis: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/chat/discover-vendors", methods=["POST"])
@login_required
@rate_limit(20, "1h")
@audit_log("discover_vendors_for_capability")
def discover_vendors_for_capability():
    """
    Discover vendors for a capability requirement via chat.

    Expected JSON:
    {
        "capability_name": "Customer Relationship Management",
        "capability_id": 123,  // Optional
        "organization_size": "medium",
        "budget_max": 500000,
        "calculate_tco": true
    }
    """
    try:
        from app.models.vendor.vendor_organization import (
            VendorOrganization,
            VendorProduct,
        )

        data = request.json or {}
        capability_name = data.get("capability_name", "")
        capability_id = data.get("capability_id")
        data.get("organization_size", "medium")
        calculate_tco = data.get("calculate_tco", False)

        if not capability_name and not capability_id:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "capability_name or capability_id required",
                    }
                ),
                400,
            )

        # Search for matching vendors
        search_term = capability_name.lower() if capability_name else ""

        # Find vendors with matching products
        matching_vendors = []

        # Search in vendor products. VendorProduct (vendor.vendor_organization)
        # has no 'description'/'product_category' columns and its vendor FK is
        # 'vendor_organization_id' — match against the real text columns instead.
        products = (
            VendorProduct.query.filter(
                db.or_(
                    VendorProduct.name.ilike(f"%{search_term}%"),
                    VendorProduct.product_type.ilike(f"%{search_term}%"),
                    VendorProduct.product_family_name.ilike(f"%{search_term}%"),
                    VendorProduct.functional_scope.ilike(f"%{search_term}%"),
                )
            )
            .limit(20)
            .all()
        )

        seen_vendor_ids = set()
        for product in products:
            if product.vendor_organization_id not in seen_vendor_ids:
                seen_vendor_ids.add(product.vendor_organization_id)
                vendor = VendorOrganization.query.get(product.vendor_organization_id)
                if vendor:
                    vendor_info = {
                        "vendor_id": vendor.id,
                        "vendor_name": vendor.name,
                        "products": [],
                        "capability_fit": 0.85,  # Default fit score
                        "tco_estimate": None,
                    }

                    # Get all matching products for this vendor
                    vendor_products = [
                        p for p in products if p.vendor_organization_id == vendor.id
                    ]
                    for vp in vendor_products:
                        vendor_info["products"].append(
                            {
                                "product_id": vp.id,
                                "product_name": vp.name,
                                "category": vp.product_type,
                            }
                        )

                    # Calculate simple TCO estimate if requested
                    if calculate_tco:
                        # Simple estimate: $50 - 200 per user per year for 1000 users
                        base_cost = DEFAULT_BASE_INFRASTRUCTURE_COST
                        vendor_info["tco_estimate"] = {
                            "annual": base_cost,
                            "three_year": base_cost * 3,
                            "five_year": base_cost * 5,
                            "currency": "USD",
                        }

                    matching_vendors.append(vendor_info)

        # Sort by fit score
        matching_vendors.sort(key=lambda x: x.get("capability_fit", 0), reverse=True)

        return jsonify(
            {
                "success": True,
                "capability_searched": capability_name,
                "vendors_found": len(matching_vendors),
                "vendors": matching_vendors[:10],  # Top 10
                "message": f"Found {len(matching_vendors)} vendors for '{capability_name}'",
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error discovering vendors: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500



# A95-013: NL-driven solution diagram creation
@unified_ai_chat_bp.route("/chat/create-solution-diagram", methods=["POST"])
@login_required
def create_solution_diagram():
    """Create a SavedDiagram from a solution's ArchiMate elements and return composer URL.

    Request JSON: { "solution_id": int, "solution_name": str (optional) }
    Response JSON: { "success": bool, "diagram_id": int, "redirect_url": str, "element_count": int }
    """
    try:
        data = request.get_json(silent=True) or {}
        solution_id = data.get("solution_id")

        if not solution_id:
            return jsonify({"success": False, "error": "solution_id is required"}), 400

        from app.models.solution_models import Solution
        solution = Solution.query.get(solution_id)
        if not solution:
            return jsonify({"success": False, "error": f"Solution {solution_id} not found"}), 404

        from app.services.solution_archimate_service import SolutionArchiMateService
        svc = SolutionArchiMateService()
        elements = svc.get_elements_for_solution(solution_id)

        # Auto-layout: arrange by layer in rows
        LAYER_ORDER = [
            "motivation", "strategy", "business",
            "application", "technology", "physical", "implementation",
        ]
        ELEMENT_W, ELEMENT_H, GAP_X, GAP_Y, START_X, START_Y = 180, 64, 20, 80, 60, 60
        MAX_COLS = 5

        layer_groups = {}
        for el in elements:
            layer = (_layer_for_type(el.get("type", "")) if not el.get("layer") else el["layer"]).lower()
            layer_groups.setdefault(layer, []).append(el)

        laid_out = []
        current_y = START_Y
        for layer_name in LAYER_ORDER + sorted(set(layer_groups.keys()) - set(LAYER_ORDER)):
            group = layer_groups.get(layer_name, [])
            if not group:
                continue
            current_x = START_X
            col = 0
            for el in group:
                laid_out.append({
                    "element": el,
                    "x": current_x,
                    "y": current_y,
                    "width": ELEMENT_W,
                    "height": ELEMENT_H,
                })
                col += 1
                current_x += ELEMENT_W + GAP_X
                if col >= MAX_COLS:
                    col = 0
                    current_x = START_X
                    current_y += ELEMENT_H + GAP_Y
            current_y += ELEMENT_H + GAP_Y * 2

        from app.models.archimate_core import SavedDiagram, SavedDiagramElement
        diagram_name = f"{solution.name} \u2014 Architecture"
        diag = SavedDiagram(
            name=diagram_name,
            solution_id=solution_id,
            viewpoint_type="Application Usage",
            created_by=current_user.id if current_user else None,
        )
        db.session.add(diag)
        db.session.flush()

        added = 0
        for item in laid_out:
            el = item["element"]
            el_id = el.get("id")
            if not el_id:
                continue
            pos = SavedDiagramElement(
                diagram_id=diag.id,
                element_id=el_id,
                position_x=item["x"],
                position_y=item["y"],
                width=item["width"],
                height=item["height"],
                rendering_mode="black_box",
            )
            db.session.add(pos)
            added += 1

        db.session.commit()

        redirect_url = f"/archimate/composer?viewpoint_id={diag.id}&solution_id={solution_id}"
        return jsonify({
            "success": True,
            "diagram_id": diag.id,
            "diagram_name": diagram_name,
            "redirect_url": redirect_url,
            "element_count": added,
            "message": f"Created diagram '{diagram_name}' with {added} elements. Opening composer\u2026",
        })

    except Exception as e:
        current_app.logger.error(f"A95-013 create_solution_diagram error: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to create diagram. Please try again."}), 500


@unified_ai_chat_bp.route("/architect/analyze", methods=["POST"])
@login_required
@rate_limit(5, "1h")
@audit_log("architect_full_analysis")
def architect_analyze():
    """
    A95-016: One-shot full TOGAF ADM analysis from a business problem statement.

    Request JSON:
        { "problem_statement": "Modernise customer onboarding..." }

    Response JSON (200):
        {
            "solution_id": 1,
            "reasoning_trail": [...],
            "phases": {
                "scope": {...}, "capabilities": {...}, "gaps": {...},
                "options": {...}, "roadmap": {...}, "arb_draft": {...},
                "archimate": {"element_count": 47, ...}
            }
        }

    Errors:
        400 — missing problem_statement (< 10 chars)
        500 — internal orchestration error
    """
    try:
        data = request.get_json(silent=True) or {}
        problem_statement = (data.get("problem_statement") or "").strip()
        if len(problem_statement) < 10:
            return jsonify({
                "success": False,
                "error": "problem_statement must be at least 10 characters"
            }), 400

        from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import (
            SolutionAIOrchestrator,
        )
        user_id = current_user.id if current_user and current_user.is_authenticated else 1
        orchestrator = SolutionAIOrchestrator()
        result = orchestrator.full_architect_analysis(problem_statement, user_id=user_id)

        return jsonify({
            "success": True,
            "solution_id": result.get("solution_id"),
            "reasoning_trail": result.get("reasoning_trail", []),
            "phases": result.get("phases", {}),
        })

    except Exception as e:
        current_app.logger.error(f"A95-016 architect_analyze error: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Analysis failed. Please try again."}), 500


@unified_ai_chat_bp.route("/architect/approve-phase", methods=["POST"])
@login_required
@rate_limit(20, "1h")
@audit_log("approve_architect_phase")
def approve_architect_phase():
    """
    A95-019: Approve a TOGAF ADM phase card from the architect analysis UI.

    Request JSON: {"solution_id": int, "phase_name": str}
    Response: {"success": true, "phase_name": str, "approved": true}
    """
    from app.models.solution_models import Solution

    try:
        data = request.get_json(silent=True) or {}
        solution_id = data.get("solution_id")
        phase_name = data.get("phase_name", "").strip()

        if not solution_id or not phase_name:
            return jsonify({
                "success": False,
                "error": "solution_id and phase_name are required",
            }), 400

        solution = Solution.query.get(solution_id)
        if not solution:
            return jsonify({"success": False, "error": "Solution not found"}), 404

        valid_phases = [
            "scope", "capabilities", "gaps", "options",
            "roadmap", "arb_draft", "archimate",
        ]
        if phase_name not in valid_phases:
            return jsonify({
                "success": False,
                "error": f"Invalid phase_name. Must be one of: {', '.join(valid_phases)}",
            }), 400

        logger.info(
            "A95-019: Phase '%s' approved for solution %s by user %s",
            phase_name, solution_id,
            current_user.id if current_user.is_authenticated else "anon",
        )

        return jsonify({
            "success": True,
            "phase_name": phase_name,
            "approved": True,
            "solution_id": solution_id,
        })

    except Exception as e:
        current_app.logger.error(f"A95-019 approve_phase error: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to approve phase"}), 500


# A95-017: Generate 4 stakeholder viewpoints from a solution in one call
@unified_ai_chat_bp.route("/architect/viewpoints", methods=["POST"])
@login_required
@rate_limit(10, "1h")
@audit_log("architect_viewpoints")
def architect_viewpoints():
    """
    A95-017: Generate 4 stakeholder viewpoints from a solution's ArchiMate elements.

    For each of 4 standard stakeholder viewpoints (Stakeholder/CIO, Application/Architect,
    Technology/Infrastructure, Implementation/Delivery), filter the solution's ArchiMate
    elements by allowed layers and create a SavedDiagram (via
    app.services.archimate_composer_service.create_diagram) so the result is a
    real, openable, tenant-scoped diagram in the composer's own saved list.

    Request JSON:
        { "solution_id": int }

    Response JSON (200):
        {
            "success": true,
            "viewpoints": [
                {
                    "type": "stakeholder",
                    "name": "Stakeholder Viewpoint",
                    "saved_diagram_id": 42,
                    "composer_url": "/archimate/composer?viewpoint_id=42"
                },
                ...
            ]
        }

    When a viewpoint matches zero elements (or diagram creation otherwise
    fails), "saved_diagram_id" and "composer_url" are both null — never a
    placeholder link to a diagram that does not exist.

    Errors:
        400 — missing solution_id
        404 — solution not found
        500 — internal error
    """
    # 4 standard stakeholder viewpoints: type, display name, layers to include
    VIEWPOINT_DEFINITIONS = [
        {
            "type": "stakeholder",
            "name": "Stakeholder Viewpoint",
            "stakeholders": ["CIO", "Business Owner", "Executive"],
            "layers": {"business", "motivation", "strategy"},
        },
        {
            "type": "application",
            "name": "Application Usage Viewpoint",
            "stakeholders": ["Enterprise Architect", "Solution Architect"],
            "layers": {"application", "business"},
        },
        {
            "type": "technology",
            "name": "Technology Viewpoint",
            "stakeholders": ["Infrastructure Architect", "IT Manager", "Infrastructure Engineer"],
            "layers": {"technology", "physical", "application"},
        },
        {
            "type": "implementation",
            "name": "Implementation and Delivery Viewpoint",
            "stakeholders": ["Project Manager", "Delivery Manager", "Implementation Manager"],
            "layers": {"implementation", "technology", "application"},
        },
    ]

    try:
        data = request.get_json(silent=True) or {}
        solution_id = data.get("solution_id")

        if not solution_id:
            return jsonify({"success": False, "error": "solution_id is required"}), 400

        from app.models.solution_models import Solution
        from app.models.solution_archimate_element import SolutionArchiMateElement
        from app.models.archimate_core import ArchiMateElement
        from app.services.archimate_composer_service import create_diagram

        solution = Solution.query.get(solution_id)
        if not solution:
            return jsonify({"success": False, "error": f"Solution {solution_id} not found"}), 404

        # Load all ArchiMate elements linked to this solution
        junctions = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
        element_ids = [j.element_id for j in junctions]

        # Fetch element records (id → layer) in one query
        elements_by_id = {}
        if element_ids:
            arch_elements = ArchiMateElement.query.filter(
                ArchiMateElement.id.in_(element_ids)
            ).all()
            for el in arch_elements:
                elements_by_id[el.id] = el

        result_viewpoints = []

        for vp_def in VIEWPOINT_DEFINITIONS:
            allowed_layers = vp_def["layers"]

            # Filter element IDs whose layer falls within this viewpoint's allowed layers
            filtered_ids = [
                eid for eid, el in elements_by_id.items()
                if (el.layer or "").lower() in allowed_layers
            ]

            try:
                composer_url = create_diagram(
                    filtered_ids,
                    name=f"{solution.name} — {vp_def['name']}",
                    created_by=current_user.id if current_user and current_user.is_authenticated else None,
                    solution_id=solution_id,
                    viewpoint_type=vp_def["type"],
                )
            except Exception as create_err:
                current_app.logger.warning(
                    f"A95-017: failed to create SavedDiagram for {vp_def['type']}: {create_err}"
                )
                composer_url = None

            saved_diagram_id = None
            if composer_url:
                try:
                    saved_diagram_id = int(composer_url.rsplit("=", 1)[-1])
                except (ValueError, IndexError):
                    saved_diagram_id = None

            result_viewpoints.append({
                "type": vp_def["type"],
                "name": vp_def["name"],
                "saved_diagram_id": saved_diagram_id,
                "composer_url": composer_url,
                "element_count": len(filtered_ids),
            })

        return jsonify({
            "success": True,
            "solution_id": solution_id,
            "solution_name": solution.name,
            "viewpoints": result_viewpoints,
        })

    except Exception as e:
        current_app.logger.error(f"A95-017 architect_viewpoints error: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to generate viewpoints. Please try again."}), 500


@unified_ai_chat_bp.route("/architect/arb-ready", methods=["POST"])
@login_required
@rate_limit(20, "1h")
@audit_log("arb_readiness_scoring")
def arb_readiness_scoring():
    """
    A95-018: ARB readiness scoring endpoint.

    Input JSON: {"solution_id": int}
    Returns: {"score": 0-100, "grade": "A"|"B"|"C"|"D",
              "blocking_issues": [{"criterion": str, "message": str, "severity": "blocking"|"advisory"}]}

    Scores against 4 criteria:
      1. Architectural principles alignment (40%) — scope, capabilities, description
      2. Technology standards alignment (30%) — linked apps in ApplicationComponent
      3. Risk coverage (20%) — risk register entries with mitigations
      4. Completeness (10%) — required SAD fields populated
    """
    from app.models.solution_models import Solution
    from app.models.solution_lifecycle_models import SolutionRisk

    try:
        try:
            data = request.get_json(silent=True) or {}
        except Exception:
            data = {}
        solution_id = data.get("solution_id")
        if not solution_id:
            return jsonify({"success": False, "error": "solution_id is required"}), 400

        solution = Solution.query.get(solution_id)
        if not solution:
            return jsonify({"success": False, "error": "Solution not found"}), 404

        blocking_issues = []

        # --- Criterion 1: Architectural principles alignment (40%) ---
        principles_score = 0
        has_description = bool(solution.description and len(solution.description.strip()) > 20)
        has_scope = bool(solution.scope_description and len(solution.scope_description.strip()) > 10)
        has_business_value = bool(solution.business_value and len(solution.business_value.strip()) > 10)
        has_domain = bool(solution.business_domain)

        principles_parts = sum([has_description, has_scope, has_business_value, has_domain])
        principles_score = int(40 * (principles_parts / 4))

        if not has_description:
            blocking_issues.append({
                "criterion": "Architectural Principles",
                "message": "Solution description is missing or too brief (min 20 chars)",
                "severity": "blocking",
            })
        if not has_scope:
            blocking_issues.append({
                "criterion": "Architectural Principles",
                "message": "Scope description is missing",
                "severity": "advisory",
            })

        # --- Criterion 2: Technology standards alignment (30%) ---
        tech_score = 0
        try:
            app_count = solution.applications.count()
        except TypeError:
            app_count = len(solution.applications) if solution.applications else 0

        if app_count > 0:
            tech_score = 30
        elif solution.in_scope_applications and len(solution.in_scope_applications) > 0:
            tech_score = 15  # partial — apps listed but not linked
            blocking_issues.append({
                "criterion": "Technology Standards",
                "message": "Applications listed in scope but not linked via junction table",
                "severity": "advisory",
            })
        else:
            blocking_issues.append({
                "criterion": "Technology Standards",
                "message": "No applications linked to solution",
                "severity": "blocking",
            })

        # --- Criterion 3: Risk coverage (20%) ---
        risk_score = 0
        risks = SolutionRisk.query.filter_by(solution_id=solution_id).all()

        if risks:
            mitigated = sum(1 for r in risks if r.mitigation and len(r.mitigation.strip()) > 5)
            ratio = mitigated / len(risks) if risks else 0
            risk_score = int(20 * ratio)
            if ratio < 0.5:
                blocking_issues.append({
                    "criterion": "Risk Coverage",
                    "message": f"Only {mitigated}/{len(risks)} risks have mitigations",
                    "severity": "advisory",
                })
        else:
            blocking_issues.append({
                "criterion": "Risk Coverage",
                "message": "No risks registered for this solution",
                "severity": "blocking",
            })

        # --- Criterion 4: Completeness (10%) ---
        completeness_score = 0
        required_fields = [
            solution.name,
            solution.description,
            solution.solution_type,
            solution.status,
            solution.solution_owner,
        ]
        filled = sum(1 for f in required_fields if f and str(f).strip())
        completeness_score = int(10 * (filled / len(required_fields)))

        if filled < len(required_fields):
            missing_names = []
            field_labels = ["name", "description", "solution_type", "status", "solution_owner"]
            for i, f in enumerate(required_fields):
                if not f or not str(f).strip():
                    missing_names.append(field_labels[i])
            blocking_issues.append({
                "criterion": "Completeness",
                "message": f"Missing fields: {', '.join(missing_names)}",
                "severity": "advisory",
            })

        # --- Total score and grade ---
        total_score = principles_score + tech_score + risk_score + completeness_score
        total_score = max(0, min(100, total_score))

        if total_score >= 85:
            grade = "A"
        elif total_score >= 70:
            grade = "B"
        elif total_score >= 50:
            grade = "C"
        else:
            grade = "D"

        return jsonify({
            "success": True,
            "score": total_score,
            "grade": grade,
            "blocking_issues": blocking_issues,
            "criteria": {
                "principles_alignment": {"score": principles_score, "max": 40},
                "technology_standards": {"score": tech_score, "max": 30},
                "risk_coverage": {"score": risk_score, "max": 20},
                "completeness": {"score": completeness_score, "max": 10},
            },
            "solution_id": solution_id,
            "solution_name": solution.name,
        })

    except Exception as e:
        current_app.logger.error(f"A95-018 arb_readiness_scoring error: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to score ARB readiness"}), 500


def _layer_for_type(element_type: str) -> str:
    """Map ArchiMate element type to layer name using the authoritative type→layer registry."""
    from app.modules.architecture.services.element_type_normalizer import ElementTypeNormalizer
    return ElementTypeNormalizer.infer_layer(element_type) or "application"


@unified_ai_chat_bp.route("/chat/generate-archimate-description", methods=["POST"])
@login_required
@rate_limit(10, "1h")
@audit_log("generate_archimate_from_description")
def generate_archimate_from_description():
    """
    Generate ArchiMate 3.2 elements from a freeform text description.

    Enables natural language ArchiMate generation without requiring an existing
    application record. Called when the user types e.g. "draw an ArchiMate
    diagram for a CRM system" in the AI Chat.

    Expected JSON:
    {
        "description": "A CRM system with contact management and sales pipeline"
    }
    """
    try:
        from app.modules.architecture.services.archimate_llm_service import ArchiMateLLMService

        data = request.json or {}
        description = (data.get("description") or "").strip()

        if not description:
            return jsonify({"success": False, "error": "description is required"}), 400
        if len(description) > 2000:
            return jsonify({"success": False, "error": "description must be 2000 characters or fewer"}), 400

        # ── Pass 0: Build portfolio context for LLM grounding ──
        # Query existing apps, capabilities, and elements matching the
        # description keywords so the LLM reuses portfolio data instead
        # of generating duplicates in a vacuum.
        portfolio_context = ""
        try:
            from app.modules.ai_chat.services.multi_domain_chat_service import MultiDomainChatService
            _chat_svc = MultiDomainChatService.__new__(MultiDomainChatService)
            # Extract keywords for search
            import re as _re
            keywords = [w for w in _re.findall(r'[a-zA-Z]{4,}', description.lower())
                        if w not in {'with', 'from', 'that', 'this', 'will', 'should',
                                     'include', 'using', 'system', 'generate', 'full',
                                     'complete', 'layers', 'layer', 'diagram', 'archimate'}][:6]

            if keywords:
                from app.models.application_layer import ApplicationComponent
                from app.models.archimate_core import ArchiMateElement
                from app.models.business_capabilities import BusinessCapability
                from sqlalchemy import or_

                # Find matching apps (top 15)
                app_filters = [ApplicationComponent.name.ilike(f"%{kw}%") for kw in keywords]
                matched_apps = ApplicationComponent.query.filter(or_(*app_filters)).limit(15).all()

                # Find matching ArchiMate elements (top 15)
                elem_filters = [ArchiMateElement.name.ilike(f"%{kw}%") for kw in keywords]
                matched_elems = ArchiMateElement.query.filter(or_(*elem_filters)).limit(15).all()

                # Find matching capabilities (top 10)
                cap_filters = [BusinessCapability.name.ilike(f"%{kw}%") for kw in keywords]
                matched_caps = BusinessCapability.query.filter(or_(*cap_filters)).limit(10).all()

                sections = []
                if matched_apps:
                    app_lines = [f"- {a.name} (lifecycle: {getattr(a, 'lifecycle_status', 'unknown')})" for a in matched_apps]
                    sections.append(f"EXISTING APPLICATIONS in portfolio ({len(matched_apps)} matches) — REUSE these as ApplicationComponent elements:\n" + "\n".join(app_lines))
                if matched_elems:
                    elem_lines = [f"- {e.name} ({e.type}, {e.layer or '?'} layer)" for e in matched_elems]
                    sections.append(f"EXISTING ARCHIMATE ELEMENTS ({len(matched_elems)} matches) — reference these by exact name:\n" + "\n".join(elem_lines))
                if matched_caps:
                    cap_lines = [f"- {c.name} (maturity: {getattr(c, 'current_maturity_level', '?')}/{getattr(c, 'target_maturity_level', '?')})" for c in matched_caps]
                    sections.append(f"EXISTING CAPABILITIES ({len(matched_caps)} matches) — use these instead of creating new:\n" + "\n".join(cap_lines))

                if sections:
                    portfolio_context = (
                        "IMPORTANT — EXISTING PORTFOLIO DATA (do NOT create duplicates of these):\n\n"
                        + "\n\n".join(sections)
                        + "\n\nWhen generating elements, use the EXACT names above for matching entities. "
                        "Only create NEW elements for things not already in the portfolio."
                    )
                    current_app.logger.info(
                        f"ENT-122: Portfolio context: {len(matched_apps)} apps, "
                        f"{len(matched_elems)} elements, {len(matched_caps)} capabilities"
                    )
        except Exception as e:
            current_app.logger.warning(f"ENT-122: Portfolio context loading failed (non-fatal): {e}")

        service = ArchiMateLLMService()
        model_data, _interaction = service.generate_archimate_from_requirements(
            requirements=description,
            context=portfolio_context,
            model_name="AI Chat Generated Architecture",
        )

        raw_elements = model_data.get("elements", [])
        raw_relationships = model_data.get("relationships", [])

        from app.modules.architecture.services.element_type_normalizer import ElementTypeNormalizer

        elements = []
        for e in raw_elements:
            if not e.get("name"):
                continue
            norm = ElementTypeNormalizer.normalize_element(e)
            if not norm.get("type"):
                continue
            elements.append({
                "type": norm["type"],
                "name": norm.get("name", ""),
                "description": norm.get("description", ""),
                "confidence": float(norm.get("confidence", 0.80)),
                "reasoning": norm.get("reasoning", ""),
                "status": "suggested",
                "layer": norm.get("layer", _layer_for_type(norm.get("type", ""))),
            })

        # Build element name → type lookup for relationship validation
        elem_types = {e["name"]: e["type"] for e in elements}

        # ── Validate relationships against full ArchiMate 3.2 matrix ──
        # Replaces ad-hoc pattern matching with exhaustive 182+ combination check.
        from app.modules.architecture.services.archimate_relationship_matrix import (
            validate_relationship as _validate_rel,
        )

        _rel_type_norm = {
            "realizes": "Realization", "realization": "Realization",
            "serves": "Serving", "serving": "Serving",
            "composition": "Composition", "aggregation": "Aggregation",
            "assignment": "Assignment", "access": "Access",
            "influence": "Influence", "triggering": "Triggering",
            "flow": "Flow", "specialization": "Specialization",
            "association": "Association",
        }

        relationships = []
        rejected_count = 0
        corrected_count = 0
        for r in raw_relationships:
            src = r.get("source", r.get("source_name", ""))
            tgt = r.get("target", r.get("target_name", ""))
            if not src or not tgt:
                continue
            raw_type = (r.get("type", "Association") or "Association").strip()
            rel_type = _rel_type_norm.get(raw_type.lower(), raw_type)

            src_type = elem_types.get(src, "")
            tgt_type = elem_types.get(tgt, "")

            if src_type and tgt_type:
                validation = _validate_rel(src_type, tgt_type, rel_type)
                if not validation["valid"]:
                    if validation["suggested"]:
                        rel_type = validation["suggested"]
                        corrected_count += 1
                    else:
                        rejected_count += 1
                        continue  # Drop invalid relationship entirely

            relationships.append({
                "type": rel_type,
                "source_name": src,
                "target_name": tgt,
            })

        if corrected_count or rejected_count:
            current_app.logger.info(
                f"ENT-122: ArchiMate matrix validation: {corrected_count} corrected, {rejected_count} rejected"
            )

        # ── Pass 2: Infer missing relationships from CANONICAL_CHAIN ──
        # LLMs often generate elements without connecting them. The canonical
        # chain defines which types MUST connect (e.g. Goal→Capability,
        # BusinessProcess→ApplicationService). For each pair of generated
        # elements that match a chain rule but have no relationship, add one.
        try:
            from app.modules.architecture.services.inference_rules_registry import CANONICAL_CHAIN

            connected_pairs = {(r["source_name"], r["target_name"]) for r in relationships}
            # Build type→names index
            type_to_names = {}
            for e in elements:
                type_to_names.setdefault(e["type"], []).append(e["name"])

            # Track which elements have at least one connection
            connected_names = set()
            for r in relationships:
                connected_names.add(r["source_name"])
                connected_names.add(r["target_name"])

            inferred_count = 0

            # Pass 1: REQUIRED chain links only (architecturally mandated)
            # These are connections that MUST exist per ArchiMate 3.2.
            for parent_type, child_type, meta in CANONICAL_CHAIN:
                if not meta.get("required"):
                    continue
                rel_type_canonical = meta["type"]
                rel_type_display = _rel_type_norm.get(rel_type_canonical, rel_type_canonical.title())
                parents = type_to_names.get(parent_type, [])
                children = type_to_names.get(child_type, [])
                if not parents or not children:
                    continue
                # For required links with multiple candidates, connect first match only
                # (not cartesian product — avoids over-connecting)
                for p_name in parents:
                    connected_this_parent = False
                    for c_name in children:
                        if (p_name, c_name) in connected_pairs:
                            connected_this_parent = True
                            break
                    if not connected_this_parent and children:
                        c_name = children[0]  # Connect to first available child
                        relationships.append({
                            "type": rel_type_display,
                            "source_name": p_name,
                            "target_name": c_name,
                            "inferred": True,
                        })
                        connected_pairs.add((p_name, c_name))
                        connected_names.add(p_name)
                        connected_names.add(c_name)
                        inferred_count += 1

            # Pass 2: ONE optional link per isolated element (rescue only)
            # Only connects elements that have ZERO connections after Pass 1.
            # Stops after one link per element — does NOT force full connectivity.
            isolated = [e["name"] for e in elements if e["name"] not in connected_names]
            for iso_name in isolated:
                iso_type = elem_types.get(iso_name, "")
                if not iso_type:
                    continue
                # Find one valid chain partner
                found = False
                for parent_type, child_type, meta in CANONICAL_CHAIN:
                    if found:
                        break
                    rel_type_canonical = meta["type"]
                    rel_type_display = _rel_type_norm.get(rel_type_canonical, rel_type_canonical.title())
                    if iso_type == parent_type:
                        candidates = type_to_names.get(child_type, [])
                        for c in candidates:
                            if (iso_name, c) not in connected_pairs:
                                relationships.append({"type": rel_type_display, "source_name": iso_name, "target_name": c, "inferred": True})
                                connected_pairs.add((iso_name, c))
                                connected_names.add(iso_name)
                                inferred_count += 1
                                found = True
                                break
                    elif iso_type == child_type:
                        candidates = type_to_names.get(parent_type, [])
                        for p in candidates:
                            if (p, iso_name) not in connected_pairs:
                                relationships.append({"type": rel_type_display, "source_name": p, "target_name": iso_name, "inferred": True})
                                connected_pairs.add((p, iso_name))
                                connected_names.add(iso_name)
                                inferred_count += 1
                                found = True
                                break
            if inferred_count:
                current_app.logger.info(f"ENT-122: Inferred {inferred_count} relationships from canonical chain")
        except Exception as e:
            current_app.logger.warning(f"ENT-122: Chain inference skipped: {e}")

        # ── Pass 3: Match generated elements to existing portfolio ──
        # Resolve element names against 850+ apps and 2763 ArchiMate elements
        # already in the database. If a match is found, annotate the element
        # with existing_id so the Composer can link rather than duplicate.
        try:
            from app.modules.architecture.services.entity_resolution_service import EntityResolutionService
            resolver = EntityResolutionService()
            matched_count = 0
            for elem in elements:
                resolution = resolver.resolve_entity(elem["name"], elem.get("type"))
                db_match = resolution.get("database_match")
                if db_match and resolution.get("confidence", 0) >= 0.7:
                    elem["existing_id"] = db_match["id"]
                    elem["existing_name"] = db_match["name"]
                    elem["existing_type"] = db_match.get("type")
                    elem["match_confidence"] = resolution["confidence"]
                    matched_count += 1
            if matched_count:
                current_app.logger.info(f"ENT-122: Matched {matched_count}/{len(elements)} elements to existing portfolio")
        except Exception as e:
            current_app.logger.warning(f"ENT-122: Entity resolution skipped: {e}")

        return jsonify({
            "success": True,
            "description": description,
            "model_name": model_data.get("model_name", "Generated Architecture"),
            "elements": elements,
            "relationships": relationships,
            "element_count": len(elements),
            "relationship_count": len(relationships),
        })

    except ConnectionError as e:
        current_app.logger.error(f"ENT-122: LLM connection error: {e}")
        return jsonify({"success": False, "error": "AI service unavailable. Check API configuration in Admin > API Settings."}), 503
    except Exception as e:
        current_app.logger.error(f"ENT-122: Error generating ArchiMate from description: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ============================================================================
# AIC-313: Greenfield solution blueprint workflow routes
# ============================================================================

@unified_ai_chat_bp.route("/workflow/greenfield/start", methods=["POST"])
@login_required
@rate_limit(10, "1h")
@audit_log("greenfield_workflow_start")
def greenfield_workflow_start():
    """
    Start a greenfield architecture workflow from a business brief.

    POST /ai-chat/workflow/greenfield/start
    Body: {"brief": "...", "solution_id": 123 (optional), "model": "..." (optional)}
    Returns: {"success": true, "workspace_id": N, "step": "BRIEF", "response": "..."}
    """
    data = request.get_json(silent=True) or {}
    brief = (data.get("brief") or "").strip()
    if not brief:
        return jsonify({"success": False, "error": "brief is required"}), 400

    context = {}
    if data.get("solution_id"):
        try:
            context["solution_id"] = int(data["solution_id"])
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "solution_id must be an integer"}), 400

    try:
        from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel, GreenfieldWorkflow

        kernel = WorkbenchKernel()
        wf_obj = GreenfieldWorkflow(kernel, user_id=current_user.id)
        result = wf_obj.start(brief, context=context or None)

        if not result.get("success"):
            return jsonify(result), 500

        workspace_id = result["workspace_id"]
        workflow_state = result["workflow_state"]

        # Persist workflow_state in workspace metadata so steps can resume
        kernel.update_workspace_metadata(workspace_id, {"workflow_state": workflow_state})

        return jsonify({
            "success": True,
            "workspace_id": workspace_id,
            "step": workflow_state.get("step"),
            "solution_id": workflow_state.get("accumulated", {}).get("solution_id"),
            "response": result.get("response", ""),
        })
    except Exception as e:
        current_app.logger.error("AIC-313: greenfield start failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@unified_ai_chat_bp.route("/workflow/greenfield/step/<step_name>/confirm", methods=["POST"])
@login_required
@rate_limit(20, "1h")
@audit_log("greenfield_workflow_confirm")
def greenfield_workflow_confirm(step_name):
    """
    Confirm the current step and advance the greenfield workflow.

    POST /ai-chat/workflow/greenfield/step/<step_name>/confirm
    Body: {"workspace_id": N, "model": "..." (optional)}
    Returns: {"success": true, "workspace_id": N, "step": "NEXT_STEP", "response": "..."}
    """
    data = request.get_json(silent=True) or {}
    workspace_id = data.get("workspace_id")
    if not workspace_id:
        return jsonify({"success": False, "error": "workspace_id is required"}), 400

    try:
        from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel, GreenfieldWorkflow
        from app.models.solution_architect_models import SolutionAnalysisSession

        kernel = WorkbenchKernel()
        session = SolutionAnalysisSession.query.get(workspace_id)
        if not session:
            return jsonify({"success": False, "error": "Workspace not found"}), 404

        meta = (session.custom_metadata or {}) if session else {}
        workflow_state = meta.get("workflow_state") or {
            "step": step_name,
            "workspace_id": workspace_id,
            "accumulated": {},
            "workspace_type": "greenfield",
        }

        wf_obj = GreenfieldWorkflow(kernel, user_id=current_user.id)
        result = wf_obj.advance("next", workflow_state, requested_model=data.get("model"))

        if result is None:
            return jsonify({"success": True, "workspace_id": workspace_id, "response": "Type 'next' to advance."})

        updated_state = result.get("workflow_state", workflow_state)
        kernel.update_workspace_metadata(workspace_id, {"workflow_state": updated_state})

        return jsonify({
            "success": result.get("success", True),
            "workspace_id": workspace_id,
            "step": updated_state.get("step"),
            "solution_id": updated_state.get("accumulated", {}).get("solution_id"),
            "response": result.get("response", ""),
        })
    except Exception as e:
        current_app.logger.error("AIC-313: greenfield confirm failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@unified_ai_chat_bp.route("/workflow/greenfield/step/<step_name>/skip", methods=["POST"])
@login_required
@rate_limit(20, "1h")
@audit_log("greenfield_workflow_skip")
def greenfield_workflow_skip(step_name):
    """
    Skip the current step and advance the greenfield workflow.

    POST /ai-chat/workflow/greenfield/step/<step_name>/skip
    Body: {"workspace_id": N, "model": "..." (optional)}
    Returns: {"success": true, "workspace_id": N, "step": "NEXT_STEP", "response": "..."}
    """
    data = request.get_json(silent=True) or {}
    workspace_id = data.get("workspace_id")
    if not workspace_id:
        return jsonify({"success": False, "error": "workspace_id is required"}), 400

    try:
        from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel, GreenfieldWorkflow
        from app.models.solution_architect_models import SolutionAnalysisSession

        kernel = WorkbenchKernel()
        session = SolutionAnalysisSession.query.get(workspace_id)
        if not session:
            return jsonify({"success": False, "error": "Workspace not found"}), 404

        meta = (session.custom_metadata or {}) if session else {}
        workflow_state = meta.get("workflow_state") or {
            "step": step_name,
            "workspace_id": workspace_id,
            "accumulated": {},
            "workspace_type": "greenfield",
        }

        wf_obj = GreenfieldWorkflow(kernel, user_id=current_user.id)
        result = wf_obj.advance("skip", workflow_state, requested_model=data.get("model"))

        if result is None:
            return jsonify({"success": True, "workspace_id": workspace_id, "response": "Type 'next' to advance."})

        updated_state = result.get("workflow_state", workflow_state)
        kernel.update_workspace_metadata(workspace_id, {"workflow_state": updated_state})

        return jsonify({
            "success": result.get("success", True),
            "workspace_id": workspace_id,
            "step": updated_state.get("step"),
            "solution_id": updated_state.get("accumulated", {}).get("solution_id"),
            "response": result.get("response", ""),
        })
    except Exception as e:
        current_app.logger.error("AIC-313: greenfield skip failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@unified_ai_chat_bp.route("/workflow/greenfield/status", methods=["GET"])
@login_required
def greenfield_workflow_status():
    """
    Get the current status of a greenfield workflow.

    GET /ai-chat/workflow/greenfield/status?workspace_id=N
    Returns: {"success": true, "workspace_id": N, "step": "...", "step_index": N, ...}
    """
    workspace_id = request.args.get("workspace_id", type=int)
    if not workspace_id:
        return jsonify({"success": False, "error": "workspace_id is required"}), 400

    try:
        from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel, GREENFIELD_STEPS
        from app.models.solution_architect_models import SolutionAnalysisSession

        kernel = WorkbenchKernel()
        ws = kernel.load_workspace(workspace_id)
        if not ws:
            return jsonify({"success": False, "error": "Workspace not found"}), 404

        session = SolutionAnalysisSession.query.get(workspace_id)
        meta = (session.custom_metadata or {}) if session else {}
        workflow_state = meta.get("workflow_state", {})

        current_step = workflow_state.get("step", "BRIEF")
        step_index = GREENFIELD_STEPS.index(current_step) if current_step in GREENFIELD_STEPS else 0

        return jsonify({
            "success": True,
            "workspace_id": workspace_id,
            "step": current_step,
            "step_index": step_index,
            "total_steps": len(GREENFIELD_STEPS),
            "steps": GREENFIELD_STEPS,
            "solution_id": ws.get("solution_id") or workflow_state.get("accumulated", {}).get("solution_id"),
            "artifacts": ws.get("artifacts", {}),
            "brief": workflow_state.get("brief", ""),
        })
    except Exception as e:
        current_app.logger.error("AIC-313: greenfield status failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# AIC-314: Brownfield modernization workflow routes
# ============================================================================

@unified_ai_chat_bp.route("/workflow/brownfield/start", methods=["POST"])
@login_required
@rate_limit(10, "1h")
@audit_log("brownfield_workflow_start")
def brownfield_workflow_start():
    """
    Start a brownfield modernization workflow from a target domain.

    POST /ai-chat/workflow/brownfield/start
    Body: {"target_domain": "...", "solution_id": 123 (optional), "model": "..." (optional)}
    Returns: {"success": true, "workspace_id": N, "step": "CONTEXT_LOAD", "response": "..."}
    """
    data = request.get_json(silent=True) or {}
    target_domain = (data.get("target_domain") or "").strip()
    if not target_domain:
        return jsonify({"success": False, "error": "target_domain is required"}), 400

    context = {}
    if data.get("solution_id"):
        try:
            context["solution_id"] = int(data["solution_id"])
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "solution_id must be an integer"}), 400

    try:
        from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel, BrownfieldWorkflow

        kernel = WorkbenchKernel()
        wf_obj = BrownfieldWorkflow(kernel, user_id=current_user.id)
        result = wf_obj.start(target_domain, context=context or None)

        if not result.get("success"):
            return jsonify(result), 500

        workspace_id = result["workspace_id"]
        workflow_state = result["workflow_state"]

        kernel.update_workspace_metadata(workspace_id, {"workflow_state": workflow_state})

        return jsonify({
            "success": True,
            "workspace_id": workspace_id,
            "step": workflow_state.get("step"),
            "solution_id": workflow_state.get("accumulated", {}).get("solution_id"),
            "response": result.get("response", ""),
        })
    except Exception as e:
        current_app.logger.error("AIC-314: brownfield start failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@unified_ai_chat_bp.route("/workflow/brownfield/step/<step_name>/confirm", methods=["POST"])
@login_required
@rate_limit(20, "1h")
@audit_log("brownfield_workflow_confirm")
def brownfield_workflow_confirm(step_name):
    """
    Confirm the current step and advance the brownfield workflow.

    POST /ai-chat/workflow/brownfield/step/<step_name>/confirm
    Body: {"workspace_id": N, "model": "..." (optional)}
    Returns: {"success": true, "workspace_id": N, "step": "NEXT_STEP", "response": "..."}
    """
    data = request.get_json(silent=True) or {}
    workspace_id = data.get("workspace_id")
    if not workspace_id:
        return jsonify({"success": False, "error": "workspace_id is required"}), 400

    try:
        from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel, BrownfieldWorkflow
        from app.models.solution_architect_models import SolutionAnalysisSession

        kernel = WorkbenchKernel()
        session = SolutionAnalysisSession.query.get(workspace_id)
        if not session:
            return jsonify({"success": False, "error": "Workspace not found"}), 404

        meta = (session.custom_metadata or {}) if session else {}
        workflow_state = meta.get("workflow_state") or {
            "step": step_name,
            "workspace_id": workspace_id,
            "accumulated": {},
            "workspace_type": "brownfield",
        }

        wf_obj = BrownfieldWorkflow(kernel, user_id=current_user.id)
        result = wf_obj.advance("next", workflow_state, requested_model=data.get("model"))

        if result is None:
            return jsonify({"success": True, "workspace_id": workspace_id, "response": "Type 'next' to advance."})

        updated_state = result.get("workflow_state", workflow_state)
        kernel.update_workspace_metadata(workspace_id, {"workflow_state": updated_state})

        return jsonify({
            "success": result.get("success", True),
            "workspace_id": workspace_id,
            "step": updated_state.get("step"),
            "solution_id": updated_state.get("accumulated", {}).get("solution_id"),
            "response": result.get("response", ""),
        })
    except Exception as e:
        current_app.logger.error("AIC-314: brownfield confirm failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@unified_ai_chat_bp.route("/workflow/brownfield/step/<step_name>/skip", methods=["POST"])
@login_required
@rate_limit(20, "1h")
@audit_log("brownfield_workflow_skip")
def brownfield_workflow_skip(step_name):
    """
    Skip the current step and advance the brownfield workflow.

    POST /ai-chat/workflow/brownfield/step/<step_name>/skip
    Body: {"workspace_id": N, "model": "..." (optional)}
    Returns: {"success": true, "workspace_id": N, "step": "NEXT_STEP", "response": "..."}
    """
    data = request.get_json(silent=True) or {}
    workspace_id = data.get("workspace_id")
    if not workspace_id:
        return jsonify({"success": False, "error": "workspace_id is required"}), 400

    try:
        from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel, BrownfieldWorkflow
        from app.models.solution_architect_models import SolutionAnalysisSession

        kernel = WorkbenchKernel()
        session = SolutionAnalysisSession.query.get(workspace_id)
        if not session:
            return jsonify({"success": False, "error": "Workspace not found"}), 404

        meta = (session.custom_metadata or {}) if session else {}
        workflow_state = meta.get("workflow_state") or {
            "step": step_name,
            "workspace_id": workspace_id,
            "accumulated": {},
            "workspace_type": "brownfield",
        }

        wf_obj = BrownfieldWorkflow(kernel, user_id=current_user.id)
        result = wf_obj.advance("skip", workflow_state, requested_model=data.get("model"))

        if result is None:
            return jsonify({"success": True, "workspace_id": workspace_id, "response": "Type 'next' to advance."})

        updated_state = result.get("workflow_state", workflow_state)
        kernel.update_workspace_metadata(workspace_id, {"workflow_state": updated_state})

        return jsonify({
            "success": result.get("success", True),
            "workspace_id": workspace_id,
            "step": updated_state.get("step"),
            "solution_id": updated_state.get("accumulated", {}).get("solution_id"),
            "response": result.get("response", ""),
        })
    except Exception as e:
        current_app.logger.error("AIC-314: brownfield skip failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@unified_ai_chat_bp.route("/workflow/brownfield/status", methods=["GET"])
@login_required
def brownfield_workflow_status():
    """
    Get the current status of a brownfield workflow.

    GET /ai-chat/workflow/brownfield/status?workspace_id=N
    Returns: {"success": true, "workspace_id": N, "step": "...", "step_index": N, ...}
    """
    workspace_id = request.args.get("workspace_id", type=int)
    if not workspace_id:
        return jsonify({"success": False, "error": "workspace_id is required"}), 400

    try:
        from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel, BROWNFIELD_STEPS
        from app.models.solution_architect_models import SolutionAnalysisSession

        kernel = WorkbenchKernel()
        ws = kernel.load_workspace(workspace_id)
        if not ws:
            return jsonify({"success": False, "error": "Workspace not found"}), 404

        session = SolutionAnalysisSession.query.get(workspace_id)
        meta = (session.custom_metadata or {}) if session else {}
        workflow_state = meta.get("workflow_state", {})

        current_step = workflow_state.get("step", "CONTEXT_LOAD")
        step_index = BROWNFIELD_STEPS.index(current_step) if current_step in BROWNFIELD_STEPS else 0

        return jsonify({
            "success": True,
            "workspace_id": workspace_id,
            "step": current_step,
            "step_index": step_index,
            "total_steps": len(BROWNFIELD_STEPS),
            "steps": BROWNFIELD_STEPS,
            "solution_id": ws.get("solution_id") or workflow_state.get("accumulated", {}).get("solution_id"),
            "artifacts": ws.get("artifacts", {}),
            "target_domain": workflow_state.get("accumulated", {}).get("target_domain", ""),
        })
    except Exception as e:
        current_app.logger.error("AIC-314: brownfield status failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# AIC-318: Evidence gate API
# ============================================================================

@unified_ai_chat_bp.route("/workflow/evidence-gate", methods=["GET"])
@login_required
def workflow_evidence_gate():
    """
    AIC-318: Check workspace evidence gate for ARB submission readiness.

    GET /ai-chat/workflow/evidence-gate?workspace_id=N&workflow_type=greenfield
    Returns: {pass: bool, workspace_id: N, workflow_type: str, missing: [...], artifact_summary: {...}, suggested_actions: [...]}
    """
    workspace_id = request.args.get("workspace_id", type=int)
    workflow_type = request.args.get("workflow_type", "greenfield")

    if not workspace_id:
        return jsonify({"pass": False, "error": "workspace_id is required", "missing": ["workspace_id"]}), 400

    if workflow_type not in ("greenfield", "brownfield"):
        return jsonify({"pass": False, "error": "workflow_type must be greenfield or brownfield"}), 400

    try:
        from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel

        kernel = WorkbenchKernel()
        result = kernel.check_evidence_gate(workspace_id, workflow_type)
        return jsonify(result)
    except Exception as e:
        current_app.logger.exception("AIC-318: evidence gate failed: %s", e)
        # The empty artifact_summary and the synthetic "exception: ..." entry in
        # `missing` read as gate findings, which they are not — they are the gate
        # failing to run. Only the verdict and the reason are reported now.
        # error-signalling-ok: fail-closed gate; denying ARB readiness on error is the honest answer and the caller renders the denial
        return jsonify({
            "pass": False,
            "workspace_id": workspace_id,
            "workflow_type": workflow_type,
            "error": "The evidence gate could not be evaluated.",
        })
