# mass-deletion-ok
"""
Capability Map — core mapping CRUD, suggestions, bulk ops, and statistics.

Extracted from app/routes/capability_map_routes.py (lines 83-610, 612-901,
902-1270, 1338-1415, 1623-1728).

Routes:
    - api_nodes_edges()                          GET  "/api/nodes-edges"
    - api_applications()                         GET  "/api/applications"
    - api_capabilities()                         GET  "/api/capabilities"
    - api_unified_capabilities()                 GET  "/api/unified-capabilities"
    - api_mappings()                             GET  "/api/mappings"
    - api_suggest_mapping(app_id)                GET  "/api/suggest-mapping/<int:app_id>"
    - api_capability_applications(capability_id) GET  "/api/capability/<capability_id>/applications"
    - api_create_mapping()                       POST "/api/mappings"
    - api_delete_mapping(mapping_id)             DELETE "/api/mappings/<int:mapping_id>"
    - api_manufacturing_capabilities()           GET  "/api/manufacturing-capabilities"
    - api_apply_suggestion()                     POST "/api/apply-suggestion"
    - api_bulk_map_applications()                POST "/api/bulk-map-applications"
    - api_statistics()                           GET  "/api/statistics"
    - api_apqc_suggestions()                     GET  "/api/capabilities/apqc-suggestions"
    - api_app_capability_suggestions()           GET  "/api/capabilities/app-suggestions"
    - api_apqc_link()                            POST "/api/capabilities/apqc-link"
    - api_abacus_auto_suggest()                   POST "/api/capabilities/abacus-auto-suggest"
    - api_abacus_pending_suggestions()            GET  "/api/capabilities/abacus-pending-suggestions"
"""

from datetime import datetime

from flask import current_app, g, jsonify, request
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from app.decorators import audit_log

from app import db
from app.exceptions import (
    BusinessRuleError,
    DatabaseError,
    ExternalServiceError,
)

from . import capability_map
from .map_views import build_nodes_edges
import logging
from app.services.pgvector_embedding_service import scoped_embedding_query
from app.utils.pagination import safe_int_arg
logger = logging.getLogger(__name__)


@capability_map.route("/api/nodes-edges")
@login_required
def api_nodes_edges():
    """API endpoint for network visualization data"""
    try:
        from app.services.application_capability_catalog import ApplicationCapabilityCatalogService

        # Get capability catalog
        catalog = ApplicationCapabilityCatalogService.get_catalog_hierarchy()

        # Build nodes and edges for network graph
        nodes, edges = build_nodes_edges(catalog)

        return jsonify(
            {
                "nodes": nodes,
                "edges": edges,
                "statistics": {
                    "total_capabilities": len([n for n in nodes if n["category"] != "function"]),
                    "total_functions": len([n for n in nodes if n["category"] == "function"]),
                    "domains": list(set(n["domain"] for n in nodes if n["domain"])),
                    "max_level": max((n["level"] for n in nodes), default=0),
                },
            }
        )
    except (ImportError, AttributeError) as e:
        current_app.logger.error(f"Service error loading nodes and edges: {e}")
        raise ExternalServiceError(
            message=f"Failed to load catalog service: {str(e)}",
            user_message="Unable to load network visualization data.",
            recovery_action="Refresh the page. If the problem persists, contact support.",
        )
    except Exception as e:
        current_app.logger.error(f"Error building nodes and edges: {e}")
        raise DatabaseError(
            message=f"Failed to build network data: {str(e)}",
            user_message="Unable to generate network visualization.",
            recovery_action="Try refreshing the page.",
        )


@capability_map.route("/api/applications")
@login_required
def api_applications():
    """API endpoint to get application mappings"""
    try:
        from app.models.application_layer import ApplicationComponent

        applications = ApplicationComponent.query.order_by(ApplicationComponent.name).all()

        app_data = []
        for app in applications:
            app_data.append(
                {
                    "id": app.id,
                    "name": app.name,
                    "type": app.component_type or "Unknown",
                    "description": app.description or "No description",
                    "domain": app.business_domain or "Not specified",
                    "owner": app.business_owner or "Not specified",
                    "department": app.business_domain or "Not specified",
                    "status": app.lifecycle_status or "Unknown",
                    "total_cost_of_ownership": float(app.total_cost_of_ownership)
                    if app.total_cost_of_ownership
                    else None,
                }
            )

        return jsonify({"applications": app_data})
    except SQLAlchemyError as e:
        current_app.logger.error(f"Database error fetching applications: {e}")
        raise DatabaseError(
            message=f"Failed to fetch applications: {str(e)}",
            user_message="Unable to retrieve application data.",
            recovery_action="Please try again. If the problem persists, contact support.",
        )


@capability_map.route("/api/capabilities")
@login_required
def api_capabilities():
    """Capabilities summary with statistics for the network view."""
    try:
        from app.models.unified_capability import UnifiedCapability

        capabilities = UnifiedCapability.query.limit(500).all()

        # Group by L1 capabilities as domain proxies
        domain_stats = []
        categories = {}
        for c in capabilities:
            name = c.name or ''
            parts = name.split(' ', 1)
            if parts[0].replace('.', '').isdigit() and len(parts) > 1:
                if getattr(c, 'level', 1) == 1:
                    categories[name] = categories.get(name, 0)
                else:
                    prefix = parts[0].split('.')[0]
                    parent = next((p.name for p in capabilities if getattr(p, 'level', 1) == 1 and p.name.startswith(prefix + ' ')), 'Other')
                    categories[parent] = categories.get(parent, 0) + 1
            else:
                categories['Other'] = categories.get('Other', 0) + 1
        domain_stats = [
            {"id": i, "name": cat, "capability_count": cnt}
            for i, (cat, cnt) in enumerate(categories.items(), 1)
        ]

        cap_data = [
            {
                "id": c.id,
                "name": c.name,
                "code": getattr(c, 'code', '') or '',
                "level": getattr(c, 'level', 1),
                "domain_id": c.domain_id,
                "category": getattr(c, 'category', '') or (c.domain.name if c.domain else '') or '',
                "description": getattr(c, 'description', '') or "",
                "parent_id": getattr(c, 'parent_capability_id', None),
            }
            for c in capabilities
        ]

        return jsonify(
            {
                "capabilities": cap_data,
                "statistics": {
                    "total": len(capabilities),
                    "domains": domain_stats,
                },
            }
        )
    except Exception as e:
        import traceback
        current_app.logger.error(f"Error loading capabilities: {e}\n{traceback.format_exc()}")
        return jsonify({"capabilities": [], "statistics": {"total": 0, "domains": []}}), 500


@capability_map.route("/api/capabilities/tree")
@login_required
def api_capabilities_tree():
    """Hierarchical capability tree with gap coverage indicators."""
    try:
        import re as _re
        from app.models.unified_capability import UnifiedCapability

        capabilities = UnifiedCapability.query.order_by(
            UnifiedCapability.level, UnifiedCapability.name
        ).all()
        if not capabilities:
            return jsonify({"tree": [], "total": 0})


        highlight_ids = set()
        highlight_param = request.args.get("highlight", "")
        if highlight_param:
            try:
                highlight_ids = {int(x) for x in highlight_param.split(",") if x.strip()}
            except ValueError:
                logger.exception("Failed to compute highlight_ids")
                pass

        coverage_map = {}  # app-coverage per capability not yet computed; default 0
        nodes_by_level = {1: [], 2: [], 3: []}
        for c in capabilities:
            app_count = coverage_map.get(c.id, 0)
            coverage_status = "full" if app_count >= 3 else "partial" if app_count >= 1 else "none"
            prefix_match = _re.match(r"^(\d+(?:\.\d+)*)\s", c.name)
            prefix = prefix_match.group(1) if prefix_match else ""
            node = {
                "id": c.id, "name": c.name, "code": c.code or "", "level": c.level,
                "description": c.description or "", "prefix": prefix,
                "coverage_status": coverage_status, "app_count": app_count,
                "highlighted": c.id in highlight_ids, "children": [],
            }
            level = c.level if c.level in (1, 2, 3) else 1
            nodes_by_level[level].append(node)

        l1_by_num = {}
        for n in nodes_by_level[1]:
            m = _re.match(r"^0?(\d+)", n["prefix"])
            if m:
                l1_by_num[m.group(1)] = n

        l2_by_prefix = {}
        for n in nodes_by_level[2]:
            parts = n["prefix"].split(".")
            if parts:
                parent_num = parts[0].lstrip("0") or "0"
                parent = l1_by_num.get(parent_num)
                if parent:
                    parent["children"].append(n)
            l2_by_prefix[n["prefix"]] = n

        for n in nodes_by_level[3]:
            parts = n["prefix"].split(".")
            if len(parts) >= 2:
                parent_prefix = ".".join(parts[:2])
                parent = l2_by_prefix.get(parent_prefix)
                if parent:
                    parent["children"].append(n)

        roots = sorted(nodes_by_level[1], key=lambda x: x.get("prefix", ""))
        return jsonify({"tree": roots, "total": len(capabilities)})
    except Exception as e:
        current_app.logger.error(f"Error loading capability tree: {e}")
        return jsonify({"tree": [], "total": 0}), 500


