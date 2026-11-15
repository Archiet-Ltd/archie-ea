"""
Autonomous Rationalization Proposal Service

Generates predictive TIME (Tolerate/Invest/Migrate/Eliminate) proposals by
clustering applications into actionable consolidation candidates. This is
proactive — it surfaces proposals from data, not just post-hoc summaries.

Design:
- Reads ApplicationRationalizationScore (existing TIME decisions)
- Reads ApplicationComponent (lifecycle, deployment, data_source)
- Clusters by: duplicate capability coverage, same lifecycle_status, same vendor
- Produces evidence-backed proposals with business rationale and remediation steps
"""

import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class RationalizationProposal:
    proposal_id: str
    action: str  # ELIMINATE | MIGRATE | INVEST | TOLERATE | CONSOLIDATE
    priority: str  # HIGH | MEDIUM | LOW
    title: str
    rationale: str
    affected_apps: List[dict] = field(default_factory=list)
    estimated_impact: str = ""
    recommended_next_step: str = ""
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "action": self.action,
            "priority": self.priority,
            "title": self.title,
            "rationale": self.rationale,
            "affected_apps": self.affected_apps,
            "estimated_impact": self.estimated_impact,
            "recommended_next_step": self.recommended_next_step,
            "evidence": self.evidence,
        }


