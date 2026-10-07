"""Step 6 Validation Engine — structural validation, compliance, COBIT/ITIL, traceability, ARB submission.

Runs a comprehensive validation pass on the journey's architecture and provides:
- Structural completeness (layer coverage, relationship completeness)
- Compliance summary (from capability→compliance links in Step 2)
- COBIT/ITIL alignment
- Traceability tree (driver → goal → capability → element → work package)
- ARB submission preparation
"""

import logging

logger = logging.getLogger(__name__)


class ValidationEngineService:
    """Comprehensive validation and review preparation for Step 6."""

    def full_validate(self, architecture_elements, capabilities, solution_id, migration_plan=None):
        """Run all validation checks and return comprehensive report.

        Args:
            architecture_elements: dict with elements_by_layer
            capabilities: list of accepted capability dicts
            solution_id: for querying related data
            migration_plan: optional migration plan from Step 5

        Returns:
            dict with structural, compliance, cobit_itil, traceability, summary
        """
        structural = self._validate_structure(architecture_elements)
        compliance = self._get_compliance_summary(capabilities)
        cobit_itil = self._get_governance_alignment(capabilities)
        traceability = self._build_traceability(capabilities, architecture_elements, migration_plan)
        risk_summary = self._get_risk_summary(solution_id)

        # Overall score: weighted across dimensions
        scores = {
            "structural": structural.get("score", 0),
            "compliance": compliance.get("score", 0),
            "governance": cobit_itil.get("score", 0),
            "traceability": traceability.get("score", 0),
        }
        overall = round(sum(scores.values()) / max(len(scores), 1))

        return {
            "structural": structural,
            "compliance": compliance,
            "cobit_itil": cobit_itil,
            "traceability": traceability,
            "risk_summary": risk_summary,
            "scores": scores,
            "overall": overall,
            "issues": structural.get("issues", []) + compliance.get("gaps", []),
            "completeness": structural.get("completeness", {}),
            "ready_for_arb": overall >= 60 and len(structural.get("blocking_issues", [])) == 0,
        }

    def submit_to_arb(self, solution_id, **_unused):
        """Fail closed: legacy validation output is not submission evidence.

        Callers must use the authenticated solution evidence-dossier endpoint,
        which binds the actor and creates the canonical immutable snapshot.
        """
        return {
            "success": False,
            "error": "legacy_submission_disabled",
            "reason_codes": ["canonical_submission_required"],
            "solution_id": solution_id,
        }

    def _validate_structure(self, elements_by_layer):
        """Check structural completeness per layer."""
        issues = []
        blocking = []
        completeness = {}

        # Required: must have elements; Recommended: should have elements
        required_layers = ("business", "application", "technology")
        recommended_layers = ("motivation", "strategy", "implementation")
        min_per_layer = {"motivation": 3, "strategy": 2, "business": 3, "application": 3,
                         "technology": 3, "implementation": 2}

        for layer in required_layers:
            elems = elements_by_layer.get(layer, [])
            count = len(elems)
            completeness[layer] = count

            if count == 0:
                issue = f"{layer.title()} layer has no elements"
                issues.append({"layer": layer, "message": issue, "severity": "blocking"})
                blocking.append(issue)
            elif count < min_per_layer.get(layer, 3):
                issues.append({
                    "layer": layer,
                    "message": f"{layer.title()} layer has {count} elements (minimum {min_per_layer.get(layer, 3)})",
                    "severity": "warning",
                })

        for layer in recommended_layers:
            elems = elements_by_layer.get(layer, [])
            count = len(elems)
            completeness[layer] = count

            if count == 0:
                issues.append({
                    "layer": layer,
                    "message": f"{layer.title()} layer has no elements — recommended for complete architecture",
                    "severity": "warning",
                })

        # Check for common structural gaps
        app_elems = elements_by_layer.get("application", [])
        tech_elems = elements_by_layer.get("technology", [])
        strategy_elems = elements_by_layer.get("strategy", [])

        has_app_component = any(e.get("type") in ("ApplicationComponent", "ApplicationService") for e in app_elems)
        has_tech_node = any(e.get("type") in ("Node", "TechnologyNode", "SystemSoftware", "TechnologyService") for e in tech_elems)
        has_capability = any(e.get("type") == "Capability" for e in strategy_elems)

        if has_app_component and not has_tech_node:
            issues.append({
                "layer": "technology",
                "message": "Application components exist but no technology nodes to host them",
                "severity": "warning",
            })

        has_data = any(e.get("type") == "DataObject" for e in app_elems)
        if has_app_component and not has_data:
            issues.append({
                "layer": "application",
                "message": "Application components exist but no DataObjects defined",
                "severity": "warning",
            })

        if has_app_component and not has_capability:
            issues.append({
                "layer": "strategy",
                "message": "Application components exist but no Capability elements link them to strategy",
                "severity": "warning",
            })

        all_layers = list(required_layers) + list(recommended_layers)
        total = sum(completeness.get(item, 0) for item in all_layers)
        target = sum(min_per_layer.get(item, 3) for item in all_layers)
        score = min(100, round(total / max(target, 1) * 100))

        return {
            "issues": issues,
            "blocking_issues": blocking,
            "completeness": completeness,
            "total_elements": total,
            "score": score,
        }

    def _get_compliance_summary(self, capabilities):
        """Aggregate compliance status from capability→compliance links.

        Reads from the canonical compliance store directly instead of through
        the removed CapabilityDerivationService.
        """
        frameworks = {}
        gaps = []
        total_reqs = 0

        for cap in capabilities:
            cap_id = cap.get("id") or cap.get("existing_id")
            if not cap_id:
                continue

            reqs = self._query_compliance_requirements(cap_id)
            for req in reqs:
                total_reqs += 1
                fw = req.get("framework", "Unknown")
                frameworks.setdefault(fw, {"requirements": [], "addressed": 0, "total": 0})
                frameworks[fw]["requirements"].append(req.get("name", ""))
                frameworks[fw]["total"] += 1
                frameworks[fw]["addressed"] += 1

        score = 100 if total_reqs == 0 else round(
            sum(f["addressed"] for f in frameworks.values()) /
            max(sum(f["total"] for f in frameworks.values()), 1) * 100
        )

        return {
            "frameworks": frameworks,
            "total_requirements": total_reqs,
            "gaps": gaps,
            "score": score,
        }

    @staticmethod
    def _query_compliance_requirements(capability_id):
        """Query compliance requirements for a business capability.

        Delegates to the shared compliance_service helper so there is exactly
        one implementation of this query.  Returns an empty list on failure
        after logging so the validation summary can continue.
        """
        import logging
        logger = logging.getLogger(__name__)
        try:
            from app.modules.architecture_assistant.services.compliance_service import (
                query_compliance_requirements,
            )
            return query_compliance_requirements(capability_id)
        except Exception:
            logger.warning(
                "Failed to query compliance requirements for capability %s",
                capability_id,
                exc_info=True,
            )
            return []

    def _get_governance_alignment(self, capabilities):
        """Query COBIT processes and ITIL practices for capabilities."""
        cobit_matches = []
        itil_matches = []

        try:
            from app.models.capabilities import COBITProcess, ITILPractice
            from app.models.capabilities import cobit_capability_mapping, itil_capability_mapping

            cap_ids = [c.get("id") or c.get("existing_id") for c in capabilities if c.get("id") or c.get("existing_id")]

            if cap_ids:
                # COBIT
                cobit = COBITProcess.query.join(
                    cobit_capability_mapping
                ).filter(
                    cobit_capability_mapping.c.capability_id.in_(cap_ids)
                ).all()
                for cp in cobit:
                    cobit_matches.append({
                        "code": cp.code,
                        "name": cp.name,
                        "domain": cp.domain.name if cp.domain else "",
                    })

                # ITIL
                itil = ITILPractice.query.join(
                    itil_capability_mapping
                ).filter(
                    itil_capability_mapping.c.capability_id.in_(cap_ids)
                ).all()
                for ip in itil:
                    itil_matches.append({
                        "code": ip.code,
                        "name": ip.name,
                        "practice_type": ip.practice_type,
                    })

        except Exception as e:
            logger.debug("Governance alignment query failed: %s", e)

        total = len(cobit_matches) + len(itil_matches)
        score = min(100, round(total * 20))  # 5+ governance links = 100%

        return {
            "cobit": cobit_matches,
            "itil": itil_matches,
            "score": score,
        }

    def _build_traceability(self, capabilities, architecture_elements, migration_plan=None):
        """Build the traceability chain: capability → elements → work packages."""
        chains = []

        for cap in capabilities:
            chain = {
                "capability": cap.get("name", "?"),
                "match_type": cap.get("match_type", "unknown"),
                "elements": [],
                "work_packages": [],
            }

            # Find architecture elements related to this capability
            cap_name_lower = cap.get("name", "").lower()
            for layer in ("business", "application", "technology"):
                for el in architecture_elements.get(layer, []):
                    el_desc = (el.get("description", "") or "").lower()
                    el_name = (el.get("name", "") or "").lower()
                    # Simple text matching for traceability
                    if cap_name_lower and (cap_name_lower in el_desc or cap_name_lower in el_name or
                            any(word in el_desc or word in el_name for word in cap_name_lower.split() if len(word) > 3)):
                        chain["elements"].append({
                            "name": el.get("name"),
                            "type": el.get("type"),
                            "layer": layer,
                        })

            # Link to work packages from migration plan
            if migration_plan:
                for phase in migration_plan.get("phases", []):
                    for wp in phase.get("work_packages", []):
                        wp_elements = [n.lower() for n in wp.get("element_names", [])]
                        for el in chain["elements"]:
                            if el.get("name", "").lower() in wp_elements:
                                chain["work_packages"].append({
                                    "name": wp.get("name"),
                                    "phase": phase.get("name"),
                                    "estimated_hours": wp.get("estimated_hours"),
                                })
                                break

            chains.append(chain)

        total_linked = sum(1 for c in chains if c["elements"])
        score = round(total_linked / max(len(chains), 1) * 100)

        return {
            "chains": chains,
            "total_capabilities": len(chains),
            "fully_traced": total_linked,
            "score": score,
        }

    def _get_risk_summary(self, solution_id):
        """Get risk summary from RiskAssessment records."""
        try:
            from app.models.models import RiskAssessment
            risks = RiskAssessment.query.filter(
                RiskAssessment.status != "closed"
            ).all()

            by_level = {"high": 0, "medium": 0, "low": 0}
            for r in risks:
                level = (r.probability or "medium").lower()
                if level in by_level:
                    by_level[level] += 1

            return {
                "total": len(risks),
                "by_level": by_level,
                "mitigated": sum(1 for r in risks if r.mitigation_strategy),
            }
        except Exception as e:
            # Do not fabricate a zero-risk summary on failure; None flows through
            # to the caller as risk_summary=None and renders as an em dash.
            logger.debug("Risk summary query failed: %s", e)
            return None