@capability_map.route("/api/capabilities/semantic-search", methods=["POST"])
@login_required
def api_capabilities_semantic_search():
    """Vector-based semantic search over capabilities."""
    try:
        from app.models.business_capability import BusinessCapability
        from app.models.vector_embeddings import BusinessCapabilityEmbedding

        data = request.get_json(silent=True) or {}
        query = data.get("query", "").strip()
        top_k = min(data.get("top_k", 20), 50)

        if not query or len(query) < 3:
            return jsonify({"capabilities": [], "error": "Query too short"})

        # Generate embedding for query
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2")
            query_embedding = model.encode(query).tolist()
        except Exception as e:
            current_app.logger.warning(f"Embedding failed, keyword fallback: {e}")
            caps = BusinessCapability.query.filter(
                db.or_(BusinessCapability.name.ilike(f"%{query}%"),
                       BusinessCapability.description.ilike(f"%{query}%"))
            ).limit(top_k).all()
            # similarity is None, not 0.5: a keyword hit has no cosine score, and a
            # literal 0.5 is indistinguishable from a measured one.
            # error-signalling-ok: the keyword fallback returns real matches, so the search did succeed; method=keyword_fallback tells the caller the ranking is not vector-based
            return jsonify({"capabilities": [
                {"id": c.id, "name": c.name, "level": c.level, "code": c.code or "",
                 "description": c.description or "", "similarity": None}
                for c in caps
            ], "method": "keyword_fallback"})

        embeddings = scoped_embedding_query(BusinessCapabilityEmbedding).all()
        if not embeddings:
            return jsonify({"capabilities": [], "method": "no_embeddings"})
        if not embeddings:
            return jsonify({"capabilities": [], "method": "no_embeddings"})

        import numpy as np
        query_vec = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return jsonify({"capabilities": []})

        import json as _json
        results = []
        for emb in embeddings:
            raw = emb.embedding
            if raw is None:
                continue
            if isinstance(raw, str):
                try:
                    raw = _json.loads(raw)
                except (ValueError, TypeError):
                    continue
            if isinstance(raw, (list, tuple)):
                emb_vec = np.array(raw, dtype=np.float32)
            elif hasattr(raw, "tolist"):
                emb_vec = np.array(raw.tolist(), dtype=np.float32)
            else:
                continue
            if len(emb_vec) != len(query_vec):
                continue
            emb_norm = np.linalg.norm(emb_vec)
            if emb_norm == 0:
                continue
            sim = float(np.dot(query_vec, emb_vec) / (query_norm * emb_norm))
            results.append((emb.business_capability_id, sim))

        results.sort(key=lambda x: x[1], reverse=True)
        top_results = results[:top_k]
        top_cap_ids = [r[0] for r in top_results]
        sim_map = {r[0]: r[1] for r in top_results}

        caps = BusinessCapability.query.filter(
            BusinessCapability.id.in_(top_cap_ids)
        ).all() if top_cap_ids else []
        cap_lookup = {c.id: c for c in caps}

        response = []
        for cap_id in top_cap_ids:
            c = cap_lookup.get(cap_id)
            if c:
                response.append({
                    "id": c.id, "name": c.name, "level": c.level,
                    "code": c.code or "", "description": c.description or "",
                    "similarity": round(sim_map.get(cap_id, 0), 4),
                })
        return jsonify({"capabilities": response, "method": "vector", "query": query})
    except Exception as e:
        current_app.logger.error(f"Semantic search error: {e}")
        return jsonify({"capabilities": [], "error": str(e)}), 500


