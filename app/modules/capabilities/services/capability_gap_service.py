"""
DEPRECATED: Import from app.modules.capabilities.services instead.
-> app.modules.capabilities.services.analysis_service

Capability Gap Analysis Service

Bridges BusinessCapability maturity/application gaps with ArchiMate 3.2
Implementation & Migration Gap elements. Analyzes which capabilities lack
adequate application support and creates formal Gap elements for migration planning.

Integration:
- BusinessCapability (maturity_gap, current/target levels)
- ApplicationComponent (support_level, coverage_percent)
- ArchiMate Gap elements (Implementation & Migration layer)
- Plateau elements (current state vs target state)
- Work Package generation for gap closure
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app import db
from app.datetime_helpers import utcnow
from app.models import (
    ADRCapabilityLink,
    ArchiMateElement,
    ArchiMateRelationship,
    BusinessCapability,
    ComplianceGap,
    ComplianceRequirement,
    QualityAttribute,
)
from app.models.application_portfolio import ApplicationComponent
from app.services.archimate.implementation_migration_service import ImplementationMigrationService
from app.services.compliance.compliance_inheritance_service import ComplianceInheritanceService
from app.services.decorators import transactional
from app.services.llm_service import LLMService


class CapabilityGapAnalysisService:
    """
    AI-powered capability gap analysis with ArchiMate 3.2 Gap element creation.

    Identifies:
    1. Capabilities with no application support
    2. Capabilities with inadequate coverage (<80%)
    3. Maturity gaps (current < target)
    4. Technical debt and replacement needs

    Creates:
    - ArchiMate Gap elements
    - Links to Plateau (current/target states)
    - Work Packages for gap remediation
    - AI-powered recommendations (build/buy/partner)
    """

    @transactional
    def __init__(self):
        self.llm_service = LLMService()
        self.implementation_service = ImplementationMigrationService()

    # ========================================================================
    # Helper Methods
    # ========================================================================

    @transactional
    def _get_level_0_domain(self, capability: BusinessCapability) -> str:
        """
        Get the Level 0 (root) capability name which represents the domain.
        Domains are Level 0 capabilities, not system classifications.

        Args:
            capability: The capability to find the domain for

        Returns:
            Name of the Level 0 capability (domain) or 'Unknown Domain'
        """
        # If this is already Level 0, it IS the domain
        if capability.level == 0 or not capability.parent_capability_id:
            return capability.name

        # Walk up the hierarchy to find Level 0
        current = capability
        while current.parent_capability_id:
            parent = db.session.get(BusinessCapability, current.parent_capability_id)
            if not parent:
                break
            if parent.level == 0 or not parent.parent_capability_id:
                return parent.name
            current = parent

        # Fallback: return the root we found or 'Unknown Domain'
        return current.name if current.id != capability.id else "Unknown Domain"

    # ========================================================================
    # Capability Gap Identification
    # ========================================================================

    def analyze_capability_gaps(
        self,
        architecture_id: Optional[int] = None,
        min_coverage_threshold: int = 80,
        include_maturity_gaps: bool = True,
    ) -> Dict[str, List[Dict]]:
        """
        Comprehensive capability gap analysis.

        Args:
            architecture_id: Filter by architecture model (optional)
            min_coverage_threshold: Minimum acceptable coverage % (default 80)
            include_maturity_gaps: Include capabilities with maturity gaps

        Returns:
            Dict with categorized gaps:
            {
                'no_application': [...],  # Capabilities with no apps
                'inadequate_coverage': [...],  # Apps exist but <threshold
                'maturity_gaps': [...],  # Current < target maturity
                'technical_debt': [...],  # High debt/replacement needed
                'summary': {...}
            }
        """
        query = BusinessCapability.query

        # Optional architecture filter
        if architecture_id:
            if hasattr(BusinessCapability, 'architecture_model_id'):
                query = query.filter_by(architecture_model_id=architecture_id)

        capabilities = query.all()

        gaps = {
            "no_application": [],
            "inadequate_coverage": [],
            "maturity_gaps": [],
            "technical_debt": [],
            "summary": {
                "total_capabilities": len(capabilities),
                "gaps_identified": 0,
                "critical_gaps": 0,
                "gaps_with_decisions": 0,
                "average_compliance_score": 0.0,
            },
        }

        category_mapping = {
            "no_application": "no_application",
            "inadequate_coverage": "inadequate_coverage",
            "maturity_gap": "maturity_gaps",
            "technical_debt": "technical_debt",
            "compliance_violation": "compliance_violations",
        }

        gaps["compliance_violations"] = []
        total_compliance_score = 0.0

        for capability in capabilities:
            gap_infos = self._collect_capability_gaps(
                capability, min_coverage_threshold, include_maturity_gaps
            )

            if not gap_infos:
                # Still check compliance for "healthy" capabilities - temporarily disabled
                # total_compliance_score += ComplianceInheritanceService.calculate_capability_compliance_score(capability.id)
                continue

            # Update compliance score - temporarily disabled
            # total_compliance_score += ComplianceInheritanceService.calculate_capability_compliance_score(capability.id)
            gaps["summary"]["gaps_identified"] += len(gap_infos)

            for gap_info in gap_infos:
                category_key = category_mapping.get(gap_info["gap_type"])
                if category_key:
                    gaps[category_key].append(gap_info)

                if gap_info.get("severity") == "critical":
                    gaps["summary"]["critical_gaps"] += 1
                if gap_info.get("has_governing_decision"):
                    gaps["summary"]["gaps_with_decisions"] += 1

        if gaps["summary"]["total_capabilities"] > 0:
            gaps["summary"]["average_compliance_score"] = (
                total_compliance_score / gaps["summary"]["total_capabilities"]
            )

        gaps["summary"]["gaps_without_decisions"] = max(
            0, gaps["summary"]["gaps_identified"] - gaps["summary"]["gaps_with_decisions"]
        )

        return gaps

    def _collect_capability_gaps(
        self, capability: BusinessCapability, min_coverage: int, include_maturity: bool
    ) -> List[Dict]:
        """
        Analyze a single capability for all applicable gaps.

        Returns:
            List of gap detail dictionaries (may be empty)
        """
        gap_infos: List[Dict] = []

        # Check Compliance first (Capability Compliance Requirement logic)
        # Compliance gaps - temporarily disabled
        # compliance_results = ComplianceInheritanceService.validate_capability_compliance(capability.id)
        # if compliance_results['compliance_score'] < 80:
        #     gaps['compliance_gaps'].append({
        #         'capability_id': capability.id,
        #         'capability_name': capability.name,
        #         'gap_type': 'compliance_violation',
        #         'severity': 'critical' if compliance_score < 50 else 'high',
        #         'compliance_score': compliance_score,
        #         'description': f"Capability compliance is at {compliance_score:.1f}%. One or more mandatory requirements are not satisfied by supporting applications.",
        #         'current_maturity': capability.current_maturity_level,
        #         'target_maturity': capability.target_maturity_level
        #     })

        # Get applications through the mapping table
        from app.models.application_portfolio import ApplicationCapabilityMapping

        app_capabilities = (
            ApplicationComponent.query.join(ApplicationCapabilityMapping)
            .filter(ApplicationCapabilityMapping.business_capability_id == capability.id)
            .all()
        )

        domain = self._get_level_0_domain(capability)
        max_coverage = max((getattr(ac, 'code_coverage_percent', 0) or 0 for ac in app_capabilities), default=0)  # model-safety-ok: field may not exist on all ApplicationComponent instances

        maturity_gap_value = self._effective_maturity_gap(capability)
        decision_records = self._serialize_decision_links(getattr(capability, "decision_links", []))
        has_decision = bool(decision_records)

        # No application support
        if not app_capabilities:
            gap_infos.append(
                {
                    "capability_id": capability.id,
                    "capability_name": capability.name,
                    "capability_description": capability.description,
                    "domain": domain,
                    "level": capability.level,
                    "strategic_importance": capability.strategic_importance,
                    "gap_type": "no_application",
                    "severity": self._calculate_severity(capability, 0),
                    "current_coverage": 0,
                    "applications": [],
                    "current_maturity": capability.current_maturity_level,
                    "target_maturity": capability.target_maturity_level,
                    "maturity_gap": maturity_gap_value,
                    "decision_records": decision_records,
                    "has_governing_decision": has_decision,
                }
            )
        else:
            # Inadequate coverage
            if max_coverage < min_coverage:
                gap_infos.append(
                    {
                        "capability_id": capability.id,
                        "capability_name": capability.name,
                        "capability_description": capability.description,
                        "domain": domain,
                        "level": capability.level,
                        "strategic_importance": capability.strategic_importance,
                        "gap_type": "inadequate_coverage",
                        "severity": self._calculate_severity(capability, max_coverage),
                        "current_coverage": max_coverage,
                        "required_coverage": min_coverage,
                        "coverage_gap": max(0, min_coverage - max_coverage),
                        "applications": [
                            {
                                "name": ac.name,
                                "type": ac.component_type,
                                "coverage": getattr(ac, 'code_coverage_percent', 0) or 0,  # model-safety-ok: field may not exist on all ApplicationComponent instances
                            }
                            for ac in app_capabilities
                        ],
                        "current_maturity": capability.current_maturity_level,
                        "target_maturity": capability.target_maturity_level,
                        "decision_records": decision_records,
                        "has_governing_decision": has_decision,
                    }
                )

        # Maturity gap (evaluate even when no apps exist)
        if include_maturity and maturity_gap_value > 0:
            gap_infos.append(
                {
                    "capability_id": capability.id,
                    "capability_name": capability.name,
                    "capability_description": capability.description,
                    "domain": domain,
                    "level": capability.level,
                    "strategic_importance": capability.strategic_importance,
                    "gap_type": "maturity_gap",
                    "severity": self._calculate_maturity_severity(capability),
                    "current_coverage": max_coverage,
                    "applications": [
                        {
                            "name": ac.name,
                            "type": ac.component_type,
                            "coverage": getattr(ac, 'code_coverage_percent', 0) or 0,  # model-safety-ok: field may not exist on all ApplicationComponent instances
                        }
                        for ac in app_capabilities
                    ],
                    "current_maturity": capability.current_maturity_level,
                    "target_maturity": capability.target_maturity_level,
                    "maturity_gap": maturity_gap_value,
                    "decision_records": decision_records,
                    "has_governing_decision": has_decision,
                }
            )

        # Technical debt (only if applications exist)
        if app_capabilities:
            high_debt_apps = [
                ac
                for ac in app_capabilities
                if (getattr(ac, 'technical_debt_hours', 0) or 0) > 70  # model-safety-ok: field may not exist on all ApplicationComponent instances
            ]

            if high_debt_apps:
                severity = self._calculate_technical_debt_severity(
                    capability, high_debt_apps, max_coverage
                )
                gap_infos.append(
                    {
                        "capability_id": capability.id,
                        "capability_name": capability.name,
                        "capability_description": capability.description,
                        "domain": domain,
                        "level": capability.level,
                        "strategic_importance": capability.strategic_importance,
                        "gap_type": "technical_debt",
                        "severity": severity,
                        "current_coverage": max_coverage,
                        "applications": [
                            {
                                "name": ac.name,
                                "type": ac.component_type,
                                "technical_debt_hours": getattr(ac, 'technical_debt_hours', 0) or 0,  # model-safety-ok: field may not exist on all ApplicationComponent instances
                            }
                            for ac in high_debt_apps
                        ],
                        "current_maturity": capability.current_maturity_level,
                        "target_maturity": capability.target_maturity_level,
                        "worst_technical_debt_hours": max(
                            getattr(ac, 'technical_debt_hours', 0) or 0 for ac in high_debt_apps  # model-safety-ok: field may not exist on all ApplicationComponent instances
                        ),
                        "decision_records": decision_records,
                        "has_governing_decision": has_decision,
                    }
                )

        return gap_infos

    def perform_compliance_rollup(self, capability_id: int) -> Dict[str, Any]:
        """
        Compliance Maturity Rollup:
        1. Calculates compliance score for a capability.
        2. Adjusts stored_maturity on the BusinessCapability model based on compliance impact.
        3. Creates/Updates ComplianceGap entries for missing requirements.

        This is the core function linking compliance to capability maturity.
        """
        cap = BusinessCapability.query.get(capability_id)
        if not cap:
            return {"status": "error", "message": f"Capability {capability_id} not found"}

        # 1. Get Compliance Validation Results
        compliance_results = ComplianceInheritanceService.validate_capability_compliance(
            capability_id
        )
        score = compliance_results.get("compliance_score", 0.0)

        # 2. Get Maturity Penalty Impact
        # Compliance < 50% => cap at 1
        # Compliance < 80% => cap at 2
        # Otherwise no cap (max 5)
        penalty_cap = ComplianceInheritanceService.get_compliance_impact_on_maturity(capability_id)

        # 3. Adjust and persist StoredMaturity
        original_maturity = cap.current_maturity_level

        # Apply the compliance floor/cap
        adjusted_maturity = (
            min(original_maturity, penalty_cap) if penalty_cap < 5 else original_maturity
        )

        cap.current_maturity_level = adjusted_maturity

        # 4. Sync Gaps to DB
        gaps_created = 0
        for app_val in compliance_results.get("application_validations", []):
            for gap_dict in app_val.get("gaps", []):
                # Identify if this gap already exists for this app and requirement
                existing = ComplianceGap.query.filter_by(
                    compliance_requirement_id=gap_dict["requirement_id"], status="open"
                ).first()

                if not existing:
                    new_gap = ComplianceGap(
                        gap_type="missing_requirement",
                        title=f"Compliance Gap: {gap_dict['requirement_title']}",
                        description=(
                            f"Application '{app_val['application_name']}' does not satisfy requirement "
                            f"'{gap_dict['requirement_title']}' inherited from Capability '{cap.name}'."
                        ),
                        compliance_requirement_id=gap_dict["requirement_id"],
                        risk_level=gap_dict["severity"] or "medium",
                        status="open",
                        identified_at=utcnow(),
                    )
                    db.session.add(new_gap)
                    gaps_created += 1

        db.session.commit()

        return {
            "status": "success",
            "capability_id": capability_id,
            "compliance_score": score,
            "original_maturity": original_maturity,
            "stored_maturity": cap.current_maturity_level,
            "penalty_applied": cap.current_maturity_level < original_maturity,
            "gaps_created": gaps_created,
        }

    def _serialize_decision_links(self, links: List[ADRCapabilityLink]) -> List[Dict]:
        if not links:
            return []

        serialised: List[Dict] = []
        for link in links:
            if not link or not link.adr:
                continue
            serialised.append(
                {
                    "adr_id": link.adr_id,
                    "adr_number": link.adr.adr_number,
                    "status": link.adr.status,
                    "title": link.adr.title,
                    "relationship_type": link.relationship_type,
                    "impact_level": link.impact_level,
                }
            )
        return serialised

    def _calculate_severity(self, capability: BusinessCapability, coverage: int) -> str:
        """
        Calculate gap severity based on strategic importance and coverage.

        Returns: 'critical' | 'high' | 'medium' | 'low'
        """
        importance = (capability.strategic_importance or "").lower()

        if importance == "critical" and coverage < 50:
            return "critical"
        elif importance in ["critical", "high"] and coverage < 80:
            return "high"
        elif coverage < 60:
            return "medium"
        else:
            return "low"

    def _calculate_maturity_severity(self, capability: BusinessCapability) -> str:
        """Calculate severity based on maturity gap."""
        gap = capability.maturity_gap or 0
        importance = (capability.strategic_importance or "").lower()

        if importance == "critical" and gap >= 2:
            return "critical"
        elif gap >= 3:
            return "high"
        elif gap >= 2:
            return "medium"
        else:
            return "low"

    def _calculate_technical_debt_severity(
        self,
        capability: BusinessCapability,
        high_debt_apps: List[ApplicationComponent],
        coverage: int,
    ) -> str:
        """Score technical debt severity using debt score, coverage, and importance."""
        importance = (capability.strategic_importance or "").lower()
        max_debt_hours = max((getattr(ac, 'technical_debt_hours', 0) or 0) for ac in high_debt_apps)  # model-safety-ok: field may not exist on all ApplicationComponent instances

        if max_debt_hours >= 90 or (importance == "critical" and max_debt_hours >= 80):
            return "critical"
        if max_debt_hours >= 80 or (importance in ["critical", "high"] and max_debt_hours >= 70):
            return "high"
        if max_debt_hours >= 60:
            return "medium"

        # Elevate severity when debt and coverage combine to increase risk
        if coverage < 50 and importance in ["critical", "high"]:
            return "high"

        return "low"

    # ========================================================================
    # Requirement Generation Prioritization
    # ========================================================================

    def get_requirement_generation_priority(
        self, architecture_id: Optional[int] = None, limit: int = 15
    ) -> List[Dict]:
        """Return ranked capabilities that need requirements generated."""
        query = BusinessCapability.query

        if architecture_id:
            if hasattr(BusinessCapability, 'architecture_model_id'):
                query = query.filter_by(architecture_model_id=architecture_id)

        capabilities = query.filter(
            BusinessCapability.target_maturity_level.isnot(None),
            BusinessCapability.current_maturity_level.isnot(None),
        ).all()

        ranked: List[Dict] = []

        for capability in capabilities:
            maturity_gap = self._effective_maturity_gap(capability)
            if maturity_gap <= 0:
                continue

            (
                priority_score,
                coverage_gap,
                max_coverage,
                application_count,
            ) = self._score_capability_for_requirements(capability, maturity_gap)

            ranked.append(
                {
                    "capability_id": capability.id,
                    "capability_name": capability.name,
                    "domain": self._get_level_0_domain(capability),
                    "level": capability.level,
                    "strategic_importance": capability.strategic_importance,
                    "maturity_gap": maturity_gap,
                    "current_maturity": capability.current_maturity_level,
                    "target_maturity": capability.target_maturity_level,
                    "coverage_gap": coverage_gap,
                    "max_coverage": max_coverage,
                    "applications_count": application_count,
                    "compliance_requirements": self._compliance_requirement_count(
                        capability.compliance_requirements
                    ),
                    "priority_score": priority_score,
                }
            )

        ranked.sort(
            key=lambda item: (
                -item["priority_score"],
                -item["maturity_gap"],
                item["capability_name"],
            )
        )

        if limit:
            ranked = ranked[:limit]

        return ranked

    def _effective_maturity_gap(self, capability: BusinessCapability) -> int:
        """Calculate gap even when stored field is null."""
        if capability.maturity_gap is not None:
            return capability.maturity_gap

        if capability.target_maturity_level is None or capability.current_maturity_level is None:
            return 0

        return max(capability.target_maturity_level - capability.current_maturity_level, 0)

    def _compliance_requirement_count(self, compliance_rel: Any) -> int:
        """Return count of compliance requirements regardless of relationship type."""
        if compliance_rel is None:
            return 0

        try:
            all_attr = getattr(compliance_rel, "all", None)
            if callable(all_attr):
                return len(all_attr())

            count_attr = getattr(compliance_rel, "count", None)
            if callable(count_attr):
                return count_attr()
        except TypeError:
            # Fall back when count() expects arguments (e.g. list.count)
            pass

        try:
            return len(compliance_rel)
        except TypeError:
            return len(list(compliance_rel))

    def _score_capability_for_requirements(
        self, capability: BusinessCapability, maturity_gap: int
    ) -> Tuple[float, int, int]:
        """Score capability for requirement generation priority."""
        importance = (capability.strategic_importance or "").lower()
        importance_weight = {"critical": 1.6, "high": 1.3, "medium": 1.0, "low": 0.7}.get(
            importance, 1.0
        )

        from app.models.application_capability import ApplicationCapabilityMapping
        # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
        app_mappings = ApplicationCapabilityMapping.query.filter_by(business_capability_id=capability.id).all()
        applications = [ApplicationComponent.query.get(m.application_component_id) for m in app_mappings if m.application_component_id]
        applications = [a for a in applications if a is not None]
        application_count = len(applications)
        max_coverage = (
            max((getattr(app, 'code_coverage_percent', 0) or 0) for app in applications) if applications else 0  # model-safety-ok: field may not exist on all ApplicationComponent instances
        )
        coverage_gap = max(0, 100 - max_coverage)

        # Compliance pressure boosts score when requirements already mapped
        compliance_rel = getattr(capability, "compliance_requirements", None)
        if compliance_rel is None:
            compliance_count = 0
        else:
            try:
                if hasattr(compliance_rel, "all"):
                    # .count() aggregates in the database; len(.all()) built an
                    # ORM object per row only to discard it.
                    compliance_count = compliance_rel.count()
                elif hasattr(compliance_rel, "count") and callable(
                    getattr(compliance_rel, "count")
                ):
                    compliance_count = compliance_rel.count()
                else:
                    compliance_count = len(compliance_rel)
            except TypeError:
                compliance_count = len(compliance_rel)

        score = maturity_gap * 10 * importance_weight
        score += coverage_gap * 0.6
        score += compliance_count * 2

        # Penalize missing application support entirely
        if application_count == 0:
            score += 15 * importance_weight

        # Slight boost for higher capability level (strategic capabilities first)
        score += max(0, 4 - (capability.level or 1)) * 1.5

        return score, coverage_gap, max_coverage, application_count

    # ========================================================================
    # ArchiMate Gap Element Creation
    # ========================================================================

    @transactional
    def create_archimate_gaps_from_analysis(
        self,
        gap_analysis: Dict,
        architecture_id: int,
        current_plateau_id: Optional[int] = None,
        target_plateau_id: Optional[int] = None,
    ) -> List[ArchiMateElement]:
        """
        Create ArchiMate Gap elements from capability gap analysis.

        Args:
            gap_analysis: Output from analyze_capability_gaps()
            architecture_id: Architecture model ID
            current_plateau_id: Optional current state Plateau
            target_plateau_id: Optional target state Plateau

        Returns:
            List of created Gap ArchiMateElements
        """
        gaps = []

        # Create Gap elements for each identified gap
        all_gaps = (
            gap_analysis["no_application"]
            + gap_analysis["inadequate_coverage"]
            + gap_analysis["maturity_gaps"]
            + gap_analysis["technical_debt"]
        )

        for gap_info in all_gaps:
            gap_element = self._upsert_gap_element(
                gap_info=gap_info,
                architecture_id=architecture_id,
                current_plateau_id=current_plateau_id,
                target_plateau_id=target_plateau_id,
            )
            gaps.append(gap_element)

            self._ensure_capability_link(gap_element, gap_info.get("capability_id"))
            self._ensure_plateau_relationships(
                gap_element, architecture_id, current_plateau_id, target_plateau_id
            )

        db.session.commit()
        return gaps

    @transactional
    def _upsert_gap_element(
        self,
        gap_info: Dict,
        architecture_id: int,
        current_plateau_id: Optional[int] = None,
        target_plateau_id: Optional[int] = None,
    ) -> ArchiMateElement:
        """Create or update a Gap element for the supplied gap details."""
        existing_gap = self._find_existing_gap_element(
            architecture_id=architecture_id,
            capability_id=gap_info.get("capability_id"),
            gap_type=gap_info.get("gap_type"),
        )

        existing_properties = (
            json.loads(existing_gap.properties) if existing_gap and existing_gap.properties else {}
        )
        related_compliance_gaps = self._get_related_compliance_gaps(gap_info.get("capability_id"))

        name, description = self._build_gap_identity(gap_info)
        properties = self._build_gap_properties(
            gap_info=gap_info,
            existing_properties=existing_properties,
            related_compliance_gaps=related_compliance_gaps,
            current_plateau_id=current_plateau_id,
            target_plateau_id=target_plateau_id,
        )

        if existing_gap:
            existing_gap.name = name
            existing_gap.description = description
            from app.modules.architecture_assistant.property_service import PropertyService
            PropertyService().merge_element_properties(existing_gap, properties, source="capability_gap")
            gap_element = existing_gap
        else:
            gap_element = ArchiMateElement(
                name=name,
                type="Gap",
                layer="implementation_migration",
                description=description,
                architecture_id=architecture_id,
            )
            db.session.add(gap_element)
            db.session.flush()
            from app.modules.architecture_assistant.property_service import PropertyService
            PropertyService().merge_element_properties(gap_element, properties, source="capability_gap")

        self._ensure_compliance_relationships(gap_element, related_compliance_gaps)
        return gap_element

    def _build_gap_identity(self, gap_info: Dict) -> Tuple[str, str]:
        """Construct gap name and human-readable description."""
        capability_name = gap_info.get("capability_name", "Capability")
        gap_type = (gap_info.get("gap_type") or "gap").replace("_", " ").title()

        name = f"Gap: {capability_name} - {gap_type}"

        if gap_info.get("gap_type") == "no_application":
            description = f"No application support for {capability_name} capability."
        elif gap_info.get("gap_type") == "inadequate_coverage":
            description = (
                f"Inadequate coverage ({gap_info.get('current_coverage', 0)}%) for "
                f"{capability_name}; requires {gap_info.get('required_coverage', 0)}%."
            )
        elif gap_info.get("gap_type") == "maturity_gap":
            description = (
                f"Maturity gap (L{gap_info.get('current_maturity')} → "
                f"L{gap_info.get('target_maturity')}) for {capability_name}."
            )
        else:
            description = f"Elevated technical debt impacting {capability_name} capability support."

        return name, description

    def _build_gap_properties(
        self,
        gap_info: Dict,
        existing_properties: Dict,
        related_compliance_gaps: List[ComplianceGap],
        current_plateau_id: Optional[int],
        target_plateau_id: Optional[int],
    ) -> Dict:
        """Merge existing properties with the latest analysis payload."""
        identified_at = existing_properties.get("identified_at", utcnow().isoformat())
        compliance_gap_ids = sorted(
            {
                *(existing_properties.get("related_compliance_gap_ids") or []),
                *(gap.id for gap in related_compliance_gaps),
            }
        )

        applications_payload = gap_info.get("applications")
        if applications_payload is None:
            applications_payload = existing_properties.get("applications", [])

        properties: Dict[str, Any] = {
            "capability_id": gap_info.get("capability_id"),
            "capability_name": gap_info.get("capability_name"),
            "domain": gap_info.get("domain"),
            "gap_type": gap_info.get("gap_type"),
            "severity": gap_info.get("severity"),
            "strategic_importance": gap_info.get("strategic_importance"),
            "current_coverage": gap_info.get("current_coverage"),
            "required_coverage": gap_info.get("required_coverage"),
            "coverage_gap": gap_info.get("coverage_gap"),
            "current_maturity": gap_info.get("current_maturity"),
            "target_maturity": gap_info.get("target_maturity"),
            "maturity_gap": gap_info.get("maturity_gap"),
            "worst_technical_debt_score": gap_info.get("worst_technical_debt_score"),
            "applications": applications_payload,
            "identified_at": identified_at,
            "latest_update_at": utcnow().isoformat(),
            "current_plateau_id": current_plateau_id
            or existing_properties.get("current_plateau_id"),
            "target_plateau_id": target_plateau_id or existing_properties.get("target_plateau_id"),
            "related_compliance_gap_ids": compliance_gap_ids,
        }

        # Carry forward properties that are not explicitly recalculated
        for key in ["applications", "current_plateau_id", "target_plateau_id"]:
            if properties.get(key) is None and existing_properties.get(key) is not None:
                properties[key] = existing_properties[key]

        return properties

    @transactional
    def _find_existing_gap_element(
        self, architecture_id: int, capability_id: Optional[int], gap_type: Optional[str]
    ) -> Optional[ArchiMateElement]:
        if not capability_id or not gap_type:
            return None

        candidates = ArchiMateElement.query.filter_by(
            architecture_id=architecture_id, type="Gap"
        ).all()

        for candidate in candidates:
            props = json.loads(candidate.properties) if candidate.properties else {}
            if props.get("capability_id") == capability_id and props.get("gap_type") == gap_type:
                return candidate

        return None

    @transactional
    def _ensure_capability_link(
        self, gap_element: ArchiMateElement, capability_id: Optional[int]
    ) -> None:
        if not capability_id:
            return

        capability = db.session.get(BusinessCapability, capability_id)
        if not capability:
            return

        cap_arch_id = getattr(capability, 'architecture_model_id', None)
        if (
            cap_arch_id
            and cap_arch_id != gap_element.architecture_id
        ):
            return

        if hasattr(capability, 'ensure_archimate_element'):
            capability_element = capability.ensure_archimate_element()
        else:
            logger.warning(f"ensure_archimate_element not available on {type(capability).__name__}")
            capability_element = None

        if capability_element is None:
            return

        if capability_element.architecture_id is None:
            capability_element.architecture_id = (
                getattr(capability, 'architecture_model_id', None) or gap_element.architecture_id
            )

        if not self._relationship_exists(
            source_id=capability_element.id,
            target_id=gap_element.id,
            architecture_id=gap_element.architecture_id,
            rel_type="association",
        ):
            db.session.add(
                ArchiMateRelationship(
                    type="association",
                    source_id=capability_element.id,
                    target_id=gap_element.id,
                    architecture_id=gap_element.architecture_id,
                )
            )

    @transactional
    def _ensure_plateau_relationships(
        self,
        gap_element: ArchiMateElement,
        architecture_id: int,
        current_plateau_id: Optional[int],
        target_plateau_id: Optional[int],
    ) -> None:
        if current_plateau_id:
            self._ensure_relationship(
                source_id=current_plateau_id,
                target_id=gap_element.id,
                architecture_id=architecture_id,
                rel_type="association",
            )

        if target_plateau_id:
            self._ensure_relationship(
                source_id=gap_element.id,
                target_id=target_plateau_id,
                architecture_id=architecture_id,
                rel_type="association",
            )

    @transactional
    def _ensure_relationship(
        self, source_id: int, target_id: int, architecture_id: int, rel_type: str
    ) -> None:
        if not self._relationship_exists(source_id, target_id, architecture_id, rel_type):
            db.session.add(
                ArchiMateRelationship(
                    type=rel_type,
                    source_id=source_id,
                    target_id=target_id,
                    architecture_id=architecture_id,
                )
            )

    def _relationship_exists(
        self, source_id: int, target_id: int, architecture_id: int, rel_type: str
    ) -> bool:
        return (
            ArchiMateRelationship.query.filter_by(
                source_id=source_id,
                target_id=target_id,
                architecture_id=architecture_id,
                type=rel_type,
            ).first()
            is not None
        )

    def _get_related_compliance_gaps(self, capability_id: Optional[int]) -> List[ComplianceGap]:
        if not capability_id:
            return []

        return (
            ComplianceGap.query.options(
                joinedload(ComplianceGap.compliance_requirement),
                joinedload(ComplianceGap.quality_attribute),
            )
            .filter(
                or_(
                    ComplianceGap.compliance_requirement.has(
                        ComplianceRequirement.applies_to_capability_id == capability_id
                    ),
                    ComplianceGap.quality_attribute.has(
                        QualityAttribute.applies_to_capability_id == capability_id
                    ),
                )
            )
            .all()
        )

    def _ensure_compliance_relationships(
        self, gap_element: ArchiMateElement, compliance_gaps: List[ComplianceGap]
    ) -> None:
        for compliance_gap in compliance_gaps:
            sources: List[int] = []

            requirement = compliance_gap.compliance_requirement
            if requirement and requirement.archimate_element_id:
                sources.append(requirement.archimate_element_id)

            quality_attr = compliance_gap.quality_attribute
            if quality_attr and quality_attr.archimate_element_id:
                sources.append(quality_attr.archimate_element_id)

            for source_id in sources:
                self._ensure_relationship(
                    source_id=source_id,
                    target_id=gap_element.id,
                    architecture_id=gap_element.architecture_id,
                    rel_type="association",
                )

    # ========================================================================
    # Work Package Generation
    # ========================================================================

    def generate_gap_remediation_work_packages(
        self, gap_ids: List[int], architecture_id: int
    ) -> List[ArchiMateElement]:
        """
        Generate Work Packages to close identified gaps.

        Args:
            gap_ids: List of Gap element IDs
            architecture_id: Architecture model ID

        Returns:
            List of WorkPackage ArchiMateElements
        """
        gaps = ArchiMateElement.query.filter(
            ArchiMateElement.id.in_(gap_ids), ArchiMateElement.type == "Gap"
        ).all()

        work_packages = []

        for gap in gaps:
            props = json.loads(gap.properties) if gap.properties else {}
            props.get("gap_type")

            # Generate AI recommendation
            recommendation = self._generate_gap_recommendation(gap, props)

            # Create Work Package
            wp_name = f"Close: {gap.name}"
            wp_description = recommendation["implementation_approach"]

            wp_properties = {
                "gap_id": gap.id,
                "recommendation": recommendation["recommendation"],
                "estimated_effort_weeks": recommendation["effort_estimate"],
                "estimated_cost": recommendation["cost_estimate"],
                "approach": recommendation["approach"],  # build/buy/partner
                "priority": props.get("severity", "medium"),
            }

            work_package = ArchiMateElement(
                name=wp_name,
                type="WorkPackage",
                layer="implementation_migration",
                description=wp_description,
                properties=json.dumps(wp_properties),
                architecture_id=architecture_id,
            )

            db.session.add(work_package)
            db.session.flush()

            # Link Work Package to Gap (realizes)
            relationship = ArchiMateRelationship(
                type="realization",
                source_id=work_package.id,
                target_id=gap.id,
                architecture_id=architecture_id,
            )
            db.session.add(relationship)

            work_packages.append(work_package)

        db.session.commit()
        return work_packages

    @transactional
    def _generate_gap_recommendation(self, gap: ArchiMateElement, gap_properties: Dict) -> Dict:
        """
        Generate AI-powered recommendation for gap closure.

        Returns:
            Dict with recommendation, approach (build/buy/partner), effort, cost
        """
        capability_id = gap_properties.get("capability_id")
        capability = db.session.get(BusinessCapability, capability_id) if capability_id else None

        if not capability:
            return {
                "recommendation": "Manual analysis required",
                "approach": "unknown",
                "effort_estimate": 0,
                "cost_estimate": 0,
                "implementation_approach": gap.description,
            }

        prompt = f"""You are an enterprise architect recommending how to close a capability gap.

