"""
DEPRECATED: Import from app.modules.capabilities.services instead.
-> app.modules.capabilities.services.analysis_service

Capability Health Service

Provides health-centric analysis for business capabilities, calculating readiness scores
and identifying hot-spots for architectural investment.

PERFORMANCE OPTIMIZED VERSION:
- Uses eager loading to prevent N + 1 queries (reduced from 339 queries to ~10)
- Pre-loads parent relationships for domain traversal
- Batches application queries
- Caches compliance scores
- Result-level TTL cache (60s) to avoid redundant recomputation
"""

import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from app import db
from app.models import BusinessCapability
from app.models.application_portfolio import ApplicationCapabilityMapping, ApplicationComponent
from app.models.solution_architect_models import SolutionRequirement
from app.models.solution_models import SolutionCapabilityMapping
from app.models.strategic import CapabilityHealthOverride
from app.services.compliance.compliance_inheritance_service import ComplianceInheritanceService
from sqlalchemy import func

logger = logging.getLogger(__name__)

# Module-level result cache, keyed BY ORGANISATION: {org_id: (timestamp, result)}
#
# It used to be a single unkeyed tuple shared by the whole process, which made it
# a cross-tenant data leak rather than a cache. The result dict carries
# health_by_capability -- every capability's name, id, domain and score -- so for
# 60 seconds after any tenant loaded /strategic/capability-health or
# /api/capability-health, EVERY other tenant was served that tenant's capability
# names and scores. No attacker action was required; one customer's portfolio
# simply appeared on another customer's dashboard.
#
# Found 30 Aug 2026 by a test that was written for something else and could not
# get a clean result between two organisations. Demonstrated: seed a capability
# in org A, read the metrics as A, switch to org B, read again -- B is handed
# A's capability by name.
#
# The tenant predicate itself was never the problem: _compute_health_metrics
# queries TenantMixin models and is correctly scoped. The cache sat in front of
# it and threw the scoping away.
_health_metrics_cache: Dict[Any, Tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 60
# Bound the map so a long-lived process serving many tenants cannot grow it
# without limit; entries are cheap to recompute.
_CACHE_MAX_TENANTS = 256


class CapabilityHealthService:

    def get_capability_health_metrics(self) -> Dict[str, Any]:
        """
        Returns a comprehensive health analysis of the capability portfolio.

        PERFORMANCE OPTIMIZED:
        - Uses eager loading to prevent N + 1 queries
        - Pre-loads parent relationships for domain traversal
        - Batches application queries
        - 60-second result cache to avoid redundant recomputation
        """
        from flask import g, has_app_context

        # Outside a request there is no tenant, and a cache entry with no owner is
        # exactly what caused the leak -- so do not cache at all there (CLI,
        # scheduler, importers). Correctness first; those paths run rarely.
        tenant = getattr(g, "current_org_id", None) if has_app_context() else None

        if tenant is not None:
            entry = _health_metrics_cache.get(tenant)
            if entry is not None:
                cached_time, cached_result = entry
                if (time.time() - cached_time) < _CACHE_TTL_SECONDS:
                    logger.debug(
                        "Capability health metrics served from cache for org %s (age=%.1fs)",
                        tenant, time.time() - cached_time,
                    )
                    return cached_result

        start = time.time()
        result = self._compute_health_metrics()
        elapsed = time.time() - start
        logger.info("Capability health metrics computed in %.2fs", elapsed)

        if tenant is not None:
            if len(_health_metrics_cache) >= _CACHE_MAX_TENANTS:
                # Drop the oldest rather than let the map grow unbounded.
                oldest = min(_health_metrics_cache, key=lambda k: _health_metrics_cache[k][0])
                _health_metrics_cache.pop(oldest, None)
            _health_metrics_cache[tenant] = (time.time(), result)
        return result

    def _compute_health_metrics(self) -> Dict[str, Any]:
        """
        Internal computation of health metrics. Called by get_capability_health_metrics()
        when cache is stale or empty.
        """
        # Load all capabilities
        capabilities = BusinessCapability.query.all()

        # Build parent lookup dict to prevent N + 1 when traversing hierarchy
        all_caps_dict = {cap.id: cap for cap in capabilities}

        # Pre-load all application mappings in one query
        capability_ids = [cap.id for cap in capabilities]
        app_mappings_query = (
            # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
            db.session.query(ApplicationCapabilityMapping, ApplicationComponent)
            .join(
                ApplicationComponent,
                ApplicationCapabilityMapping.application_component_id == ApplicationComponent.id,
            )
            .filter(ApplicationCapabilityMapping.business_capability_id.in_(capability_ids))
            .all()
        )

        # Build lookup dict for O(1) access
        apps_by_capability = {}
        for mapping, app_component in app_mappings_query:
            cap_id = mapping.business_capability_id
            if cap_id not in apps_by_capability:
                apps_by_capability[cap_id] = []
            app_component._capability_coverage = mapping.coverage_percentage or 0
            app_component._capability_debt = mapping.technical_debt_score or 0
            apps_by_capability[cap_id].append(app_component)

        # Pre-calculate all compliance scores in batch (OPTIMIZED: 1-2 queries vs N queries)
        compliance_scores = ComplianceInheritanceService.calculate_compliance_scores_batch(
            capability_ids
        )

        delivery_signals = defaultdict(
            lambda: {
                "planned_mapping_count": 0,
                "planned_solution_ids": set(),
                "requirement_count": 0,
            }
        )

        solution_mapping_rows = (
            db.session.query(
                SolutionCapabilityMapping.capability_id,
                SolutionCapabilityMapping.solution_id,
                SolutionCapabilityMapping.problem_id,
            )
            .filter(SolutionCapabilityMapping.capability_id.in_(capability_ids))
            .all()
        )
        for capability_id, solution_id, problem_id in solution_mapping_rows:
            signal = delivery_signals[capability_id]
            if solution_id or problem_id:
                signal["planned_mapping_count"] += 1
            if solution_id:
                signal["planned_solution_ids"].add(solution_id)

        requirement_rows = (
            db.session.query(
                SolutionRequirement.capability_id,
                func.count(SolutionRequirement.id),
            )
            .filter(
                SolutionRequirement.capability_id.in_(capability_ids),
                SolutionRequirement.deleted_at.is_(None),
            )
            .group_by(SolutionRequirement.capability_id)
            .all()
        )
        for capability_id, requirement_count in requirement_rows:
            delivery_signals[capability_id]["requirement_count"] = int(requirement_count or 0)

        # Pre-load active overrides in batch
        active_overrides = (
            db.session.query(CapabilityHealthOverride)
            .filter(
                CapabilityHealthOverride.capability_id.in_(capability_ids),
                CapabilityHealthOverride.active == True,  # noqa: E712
            )
            .all()
        )
        overrides_by_capability = {o.capability_id: o for o in active_overrides}

        health_scores = []
        domain_health = {}

        total_health_sum = 0
        # Averages divide by what was MEASURED, not by how many rows exist.
        assessed_count = 0
        critical_count = 0
        at_risk_count = 0
        # Inline gap summary counters (avoids calling analyze_capability_gaps which
        # re-queries all capabilities with N+1 per-capability application lookups)
        gaps_identified = 0
        critical_gaps = 0
        gaps_with_decisions = 0

        for cap in capabilities:
            # Use pre-loaded data to avoid N + 1 queries
            apps_for_cap = apps_by_capability.get(cap.id, [])
            compliance_score_val = compliance_scores.get(cap.id, 100.0)
            delivery_signal = delivery_signals.get(cap.id) or {
                "planned_mapping_count": 0,
                "planned_solution_ids": set(),
                "requirement_count": 0,
            }
            planned_solution_count = len(delivery_signal["planned_solution_ids"])
            planned_mapping_count = int(delivery_signal["planned_mapping_count"] or 0)
            requirement_count = int(delivery_signal["requirement_count"] or 0)
            has_delivery_plan = bool(planned_solution_count or planned_mapping_count or requirement_count)
            score = self._calculate_health_score_with_apps(
                cap,
                apps_for_cap,
                compliance_score_val,
                planned_solution_count=planned_solution_count,
                planned_mapping_count=planned_mapping_count,
                requirement_count=requirement_count,
            )
            if not self._is_assessable(
                cap, apps_for_cap, planned_solution_count,
                planned_mapping_count, requirement_count,
            ):
                # Nothing to measure. Withhold the number rather than publish the
                # formula's floor as if it were an assessment.
                score = None
            maturity_gap = self._effective_maturity_gap(cap)

            # Compute inline gap counts using pre-loaded data
            if not apps_for_cap:
                gaps_identified += 1
                if has_delivery_plan:
                    gaps_with_decisions += 1
                if cap.strategic_importance == "critical":
                    critical_gaps += 1
            else:
                max_coverage = max((getattr(a, "_capability_coverage", 0) or 0 for a in apps_for_cap), default=0)
                if max_coverage < 80:
                    gaps_identified += 1
                    if has_delivery_plan:
                        gaps_with_decisions += 1
            if maturity_gap is not None and maturity_gap > 0:
                gaps_identified += 1
                if has_delivery_plan:
                    gaps_with_decisions += 1
                if maturity_gap >= 2 and cap.strategic_importance == "critical":
                    critical_gaps += 1

            # Check for active override
            override = overrides_by_capability.get(cap.id)
            original_score = score
            if override and not override.is_expired():
                score = int(override.override_score)

            health_item = {
                "id": cap.id,
                "name": cap.name,
                "domain": self._get_level_0_domain_optimized(cap, all_caps_dict),
                "score": score,
                "status": self._get_status_from_score(score),
                "strategic_importance": cap.strategic_importance,
                "maturity_gap": maturity_gap,
                "compliance_score": compliance_score_val,
                "application_count": len(apps_for_cap),
                "planned_solution_count": planned_solution_count,
                "planned_mapping_count": planned_mapping_count,
                "requirement_count": requirement_count,
                "has_delivery_plan": has_delivery_plan,
            }

            # Add override metadata if present
            if override and not override.is_expired():
                health_item["is_overridden"] = True
                health_item["original_score"] = original_score
                health_item["override_justification"] = override.justification
                health_item["override_reason"] = override.override_reason
                health_item["override_by"] = override.created_by.name if override.created_by else "Unknown"
            else:
                health_item["is_overridden"] = False

            health_scores.append(health_item)

            # Aggregate by domain -- MEASURED capabilities only. Averaging an
            # unmeasured 60 into a domain drags every domain towards 60 and
            # manufactures the uniformity the audit reported.
            if score is not None:
                d_name = health_item["domain"]
                if d_name not in domain_health:
                    domain_health[d_name] = {"sum": 0, "count": 0}
                domain_health[d_name]["sum"] += score
                domain_health[d_name]["count"] += 1

                total_health_sum += score
                assessed_count += 1
                if score < 60:
                    at_risk_count += 1
            if cap.strategic_importance == "critical":
                critical_count += 1

        # Finalize domain scores
        formatted_domains = []
        for d_name, data in domain_health.items():
            formatted_domains.append(
                {
                    "name": d_name,
                    "score": round(data["sum"] / data["count"]) if data["count"] > 0 else 0,
                    "count": data["count"],
                }
            )

        # None, not 0: "no capability has been assessed" and "every capability
        # scored zero" are different facts and must not render the same.
        avg_health = round(total_health_sum / assessed_count) if assessed_count else None

        # Sort health scores for "At Risk" list. An unassessed capability is not
        # at risk; it is unknown, which is a different queue.
        at_risk_list = [
            h for h in health_scores if h["score"] is not None and h["score"] < 70
        ]
        at_risk_list.sort(key=lambda x: (x["strategic_importance"] != "critical", x["score"]))

        return {
            "average_health": avg_health,
            # So a caller can say "60% across 12 of 191 assessed" rather than
            # implying the whole portfolio was measured.
            "assessed_capabilities": assessed_count,
            "unassessed_capabilities": len(capabilities) - assessed_count,
            "total_capabilities": len(capabilities),
            "critical_capabilities": critical_count,
            "at_risk_capabilities": at_risk_count,
            "health_by_capability": sorted(health_scores, key=lambda x: (x["score"] is None, x["score"] or 0)),
            "health_by_domain": sorted(formatted_domains, key=lambda x: x["score"]),
            "at_risk_list": at_risk_list[:10],  # Top 10 risks
            "gaps_summary": {
                "total_capabilities": len(capabilities),
                "gaps_identified": gaps_identified,
                "critical_gaps": critical_gaps,
                "gaps_with_decisions": gaps_with_decisions,
                "gaps_without_decisions": max(gaps_identified - gaps_with_decisions, 0),
                "average_compliance_score": round(
                    sum(compliance_scores.values()) / len(capability_ids), 1
                ) if capability_ids else 0.0,
            },
        }

    def _calculate_health_score_with_apps(
        self,
        capability: BusinessCapability,
        apps: List[ApplicationComponent],
        compliance_score: float,
        planned_solution_count: int = 0,
        planned_mapping_count: int = 0,
        requirement_count: int = 0,
    ) -> int:
        """
        Calculates health score (0 - 100) based on multiple risk vectors.

        PERFORMANCE OPTIMIZED: Accepts pre-loaded apps and compliance score
        to avoid N + 1 queries.

        Args:
            capability: The capability to score
            apps: Pre-loaded list of applications supporting this capability
            compliance_score: Pre-calculated compliance score
            planned_solution_count: Distinct linked solutions addressing the capability
            planned_mapping_count: Total solution capability mappings for the capability
            requirement_count: Linked requirements for the capability

        Returns:
            Health score from 0 - 100
        """
        score = 100

        # 1. Maturity Gap Impact (Max -45 if gap is 3)
        gap = self._effective_maturity_gap(capability)
        if gap is not None:
            score -= gap * 15

        # 2. Application Coverage Impact (using pre-loaded apps)
        if not apps:
            score -= 20 if (planned_solution_count or planned_mapping_count or requirement_count) else 40
        else:
            max_coverage = max((getattr(a, "_capability_coverage", 0) or 0) for a in apps)
            if max_coverage < 80:
                score -= (80 - max_coverage) * 0.5

        # 2b. Planned remediation signal — recognize capabilities with active delivery intent
        if planned_solution_count:
            score += min(12, planned_solution_count * 4)
        elif planned_mapping_count:
            score += min(8, planned_mapping_count * 2)

        if requirement_count:
            score += min(8, requirement_count * 2)

        # 3. Technical Debt Impact (uses mapping.technical_debt_score 0-100)
        high_debt_apps = [a for a in apps if (getattr(a, "_capability_debt", 0) or 0) > 70]
        if high_debt_apps:
            worst_debt = max(getattr(a, "_capability_debt", 0) for a in high_debt_apps)
            score -= (worst_debt - 70) * 0.3

        # 4. Compliance Impact (using pre-calculated score)
        if compliance_score < 100:
            score -= (100 - compliance_score) * 0.2

        return max(0, min(100, int(score)))

    def _get_level_0_domain_optimized(
        self, capability: BusinessCapability, all_caps_dict: Dict[int, BusinessCapability]
    ) -> str:
        """
        Get the Level 0 (root) capability name which represents the domain.

        PERFORMANCE OPTIMIZED: Uses pre-loaded capability dictionary to avoid queries.

        Args:
            capability: The capability to find the domain for
            all_caps_dict: Dictionary mapping capability IDs to capability objects

        Returns:
            Name of the Level 0 capability (domain) or 'Unknown Domain'
        """
        # If this is already Level 0, it IS the domain
        if capability.level == 0 or not capability.parent_capability_id:
            return capability.name

        # Walk up using pre-loaded dict (no DB queries)
        current = capability
        max_depth = 10  # Safety limit to prevent infinite loops
        depth = 0

        while current.parent_capability_id and depth < max_depth:
            parent = all_caps_dict.get(current.parent_capability_id)
            if not parent:
                break
            if parent.level == 0 or not parent.parent_capability_id:
                return parent.name
            current = parent
            depth += 1

        # Fallback: return the root we found or 'Unknown Domain'
        return current.name if current.id != capability.id else "Unknown Domain"

    @staticmethod
    def _is_assessable(
        capability: BusinessCapability,
        apps,
        planned_solution_count: int,
        planned_mapping_count: int,
        requirement_count: int,
    ) -> bool:
        """Is there ANY real input behind a score for this capability?

        The formula starts at 100 and subtracts. A capability with no maturity
        assessment (gap treated as 0, so no penalty) and no applications
        (-40) and nothing planned lands on exactly 60 -- every time, for every
        such capability. The 30 Aug 2026 QA audit saw "every domain ... shows an
        identical 60% score, aligning with 0/191 capabilities having any
        recorded maturity assessment" and reasonably read it as a hardcoded
        fallback. It is not hardcoded; it is what the arithmetic produces when
        every input is missing, which is worse: a number with no measurement
        behind it, rendered identically to a measured one.

        CLAUDE.md: "A 0 that means 'not computed' is indistinguishable from a
        measured zero; use None -> em dash." The same applies to a 60.
        """
        if capability.maturity_gap is not None:
            return True
        if capability.current_maturity_level is not None:
            return True
        if capability.target_maturity_level is not None:
            return True
        if apps:
            return True
        return bool(planned_solution_count or planned_mapping_count or requirement_count)

    @staticmethod
    def _effective_maturity_gap(capability: BusinessCapability) -> Optional[int]:
        """Calculate maturity gap even when stored field is null.

        Returns None when either current or target maturity is unrecorded,
        so callers can distinguish "no gap" from "gap unknown".
        """
        if capability.maturity_gap is not None:
            return capability.maturity_gap
        if capability.target_maturity_level is None or capability.current_maturity_level is None:
            return None
        return max(capability.target_maturity_level - capability.current_maturity_level, 0)

    def _get_status_from_score(self, score) -> str:
        """Convert numeric score to status label; None means nothing was measured."""
        if score is None:
            return "Not assessed"
        if score >= 80:
            return "Healthy"
        if score >= 60:
            return "Warning"
        return "Critical"

    def assess_capability_health(self, capability: BusinessCapability) -> Dict[str, Any]:
        """
        Assess health for a single capability.

        Returns a dict with health score, maturity_recorded flag, and details.
        """
        from app.models.application_portfolio import ApplicationCapabilityMapping, ApplicationComponent
        from app.models.solution_architect_models import SolutionRequirement
        from app.models.solution_models import SolutionCapabilityMapping
        from app.services.compliance.compliance_inheritance_service import ComplianceInheritanceService

        # Load applications for this capability
        app_mappings = (
            ApplicationCapabilityMapping.query.filter_by(business_capability_id=capability.id)
            .all()
        )  # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model)
        app_ids = [m.application_component_id for m in app_mappings]
        apps = []
        if app_ids:
            apps = ApplicationComponent.query.filter(ApplicationComponent.id.in_(app_ids)).all()  # tenant-scoping-ok: FK id already org-scoped
            for app in apps:
                mapping = next((m for m in app_mappings if m.application_component_id == app.id), None)
                if mapping:
                    app._capability_coverage = mapping.coverage_percentage or 0
                    app._capability_debt = mapping.technical_debt_score or 0

        # Load compliance score
        compliance_scores = ComplianceInheritanceService.calculate_compliance_scores_batch([capability.id])
        compliance_score = compliance_scores.get(capability.id, 100.0)

        # Load delivery signals
        solution_mappings = (
            SolutionCapabilityMapping.query.filter_by(capability_id=capability.id)
            .all()
        )  # tenant-scoping-ok: FK id already org-scoped
        planned_solution_ids = set()
        planned_mapping_count = 0
        for sm in solution_mappings:
            if sm.solution_id or sm.problem_id:
                planned_mapping_count += 1
            if sm.solution_id:
                planned_solution_ids.add(sm.solution_id)
        planned_solution_count = len(planned_solution_ids)

        requirement_count = (
            SolutionRequirement.query.filter_by(capability_id=capability.id)
            .filter(SolutionRequirement.deleted_at.is_(None))
            .count()
        )  # tenant-scoping-ok: FK id already org-scoped

        # Determine if maturity is recorded
        maturity_recorded = (
            capability.maturity_gap is not None
            or capability.current_maturity_level is not None
            or capability.target_maturity_level is not None
        )

        # Calculate health score
        score = self._calculate_health_score_with_apps(
            capability,
            apps,
            compliance_score,
            planned_solution_count=planned_solution_count,
            planned_mapping_count=planned_mapping_count,
            requirement_count=requirement_count,
        )

        # Check if assessable (has any real input)
        is_assessable = self._is_assessable(
            capability, apps, planned_solution_count, planned_mapping_count, requirement_count
        )
        if not is_assessable:
            score = None

        return {
            "capability_id": capability.id,
            "capability_name": capability.name,
            "score": score,
            "status": self._get_status_from_score(score),
            "maturity_recorded": maturity_recorded,
            "maturity_gap": self._effective_maturity_gap(capability),
            "compliance_score": compliance_score,
            "application_count": len(apps),
            "planned_solution_count": planned_solution_count,
            "planned_mapping_count": planned_mapping_count,
            "requirement_count": requirement_count,
        }