@capability_map.route("/api/capabilities/create-missing", methods=["POST"])
@login_required
def api_capabilities_create_missing():
    """Create a capability not in the catalog.

    Writes through ensure_capability_record (the single creator for
    UnifiedCapability) so the map's add/edit controls never write to
    BusinessCapability directly.
    """
    try:
        from app.modules.capabilities.services.capability_service import (
            ensure_capability_record,
        )
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Name is required"}), 400

        cap, created = ensure_capability_record(
            name=name,
            level=data.get("level", 2),
            description=data.get("description", ""),
            code=data.get("code"),
            domain_id=data.get("domain_id"),
            category=data.get("category"),
            capability_type=data.get("capability_type"),
            parent_capability_id=data.get("parent_capability_id"),
            specialization_type=data.get("specialization_type", "BUSINESS"),
        )
        db.session.commit()
        return jsonify(
            {
                "capability": {
                    "id": cap.id,
                    "name": cap.name,
                    "level": cap.level,
                },
                "created": created,
            }
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@capability_map.route("/api/unified-capabilities")
@login_required
def api_unified_capabilities():
    """
    API endpoint to get unified view of all capabilities (Application + Manufacturing)

    Optimized: Pre-fetches domains and applications to avoid N + 1 queries.

    ---
    tags:
      - Capabilities
      - Capability Map
    parameters:
      - name: level
        in: query
        type: integer
        required: false
        description: Filter capabilities by level (1 - 5)
      - name: domain_id
        in: query
        type: integer
        required: false
        description: Filter capabilities by domain ID
    responses:
      200:
        description: List of unified capabilities
        schema:
          type: array
          items:
            type: object
            properties:
              id:
                type: integer
                description: Capability ID
              name:
                type: string
                description: Capability name
              level:
                type: integer
                description: Capability level (1 - 5)
              domain:
                type: string
                description: Domain name
      500:
        description: Internal server error
    """
    try:
        from app.models.application_layer import ApplicationComponent
        from app.models.application_capability import ApplicationCapabilityMapping
        from app.models.business_capabilities import BusinessCapability

        # Use BusinessCapability (real APQC data) as the primary source
        app_capabilities = BusinessCapability.query.limit(500).all()

        # tenant-scoping-ok: scoped via the TenantMixin FK parent
        # BusinessCapability, not ACM.organization_id -- that column is NULL
        # on every row in production, so a predicate on it would report
        # every capability as unmapped. Joining BusinessCapability lets
        # do_orm_execute scope the join automatically. See e622d36 /
        # rationalization_scoring_service.py.
        app_mappings = (
            # tenant-scoping-ok: scoped via TenantMixin FK parent BusinessCapability, ACM.organization_id is NULL in prod (see e622d36).
            ApplicationCapabilityMapping.query
            .join(
                BusinessCapability,
                ApplicationCapabilityMapping.business_capability_id == BusinessCapability.id,
            )
            .limit(500)
            .all()
        )
        mapped_app_cap_ids = {mapping.business_capability_id for mapping in app_mappings}

        # Build mapping index (business_capability_id -> list of mappings)
        mappings_by_cap_id = {}
        for mapping in app_mappings:
            if mapping.business_capability_id not in mappings_by_cap_id:
                mappings_by_cap_id[mapping.business_capability_id] = []
            mappings_by_cap_id[mapping.business_capability_id].append(mapping)

        # OPTIMIZATION: Pre-fetch all applications referenced in mappings
        app_ids_in_mappings = {m.application_component_id for m in app_mappings}
        all_apps = (
            ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(app_ids_in_mappings)
            ).all()
            if app_ids_in_mappings
            else []
        )
        apps_by_id = {a.id: a for a in all_apps}

        # Try to get manufacturing capabilities - handle if model doesn't exist
        mfg_capabilities = []
        try:
            from app.models.manufacturing_capability import ManufacturingCapability

            mfg_capabilities = ManufacturingCapability.query.limit(500).all()
        except ImportError:
            current_app.logger.info(
                "ManufacturingCapability model not found; manufacturing capabilities will be skipped."
            )
        except Exception:
            current_app.logger.exception(
                "Error importing or querying ManufacturingCapability; manufacturing capabilities will be skipped."
            )

        # Build unified capability list
        unified_capabilities = []

        # Add application capabilities
        for capability in app_capabilities:
            try:
                # BusinessCapability uses a string field for domain
                domain_name = capability.business_domain or "Unknown"
                domain_id_str = None
                domain_code = "UNK"

                # Calculate business impact
                business_impact = 0
                strategic_imp = getattr(capability, "strategic_importance", None)
                if strategic_imp == "critical":
                    business_impact += 40
                elif strategic_imp == "high":
                    business_impact += 30
                elif strategic_imp == "medium":
                    business_impact += 20
                elif strategic_imp == "low":
                    business_impact += 10

                biz_criticality = getattr(capability, "business_criticality", None)
                if biz_criticality == "mission_critical":
                    business_impact += 30
                elif biz_criticality == "important":
                    business_impact += 20
                elif biz_criticality == "supporting":
                    business_impact += 10

                if getattr(capability, "is_core_differentiator", False):
                    business_impact += 30

                # Get application names for application capability using pre-indexed mappings
                app_mapped = capability.id in mapped_app_cap_ids
                cap_mappings = mappings_by_cap_id.get(capability.id, [])
                app_mapping_count = len(cap_mappings)

                # Get application names using pre-fetched apps lookup
                app_applications = []
                for mapping in cap_mappings:
                    app_comp = apps_by_id.get(mapping.application_component_id)
                    if app_comp:
                        app_applications.append(
                            {
                                "name": app_comp.name,
                                "type": getattr(app_comp, "component_type", "Application"),
                                "id": str(app_comp.id),  # Convert to string to preserve precision
                            }
                        )

                unified_capabilities.append(
                    {
                        "id": str(capability.id),
                        "name": capability.name,
                        "code": capability.code,
                        "type": "Application",
                        "level": capability.level,
                        "category": capability.category,
                        "domain": {
                            "id": domain_id_str,
                            "name": domain_name,
                            "code": domain_code,
                        },
                        "business_owner": getattr(capability, "business_owner", None) or "Unassigned",
                        "strategic_importance": strategic_imp or "medium",
                        "business_criticality": biz_criticality or "supporting",
                        "is_core_differentiator": getattr(capability, "is_core_differentiator", False),
                        "business_impact": min(business_impact, 100),
                        # R3-1: an unassessed capability must not render as a
                        # fabricated "Level 1 -> Level 3" pair (same fix as
                        # already applied at :890/:972 in this file).
                        "current_maturity": getattr(capability, "current_maturity_level", None),
                        "target_maturity": getattr(capability, "target_maturity_level", None),
                        "maturity_reason_code": None
                        if getattr(capability, "current_maturity_level", None) is not None
                        else "no_maturity_recorded",
                        "maturity_gap": getattr(capability, "maturity_gap", None),
                        "annual_cost": getattr(capability, "annual_cost", None),
                        "annual_revenue_impact": getattr(capability, "annual_revenue_impact", None),
                        "status": getattr(capability, "status", "defined"),
                        "is_mapped": app_mapped,
                        "mapping_count": app_mapping_count,
                        "applications": app_applications,
                        "application_name": app_applications[0]["name"]
                        if app_applications
                        else "No Application Mapped",
                        "application_type": app_applications[0]["type"]
                        if app_applications
                        else "Application Capability",
                        "coverage_percentage": 100 if app_mapped else 0,
                    }
                )
            except Exception:
                # Skip problematic capability
                continue

        # Pre-fetch unified capabilities by ID for manufacturing lookups
        unified_caps_by_id = {cap.id: cap for cap in app_capabilities}

        # Add manufacturing capabilities if available
        for capability in mfg_capabilities:
            try:
                # Calculate business impact for manufacturing
                mfg_business_impact = 0
                if hasattr(capability, "strategic_importance"):  # model-safety-ok: polymorphic ManufacturingCapability may not have this field
                    if capability.strategic_importance == "critical":
                        mfg_business_impact += 40
                    elif capability.strategic_importance == "high":
                        mfg_business_impact += 30
                    elif capability.strategic_importance == "medium":
                        mfg_business_impact += 20

                if (
                    hasattr(capability, "is_core_manufacturing")  # model-safety-ok: polymorphic field not on all capability types
                    and capability.is_core_manufacturing
                ):
                    mfg_business_impact += 30

                # Get unified capability ID - prefer relationship, fallback to direct field
                unified_cap_id = None
                if hasattr(capability, "unified_capability") and capability.unified_capability:  # model-safety-ok: polymorphic - relationship may not exist on all capability types
                    unified_cap_id = capability.unified_capability.id
                elif hasattr(capability, "unified_capability_id"):  # model-safety-ok: polymorphic - field may not exist on all capability types
                    unified_cap_id = capability.unified_capability_id
                else:
                    # This shouldn't happen as unified_capability_id is not nullable, but handle it
                    current_app.logger.warning(
                        f"Manufacturing capability {capability.id} has no unified_capability_id"
                    )
                    continue  # Skip this capability

                # Check if manufacturing capability has application mappings using pre-indexed data
                mfg_mapped = unified_cap_id in mapped_app_cap_ids
                mfg_cap_mappings = mappings_by_cap_id.get(unified_cap_id, [])
                mfg_mapping_count = len(mfg_cap_mappings)

                # Get application names using pre-fetched apps lookup
                mfg_applications = []
                for mapping in mfg_cap_mappings:
                    app_comp = apps_by_id.get(mapping.application_component_id)
                    if app_comp:
                        mfg_applications.append(
                            {
                                "name": app_comp.name,
                                "type": getattr(app_comp, "component_type", "Application"),
                                "id": str(app_comp.id),  # Convert to string to preserve precision
                            }
                        )

                # Get unified capability details using pre-fetched lookup
                unified_cap = None
                if hasattr(capability, "unified_capability") and capability.unified_capability:  # model-safety-ok: polymorphic - relationship may not exist on all capability types
                    unified_cap = capability.unified_capability
                else:
                    # Use pre-fetched lookup instead of query
                    unified_cap = unified_caps_by_id.get(unified_cap_id)

                unified_capabilities.append(
                    {
                        "id": str(
                            unified_cap_id
                        ),  # Convert to string to preserve precision - always use unified capability ID
                        "name": unified_cap.name
                        if unified_cap
                        else f"Manufacturing Process {capability.id}",
                        "code": unified_cap.code if unified_cap else f"MFG-{capability.id}",
                        "type": "Manufacturing",
                        "level": unified_cap.level
                        if unified_cap
                        else getattr(capability, "level", 1),  # model-safety-ok: polymorphic ManufacturingCapability does not have level
                        "category": getattr(capability, "manufacturing_domain", "manufacturing"),  # model-safety-ok: field on ManufacturingCapability, defensive for mixed types
                        "domain": {
                            "id": None,
                            "name": getattr(capability, "manufacturing_domain", "Manufacturing"),  # model-safety-ok: field on ManufacturingCapability, defensive for mixed types
                            "code": "MFG",
                        },
                        "business_owner": getattr(
                            capability.unified_capability, "business_owner", "Manufacturing Lead"
                        )
                        if capability.unified_capability
                        else "Manufacturing Lead",
                        "strategic_importance": getattr(
                            capability.unified_capability, "strategic_importance", "medium"
                        )
                        if capability.unified_capability
                        else "medium",
                        "business_criticality": getattr(
                            capability.unified_capability, "business_criticality", "supporting"
                        )
                        if capability.unified_capability
                        else "supporting",
                        "is_core_differentiator": getattr(
                            capability.unified_capability, "is_core_differentiator", False
                        )
                        if capability.unified_capability
                        else False,
                        "business_impact": min(mfg_business_impact, 100),
                        # D-R7-1: ManufacturingCapability only carries a single
                        # `lean_maturity` field (current, no separate target
                        # concept on that model). The single authority for a
                        # current/target/gap TRIPLET for this row is the linked
                        # UnifiedCapability (`unified_cap`), same as the other 3
                        # sites in this file (:561-566, :890, :972). Do not copy
                        # `lean_maturity` into both current and target — that
                        # fabricates a gap of 0 that can never be anything else.
                        "current_maturity": unified_cap.current_maturity_level
                        if unified_cap
                        else None,
                        "target_maturity": unified_cap.target_maturity_level
                        if unified_cap
                        else None,
                        "maturity_reason_code": None
                        if unified_cap and unified_cap.current_maturity_level is not None
                        else "no_maturity_recorded",
                        "maturity_gap": unified_cap.maturity_gap
                        if unified_cap and unified_cap.maturity_gap is not None
                        else None,
                        "annual_cost": None,
                        "annual_revenue_impact": None,
                        "status": getattr(capability, "status", "defined"),  # model-safety-ok: ManufacturingCapability does not have status field
                        "is_mapped": mfg_mapped,  # Manufacturing capabilities can have mappings
                        "mapping_count": mfg_mapping_count,
                        "applications": mfg_applications,  # Include actual application data
                        "application_name": mfg_applications[0]["name"]
                        if mfg_applications
                        else "No Application Mapped",
                        "application_type": mfg_applications[0]["type"]
                        if mfg_applications
                        else "Manufacturing Capability",
                        "coverage_percentage": 100 if mfg_mapped else 0,
                    }
                )
            except Exception:
                # Skip problematic manufacturing capability
                continue

        # Sort by business impact (highest first)
        unified_capabilities.sort(key=lambda x: x["business_impact"], reverse=True)

        # Calculate statistics
        total_capabilities = len(unified_capabilities)
        application_capabilities = len(
            [c for c in unified_capabilities if c["type"] == "Application"]
        )
        manufacturing_capabilities = len(
            [c for c in unified_capabilities if c["type"] == "Manufacturing"]
        )
        mapped_capabilities = len([c for c in unified_capabilities if c["is_mapped"]])
        unmapped_capabilities = total_capabilities - mapped_capabilities

        # Domain distribution
        domain_stats = {}
        for capability in unified_capabilities:
            domain_key = capability["domain"]["code"]
            if domain_key not in domain_stats:
                domain_stats[domain_key] = {
                    "name": capability["domain"]["name"],
                    "count": 0,
                    "mapped": 0,
                    "avg_impact": 0,
                }
            domain_stats[domain_key]["count"] += 1
            if capability["is_mapped"]:
                domain_stats[domain_key]["mapped"] += 1
            domain_stats[domain_key]["avg_impact"] += capability["business_impact"]

        # Calculate averages
        for domain in domain_stats.values():
            domain["avg_impact"] = round(domain["avg_impact"] / domain["count"], 1)
            domain["coverage"] = round((domain["mapped"] / domain["count"]) * 100, 1)

        return jsonify(
            {
                "unified_capabilities": unified_capabilities,
                "statistics": {
                    "total_capabilities": total_capabilities,
                    "application_capabilities": application_capabilities,
                    "manufacturing_capabilities": manufacturing_capabilities,
                    "mapped_capabilities": mapped_capabilities,
                    "unmapped_capabilities": unmapped_capabilities,
                    "coverage_percentage": round(
                        (mapped_capabilities / total_capabilities) * 100, 2
                    )
                    if total_capabilities > 0
                    else 0,
                    "domain_distribution": domain_stats,
                },
            }
        )
    except Exception:
        return (
            jsonify(
                {
                    "error": "An internal error occurred",
                    "unified_capabilities": [],
                    "statistics": {
                        "total_capabilities": 0,
                        "application_capabilities": 0,
                        "manufacturing_capabilities": 0,
                        "mapped_capabilities": 0,
                        "unmapped_capabilities": 0,
                        "coverage_percentage": 0,
                        "domain_distribution": {},
                    },
                }
            ),
            500,
        )


