"""
ImpactAnalysisService - Architecture Change Impact Analysis

Analyzes impact of changes across architecture elements with full dependency tracking.

Single persistence store: ImpactAnalysisResult (table impact_analysis_results).
All analysis results are stored via this ORM model only.
"""
from typing import Dict, List

from app import db
from app.services.application_cost_accessor import get_run_cost_with_source
from .decorators import transactional


def _acting_user_id():
    """Id of the signed-in user running the analysis, or None outside a request.

    Stored results belong to the organisation of this user, so a result with no
    user is visible to nobody.
    """
    from flask import has_request_context
    from flask_login import current_user

    if not has_request_context() or not current_user.is_authenticated:
        return None
    return getattr(current_user, "id", None)


def reached_by_kind(server: Dict) -> Dict[str, List[Dict]]:
    """The capabilities and the processes an analysis reached, by element type.

    The one classification of an analysis's elements: the impact screen, the simulation, and a
    saved scenario all read these two lists, so a count means the same wherever it is shown.
    """
    rows = list(server.get("direct_dependencies") or []) + list(server.get("indirect_dependencies") or [])

    def of_kind(word):
        return [r for r in rows if word in "".join(c for c in (r.get("type") or "").lower() if c.isalnum())]

    return {"impacted_capabilities": of_kind("capability"), "impacted_processes": of_kind("process")}


class _NotRecorded(Exception):
    """A run asked not to be stored in the history."""