class RationalizationProposalService:

    @classmethod
    def generate_proposals(cls, limit: int = 10) -> dict:
        """
        Generate actionable rationalization proposals from portfolio data.

        Returns: {"success": True, "proposals": [...], "total": N, "methodology": "TIME"}
        """
        try:
            proposals = []
            proposals.extend(cls._find_eliminate_candidates())
            proposals.extend(cls._find_consolidation_candidates())
            proposals.extend(cls._find_migrate_candidates())
            proposals.extend(cls._find_invest_candidates())

            # Sort by priority
            priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
            proposals.sort(key=lambda p: priority_order.get(p.priority, 99))

            return {
                "success": True,
                "proposals": [p.to_dict() for p in proposals[:limit]],
                "total": len(proposals),
                "methodology": "TIME (Tolerate/Invest/Migrate/Eliminate)",
                "message": (
                    f"Generated {len(proposals)} rationalization proposal(s). "
                    f"Top action: {proposals[0].action if proposals else 'none'} — "
                    f"{proposals[0].title if proposals else 'portfolio appears optimised'}."
                ),
            }
        except Exception as e:
            logger.exception("RationalizationProposalService.generate_proposals failed")
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    # Proposal generators                                                  #
    # ------------------------------------------------------------------ #

    @classmethod
    def _find_eliminate_candidates(cls) -> List[RationalizationProposal]:
        """Find apps already scored ELIMINATE with no active programme membership."""
        proposals = []
        try:
            from app.models.application_rationalization import ApplicationRationalizationScore
            from app.models.application_portfolio import ApplicationComponent
            from app import db

            eliminate_ids = db.session.query(
                ApplicationRationalizationScore.application_id
            ).filter(
                ApplicationRationalizationScore.rationalization_action == "ELIMINATE"
            ).all()
            eliminate_ids = [r[0] for r in eliminate_ids]

            if not eliminate_ids:
                return []

            apps = ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(eliminate_ids)
            ).limit(20).all()

            if not apps:
                return []

            proposals.append(RationalizationProposal(
                proposal_id="RP-ELIM-001",
                action="ELIMINATE",
                priority="HIGH",
                title=f"Retire {len(apps)} ELIMINATE-scored application(s)",
                rationale=(
                    f"{len(apps)} applications are scored ELIMINATE in the TIME framework but "
                    "have no retirement programme active. Each month of delay incurs support cost and "
                    "technical debt compounding."
                ),
                affected_apps=[{"id": a.id, "name": a.name, "status": a.lifecycle_status or "unknown"}
                               for a in apps[:10]],
                estimated_impact=f"Reduce estate by {len(apps)} applications; free integration maintenance budget.",
                recommended_next_step=(
                    "Create a retirement programme at /solutions/programmes. "
                    "Link these applications. Set target decommission date. "
                    "ARB approval required if any app has active integrations."
                ),
                evidence=[f"{len(apps)} apps with rationalization_action=ELIMINATE in DB"],
            ))
        except Exception as e:
            logger.debug("_find_eliminate_candidates failed: %s", e)
        return proposals

    @classmethod
    def _find_consolidation_candidates(cls) -> List[RationalizationProposal]:
        """Find duplicate-function apps via shared capability mappings."""
        proposals = []
        try:
            from app.models.application_capability import ApplicationCapabilityMapping
            from app.models.business_capabilities import BusinessCapability
            from app import db
            from sqlalchemy import func

            # Capabilities covered by 3+ applications
            # tenant-scoping-ok: scoped via the TenantMixin FK parent
            # BusinessCapability, not ACM.organization_id -- that column is
            # NULL on every row in production, so a predicate on it would
            # match zero rows and always report no consolidation candidates.
            # Joining BusinessCapability lets do_orm_execute scope the join
            # automatically. See e622d36 / rationalization_scoring_service.py.
            over_covered = db.session.query(
                ApplicationCapabilityMapping.business_capability_id,
                # tenant-scoping-ok: scoped via TenantMixin FK parent BusinessCapability, ACM.organization_id is NULL in prod (see e622d36).
                func.count(ApplicationCapabilityMapping.application_component_id).label("app_count")
            ).join(
                BusinessCapability,
                ApplicationCapabilityMapping.business_capability_id == BusinessCapability.id,
            ).group_by(
                ApplicationCapabilityMapping.business_capability_id
            ).having(
                # tenant-scoping-ok: scoped via TenantMixin FK parent BusinessCapability, ACM.organization_id is NULL in prod (see e622d36).
                func.count(ApplicationCapabilityMapping.application_component_id) >= 3
            ).limit(5).all()

            if not over_covered:
                return []

            total_over = sum(r.app_count for r in over_covered)
            cap_ids = [r.business_capability_id for r in over_covered]

            proposals.append(RationalizationProposal(
                proposal_id="RP-CONS-001",
                action="CONSOLIDATE",
                priority="MEDIUM",
                title=f"{len(over_covered)} business capabilities covered by 3+ applications each",
                rationale=(
                    f"{total_over} application-capability mappings across {len(over_covered)} capabilities "
                    "where 3+ systems serve the same function. Consolidation reduces licensing, integration "
                    "surface, and data duplication."
                ),
                affected_apps=[{"capability_id": r.business_capability_id, "app_count": r.app_count}
                               for r in over_covered],
                estimated_impact=f"Potential to consolidate ~{total_over - len(over_covered)} redundant mappings.",
                recommended_next_step=(
                    "Review at /capability-map. For each over-covered capability, identify the 'invest' "
                    "application to retain and tag others for migration."
                ),
                evidence=[f"Capability IDs with 3+ app coverage: {cap_ids}"],
            ))
        except Exception as e:
            logger.debug("_find_consolidation_candidates failed: %s", e)
        return proposals

    @classmethod
    def _find_migrate_candidates(cls) -> List[RationalizationProposal]:
        """Find on-premise apps where cloud alternatives are being invested in same domain."""
        proposals = []
        try:
            from app.models.application_portfolio import ApplicationComponent
            from app import db
            from sqlalchemy import func
            from app.services.application_kind import is_application_expression

            on_prem = db.session.query(func.count(ApplicationComponent.id)).filter(
                is_application_expression(ApplicationComponent), ApplicationComponent.deployment_model == "on-premise"
            ).scalar() or 0

            saas = db.session.query(func.count(ApplicationComponent.id)).filter(
                is_application_expression(ApplicationComponent), ApplicationComponent.deployment_model.in_(["saas", "cloud", "hybrid"])
            ).scalar() or 0

            if on_prem > 0 and saas > 0 and on_prem > saas:
                proposals.append(RationalizationProposal(
                    proposal_id="RP-MIG-001",
                    action="MIGRATE",
                    priority="MEDIUM",
                    title=f"Cloud migration backlog: {on_prem} on-premise vs {saas} cloud/SaaS apps",
                    rationale=(
                        f"{on_prem} on-premise applications vs {saas} cloud/SaaS. "
                        "On-premise apps incur higher infrastructure cost and block SAP RISE clean-core compliance. "
                        "The imbalance suggests a cloud migration programme is needed."
                    ),
                    affected_apps=[{"on_premise_count": on_prem, "cloud_count": saas}],
                    estimated_impact=f"Migrating {on_prem} on-premise apps reduces hosting cost and unblocks RISE adoption.",
                    recommended_next_step=(
                        "Filter /applications by deployment_model=on-premise. "
                        "Cross-reference with SAP clean-core scan. "
                        "Prioritise SAP-adjacent on-premise apps for RISE migration."
                    ),
                    evidence=[f"DB count: on-premise={on_prem}, cloud/saas={saas}"],
                ))
        except Exception as e:
            logger.debug("_find_migrate_candidates failed: %s", e)
        return proposals

    @classmethod
    def _find_invest_candidates(cls) -> List[RationalizationProposal]:
        """Find INVEST-scored apps with no active programme investment."""
        proposals = []
        try:
            from app.models.application_rationalization import ApplicationRationalizationScore
            from app import db

            invest_count = db.session.query(
                ApplicationRationalizationScore
            ).filter(
                ApplicationRationalizationScore.rationalization_action == "INVEST"
            ).count()

            if invest_count >= 5:
                proposals.append(RationalizationProposal(
                    proposal_id="RP-INV-001",
                    action="INVEST",
                    priority="LOW",
                    title=f"{invest_count} INVEST-scored applications need programme sponsorship",
                    rationale=(
                        f"{invest_count} applications are scored INVEST (high value, needs improvement) "
                        "but may lack an active transformation programme. Without explicit investment, "
                        "these drift toward TOLERATE and eventually ELIMINATE."
                    ),
                    affected_apps=[{"invest_scored_count": invest_count}],
                    estimated_impact="Unlocks strategic application value currently plateauing.",
                    recommended_next_step=(
                        "Review INVEST-scored apps at /applications/rationalization. "
                        "For top-priority apps, create or link to a transformation programme."
                    ),
                    evidence=[f"DB count: rationalization_action=INVEST is {invest_count}"],
                ))
        except Exception as e:
            logger.debug("_find_invest_candidates failed: %s", e)
        return proposals
