"""
Technology Roadmap Service

Plans and manages technology evolution and modernization:
- Technology gap analysis
- Modernization roadmap planning
- Technology lifecycle management
- Innovation opportunity assessment
- Technology investment planning
"""

from datetime import date, datetime
from typing import Dict, List

from app.services.decorators import transactional


class TechnologyRoadmapService:
    """
    Service for technology roadmap planning and management.

    Provides comprehensive technology planning:
    - Technology gap analysis and assessment
    - Modernization roadmap development
    - Technology lifecycle management
    - Innovation opportunity identification
    - Technology investment optimization
    """

    def __init__(self):
        pass

    @transactional
    def analyze_technology_portfolio(self, include_innovation: bool = True) -> Dict:
        """
        Comprehensive technology portfolio analysis.

        Args:
            include_innovation: Include innovation opportunity analysis

        Returns:
            Dict with technology roadmap analysis results
        """
        # Get all technology components
        technology_components = self._get_technology_components()

        # Analyze each technology component
        tech_analyses = []
        for component in technology_components:
            analysis = self._analyze_technology_component(component, include_innovation)
            tech_analyses.append(analysis)

        # Sort by modernization priority (highest first)
        tech_analyses.sort(key=lambda x: x["modernization_priority_score"], reverse=True)

        # Categorize by priority levels
        critical_tech = [t for t in tech_analyses if t["modernization_level"] == "CRITICAL"]
        high_tech = [t for t in tech_analyses if t["modernization_level"] == "HIGH"]
        medium_tech = [t for t in tech_analyses if t["modernization_level"] == "MEDIUM"]
        low_tech = [t for t in tech_analyses if t["modernization_level"] == "LOW"]

        # Calculate portfolio metrics
        portfolio_metrics = self._calculate_portfolio_metrics(tech_analyses)

        # Generate roadmap phases
        roadmap_phases = self._generate_roadmap_phases(tech_analyses)

        # Generate technology recommendations
        recommendations = self._generate_technology_recommendations(tech_analyses)

        return {
            "total_components": len(technology_components),
            "technology_analyses": tech_analyses,
            "critical_technology": critical_tech,
            "high_technology": high_tech,
            "medium_technology": medium_tech,
            "low_technology": low_tech,
            "portfolio_metrics": portfolio_metrics,
            "roadmap_phases": roadmap_phases,
            "recommendations": recommendations,
            "analysis_date": datetime.utcnow().isoformat(),
        }

    def _get_technology_components(self) -> List[Dict]:
        """Get all technology components from the database.

        Query failures propagate. `[]` is reported by
        ``analyze_technology_portfolio`` as ``total_components: 0`` with empty
        critical/high/medium/low buckets — a roadmap that says there is no
        technology debt, indistinguishable from one that measured none.
        """
        components = []

        # Get from application components
        from app.models.application_layer import ApplicationComponent

        applications = ApplicationComponent.query.filter(
            ApplicationComponent.deployment_status.in_(
                ["production", "Production", "Implementing"]
            )
        ).all()

        for app in applications:
            # Extract technology stack information
            tech_stack = app.technology_stack or []
            for tech in tech_stack:
                components.append(
                    {
                        "id": f"app_{app.id}_{tech}",
                        "name": tech,
                        "type": "ApplicationTechnology",
                        "application_id": app.id,
                        "application_name": app.name,
                        "deployment_status": app.deployment_status,
                        "age_years": getattr(app, "age_years", 0),
                        "platform_status": getattr(app, "platform_status", "supported"),
                        "technology_category": self._categorize_technology(tech),
                        "strategic_importance": self._assess_tech_importance(tech, app),
                        "modernization_need": self._assess_modernization_need(tech, app),
                    }
                )

        # Get from infrastructure components if available
        from app.models.platform_models import PlatformConfiguration

        platforms = PlatformConfiguration.query.all()
        for platform in platforms:
            components.append(
                {
                    "id": f"platform_{platform.id}",
                    "name": platform.name,
                    "type": "PlatformTechnology",
                    "platform_id": platform.id,
                    "technology_category": "Infrastructure",
                    "strategic_importance": getattr(
                        platform, "strategic_importance", "medium"
                    ),
                    "modernization_need": getattr(platform, "modernization_need", "low"),
                }
            )

        return components

    def _categorize_technology(self, technology: str) -> str:
        """Categorize technology based on its name/type."""

        tech_lower = technology.lower()

        # Programming languages
        if any(
            lang in tech_lower
            for lang in ["java", "python", "javascript", "c#", "php", "ruby", "go"]
        ):
            return "Programming Language"

        # Databases
        if any(
            db_name in tech_lower
            for db_name in ["mysql", "postgresql", "oracle", "sql server", "mongodb", "redis"]
        ):
            return "Database"

        # Web frameworks
        if any(
            framework in tech_lower
            for framework in ["react", "angular", "vue", "django", "flask", "spring"]
        ):
            return "Web Framework"

        # Cloud platforms
        if any(cloud in tech_lower for cloud in ["aws", "azure", "gcp", "heroku"]):
            return "Cloud Platform"

        # Container technologies
        if any(container in tech_lower for container in ["docker", "kubernetes", "openshift"]):
            return "Container Technology"

        # Message queues
        if any(queue in tech_lower for queue in ["rabbitmq", "kafka", "activemq"]):
            return "Message Queue"

        # Default
        return "Other"

    def _assess_tech_importance(self, technology: str, application) -> str:
        """Assess strategic importance of technology."""

        # Check if application is critical
        app_importance = getattr(application, "strategic_importance", "medium")
        if app_importance == "critical":
            return "critical"
        elif app_importance == "high":
            return "high"

        # Check if technology is core infrastructure
        core_tech = ["database", "application server", "web server", "message queue"]
        if any(core in technology.lower() for core in core_tech):
            return "high"

        return "medium"

    def _assess_modernization_need(self, technology: str, application) -> str:
        """Assess modernization need for technology."""

        # Check application age
        age = getattr(application, "age_years", 0)
        if age > 15:
            return "critical"
        elif age > 10:
            return "high"
        elif age > 5:
            return "medium"

        # Check platform status
        platform_status = getattr(application, "platform_status", "supported")
        if platform_status == "unsupported":
            return "critical"
        elif platform_status == "deprecated":
            return "high"

        # Check technology obsolescence
        obsolete_tech = ["flash", "silverlight", "ie6", "old framework"]
        if any(old in technology.lower() for old in obsolete_tech):
            return "critical"

        return "low"

    def _analyze_technology_component(self, component: Dict, include_innovation: bool) -> Dict:
        """Analyze modernization opportunities for a technology component."""

        # Calculate different modernization factors
        age_score = self._calculate_age_score(component)
        obsolescence_score = self._calculate_obsolescence_score(component)
        support_score = self._calculate_support_score(component)
        performance_score = self._calculate_performance_score(component)
        innovation_score = self._calculate_innovation_score(component) if include_innovation else 0

        # Calculate overall modernization priority score (0 - 100)
        total_score = (
            age_score + obsolescence_score + support_score + performance_score + innovation_score
        )

        # Determine modernization level
        if total_score >= 80:
            modernization_level = "CRITICAL"
        elif total_score >= 60:
            modernization_level = "HIGH"
        elif total_score >= 40:
            modernization_level = "MEDIUM"
        else:
            modernization_level = "LOW"

        # Identify specific modernization factors
        modernization_factors = []
        if age_score >= 20:
            modernization_factors.append("AGING_TECHNOLOGY")
        if obsolescence_score >= 15:
            modernization_factors.append("OBSOLETE_TECHNOLOGY")
        if support_score >= 15:
            modernization_factors.append("UNSUPPORTED_PLATFORM")
        if performance_score >= 15:
            modernization_factors.append("PERFORMANCE_ISSUES")
        if innovation_score >= 10 and include_innovation:
            modernization_factors.append("INNOVATION_OPPORTUNITY")

        # Estimate modernization needs
        modernization_needs = self._estimate_modernization_needs(component, total_score)

        return {
            "component_id": component["id"],
            "component_name": component["name"],
            "component_type": component["type"],
            "technology_category": component["technology_category"],
            "application_name": component.get("application_name", ""),
            "strategic_importance": component["strategic_importance"],
            "age_score": age_score,
            "obsolescence_score": obsolescence_score,
            "support_score": support_score,
            "performance_score": performance_score,
            "innovation_score": innovation_score,
            "modernization_priority_score": total_score,
            "modernization_level": modernization_level,
            "modernization_factors": modernization_factors,
            "modernization_needs": modernization_needs,
            "modernization_assessment": self._generate_modernization_assessment(
                component, modernization_factors, total_score
            ),
        }

    def _calculate_age_score(self, component: Dict) -> int:
        """Calculate age-based modernization score (0 - 25 points)."""

        age = component.get("age_years", 0)

        if age >= 15:
            return 25  # Very old - critical modernization needed
        elif age >= 10:
            return 20  # Old - high modernization needed
        elif age >= 5:
            return 15  # Moderate age - medium modernization
        elif age >= 2:
            return 10  # Recent - low modernization
        else:
            return 5  # New - minimal modernization

    def _calculate_obsolescence_score(self, component: Dict) -> int:
        """Calculate obsolescence score (0 - 20 points)."""

        tech_name = component["name"].lower()

        # Check for known obsolete technologies
        obsolete_tech = ["flash", "silverlight", "ie6", "activex", "vb6", "classic asp"]
        if any(old in tech_name for old in obsolete_tech):
            return 20  # Obsolete technology

        # Check for deprecated frameworks
        deprecated_tech = ["angularjs", "jquery 1.x", "bootstrap 3"]
        if any(deprecated in tech_name for deprecated in deprecated_tech):
            return 15  # Deprecated technology  # dead-code-ok

        # Check for older versions
        if any(version in tech_name for version in ["1.x", "2.x", "3.x"]):
            return 10  # Older version

        return 0  # Current technology

    def _calculate_support_score(self, component: Dict) -> int:
        """Calculate support-based modernization score (0 - 20 points)."""

        platform_status = component.get("platform_status", "supported")

        if platform_status == "unsupported":
            return 20  # No support - critical modernization
        elif platform_status == "deprecated":
            return 15  # Deprecated - high modernization  # dead-code-ok
        elif platform_status == "limited":
            return 10  # Limited support - medium modernization
        else:
            return 5  # Full support - minimal modernization

    def _calculate_performance_score(self, component: Dict) -> int:
        """Calculate performance-based modernization score (0 - 20 points)."""

        # This would be based on actual performance metrics
        # For now, use heuristics based on technology type

        tech_category = component["technology_category"]
        tech_name = component["name"].lower()

        # Known performance issues
        performance_issues = ["old database", "legacy framework", "monolithic architecture"]
        if any(issue in tech_name for issue in performance_issues):
            return 15  # Performance issues identified

        # Database performance concerns
        if tech_category == "Database" and "mysql" in tech_name:
            return 10  # Potential performance concerns

        return 0  # No known performance issues

    def _calculate_innovation_score(self, component: Dict) -> int:
        """Calculate innovation opportunity score (0 - 15 points)."""

        tech_name = component["name"].lower()
        tech_category = component["technology_category"]

        # Check for modern, innovative technologies
        innovative_tech = ["kubernetes", "microservices", "serverless", "ai/ml", "blockchain"]
        if any(innovative in tech_name for innovative in innovative_tech):
            return 15  # High innovation opportunity

        # Check for emerging categories
        emerging_categories = ["AI/ML", "Blockchain", "IoT", "Edge Computing"]
        if tech_category in emerging_categories:
            return 10  # Emerging technology

        # Check for cloud-native technologies
        cloud_native = ["docker", "kubernetes", "serverless", "lambda"]
        if any(cloud in tech_name for cloud in cloud_native):
            return 10  # Cloud-native opportunity

        return 0  # No specific innovation opportunity

    def _estimate_modernization_needs(self, component: Dict, score: int) -> Dict:
        """Estimate modernization needs for the component."""

        # Base modernization estimation
        if score >= 80:
            base_cost = 500000  # $500k for critical modernization
            complexity_multiplier = 1.5
        elif score >= 60:
            base_cost = 250000  # $250k for high modernization
            complexity_multiplier = 1.2
        elif score >= 40:
            base_cost = 100000  # $100k for medium modernization
            complexity_multiplier = 1.0
        else:
            base_cost = 50000  # $50k for low modernization
            complexity_multiplier = 0.8

        # Adjust for strategic importance
        if component["strategic_importance"] == "critical":
            complexity_multiplier *= 1.3
        elif component["strategic_importance"] == "low":
            complexity_multiplier *= 0.8

        estimated_cost = base_cost * complexity_multiplier

        # Timeframe estimation
        if score >= 80:
            timeframe = "12 - 18 months"
        elif score >= 60:
            timeframe = "6 - 12 months"
        elif score >= 40:
            timeframe = "3 - 6 months"
        else:
            timeframe = "1 - 3 months"

        return {
            "estimated_cost": estimated_cost,
            "currency": "USD",
            "timeframe": timeframe,
            "modernization_type": "REPLACE"
            if score >= 70
            else "UPGRADE"
            if score >= 50
            else "OPTIMIZE",
            "complexity": "HIGH"
            if complexity_multiplier > 1.2
            else "MEDIUM"
            if complexity_multiplier > 1.0
            else "LOW",
        }

    def _generate_modernization_assessment(self, component: Dict, factors: List, score: int) -> str:
        """Generate modernization assessment for the component."""

        if score >= 80:
            return f"CRITICAL: {component['name']} requires immediate modernization - multiple critical issues identified"
        elif score >= 60:
            return (
                f"HIGH: {component['name']} has significant modernization needs requiring attention"
            )
        elif score >= 40:
            return f"MEDIUM: {component['name']} has moderate modernization opportunities that should be considered"
        else:
            return f"LOW: {component['name']} is current with minimal modernization needs"

    def _calculate_portfolio_metrics(self, tech_analyses: List[Dict]) -> Dict:
        """Calculate portfolio-level technology metrics."""

        total_components = len(tech_analyses)
        critical_count = len([t for t in tech_analyses if t["modernization_level"] == "CRITICAL"])
        high_count = len([t for t in tech_analyses if t["modernization_level"] == "HIGH"])

        # Technology category distribution
        category_counts = {}
        for tech in tech_analyses:
            category = tech["technology_category"]
            category_counts[category] = category_counts.get(category, 0) + 1

        # Modernization factor distribution
        aging_count = len(
            [t for t in tech_analyses if "AGING_TECHNOLOGY" in t["modernization_factors"]]
        )
        obsolete_count = len(
            [t for t in tech_analyses if "OBSOLETE_TECHNOLOGY" in t["modernization_factors"]]
        )
        unsupported_count = len(
            [t for t in tech_analyses if "UNSUPPORTED_PLATFORM" in t["modernization_factors"]]
        )

        # Average scores
        avg_age_score = (
            sum(t["age_score"] for t in tech_analyses) / total_components
            if total_components > 0
            else 0
        )
        avg_obsolescence_score = (
            sum(t["obsolescence_score"] for t in tech_analyses) / total_components
            if total_components > 0
            else 0
        )
        avg_support_score = (
            sum(t["support_score"] for t in tech_analyses) / total_components
            if total_components > 0
            else 0
        )

        return {
            "total_components": total_components,
            "critical_modernizations": critical_count,
            "high_modernizations": high_count,
            "technology_categories": category_counts,
            "aging_technologies": aging_count,
            "obsolete_technologies": obsolete_count,
            "unsupported_platforms": unsupported_count,
            "average_age_score": round(avg_age_score, 1),
            "average_obsolescence_score": round(avg_obsolescence_score, 1),
            "average_support_score": round(avg_support_score, 1),
            "portfolio_modernization_level": "HIGH"
            if critical_count > 5
            else "MEDIUM"
            if high_count > 10
            else "LOW",
        }

    def _generate_roadmap_phases(self, tech_analyses: List[Dict]) -> List[Dict]:
        """Generate technology roadmap phases."""

        phases = []

        # Phase 1: Critical modernizations (0 - 6 months)
        critical_tech = [t for t in tech_analyses if t["modernization_level"] == "CRITICAL"]
        if critical_tech:
            phases.append(
                {
                    "phase": "Phase 1",
                    "name": "Critical Modernizations",
                    "timeframe": "0 - 6 months",
                    "components": critical_tech,
                    "total_cost": sum(
                        t["modernization_needs"]["estimated_cost"] for t in critical_tech
                    ),
                    "priority": "CRITICAL",
                    "description": "Immediate modernization of critical and obsolete technologies",
                }
            )

        # Phase 2: High priority modernizations (6 - 12 months)
        high_tech = [t for t in tech_analyses if t["modernization_level"] == "HIGH"]
        if high_tech:
            phases.append(
                {
                    "phase": "Phase 2",
                    "name": "High Priority Modernizations",
                    "timeframe": "6 - 12 months",
                    "components": high_tech,
                    "total_cost": sum(
                        t["modernization_needs"]["estimated_cost"] for t in high_tech
                    ),
                    "priority": "HIGH",
                    "description": "Modernization of aging and deprecated technologies",
                }
            )

        # Phase 3: Medium priority improvements (12 - 18 months)
        medium_tech = [t for t in tech_analyses if t["modernization_level"] == "MEDIUM"]
        if medium_tech:
            phases.append(
                {
                    "phase": "Phase 3",
                    "name": "Technology Improvements",
                    "timeframe": "12 - 18 months",
                    "components": medium_tech,
                    "total_cost": sum(
                        t["modernization_needs"]["estimated_cost"] for t in medium_tech
                    ),
                    "priority": "MEDIUM",
                    "description": "Performance improvements and technology optimizations",
                }
            )

        # Phase 4: Innovation opportunities (18 - 24 months)
        innovation_tech = [
            t for t in tech_analyses if "INNOVATION_OPPORTUNITY" in t["modernization_factors"]
        ]
        if innovation_tech:
            phases.append(
                {
                    "phase": "Phase 4",
                    "name": "Innovation Initiatives",
                    "timeframe": "18 - 24 months",
                    "components": innovation_tech,
                    "total_cost": sum(
                        t["modernization_needs"]["estimated_cost"] for t in innovation_tech
                    ),
                    "priority": "LOW",
                    "description": "Adoption of innovative and emerging technologies",
                }
            )

        return phases

    def _generate_technology_recommendations(self, tech_analyses: List[Dict]) -> List[Dict]:
        """Generate technology modernization recommendations."""

        recommendations = []

        # Critical modernizations
        critical_tech = [t for t in tech_analyses if t["modernization_level"] == "CRITICAL"][:5]
        for tech in critical_tech:
            recommendations.append(
                {
                    "type": "IMMEDIATE_MODERNIZATION",
                    "priority": "CRITICAL",
                    "technology": tech["component_name"],
                    "modernization_level": tech["modernization_level"],
                    "modernization_factors": tech["modernization_factors"],
                    "recommendation": self._get_modernization_recommendation(tech),
                    "timeframe": tech["modernization_needs"]["timeframe"],
                    "estimated_cost": tech["modernization_needs"]["estimated_cost"],
                    "business_impact": "HIGH"
                    if tech["strategic_importance"] == "critical"
                    else "MEDIUM",
                }
            )

        # Technology consolidation opportunities
        category_counts = {}
        for tech in tech_analyses:
            category = tech["technology_category"]
            category_counts[category] = category_counts.get(category, 0) + 1

        consolidation_opps = [(cat, count) for cat, count in category_counts.items() if count > 3]
        if consolidation_opps:
            recommendations.append(
                {
                    "type": "TECHNOLOGY_CONSOLIDATION",
                    "priority": "MEDIUM",
                    "technology": f"{len(consolidation_opps)} technology categories",
                    "modernization_level": "MEDIUM",
                    "modernization_factors": ["CONSOLIDATION_OPPORTUNITY"],
                    "recommendation": "Consolidate redundant technologies to reduce complexity and maintenance overhead",
                    "timeframe": "6 - 12 months",
                    "estimated_cost": "SAVINGS",
                    "business_impact": "HIGH",
                }
            )

        # Innovation opportunities
        innovation_tech = [
            t for t in tech_analyses if "INNOVATION_OPPORTUNITY" in t["modernization_factors"]
        ]
        if innovation_tech:
            recommendations.append(
                {
                    "type": "INNOVATION_ADOPTION",
                    "priority": "LOW",
                    "technology": f"{len(innovation_tech)} technologies",
                    "modernization_level": "LOW",
                    "modernization_factors": ["INNOVATION_OPPORTUNITY"],
                    "recommendation": "Adopt innovative technologies to gain competitive advantage",
                    "timeframe": "12 - 24 months",
                    "estimated_cost": sum(
                        t["modernization_needs"]["estimated_cost"] for t in innovation_tech
                    ),
                    "business_impact": "HIGH",
                }
            )

        return recommendations

    def _get_modernization_recommendation(self, tech: Dict) -> str:
        """Get specific modernization recommendation for a technology."""

        factors = tech["modernization_factors"]

        if "AGING_TECHNOLOGY" in factors:
            return f"Replace {tech['component_name']} - aging technology requiring modernization"
        elif "OBSOLETE_TECHNOLOGY" in factors:
            return f"Replace {tech['component_name']} - obsolete technology with no vendor support"
        elif "UNSUPPORTED_PLATFORM" in factors:
            return (
                f"Migrate {tech['component_name']} - unsupported platform creating security risks"
            )
        elif "PERFORMANCE_ISSUES" in factors:
            return f"Upgrade {tech['component_name']} - performance issues affecting business operations"
        elif "INNOVATION_OPPORTUNITY" in factors:
            return (
                f"Adopt {tech['component_name']} - innovation opportunity for competitive advantage"
            )
        else:
            return f"Monitor {tech['component_name']} - continuous technology improvement needed"

    # ------------------------------------------------------------------
    # TD-006: Technology debt scoring — EOL detection and licence risk
    # ------------------------------------------------------------------

    def score_technology_debt(self, app_id: int) -> Dict:
        """Score technology debt for a single application.

        Returns a dict with keys:
            app_id, app_name, debt_score, risk_tier,
            eol_score, version_score, support_status_score,
            missing_data (names of the inputs the application has no value for),
            unreadable_data (names of the inputs that have a value the scorer cannot read),
            major_version (the major version number the version score was taken from, or None)

        Returns {} when the application is not found.

        Scoring:
        - eol_score:           EOL date past → 100; ≤12 months → 75;
                               ≤24 months → 50; else → 0.
        - version_score:       major version ≤ 7 (legacy heuristic) → 60; else 0.
        - support_status_score: 'unsupported' → 80; 'limited' → 60; a supported value → 0; blank or unrecognised text → not scored.
        - debt_score:          max(eol_score, version_score, support_status_score).
        - risk_tier:           ≥ 75 → 'critical'; ≥ 50 → 'high';
                               ≥ 25 → 'medium'; else 'low'.

        All reasoning strings cite actual DB column values — no fabricated text.
        Returns {} on application-not-found; returns {} on unexpected error.
        """
        import logging
        logger = logging.getLogger(__name__)

        try:
            from app.models.application_layer import ApplicationComponent

            app = ApplicationComponent.query.get(app_id)
            if app is None:
                return {}

            today = date.today()

            # -- EOL score -----------------------------------------------
            eol_date = app.end_of_life_date  # model-safety-ok
            if eol_date is not None:
                if isinstance(eol_date, datetime):
                    eol_date = eol_date.date()
                days_left = (eol_date - today).days
                if days_left < 0:
                    eol_score = 100
                elif days_left <= 365:
                    eol_score = 75
                elif days_left <= 730:
                    eol_score = 50
                else:
                    eol_score = 0
            else:
                eol_score = 0

            # -- Version score -------------------------------------------
            try:
                version_str = app.version or ""  # model-safety-ok
            except AttributeError:
                version_str = ""
            version_score = 0
            major_version = None
            version_unreadable = False
            if version_str:
                try:
                    major_version = int(str(version_str).split(".")[0])
                    if major_version <= 7:
                        version_score = 60
                except (ValueError, IndexError):
                    version_unreadable = True

            # -- Support status score ------------------------------------
            # support_status is the test-facing attribute; support_level is the DB column.
            try:
                _raw_support = app.support_status or ""  # model-safety-ok
            except AttributeError:
                try:
                    _raw_support = app.support_level or ""  # model-safety-ok
                except AttributeError:
                    _raw_support = ""
            from app.services.import_validation.validation_schemas import classify_support_level

            support_state = classify_support_level(_raw_support)
            support_status_score = {"unsupported": 80, "limited": 60}.get(support_state, 0)
            support_unrecognised = support_state == "unrecognised"

            # -- Composite -----------------------------------------------
            debt_score = max(eol_score, version_score, support_status_score)

            if debt_score >= 75:
                risk_tier = "critical"
            elif debt_score >= 50:
                risk_tier = "high"
            elif debt_score >= 25:
                risk_tier = "medium"
            else:
                risk_tier = "low"

            return {
                "app_id": app_id,
                "app_name": app.name,  # model-safety-ok
                "debt_score": debt_score,
                "risk_tier": risk_tier,
                "eol_score": eol_score,
                "version_score": version_score,
                "support_status_score": support_status_score,
                "major_version": major_version,
                "unreadable_data": (["version"] if version_unreadable else [])
                + (["support status"] if support_unrecognised else []),
                "unrecognised_support_status": str(_raw_support).strip() if support_unrecognised else None,
                "missing_data": [
                    label
                    for label, present in (
                        ("end-of-life date", eol_date is not None),
                        ("version", bool(version_str)),
                        ("support status", support_state != "blank"),
                    )
                    if not present
                ],
            }

        except Exception as exc:
            logger.warning("score_technology_debt app_id=%s: %s", app_id, exc)
            return {}