@capability_map.route("/api/mappings")
@login_required
def api_mappings():
    """API endpoint to get application-capability mappings with gap analysis and filtering"""
    try:
        from app.models.application_layer import ApplicationComponent
        from app.models.business_capabilities import (
            ApplicationCapabilityCoverage,
            BusinessCapability,
        )

        # Get filter parameters
        domain_filter = request.args.get("domain", "")
        level_filter = request.args.get("level", "")

        # Get all mappings with rich data
        mappings = ApplicationCapabilityCoverage.query.limit(500).all()

        # OPTIMIZATION: Batch-prefetch capabilities and applications to avoid N+1 queries
        cap_ids = {m.capability_id for m in mappings}
        app_ids = {m.application_component_id for m in mappings}
        all_caps = BusinessCapability.query.filter(BusinessCapability.id.in_(cap_ids)).all() if cap_ids else []
        caps_by_id = {c.id: c for c in all_caps}
        all_apps_prefetch = ApplicationComponent.query.filter(ApplicationComponent.id.in_(app_ids)).all() if app_ids else []
        apps_by_id = {a.id: a for a in all_apps_prefetch}

        # Build enhanced mapping data
        mapping_data = []
        for mapping in mappings:
            # Get capability details from prefetched data
            capability = caps_by_id.get(mapping.capability_id)

            # Get application details from prefetched data
            application = apps_by_id.get(mapping.application_component_id)

            if capability and application:
                # Apply filters — use business_domain string as domain proxy
                cap_domain = capability.business_domain or "Unknown"
                if domain_filter and cap_domain != domain_filter:
                    continue
                if level_filter and str(capability.level) != level_filter:
                    continue

                # Use application business owner instead of capability business owner
                business_owner = (
                    application.business_owner or getattr(capability, "capability_owner", None) or "Unassigned"
                )

                # Calculate business impact based on real application data
                business_impact = 0

                # Use application weight if available
                app_weight = getattr(application, "application_weight", None)  # model-safety-ok: optional field not on ApplicationComponent
                if app_weight:
                    if app_weight >= 8:
                        business_impact += 40
                    elif app_weight >= 6:
                        business_impact += 30
                    elif app_weight >= 4:
                        business_impact += 20
                    else:
                        business_impact += 10

                # Use application risk level for impact
                if hasattr(application, "risk_level") and application.risk_level:  # model-safety-ok: optional field not on ApplicationComponent
                    if application.risk_level.lower() == "critical":
                        business_impact += 30
                    elif application.risk_level.lower() == "high":
                        business_impact += 20
                    elif application.risk_level.lower() == "medium":
                        business_impact += 10

                # Use capability strategic importance
                cap_strategic = getattr(capability, "strategic_importance", None)
                if cap_strategic == "critical":
                    business_impact += 30
                elif cap_strategic == "high":
                    business_impact += 20
                elif cap_strategic == "medium":
                    business_impact += 10

                # Core differentiator bonus
                if getattr(capability, "is_core_differentiator", False):
                    business_impact += 20

                # Cap at 100
                business_impact = min(business_impact, 100)

                mapping_data.append(
                    {
                        "id": str(mapping.id),
                        "capability_id": str(capability.id),
                        "capability_name": capability.name,
                        "capability_code": capability.code,
                        "capability_level": capability.level,
                        "application_id": str(application.id),
                        "application_name": application.name,
                        "application_type": application.component_type,
                        "support_level": mapping.support_level or "Primary",
                        "coverage_percentage": mapping.coverage_percentage or 80,
                        "domain": {
                            "id": None,
                            "name": capability.business_domain or "Unknown",
                            "code": "UNK",
                        },
                        "business_owner": business_owner,
                        "capability_owner": getattr(capability, "capability_owner", None),
                        "strategic_importance": cap_strategic or "medium",
                        "business_criticality": getattr(capability, "business_criticality", None) or "supporting",
                        "is_core_differentiator": getattr(capability, "is_core_differentiator", False),
                        "business_impact": business_impact,
                        # D-7: an unassessed capability must not render as a
                        # fabricated "Level 1 -> Level 3" pair.
                        "current_maturity": getattr(capability, "current_maturity_level", None),
                        "target_maturity": getattr(capability, "target_maturity_level", None),
                        "maturity_reason_code": None
                        if getattr(capability, "current_maturity_level", None) is not None
                        else "no_maturity_recorded",
                        "maturity_gap": getattr(capability, "maturity_gap", None),
                        "annual_cost": getattr(capability, "annual_cost", None),
                        "annual_revenue_impact": getattr(capability, "annual_revenue_impact", None),
                        "status": getattr(capability, "status", "defined"),
                        # Application-specific data
                        "application_weight": getattr(application, "application_weight", None),
                        "risk_level": getattr(application, "risk_level", "Unknown"),
                        "priority_for_action": getattr(
                            application, "priority_for_action", "Medium"
                        ),
                    }
                )

        # Sort by business impact (highest first)
        mapping_data.sort(key=lambda x: x["business_impact"], reverse=True)

        # Gap analysis with filters
        all_capabilities = BusinessCapability.query.limit(500).all()
        mapped_capability_ids = {mapping.capability_id for mapping in mappings}
        unmapped_capabilities = [
            cap for cap in all_capabilities if cap.id not in mapped_capability_ids
        ]

        # Apply filters to unmapped capabilities
        filtered_unmapped = []
        for capability in unmapped_capabilities:
            cap_domain = capability.business_domain or "Unknown"
            if domain_filter and cap_domain != domain_filter:
                continue
            if level_filter and str(capability.level) != level_filter:
                continue
            filtered_unmapped.append(capability)

        # Enhanced gap analysis with business priority
        gap_analysis_data = []
        for capability in filtered_unmapped:
            # Calculate business priority for unmapped capabilities
            business_priority = "Low"
            business_impact = 0
            cap_si = getattr(capability, "strategic_importance", None)
            cap_bc = getattr(capability, "business_criticality", None)

            if cap_si == "critical" or cap_bc == "mission_critical":
                business_priority = "Critical"
                business_impact = 80
            elif cap_si == "high" or cap_bc == "important":
                business_priority = "High"
                business_impact = 60
            elif cap_si == "medium":
                business_priority = "Medium"
                business_impact = 40
            else:
                business_priority = "Low"
                business_impact = 20

            if getattr(capability, "is_core_differentiator", False):
                business_priority = (
                    "Critical" if business_priority != "Critical" else business_priority
                )
                business_impact += 20

            gap_analysis_data.append(
                {
                    "id": str(capability.id),
                    "name": capability.name,
                    "code": capability.code,
                    "level": capability.level,
                    "domain": {
                        "id": None,
                        "name": capability.business_domain or "Unknown",
                        "code": "UNK",
                    },
                    "business_owner": getattr(capability, "capability_owner", None) or "Unassigned",
                    "business_priority": business_priority,
                    "business_impact": business_impact,
                    # D-7: an unassessed capability must not render as a
                    # fabricated "Level 1 -> Level 3" pair.
                    "current_maturity": getattr(capability, "current_maturity_level", None),
                    "target_maturity": getattr(capability, "target_maturity_level", None),
                    "maturity_reason_code": None
                    if getattr(capability, "current_maturity_level", None) is not None
                    else "no_maturity_recorded",
                    "maturity_gap": getattr(capability, "maturity_gap", None),
                    "is_core_differentiator": getattr(capability, "is_core_differentiator", False),
                    "annual_revenue_impact": getattr(capability, "annual_revenue_impact", None),
                }
            )

        # Sort gaps by business impact
        gap_analysis_data.sort(key=lambda x: x["business_impact"], reverse=True)

        # Calculate gap statistics
        critical_gaps = len(
            [gap for gap in gap_analysis_data if gap["business_priority"] == "Critical"]
        )
        high_priority_gaps = len(
            [gap for gap in gap_analysis_data if gap["business_priority"] == "High"]
        )
        core_differentiator_gaps = len(
            [gap for gap in gap_analysis_data if gap["is_core_differentiator"]]
        )
        total_business_impact = sum(gap["business_impact"] for gap in gap_analysis_data)

        # Get available domains for filter options — use L1 capabilities as domain proxies
        l1_caps = BusinessCapability.query.filter_by(level=1).order_by(BusinessCapability.name).all()
        domain_options = [{"code": c.name, "name": c.name} for c in l1_caps]

        return jsonify(
            {
                "mappings": mapping_data,
                "gap_analysis": {
                    "total_capabilities": len(all_capabilities),
                    "mapped_capabilities": len(mapped_capability_ids),
                    "gap_count": len(filtered_unmapped),
                    "coverage_percentage": round(
                        (len(mapped_capability_ids) / len(all_capabilities)) * 100, 2
                    )
                    if all_capabilities
                    else 0,
                    "unmapped_capabilities": gap_analysis_data,
                    "critical_gaps": critical_gaps,
                    "high_priority_gaps": high_priority_gaps,
                    "core_differentiator_gaps": core_differentiator_gaps,
                    "total_business_impact": total_business_impact,
                },
                "filters": {
                    "applied_domain": domain_filter,
                    "applied_level": level_filter,
                    "domain_options": domain_options,
                    "level_options": ["1", "2", "3"],
                },
            }
        )
    except SQLAlchemyError as e:
        current_app.logger.error(f"Database error in gap analysis: {e}")
        raise DatabaseError(
            message=f"Failed to perform gap analysis: {str(e)}",
            user_message="Unable to complete gap analysis.",
            recovery_action="Please try again. If the problem persists, contact support.",
        )
    except Exception as e:
        current_app.logger.error(f"Unexpected error in gap analysis: {e}")
        raise BusinessRuleError(
            message=f"Gap analysis failed: {str(e)}",
            user_message="Unable to generate gap analysis report.",
            recovery_action="Try adjusting your filters or refresh the page.",
        )