Capability: {capability.name}
Description: {capability.description}
Domain: {self._get_level_0_domain(capability)}
Strategic Importance: {capability.strategic_importance}

Gap Type: {gap_properties.get('gap_type')}
Severity: {gap_properties.get('severity')}
Current Coverage: {gap_properties.get('current_coverage', 0)}%

Recommend:
1. **Approach**: BUILD (custom development) | BUY (COTS/SaaS) | PARTNER (integration)
2. **Implementation Strategy**: Concrete steps
3. **Effort Estimate**: Person-weeks
4. **Cost Estimate**: USD (rough order of magnitude)
5. **Rationale**: Why this approach

Return JSON:
{{
  "recommendation": "Brief recommendation (1 sentence)",
  "approach": "build|buy|partner",
  "implementation_approach": "Detailed implementation strategy",
  "effort_estimate": <person-weeks>,
  "cost_estimate": <usd>,
  "rationale": "Why this approach is best",
  "risks": ["Risk 1", "Risk 2"],
  "alternatives": ["Alternative 1", "Alternative 2"]
}}"""

        try:
            response = self.llm_service.generate_from_prompt(prompt)
        except Exception as exc:
            return self._fallback_recommendation(gap, f"LLM invocation failed: {exc}")

        parsed_payload = self._parse_json_payload(response)
        normalized = self._normalize_recommendation_payload(parsed_payload)

        if normalized:
            return normalized

        return self._fallback_recommendation(gap, "LLM response missing required fields")

    def _parse_json_payload(self, payload: str) -> Optional[Dict]:
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            start = payload.find("{")
            end = payload.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    trimmed = payload[start : end + 1]
                    return json.loads(trimmed)
                except json.JSONDecodeError:
                    return None
        return None

    def _normalize_recommendation_payload(self, payload: Optional[Dict]) -> Optional[Dict]:
        if not isinstance(payload, dict):
            return None

        required_keys = {
            "recommendation",
            "approach",
            "implementation_approach",
            "effort_estimate",
            "cost_estimate",
            "rationale",
        }

        if not required_keys.issubset(payload.keys()):
            return None

        def _coerce_to_list(value: Any) -> List[str]:
            if value is None:
                return []
            if isinstance(value, list):
                return [str(item) for item in value]
            return [str(value)]

        def _coerce_number(value: Any) -> Any:
            if isinstance(value, (int, float)):
                return value
            try:
                numeric = float(str(value).strip().replace(",", "").split(" ")[0])
                return numeric
            except (ValueError, AttributeError):
                return str(value)

        approach = str(payload.get("approach", "unknown")).lower().strip()
        if approach not in {"build", "buy", "partner"}:
            approach = "unknown"

        normalized = {
            "recommendation": str(payload["recommendation"]).strip(),
            "approach": approach,
            "implementation_approach": str(payload["implementation_approach"]).strip(),
            "effort_estimate": _coerce_number(payload.get("effort_estimate")),
            "cost_estimate": _coerce_number(payload.get("cost_estimate")),
            "rationale": str(payload.get("rationale", "")).strip() or "No rationale provided.",
            "risks": _coerce_to_list(payload.get("risks")),
            "alternatives": _coerce_to_list(payload.get("alternatives")),
        }

        return normalized

    def _fallback_recommendation(self, gap: ArchiMateElement, reason: str) -> Dict:
        return {
            "recommendation": f"Review required: {reason}",
            "approach": "unknown",
            "effort_estimate": 0,
            "cost_estimate": 0,
            "implementation_approach": gap.description or "No automated recommendation generated.",
            "rationale": reason,
            "risks": [],
            "alternatives": [],
        }

    # ========================================================================
    # Plateau Integration
    # ========================================================================

    def create_gap_plateaus(
        self,
        architecture_id: int,
        current_state_description: str = "Current State",
        target_state_description: str = "Target State",
    ) -> Tuple[ArchiMateElement, ArchiMateElement]:
        """
        Create current and target Plateau elements for gap analysis.

        Returns:
            Tuple of (current_plateau, target_plateau)
        """
        current_plateau = self.implementation_service.create_plateau(
            plateau_name="Current State - Capability Assessment",
            plateau_description=current_state_description,
            architecture_id=architecture_id,
            start_date=utcnow().isoformat(),
        )

        target_plateau = self.implementation_service.create_plateau(
            plateau_name="Target State - Full Capability Coverage",
            plateau_description=target_state_description,
            architecture_id=architecture_id,
        )

        return current_plateau, target_plateau

    # ========================================================================
    # Query and Reporting
    # ========================================================================

    def get_gaps_by_capability(self, capability_id: int) -> List[ArchiMateElement]:
        """Get all Gap elements for a capability."""
        gaps = ArchiMateElement.query.filter_by(type="Gap").all()

        # Filter by capability_id in properties
        matching_gaps = []
        for gap in gaps:
            props = json.loads(gap.properties) if gap.properties else {}
            if props.get("capability_id") == capability_id:
                matching_gaps.append(gap)

        return matching_gaps

    def get_gap_summary_statistics(self, architecture_id: Optional[int] = None) -> Dict:
        """
        Get summary statistics for gaps.

        Returns:
            Dict with counts by type, severity, domain
        """
        query = ArchiMateElement.query.filter_by(type="Gap")

        if architecture_id:
            query = query.filter_by(architecture_id=architecture_id)

        gaps = query.all()

        stats = {
            "total_gaps": len(gaps),
            "by_type": {},
            "by_severity": {},
            "by_domain": {},
            "critical_count": 0,
        }

        for gap in gaps:
            props = json.loads(gap.properties) if gap.properties else {}

            gap_type = props.get("gap_type", "unknown")
            stats["by_type"][gap_type] = stats["by_type"].get(gap_type, 0) + 1

            severity = props.get("severity", "unknown")
            stats["by_severity"][severity] = stats["by_severity"].get(severity, 0) + 1

            if severity == "critical":
                stats["critical_count"] += 1

            domain = props.get("domain") or "unknown"
            stats["by_domain"][domain] = stats["by_domain"].get(domain, 0) + 1

        return stats

    # ========================================================================
    # PHASE 3 ENHANCEMENTS: Strategic Architecture Analysis
    # ========================================================================

    def analyze_strategic_alignment(
        self,
        architecture_id: Optional[int] = None,
        strategic_objectives: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze strategic alignment of capabilities against business objectives.

        Provides solution architects with:
        - Strategic importance scoring (0 - 100)
        - Business value assessment
        - Investment prioritization matrix
        - Capability-objective alignment heatmap

        Args:
            architecture_id: Filter by architecture model
            strategic_objectives: List of strategic objectives to measure against

        Returns:
            Dict with strategic alignment analysis:
            {
                'capability_scores': [...],  # Strategic scores for each capability
                'investment_matrix': {...},  # ROI vs Strategic Importance
                'alignment_heatmap': {...},  # Capability x Objective matrix
                'recommendations': [...]  # Investment recommendations
            }
        """
        query = db.session.query(BusinessCapability)
        if architecture_id:
            if hasattr(BusinessCapability, 'architecture_model_id'):
                query = query.filter_by(architecture_model_id=architecture_id)

        capabilities = query.all()

        alignment_data = {
            "capability_scores": [],
            "investment_matrix": {"quadrants": {}},
            "alignment_heatmap": {},
            "recommendations": [],
        }

        for cap in capabilities:
            # Calculate strategic scores
            strategic_score = self._calculate_strategic_score(cap)
            business_value = self._calculate_business_value(cap)
            investment_priority = self._calculate_investment_priority(cap, strategic_score)

            capability_data = {
                "id": cap.id,
                "name": cap.name,
                "level": cap.level,
                "strategic_score": strategic_score,
                "business_value": business_value,
                "investment_priority": investment_priority,
                "maturity_gap": cap.maturity_gap or 0,
                "current_maturity": cap.current_maturity_level or 0,
                "target_maturity": cap.target_maturity_level or 0,
            }

            alignment_data["capability_scores"].append(capability_data)

            # Categorize into investment matrix quadrants
            quadrant = self._get_investment_quadrant(strategic_score, business_value)
            if quadrant not in alignment_data["investment_matrix"]["quadrants"]:
                alignment_data["investment_matrix"]["quadrants"][quadrant] = []
            alignment_data["investment_matrix"]["quadrants"][quadrant].append(capability_data)

        # Generate strategic recommendations
        alignment_data["recommendations"] = self._generate_strategic_recommendations(
            alignment_data["capability_scores"]
        )

        return alignment_data

    def analyze_capability_dependencies(
        self, architecture_id: Optional[int] = None, identify_critical_path: bool = True
    ) -> Dict[str, Any]:
        """
        Analyze capability interdependencies and identify critical paths.

        Provides solution architects with:
        - Dependency graph visualization data
        - Critical path identification
        - Risk cascading analysis
        - Dependency complexity scoring

        Args:
            architecture_id: Filter by architecture model
            identify_critical_path: Calculate critical path through dependencies

        Returns:
            Dict with dependency analysis:
            {
                'dependency_graph': {...},  # Nodes and edges for visualization
                'critical_path': [...],  # Critical capability sequence
                'risk_cascade': {...},  # Impact propagation analysis
                'dependency_metrics': {...}  # Complexity, coupling scores
            }
        """
        query = db.session.query(ArchiMateElement).filter_by(type="Capability")
        if architecture_id:
            query = query.filter_by(architecture_id=architecture_id)

        capabilities = query.all()

        # Build dependency graph
        graph = {"nodes": [], "edges": []}
        dependency_map = {}

        for cap in capabilities:
            # Parse dependencies from JSON fields
            upstream_deps = (
                json.loads(cap.upstream_dependencies) if cap.upstream_dependencies else []
            )
            downstream_deps = (
                json.loads(cap.downstream_dependents) if cap.downstream_dependents else []
            )

            node = {
                "id": cap.id,
                "name": cap.name,
                "dependency_level": cap.dependency_level or "unknown",
                "upstream_count": len(upstream_deps),
                "downstream_count": len(downstream_deps),
                "criticality_score": cap.strategic_alignment_score or 0,
            }
            graph["nodes"].append(node)
            dependency_map[cap.id] = {"upstream": upstream_deps, "downstream": downstream_deps}

            # Create edges
            for dep_id in upstream_deps:
                graph["edges"].append({"source": dep_id, "target": cap.id, "type": "depends_on"})

        # Identify critical path if requested
        critical_path = []
        if identify_critical_path:
            critical_path = self._identify_critical_path(dependency_map, capabilities)

        # Analyze risk cascading
        risk_cascade = self._analyze_risk_cascade(dependency_map, capabilities)

        # Calculate dependency metrics
        dependency_metrics = {
            "average_coupling": sum(
                len(d["upstream"]) + len(d["downstream"]) for d in dependency_map.values()
            )
            / len(dependency_map)
            if dependency_map
            else 0,
            "max_dependency_depth": self._calculate_max_depth(dependency_map),
            "circular_dependencies": self._detect_circular_dependencies(dependency_map),
            "isolated_capabilities": [
                n["id"]
                for n in graph["nodes"]
                if n["upstream_count"] == 0 and n["downstream_count"] == 0
            ],
        }

        return {
            "dependency_graph": graph,
            "critical_path": critical_path,
            "risk_cascade": risk_cascade,
            "dependency_metrics": dependency_metrics,
        }

    def analyze_architectural_patterns(
        self, architecture_id: Optional[int] = None, pattern_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Analyze architectural patterns and design patterns used across capabilities.

        Provides software architects with:
        - Pattern usage analysis
        - Reference architecture mapping
        - Design pattern recommendations
        - Architectural decision records (ADRs)

        Args:
            architecture_id: Filter by architecture model
            pattern_type: Filter by specific pattern type (microservices, layered, etc.)

        Returns:
            Dict with pattern analysis:
            {
                'pattern_usage': {...},  # Patterns used per capability
                'reference_architectures': [...],  # Applicable ref architectures
                'pattern_recommendations': [...],  # Suggested patterns
                'adr_summary': {...}  # ADR statistics
            }
        """
        query = db.session.query(ArchiMateElement)
        if architecture_id:
            query = query.filter_by(architecture_id=architecture_id)

        elements = query.all()

        pattern_analysis = {
            "pattern_usage": {},
            "reference_architectures": [],
            "pattern_recommendations": [],
            "adr_summary": {"total": 0, "by_status": {}},
        }

        for elem in elements:
            # Analyze architectural patterns
            if elem.architectural_pattern:
                pattern = elem.architectural_pattern
                if pattern not in pattern_analysis["pattern_usage"]:
                    pattern_analysis["pattern_usage"][pattern] = {"count": 0, "elements": []}
                pattern_analysis["pattern_usage"][pattern]["count"] += 1
                pattern_analysis["pattern_usage"][pattern]["elements"].append(
                    {"id": elem.id, "name": elem.name, "layer": elem.layer}
                )

            # Parse design pattern tags
            if elem.design_pattern_tags:
                design_patterns = (
                    json.loads(elem.design_pattern_tags)
                    if isinstance(elem.design_pattern_tags, str)
                    else elem.design_pattern_tags
                )
                for pattern in design_patterns:
                    if pattern not in pattern_analysis["pattern_usage"]:
                        pattern_analysis["pattern_usage"][pattern] = {"count": 0, "elements": []}
                    pattern_analysis["pattern_usage"][pattern]["count"] += 1

            # Collect ADRs
            if elem.architectural_decision_records:
                adrs = (
                    json.loads(elem.architectural_decision_records)
                    if isinstance(elem.architectural_decision_records, str)
                    else elem.architectural_decision_records
                )
                pattern_analysis["adr_summary"]["total"] += len(adrs)
                for adr in adrs:
                    status = adr.get("status", "unknown")
                    pattern_analysis["adr_summary"]["by_status"][status] = (
                        pattern_analysis["adr_summary"]["by_status"].get(status, 0) + 1
                    )

        # Generate pattern recommendations
        pattern_analysis["pattern_recommendations"] = self._generate_pattern_recommendations(
            pattern_analysis["pattern_usage"], elements
        )

        return pattern_analysis

    def analyze_quality_attributes(
        self, architecture_id: Optional[int] = None, attribute_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Analyze quality attributes across the architecture.

        Provides software architects with:
        - Performance metrics aggregation
        - Scalability assessment
        - Security posture analysis
        - Reliability metrics

        Args:
            architecture_id: Filter by architecture model
            attribute_type: Filter by attribute type (performance, security, etc.)

        Returns:
            Dict with quality attribute analysis:
            {
                'performance': {...},  # Performance metrics
                'scalability': {...},  # Scalability metrics
                'security': {...},  # Security posture
                'reliability': {...}  # Reliability metrics
            }
        """
        query = db.session.query(ArchiMateElement)
        if architecture_id:
            query = query.filter_by(architecture_id=architecture_id)

        elements = query.all()

        quality_analysis = {
            "performance": {"elements": [], "metrics": {}},
            "scalability": {"elements": [], "metrics": {}},
            "security": {"elements": [], "posture": {}},
            "reliability": {"elements": [], "metrics": {}},
        }

        for elem in elements:
            # Parse performance metrics
            if elem.performance_metrics:
                perf_data = (
                    json.loads(elem.performance_metrics)
                    if isinstance(elem.performance_metrics, str)
                    else elem.performance_metrics
                )
                quality_analysis["performance"]["elements"].append(
                    {"id": elem.id, "name": elem.name, "metrics": perf_data}
                )

            # Parse scalability metrics
            if elem.scalability_metrics:
                scale_data = (
                    json.loads(elem.scalability_metrics)
                    if isinstance(elem.scalability_metrics, str)
                    else elem.scalability_metrics
                )
                quality_analysis["scalability"]["elements"].append(
                    {"id": elem.id, "name": elem.name, "metrics": scale_data}
                )

            # Parse security posture
            if elem.security_posture:
                sec_data = (
                    json.loads(elem.security_posture)
                    if isinstance(elem.security_posture, str)
                    else elem.security_posture
                )
                quality_analysis["security"]["elements"].append(
                    {"id": elem.id, "name": elem.name, "posture": sec_data}
                )

            # Parse reliability metrics
            if elem.reliability_metrics:
                rel_data = (
                    json.loads(elem.reliability_metrics)
                    if isinstance(elem.reliability_metrics, str)
                    else elem.reliability_metrics
                )
                quality_analysis["reliability"]["elements"].append(
                    {"id": elem.id, "name": elem.name, "metrics": rel_data}
                )

        # Aggregate metrics
        quality_analysis["performance"]["metrics"] = self._aggregate_performance_metrics(
            quality_analysis["performance"]["elements"]
        )
        quality_analysis["scalability"]["metrics"] = self._aggregate_scalability_metrics(
            quality_analysis["scalability"]["elements"]
        )
        quality_analysis["security"]["posture"] = self._assess_security_posture(
            quality_analysis["security"]["elements"]
        )
        quality_analysis["reliability"]["metrics"] = self._aggregate_reliability_metrics(
            quality_analysis["reliability"]["elements"]
        )

        return quality_analysis

    # Helper methods for strategic analysis

    def _calculate_strategic_score(self, capability: BusinessCapability) -> float:
        """Calculate strategic importance score (0 - 100)"""
        score = 50.0  # Base score

        # Factor in strategic importance field
        if hasattr(capability, "strategic_importance"):
            importance_map = {"critical": 100, "high": 80, "medium": 50, "low": 20}
            score = importance_map.get(capability.strategic_importance, score)

        # Adjust for maturity gap
        if hasattr(capability, "maturity_gap") and capability.maturity_gap:
            score += min(capability.maturity_gap * 10, 20)

        return min(score, 100.0)

    def _calculate_business_value(self, capability: BusinessCapability) -> float:
        """Calculate business value score (0 - 100)"""
        value = 50.0

        # Factor in investment priority
        if hasattr(capability, "investment_priority") and capability.investment_priority:
            value = capability.investment_priority * 10

        return min(value, 100.0)

    def _calculate_investment_priority(
        self, capability: BusinessCapability, strategic_score: float
    ) -> int:
        """Calculate investment priority ranking (1 - 10)"""
        # Combine strategic score with maturity gap
        gap_weight = (capability.maturity_gap or 0) * 2
        priority = int((strategic_score + gap_weight) / 15)
        return min(max(priority, 1), 10)

    def _get_investment_quadrant(self, strategic_score: float, business_value: float) -> str:
        """Determine investment matrix quadrant"""
        if strategic_score >= 70 and business_value >= 70:
            return "transform"  # High strategic, high value - invest heavily
        elif strategic_score >= 70 and business_value < 70:
            return "strategic_invest"  # High strategic, lower value - strategic investment
        elif strategic_score < 70 and business_value >= 70:
            return "optimize"  # Lower strategic, high value - optimize existing
        else:
            return "maintain"  # Lower strategic, lower value - maintain/divest

    def _generate_strategic_recommendations(self, capability_scores: List[Dict]) -> List[Dict]:
        """Generate strategic investment recommendations"""
        recommendations = []

        # Sort by investment priority
        sorted_caps = sorted(
            capability_scores, key=lambda x: x["investment_priority"], reverse=True
        )

        # Top 5 investment priorities
        for cap in sorted_caps[:5]:
            if cap["maturity_gap"] > 0:
                recommendations.append(
                    {
                        "capability_id": cap["id"],
                        "capability_name": cap["name"],
                        "recommendation": "HIGH_PRIORITY_INVESTMENT",
                        "rationale": f"Strategic score {cap['strategic_score']:.1f}, maturity gap {cap['maturity_gap']}",
                        "priority": cap["investment_priority"],
                    }
                )

        return recommendations

    def _identify_critical_path(self, dependency_map: Dict, capabilities: List) -> List[Dict]:
        """Identify critical path through capability dependencies"""
        # Simplified critical path - find longest dependency chain
        critical_path = []

        def find_longest_path(node_id, visited=set()):
            if node_id in visited:
                return []
            visited.add(node_id)

            deps = dependency_map.get(node_id, {}).get("upstream", [])
            if not deps:
                return [node_id]

            longest = []
            for dep in deps:
                path = find_longest_path(dep, visited.copy())
                if len(path) > len(longest):
                    longest = path

            return longest + [node_id]

        # Find longest path from all nodes
        for cap_id in dependency_map.keys():
            path = find_longest_path(cap_id)
            if len(path) > len(critical_path):
                critical_path = path

        return [{"id": cid, "position": idx} for idx, cid in enumerate(critical_path)]

    def _analyze_risk_cascade(self, dependency_map: Dict, capabilities: List) -> Dict:
        """Analyze how risks cascade through dependencies"""
        cascade_analysis = {}

        for cap_id, deps in dependency_map.items():
            downstream_count = len(deps.get("downstream", []))
            upstream_count = len(deps.get("upstream", []))

            # Risk score based on dependency count
            risk_score = (downstream_count * 2) + (upstream_count * 1)

            cascade_analysis[cap_id] = {
                "risk_score": risk_score,
                "impact_radius": downstream_count,
                "dependency_depth": upstream_count,
                "risk_level": "critical"
                if risk_score > 10
                else "high"
                if risk_score > 5
                else "medium",
            }

        return cascade_analysis

    def _calculate_max_depth(self, dependency_map: Dict) -> int:
        """Calculate maximum dependency depth"""
        max_depth = 0

        def get_depth(node_id, visited=set()):
            if node_id in visited:
                return 0
            visited.add(node_id)

            deps = dependency_map.get(node_id, {}).get("upstream", [])
            if not deps:
                return 1

            return 1 + max(get_depth(dep, visited.copy()) for dep in deps)

        for cap_id in dependency_map.keys():
            depth = get_depth(cap_id)
            max_depth = max(max_depth, depth)

        return max_depth

    def _detect_circular_dependencies(self, dependency_map: Dict) -> List[List[int]]:
        """Detect circular dependencies in the graph"""
        cycles = []
        visited = set()
        rec_stack = set()

        def detect_cycle(node_id, path=[]):
            if node_id in rec_stack:
                cycle_start = path.index(node_id)
                cycles.append(path[cycle_start:])
                return True

            if node_id in visited:
                return False

            visited.add(node_id)
            rec_stack.add(node_id)
            path.append(node_id)

            deps = dependency_map.get(node_id, {}).get("upstream", [])
            for dep in deps:
                detect_cycle(dep, path.copy())

            rec_stack.remove(node_id)
            return False

        for cap_id in dependency_map.keys():
            detect_cycle(cap_id)

        return cycles

    def _generate_pattern_recommendations(self, pattern_usage: Dict, elements: List) -> List[Dict]:
        """Generate architectural pattern recommendations"""
        recommendations = []

        # Identify under-patterned elements
        unpatterned = [e for e in elements if not e.architectural_pattern]

        if len(unpatterned) > len(elements) * 0.3:  # >30% unpatterned
            recommendations.append(
                {
                    "type": "PATTERN_ADOPTION",
                    "severity": "medium",
                    "message": f"{len(unpatterned)} elements lack defined architectural patterns",
                    "action": "Define and document architectural patterns for consistency",
                }
            )

        # Check for pattern consistency
        if len(pattern_usage) > 5:  # Too many different patterns
            recommendations.append(
                {
                    "type": "PATTERN_CONSOLIDATION",
                    "severity": "low",
                    "message": f"{len(pattern_usage)} different patterns in use",
                    "action": "Consolidate to 3 - 5 core architectural patterns",
                }
            )

        return recommendations

    def _aggregate_performance_metrics(self, elements: List[Dict]) -> Dict:
        """Aggregate performance metrics across elements"""
        if not elements:
            return {}

        # Calculate averages
        total_latency = sum(e["metrics"].get("latency_ms", 0) for e in elements)
        total_throughput = sum(e["metrics"].get("throughput_rps", 0) for e in elements)

        return {
            "avg_latency_ms": total_latency / len(elements) if elements else 0,
            "total_throughput_rps": total_throughput,
            "elements_analyzed": len(elements),
        }

    def _aggregate_scalability_metrics(self, elements: List[Dict]) -> Dict:
        """Aggregate scalability metrics"""
        horizontal_count = sum(1 for e in elements if e["metrics"].get("horizontal_scaling"))
        vertical_count = sum(1 for e in elements if e["metrics"].get("vertical_scaling"))

        return {
            "horizontal_scaling_capable": horizontal_count,
            "vertical_scaling_capable": vertical_count,
            "scalability_coverage": (horizontal_count / len(elements) * 100) if elements else 0,
        }

    def _assess_security_posture(self, elements: List[Dict]) -> Dict:
        """Assess overall security posture"""
        if not elements:
            return {"score": 0, "status": "unknown"}

        # Count security controls
        total_controls = sum(len(e["posture"].get("controls", [])) for e in elements)
        avg_controls = total_controls / len(elements)

        # Assess posture
        if avg_controls >= 5:
            status = "strong"
            score = 90
        elif avg_controls >= 3:
            status = "adequate"
            score = 70
        elif avg_controls >= 1:
            status = "weak"
            score = 40
        else:
            status = "insufficient"
            score = 20

        return {
            "score": score,
            "status": status,
            "avg_controls_per_element": avg_controls,
            "elements_analyzed": len(elements),
        }

    def _aggregate_reliability_metrics(self, elements: List[Dict]) -> Dict:
        """Aggregate reliability metrics"""
        if not elements:
            return {}

        total_availability = sum(e["metrics"].get("availability_percent", 0) for e in elements)
        total_mtbf = sum(e["metrics"].get("mtbf_hours", 0) for e in elements)

        return {
            "avg_availability_percent": total_availability / len(elements),
            "avg_mtbf_hours": total_mtbf / len(elements) if elements else 0,
            "elements_analyzed": len(elements),
        }
