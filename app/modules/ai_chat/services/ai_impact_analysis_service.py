"""
-> app.modules.ai_chat.services.ai_analysis_service

AI-Powered Impact Analysis Service

Provides intelligent impact analysis capabilities including:
- Multi-application portfolio analysis
- AI-driven risk quantification
- Automated mitigation planning
- Scenario comparison
- Business impact translation

CRITICAL POLICY: NO HARDCODED DATA
===================================
All LLM configurations come from database APISettings only.
"""

# Force reload check
import json
import logging
from datetime import datetime
from typing import Any, Dict, List


from app.models.application_portfolio import ApplicationComponent
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class AIImpactAnalysisService:
    """
    AI-powered impact analysis service providing intelligent risk assessment,
    mitigation planning, and business impact translation.
    """

    # Risk weights for different impact types (based on enterprise architecture best practices)
    RISK_WEIGHTS = {
        "business_process": 25,
        "application_component": 15,
        "data_object": 20,
        "technology_service": 10,
        "business_capability": 30,
        "compliance": 35,
        "revenue": 40,
    }

    @staticmethod
    def analyze_application_impact(
        app_id: int, scenario: str, include_ai_analysis: bool = True
    ) -> Dict[str, Any]:
        """
        Perform comprehensive impact analysis for an application change scenario.

        Args:
            app_id: Application component ID
            scenario: Change scenario type (retirement, upgrade, cloud_migration, vendor_switch, custom)
            include_ai_analysis: Whether to include AI-powered insights

        Returns:
            Dictionary containing complete impact analysis results
        """
        app = ApplicationComponent.query.get_or_404(app_id)

        # Step 1: Graph-based dependency analysis
        graph_analysis = AIImpactAnalysisService._analyze_dependencies(app)

        # Step 2: Calculate risk score with weighted factors
        risk_assessment = AIImpactAnalysisService._calculate_risk_score(
            app, graph_analysis, scenario
        )

        # Step 3: Get AI-powered insights if enabled and configured
        ai_insights = {}
        if include_ai_analysis:
            try:
                ai_insights = AIImpactAnalysisService._generate_ai_insights(
                    app, graph_analysis, scenario, risk_assessment
                )
            except ValueError as e:
                # LLM not configured - provide graceful degradation
                logger.warning(f"AI analysis unavailable: {e}")
                ai_insights = {
                    "available": False,
                    "message": str(e),
                    "fallback_recommendations": AIImpactAnalysisService._generate_rule_based_recommendations(
                        scenario, risk_assessment
                    ),
                }

        # Step 4: Generate graph visualization data
        graph_data = AIImpactAnalysisService._format_graph_visualization(app.name, graph_analysis)

        return {
            "application": {
                "id": app.id,
                "name": app.name,
                "description": app.description,
                "lifecycle_status": app.lifecycle_status,
                "business_criticality": app.business_criticality,
            },
            "scenario": scenario,
            "timestamp": datetime.utcnow().isoformat(),
            "dependency_analysis": graph_analysis,
            "risk_assessment": risk_assessment,
            "ai_insights": ai_insights,
            "graph_data": graph_data,
            "summary": AIImpactAnalysisService._generate_summary(app, risk_assessment, ai_insights),
        }

    @staticmethod
    def analyze_portfolio_impact(app_ids: List[int], scenario: str) -> Dict[str, Any]:
        """
        Analyze impact across multiple applications simultaneously.

        Useful for:
        - Technology stack consolidation
        - Vendor contract renegotiation
        - Platform migration planning
        """
        results = []
        aggregate_risk = 0
        all_affected_capabilities = set()

        for app_id in app_ids:
            try:
                analysis = AIImpactAnalysisService.analyze_application_impact(
                    app_id, scenario, include_ai_analysis=False
                )
                results.append(analysis)
                aggregate_risk += analysis["risk_assessment"]["total_score"]

                # Collect affected capabilities
                for cap in analysis["dependency_analysis"].get("affected_capabilities", []):
                    all_affected_capabilities.add(cap["name"])

            except Exception as e:
                logger.error(f"Failed to analyze app {app_id}: {e}")
                results.append({"application": {"id": app_id}, "error": str(e)})

        # Calculate portfolio-level metrics
        avg_risk = aggregate_risk / len(app_ids) if app_ids else 0

        # Calculate shared dependencies and unique blast radius
        component_counts = {}
        unique_impacts = set()

        for res in results:
            if "error" in res:
                continue

            # Add direct impacts
            for item in res["dependency_analysis"].get("direct_impacts", []):
                item_id = f"{item.get('type')}:{item.get('name')}"
                component_counts[item_id] = component_counts.get(item_id, 0) + 1
                unique_impacts.add(item_id)

            # Add indirect impacts
            indirect = res["dependency_analysis"].get("indirect_impacts", {})
            if isinstance(indirect, dict):
                for depth_items in indirect.values():
                    for item in depth_items:
                        item_id = f"{item.get('type')}:{item.get('name')}"
                        component_counts[item_id] = component_counts.get(item_id, 0) + 1
                        unique_impacts.add(item_id)

        # Identify shared dependencies (impacted by more than 1 application)
        shared_dependencies = [{"id": k, "count": v} for k, v in component_counts.items() if v > 1]

        return {
            "portfolio_analysis": {
                "total_applications": len(app_ids),
                "aggregate_risk_score": aggregate_risk,
                "average_risk_score": round(avg_risk, 1),
                "risk_level": AIImpactAnalysisService._score_to_risk_level(avg_risk),
                "affected_capabilities_count": len(all_affected_capabilities),
                "affected_capabilities": list(all_affected_capabilities),
                "unique_blast_radius": len(unique_impacts),
                "shared_dependencies": shared_dependencies,
                "shared_dependencies_count": len(shared_dependencies),
            },
            "individual_analyses": results,
            "timestamp": datetime.utcnow().isoformat(),
        }

    @staticmethod
    def compare_scenarios(app_id: int, scenarios: List[str]) -> Dict[str, Any]:
        """
        Compare multiple change scenarios for the same application.

        Useful for decision-making when choosing between different approaches.
        """
        comparisons = []

        for scenario in scenarios:
            analysis = AIImpactAnalysisService.analyze_application_impact(
                app_id, scenario, include_ai_analysis=True
            )
            comparisons.append(
                {
                    "scenario": scenario,
                    "risk_score": analysis["risk_assessment"]["total_score"],
                    "risk_level": analysis["risk_assessment"]["risk_level"],
                    "blast_radius": analysis["dependency_analysis"]["blast_radius"],
                    "ai_recommendation": analysis["ai_insights"].get("recommendation", "N/A"),
                }
            )

        # Sort by risk score (lowest first)
        comparisons.sort(key=lambda x: x["risk_score"])

        return {
            "application_id": app_id,
            "scenario_comparisons": comparisons,
            "recommended_scenario": comparisons[0]["scenario"] if comparisons else None,
            "timestamp": datetime.utcnow().isoformat(),
        }

    @staticmethod
    def _analyze_dependencies(app: ApplicationComponent) -> Dict[str, Any]:
        """
        Perform graph-based dependency analysis using the canonical cross_layer_impact walk.

        Repointed to IntelligenceQueryService.cross_layer_impact — the one
        engine for impact traversal, replacing the previous RelationshipService path.
        """
        if not app.archimate_element_id:
            return {
                "has_archimate_mapping": False,
                "blast_radius": 0,
                "direct_impacts": [],
                "indirect_impacts": [],
                "affected_capabilities": [],
                "affected_goals": [],
                "message": "No architectural mapping found for this application.",
            }

        from app.modules.intelligence.services.query_service import IntelligenceQueryService

        result = IntelligenceQueryService.cross_layer_impact(
            app.archimate_element_id,
            include_derived=False,
            max_depth=3,
            direction="downstream",
            with_owner=True,
        )
        rows = result.get("rows") or []
        elements = result.get("elements") or {}

        # Batch-resolve BusinessCapability level and category for enrichment.
        capability_element_ids = [
            row["element_id"] for row in rows
            if elements.get(str(row["element_id"]), {}).get("type") == "Capability"
        ]
        capability_lookup: Dict[int, Dict[str, Any]] = {}
        if capability_element_ids:
            from app.models.business_capabilities import BusinessCapability

            caps = BusinessCapability.query.filter(
                BusinessCapability.archimate_element_id.in_(capability_element_ids)
            ).all()
            capability_lookup = {c.archimate_element_id: {"level": c.level, "category": c.category} for c in caps}

        direct_impacts = []
        indirect_by_depth: Dict[int, list] = {}
        affected_capabilities = []
        affected_goals = []
        layer_counts: Dict[str, int] = {}

        for row in rows:
            el = elements.get(str(row["element_id"]), {})
            item = {
                "id": row["element_id"],
                "name": el.get("name"),
                "type": el.get("type"),
                "layer": el.get("layer"),
                "depth": row["relation"]["depth"],
                "owner": row.get("owner"),
                "health": row.get("health"),
            }
            depth = row["relation"]["depth"]
            if depth == 1:
                direct_impacts.append(item)
            else:
                indirect_by_depth.setdefault(depth, []).append(item)

            el_type = el.get("type", "")
            if el_type == "Capability":
                cap_info = capability_lookup.get(row["element_id"], {})
                affected_capabilities.append({
                    "id": row["element_id"],
                    "name": el.get("name"),
                    "level": cap_info.get("level"),
                    "criticality": cap_info.get("category") or "unknown",
                })
            elif el_type == "Goal":
                affected_goals.append(item)

            layer = el.get("layer") or "Unknown"
            layer_counts[layer] = layer_counts.get(layer, 0) + 1

        direct_count = len(direct_impacts)
        indirect_count = sum(len(v) for v in indirect_by_depth.values())

        return {
            "has_archimate_mapping": True,
            "blast_radius": direct_count + indirect_count,
            "direct_impacts": direct_impacts,
            "indirect_impacts": indirect_by_depth,
            "affected_capabilities": affected_capabilities,
            "affected_goals": affected_goals,
            "layer_breakdown": layer_counts,
        }

    @staticmethod
    def _calculate_risk_score(
        app: ApplicationComponent, graph_analysis: Dict, scenario: str
    ) -> Dict[str, Any]:
        """
        Calculate weighted risk score based on multiple factors.
        """
        scores = {
            "dependency_score": 0,
            "criticality_score": 0,
            "capability_score": 0,
            "scenario_multiplier": 1.0,
        }

        # Dependency-based score
        blast_radius = graph_analysis.get("blast_radius", 0)
        scores["dependency_score"] = min(30, blast_radius * 3)

        # Business criticality score
        criticality_map = {
            "mission_critical": 30,
            "business_critical": 25,
            "business_operational": 15,
            "administrative": 5,
        }
        scores["criticality_score"] = criticality_map.get(app.business_criticality, 10)

        # Affected capabilities score
        cap_count = len(graph_analysis.get("affected_capabilities", []))
        scores["capability_score"] = min(25, cap_count * 5)

        # Scenario multiplier
        scenario_multipliers = {
            "retirement": 1.5,  # Highest risk - complete removal
            "vendor_switch": 1.3,  # High risk - dependency changes
            "cloud_migration": 1.2,  # Medium-high risk - architecture changes
            "upgrade": 1.0,  # Standard risk
            "custom": 1.1,
        }
        scores["scenario_multiplier"] = scenario_multipliers.get(scenario, 1.0)

        # Calculate total
        base_score = (
            scores["dependency_score"] + scores["criticality_score"] + scores["capability_score"]
        )
        total_score = min(100, int(base_score * scores["scenario_multiplier"]))

        return {
            "total_score": total_score,
            "risk_level": AIImpactAnalysisService._score_to_risk_level(total_score),
            "breakdown": scores,
            "factors": {
                "blast_radius": blast_radius,
                "business_criticality": app.business_criticality,
                "affected_capabilities": cap_count,
                "scenario": scenario,
            },
        }

    @staticmethod
    def _score_to_risk_level(score: int) -> str:
        """Convert numeric score to risk level."""
        if score >= 80:
            return "critical"
        elif score >= 60:
            return "high"
        elif score >= 40:
            return "medium"
        elif score >= 20:
            return "low"
        else:
            return "minimal"

    @staticmethod
    def _generate_ai_insights(
        app: ApplicationComponent, graph_analysis: Dict, scenario: str, risk_assessment: Dict
    ) -> Dict[str, Any]:
        """
        Generate AI-powered insights including hidden dependencies,
        business impact translation, and mitigation strategies.
        """
        # Build context for LLM
        context = {
            "application_name": app.name,
            "description": app.description or "No description available",
            "lifecycle_status": app.lifecycle_status,
            "business_criticality": app.business_criticality,
            "scenario": scenario,
            "risk_score": risk_assessment["total_score"],
            "risk_level": risk_assessment["risk_level"],
            "blast_radius": graph_analysis.get("blast_radius", 0),
            "direct_impacts": [
                {"name": i.get("name"), "type": i.get("type"), "layer": i.get("layer")}
                for i in graph_analysis.get("direct_impacts", [])[:10]
            ],
            "affected_capabilities": [
                c["name"] for c in graph_analysis.get("affected_capabilities", [])[:5]
            ],
        }

        prompt = f"""You are an enterprise architect analyzing the impact of a proposed change.

APPLICATION CONTEXT:
- Name: {context['application_name']}
- Description: {context['description']}
- Lifecycle Status: {context['lifecycle_status']}
- Business Criticality: {context['business_criticality']}

CHANGE SCENARIO: {context['scenario']}

RISK ASSESSMENT:
- Risk Score: {context['risk_score']}/100 ({context['risk_level']})
- Blast Radius: {context['blast_radius']} components affected

DIRECT IMPACTS:
{json.dumps(context['direct_impacts'], indent=2)}

AFFECTED BUSINESS CAPABILITIES:
{', '.join(context['affected_capabilities']) if context['affected_capabilities'] else 'None identified'}

ANALYSIS TASKS:
1. Identify any HIDDEN DEPENDENCIES not captured by the graph (based on common patterns)
2. Translate technical impacts into BUSINESS CONSEQUENCES (revenue, operations, compliance)
3. Provide a RISK MITIGATION STRATEGY with prioritized actions
4. Give an overall RECOMMENDATION (proceed/defer/cancel) with justification

OUTPUT FORMAT (JSON only, no markdown):
{{
  "hidden_dependencies": [
    {{"type": "dependency_type", "description": "...", "likelihood": "high/medium/low"}}
  ],
  "business_impact": {{
    "revenue_impact": "description of revenue risk",
    "operational_impact": "description of operational risk",
    "compliance_impact": "description of compliance risk",
    "reputational_impact": "description of reputational risk"
  }},
  "mitigation_strategy": [
    {{"priority": 1, "action": "...", "effort": "low/medium/high", "timeline": "..."}}
  ],
  "recommendation": {{
    "decision": "proceed/proceed_with_caution/defer/cancel",
    "justification": "...",
    "prerequisites": ["..."]
  }}
}}"""

        try:
            response = LLMService.generate_from_prompt(
                prompt=prompt, use_cache=True, cache_ttl=1800  # 30 minutes cache
            )

            # Parse JSON response
            result = json.loads(response)
            result["available"] = True
            result["generated_at"] = datetime.utcnow().isoformat()
            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response: {e}")
            return {
                "available": False,
                "error": "Failed to parse AI response",
                "raw_response": response[:500] if "response" in locals() else None,
            }
        except Exception as e:
            logger.error(f"AI insight generation failed: {e}")
            raise

    @staticmethod
    def _generate_rule_based_recommendations(scenario: str, risk_assessment: Dict) -> List[Dict]:
        """
        Generate rule-based recommendations when AI is not available.
        """
        risk_level = risk_assessment["risk_level"]

        base_recommendations = {
            "retirement": [
                {
                    "priority": 1,
                    "action": "Identify all consuming applications",
                    "effort": "medium",
                },
                {"priority": 2, "action": "Document data migration requirements", "effort": "high"},
                {"priority": 3, "action": "Plan communication to stakeholders", "effort": "low"},
            ],
            "upgrade": [
                {
                    "priority": 1,
                    "action": "Test compatibility with dependent systems",
                    "effort": "medium",
                },
                {"priority": 2, "action": "Create rollback plan", "effort": "medium"},
                {"priority": 3, "action": "Schedule maintenance window", "effort": "low"},
            ],
            "cloud_migration": [
                {
                    "priority": 1,
                    "action": "Assess network latency requirements",
                    "effort": "medium",
                },
                {
                    "priority": 2,
                    "action": "Review security and compliance requirements",
                    "effort": "high",
                },
                {"priority": 3, "action": "Plan data migration strategy", "effort": "high"},
            ],
            "vendor_switch": [
                {"priority": 1, "action": "Map feature parity between vendors", "effort": "high"},
                {"priority": 2, "action": "Plan integration point modifications", "effort": "high"},
                {
                    "priority": 3,
                    "action": "Negotiate transition support with vendors",
                    "effort": "medium",
                },
            ],
        }

        recommendations = base_recommendations.get(
            scenario,
            [
                {"priority": 1, "action": "Document all dependencies", "effort": "medium"},
                {"priority": 2, "action": "Create impact assessment report", "effort": "medium"},
            ],
        )

        # Add risk-based recommendations
        if risk_level in ["critical", "high"]:
            recommendations.insert(
                0,
                {
                    "priority": 0,
                    "action": "Obtain executive approval before proceeding",
                    "effort": "low",
                },
            )

        return recommendations

    @staticmethod
    def _format_graph_visualization(root_name: str, graph_analysis: Dict) -> Dict[str, Any]:
        """
        Format analysis data for Mermaid.js visualization.
        """
        nodes = set()
        edges = []

        # Add root node
        nodes.add(f'root["{root_name}"]:::highlight')

        # Add direct impacts with layer-based styling
        layer_styles = {
            "Business": "business",
            "Application": "application",
            "Technology": "technology",
            "Motivation": "motivation",
        }

        for item in graph_analysis.get("direct_impacts", []):
            node_id = f"node_{item.get('id', hash(item.get('name', '')))}"
            layer = item.get("layer", "Unknown")
            style = layer_styles.get(layer, "default")
            nodes.add(f'{node_id}["{item.get("name", "Unknown")}"]:::{style}')
            edges.append(f"root --> {node_id}")

        # Add affected capabilities
        for cap in graph_analysis.get("affected_capabilities", []):
            cap_id = f"cap_{cap.get('id', hash(cap.get('name', '')))}"
            nodes.add(f'{cap_id}("{cap.get("name", "Unknown")}"):::capability')
            edges.append(f"root -.-> {cap_id}")

        return {
            "nodes": list(nodes),
            "edges": edges,
            "styles": """
                classDef highlight fill:#ef4444,stroke:#dc2626,color:white
                classDef business fill:#3b82f6,stroke:#2563eb,color:white
                classDef application fill:#10b981,stroke:#059669,color:white
                classDef technology fill:#8b5cf6,stroke:#7c3aed,color:white
                classDef motivation fill:#f59e0b,stroke:#d97706,color:white
                classDef capability fill:#ec4899,stroke:#db2777,color:white
                classDef default fill:#6b7280,stroke:#4b5563,color:white
            """,
        }

    @staticmethod
    def _generate_summary(
        app: ApplicationComponent, risk_assessment: Dict, ai_insights: Dict
    ) -> Dict[str, str]:
        """
        Generate human-readable summary of the analysis.
        """
        risk_level = risk_assessment["risk_level"]

        # Risk level descriptions
        risk_descriptions = {
            "critical": "This change poses critical risk to business operations.",
            "high": "This change carries significant risk and requires careful planning.",
            "medium": "This change has moderate risk with manageable impacts.",
            "low": "This change has low risk with limited impact on operations.",
            "minimal": "This change has minimal risk and can proceed with standard precautions.",
        }

        summary = {
            "headline": f"{risk_level.upper()} RISK: {app.name}",
            "risk_description": risk_descriptions.get(risk_level, "Risk level undetermined."),
            "key_metric": f"Risk Score: {risk_assessment['total_score']}/100",
        }

        # Add AI recommendation if available
        if ai_insights.get("available") and ai_insights.get("recommendation"):
            rec = ai_insights["recommendation"]
            summary["ai_recommendation"] = rec.get("decision", "N/A").upper()
            summary["ai_justification"] = rec.get("justification", "No justification provided.")

        return summary