@capability_map.route("/api/suggest-mapping/<int:app_id>")
@login_required
def api_suggest_mapping(app_id):
    """API endpoint to get AI-suggested capability mappings for an application"""
    try:
        from app.services.application_capability_mapper import ApplicationCapabilityMapperService

        suggestions = ApplicationCapabilityMapperService.suggest_capabilities_for_application(
            application_id=app_id, top_n=5
        )
        return jsonify({"suggestions": suggestions})
    except (ImportError, AttributeError) as e:
        current_app.logger.error(f"Service error suggesting mappings: {e}")
        raise ExternalServiceError(
            message=f"Failed to load mapper service: {str(e)}",
            user_message="Unable to generate mapping suggestions.",
            recovery_action="Refresh the page. If the problem persists, contact support.",
        )
    except Exception as e:
        current_app.logger.error(f"Error suggesting mappings: {e}")
        raise BusinessRuleError(
            message=f"Mapping suggestion failed: {str(e)}",
            user_message="Unable to suggest capability mappings.",
            recovery_action="Try again or map capabilities manually.",
        )


@capability_map.route("/api/capability/<capability_id>/applications", methods=["GET"])
@login_required
def api_capability_applications(capability_id):
    """API endpoint to get all applications (mapped and unmapped) for a specific capability"""
    try:
        from app.models.application_layer import ApplicationComponent
        from app.models.business_capabilities import (
            ApplicationCapabilityCoverage,
            BusinessCapability,
        )

        # Convert capability_id to int
        try:
            capability_id_str = str(capability_id).strip()
            capability_id_int = int(capability_id_str)
        except (ValueError, TypeError):
            current_app.logger.error(f"Invalid capability ID format: {capability_id}")
            return jsonify({"error": f"Invalid capability ID format: {capability_id}"}), 400

        # Look up in BusinessCapability (real APQC data)
        capability = db.session.get(BusinessCapability, capability_id_int)
        if not capability:
            capability = BusinessCapability.query.filter(
                BusinessCapability.id == capability_id_int
            ).first()

        if not capability:
            current_app.logger.warning(
                f"Capability not found: {capability_id_int} (original: {capability_id})"
            )
            return jsonify({"error": f"Capability not found: {capability_id}"}), 404

        # Get all applications
        all_applications = ApplicationComponent.query.order_by(ApplicationComponent.name).limit(500).all()

        # Get existing mappings for this capability
        existing_mappings = ApplicationCapabilityCoverage.query.filter_by(
            capability_id=capability_id_int
        ).all()

        mapped_app_ids = {m.application_component_id for m in existing_mappings}
        mapping_details = {m.application_component_id: m for m in existing_mappings}

        # Build application list with mapping status
        applications_data = []
        for app in all_applications:
            is_mapped = app.id in mapped_app_ids
            mapping = mapping_details.get(app.id)

            app_data = {
                "id": str(app.id),  # Convert to string to preserve precision
                "name": app.name,
                "type": app.component_type or "Unknown",
                "description": app.description or "",
                "domain": app.business_domain or "Not specified",
                "owner": app.business_owner or "Not specified",
                "is_mapped": is_mapped,
                "mapping_id": str(mapping.id)
                if mapping and mapping.id
                else None,  # Convert to string to preserve precision
                "support_level": (mapping.support_level or "partial") if mapping else "partial",
                "coverage_percentage": (mapping.coverage_percentage or 0) if mapping else 0,
                "support_quality": (mapping.support_quality or 3) if mapping else 3,  # model-safety-ok: support_quality_score fallback removed, field is support_quality
                "relationship_type": (mapping.relationship_type or "enables") if mapping else "enables",
                "relationship_strength": (mapping.relationship_strength or 3) if mapping else 3,
                "dependency_level": (mapping.dependency_level or "medium") if mapping else "medium",
                "gap_status": (mapping.gap_status or "unknown") if mapping else "unknown",
                "gap_description": (mapping.gap_description or "") if mapping else "",
                "gap_impact": (mapping.gap_impact or "medium") if mapping else "medium",
                "priority": (mapping.priority or "medium") if mapping else "medium",  # model-safety-ok: replacement_priority fallback removed, field is priority
                "integration_complexity": (mapping.integration_complexity or "medium") if mapping else "medium",
                "is_active": (mapping.is_active if mapping and mapping.is_active is not None else True),
            }
            applications_data.append(app_data)

        return jsonify(
            {
                "capability": {
                    "id": str(capability.id),  # Convert to string to preserve precision
                    "name": capability.name,
                    "code": capability.code,
                    "level": capability.level,
                },
                "applications": applications_data,
                "mapped_count": len(mapped_app_ids),
                "total_count": len(applications_data),
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error getting capability applications: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


# --------------------------------------------------------------------------- dual write

def _mirror_mapping(capability_id, application_id, support_level, delete=False):
    """Keep application_capability_mapping in step with application_capability_coverage.

    Two tables model the same fact -- "this application supports this capability".
    This endpoint wrote only ApplicationCapabilityCoverage, while the flagship
    Capability Map (api_unified_capabilities), its gap analysis and its coverage
    percentage all read ApplicationCapabilityMapping. The result: you could map
    two applications on the mapping screen, see them listed there, and the
    Capability Map would still report "Mapped to Apps 0 / Coverage 0% / No
    Application Mapped" for the same capability. Two screens disagreeing about
    the same fact is the worst failure mode for a system of record.

    Consolidating onto one table is a data migration across ~600 call sites and
    is not this change; keeping both in step at the single point that writes them
    is, and it makes every existing reader correct.
    """
    from app.models.application_capability import ApplicationCapabilityMapping

    # This model carries organization_id but NOT TenantMixin, so nothing filters
    # it for us -- the predicate has to be written here. Without it the lookup
    # could match another tenant's row for the same (capability, application)
    # pair and then update or delete it, which is exactly what the insert below
    # already guards against by setting organization_id explicitly.
    existing = ApplicationCapabilityMapping.query.filter_by(
        business_capability_id=capability_id,
        application_component_id=application_id,
        organization_id=getattr(g, "current_org_id", None),
    ).first()

    if delete:
        if existing:
            db.session.delete(existing)
        return

    if existing:
        existing.support_level = support_level
    else:
        db.session.add(
            ApplicationCapabilityMapping(
                business_capability_id=capability_id,
                application_component_id=application_id,
                support_level=support_level,
                organization_id=getattr(g, "current_org_id", None),
            )
        )
    # "The field IS the element" applies to relationships too — but this is
    # ALREADY handled: app/models/archimate_relationship_sync.py's Listener 10
    # (after_insert/after_delete on ApplicationCapabilityMapping, the exact
    # model this function writes) already mirrors every row here into a real
    # ArchiMateRelationship(type="serving") the moment it's inserted/deleted.
    #
    # A prior session pass (2 Sep 2026) added a SECOND, hand-rolled mirror here
    # — type="realization" — without checking whether one already existed. It
    # did: every mapping made through this endpoint was producing TWO
    # relationships for one fact, exactly the duplicate-authority problem
    # ADR-0008 exists to prevent. Removed; the canonical listener already does
    # this, correctly, tenant-validated, and idempotently — nothing to add here.


@capability_map.route("/api/mappings", methods=["POST"])
@login_required
@audit_log("capability_mapping_create")
def api_create_mapping():
    """API endpoint to create or update application-capability mappings"""
    try:
        from app.models.application_layer import ApplicationComponent
        from app.models.business_capabilities import (
            ApplicationCapabilityCoverage,
            BusinessCapability,
        )

        data = request.get_json()
        capability_id = data.get("capability_id")
        applications = data.get("applications", [])  # List of {app_id, mapping_data}

        if not capability_id:
            current_app.logger.error("Missing capability_id in request")
            return jsonify({"error": "capability_id is required"}), 400

        # Convert string ID to int for database query
        try:
            capability_id_int = int(capability_id)
        except (ValueError, TypeError):
            current_app.logger.error(f"Invalid capability_id format: {capability_id}")
            return jsonify({"error": f"Invalid capability_id format: {capability_id}"}), 400

        # Verify capability exists
        capability = BusinessCapability.query.get(capability_id_int)
        if not capability:
            current_app.logger.error(f"Capability not found: {capability_id_int}")
            return jsonify({"error": "Capability not found"}), 404

        created_count = 0
        updated_count = 0
        errors = []

        # Batch-prefetch existing mappings for this capability to avoid N+1 queries
        existing_cap_mappings = ApplicationCapabilityCoverage.query.filter_by(
            capability_id=capability_id_int
        ).all()
        existing_by_app_id = {m.application_component_id: m for m in existing_cap_mappings}
        existing_by_mapping_id = {m.id: m for m in existing_cap_mappings}

        # Batch-prefetch application IDs to validate existence
        requested_app_ids = []
        for app_data in applications:
            try:
                requested_app_ids.append(int(app_data.get("application_id", 0)))
            except (ValueError, TypeError):
                logger.exception("Failed to operation")
                pass
        valid_app_ids = set()
        if requested_app_ids:
            valid_apps = ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(requested_app_ids)
            ).all()
            valid_app_ids = {a.id for a in valid_apps}

        for app_data in applications:
            app_id = app_data.get("application_id")
            mapping_id = app_data.get("mapping_id")  # For updates
            mapping_fields = app_data.get("mapping", {})

            if not app_id:
                errors.append("Missing application_id in application data")
                continue

            # Convert string IDs to int for database queries
            try:
                app_id_int = int(app_id)
            except (ValueError, TypeError):
                errors.append(f"Invalid application_id format: {app_id}")
                continue

            # Verify application exists using prefetched set
            if app_id_int not in valid_app_ids:
                errors.append(f"Application {app_id} not found")
                continue

            # Check if mapping already exists using prefetched data
            existing = None
            if mapping_id:
                try:
                    mapping_id_int = int(mapping_id)
                    existing = existing_by_mapping_id.get(mapping_id_int)
                    if existing and existing.capability_id != capability_id_int:
                        existing = None  # Wrong mapping, create new
                except (ValueError, TypeError):
                    pass  # Invalid mapping_id, will create new

            if not existing:
                existing = existing_by_app_id.get(app_id_int)

            if existing:
                # Update existing mapping
                ALLOWED_MAPPING_FIELDS = {"support_level", "coverage_percentage", "confidence_score", "notes", "is_strategic", "investment_priority"}
                for key, value in mapping_fields.items():
                    if key in ALLOWED_MAPPING_FIELDS and hasattr(existing, key):
                        setattr(existing, key, value)
                existing.updated_at = datetime.utcnow()
                _mirror_mapping(capability_id_int, app_id_int, existing.support_level)
                updated_count += 1
            else:
                # Create new mapping
                mapping = ApplicationCapabilityCoverage(
                    capability_id=capability_id_int,
                    application_component_id=app_id_int,
                    support_level=mapping_fields.get("support_level", "partial"),
                    coverage_percentage=mapping_fields.get("coverage_percentage", 0),
                    confidence_score=mapping_fields.get("confidence_score", 0.5),
                    is_strategic=mapping_fields.get("is_strategic", False),
                    investment_priority=mapping_fields.get("investment_priority", "medium"),
                    notes=mapping_fields.get("notes", ""),
                )
                db.session.add(mapping)
                _mirror_mapping(capability_id_int, app_id_int, mapping.support_level)
                created_count += 1

        db.session.commit()

        # COM-013: Journey 3 — Capability mapped to application
        try:
            from app.services.analytics_service import AnalyticsService
            from flask import g
            from flask_login import current_user as _cur_user
            _org_id = getattr(g, "current_org_id", None)
            _uid = getattr(_cur_user, "id", None)
            # Report first application_id from request for the event
            _first_app_id = int(applications[0].get("application_id", 0)) if applications else None
            AnalyticsService().capture(
                f"{_org_id}:{_uid}",
                "capability_mapped",
                {
                    "capability_id": capability_id_int,
                    "application_id": _first_app_id,
                    "org_id": _org_id,
                    "created_count": created_count,
                },
            )
        except Exception as exc:
            logger.debug("suppressed error in api_create_mapping (app/modules/capabilities/routes/mapping_routes.py): %s", exc)

        return jsonify(
            {
                "success": True,
                "created": created_count,
                "updated": updated_count,
                "errors": errors if errors else None,
            }
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating/updating mappings: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@capability_map.route("/api/mappings/<int:mapping_id>", methods=["DELETE"])
@login_required
@audit_log("capability_mapping_delete")
def api_delete_mapping(mapping_id):
    """API endpoint to delete an application-capability mapping"""
    try:
        from app.models.business_capabilities import ApplicationCapabilityCoverage

        from app.models.business_capabilities import BusinessCapability

        mapping = ApplicationCapabilityCoverage.query.filter_by(id=mapping_id).first()
        if not mapping:
            return jsonify({"error": "Mapping not found"}), 404

        # ApplicationCapabilityCoverage has no TenantMixin, so nothing scopes it
        # to the caller's organisation -- a bare id lookup would let any tenant
        # delete any other tenant's mapping. BusinessCapability IS tenant-filtered,
        # so resolving the parent capability is the check.
        owning_capability = BusinessCapability.query.filter_by(
            id=mapping.capability_id
        ).first()
        if owning_capability is None:
            return jsonify({"error": "Mapping not found"}), 404

        capability_id = mapping.capability_id
        application_id = mapping.application_component_id

        db.session.delete(mapping)
        _mirror_mapping(capability_id, application_id, None, delete=True)
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": "Mapping deleted successfully",
                "capability_id": capability_id,
                "application_id": application_id,
            }
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting mapping: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@capability_map.route(
    "/api/mappings/by-capability/<int:capability_id>/application/<int:application_id>",
    methods=["DELETE"],
)
@login_required
@audit_log("capability_mapping_delete")
def api_delete_mapping_by_pair(capability_id, application_id):
    """Delete a mapping addressed by the pair it connects.

    The mapping UI knows which capability and which application the user
    unticked; it has no reason to also track the surrogate id of the join row.
    Scoped through BusinessCapability, which carries the tenant filter.
    """
    try:
        from app.models.business_capabilities import (
            ApplicationCapabilityCoverage,
            BusinessCapability,
        )

        capability = BusinessCapability.query.filter_by(id=capability_id).first()
        if capability is None:
            return jsonify({"error": "Capability not found"}), 404

        mapping = ApplicationCapabilityCoverage.query.filter_by(
            capability_id=capability_id, application_component_id=application_id
        ).first()
        if mapping is None:
            return jsonify({"error": "Mapping not found"}), 404

        db.session.delete(mapping)
        _mirror_mapping(capability_id, application_id, None, delete=True)
        db.session.commit()
        return jsonify({"success": True, "capability_id": capability_id,
                        "application_id": application_id})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting mapping by pair: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


@capability_map.route("/api/manufacturing-capabilities")
@login_required
def api_manufacturing_capabilities():
    """API endpoint for manufacturing capability data"""
    try:
        from app.models.manufacturing_capability import ManufacturingCapability

        manufacturing_caps = ManufacturingCapability.query.options(
            joinedload(ManufacturingCapability.unified_capability)
        ).limit(500).all()

        manufacturing_data = []
        for cap in manufacturing_caps:
            # Get the capability name from the unified capability relationship
            capability_name = (
                cap.unified_capability.name if cap.unified_capability else f"Capability {cap.id}"
            )

            manufacturing_data.append(
                {
                    "id": cap.id,
                    "name": capability_name,
                    "description": cap.unified_capability.description
                    if cap.unified_capability
                    else "No description",
                    "manufacturing_domain": cap.manufacturing_domain,
                    "process_type": cap.manufacturing_process_type,
                    "oee_current": cap.oee_current or 0,
                    "oee_target": cap.oee_target or 0,
                    "first_pass_yield_current": cap.first_pass_yield_current or 0,
                    "first_pass_yield_target": cap.first_pass_yield_target or 0,
                    "on_time_delivery_current": cap.on_time_delivery_current or 0,
                    "on_time_delivery_target": cap.on_time_delivery_target or 0,
                    "inventory_turns_current": cap.inventory_turns_current or 0,
                    "inventory_turns_target": cap.inventory_turns_target or 0,
                }
            )

        # Calculate averages
        if manufacturing_data:
            avg_oee = sum(cap["oee_current"] for cap in manufacturing_data) / len(
                manufacturing_data
            )
            avg_fpy = sum(cap["first_pass_yield_current"] for cap in manufacturing_data) / len(
                manufacturing_data
            )
            avg_otd = sum(cap["on_time_delivery_current"] for cap in manufacturing_data) / len(
                manufacturing_data
            )
        else:
            avg_oee = avg_fpy = avg_otd = 0

        return jsonify(
            {
                "success": True,
                "data": manufacturing_data,
                "stats": {
                    "total_capabilities": len(manufacturing_data),
                    "avg_oee": round(avg_oee, 1),
                    "avg_fpy": round(avg_fpy, 1),
                    "avg_otd": round(avg_otd, 1),
                },
            }
        )
    except SQLAlchemyError as e:
        current_app.logger.error(f"Database error loading manufacturing performance: {e}")
        raise DatabaseError(
            message=f"Failed to load manufacturing performance data: {str(e)}",
            user_message="Unable to retrieve manufacturing performance metrics.",
            recovery_action="Please try again. If the problem persists, contact support.",
        )
    except Exception as e:
        current_app.logger.error(f"Error calculating manufacturing performance: {e}")
        raise BusinessRuleError(
            message=f"Performance calculation failed: {str(e)}",
            user_message="Unable to calculate manufacturing performance.",
            recovery_action="Try refreshing the page.",
        )


@capability_map.route("/api/apply-suggestion", methods=["POST"])
@login_required
@audit_log("capability_suggestion_apply")
def api_apply_suggestion():
    """API endpoint to apply a capability suggestion to create a mapping"""
    try:
        from app.services.application_capability_mapper import ApplicationCapabilityMapperService

        data = request.get_json()
        application_id = data.get("application_id")
        capability_id = data.get("capability_id")
        support_level = data.get("support_level")
        confidence_score = data.get("confidence_score")
        reasoning = data.get("reasoning")

        if not all([application_id, capability_id]):
            return jsonify({"error": "Missing required fields: application_id, capability_id"}), 400

        # Create mapping from suggestion
        mapping = ApplicationCapabilityMapperService.create_mapping_from_suggestion(
            application_id=int(application_id),
            suggestion={
                "capability_id": int(capability_id),
                "support_level": support_level,
                "confidence_score": float(confidence_score or 0),
                "reasoning": reasoning,
            },
        )

        return jsonify(
            {
                "success": True,
                "mapping": {
                    "id": mapping.id,
                    "application_id": mapping.application_component_id,
                    "capability_id": getattr(mapping, "capability_id", None) or getattr(mapping, "unified_capability_id", None),
                    "support_level": mapping.support_level,
                    "coverage_percentage": mapping.coverage_percentage,
                    "support_quality": mapping.support_quality,
                    "relationship_type": mapping.relationship_type,
                    "gap_status": mapping.gap_status,
                    "gap_description": mapping.gap_description,
                    "gap_impact": mapping.gap_impact,
                    "priority": mapping.priority,
                    "integration_complexity": mapping.integration_complexity,
                    "is_active": mapping.is_active,
                },
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error applying suggestion: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


@capability_map.route("/api/bulk-map-applications", methods=["POST"])
@login_required
@audit_log("capability_bulk_mapping_create")
def api_bulk_map_applications():
    """API endpoint to bulk map applications to capabilities"""
    try:
        from app.services.application_capability_mapper import ApplicationCapabilityMapperService

        data = request.get_json()
        confidence_threshold = data.get("confidence_threshold", 0.7)
        auto_create = data.get("auto_create", False)

        # Perform bulk mapping
        result = ApplicationCapabilityMapperService.bulk_map_applications(
            confidence_threshold=confidence_threshold, auto_create=auto_create
        )

        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"Error in bulk mapping: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


@capability_map.route("/api/statistics")
@login_required
def api_statistics():
    """API endpoint to get mapping statistics"""
    try:
        from app.models.application_layer import ApplicationComponent
        from app.models.business_capabilities import ApplicationCapabilityCoverage

        total_applications = ApplicationComponent.query.count()
        mapped_applications = (
            db.session.query(ApplicationCapabilityCoverage.application_component_id)
            .distinct()
            .count()
        )
        unmapped_applications = total_applications - mapped_applications

        coverage = (mapped_applications / total_applications * 100) if total_applications > 0 else 0

        return jsonify(
            {
                "total_applications": total_applications,
                "mapped_applications": mapped_applications,
                "unmapped_applications": unmapped_applications,
                "coverage_percentage": coverage,
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error getting statistics: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


# ---------------------------------------------------------------------------
# APQC → Capability hierarchy mapping (CAP-006)
# ---------------------------------------------------------------------------


def _tokenize(text):
    """Split text into lowercase keyword tokens for fuzzy matching."""
    import re

    if not text:
        return set()
    # Split on non-alphanumeric, lowercase, drop short tokens
    return {w for w in re.split(r"[^a-zA-Z0-9]+", text.lower()) if len(w) > 2}


@capability_map.route("/api/capabilities/apqc-suggestions")
@login_required
def api_apqc_suggestions():
    """Return ranked suggestions mapping APQC processes to BusinessCapability parents.

    For each APQC process, performs keyword matching against existing
    BusinessCapability names and domains to suggest the best parent capability
    and an appropriate child level (L3-L5).
    """
    try:
        from app.models.apqc_process import APQCProcess
        from app.models.business_capabilities import BusinessCapability

        apqc_processes = APQCProcess.query.all()
        capabilities = BusinessCapability.query.all()

        # Pre-compute capability token sets
        cap_tokens = []
        for cap in capabilities:
            tokens = _tokenize(cap.name) | _tokenize(cap.business_domain) | _tokenize(cap.category)
            cap_tokens.append((cap, tokens))

        # Find APQC processes that are already linked. Column-only selects
        # are not covered by the ambient tenant listener (unlike the
        # `BusinessCapability.query.all()` above, which is), and
        # CapabilityProcessMapping has no organization_id of its own --
        # another organisation's linked processes would otherwise be
        # excluded from this organisation's suggestions (pr306-v2 review,
        # DEFECT D5).
        from app.services.apqc_mapping_tenant_fence import (
            fenced_capability_mappings_query,
        )

        existing_links = {
            m.apqc_process_id for m in fenced_capability_mappings_query().all()
        }

        suggestions = []
        for proc in apqc_processes:
            proc_tokens = (
                _tokenize(proc.process_name)
                | _tokenize(proc.process_category)
                | _tokenize(proc.industry_domain)
            )
            if not proc_tokens:
                continue

            best_cap = None
            best_score = 0.0

            for cap, ctokens in cap_tokens:
                if not ctokens:
                    continue
                overlap = len(proc_tokens & ctokens)
                union = len(proc_tokens | ctokens)
                score = overlap / union if union else 0.0
                if score > best_score:
                    best_score = score
                    best_cap = cap

            # Determine suggested level: default to parent level + 1, capped 3-5
            suggested_level = 3
            if best_cap and best_cap.level:
                suggested_level = min(max(best_cap.level + 1, 3), 5)

            suggestions.append(
                {
                    "apqc_id": proc.id,
                    "apqc_name": proc.process_name,
                    "apqc_code": proc.process_code,
                    "suggested_capability_id": best_cap.id if best_cap else None,
                    "suggested_capability_name": best_cap.name if best_cap else None,
                    "confidence": round(best_score, 4),
                    "suggested_level": suggested_level,
                    "already_linked": proc.id in existing_links,
                }
            )

        # Sort by confidence descending
        suggestions.sort(key=lambda s: s["confidence"], reverse=True)

        return jsonify(suggestions)

    except Exception as e:
        current_app.logger.error(f"Error generating APQC suggestions: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


@capability_map.route("/api/capabilities/app-suggestions")
@login_required
def api_app_capability_suggestions():
    """Return ranked capability suggestions for an application.

    Tokenizes the application's name, description, and business_domain into
    keywords, then fuzzy-matches against BusinessCapability names using Jaccard
    similarity (same logic as ``api_apqc_suggestions``).

    Query params:
        app_id (int): Required — the ApplicationComponent ID.

    Returns JSON list sorted by confidence descending::

        [{capability_id, capability_name, capability_level, confidence, already_mapped}]
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import (
        ApplicationCapabilityCoverage,
        BusinessCapability,
    )

    app_id = request.args.get("app_id", type=int)
    if app_id is None:
        return jsonify({"error": "app_id query parameter is required"}), 400

    try:
        app_obj = db.session.get(ApplicationComponent, app_id)
        if not app_obj:
            return jsonify({"error": f"Application {app_id} not found"}), 404

        # Tokenize application attributes
        app_tokens = (
            _tokenize(app_obj.name)
            | _tokenize(app_obj.description)
            | _tokenize(app_obj.business_domain)
        )

        if not app_tokens:
            return jsonify([])

        # Load all capabilities and pre-compute token sets
        capabilities = BusinessCapability.query.all()
        cap_tokens = []
        for cap in capabilities:
            tokens = (
                _tokenize(cap.name)
                | _tokenize(cap.business_domain)
                | _tokenize(cap.category)
            )
            cap_tokens.append((cap, tokens))

        # Find capabilities already mapped to this application
        existing_mappings = {
            m.capability_id
            for m in db.session.query(
                ApplicationCapabilityCoverage.capability_id
            )
            .filter_by(application_component_id=app_id)
            .all()
        }

        suggestions = []
        for cap, ctokens in cap_tokens:
            if not ctokens:
                continue
            overlap = len(app_tokens & ctokens)
            union = len(app_tokens | ctokens)
            score = overlap / union if union else 0.0
            if score > 0.0:
                suggestions.append(
                    {
                        "capability_id": cap.id,
                        "capability_name": cap.name,
                        "capability_level": cap.level,
                        "confidence": round(score, 4),
                        "already_mapped": cap.id in existing_mappings,
                    }
                )

        # Sort by confidence descending
        suggestions.sort(key=lambda s: s["confidence"], reverse=True)

        return jsonify(suggestions)

    except Exception as e:
        current_app.logger.error(
            f"Error generating app capability suggestions: {e}"
        )
        return jsonify({"error": "An internal error occurred"}), 500


@capability_map.route("/api/capabilities/apqc-link", methods=["POST"])
@login_required
@audit_log("apqc_capability_link")
def api_apqc_link():
    """Create a BusinessCapability child linked to an APQC process.

    Accepts JSON: ``{apqc_id, capability_id, level?}``

    * Creates a new BusinessCapability with *parent_capability_id* = capability_id
    * Sets name from the APQC process name and stores the APQC reference in
      the description field.
    * Returns 409 if a CapabilityProcessMapping already links this APQC process
      to any capability.
    """
    try:
        from app.models.apqc_process import APQCProcess, CapabilityProcessMapping
        from app.models.business_capabilities import BusinessCapability

        data = request.get_json(silent=True) or {}
        apqc_id = data.get("apqc_id")
        capability_id = data.get("capability_id")
        level = data.get("level", 3)

        if not apqc_id or not capability_id:
            return jsonify({"error": "apqc_id and capability_id are required"}), 400

        apqc = db.session.get(APQCProcess, apqc_id)
        if not apqc:
            return jsonify({"error": f"APQC process {apqc_id} not found"}), 404

        parent_cap = db.session.get(BusinessCapability, capability_id)
        if not parent_cap:
            return jsonify({"error": f"Capability {capability_id} not found"}), 404

        # Check for existing mapping. CapabilityProcessMapping has no
        # organization_id of its own; unfenced, this returned another
        # organisation's existing_capability_id in the 409 body (pr306-v2
        # review, DEFECT-1). apqc_process_id is a shared reference id, so
        # scope the duplicate check to the caller's own organisation's
        # capabilities the same way the other routes in this file do.
        from app.services.apqc_mapping_tenant_fence import (
            fenced_capability_mappings_query,
        )

        existing = fenced_capability_mappings_query().filter(
            CapabilityProcessMapping.apqc_process_id == apqc_id
        ).first()
        if existing:
            return (
                jsonify(
                    {
                        "error": "This APQC process is already linked to a capability",
                        "existing_capability_id": existing.capability_id,
                    }
                ),
                409,
            )

        # Create new child capability
        child_cap = BusinessCapability(
            name=apqc.process_name,
            description=f"Derived from APQC process {apqc.process_code}: {apqc.process_name}",
            level=level,
            parent_capability_id=parent_cap.id,
            business_domain=parent_cap.business_domain,
            category=parent_cap.category,
            discovery_source="apqc_mapping",
        )
        db.session.add(child_cap)
        db.session.flush()  # get child_cap.id

        # Create the junction mapping
        mapping = CapabilityProcessMapping(
            capability_id=child_cap.id,
            apqc_process_id=apqc.id,
            relationship_type="enables",
            relationship_strength=4,
        )
        db.session.add(mapping)
        db.session.commit()

        return (
            jsonify(
                {
                    "message": "APQC process linked to capability hierarchy",
                    "child_capability": child_cap.to_dict(),
                    "mapping_id": mapping.id,
                }
            ),
            201,
        )

    except SQLAlchemyError as e:
        db.session.rollback()
        current_app.logger.error(f"Database error linking APQC to capability: {e}")
        return jsonify({"error": "A database error occurred"}), 500
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error linking APQC to capability: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


# ---------------------------------------------------------------------------
# CAP-026: Abacus auto-suggest capability mappings
# ---------------------------------------------------------------------------


@capability_map.route(
    "/api/capabilities/abacus-auto-suggest", methods=["POST"]
)
@login_required
@audit_log("abacus_capability_auto_suggest")
def api_abacus_auto_suggest():
    """Fuzzy-match Abacus-imported applications to BusinessCapability records.

    Accepts JSON::

        {
            "application_ids": [1, 2, 3],
            "threshold": 0.3,          # min confidence to include (default 0.3)
            "auto_apply_threshold": 0.7 # auto-create mapping above this (default 0.7, 0 to disable)
        }

    For each application:
      1. Tokenizes name + description + business_domain
      2. Computes Jaccard similarity against every BusinessCapability
      3. Returns suggestions sorted by confidence descending
      4. If confidence >= auto_apply_threshold, creates an
         ApplicationCapabilityCoverage record automatically

    Returns JSON::

        {
            "suggestions": [
                {
                    "application_id": 1,
                    "application_name": "...",
                    "capability_id": 5,
                    "capability_name": "...",
                    "confidence": 0.82,
                    "auto_applied": true,
                    "coverage_id": 42
                }, ...
            ],
            "summary": {
                "apps_processed": 3,
                "suggestions_total": 12,
                "auto_applied": 4
            }
        }
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import (
        ApplicationCapabilityCoverage,
        BusinessCapability,
    )

    data = request.get_json(silent=True) or {}
    application_ids = data.get("application_ids")
    if not application_ids or not isinstance(application_ids, list):
        return jsonify({"error": "application_ids (list of ints) is required"}), 400

    try:
        threshold = float(data.get("threshold", 0.3))
        auto_apply_threshold = float(data.get("auto_apply_threshold", 0.7))
    except (ValueError, TypeError):
        return jsonify({"error": "threshold and auto_apply_threshold must be numbers"}), 400

    try:
        # Load requested applications (filter to Abacus-sourced ones)
        applications = (
            ApplicationComponent.query
            .filter(ApplicationComponent.id.in_(application_ids))
            .all()
        )

        if not applications:
            return jsonify({"error": "No applications found for the given IDs"}), 404

        # Pre-compute capability token sets once
        capabilities = BusinessCapability.query.all()
        cap_tokens = []
        for cap in capabilities:
            tokens = (
                _tokenize(cap.name)
                | _tokenize(getattr(cap, "business_domain", None))
                | _tokenize(getattr(cap, "category", None))
            )
            cap_tokens.append((cap, tokens))

        # Pre-load all existing coverage mappings for these apps in one query
        existing_coverage = set()
        if applications:
            rows = (
                db.session.query(
                    ApplicationCapabilityCoverage.application_component_id,
                    ApplicationCapabilityCoverage.capability_id,
                )
                .filter(
                    ApplicationCapabilityCoverage.application_component_id.in_(
                        [a.id for a in applications]
                    )
                )
                .all()
            )
            existing_coverage = {(r[0], r[1]) for r in rows}

        suggestions = []
        auto_applied_count = 0

        for app_obj in applications:
            app_tokens = (
                _tokenize(app_obj.name)
                | _tokenize(app_obj.description)
                | _tokenize(app_obj.business_domain)
            )
            if not app_tokens:
                continue

            for cap, ctokens in cap_tokens:
                if not ctokens:
                    continue
                overlap = len(app_tokens & ctokens)
                union = len(app_tokens | ctokens)
                score = overlap / union if union else 0.0

                if score < threshold:
                    continue

                already_mapped = (app_obj.id, cap.id) in existing_coverage
                auto_applied = False
                coverage_id = None

                # Auto-apply if above threshold and not already mapped
                if (
                    score >= auto_apply_threshold
                    and auto_apply_threshold > 0
                    and not already_mapped
                ):
                    coverage = ApplicationCapabilityCoverage(
                        application_component_id=app_obj.id,
                        capability_id=cap.id,
                        support_level="partial",
                        coverage_percentage=round(score * 100, 1),
                        confidence_score=round(score, 4),
                        notes=(
                            f"Auto-suggested by Abacus import "
                            f"(Jaccard={score:.4f})"
                        ),
                    )
                    db.session.add(coverage)
                    db.session.flush()
                    coverage_id = coverage.id
                    auto_applied = True
                    auto_applied_count += 1
                    existing_coverage.add((app_obj.id, cap.id))

                suggestions.append(
                    {
                        "application_id": app_obj.id,
                        "application_name": app_obj.name,
                        "capability_id": cap.id,
                        "capability_name": cap.name,
                        "capability_level": cap.level,
                        "confidence": round(score, 4),
                        "already_mapped": already_mapped,
                        "auto_applied": auto_applied,
                        "coverage_id": coverage_id,
                    }
                )

        # Commit any auto-applied mappings
        if auto_applied_count > 0:
            db.session.commit()

        # Sort all suggestions by confidence descending
        suggestions.sort(key=lambda s: s["confidence"], reverse=True)

        return jsonify(
            {
                "suggestions": suggestions,
                "summary": {
                    "apps_processed": len(applications),
                    "suggestions_total": len(suggestions),
                    "auto_applied": auto_applied_count,
                },
            }
        )

    except SQLAlchemyError as e:
        db.session.rollback()
        current_app.logger.error(
            f"Database error in abacus auto-suggest: {e}"
        )
        return jsonify({"error": "A database error occurred"}), 500
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"Error in abacus auto-suggest: {e}"
        )
        return jsonify({"error": "An internal error occurred"}), 500


@capability_map.route(
    "/api/capabilities/abacus-pending-suggestions", methods=["GET"]
)
@login_required
def api_abacus_pending_suggestions():
    """Return Abacus-imported applications that lack capability mappings.

    Finds all ApplicationComponent records where ``abacus_source=True`` and
    no ApplicationCapabilityCoverage row exists, then runs the same Jaccard
    fuzzy-match to produce ready-to-review suggestions.

    Query params:
        threshold (float): Minimum confidence to include (default 0.3).
        limit (int): Max applications to process (default 50).

    Returns JSON::

        {
            "pending_apps": [
                {
                    "application_id": 1,
                    "application_name": "...",
                    "suggestions": [
                        {"capability_id": 5, "capability_name": "...", "confidence": 0.65},
                        ...
                    ]
                }
            ],
            "total_pending": 12
        }
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import (
        ApplicationCapabilityCoverage,
        BusinessCapability,
    )

    threshold = request.args.get("threshold", 0.3, type=float)
    limit = safe_int_arg('limit', 50, minimum=1, maximum=500)

    try:
        # Subquery: app IDs that already have at least one coverage mapping
        mapped_app_ids = (
            db.session.query(
                ApplicationCapabilityCoverage.application_component_id
            )
            .distinct()
            .subquery()
        )

        # Abacus-sourced apps with no capability coverage
        pending_apps = (
            ApplicationComponent.query
            .filter(
                ApplicationComponent.abacus_source.is_(True),
                ~ApplicationComponent.id.in_(
                    db.session.query(mapped_app_ids)
                ),
            )
            .limit(limit)
            .all()
        )

        total_pending = (
            ApplicationComponent.query
            .filter(
                ApplicationComponent.abacus_source.is_(True),
                ~ApplicationComponent.id.in_(
                    db.session.query(mapped_app_ids)
                ),
            )
            .count()
        )

        if not pending_apps:
            return jsonify({"pending_apps": [], "total_pending": 0})

        # Pre-compute capability token sets
        capabilities = BusinessCapability.query.all()
        cap_tokens = []
        for cap in capabilities:
            tokens = (
                _tokenize(cap.name)
                | _tokenize(getattr(cap, "business_domain", None))
                | _tokenize(getattr(cap, "category", None))
            )
            cap_tokens.append((cap, tokens))

        result = []
        for app_obj in pending_apps:
            app_tokens = (
                _tokenize(app_obj.name)
                | _tokenize(app_obj.description)
                | _tokenize(app_obj.business_domain)
            )
            if not app_tokens:
                result.append(
                    {
                        "application_id": app_obj.id,
                        "application_name": app_obj.name,
                        "suggestions": [],
                    }
                )
                continue

            app_suggestions = []
            for cap, ctokens in cap_tokens:
                if not ctokens:
                    continue
                overlap = len(app_tokens & ctokens)
                union = len(app_tokens | ctokens)
                score = overlap / union if union else 0.0

                if score >= threshold:
                    app_suggestions.append(
                        {
                            "capability_id": cap.id,
                            "capability_name": cap.name,
                            "capability_level": cap.level,
                            "confidence": round(score, 4),
                        }
                    )

            # Sort suggestions by confidence descending
            app_suggestions.sort(
                key=lambda s: s["confidence"], reverse=True
            )

            result.append(
                {
                    "application_id": app_obj.id,
                    "application_name": app_obj.name,
                    "suggestions": app_suggestions,
                }
            )

        return jsonify(
            {"pending_apps": result, "total_pending": total_pending}
        )

    except Exception as e:
        current_app.logger.error(
            f"Error fetching Abacus pending suggestions: {e}"
        )
        return jsonify({"error": "An internal error occurred"}), 500
