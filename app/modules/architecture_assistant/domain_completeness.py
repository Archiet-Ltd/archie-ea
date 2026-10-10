"""Domain Completeness Service — scoring and enforcement for ACM domain coverage."""

import logging

logger = logging.getLogger(__name__)

ACM_DOMAINS = ["UX", "APP", "DATA", "SEC", "DEV", "AI", "COM"]

TIER_THRESHOLDS = {
    "differentiating": {"nfr_coverage": 0.9, "cross_domain_resolution": 0.9, "property_coverage": 0.4},
    "important": {"nfr_coverage": 0.7, "cross_domain_resolution": 0.7, "property_coverage": 0.4},
    "standard": {"nfr_coverage": 0.5, "cross_domain_resolution": 0.5, "property_coverage": 0.4},
}


class DomainCompletenessService:
    """Calculates completeness scores and identifies blockers."""

    def score(self, solution_id):
        """Calculate domain coverage, NFR scores, and progression eligibility."""
        from app.models.solution_domain_spec import SolutionDomainSpec

        specs = SolutionDomainSpec.query.filter_by(solution_id=solution_id).all()
        specs_by_domain = {s.domain_code: s for s in specs}

        covered = 0
        blockers = []
        per_domain = {}

        for code in ACM_DOMAINS:
            spec = specs_by_domain.get(code)
            domain_info = {
                "code": code,
                "status": spec.status if spec else "missing",
                "tier": spec.relevance_tier if spec else "standard",
                "covered": False,
                "nfr_coverage": 0.0,
                "cross_domain_resolution": 0.0,
            }

            if not spec or spec.status == "pending":
                blockers.append({
                    "domain": code,
                    "reason": "%s: not yet confirmed" % code,
                    "type": "domain_not_confirmed",
                })
            elif spec.status == "confirmed":
                covered += 1
                domain_info["covered"] = True

                # Check NFR threshold for differentiating domains
                if spec.relevance_tier == "differentiating":
                    nfr_pct = self._get_nfr_coverage(solution_id, code, spec.relevance_tier)
                    domain_info["nfr_coverage"] = nfr_pct
                    threshold = TIER_THRESHOLDS["differentiating"]["nfr_coverage"]
                    if nfr_pct < threshold:
                        blockers.append({
                            "domain": code,
                            "reason": "%s (Differentiating): NFR coverage %.0f%% (need >= %.0f%%)" % (
                                code, nfr_pct * 100, threshold * 100),
                            "type": "nfr_below_threshold",
                        })

                # Check property threshold for differentiating domains
                if spec.relevance_tier == "differentiating":
                    prop_pct = self._get_property_coverage(solution_id, code, spec.relevance_tier)
                    domain_info["property_coverage"] = prop_pct
                    prop_threshold = TIER_THRESHOLDS["differentiating"]["property_coverage"]
                    if prop_pct < prop_threshold:
                        blockers.append({
                            "domain": code,
                            "reason": "%s (Differentiating): Property coverage %.0f%% (need >= %.0f%%)" % (
                                code, prop_pct * 100, prop_threshold * 100),
                            "type": "property_below_threshold",
                        })

            elif spec.status == "not_applicable":
                if spec.status_justification and spec.status_justification.strip():
                    covered += 1
                    domain_info["covered"] = True
                else:
                    blockers.append({
                        "domain": code,
                        "reason": "%s: marked N/A but no justification provided" % code,
                        "type": "na_without_justification",
                    })

            per_domain[code] = domain_info

        # Check unresolved required cross-domain dependencies
        unresolved = self._get_unresolved_required_deps(solution_id)
        for dep in unresolved:
            blockers.append({
                "domain": dep["target_domain"],
                "reason": "Required dependency unresolved: %s" % dep["name"],
                "type": "cross_domain_unresolved",
            })

        can_proceed = (covered == 7) and len(blockers) == 0

        return {
            "domain_coverage": covered,
            "domain_coverage_total": 7,
            "per_domain": per_domain,
            "can_proceed": can_proceed,
            "blockers": blockers,
        }

    def _get_nfr_coverage(self, solution_id, domain_code, tier):
        """Calculate NFR coverage for a domain. Returns float 0.0-1.0."""
        from app.models.acm_domain_template import AcmDomainTemplate
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

        # Count required NFR templates for this tier
        query = AcmDomainTemplate.query.filter_by(domain_code=domain_code, is_nfr=True)
        if tier == "standard":
            query = query.filter_by(is_core_nfr=True)
        required_count = query.count()
        if required_count == 0:
            return 1.0

        # Count filled NFR proposals (source='nfr', status='accepted' or 'proposed')
        filled = SolutionBlueprintProposal.query.filter_by(
            solution_id=solution_id, acm_domain=domain_code, source="nfr",
        ).filter(
            SolutionBlueprintProposal.status.in_(["proposed", "accepted", "promoted"])
        ).count()

        return min(1.0, filled / required_count)

    def _get_property_coverage(self, solution_id, domain_code, tier):
        """Calculate property coverage for a domain. Delegates to PropertyService."""
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
        from app.modules.architecture_assistant.property_service import PropertyService

        proposals = SolutionBlueprintProposal.query.filter_by(
            solution_id=solution_id, acm_domain=domain_code,
        ).filter(
            SolutionBlueprintProposal.status.in_(["accepted", "promoted"])
        ).all()

        if not proposals:
            return 1.0

        svc = PropertyService()
        total_score = 0.0
        for p in proposals:
            total_score += svc.calculate_element_score(
                p.archimate_type, p.acm_properties or {}, tier
            )
        return total_score / len(proposals)

    def get_missing_properties(self, solution_id, domain_code, tier):
        """Return specific missing properties per element for actionable blockers.

        Returns list of {element_name, element_type, missing: [property_key, ...]}
        """
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
        from app.models.acm_property_template import AcmPropertyTemplate
        from app.modules.architecture_assistant.property_service import is_visible, template_query, tiers_up_to

        proposals = SolutionBlueprintProposal.query.filter_by(
            solution_id=solution_id, acm_domain=domain_code,
        ).filter(
            SolutionBlueprintProposal.status.in_(["accepted", "promoted"])
        ).all()

        result = []
        for p in proposals:
            templates = template_query().filter_by(
                archimate_type=p.archimate_type,
            ).filter(
                AcmPropertyTemplate.required_for_tier.in_(tiers_up_to(tier))
            ).all()

            props = p.acm_properties or {}
            visible = [t for t in templates if is_visible(t, props)]
            missing = []
            for t in visible:
                val = props.get(t.property_key)
                if isinstance(val, dict):
                    val = val.get("value")
                if val in (None, "", "TBD"):
                    missing.append(t.display_name)

            if missing:
                result.append({
                    "element_name": p.name,
                    "element_type": p.archimate_type,
                    "element_id": p.id,
                    "missing": missing,
                })
        return result

    def _get_unresolved_required_deps(self, solution_id):
        """Find required cross-domain deps that haven't been accepted or waived."""
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

        # Cross-domain proposals that are required but not yet resolved
        unresolved = SolutionBlueprintProposal.query.filter_by(
            solution_id=solution_id, source="cross_domain",
        ).filter(
            SolutionBlueprintProposal.status == "proposed",
            SolutionBlueprintProposal.waived == False,
            SolutionBlueprintProposal.cross_domain_rule_id.isnot(None),
        ).all()

        # Check severity of each rule
        result = []
        for p in unresolved:
            from app.models.acm_cross_domain_rule import AcmCrossDomainRule
            rule = AcmCrossDomainRule.query.get(p.cross_domain_rule_id)
            if rule and rule.severity == "required":
                result.append({
                    "target_domain": p.acm_domain,
                    "name": p.name,
                    "rule_id": p.cross_domain_rule_id,
                })
        return result