class ImpactAnalysisService:
    """Service for analyzing impact of architecture changes with transitive dependencies."""

    @classmethod
    def analyze_change_impact(
        cls, element_id: int, change_type: str = "MODIFY", scenario: str = None,
        cursor: int = None, page_size: int = None, record: bool = True,
    ) -> Dict:
        """
        Analyze complete impact of changing an element.

        Args:
            element_id: Element being changed
            change_type: MODIFY, RETIRE, REPLACE
            scenario: Optional API scenario name (e.g. retirement, modification) for persistence.
            cursor: Optional pagination cursor (0-based index into the full result set).
            page_size: Optional page size for pagination.
            record: Store the run in the analysis history. A simulation passes False: it writes nothing.

        Returns:
            Full impact analysis with risk assessment
        """
        # Repointed to the canonical cross_layer_impact walk (max_depth=3, 3 hops from seed).
        from app.modules.intelligence.services.query_service import IntelligenceQueryService

        is_paginated = cursor is not None or page_size is not None

        # When paginated, fetch the full (unpaginated) result for risk scoring
        # and persistence, then paginate only the rows returned to the caller.
        if is_paginated:
            full_result = IntelligenceQueryService.cross_layer_impact(
                element_id,
                include_derived=False,
                max_depth=3,
                direction="downstream",
                # Scoring and storage never read owners; the page call below
                # resolves them for the rows it returns.
                with_owner=False,
            )
        else:
            full_result = None

        result = IntelligenceQueryService.cross_layer_impact(
            element_id,
            include_derived=False,
            max_depth=3,
            direction="downstream",
            with_owner=True,
            cursor=cursor,
            page_size=page_size,
        )
        rows = result.get("rows") or []

        # Use the full (unpaginated) rows for scoring and storage.
        scoring_rows = (full_result or result).get("rows") or []
        scoring_elements = (full_result or result).get("elements") or {}

        # Batch-resolve dependency_level from archimate_elements.
        all_element_ids = list({r["element_id"] for r in scoring_rows})
        dep_levels = {}
        if all_element_ids:
            from app.models.archimate_core import ArchiMateElement as _AE
            _ae_rows = _AE.query.filter(_AE.id.in_(all_element_ids)).with_entities(
                _AE.id, _AE.dependency_level
            ).all()
            dep_levels = {row.id: (row.dependency_level or "medium") for row in _ae_rows}

        # The application each reached element belongs to: itself when it is an application, otherwise the
        # nearest application on the path that reaches it (the changed element included).
        from app.middleware.tenant_context import current_org_id

        org_id = current_org_id()
        chain_ids = {i for r in scoring_rows for i in (r["relation"].get("chain_elements") or [])}
        lookup_ids = list(set(all_element_ids) | chain_ids | {element_id})
        applications = {}
        if lookup_ids:
            from app.models.application_portfolio import ApplicationComponent as _AC
            for component in _AC.query.filter(_AC.archimate_element_id.in_(lookup_ids)).all():
                applications[component.archimate_element_id] = component
        reached_ids = set(all_element_ids)
        app_names = {eid: component.name for eid, component in applications.items() if eid in reached_ids}
        costs = {}
        for eid, component in applications.items():
            value, source = get_run_cost_with_source(component)
            costs[eid] = (value, source)
        owner_rows = {}
        if org_id is not None and applications:
            from app.models.application_owner import ApplicationOwner
            by_component = ApplicationOwner.get_display_rows_for_applications(
                [c.id for c in applications.values()], org_id)
            owner_rows = {eid: [o["user_name"] for o in by_component.get(c.id, [])
                                if o.get("user_name") and o["user_name"] != "—"]
                          for eid, c in applications.items()}

        def _nearest_application(row):
            eid = row["element_id"]
            if eid in applications:
                return eid
            for step in reversed(row["relation"].get("chain_elements") or []):
                if step in applications:
                    return step
            return None

        def _row_to_dep(row):
            el = scoring_elements.get(str(row["element_id"]), {})
            eid = row["element_id"]
            near = _nearest_application(row)
            app_cost = costs.get(near, (None, None))[0] if near is not None else None
            return {
                "id": eid,
                "name": el.get("name"),
                "type": el.get("type"),
                "level": row["relation"]["depth"],
                "dependency_level": dep_levels.get(eid, "medium"),
                "app_name": app_names.get(eid),
                "served_by_element_id": near,
                "served_by": applications[near].name if near is not None else None,
                "application_cost": app_cost,
                "criticality": None,
                "owner": row.get("owner"),
                "health": row.get("health"),
                "owners": row.get("owners", []),
                "application_owners": owner_rows.get(near, []) if near is not None else [],
            }

        # Build deps from the full (unpaginated) rows for scoring.
        all_deps = [_row_to_dep(r) for r in scoring_rows]
        direct_deps = [d for d in all_deps if d["level"] == 1]
        indirect_deps = [d for d in all_deps if d["level"] > 1]

        # Severity-weighted risk: critical elements count 5x, high 3x, medium 2x, low/unknown 1x
        _dep_weights = {"critical": 5, "high": 3, "medium": 2, "low": 1}
        total_affected = len(direct_deps) + len(indirect_deps)
        weighted_score = sum(
            _dep_weights.get((d.get("dependency_level") or "low").lower(), 1)
            for d in all_deps
        )
        if weighted_score > 40:
            risk_level = "CRITICAL"
        elif weighted_score > 20:
            risk_level = "HIGH"
        elif weighted_score > 5:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        # Financial exposure: the yearly running cost of the changed application and of the other applications
        # the change reaches, from the one cost accessor. Never a guess: when nothing is costed the figure is
        # None and the reason says why.
        reached_apps = {}
        for dep in all_deps:
            near = dep.get("served_by_element_id")
            if near is not None and near != element_id:
                reached_apps[near] = applications[near].name
        exposed = dict(reached_apps)
        if element_id in applications:
            exposed[element_id] = applications[element_id].name
        costed = {eid: costs[eid][0] for eid in exposed if costs.get(eid, (None, None))[0] is not None}
        others = len(reached_apps)
        scope_words = ("%s%s" % ("the changed application" if element_id in applications else "",
                                 (" and %d other application%s it reaches" % (others, "" if others == 1 else "s"))
                                 if others and element_id in applications else
                                 ("%d application%s it reaches" % (others, "" if others == 1 else "s") if others else "")))
        if costed:
            estimated_financial_risk = float(sum(costed.values()))
            if len(costed) == len(exposed):
                financial_risk_reason = "Yearly running cost of %s." % scope_words
            else:
                financial_risk_reason = "Yearly running cost of %d of the %d applications at stake; the rest have no cost recorded." % (
                    len(costed), len(exposed))
        else:
            estimated_financial_risk = None
            if not exposed:
                financial_risk_reason = ("This element is not an application and no application is reached by the "
                                         "change, so no running cost is at risk.")
            else:
                financial_risk_reason = ("No running cost is recorded for %s, so the cost at risk cannot be worked out." % (
                    scope_words or "the applications at stake"))
        element_application = applications.get(element_id)

        # Store in impact_analysis_results (ORM model - table exists)
        analysis_id = None
        try:
            if not record:
                raise _NotRecorded
            from app.models.traceability import ImpactAnalysisResult
            import json as _json
            stored = ImpactAnalysisResult(
                analysis_type="change_impact",
                trigger_element_type=change_type,
                trigger_element_id=element_id,
                scenario=scenario,
                impacted_elements=_json.dumps([d["id"] for d in all_deps]),
                overall_severity=risk_level.lower(),
                affected_applications_count=sum(1 for d in all_deps if d.get("app_name")),
                created_by_id=_acting_user_id(),
            )
            db.session.add(stored)
            db.session.commit()
            analysis_id = stored.id
        except _NotRecorded:
            pass
        except Exception:
            db.session.rollback()

        # Build paginated deps for the response when pagination is active.
        if is_paginated:
            page_deps = [_row_to_dep(r) for r in rows]
            page_direct = [d for d in page_deps if d["level"] == 1]
            page_indirect = [d for d in page_deps if d["level"] > 1]
        else:
            page_direct = direct_deps
            page_indirect = indirect_deps

        return {
            "element_id": element_id,
            "change_type": change_type,
            "direct_dependencies": page_direct,
            "indirect_dependencies": page_indirect,
            "total_affected": total_affected,
            "weighted_score": weighted_score,
            "risk_level": risk_level,
            "estimated_financial_risk": estimated_financial_risk,
            "financial_risk_reason": financial_risk_reason,
            "reached_applications": [
                {"element_id": eid, "name": name, "annual_cost": costs.get(eid, (None, None))[0],
                 "cost_source": costs.get(eid, (None, None))[1]}
                for eid, name in sorted(reached_apps.items(), key=lambda kv: kv[1] or "")],
            "changed_application": (
                {"element_id": element_id, "name": element_application.name,
                 "annual_cost": costs.get(element_id, (None, None))[0],
                 "cost_source": costs.get(element_id, (None, None))[1]}
                if element_application is not None else None),
            "analysis_id": analysis_id,
            "total": result.get("total"),
            "next_cursor": result.get("next_cursor"),
        }

    @classmethod
    @transactional
    def analyze_portfolio_impact(cls, change_scenarios: List[Dict]) -> Dict:
        """
        Analyze impact of multiple changes across the portfolio.

        Args:
            change_scenarios: List of {'element_id': int, 'change_type': str}

        Returns:
            Portfolio-wide impact analysis
        """
        portfolio_impact = []

        for scenario in change_scenarios:
            impact = cls.analyze_change_impact(
                scenario["element_id"], scenario.get("change_type", "MODIFY")
            )
            portfolio_impact.append(impact)

        # Calculate portfolio metrics
        total_affected = sum(imp["total_affected"] for imp in portfolio_impact)
        critical_count = len([imp for imp in portfolio_impact if imp["risk_level"] == "CRITICAL"])
        high_count = len([imp for imp in portfolio_impact if imp["risk_level"] == "HIGH"])

        return {
            "change_scenarios": change_scenarios,
            "individual_impacts": portfolio_impact,
            "portfolio_metrics": {
                "total_affected_elements": total_affected,
                "critical_impacts": critical_count,
                "high_impacts": high_count,
                "overall_risk": "CRITICAL"
                if critical_count > 2
                else "HIGH"
                if high_count > 3
                else "MEDIUM"
                if (critical_count > 0 or high_count > 0)
                else "LOW",
            },
        }

    @classmethod
    def check_solution_vs_principles(cls, solution_id: int) -> Dict:
        """ENH-021: Check a solution against all active architectural principles.

        Returns:
            {
                "solution_id": int,
                "compliant": bool,
                "violations": [{"principle_id": int, "principle_name": str, "reason": str}],
                "checked_at": str (ISO 8601),
            }
        """
        from datetime import datetime as _dt

        violations = []

        try:
            from app.models.solution_models import Solution
            from app.models.motivation_extended import Principle

            solution = db.session.get(Solution, solution_id)
            if not solution:
                return {"error": "Solution not found", "solution_id": solution_id}

            # Load all mandatory/advisory active principles
            principles = Principle.query.filter(
                Principle.enforcement_status != "retired"
            ).all()

            if not principles:
                return {
                    "solution_id": solution_id,
                    "compliant": True,
                    "violations": [],
                    "warnings": ["No active principles found to check against."],
                    "checked_at": _dt.utcnow().isoformat(),
                }

            # Rule-based checks against solution fields
            for principle in principles:
                reason = cls._evaluate_principle(solution, principle)
                if reason:
                    violations.append({
                        "principle_id": principle.id,
                        "principle_name": principle.name,
                        "enforcement_level": getattr(principle, "enforcement_level", "advisory"),
                        "reason": reason,
                    })

        except Exception as exc:
            return {
                "solution_id": solution_id,
                "compliant": False,
                "violations": [],
                "error": str(exc),
                "checked_at": _dt.utcnow().isoformat(),
            }

        # Only mandatory violations count as non-compliant
        mandatory_violations = [
            v for v in violations if v.get("enforcement_level") == "mandatory"
        ]

        return {
            "solution_id": solution_id,
            "compliant": len(mandatory_violations) == 0,
            "violations": violations,
            "checked_at": _dt.utcnow().isoformat(),
        }

    @staticmethod
    def _evaluate_principle(solution, principle) -> str:
        """Return a violation reason string, or empty string if compliant."""
        name_lower = (principle.name or "").lower()
        (principle.statement or "").lower()

        # Security principle: solution must have a security_lead defined
        if "security" in name_lower and not solution.security_lead:
            return (
                f"Principle '{principle.name}' requires a security lead to be assigned. "
                "Set solution.security_lead."
            )

        # Data protection principle: must have data_protection_officer
        if "data protection" in name_lower and not solution.data_protection_officer:
            return (
                f"Principle '{principle.name}' requires a Data Protection Officer. "
                "Set solution.data_protection_officer."
            )

        # Business value: solution must have business_value
        if "business value" in name_lower and not solution.business_value:
            return (
                f"Principle '{principle.name}' requires business_value to be documented."
            )

        # Owner accountability: must have solution_owner
        if "accountability" in name_lower or "ownership" in name_lower:
            if not solution.solution_owner:
                return (
                    f"Principle '{principle.name}' requires solution_owner to be assigned."
                )

        # No violations detected
        return ""
