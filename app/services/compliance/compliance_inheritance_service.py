"""
Compliance Inheritance & Roll-up Service

Enforces the logic that "Compliance Requirements are Capability Requirements"
and must be inherited by all applications supporting those capabilities.

PERFORMANCE OPTIMIZED:
- Batch calculation for multiple capabilities (1 query vs N queries)
- Redis caching with 1-hour TTL
- Eager loading of relationships to prevent N+1 queries
"""

import logging
from typing import Any, Dict, List, Optional  # dead-code-ok


from app import db
from app.extensions.cache import cache_manager
from app.models.application_portfolio import ApplicationComponent
from app.models.business_capabilities import BusinessCapability
from app.models.unified_application_capability_mapping import (
    UnifiedApplicationCapabilityMapping,
)

logger = logging.getLogger(__name__)

# Credit a control earns towards a compliance score, by implementation status.
_CONTROL_CREDIT = {"implemented": 1.0, "verified": 1.0, "in_progress": 0.5, "planned": 0.0}
_EXCLUDED_STATUSES = ("not_applicable", "waived")


class ComplianceInheritanceService:
    """
    Handles the inheritance of compliance requirements from capabilities to applications.
    
    PERFORMANCE FEATURES:
    - calculate_compliance_scores_batch(): Compute all scores in 1-2 queries
    - Redis caching with 1-hour TTL
    - Cache warming support for pre-computation
    """

    # Cache TTL in seconds (1 hour)
    CACHE_TTL = 3600

    @staticmethod
    def calculate_compliance_scores_batch(
        capability_ids: List[int],
    ) -> Dict[int, Optional[float]]:
        """Compliance score (0.0-100.0) for each capability, or None where it is not assessed.

        Reads ``assess_capabilities``, the one computation every compliance surface uses.
        """
        return {
            cap_id: row["score"]
            for cap_id, row in ComplianceInheritanceService.assess_capabilities(capability_ids).items()
        }

    @staticmethod
    def _calculate_scores_from_db(capability_ids: List[int]) -> Dict[int, Optional[float]]:
        return ComplianceInheritanceService.calculate_compliance_scores_batch(capability_ids)

    @staticmethod
    def assess_applications(
        application_ids: List[int], organization_id: Optional[int] = None
    ) -> Dict[int, Dict[str, Any]]:
        """Compliance of each application from the implementation status of the controls mapped to it.

        A control counts in full when implemented or verified, by half when in progress, and not at all when
        only planned. Controls marked not applicable or waived are left out. An application with no mapped
        control has a score of None: it is not assessed, which is neither 100% nor 0%.
        """
        from app.middleware.tenant_context import current_org_id
        from app.models.application_compliance import ApplicationComplianceControl
        from app.models.compliance_models import ComplianceControl

        result: Dict[int, Dict[str, Any]] = {
            app_id: ComplianceInheritanceService._empty_assessment() for app_id in application_ids
        }
        if not application_ids:
            return result
        org_id = organization_id if organization_id is not None else current_org_id()
        query = (
            db.session.query(ApplicationComplianceControl, ComplianceControl)
            .join(ComplianceControl, ComplianceControl.id == ApplicationComplianceControl.control_id)
            .filter(ApplicationComplianceControl.application_id.in_(application_ids))
        )
        if org_id is not None:
            query = query.filter(ApplicationComplianceControl.organization_id == org_id)
        for mapping, control in query.order_by(ComplianceControl.control_code).all():
            row = result[mapping.application_id]
            status = mapping.implementation_status or "planned"
            if status in _EXCLUDED_STATUSES:
                continue
            credit = _CONTROL_CREDIT.get(status, 0.0)
            row["assessed_controls"] += 1
            row["credit"] += credit
            if mapping.evidence_url:
                row["with_evidence"] += 1
            recorded = mapping.updated_at or mapping.created_at
            if recorded and (row["newest_record"] is None or recorded > row["newest_record"]):
                row["newest_record"] = recorded
            bucket = "implemented" if credit == 1.0 else "partial" if credit else "missing"
            row[bucket] += 1
            if bucket != "implemented":
                row["findings"].append(
                    {
                        "application_id": mapping.application_id,
                        "control_id": control.id,
                        "control_code": control.control_code,
                        "control_title": control.title,
                        "status": status,
                        "missing": bucket == "missing",
                        "mapping_id": mapping.id,
                        "evidence_url": mapping.evidence_url,
                        "recorded_at": mapping.updated_at or mapping.created_at,
                    }
                )
            if mapping.verified_date and (
                row["last_review"] is None or mapping.verified_date > row["last_review"]
            ):
                row["last_review"] = mapping.verified_date
        for row in result.values():
            ComplianceInheritanceService._finish(row)
        return result

    @staticmethod
    def controls_not_in_place(organization_id: Optional[int] = None) -> Dict[str, int]:
        """How many controls mapped to an application are not in place, counted once per application and control.

        "Not in place" means not yet implemented or verified: not started (planned) plus in progress. Controls
        marked not applicable or waived are left out. Compliance Tracking, Capability Health and the compliance
        dashboard all show this count under "controls not in place", read here, so it cannot differ between them.
        A capability's own count (``not_in_place`` in its assessment) counts a control under each capability its
        application supports, so the capability counts can add up to more than this.
        """
        from app.middleware.tenant_context import current_org_id
        from app.models.application_compliance import ApplicationComplianceControl

        org_id = organization_id if organization_id is not None else current_org_id()
        query = db.session.query(ApplicationComplianceControl.implementation_status).filter(
            ApplicationComplianceControl.application_id.isnot(None)
        )
        if org_id is not None:
            query = query.filter(ApplicationComplianceControl.organization_id == org_id)
        not_started = in_progress = 0
        for (status,) in query.all():
            status = status or "planned"
            if status == "planned":
                not_started += 1
            elif status == "in_progress":
                in_progress += 1
        return {"not_started": not_started, "in_progress": in_progress, "total": not_started + in_progress}

    @staticmethod
    def not_in_place_text(counts: Dict[str, int]) -> str:
        """The count in words: "437 controls not in place: 217 not started and 220 in progress"."""
        total = counts["total"]
        return "%d control%s not in place: %d not started and %d in progress" % (
            total, "" if total == 1 else "s", counts["not_started"], counts["in_progress"])

    @staticmethod
    def assess_capabilities(
        capability_ids: List[int], organization_id: Optional[int] = None
    ) -> Dict[int, Dict[str, Any]]:
        """Compliance of each capability: its supporting applications' controls taken together.

        The one function that computes a compliance percentage. Tracking, capability health and the
        compliance dashboard all read it, so the same capability shows the same figure on each.
        """
        from app.middleware.tenant_context import current_org_id
        from app.models.application_capability import ApplicationCapabilityMapping

        result: Dict[int, Dict[str, Any]] = {
            cap_id: ComplianceInheritanceService._empty_assessment() for cap_id in capability_ids
        }
        if not capability_ids:
            return result
        org_id = organization_id if organization_id is not None else current_org_id()
        query = db.session.query(
            ApplicationCapabilityMapping.business_capability_id,
            ApplicationCapabilityMapping.application_component_id,
        ).filter(ApplicationCapabilityMapping.business_capability_id.in_(capability_ids))
        if org_id is not None:
            query = query.filter(ApplicationCapabilityMapping.organization_id == org_id)
        apps_by_capability: Dict[int, set] = {}
        for cap_id, app_id in query.all():
            apps_by_capability.setdefault(cap_id, set()).add(app_id)
        all_apps = sorted({a for apps in apps_by_capability.values() for a in apps})
        by_app = ComplianceInheritanceService.assess_applications(all_apps, org_id)
        for cap_id, apps in apps_by_capability.items():
            row = result[cap_id]
            for app_id in sorted(apps):
                part = by_app[app_id]
                for key in ("assessed_controls", "implemented", "partial", "missing", "credit", "with_evidence"):
                    row[key] += part[key]
                if part["newest_record"] and (
                    row["newest_record"] is None or part["newest_record"] > row["newest_record"]
                ):
                    row["newest_record"] = part["newest_record"]
                row["findings"].extend(part["findings"])
                if part["last_review"] and (
                    row["last_review"] is None or part["last_review"] > row["last_review"]
                ):
                    row["last_review"] = part["last_review"]
            ComplianceInheritanceService._finish(row)
        return result

    @staticmethod
    def figure(assessment: Dict[str, Any]) -> str:
        """The compliance figure as a person reads it: "62.5%", or "Not assessed" when no control is mapped."""
        return ComplianceInheritanceService.format_score(assessment["score"])

    @staticmethod
    def format_score(score: Optional[float]) -> str:
        """One score as a person reads it: "62.5%", "50%", or "Not assessed" for None."""
        return "Not assessed" if score is None else "%g%%" % score

    @staticmethod
    def portfolio_score(assessments) -> Optional[float]:
        """The average of the assessed scores in *assessments*, or None when none is assessed.

        The one organisation-wide figure: every page and API that shows an overall compliance score reads it.
        """
        scores = [a["score"] for a in assessments if a["score"] is not None]
        return round(sum(scores) / len(scores), 1) if scores else None

    @staticmethod
    def status(assessment: Dict[str, Any]) -> str:
        """Compliant from 80% with every control in place, partly compliant from 50% (or from 80% with a control not in place),
        otherwise not compliant; "Not assessed" with no score."""
        score = assessment["score"]
        if score is None:
            return "Not assessed"
        if score >= 80 and assessment.get("not_in_place"):
            return "Partly compliant"
        return "Compliant" if score >= 80 else "Partly compliant" if score >= 50 else "Not compliant"

    @staticmethod
    def _empty_assessment() -> Dict[str, Any]:
        return {
            "score": None,
            "assessed_controls": 0,
            "implemented": 0,
            "partial": 0,
            "missing": 0,
            "not_in_place": 0,
            "credit": 0.0,
            "findings": [],
            "last_review": None,
            "with_evidence": 0,
            "newest_record": None,
        }

    @staticmethod
    def _finish(row: Dict[str, Any]) -> None:
        total = row["assessed_controls"]
        row["not_in_place"] = row["partial"] + row["missing"]
        row["score"] = round(row["credit"] / total * 100, 1) if total else None

    @staticmethod
    def invalidate_compliance_cache(capability_id: Optional[int] = None) -> None:
        """
        Invalidate compliance score cache for a specific capability or all capabilities.
        
        WHEN TO CALL:
        - After adding/removing compliance requirements to/from a capability
        - After changing application-capability mappings
        - After updating application compliance realizations
        - During data imports or bulk updates
        
        Args:
            capability_id: If provided, invalidate only this capability. Otherwise clear all.
        
        Example:
            # After adding a new compliance requirement to a capability
            ComplianceInheritanceService.invalidate_compliance_cache(capability_id=123)
            
            # After bulk data import
            ComplianceInheritanceService.invalidate_compliance_cache()  # Clear all
        """
        if capability_id:
            cache_key = f"compliance_score:cap:{capability_id}"
            cache_manager.delete(cache_key)
            logger.info(f"Invalidated compliance cache for capability {capability_id}")
        else:
            # Delete all compliance score cache keys (pattern-based deletion)
            # Note: This requires Redis SCAN which may not be available in all cache backends
            logger.warning("Full compliance cache invalidation not implemented for all backends")
            # Alternative: Store a version number and increment it on full invalidation

    @staticmethod
    def invalidate_compliance_cache_batch(capability_ids: List[int]) -> None:
        """
        Invalidate compliance score cache for multiple capabilities efficiently.
        
        Args:
            capability_ids: List of capability IDs to invalidate
            
        Example:
            # After bulk update affecting multiple capabilities
            ComplianceInheritanceService.invalidate_compliance_cache_batch([123, 456, 789])
        """
        for cap_id in capability_ids:
            cache_key = f"compliance_score:cap:{cap_id}"
            cache_manager.delete(cache_key)
        logger.info(f"Invalidated compliance cache for {len(capability_ids)} capabilities")

    @staticmethod
    def get_inherited_requirements(application_id: int) -> List[Dict[str, Any]]:
        """
        Get all compliance requirements inherited by an application from its capabilities.
        """
        app = ApplicationComponent.query.get(application_id)
        if not app:
            return []

        # Find all capabilities the app supports
        capabilities = (
            BusinessCapability.query.join(
                UnifiedApplicationCapabilityMapping,
                UnifiedApplicationCapabilityMapping.unified_capability_id == BusinessCapability.id,
            )
            .filter(UnifiedApplicationCapabilityMapping.application_component_id == application_id)
            .all()
        )

        inherited = []
        seen_req_ids = set()

        for cap in capabilities:
            for req in cap.compliance_requirements:
                if req.id not in seen_req_ids:
                    inherited.append(
                        {
                            "requirement": req,
                            "source_capability": cap,
                            "is_mandatory": True,  # Inherited are typically mandatory
                        }
                    )
                    seen_req_ids.add(req.id)

        return inherited

    @staticmethod
    def validate_application_compliance(application_id: int) -> Dict[str, Any]:
        """
        Validate that an application satisfies all requirements inherited from its capabilities.
        """
        inherited = ComplianceInheritanceService.get_inherited_requirements(application_id)
        app = ApplicationComponent.query.get(application_id)

        results = {
            "application_id": application_id,
            "application_name": app.name if app else "Unknown",
            "total_requirements": len(inherited),
            "satisfied_count": 0,
            "missing_count": 0,
            "gaps": [],
        }

        # For each inherited requirement, check if the app has a realization link
        satisfying_req_ids = {req.id for req in app.compliance_realizations}

        for item in inherited:
            req = item["requirement"]
            cap = item["source_capability"]

            is_satisfied = req.id in satisfying_req_ids

            if is_satisfied:
                results["satisfied_count"] += 1
            else:
                results["missing_count"] += 1
                results["gaps"].append(
                    {
                        "requirement_id": req.id,
                        "requirement_title": req.title,
                        "source_capability": cap.name,
                        "severity": req.priority or "medium",
                    }
                )

        return results

    @staticmethod
    def calculate_capability_compliance_score(capability_id: int) -> Optional[float]:
        """Compliance score of one capability, or None when none of its controls is assessed."""
        return ComplianceInheritanceService.assess_capabilities([capability_id])[capability_id]["score"]

    @staticmethod
    def get_compliance_impact_on_maturity(capability_id: int) -> int:
        """
        Calculates a "Maturity Penalty" based on compliance gaps.
        If compliance is < 50%, maturity is capped at 1.
        If compliance is < 80%, maturity is capped at 2.
        """
        score = ComplianceInheritanceService.calculate_capability_compliance_score(capability_id)

        if score is None:
            return 5  # Not assessed: nothing to penalise.
        if score < 50:
            return 1
        elif score < 80:
            return 2

        return 5  # No penalty

    @staticmethod
    def validate_capability_compliance(capability_id: int) -> Dict[str, Any]:
        """
        Validate all applications supporting a capability against its requirements.
        """
        cap = BusinessCapability.query.get(capability_id)
        if not cap:
            return {}

        apps = (
            ApplicationComponent.query.join(UnifiedApplicationCapabilityMapping)
            .filter(UnifiedApplicationCapabilityMapping.unified_capability_id == capability_id)
            .all()
        )

        app_results = []
        overall_satisfied = True

        for app in apps:
            app_val = ComplianceInheritanceService.validate_application_compliance(app.id)
            app_results.append(app_val)
            if app_val["missing_count"] > 0:
                overall_satisfied = False

        return {
            "capability_id": capability_id,
            "capability_name": cap.name,
            "is_compliant": overall_satisfied,
            "application_validations": app_results,
            "compliance_score": ComplianceInheritanceService.calculate_capability_compliance_score(
                capability_id
            ),
        }