def get_analysis_types() -> List[Dict[str, str]]:
    """
    Return available analysis types for the Impact Analysis Hub.
    """
    return [
        {
            "id": "application_impact",
            "name": "Application Impact Analysis",
            "description": "Analyze the impact of changing, retiring, or migrating a single application",
            "icon": "📱",
        },
        {
            "id": "portfolio_impact",
            "name": "Portfolio Impact Analysis",
            "description": "Analyze impacts across multiple applications simultaneously",
            "icon": "📊",
        },
        {
            "id": "scenario_comparison",
            "name": "Scenario Comparison",
            "description": "Compare different change scenarios for the same application",
            "icon": "⚖️",
        },
        {
            "id": "cobit_gap",
            "name": "COBIT Gap Analysis",
            "description": "Analyze application coverage for COBIT 2019 processes",
            "icon": "📋",
            "route": "architecture.cobit_gap_analysis",
        },
        {
            "id": "portfolio_gap",
            "name": "Portfolio Gap Analysis",
            "description": "Identify compliance risks, single points of failure, and unsupported capabilities",
            "icon": "🎯",
            "route": "application_mgmt.portfolio_gap_analysis",
        },
        {
            "id": "strategic_gap",
            "name": "Strategic Gap Analysis",
            "description": "Analyze maturity gaps and linked work packages",
            "icon": "📈",
            "route": "dashboard.gap_analysis",
        },
    ]
