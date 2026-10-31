"""
Industry-Specific APQC Service

Provides industry-tailored APQC process recommendations and management.

Features:
- Industry framework management (Manufacturing, Finance, Healthcare, etc.)
- Industry-specific process variants
- Benchmark comparisons
- AI-powered recommendations based on industry context
- Regulatory compliance mapping

Usage:
    service = IndustryAPQCService()

    # Get processes for an industry
    processes = service.get_industry_processes('MFG')

    # Get recommendations for an organization
    recommendations = service.generate_recommendations(
        industry_code='MFG',
        current_maturity_data={...}
    )
"""

from datetime import datetime
from typing import Dict, List, Optional

from flask import current_app

from app import db
from app.models.apqc_process import APQCProcess
from app.models.industry_apqc import (
    IndustryAPQCFramework,
    IndustryAPQCProcess,
    IndustryProcessRecommendation,
)


class IndustryAPQCService:
    """
    Service for managing industry-specific APQC process frameworks.
    """

    # Industry codes and their full names
    INDUSTRY_CODES = {
        "MFG": "Manufacturing",
        "MFG_D": "Discrete Manufacturing",
        "MFG_P": "Process Manufacturing",
        "BFS": "Banking & Financial Services",
        "INS": "Insurance",
        "HCP": "Healthcare Provider",
        "HCY": "Healthcare Payer",
        "PHA": "Pharmaceutical & Life Sciences",
        "TEL": "Telecommunications",
        "UTL": "Utilities",
        "RET": "Retail",
        "CPG": "Consumer Packaged Goods",
        "GOV": "Government",
        "EDU": "Education",
        "TRN": "Transportation & Logistics",
        "ENR": "Energy",
        "CRS": "Cross-Industry",
    }

    # Default industry characteristics
    INDUSTRY_CHARACTERISTICS = {
        "MFG": {
            "regulatory_intensity": "medium",
            "digital_maturity_typical": 3,
            "process_complexity": "complex",
            "key_differentiators": [
                "Supply chain and logistics processes",
                "Production and manufacturing operations",
                "Quality management and compliance",
                "Product lifecycle management",
            ],
            "unique_process_areas": [
                "Production planning and scheduling",
                "Shop floor management",
                "Equipment maintenance",
                "Material requirements planning",
            ],
        },
        "BFS": {
            "regulatory_intensity": "very_high",
            "digital_maturity_typical": 4,
            "process_complexity": "highly_complex",
            "key_differentiators": [
                "Regulatory compliance (SOX, Basel, GDPR)",
                "Risk management processes",
                "Customer onboarding and KYC",
                "Transaction processing",
            ],
            "unique_process_areas": [
                "Credit risk assessment",
                "Treasury management",
                "Regulatory reporting",
                "Anti-money laundering",
            ],
        },
        "HCP": {
            "regulatory_intensity": "very_high",
            "digital_maturity_typical": 3,
            "process_complexity": "highly_complex",
            "key_differentiators": [
                "Patient care delivery",
                "Clinical documentation",
                "Regulatory compliance (HIPAA, HITECH)",
                "Revenue cycle management",
            ],
            "unique_process_areas": [
                "Patient registration and scheduling",
                "Clinical decision support",
                "Medical records management",
                "Care coordination",
            ],
        },
    }

    def __init__(self):
        """Initialize the industry APQC service."""
        self.app = current_app._get_current_object() if current_app else None

    # =========================================================================
    # FRAMEWORK MANAGEMENT
    # =========================================================================

    def get_all_frameworks(self, active_only: bool = True) -> List[IndustryAPQCFramework]:
        """
        Get all industry frameworks.

        Args:
            active_only: Only return active frameworks

        Returns:
            List of IndustryAPQCFramework objects
        """
        query = IndustryAPQCFramework.query
        if active_only:
            query = query.filter(IndustryAPQCFramework.is_active == True)
        return query.order_by(IndustryAPQCFramework.industry_name).all()

    def get_framework_by_code(self, industry_code: str) -> Optional[IndustryAPQCFramework]:
        """
        Get a specific industry framework by code.

        Args:
            industry_code: Industry code (e.g., 'MFG', 'BFS')

        Returns:
            IndustryAPQCFramework or None
        """
        return IndustryAPQCFramework.query.filter_by(industry_code=industry_code.upper()).first()

    def create_framework(
        self, industry_code: str, industry_name: str, **kwargs
    ) -> IndustryAPQCFramework:
        """
        Create a new industry framework.

        Args:
            industry_code: Unique industry code
            industry_name: Display name
            **kwargs: Additional framework attributes

        Returns:
            Created IndustryAPQCFramework
        """
        # Get default characteristics if available
        defaults = self.INDUSTRY_CHARACTERISTICS.get(industry_code.upper(), {})

        framework = IndustryAPQCFramework(
            industry_code=industry_code.upper(),
            industry_name=industry_name,
            industry_subcategory=kwargs.get("industry_subcategory"),
            description=kwargs.get("description"),
            pcf_version=kwargs.get("pcf_version", "8.0.0"),
            regulatory_intensity=kwargs.get(
                "regulatory_intensity", defaults.get("regulatory_intensity", "medium")
            ),
            digital_maturity_typical=kwargs.get(
                "digital_maturity_typical", defaults.get("digital_maturity_typical", 3)
            ),
            process_complexity=kwargs.get(
                "process_complexity", defaults.get("process_complexity", "moderate")
            ),
            key_differentiators=kwargs.get(
                "key_differentiators", defaults.get("key_differentiators", [])
            ),
            unique_process_areas=kwargs.get(
                "unique_process_areas", defaults.get("unique_process_areas", [])
            ),
            benchmark_metrics=kwargs.get("benchmark_metrics"),
            maturity_benchmarks=kwargs.get("maturity_benchmarks"),
            is_active=True,
        )

        db.session.add(framework)
        db.session.commit()
        return framework

    def seed_default_frameworks(self) -> List[IndustryAPQCFramework]:
        """Load any default framework that is missing (the one loader the
        migration and a new organisation also use). Returns those added."""
        from app.modules.industry_apqc.framework_data import load_default_frameworks

        try:
            _, newly_added_codes = load_default_frameworks(db.session.connection())
            db.session.commit()
            db.session.remove()
        except Exception as e:
            current_app.logger.error(f"Error seeding default frameworks: {e}")
            raise
        newly_added_framework_objects = []
        if newly_added_codes:
            newly_added_framework_objects = db.session.query(IndustryAPQCFramework).filter(
                IndustryAPQCFramework.industry_code.in_(list(newly_added_codes))
            ).all()
        return newly_added_framework_objects

    # =========================================================================
    # INDUSTRY PROCESS MANAGEMENT
    # =========================================================================

    def get_industry_processes(
        self, industry_code: str, level: Optional[int] = None, category: Optional[str] = None
    ) -> List[IndustryAPQCProcess]:
        """
        Get all processes for a specific industry.

        Args:
            industry_code: Industry code
            level: Optional APQC level filter (1 - 5)
            category: Optional category filter

        Returns:
            List of IndustryAPQCProcess objects
        """
        query = IndustryAPQCProcess.query.join(IndustryAPQCFramework).filter(
            IndustryAPQCFramework.industry_code == industry_code.upper(),
            IndustryAPQCProcess.is_active == True,
        )

        if category:
            query = query.filter(IndustryAPQCProcess.industry_category_1 == category)

        return query.order_by(IndustryAPQCProcess.industry_process_code).all()

    def get_industry_unique_processes(self, industry_code: str) -> List[IndustryAPQCProcess]:
        """
        Get processes unique to an industry (not in cross-industry PCF).

        Args:
            industry_code: Industry code

        Returns:
            List of industry-unique processes
        """
        return IndustryAPQCProcess.get_industry_unique_processes(industry_code.upper())

    def get_regulatory_processes(
        self, industry_code: str, regulation: Optional[str] = None
    ) -> List[IndustryAPQCProcess]:
        """
        Get processes with regulatory requirements.

        Args:
            industry_code: Industry code
            regulation: Optional specific regulation filter (e.g., 'HIPAA', 'SOX')

        Returns:
            List of processes with regulatory requirements
        """
        processes = IndustryAPQCProcess.get_with_regulatory_requirements(industry_code.upper())

        if regulation:
            filtered = []
            for p in processes:
                if p.regulatory_requirements:
                    for req in p.regulatory_requirements:
                        if regulation.upper() in req.get("regulation", "").upper():
                            filtered.append(p)
                            break
            return filtered

        return processes

    def map_base_process_to_industry(
        self,
        base_process_id: int,
        industry_code: str,
        industry_name: str = None,
        industry_description: str = None,
        **kwargs,
    ) -> IndustryAPQCProcess:
        """
        Create an industry variant from a base APQC process.

        Args:
            base_process_id: ID of the base APQCProcess
            industry_code: Industry code to map to
            industry_name: Optional industry-specific name
            industry_description: Optional industry-specific description
            **kwargs: Additional industry-specific attributes

        Returns:
            Created IndustryAPQCProcess
        """
        base_process = db.session.get(APQCProcess, base_process_id)
        if not base_process:
            raise ValueError(f"Base process {base_process_id} not found")

        framework = self.get_framework_by_code(industry_code)
        if not framework:
            raise ValueError(f"Industry framework {industry_code} not found")

        industry_process = IndustryAPQCProcess(
            industry_framework_id=framework.id,
            base_process_id=base_process_id,
            industry_process_code=base_process.process_code,
            industry_process_name=industry_name or base_process.process_name,
            industry_process_description=industry_description or base_process.process_description,
            industry_category_1=base_process.category_level_1,
            industry_category_2=base_process.category_level_2,
            industry_category_3=base_process.category_level_3,
            is_industry_unique=False,
            is_modified_from_base=bool(industry_name or industry_description),
            **kwargs,
        )

        db.session.add(industry_process)
        db.session.commit()
        return industry_process

    # =========================================================================
    # RECOMMENDATIONS
    # =========================================================================

    def generate_recommendations(
        self,
        industry_code: str,
        current_maturity_data: Dict[str, int],
        current_automation_data: Optional[Dict[str, int]] = None,
        focus_areas: Optional[List[str]] = None,
        max_recommendations: int = 10,
    ) -> List[IndustryProcessRecommendation]:
        """
        Generate AI-powered recommendations based on current state assessment.

        Args:
            industry_code: Industry code
            current_maturity_data: Dict of process_code -> maturity level (1 - 5)
            current_automation_data: Optional dict of process_code -> automation % (0 - 100)
            focus_areas: Optional list of focus areas to prioritize
            max_recommendations: Maximum number of recommendations to generate

        Returns:
            List of generated recommendations
        """
        framework = self.get_framework_by_code(industry_code)
        if not framework:
            return []

        recommendations = []
        processes = self.get_industry_processes(industry_code)

        for process in processes:
            current_maturity = current_maturity_data.get(process.industry_process_code, 2)
            current_automation = (current_automation_data or {}).get(
                process.industry_process_code, 30
            )

            # Calculate gaps
            target_maturity = 4  # Target top quartile
            maturity_gap = target_maturity - current_maturity

            benchmark_automation = process.typical_automation_level or 50
            automation_gap = benchmark_automation - current_automation

            # Skip if already at target
            if maturity_gap <= 0 and automation_gap <= 0:
                continue

            # Generate recommendation
            rec = self._generate_process_recommendation(
                framework=framework,
                process=process,
                current_maturity=current_maturity,
                current_automation=current_automation,
                maturity_gap=maturity_gap,
                automation_gap=automation_gap,
            )

            if rec:
                recommendations.append(rec)

        # Sort by priority score and limit
        recommendations.sort(key=lambda r: r.priority_score or 0, reverse=True)
        return recommendations[:max_recommendations]

    def _generate_process_recommendation(
        self,
        framework: IndustryAPQCFramework,
        process: IndustryAPQCProcess,
        current_maturity: int,
        current_automation: int,
        maturity_gap: int,
        automation_gap: int,
    ) -> Optional[IndustryProcessRecommendation]:
        """
        Generate a single process recommendation.

        Args:
            framework: Industry framework
            process: Industry process
            current_maturity: Current maturity level
            current_automation: Current automation percentage
            maturity_gap: Gap to target maturity
            automation_gap: Gap to target automation

        Returns:
            IndustryProcessRecommendation or None
        """
        # Determine recommendation type based on gaps
        if automation_gap > 30:
            rec_type = "automation"
            impact = 8
            effort = 6
            title = f"Automate {process.industry_process_name}"
            description = f"Increase automation from {current_automation}% to {current_automation + automation_gap}% to match industry benchmarks."
        elif maturity_gap >= 2:
            rec_type = "process_improvement"
            impact = 9
            effort = 7
            title = f"Improve Maturity: {process.industry_process_name}"
            description = f"Advance from maturity level {current_maturity} to level {current_maturity + maturity_gap} through process standardization and optimization."
        elif maturity_gap == 1:
            rec_type = "process_improvement"
            impact = 6
            effort = 4
            title = f"Optimize {process.industry_process_name}"
            description = f"Refine process to reach maturity level {current_maturity + 1}."
        else:
            return None

        # Calculate priority score
        priority_score = (impact / effort) * (1 + maturity_gap * 0.2)

        # Determine if quick win
        is_quick_win = impact >= 7 and effort <= 4
        is_strategic = impact >= 8 and effort >= 6

        recommendation = IndustryProcessRecommendation(
            industry_framework_id=framework.id,
            industry_process_id=process.id,
            current_maturity=current_maturity,
            current_automation=current_automation,
            performance_gap=maturity_gap,
            recommendation_type=rec_type,
            recommendation_title=title,
            recommendation_description=description,
            impact_score=impact,
            effort_score=effort,
            priority_score=round(priority_score, 2),
            estimated_roi=round((impact - effort) * 10 + 50, 1),  # Simplified ROI
            implementation_steps=self._get_implementation_steps(rec_type, process),
            required_technologies=process.technology_enablers,
            typical_timeline=self._estimate_timeline(effort),
            recommended_vendors=process.leading_vendors,
            is_quick_win=is_quick_win,
            is_strategic_initiative=is_strategic,
            status="pending",
            ai_confidence=0.85,
        )

        db.session.add(recommendation)
        return recommendation

    def _get_implementation_steps(self, rec_type: str, process: IndustryAPQCProcess) -> List[Dict]:
        """Generate implementation steps based on recommendation type."""
        if rec_type == "automation":
            return [
                {
                    "step": 1,
                    "action": "Document current process workflow",
                    "duration": "1 - 2 weeks",
                },
                {"step": 2, "action": "Identify automation opportunities", "duration": "1 week"},
                {"step": 3, "action": "Select automation technology", "duration": "2 weeks"},
                {"step": 4, "action": "Develop automation solution", "duration": "4 - 8 weeks"},
                {"step": 5, "action": "Test and validate", "duration": "2 weeks"},
                {"step": 6, "action": "Deploy and monitor", "duration": "1 week"},
            ]
        else:
            return [
                {"step": 1, "action": "Assess current state in detail", "duration": "1 week"},
                {"step": 2, "action": "Define target state and KPIs", "duration": "1 week"},
                {"step": 3, "action": "Develop improvement roadmap", "duration": "2 weeks"},
                {"step": 4, "action": "Implement process changes", "duration": "4 - 12 weeks"},
                {"step": 5, "action": "Train staff and stakeholders", "duration": "2 weeks"},
                {"step": 6, "action": "Monitor and optimize", "duration": "Ongoing"},
            ]

    def _estimate_timeline(self, effort: int) -> str:
        """Estimate timeline based on effort score."""
        if effort <= 3:
            return "1 - 2 months"
        elif effort <= 5:
            return "2 - 4 months"
        elif effort <= 7:
            return "4 - 6 months"
        else:
            return "6 - 12 months"

    def get_pending_recommendations(
        self,
        industry_code: Optional[str] = None,
        recommendation_type: Optional[str] = None,
        quick_wins_only: bool = False,
        limit: int = 50,
    ) -> List[IndustryProcessRecommendation]:
        """
        Get pending recommendations for review.

        Args:
            industry_code: Optional filter by industry
            recommendation_type: Optional filter by type
            quick_wins_only: Only return quick wins
            limit: Maximum results

        Returns:
            List of pending recommendations
        """
        query = IndustryProcessRecommendation.query.filter(
            IndustryProcessRecommendation.status == "pending"
        )

        if industry_code:
            query = query.join(IndustryAPQCFramework).filter(
                IndustryAPQCFramework.industry_code == industry_code.upper()
            )

        if recommendation_type:
            query = query.filter(
                IndustryProcessRecommendation.recommendation_type == recommendation_type
            )

        if quick_wins_only:
            query = query.filter(IndustryProcessRecommendation.is_quick_win == True)

        return (
            query.order_by(IndustryProcessRecommendation.priority_score.desc()).limit(limit).all()
        )

    def accept_recommendation(self, recommendation_id: int, user_id: int) -> Dict:
        """Accept a recommendation."""
        rec = db.session.get(IndustryProcessRecommendation, recommendation_id)
        if not rec:
            return {"success": False, "error": "Recommendation not found"}

        rec.status = "accepted"
        rec.accepted_by_id = user_id
        rec.accepted_at = datetime.utcnow()
        db.session.commit()

        return {"success": True, "recommendation_id": recommendation_id}

    def reject_recommendation(self, recommendation_id: int, user_id: int, reason: str) -> Dict:
        """Reject a recommendation with reason."""
        rec = db.session.get(IndustryProcessRecommendation, recommendation_id)
        if not rec:
            return {"success": False, "error": "Recommendation not found"}

        rec.status = "rejected"
        rec.accepted_by_id = user_id  # Tracking who rejected
        rec.accepted_at = datetime.utcnow()
        rec.rejection_reason = reason
        db.session.commit()

        return {"success": True, "recommendation_id": recommendation_id}

    # =========================================================================
    # HIERARCHY AND ADOPTION
    # =========================================================================

    def framework_summaries(self) -> List[Dict]:
        """One entry per framework with its process counts, for the list page."""
        from sqlalchemy import func

        counts = {
            row.industry_framework_id: row
            for row in db.session.query(
                IndustryAPQCProcess.industry_framework_id.label("industry_framework_id"),
                func.count(IndustryAPQCProcess.id).label("total"),
                func.count(IndustryAPQCProcess.id)
                .filter(IndustryAPQCProcess.is_industry_unique.is_(True))
                .label("specific"),
            )
            .filter(IndustryAPQCProcess.is_active.is_(True))
            .group_by(IndustryAPQCProcess.industry_framework_id)
            .all()
        }
        summaries = []
        for fw in self.get_all_frameworks():
            row = counts.get(fw.id)
            summaries.append(
                {
                    "framework": fw,
                    "total": row.total if row else 0,
                    "specific": row.specific if row else 0,
                    "categories": 13,
                }
            )
        return summaries

    def build_hierarchy(self, industry_code: str) -> List[Dict]:
        """The framework as the PCF tree: categories, each holding its levels.

        Every node is ``{"id", "code", "name", "level", "specific", "children"}``.
        Industry specific processes sit directly under their category.
        """
        from app.modules.industry_apqc.framework_data import process_level

        processes = self.get_industry_processes(industry_code)
        nodes = {}
        for p in processes:
            nodes[p.industry_process_code] = {
                "id": p.id,
                "code": p.industry_process_code,
                "name": p.industry_process_name,
                "level": 2 if p.is_industry_unique else process_level(p.industry_process_code),
                "specific": bool(p.is_industry_unique),
                "children": [],
            }

        def parent_code(p):
            if p.is_industry_unique:
                return (p.industry_category_1 or "").split(" ", 1)[0]
            parts = p.industry_process_code.split(".")
            if process_level(p.industry_process_code) == 1:
                return None
            if len(parts) == 2:
                return parts[0] + ".0"
            return ".".join(parts[:-1])

        roots = []
        for p in processes:
            parent = nodes.get(parent_code(p)) if parent_code(p) else None
            (parent["children"] if parent else roots).append(nodes[p.industry_process_code])

        def order(node):
            if node["specific"]:
                return (1, 0, node["code"])
            return (0, [int(x) for x in node["code"].split(".")], "")

        def sort(items):
            items.sort(key=order)
            for item in items:
                sort(item["children"])

        sort(roots)
        roots.sort(key=lambda n: [int(x) for x in n["code"].split(".")])
        return roots

    def recommended_processes(self, industry_code: str, organization_id: int) -> List[Dict]:
        """The processes particular to this industry, and whether the
        organisation's capability model already holds each one."""
        from app.models.unified_capability import UnifiedCapability

        processes = [
            p for p in self.get_industry_processes(industry_code) if p.is_industry_unique
        ]
        held = {
            (name or "").strip().lower()
            for (name,) in db.session.query(UnifiedCapability.name).filter(
                UnifiedCapability.organization_id == organization_id
            )
        }
        return [
            {
                "process": p,
                "category": p.industry_category_1,
                "adopted": p.industry_process_name.strip().lower() in held,
            }
            for p in processes
        ]

    def adopt_process(self, industry_code: str, process_id: int, organization_id: int):
        """Add an industry process to the organisation's capability model
        through the one capability writer. Returns (capability, created)."""
        from app.modules.capabilities.services.capability_service import (
            ensure_capability_record,
        )

        process = (
            IndustryAPQCProcess.query.join(IndustryAPQCFramework)
            .filter(
                IndustryAPQCProcess.id == process_id,
                IndustryAPQCFramework.industry_code == industry_code.upper(),
            )
            .first()
        )
        if process is None:
            return None, False
        capability, created = ensure_capability_record(
            name=process.industry_process_name,
            level=1,
            description=(
                "Adopted from the %s process framework (%s)."
                % (process.framework.industry_name, process.industry_process_code)
            ),
            organization_id=organization_id,
            scope="tenant",
        )
        db.session.commit()
        if created:
            # The capability map caches its hierarchy per organisation for five
            # minutes; drop it so the adopted process is on the page now.
            from app.extensions.cache import invalidate_cache

            invalidate_cache("capability_map:*:%s" % organization_id)
        return capability, created

    # =========================================================================
    # ANALYTICS
    # =========================================================================

    def get_industry_statistics(self, industry_code: str) -> Dict:
        """
        Get statistics for an industry framework.

        Args:
            industry_code: Industry code

        Returns:
            Statistics dictionary
        """
        framework = self.get_framework_by_code(industry_code)
        if not framework:
            return {}

        processes = self.get_industry_processes(industry_code)
        unique_processes = self.get_industry_unique_processes(industry_code)
        regulatory_processes = self.get_regulatory_processes(industry_code)

        # Count by category
        categories = {}
        for p in processes:
            cat = p.industry_category_1 or "Uncategorized"
            categories[cat] = categories.get(cat, 0) + 1

        return {
            "industry_code": industry_code,
            "industry_name": framework.industry_name,
            "total_processes": len(processes),
            "unique_processes": len(unique_processes),
            "regulatory_processes": len(regulatory_processes),
            "processes_by_category": categories,
            "regulatory_intensity": framework.regulatory_intensity,
            "digital_maturity_typical": framework.digital_maturity_typical,
            "process_complexity": framework.process_complexity,
        }


# Singleton instance
industry_apqc_service = IndustryAPQCService()
