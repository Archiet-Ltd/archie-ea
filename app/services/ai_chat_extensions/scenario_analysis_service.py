"""
What-If Scenario Analysis Service

Provides impact analysis and scenario simulation capabilities:
- Application retirement impact analysis
- Technology migration scenarios
- Capability gap impact assessment
- Cost/benefit projections
- Risk cascade analysis
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.utils.tenant_sql import org_scope
from app.services.application_kind import applications_query

logger = logging.getLogger(__name__)


class ScenarioAnalysisService:
    """Service for What-If scenario analysis and impact simulation."""

    SCENARIO_TYPES = {
        "application_retirement": {
            "name": "Application Retirement",
            "description": "Analyze impact of retiring an application",
            "required_params": ["application_id"],
            "impact_areas": ["capabilities", "integrations", "users", "costs", "risks"],
        },
        "technology_migration": {
            "name": "Technology Migration",
            "description": "Assess migration from one technology to another",
            "required_params": ["source_technology", "target_technology"],
            "impact_areas": ["applications", "skills", "costs", "timeline", "risks"],
        },
        "vendor_change": {
            "name": "Vendor Change",
            "description": "Evaluate impact of changing vendors",
            "required_params": ["current_vendor_id", "new_vendor_id"],
            "impact_areas": ["products", "contracts", "integrations", "costs", "risks"],
        },
        "capability_investment": {
            "name": "Capability Investment",
            "description": "Project outcomes of investing in a capability",
            "required_params": ["capability_id", "investment_level"],
            "impact_areas": ["maturity", "applications", "processes", "costs", "benefits"],
        },
        "merger_acquisition": {
            "name": "Merger/Acquisition",
            "description": "Analyze portfolio overlap and integration scenarios",
            "required_params": ["portfolio_a", "portfolio_b"],
            "impact_areas": ["duplicates", "gaps", "synergies", "costs", "timeline"],
        },
        "cloud_migration": {
            "name": "Cloud Migration",
            "description": "Evaluate cloud migration strategy and impact",
            "required_params": ["application_ids", "target_cloud"],
            "impact_areas": ["costs", "performance", "security", "compliance", "timeline"],
        },
    }

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def get_available_scenarios(self) -> Dict[str, Any]:
        """Return available scenario types."""
        return {"scenarios": self.SCENARIO_TYPES, "count": len(self.SCENARIO_TYPES)}

    def analyze_scenario(
        self, scenario_type: str, parameters: Dict[str, Any], options: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Run a what-if scenario analysis.

        Args:
            scenario_type: Type of scenario to analyze
            parameters: Scenario-specific parameters
            options: Additional analysis options

        Returns:
            Comprehensive impact analysis results
        """
        if scenario_type not in self.SCENARIO_TYPES:
            return {
                "success": False,
                "error": f"Unknown scenario type: {scenario_type}",
                "available_types": list(self.SCENARIO_TYPES.keys()),
            }

        options = options or {}

        try:
            if scenario_type == "application_retirement":
                return self._analyze_application_retirement(parameters, options)
            elif scenario_type == "technology_migration":
                return self._analyze_technology_migration(parameters, options)
            elif scenario_type == "vendor_change":
                return self._analyze_vendor_change(parameters, options)
            elif scenario_type == "capability_investment":
                return self._analyze_capability_investment(parameters, options)
            elif scenario_type == "merger_acquisition":
                return self._analyze_merger_acquisition(parameters, options)
            elif scenario_type == "cloud_migration":
                return self._analyze_cloud_migration(parameters, options)
            else:
                return {"success": False, "error": "Scenario type not implemented"}

        except Exception as e:
            self.logger.error(f"Error analyzing scenario: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _analyze_application_retirement(
        self, params: Dict[str, Any], options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze the impact of retiring an application."""
        app_id = params.get("application_id")
        retirement_date = params.get("retirement_date", datetime.now() + timedelta(days=180))

        # Load application details
        app_details = self._load_application(app_id)
        if not app_details:
            return {"success": False, "error": f"Application {app_id} not found"}

        # Analyze impacts
        capability_impact = self._assess_capability_impact(app_id)
        integration_impact = self._assess_integration_impact(app_id)
        user_impact = self._assess_user_impact(app_id)
        cost_impact = self._calculate_retirement_costs(app_details)
        risk_assessment = self._assess_retirement_risks(
            app_details, capability_impact, integration_impact
        )

        # Calculate overall impact score
        impact_score = self._calculate_impact_score(
            [
                capability_impact["score"],
                integration_impact["score"],
                user_impact["score"],
                risk_assessment["score"],
            ]
        )

        # Generate recommendations
        recommendations = self._generate_retirement_recommendations(
            app_details, capability_impact, integration_impact, user_impact, risk_assessment
        )

        # Generate migration path suggestions
        migration_options = self._suggest_migration_options(app_details, capability_impact)

        return {
            "success": True,
            "scenario_type": "application_retirement",
            "application": app_details,
            "retirement_date": str(retirement_date),
            "impact_summary": {
                "overall_score": impact_score,
                "risk_level": self._score_to_risk_level(impact_score),
                "affected_capabilities": capability_impact["count"],
                "affected_integrations": integration_impact["count"],
                "affected_users": user_impact["count"],
                "estimated_savings": cost_impact["annual_savings"],
                "migration_cost": cost_impact["migration_cost"],
            },
            "capability_impact": capability_impact,
            "integration_impact": integration_impact,
            "user_impact": user_impact,
            "cost_impact": cost_impact,
            "risk_assessment": risk_assessment,
            "recommendations": recommendations,
            "migration_options": migration_options,
            "timeline_suggestion": self._suggest_retirement_timeline(impact_score),
        }

    def _analyze_technology_migration(
        self, params: Dict[str, Any], options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze technology migration impact."""
        source_tech = params.get("source_technology")
        target_tech = params.get("target_technology")

        # Find affected applications
        affected_apps = self._find_apps_by_technology(source_tech)

        # Assess migration complexity
        complexity_assessment = self._assess_migration_complexity(
            affected_apps, source_tech, target_tech
        )

        # Skills analysis
        skills_impact = self._assess_skills_gap(source_tech, target_tech)

        # Cost projection
        cost_projection = self._project_migration_costs(affected_apps, complexity_assessment)

        # Timeline estimation
        timeline = self._estimate_migration_timeline(affected_apps, complexity_assessment)

        # Risk assessment
        risks = self._assess_migration_risks(affected_apps, source_tech, target_tech)

        return {
            "success": True,
            "scenario_type": "technology_migration",
            "source_technology": source_tech,
            "target_technology": target_tech,
            "affected_applications": {
                "count": len(affected_apps),
                "list": affected_apps[:10],  # Limit to 10 for response size
                "by_criticality": self._group_by_criticality(affected_apps),
            },
            "complexity_assessment": complexity_assessment,
            "skills_impact": skills_impact,
            "cost_projection": cost_projection,
            "timeline": timeline,
            "risks": risks,
            "recommendations": self._generate_migration_recommendations(
                affected_apps, complexity_assessment, skills_impact, risks
            ),
            "phased_approach": self._suggest_phased_migration(affected_apps, complexity_assessment),
        }

    def _analyze_vendor_change(
        self, params: Dict[str, Any], options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze vendor change impact."""
        current_vendor_id = params.get("current_vendor_id")
        new_vendor_id = params.get("new_vendor_id")

        # Load vendor details
        current_vendor = self._load_vendor(current_vendor_id)
        new_vendor = self._load_vendor(new_vendor_id) if new_vendor_id else None

        # Find affected products and applications
        affected_products = self._find_vendor_products(current_vendor_id)
        affected_apps = self._find_apps_using_vendor(current_vendor_id)

        # Contract analysis
        contract_impact = self._analyze_contract_impact(current_vendor_id)

        # Cost comparison
        cost_comparison = self._compare_vendor_costs(current_vendor, new_vendor)

        # Integration impact
        integration_impact = self._assess_vendor_integration_impact(
            current_vendor_id, new_vendor_id
        )

        # Risk assessment
        risks = self._assess_vendor_change_risks(current_vendor, new_vendor, affected_apps)

        return {
            "success": True,
            "scenario_type": "vendor_change",
            "current_vendor": current_vendor,
            "new_vendor": new_vendor,
            "affected_products": affected_products,
            "affected_applications": {"count": len(affected_apps), "list": affected_apps[:10]},
            "contract_impact": contract_impact,
            "cost_comparison": cost_comparison,
            "integration_impact": integration_impact,
            "risks": risks,
            "recommendations": self._generate_vendor_change_recommendations(
                current_vendor, new_vendor, affected_apps, risks
            ),
            "transition_plan": self._suggest_vendor_transition_plan(
                affected_products, affected_apps
            ),
        }

    def _analyze_capability_investment(
        self, params: Dict[str, Any], options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze capability investment outcomes."""
        capability_id = params.get("capability_id")
        investment_level = params.get("investment_level", "medium")  # low, medium, high

        # Load capability
        capability = self._load_capability(capability_id)
        if not capability:
            return {"success": False, "error": f"Capability {capability_id} not found"}

        # Current state assessment
        current_state = self._assess_capability_current_state(capability)

        # Project future state based on investment
        future_state = self._project_capability_future_state(capability, investment_level)

        # Calculate investment requirements
        investment_requirements = self._calculate_investment_requirements(
            capability, investment_level
        )

        # Project benefits
        projected_benefits = self._project_investment_benefits(capability, investment_level)

        # ROI calculation
        roi_analysis = self._calculate_capability_roi(investment_requirements, projected_benefits)

        return {
            "success": True,
            "scenario_type": "capability_investment",
            "capability": capability,
            "investment_level": investment_level,
            "current_state": current_state,
            "future_state": future_state,
            "investment_requirements": investment_requirements,
            "projected_benefits": projected_benefits,
            "roi_analysis": roi_analysis,
            "recommendations": self._generate_investment_recommendations(
                capability, investment_level, roi_analysis
            ),
            "implementation_roadmap": self._create_investment_roadmap(capability, investment_level),
        }

    def _analyze_merger_acquisition(
        self, params: Dict[str, Any], options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze M&A portfolio integration scenarios."""
        portfolio_a = params.get("portfolio_a", [])
        portfolio_b = params.get("portfolio_b", [])

        # Identify overlaps
        overlaps = self._identify_portfolio_overlaps(portfolio_a, portfolio_b)

        # Identify gaps
        gaps = self._identify_portfolio_gaps(portfolio_a, portfolio_b)

        # Calculate synergies
        synergies = self._calculate_synergies(overlaps)

        # Estimate integration costs
        integration_costs = self._estimate_integration_costs(portfolio_a, portfolio_b, overlaps)

        # Create integration timeline
        timeline = self._create_integration_timeline(overlaps, gaps)

        return {
            "success": True,
            "scenario_type": "merger_acquisition",
            "portfolio_analysis": {
                "portfolio_a_count": len(portfolio_a),
                "portfolio_b_count": len(portfolio_b),
                "overlap_count": len(overlaps),
                "gap_count": len(gaps),
            },
            "overlaps": overlaps,
            "gaps": gaps,
            "synergies": synergies,
            "integration_costs": integration_costs,
            "timeline": timeline,
            "recommendations": self._generate_ma_recommendations(overlaps, gaps, synergies),
        }

    def _analyze_cloud_migration(
        self, params: Dict[str, Any], options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze cloud migration strategy and impact."""
        app_ids = params.get("application_ids", [])
        target_cloud = params.get("target_cloud", "AWS")

        # Load applications
        applications = (
            self._load_applications(app_ids) if app_ids else self._load_all_applications(limit=20)
        )

        # Assess cloud readiness for each application
        readiness_assessment = self._assess_cloud_readiness(applications)

        # Migration strategy recommendation (6Rs)
        migration_strategies = self._recommend_migration_strategies(applications, target_cloud)

        # Cost projection (on-prem vs cloud)
        cost_projection = self._project_cloud_costs(applications, target_cloud)

        # Security and compliance assessment
        compliance_assessment = self._assess_cloud_compliance(applications, target_cloud)

        # Timeline estimation
        timeline = self._estimate_cloud_migration_timeline(applications, migration_strategies)

        return {
            "success": True,
            "scenario_type": "cloud_migration",
            "target_cloud": target_cloud,
            "applications_analyzed": len(applications),
            "readiness_assessment": readiness_assessment,
            "migration_strategies": migration_strategies,
            "cost_projection": cost_projection,
            "compliance_assessment": compliance_assessment,
            "timeline": timeline,
            "recommendations": self._generate_cloud_migration_recommendations(
                readiness_assessment, migration_strategies, cost_projection
            ),
            "migration_waves": self._plan_migration_waves(applications, readiness_assessment),
        }

    # Helper methods for impact assessment

    def _load_application(self, app_id: int) -> Optional[Dict]:
        """Load application details."""
        try:
            from app.models.application_portfolio import ApplicationComponent

            app = ApplicationComponent.query.get(app_id)
            if app:
                return {
                    "id": app.id,
                    "name": app.name,
                    "description": getattr(app, "description", ""),
                    "status": getattr(app, "lifecycle_status", "Active"),
                    "criticality": getattr(app, "criticality", "Medium"),
                    "technology_stack": getattr(app, "technology_stack", ""),
                    "annual_cost": getattr(app, "annual_cost", 0),
                    "user_count": getattr(app, "user_count", 0),
                    "business_owner": getattr(app, "business_owner", ""),
                    "maintenance_cost": getattr(app, "maintenance_cost", 0),  # model-safety-ok
                    "infrastructure_cost": getattr(app, "infrastructure_cost", 0),  # model-safety-ok
                    "support_cost": getattr(app, "support_cost", 0),  # model-safety-ok
                    "number_of_integrations": getattr(app, "number_of_integrations", 0),  # model-safety-ok
                    "interfaces_count": getattr(app, "interfaces_count", 0),  # model-safety-ok
                    "dependencies_count": getattr(app, "dependencies_count", 0),  # model-safety-ok
                }
            return None
        except Exception as e:
            self.logger.warning(f"Could not load application: {e}")
            return None

    def _load_applications(self, app_ids: List[int]) -> List[Dict]:
        """Load multiple applications."""
        return [self._load_application(aid) for aid in app_ids if self._load_application(aid)]

    def _load_all_applications(self, limit: int = 20) -> List[Dict]:
        """Load all applications.

        Deliberately unguarded. This is the whole input set for a cloud
        migration scenario when the caller names no applications: returning []
        produced a full analysis — readiness assessment, 6R strategies, cost
        projection, risk score — over zero applications, and reported it as the
        migration plan for the portfolio. analyze_scenario logs the exception
        with a traceback and answers {"success": False, "error": ...}.
        """

        apps = applications_query().limit(limit).all()
        return [
            {
                "id": app.id,
                "name": app.name,
                "status": getattr(app, "lifecycle_status", "Active"),  # model-safety-ok
                "criticality": getattr(app, "criticality", "Medium"),  # model-safety-ok
                "technology_stack": getattr(app, "technology_stack", ""),  # model-safety-ok
            }
            for app in apps
        ]

    def _load_capability(self, cap_id: int) -> Optional[Dict]:
        """Load capability details."""
        try:
            from app.models.unified_capability import UnifiedCapability

            cap = UnifiedCapability.query.get(cap_id)
            if cap:
                return {
                    "id": cap.id,
                    "name": cap.name,
                    "level": getattr(cap, "level", "L1"),
                    "maturity_level": getattr(cap, "maturity_level", 2),
                    "description": getattr(cap, "description", ""),
                }
            return None
        except Exception:
            return None

    def _load_vendor(self, vendor_id: int) -> Optional[Dict]:
        """Load vendor details."""
        try:
            from app.models.vendor.vendor_organization import VendorOrganization

            vendor = VendorOrganization.query.get(vendor_id)
            if vendor:
                return {
                    "id": vendor.id,
                    "name": vendor.name,
                    "vendor_type": getattr(vendor, "vendor_type", ""),  # model-safety-ok
                    "strategic_tier": getattr(vendor, "strategic_tier", ""),  # model-safety-ok
                    "annual_spend": getattr(vendor, "annual_spend", 0),  # model-safety-ok
                }
            return None
        except Exception:
            return None

    def _assess_capability_impact(self, app_id: int) -> Dict[str, Any]:
        """Assess impact on capabilities if application is retired."""
        try:
            from app import db
            from sqlalchemy import text

            # app_id is an LLM tool argument — it reaches here from chat text, so
            # the FK join proves only that the mapping exists, not whose it is.
            #
            # Scope on business_capability, NOT on application_capability_mapping:
            # acm has an organization_id column but nothing populates it (0 of
            # 2686 rows non-NULL — it is not a TenantMixin model), so a predicate
            # there would match no rows and silently report "no capabilities
            # affected" for every retirement.
            _org_clause, _org_params = org_scope(prefix="bc.")

            # Find capabilities this app supports
            cap_rows = db.session.execute(text(
                "SELECT DISTINCT acm.business_capability_id, bc.name "
                "FROM application_capability_mapping acm "
                "JOIN business_capability bc ON bc.id = acm.business_capability_id "
                "WHERE acm.application_component_id = :app_id" + _org_clause
            ), {"app_id": app_id, **_org_params}).fetchall()

            affected = []
            critical_gaps = 0
            for cap_id, cap_name in cap_rows:
                # tenancy-ok: cap_id comes from the org-scoped query above, so
                # the capability is this tenant's; acm rows are reachable only
                # through it. acm itself cannot be scoped (organization_id is
                # NULL for every row).
                other_apps = db.session.execute(text(
                    "SELECT COUNT(DISTINCT application_component_id) "
                    "FROM application_capability_mapping "
                    "WHERE business_capability_id = :cap_id "
                    "AND application_component_id != :app_id"
                ), {"cap_id": cap_id, "app_id": app_id}).scalar() or 0

                is_at_risk = other_apps == 0
                if is_at_risk:
                    critical_gaps += 1
                affected.append({
                    "id": cap_id, "name": cap_name,
                    "other_supporting_apps": other_apps,
                    "at_risk": is_at_risk,
                })

            count = len(affected)
            score = min(count * 15 + critical_gaps * 25, 100)
            return {
                "score": score, "count": count,
                "affected_capabilities": affected[:20],
                "critical_gaps": critical_gaps,
                "recommendation": f"{critical_gaps} capabilities would lose their only supporting application" if critical_gaps > 0
                    else f"{count} capabilities affected, all have alternative support",
            }
        except Exception as e:
            logger.warning(f"Capability impact assessment failed: {e}")
            return {"score": 0, "count": 0, "affected_capabilities": [], "critical_gaps": 0,
                    "recommendation": "Assessment unavailable"}

    def _assess_integration_impact(self, app_id: int) -> Dict[str, Any]:
        """Assess integration impact."""
        try:
            from app import db
            from sqlalchemy import text

            _org_clause, _org_params = org_scope()

            # Get the app's archimate element ID
            elem_id = db.session.execute(text(
                "SELECT archimate_element_id FROM application_components "
                "WHERE id = :app_id AND deleted_at IS NULL" + _org_clause
            ), {"app_id": app_id, **_org_params}).scalar()

            if not elem_id:
                # Fallback: use app fields
                row = db.session.execute(text(
                    "SELECT number_of_integrations, interfaces_count, dependencies_count "
                    "FROM application_components WHERE id = :app_id AND deleted_at IS NULL" + _org_clause
                ), {"app_id": app_id, **_org_params}).fetchone()
                if row:
                    total = (row[0] or 0) + (row[1] or 0) + (row[2] or 0)
                    return {"score": min(total * 10, 100), "count": total,
                            "upstream_systems": row[2] or 0, "downstream_systems": row[1] or 0,
                            "interfaces": [], "recommendation": f"{total} integrations from app metadata"}
                return {"score": 0, "count": 0, "upstream_systems": 0, "downstream_systems": 0,
                        "interfaces": [], "recommendation": "No integration data"}

            # Count ArchiMate relationships. elem_id now comes from an org-scoped
            # read, but archimate_relationships carries organization_id on every
            # row, so scope it directly rather than inferring it.
            upstream = db.session.execute(text(
                "SELECT COUNT(*) FROM archimate_relationships "
                "WHERE target_id = :eid" + _org_clause
            ), {"eid": elem_id, **_org_params}).scalar() or 0
            downstream = db.session.execute(text(
                "SELECT COUNT(*) FROM archimate_relationships "
                "WHERE source_id = :eid" + _org_clause
            ), {"eid": elem_id, **_org_params}).scalar() or 0

            total = upstream + downstream
            score = min(total * 8, 100)
            return {"score": score, "count": total,
                    "upstream_systems": upstream, "downstream_systems": downstream,
                    "interfaces": [],
                    "recommendation": f"{upstream} upstream and {downstream} downstream dependencies via ArchiMate"}
        except Exception as e:
            logger.warning(f"Integration impact assessment failed: {e}")
            return {"score": 0, "count": 0, "upstream_systems": 0, "downstream_systems": 0,
                    "interfaces": [], "recommendation": "Assessment unavailable"}

    def _assess_user_impact(self, app_id: int) -> Dict[str, Any]:
        """Assess user impact."""
        try:
            from app import db
            from sqlalchemy import text

            _org_clause, _org_params = org_scope()
            row = db.session.execute(text(
                "SELECT user_count FROM application_components "
                "WHERE id = :app_id AND deleted_at IS NULL" + _org_clause
            ), {"app_id": app_id, **_org_params}).fetchone()
            if not row:
                # The org-scoped read above found no such application in this
                # tenant. Do not go on to sum its capability mappings.
                return {"score": 0, "count": 0, "departments": [], "training_required": None,
                        "change_management_effort": "Not assessed",
                        "recommendation": "No user count data available"}
            user_count = row[0] or 0

            # Also sum user_count from capability mappings if available.
            # tenancy-ok: reached only after the org-scoped read above confirmed
            # app_id belongs to this tenant, and application_capability_mapping
            # cannot be scoped directly — its organization_id is NULL on every row.
            cap_users = db.session.execute(text(
                "SELECT SUM(user_count) FROM application_capability_mapping "
                "WHERE application_component_id = :app_id AND user_count IS NOT NULL"
            ), {"app_id": app_id}).scalar() or 0

            total_users = max(user_count, cap_users)
            score = min(total_users // 100, 100) if total_users > 0 else 0

            return {"score": score, "count": total_users, "departments": [],
                    "training_required": "Yes" if total_users > 500 else "Minimal",
                    "change_management_effort": "High" if total_users > 1000 else ("Medium" if total_users > 100 else "Low"),
                    "recommendation": f"{total_users} users affected" if total_users > 0 else "No user count data available"}
        except Exception as e:
            logger.warning(f"User impact assessment failed: {e}")
            return {"score": 0, "count": 0, "departments": [], "training_required": None,
                    "change_management_effort": "Not assessed", "recommendation": "Assessment unavailable"}

    def _calculate_retirement_costs(self, app_details: Dict) -> Dict[str, Any]:
        """Calculate costs associated with retirement."""
        try:
            from app import db
            from sqlalchemy import text

            app_id = app_details.get("id")
            _org_clause, _org_params = org_scope()
            row = db.session.execute(text(
                "SELECT annual_cost, maintenance_cost, infrastructure_cost, support_cost "
                "FROM application_components WHERE id = :app_id" + _org_clause
            ), {"app_id": app_id, **_org_params}).fetchone()

            if row:
                annual = float(row[0] or 0)
                maintenance = float(row[1] or 0)
                infra = float(row[2] or 0)
                support = float(row[3] or 0)
                current_annual = annual + maintenance + infra + support
            else:
                current_annual = float(app_details.get("annual_cost", 0))

            migration_cost = current_annual * 0.5  # estimated 6 months of costs for migration
            annual_savings = current_annual
            payback = int(migration_cost / (annual_savings / 12)) if annual_savings > 0 else 0

            return {
                "current_annual_cost": current_annual,
                "migration_cost": migration_cost,
                "annual_savings": annual_savings,
                "payback_period_months": payback,
                "five_year_savings": (annual_savings * 5) - migration_cost,
            }
        except Exception as e:
            logger.warning(f"Retirement cost calculation failed: {e}")
            return {"current_annual_cost": None, "migration_cost": None, "annual_savings": None,
                    "payback_period_months": None, "five_year_savings": None}  # honest: costs not computed on error

    def _assess_retirement_risks(
        self, app_details: Dict, capability_impact: Dict, integration_impact: Dict
    ) -> Dict[str, Any]:
        """Assess risks of retirement."""
        risks = []
        score = 0

        criticality = (app_details.get("criticality", "") or "").lower()
        if criticality in ("mission_critical", "mission critical", "critical"):
            risks.append({"risk": "Mission-critical application", "severity": "Critical", "mitigation": "Requires full DR plan and phased transition"})
            score += 40
        elif criticality in ("important", "high"):
            risks.append({"risk": "High-importance application", "severity": "High", "mitigation": "Requires stakeholder sign-off and transition plan"})
            score += 20

        critical_gaps = capability_impact.get("critical_gaps", 0)
        if critical_gaps > 0:
            risks.append({"risk": f"{critical_gaps} capabilities would lose their only supporting application",
                           "severity": "Critical", "mitigation": "Identify replacement applications before retirement"})
            score += min(critical_gaps * 15, 40)

        integration_count = integration_impact.get("count", 0)
        if integration_count > 5:
            risks.append({"risk": f"{integration_count} integrations to decommission",
                           "severity": "High", "mitigation": "Map and reroute all integration interfaces"})
            score += min(integration_count * 3, 30)
        elif integration_count > 0:
            risks.append({"risk": f"{integration_count} integrations to decommission",
                           "severity": "Medium", "mitigation": "Update integration partners"})
            score += integration_count * 2

        score = min(score, 100)
        overall = "Critical" if score >= 70 else ("High" if score >= 40 else ("Medium" if score >= 20 else "Low"))
        return {"score": score, "risks": risks, "overall_risk": overall}

    def _generate_retirement_recommendations(
        self,
        app_details: Dict,
        capability_impact: Dict,
        integration_impact: Dict,
        user_impact: Dict,
        risk_assessment: Dict,
    ) -> List[Dict]:
        """Generate recommendations for application retirement."""
        recs = []
        if capability_impact.get("critical_gaps", 0) > 0:
            recs.append({"priority": "Critical", "action": "Identify replacement applications for at-risk capabilities before proceeding",
                          "effort": "High", "timeline": "Before retirement"})
        if integration_impact.get("count", 0) > 3:
            recs.append({"priority": "High", "action": f"Map and plan migration for {integration_impact['count']} integration interfaces",
                          "effort": "Medium", "timeline": "Phase 1"})
        if user_impact.get("count", 0) > 500:
            recs.append({"priority": "High", "action": f"Develop change management plan for {user_impact['count']} affected users",
                          "effort": "Medium", "timeline": "Phase 1"})
        if app_details.get("annual_cost", 0) > 0:
            recs.append({"priority": "Medium", "action": "Create detailed cost-benefit analysis with 5-year savings projection",
                          "effort": "Low", "timeline": "Immediately"})
        if not recs:
            recs.append({"priority": "Low", "action": "Low-risk retirement — proceed with standard decommission process",
                          "effort": "Low", "timeline": "3-6 months"})
        return recs

    def _suggest_migration_options(self, app_details: Dict, capability_impact: Dict) -> List[Dict]:
        """Suggest migration options."""
        return []

    def _suggest_retirement_timeline(self, impact_score: float) -> Dict[str, Any]:
        """Suggest retirement timeline based on impact."""
        if impact_score >= 70:
            return {
                "recommended_duration": "9 - 12 months",
                "phases": 4,
                "rationale": "High impact requires extended timeline",
            }
        elif impact_score >= 40:
            return {
                "recommended_duration": "6 - 9 months",
                "phases": 3,
                "rationale": "Medium impact allows standard timeline",
            }
        else:
            return {
                "recommended_duration": "3 - 6 months",
                "phases": 2,
                "rationale": "Low impact enables accelerated timeline",
            }

    def _calculate_impact_score(self, scores: List[float]) -> float:
        """Calculate weighted impact score."""
        weights = [0.3, 0.25, 0.2, 0.25]  # capability, integration, user, risk
        weighted_sum = sum(s * w for s, w in zip(scores, weights))
        return round(weighted_sum, 1)

    def _score_to_risk_level(self, score: float) -> str:
        """Convert score to risk level."""
        if score >= 70:
            return "High"
        elif score >= 40:
            return "Medium"
        else:
            return "Low"

    # Additional helper methods (stubs for other scenario types)

    def _find_apps_by_technology(self, tech: str) -> List[Dict]:
        try:
            from app import db
            from sqlalchemy import text
            # No id key at all — a bare LIKE over the whole table, so unfiltered
            # this named other organisations' applications as the migration scope.
            # The OR chain must be parenthesised or the org predicate binds only
            # to the last branch.
            _org_clause, _org_params = org_scope()
            rows = db.session.execute(text(
                "SELECT id, name, lifecycle_status, criticality, technology_stack "
                "FROM application_components "
                "WHERE (LOWER(technology_stack) LIKE :tech "
                "OR LOWER(programming_languages) LIKE :tech "
                "OR LOWER(database_platforms) LIKE :tech)" + _org_clause
            ), {"tech": f"%{tech.lower()}%", **_org_params}).fetchall()
            return [{"id": r[0], "name": r[1], "status": r[2] or "Active",
                     "criticality": r[3] or "Medium", "technology_stack": r[4] or ""}
                    for r in rows]
        except Exception as e:
            logger.warning(f"Technology search failed: {e}")
            return []

    def _assess_migration_complexity(self, apps: List, source: str, target: str) -> Dict:
        return {"overall": "Not assessed", "score": 0}

    def _assess_skills_gap(self, source: str, target: str) -> Dict:
        return {"gap_level": "Not assessed", "training_needed": None}

    def _project_migration_costs(self, apps: List, complexity: Dict) -> Dict:
        return {"total": 0, "per_app_average": 0}

    def _estimate_migration_timeline(self, apps: List, complexity: Dict) -> Dict:
        return {"total_months": 0, "phases": 0}

    def _assess_migration_risks(self, apps: List, source: str, target: str) -> Dict:
        return {"overall": "Not assessed", "risks": []}

    def _group_by_criticality(self, apps: List) -> Dict:
        return {"High": 0, "Medium": 0, "Low": 0}

    def _generate_migration_recommendations(self, apps, complexity, skills, risks) -> List:
        return []

    def _suggest_phased_migration(self, apps: List, complexity: Dict) -> List:
        return []

    def _find_vendor_products(self, vendor_id: int) -> List:
        try:
            from app import db
            from sqlalchemy import text
            rows = db.session.execute(text(  # tenant-filtered: scoped via vendor_products (tenant-scoped table)
                # vendor_products's FK to the vendor is vendor_organization_id;
                # there is no vendor_id column on that table.
                "SELECT id, name, product_type, version FROM vendor_products "
                "WHERE vendor_organization_id = :vid"
            ), {"vid": vendor_id}).fetchall()
            return [{"id": r[0], "name": r[1], "type": r[2] or "", "version": r[3] or ""} for r in rows]
        except Exception as e:
            logger.warning(f"Vendor products query failed: {e}")
            return []

    def _find_apps_using_vendor(self, vendor_id: int) -> List:
        try:
            from app import db
            from sqlalchemy import text
            # vendor_products has no organization_id column, so the subquery
            # cannot be scoped; application_components does, so scope the outer
            # read — otherwise a vendor id names every tenant's apps.
            _org_clause, _org_params = org_scope(prefix="ac.")
            rows = db.session.execute(text(
                "SELECT ac.id, ac.name, ac.lifecycle_status, ac.criticality "
                "FROM application_components ac "
                "WHERE ac.vendor_product_id IN "
                "(SELECT id FROM vendor_products WHERE vendor_id = :vid)" + _org_clause
            ), {"vid": vendor_id, **_org_params}).fetchall()
            return [{"id": r[0], "name": r[1], "status": r[2] or "Active", "criticality": r[3] or "Medium"}
                    for r in rows]
        except Exception as e:
            logger.warning(f"Vendor apps query failed: {e}")
            return []

    def _analyze_contract_impact(self, vendor_id: int) -> Dict:
        return {}

    def _compare_vendor_costs(self, current: Dict, new: Dict) -> Dict:
        return {}

    def _assess_vendor_integration_impact(self, current_id: int, new_id: int) -> Dict:
        return {}

    def _assess_vendor_change_risks(self, current: Dict, new: Dict, apps: List) -> Dict:
        return {}

    def _generate_vendor_change_recommendations(self, current, new, apps, risks) -> List:
        return []

    def _suggest_vendor_transition_plan(self, products: List, apps: List) -> Dict:
        return {}

    def _assess_capability_current_state(self, capability: Dict) -> Dict:
        return {"maturity": capability.get("maturity_level", 0)}

    def _project_capability_future_state(self, capability: Dict, level: str) -> Dict:
        return {"maturity": 0}

    def _calculate_investment_requirements(self, capability: Dict, level: str) -> Dict:
        return {"budget": 0}

    def _project_investment_benefits(self, capability: Dict, level: str) -> Dict:
        return {"efficiency_gain": "0%", "cost_reduction": 0}

    def _calculate_capability_roi(self, investment: Dict, benefits: Dict) -> Dict:
        return {"roi_percentage": 0, "payback_months": 0}

    def _generate_investment_recommendations(self, cap, level, roi) -> List:
        return []

    def _create_investment_roadmap(self, capability: Dict, level: str) -> Dict:
        return {}

    def _identify_portfolio_overlaps(self, a: List, b: List) -> List:
        return []

    def _identify_portfolio_gaps(self, a: List, b: List) -> List:
        return []

    def _calculate_synergies(self, overlaps: List) -> Dict:
        return {}

    def _estimate_integration_costs(self, a: List, b: List, overlaps: List) -> Dict:
        return {}

    def _create_integration_timeline(self, overlaps: List, gaps: List) -> Dict:
        return {}

    def _generate_ma_recommendations(self, overlaps, gaps, synergies) -> List:
        return []

    def _assess_cloud_readiness(self, apps: List) -> Dict:
        return {"ready": 0, "needs_work": 0, "not_suitable": 0}

    def _recommend_migration_strategies(self, apps: List, cloud: str) -> Dict:
        return {"rehost": 0, "replatform": 0, "refactor": 0, "retire": 0}

    def _project_cloud_costs(self, apps: List, cloud: str) -> Dict:
        return {"current_annual": 0, "projected_cloud": 0, "savings": 0}

    def _assess_cloud_compliance(self, apps: List, cloud: str) -> Dict:
        return {"compliant": 0, "needs_review": 0}

    def _estimate_cloud_migration_timeline(self, apps: List, strategies: Dict) -> Dict:
        return {"total_months": 0, "waves": 0}

    def _generate_cloud_migration_recommendations(self, readiness, strategies, costs) -> List:
        return []

    def _plan_migration_waves(self, apps: List, readiness: Dict) -> List:
        return []
