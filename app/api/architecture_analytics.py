"""
Architecture Analytics API Routes

Provides comprehensive architectural analysis endpoints for:
- Strategic alignment views
- Dependency mapping and analysis
- Gap analysis and redundancy detection
- Roadmap integration
- Reference architecture patterns
- Quality attributes modeling
- Architectural governance
"""

import logging

from flask import Blueprint, jsonify, request
from flask_login import login_required  # dead-code-ok

from ..models.base_models import Application, Solution
from ..models.capability_mapping import UnifiedToApplicationMapping  # dead-code-ok
from ..models.unified_capability import UnifiedCapability

architecture_analytics_bp = Blueprint(
    "architecture_analytics", __name__, url_prefix="/api/architecture/analytics"
)
logger = logging.getLogger(__name__)


@architecture_analytics_bp.route("/capability-heatmap", methods=["GET"])
@login_required
def get_capability_heatmap():
    """
    Generate capability heatmap showing strategic importance vs maturity/coverage.

    Query params:
        entity_type: 'solution' or 'application'
        entity_id: ID of the entity
        dimension: 'maturity' or 'coverage' (what to plot)
    """
    entity_type = request.args.get("entity_type", "application")
    entity_id = request.args.get("entity_id", type=int)
    dimension = request.args.get("dimension", "coverage")  # maturity or coverage

    if not entity_id:
        return jsonify({"success": False, "error": "entity_id required"}), 400

    try:
        # Get capability mappings
        if entity_type == "application":
            mappings = UnifiedToApplicationMapping.query.filter_by(application_id=entity_id).all()
        elif entity_type == "solution":
            # Get applications in solution, then aggregate their capabilities
            solution = Solution.query.get_or_404(entity_id)
            app_ids = [app.id for app in solution.applications]
            mappings = UnifiedToApplicationMapping.query.filter(
                UnifiedToApplicationMapping.application_id.in_(app_ids)
            ).all()
        else:
            return jsonify({"success": False, "error": "Invalid entity_type"}), 400

        # Build heatmap data
        heatmap_data = []
        for mapping in mappings:
            # Calculate strategic importance (1 - 5 scale based on coverage_type)
            strategic_importance = {"core": 5, "supporting": 3, "optional": 1}.get(
                mapping.coverage_type, 2
            )

            # Get dimension value
            if dimension == "maturity":
                value = mapping.maturity_level or 0
            else:  # coverage
                value = mapping.coverage_percentage or 0

            heatmap_data.append(
                {
                    "capability_id": mapping.unified_capability_id,
                    "capability_name": mapping.unified_capability_name,
                    "category": mapping.category,
                    "strategic_importance": strategic_importance,
                    "value": value,
                    "coverage_type": mapping.coverage_type,
                    "notes": mapping.notes,
                }
            )

        return jsonify(
            {
                "success": True,
                "heatmap": heatmap_data,
                "dimension": dimension,
                "entity_type": entity_type,
                "entity_id": entity_id,
            }
        )

    except Exception as e:
        logger.error(
            "Capability heatmap failed route=%s entity_type=%s entity_id=%s dimension=%s: %s",
            request.path,
            entity_type,
            entity_id,
            dimension,
            e,
            exc_info=True,
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@architecture_analytics_bp.route("/dependency-graph", methods=["GET"])
@login_required
def get_dependency_graph():
    """
    Generate dependency graph showing capability interdependencies.

    Query params:
        entity_type: 'solution' or 'application'
        entity_id: ID of the entity
        depth: depth of dependency analysis (1 - 3)
    """
    entity_type = request.args.get("entity_type", "application")
    entity_id = request.args.get("entity_id", type=int)
    depth = request.args.get("depth", 2, type=int)

    if not entity_id:
        return jsonify({"success": False, "error": "entity_id required"}), 400

    try:
        # Get capability mappings
        if entity_type == "application":
            mappings = UnifiedToApplicationMapping.query.filter_by(application_id=entity_id).all()
        elif entity_type == "solution":
            solution = Solution.query.get_or_404(entity_id)
            app_ids = [app.id for app in solution.applications]
            mappings = UnifiedToApplicationMapping.query.filter(
                UnifiedToApplicationMapping.application_id.in_(app_ids)
            ).all()
        else:
            return jsonify({"success": False, "error": "Invalid entity_type"}), 400

        # Build nodes and edges
        nodes = []
        edges = []
        node_ids = set()

        for mapping in mappings:
            node_id = f"cap_{mapping.unified_capability_id}"

            if node_id not in node_ids:
                nodes.append(
                    {
                        "id": node_id,
                        "label": mapping.unified_capability_name,
                        "type": "capability",
                        "category": mapping.category,
                        "coverage_type": mapping.coverage_type,
                        "strategic_weight": {"core": 3, "supporting": 2, "optional": 1}.get(
                            mapping.coverage_type, 1
                        ),
                    }
                )
                node_ids.add(node_id)

            # Create dependency edges (for now, based on category relationships)
            # In a real implementation, this would query a dependency table
            for other_mapping in mappings:
                if mapping.id != other_mapping.id and mapping.category == other_mapping.category:
                    edge_id = f"{node_id}_to_cap_{other_mapping.unified_capability_id}"
                    edges.append(
                        {
                            "id": edge_id,
                            "source": node_id,
                            "target": f"cap_{other_mapping.unified_capability_id}",
                            "type": "related",
                            "strength": 0.5,
                        }
                    )

        # Add application/solution nodes
        if entity_type == "application":
            app = Application.query.get(entity_id)
            if app:
                app_node = {
                    "id": f"app_{app.id}",
                    "label": app.name,
                    "type": "application",
                    "central": True,
                }
                nodes.append(app_node)

                # Connect capabilities to application
                for mapping in mappings:
                    edges.append(
                        {
                            "id": f"app_{app.id}_to_cap_{mapping.unified_capability_id}",
                            "source": f"app_{app.id}",
                            "target": f"cap_{mapping.unified_capability_id}",
                            "type": "provides",
                            "strength": 1.0,
                        }
                    )

        return jsonify(
            {
                "success": True,
                "graph": {"nodes": nodes, "edges": edges},
                "entity_type": entity_type,
                "entity_id": entity_id,
                "depth": depth,
            }
        )

    except Exception as e:
        logger.error(
            "Dependency graph failed route=%s entity_type=%s entity_id=%s depth=%s: %s",
            request.path,
            entity_type,
            entity_id,
            depth,
            e,
            exc_info=True,
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@architecture_analytics_bp.route("/gap-analysis", methods=["GET"])
@login_required
def get_gap_analysis():
    """
    Perform comprehensive gap analysis identifying:
    - Missing capabilities
    - Capability gaps
    - Redundant capabilities
    - Under-invested capabilities

    Query params:
        entity_type: 'solution' or 'application'
        entity_id: ID of the entity
        target_profile: Optional target capability profile
    """
    entity_type = request.args.get("entity_type", "application")
    entity_id = request.args.get("entity_id", type=int)
    target_profile = request.args.get("target_profile")  # e.g., 'enterprise', 'startup'

    if not entity_id:
        return jsonify({"success": False, "error": "entity_id required"}), 400

    try:
        # Get current capability mappings
        if entity_type == "application":
            current_mappings = UnifiedToApplicationMapping.query.filter_by(
                application_id=entity_id
            ).all()
        elif entity_type == "solution":
            solution = Solution.query.get_or_404(entity_id)
            app_ids = [app.id for app in solution.applications]
            current_mappings = UnifiedToApplicationMapping.query.filter(
                UnifiedToApplicationMapping.application_id.in_(app_ids)
            ).all()
        else:
            return jsonify({"success": False, "error": "Invalid entity_type"}), 400

        # Identify capability gaps using SQL NOT IN — avoids loading the full table
        # into Python only to discard already-mapped rows.
        current_cap_ids = {m.unified_capability_id for m in current_mappings}
        missing_caps_query = UnifiedCapability.active()
        if current_cap_ids:
            missing_caps_query = missing_caps_query.filter(
                ~UnifiedCapability.id.in_(current_cap_ids)
            )
        missing_caps = missing_caps_query.all()

        missing_capabilities = [
            {
                "capability_id": cap.id,
                "capability_name": cap.capability_name,
                "category": cap.category,
                "recommended": cap.category in ["Data Management", "Security"],
                "reason": "Core enterprise capability not covered",
            }
            for cap in missing_caps
        ]

        # Identify under-invested capabilities (low maturity or coverage)
        under_invested = []
        for mapping in current_mappings:
            if (mapping.coverage_percentage or 0) < 50 or (mapping.maturity_level or 0) < 3:
                under_invested.append(
                    {
                        "capability_id": mapping.unified_capability_id,
                        "capability_name": mapping.unified_capability_name,
                        "coverage_percentage": mapping.coverage_percentage,
                        "maturity_level": mapping.maturity_level,
                        "coverage_type": mapping.coverage_type,
                        "recommendation": "Increase investment or remove if not strategic",
                    }
                )

        # Identify potential redundancies (multiple apps providing same capability in solutions)
        redundancies = []
        if entity_type == "solution":
            # Group by capability
            cap_providers = {}
            for mapping in current_mappings:
                cap_id = mapping.unified_capability_id
                if cap_id not in cap_providers:
                    cap_providers[cap_id] = []
                cap_providers[cap_id].append(mapping)

            # Find capabilities with multiple providers
            for cap_id, providers in cap_providers.items():
                if len(providers) > 1:
                    redundancies.append(
                        {
                            "capability_id": cap_id,
                            "capability_name": providers[0].unified_capability_name,
                            "provider_count": len(providers),
                            "providers": [
                                {
                                    "application_id": p.application_id,
                                    "coverage_type": p.coverage_type,
                                    "coverage_percentage": p.coverage_percentage,
                                }
                                for p in providers
                            ],
                            "recommendation": "Consolidate or clarify ownership",
                        }
                    )

        return jsonify(
            {
                "success": True,
                "gap_analysis": {
                    "missing_capabilities": missing_capabilities[:20],  # Limit results
                    "under_invested": under_invested,
                    "redundancies": redundancies,
                    "summary": {
                        "current_count": len(current_mappings),
                        "missing_count": len(missing_capabilities),
                        "under_invested_count": len(under_invested),
                        "redundancy_count": len(redundancies),
                    },
                },
                "entity_type": entity_type,
                "entity_id": entity_id,
            }
        )

    except Exception as e:
        logger.error(
            "Gap analysis failed route=%s entity_type=%s entity_id=%s target_profile=%s: %s",
            request.path,
            entity_type,
            entity_id,
            target_profile,
            e,
            exc_info=True,
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@architecture_analytics_bp.route("/quality-attributes", methods=["GET"])
@login_required
def get_quality_attributes():
    """
    Assess quality attributes across capabilities:
    - Performance
    - Scalability
    - Security
    - Reliability
    - Maintainability

    Query params:
        entity_type: 'solution' or 'application'
        entity_id: ID of the entity
    """
    entity_type = request.args.get("entity_type", "application")
    entity_id = request.args.get("entity_id", type=int)

    if not entity_id:
        return jsonify({"success": False, "error": "entity_id required"}), 400

    try:
        # Get capability mappings
        if entity_type == "application":
            mappings = UnifiedToApplicationMapping.query.filter_by(application_id=entity_id).all()
        elif entity_type == "solution":
            solution = Solution.query.get_or_404(entity_id)
            app_ids = [app.id for app in solution.applications]
            mappings = UnifiedToApplicationMapping.query.filter(
                UnifiedToApplicationMapping.application_id.in_(app_ids)
            ).all()
        else:
            return jsonify({"success": False, "error": "Invalid entity_type"}), 400

        # Calculate quality attribute scores
        # In a real implementation, these would be stored in DB or calculated from metrics
        quality_scores = {
            "performance": 0,
            "scalability": 0,
            "security": 0,
            "reliability": 0,
            "maintainability": 0,
        }

        capability_assessments = []

        for mapping in mappings:
            # Simplified quality attribute calculation using maturity level and coverage data
            maturity = mapping.maturity_level or 3
            coverage = mapping.coverage_percentage or 50

            # Calculate quality scores (simplified algorithm)
            perf_score = min(100, int(maturity * 15 + coverage * 0.3))
            scale_score = min(100, int(maturity * 18 + (100 - coverage) * 0.1))
            sec_score = min(100, int(maturity * 20))
            rel_score = min(100, int(maturity * 16 + coverage * 0.2))
            maint_score = min(100, int(maturity * 14 + coverage * 0.4))

            quality_scores["performance"] += perf_score
            quality_scores["scalability"] += scale_score
            quality_scores["security"] += sec_score
            quality_scores["reliability"] += rel_score
            quality_scores["maintainability"] += maint_score

            capability_assessments.append(
                {
                    "capability_name": mapping.unified_capability_name,
                    "category": mapping.category,
                    "quality_attributes": {
                        "performance": perf_score,
                        "scalability": scale_score,
                        "security": sec_score,
                        "reliability": rel_score,
                        "maintainability": maint_score,
                    },
                }
            )

        # Average the scores
        count = len(mappings) or 1
        for key in quality_scores:
            quality_scores[key] = int(quality_scores[key] / count)

        return jsonify(
            {
                "success": True,
                "quality_attributes": {
                    "overall_scores": quality_scores,
                    "capability_assessments": capability_assessments,
                    "recommendations": generate_quality_recommendations(quality_scores),
                },
                "entity_type": entity_type,
                "entity_id": entity_id,
            }
        )

    except Exception as e:
        logger.error(
            "Quality attributes analysis failed route=%s entity_type=%s entity_id=%s: %s",
            request.path,
            entity_type,
            entity_id,
            e,
            exc_info=True,
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@architecture_analytics_bp.route("/architecture-patterns", methods=["GET"])
@login_required
def get_architecture_patterns():
    """
    Identify and recommend architectural patterns based on capabilities.

    Query params:
        entity_type: 'solution' or 'application'
        entity_id: ID of the entity
    """
    entity_type = request.args.get("entity_type", "application")
    entity_id = request.args.get("entity_id", type=int)

    if not entity_id:
        return jsonify({"success": False, "error": "entity_id required"}), 400

    try:
        # Get capability mappings
        if entity_type == "application":
            mappings = UnifiedToApplicationMapping.query.filter_by(application_id=entity_id).all()
        elif entity_type == "solution":
            solution = Solution.query.get_or_404(entity_id)
            app_ids = [app.id for app in solution.applications]
            mappings = UnifiedToApplicationMapping.query.filter(
                UnifiedToApplicationMapping.application_id.in_(app_ids)
            ).all()
        else:
            return jsonify({"success": False, "error": "Invalid entity_type"}), 400

        # Analyze capability patterns
        categories = {m.category for m in mappings}
        capability_count = len(mappings)

        # Pattern detection logic
        detected_patterns = []
        recommended_patterns = []

        # Microservices pattern detection
        if capability_count > 10 and "API" in categories:
            detected_patterns.append(
                {
                    "pattern": "Microservices Architecture",
                    "confidence": 0.75,
                    "evidence": "High capability count with API capabilities",
                    "benefits": ["Scalability", "Independent deployment", "Technology diversity"],
                    "considerations": [
                        "Increased complexity",
                        "Network overhead",
                        "Distributed transactions",
                    ],
                }
            )

        # Event-driven pattern detection
        if "Integration" in categories or "Messaging" in categories:
            detected_patterns.append(
                {
                    "pattern": "Event-Driven Architecture",
                    "confidence": 0.65,
                    "evidence": "Integration and messaging capabilities present",
                    "benefits": ["Loose coupling", "Scalability", "Real-time processing"],
                    "considerations": [
                        "Event schema management",
                        "Eventual consistency",
                        "Debugging complexity",
                    ],
                }
            )

        # Layered architecture (default fallback)
        if capability_count < 8:
            recommended_patterns.append(
                {
                    "pattern": "Layered Architecture",
                    "suitability": 0.8,
                    "rationale": "Lower complexity, suitable for smaller applications",
                    "benefits": [
                        "Simplicity",
                        "Clear separation of concerns",
                        "Easy to understand",
                    ],
                    "implementation_notes": "Consider 3 - tier or 4 - tier layered approach",
                }
            )

        # CQRS pattern recommendation
        if "Data Management" in categories and capability_count > 5:
            recommended_patterns.append(
                {
                    "pattern": "CQRS (Command Query Responsibility Segregation)",
                    "suitability": 0.6,
                    "rationale": "Complex data management with multiple capabilities",
                    "benefits": ["Optimized read/write paths", "Scalability", "Performance"],
                    "implementation_notes": "Separate read and write models for complex domains",
                }
            )

        return jsonify(
            {
                "success": True,
                "architecture_patterns": {
                    "detected": detected_patterns,
                    "recommended": recommended_patterns,
                    "capability_profile": {
                        "total_capabilities": capability_count,
                        "categories": list(categories),
                        "complexity": "high"
                        if capability_count > 15
                        else "medium"
                        if capability_count > 8
                        else "low",
                    },
                },
                "entity_type": entity_type,
                "entity_id": entity_id,
            }
        )

    except Exception as e:
        logger.error(
            "Architecture patterns analysis failed route=%s entity_type=%s entity_id=%s: %s",
            request.path,
            entity_type,
            entity_id,
            e,
            exc_info=True,
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@architecture_analytics_bp.route("/investment-portfolio", methods=["GET"])
@login_required
def get_investment_portfolio():
    """
    Generate investment portfolio view showing where resources are allocated.

    Query params:
        entity_type: 'solution' or 'application'
        entity_id: ID of the entity
    """
    entity_type = request.args.get("entity_type", "application")
    entity_id = request.args.get("entity_id", type=int)

    if not entity_id:
        return jsonify({"success": False, "error": "entity_id required"}), 400

    try:
        # Get capability mappings
        if entity_type == "application":
            mappings = UnifiedToApplicationMapping.query.filter_by(application_id=entity_id).all()
        elif entity_type == "solution":
            solution = Solution.query.get_or_404(entity_id)
            app_ids = [app.id for app in solution.applications]
            mappings = UnifiedToApplicationMapping.query.filter(
                UnifiedToApplicationMapping.application_id.in_(app_ids)
            ).all()
        else:
            return jsonify({"success": False, "error": "Invalid entity_type"}), 400

        # Calculate investment by coverage type
        investment_by_coverage = {"core": 0, "supporting": 0, "optional": 0}
        investment_by_category = {}

        for mapping in mappings:
            coverage = mapping.coverage_type or "optional"
            category = mapping.category or "Other"

            # Weight investment by maturity and coverage
            investment_value = (
                (mapping.maturity_level or 3) * (mapping.coverage_percentage or 50) / 100
            )

            investment_by_coverage[coverage] += investment_value
            if category not in investment_by_category:
                investment_by_category[category] = 0
            investment_by_category[category] += investment_value

        # Sort categories by investment
        sorted_categories = sorted(investment_by_category.items(), key=lambda x: x[1], reverse=True)

        return jsonify(
            {
                "success": True,
                "investment_portfolio": {
                    "by_coverage_type": investment_by_coverage,
                    "by_category": dict(sorted_categories),
                    "top_investments": sorted_categories[:5],
                    "recommendations": [
                        {
                            "type": "rebalance",
                            "message": "Consider balancing investment across categories",
                            "priority": "medium",
                        }
                        if len(sorted_categories) > 3
                        and sorted_categories[0][1] > sum(x[1] for x in sorted_categories[1:])
                        else None
                    ],
                },
                "entity_type": entity_type,
                "entity_id": entity_id,
            }
        )

    except Exception as e:
        logger.error(
            "Investment portfolio analysis failed route=%s entity_type=%s entity_id=%s: %s",
            request.path,
            entity_type,
            entity_id,
            e,
            exc_info=True,
        )
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


def generate_quality_recommendations(quality_scores):
    """Generate recommendations based on quality scores."""
    recommendations = []

    for attribute, score in quality_scores.items():
        if score < 60:
            recommendations.append(
                {
                    "attribute": attribute,
                    "current_score": score,
                    "priority": "high",
                    "recommendation": f"Improve {attribute} through targeted investments",
                    "actions": [
                        f"Review {attribute} requirements",
                        f"Implement {attribute} monitoring",
                        f"Address {attribute} technical debt",
                    ],
                }
            )
        elif score < 75:
            recommendations.append(
                {
                    "attribute": attribute,
                    "current_score": score,
                    "priority": "medium",
                    "recommendation": f"Monitor and maintain {attribute}",
                    "actions": [
                        f"Establish {attribute} benchmarks",
                        f"Regular {attribute} assessments",
                    ],
                }
            )

    return recommendations
