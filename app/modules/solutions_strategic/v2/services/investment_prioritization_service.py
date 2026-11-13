"""
Investment Prioritization Service

Analyzes and prioritizes investments in business capabilities based on:
- Strategic importance and business value
- Current coverage and maturity gaps
- Risk assessment and single points of failure
- ROI and cost-benefit analysis
- Technology debt and modernization needs
"""

import logging
from datetime import datetime
from typing import Dict, List

from app import db
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import UnifiedCapability
from .decorators import transactional

_CLOSED_RISK_STATUSES = ("mitigated", "accepted", "closed")

# Strategic score (0 - 25) of each importance level, recorded or worked out.
_IMPORTANCE_POINTS = {"critical": 25, "high": 20, "medium": 15, "low": 10}
_LEVEL_ORDER = ("low", "medium", "high", "critical")
# What an application's recorded criticality says about the capabilities it supports.
_CRITICALITY_LEVEL = {
    "critical": "critical", "mission_critical": "critical", "mission critical": "critical",
    "high": "high", "important": "high",
    "medium": "medium", "supporting": "medium",
    "low": "low", "optional": "low",
}
# The elements of the motivation layer a capability can be linked to.
_MOTIVATION_TYPES = ("goal", "driver", "outcome", "principle")
_MAX_EVIDENCE = 5
STRATEGIC_STEP_TEXT = "Record its importance"
STRATEGIC_UNSCORED_REASON = (
    "Record how important it is, or link an application that has a criticality, a goal or a value "
    "stream to it, to score it."
)


def _record_page(capability=None):
    """The page where a capability's importance is recorded, or None when it cannot be built.

    With a capability the link opens that capability's own edit form. The score reads the unified
    record; the form edits the capability it was projected from, so the link names that one."""
    from app.services.result_footer_service import page_link

    href = page_link("unified_low_priority.capability_map_capabilities")
    if href and capability is not None:
        edit_id = capability.source_id if capability.source_table == "business_capability" else capability.id
        href = f"{href}{'&' if '?' in href else '?'}edit={edit_id}"
    return href


def currency_symbol() -> str:
    """The tenant's configured currency symbol, the one both investment pages show."""
    from config import CurrencyConfig

    try:
        return CurrencyConfig.get_currency_config().get("symbol", "£")
    except Exception:
        return "£"

# Timeframe string → integer months for ROI calculator
_TIMEFRAME_MONTHS: Dict[str, int] = {
    "12 - 18 months": 15,
    "6 - 12 months": 9,
    "3 - 6 months": 4,
}

logger = logging.getLogger(__name__)


class InvestmentPrioritizationService:
    """
    Service for prioritizing investments in business capabilities.

    Provides data-driven investment recommendations based on multiple factors:
    - Strategic alignment and business criticality
    - Current capability coverage and maturity
    - Risk factors and dependencies
    - Technology health and modernization needs
    """

    def __init__(self):
        pass

    @transactional
    def analyze_investment_priorities(self, include_risk_analysis: bool = True) -> Dict:
        """
        Comprehensive investment prioritization analysis.

        Args:
            include_risk_analysis: Include risk assessment in prioritization

        Returns:
            Dict with prioritized investment recommendations
        """
        from app.middleware.tenant_context import current_org_id
        from app.modules.capabilities.services.capability_count_service import capability_totals

        # The organisation's own capabilities and the shared catalogue's, by the one visibility rule
        org_id = current_org_id()
        capabilities = UnifiedCapability.query.filter(UnifiedCapability.visibility_predicate(org_id)).all()

        capability_ids = [c.id for c in capabilities]

        # Batch-load the capability mappings of these capabilities, keeping only
        # those whose application this organisation can see.
        mappings_by_cap = {}
        app_rows = {}
        try:
            from app.models.application_portfolio import ApplicationComponent

            app_rows = {
                row.id: row
                for row in db.session.execute(
                    db.select(
                        ApplicationComponent.id, ApplicationComponent.name,
                        ApplicationComponent.business_criticality, ApplicationComponent.criticality,
                        ApplicationComponent.updated_at, ApplicationComponent.created_at,
                    )
                ).all()
            }
            visible_apps = set(app_rows)
            if capability_ids:
                for m in UnifiedApplicationCapabilityMapping.query.filter(
                    UnifiedApplicationCapabilityMapping.unified_capability_id.in_(capability_ids)
                ).all():
                    if m.application_component_id in visible_apps:
                        mappings_by_cap.setdefault(m.unified_capability_id, []).append(m)
        except Exception:
            logger.debug("Could not batch-load capability mappings", exc_info=True)

        motivation = self._motivation_links(capabilities)
        evidence = {
            "domains": self._domain_names(capabilities),
            "objectives": {
                cap_id: sum(1 for link in links if link["type"] == "goal")
                for cap_id, links in motivation.items()
            },
            "motivation": motivation,
            "applications": app_rows,
            "streams": self._value_streams_by_capability(capability_ids),
            "risks": self._risks_by_capability(mappings_by_cap),
            "work": self._work_cost_by_capability(capability_ids),
        }

        # Build capability → initiative map via RoadmapItem.linked_capabilities JSON
        linked_initiatives = self._get_linked_initiatives()

        # Analyze each capability for investment priority
        capability_scores = []
        for capability in capabilities:
            score_data = self._calculate_capability_score(
                capability, include_risk_analysis, mappings_by_cap, linked_initiatives, evidence
            )
            capability_scores.append(score_data)

        # Sort by investment priority (highest first)
        capability_scores.sort(
            key=lambda x: (x["investment_priority_score"] is None, -(x["investment_priority_score"] or 0))
        )

        # Categorize by priority levels
        critical_investments = [c for c in capability_scores if c["priority_level"] == "CRITICAL"]
        high_investments = [c for c in capability_scores if c["priority_level"] == "HIGH"]
        medium_investments = [c for c in capability_scores if c["priority_level"] == "MEDIUM"]
        low_investments = [c for c in capability_scores if c["priority_level"] == "LOW"]

        # Calculate portfolio metrics; the total is a count query, never the length of a list
        portfolio_metrics = self._calculate_portfolio_metrics(capability_scores)
        totals = capability_totals(org_id)
        portfolio_metrics.update({
            "total_capabilities": totals["total"],
            "own_capabilities": totals["own"],
            "shared_capabilities": totals["shared"],
        })

        # Generate investment recommendations
        recommendations = self._generate_investment_recommendations(capability_scores)

        return {
            "total_capabilities": totals["total"],
            "capability_scores": capability_scores,
            "critical_investments": critical_investments,
            "high_investments": high_investments,
            "medium_investments": medium_investments,
            "low_investments": low_investments,
            "portfolio_metrics": portfolio_metrics,
            "recommendations": recommendations,
            "analysis_date": datetime.utcnow().isoformat(),
        }

    def _get_linked_initiatives(self) -> Dict[int, Dict]:
        """
        Build a map of capability_id → initiative budget data.

        Traverses RoadmapItem.linked_capabilities JSON to resolve the
        StrategicInitiative that funds each BusinessCapability.
        """
        cap_to_initiative: Dict[int, Dict] = {}
        try:
            from app.models.strategic import RoadmapItem, StrategicInitiative

            items = (
                RoadmapItem.query
                .filter(
                    RoadmapItem.initiative_id.isnot(None),
                    RoadmapItem.linked_capabilities.isnot(None),
                )
                .all()
            )
            if not items:
                return cap_to_initiative

            # Batch-load linked initiatives to avoid N+1
            initiative_ids = {item.initiative_id for item in items}
            initiatives = {
                ini.id: ini
                for ini in StrategicInitiative.query.filter(
                    StrategicInitiative.id.in_(initiative_ids)
                ).all()
            }

            for item in items:
                initiative = initiatives.get(item.initiative_id)
                if not initiative:
                    continue
                for raw_id in item.get_linked_capabilities():
                    try:
                        cap_id = int(raw_id)
                    except (TypeError, ValueError):
                        continue
                    existing = cap_to_initiative.get(cap_id)
                    new_budget = initiative.budget_allocated or 0.0
                    # When multiple items link the same capability, keep the highest-budget initiative
                    if existing is None or new_budget > (existing.get("budget_allocated") or 0.0):
                        cap_to_initiative[cap_id] = {
                            "initiative_id": initiative.id,
                            "initiative_name": initiative.name,
                            "budget_allocated": new_budget,
                            "budget_spent": initiative.budget_spent or 0.0,
                            "budget_remaining": initiative.budget_remaining,
                            "business_value_score": initiative.business_value_score,
                            "risk_level": initiative.risk_level,
                            "status": initiative.status,
                        }
        except Exception:
            logger.debug("Could not build linked-initiatives map", exc_info=True)
        return cap_to_initiative

    def _calculate_capability_score(
        self, capability, include_risk: bool,
        mappings_by_cap: Dict = None,
        linked_initiatives: Dict = None,
        evidence: Dict = None,
    ) -> Dict:
        """Calculate investment priority score for a single capability.

        Every figure comes from the model. A figure the model cannot compute
        is None with a reason, never a stand-in value.
        """
        evidence = evidence or {}

        # Use pre-loaded mappings dict (batch) instead of per-capability query
        app_mappings = (mappings_by_cap or {}).get(capability.id, [])

        coverage_count = len(app_mappings)

        # Strategic importance score (0 - 25 points): the recorded rating, else the
        # rating the model's own facts imply; None only when it holds nothing to work from
        linked_risks = (evidence.get("risks") or {}).get(capability.id, [])
        strategic_score = self._calculate_strategic_score(capability)
        derivation = None
        if strategic_score is None:
            derivation = self._derive_importance(capability, app_mappings, linked_risks, evidence)
            if derivation:
                strategic_score = _IMPORTANCE_POINTS[derivation["level"]]
        recorded = (capability.strategic_importance or "").lower()
        importance = recorded if recorded in _IMPORTANCE_POINTS else (derivation or {}).get("level")
        objective_count = (evidence.get("objectives") or {}).get(capability.id, 0)

        # Coverage gap score (0 - 25 points)
        coverage_score = self._calculate_coverage_score(coverage_count, capability, importance)

        # Maturity gap score (0 - 25 points)
        maturity_score = self._calculate_maturity_score(capability)

        # Risk score (0 - 25 points): the highest exposure among the open risks
        # linked to the applications that run this capability
        risk_score = 0
        if include_risk:
            risk_score = self._calculate_risk_score(capability, app_mappings, linked_risks)

        # Total investment priority score (0 - 100). Without an importance
        # rating the total cannot be computed, so it is not scored at all.
        if strategic_score is None:
            total_score = None
            priority_level = "NOT SCORED"
            unscored_reason = STRATEGIC_UNSCORED_REASON
        else:
            total_score = strategic_score + coverage_score + maturity_score + risk_score
            unscored_reason = None
            if total_score >= 80:
                priority_level = "CRITICAL"
            elif total_score >= 60:
                priority_level = "HIGH"
            elif total_score >= 40:
                priority_level = "MEDIUM"
            else:
                priority_level = "LOW"

        # Resolve linked initiative for this capability (if any)
        linked_initiative = (linked_initiatives or {}).get(capability.id)

        investment_need = self._estimate_investment_need(
            capability, total_score, app_mappings, linked_initiative,
            (evidence.get("work") or {}).get(capability.id),
        )

        has_real_budget = investment_need.get("has_real_budget", False)

        return {
            "capability_id": capability.id,
            "capability_name": capability.name,
            "archimate_element_id": capability.archimate_element_id,
            "capability_domain": (evidence.get("domains") or {}).get(capability.id),
            "strategic_importance": capability.strategic_importance,
            "effective_importance": importance,
            "strategic_basis": (
                "derived" if derivation else ("recorded" if recorded in _IMPORTANCE_POINTS else None)
            ),
            "strategic_derivation": derivation,
            "strategic_step": (
                None if strategic_score is not None else {"text": STRATEGIC_STEP_TEXT, "href": _record_page(capability)}
            ),
            "capability_type": capability.capability_type,
            "coverage_count": coverage_count,
            "strategic_score": strategic_score,
            "objective_count": objective_count,
            "coverage_score": coverage_score,
            "maturity_score": maturity_score,
            "current_maturity_level": capability.current_maturity_level,
            "target_maturity_level": capability.target_maturity_level,
            "maturity_assessed": (
                capability.current_maturity_level is not None
                and capability.target_maturity_level is not None
            ),
            "risk_score": risk_score,
            "risk_count": len(linked_risks),
            "investment_priority_score": total_score,
            "priority_level": priority_level,
            "unscored_reason": unscored_reason,
            "investment_need": investment_need,
            # Flat fields consumed by ROI Calculator modal
            "estimated_cost": investment_need["estimated_cost"],
            "timeframe": investment_need.get("timeframe_months", 12),
            # Initiative linkage
            "has_real_budget": has_real_budget,
            "linked_initiative_id": linked_initiative["initiative_id"] if linked_initiative else None,
            "linked_initiative_name": linked_initiative["initiative_name"] if linked_initiative else None,
            "linked_initiative_budget": linked_initiative["budget_allocated"] if linked_initiative else None,
            "recommendation": (
                "RATE: Set how important %s is to the strategy so it can be scored" % capability.name
                if total_score is None
                else self._get_capability_recommendation(capability, total_score, coverage_count, importance)
            ),
        }

    def _domain_names(self, capabilities) -> Dict[int, str]:
        """Domain of each capability: its own, else the nearest ancestor's."""
        from app.models.unified_capability import BusinessDomain

        by_id = {c.id: c for c in capabilities}
        domain_ids = {c.domain_id for c in capabilities if c.domain_id}
        names = {}
        if domain_ids:
            names = {
                d.id: d.name
                for d in BusinessDomain.query.filter(BusinessDomain.id.in_(domain_ids)).all()
            }
        result = {}
        for cap in capabilities:
            node, hops = cap, 0
            while node is not None and hops < 10:
                if node.domain_id and node.domain_id in names:
                    result[cap.id] = names[node.domain_id]
                    break
                node = by_id.get(node.parent_capability_id)
                hops += 1
        return result

    def _motivation_links(self, capabilities) -> Dict[int, List[Dict]]:
        """Goals, drivers, outcomes and principles each capability is linked to through the model."""
        from app.models.models import ArchiMateElement, ArchiMateRelationship

        element_to_cap = {
            c.archimate_element_id: c.id for c in capabilities if c.archimate_element_id
        }
        if not element_to_cap:
            return {}
        rels = ArchiMateRelationship.query.filter(
            db.or_(
                ArchiMateRelationship.source_id.in_(list(element_to_cap)),
                ArchiMateRelationship.target_id.in_(list(element_to_cap)),
            )
        ).all()
        other_ends = {}
        for rel in rels:
            when = rel.updated_at or rel.created_at
            for mine, theirs in ((rel.source_id, rel.target_id), (rel.target_id, rel.source_id)):
                if mine in element_to_cap and theirs:
                    ends = other_ends.setdefault(element_to_cap[mine], {})
                    if theirs not in ends or (when and (ends[theirs] is None or when > ends[theirs])):
                        ends[theirs] = when
        wanted = {e for ends in other_ends.values() for e in ends}
        if not wanted:
            return {}
        found = {
            row.id: row
            for row in db.session.execute(
                db.select(ArchiMateElement.id, ArchiMateElement.name, ArchiMateElement.type).where(
                    ArchiMateElement.id.in_(wanted),
                    db.func.lower(ArchiMateElement.type).in_(_MOTIVATION_TYPES),
                )
            ).all()
        }
        result = {}
        for cap_id, ends in other_ends.items():
            links = [
                {"id": eid, "name": found[eid].name, "type": found[eid].type.lower(),
                 "as_of": when.isoformat() if when else None}
                for eid, when in ends.items() if eid in found
            ]
            if links:
                result[cap_id] = sorted(links, key=lambda link: (link["type"], link["name"] or ""))
        return result

    def _value_streams_by_capability(self, capability_ids: List[int]) -> Dict[int, List[Dict]]:
        """The value streams each capability serves, one entry per stream."""
        from app.models.unified_capability import CapabilityValueStreamMapping, ValueStream

        if not capability_ids:
            return {}
        rows = db.session.execute(
            db.select(
                CapabilityValueStreamMapping.capability_id, ValueStream.id, ValueStream.name,
                CapabilityValueStreamMapping.updated_at, CapabilityValueStreamMapping.created_at,
            )
            .join(ValueStream, ValueStream.id == CapabilityValueStreamMapping.value_stream_id)
            .where(CapabilityValueStreamMapping.capability_id.in_(capability_ids))
        ).all()
        result = {}
        for cap_id, stream_id, name, updated, created in rows:
            when = updated or created
            by_stream = result.setdefault(cap_id, {})
            if stream_id not in by_stream:
                by_stream[stream_id] = {"id": stream_id, "name": name,
                                        "as_of": when.isoformat() if when else None}
        return {cap_id: sorted(v.values(), key=lambda s: s["name"] or "") for cap_id, v in result.items()}

    def _derive_importance(self, capability, app_mappings: List, linked_risks: List, evidence: Dict):
        """The importance level the model's own facts imply for a capability nobody rated, with its evidence.

        The first of these rules that has a fact decides; a rating somebody recorded never reaches here.
          1. the highest recorded criticality among the applications that support it;
          2. its links to goals, drivers, outcomes or principles (two or more: high, one: medium);
          3. the value streams it serves (two or more: high, one: medium);
          4. the open risks on the applications that support it (an exposure of 15 or more: high, else medium).
        Returns None when the model holds none of these.
        """
        def derived(level, basis, sentence, facts):
            return {
                "level": level, "basis": basis, "sentence": sentence,
                "facts": facts[:_MAX_EVIDENCE], "more": max(0, len(facts) - _MAX_EVIDENCE),
            }

        def plural(n, one, many=None):
            return "%d %s" % (n, one if n == 1 else (many or one + "s"))

        rows = evidence.get("applications") or {}
        facts = []
        levels = []
        for mapping in app_mappings:
            row = rows.get(mapping.application_component_id)
            raw = ((row.business_criticality or row.criticality) if row is not None else None) or ""
            level = _CRITICALITY_LEVEL.get(raw.strip().lower())
            if level:
                levels.append(level)
                when = row.updated_at or row.created_at
                facts.append({"kind": "application", "id": row.id, "label": "%s: %s criticality" % (row.name, raw.strip()),
                              "as_of": when.isoformat() if when else None})
        if levels:
            n = len(levels)
            return derived(
                max(levels, key=_LEVEL_ORDER.index), "the criticality of its applications",
                "Derived from the criticality of the %s that %s it." % (
                    plural(n, "application"), "supports" if n == 1 else "support"),
                facts)

        links = (evidence.get("motivation") or {}).get(capability.id) or []
        if links:
            counts = {}
            for link in links:
                counts[link["type"]] = counts.get(link["type"], 0) + 1
            parts = [plural(counts[t], t) for t in _MOTIVATION_TYPES if t in counts]
            phrase = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
            return derived(
                "high" if len(links) >= 2 else "medium", "its links to goals, drivers, outcomes and principles",
                "Derived from its links to %s." % phrase,
                [{"kind": link["type"], "id": link["id"], "label": "%s: %s" % (link["type"].capitalize(), link["name"]),
                  "as_of": link["as_of"]} for link in links])

        streams = (evidence.get("streams") or {}).get(capability.id) or []
        if streams:
            n = len(streams)
            return derived(
                "high" if n >= 2 else "medium", "the value streams it serves",
                "Derived from the %s it serves." % plural(n, "value stream"),
                [{"kind": "value_stream", "id": st["id"], "label": "Value stream: %s" % st["name"],
                  "as_of": st["as_of"]} for st in streams])

        if linked_risks:
            n = len(linked_risks)
            return derived(
                "high" if max(r["exposure"] for r in linked_risks) >= 15 else "medium", "its open risks",
                "Derived from the %s on the applications that support it." % plural(n, "open risk"),
                [{"kind": "risk", "id": r["id"], "label": "Risk: %s" % r["title"], "as_of": None}
                 for r in linked_risks])
        return None

    def _risks_by_capability(self, mappings_by_cap: Dict) -> Dict[int, List[Dict]]:
        """Open risks linked to the applications that run each capability."""
        from app.models.risk import Risk
        from app.models.risk_entity_link import RiskEntityLink

        app_ids = {m.application_component_id for ms in mappings_by_cap.values() for m in ms}
        if not app_ids:
            return {}
        links = RiskEntityLink.query.filter(
            RiskEntityLink.entity_type == "application",
            RiskEntityLink.entity_id.in_(app_ids),
        ).all()
        risk_ids = {link.risk_id for link in links}
        if not risk_ids:
            return {}
        risks = {
            r.id: r
            for r in Risk.query.filter(Risk.id.in_(risk_ids)).all()
            if getattr(r.status, "value", r.status) not in _CLOSED_RISK_STATUSES
        }
        risks_by_app = {}
        for link in links:
            risk = risks.get(link.risk_id)
            if risk is not None:
                risks_by_app.setdefault(link.entity_id, {})[risk.id] = {
                    "id": risk.id,
                    "title": risk.title,
                    "exposure": int(risk.likelihood) * int(risk.impact),
                }
        result = {}
        for cap_id, mappings in mappings_by_cap.items():
            found = {}
            for m in mappings:
                found.update(risks_by_app.get(m.application_component_id, {}))
            if found:
                result[cap_id] = sorted(found.values(), key=lambda r: -r["exposure"])
        return result

    def _work_cost_by_capability(self, capability_ids: List[int]) -> Dict[int, Dict]:
        """Estimated cost of the work packages linked to each capability.

        A package is linked to its primary capability and to every capability
        in its list of capabilities. A package that changes several shares its
        estimate equally between them, so the total over all capabilities is
        never more than the estimates recorded. A package with no estimate
        (0 is the column default) adds no figure.
        """
        from app.models.unified_work_package import UnifiedWorkPackage
        from app.services.work_package_service import _capability_ids_of

        if not capability_ids:
            return {}
        wanted = set(capability_ids)
        result = {}
        for wp in UnifiedWorkPackage.query.filter(UnifiedWorkPackage.estimated_cost > 0).all():
            linked = [i for i in _capability_ids_of(wp) if i in wanted]
            if not linked:
                continue
            share = float(wp.estimated_cost) / len(linked)
            for cap_id in linked:
                entry = result.setdefault(cap_id, {"cost": 0.0, "count": 0})
                entry["cost"] += share
                entry["count"] += 1
        return result

    def _calculate_strategic_score(self, capability) -> int:
        """Calculate strategic importance score (0 - 25 points) from the recorded rating; None when unrated."""
        return _IMPORTANCE_POINTS.get((capability.strategic_importance or "").lower())

    def _calculate_coverage_score(self, coverage_count: int, capability, importance: str = None) -> int:
        """Calculate coverage gap score (0 - 25 points), by the recorded importance or the one worked out."""
        importance = (importance or capability.strategic_importance or "").lower()

        # Critical capabilities need more coverage
        if importance == "critical":
            if coverage_count == 0:
                return 25  # No coverage - highest priority
            elif coverage_count == 1:
                return 20  # Single point of failure
            elif coverage_count == 2:
                return 10  # Minimal coverage
            else:
                return 5  # Adequate coverage
        elif importance == "high":
            if coverage_count == 0:
                return 20
            elif coverage_count == 1:
                return 15
            elif coverage_count == 2:
                return 5
            else:
                return 0
        else:
            if coverage_count == 0:
                return 15
            elif coverage_count == 1:
                return 5
            else:
                return 0

    def _calculate_maturity_score(self, capability) -> int:
        """Calculate maturity gap score (0 - 25 points).

        D-7: `or 1` / `or 3` fabricated a specific invented gap (e.g. "2")
        for a capability with no recorded maturity at all, feeding a
        computed investment-priority number from a made-up input. When
        either side is genuinely unassessed, this returns a documented
        NEUTRAL score (the midpoint of the 0-25 range) rather than
        pretending a real gap was measured -- the caller can distinguish
        "measured" from "assumed neutral" via `maturity_assessed` in the
        returned capability dict (see `_calculate_capability_score`).
        """
        current_maturity = capability.current_maturity_level
        target_maturity = capability.target_maturity_level

        if current_maturity is None or target_maturity is None:
            return 12  # neutral midpoint -- not a measured gap

        maturity_gap = target_maturity - current_maturity

        if maturity_gap >= 3:
            return 25  # Large maturity gap
        elif maturity_gap >= 2:
            return 20
        elif maturity_gap >= 1:
            return 15
        else:
            return 5  # Small or no gap

    def _calculate_risk_score(self, capability, app_mappings: List, linked_risks: List = None) -> int:
        """Calculate risk score (0 - 25 points).

        It is the highest exposure (likelihood times impact, 1 - 25) among the open
        risks linked to the applications that run the capability; 0 when none are.
        """
        return max((r["exposure"] for r in (linked_risks or [])), default=0)

    def _estimate_investment_need(
        self, capability, score,
        app_mappings: List = None,
        linked_initiative: Dict = None,
        work: Dict = None,
    ) -> Dict:
        """Investment need from recorded figures only, with its source.

        The remaining budget of the initiative that funds the capability, else the
        estimated cost of the work packages linked to it. With neither, the need
        is not estimated: ``estimated_cost`` is None, never a flat guess.
        """
        base = {
            "currency": "USD",
            "estimated_cost": None,
            "source": None,
            "timeframe": None,
            "has_real_budget": False,
            "budget_allocated": None,
            "budget_spent": None,
            "initiative_name": None,
        }
        if linked_initiative and (linked_initiative.get("budget_allocated") or 0.0) > 0.0:
            base.update(
                estimated_cost=linked_initiative["budget_remaining"],
                source="Initiative: %s" % linked_initiative["initiative_name"],
                has_real_budget=True,
                budget_allocated=linked_initiative["budget_allocated"],
                budget_spent=linked_initiative["budget_spent"],
                initiative_name=linked_initiative["initiative_name"],
            )
        elif work and work["count"]:
            base.update(
                estimated_cost=work["cost"],
                source="%d work package%s" % (work["count"], "" if work["count"] == 1 else "s"),
            )
        return base

    def _get_capability_recommendation(
        self, capability, score: int, coverage_count: int, importance: str = None
    ) -> str:
        """Generate specific recommendation for the capability."""
        importance = (importance or capability.strategic_importance or "").lower()

        if coverage_count == 0:
            return (
                f"INVEST: Establish {capability.name} capability - no current application support"
            )
        elif coverage_count == 1 and importance in ["critical", "high"]:
            return f"INVEST: Add redundancy for {capability.name} - single point of failure risk"
        elif score >= 70:
            return f"INVEST: High priority investment in {capability.name} - strategic importance with gaps"
        elif score >= 50:
            return f"CONSIDER: Moderate investment in {capability.name} - improvement opportunities"
        else:
            return f"MONITOR: {capability.name} - adequate coverage, monitor for changes"

    def _calculate_portfolio_metrics(self, capability_scores: List[Dict]) -> Dict:
        """Calculate portfolio-level investment metrics."""

        total_capabilities = len(capability_scores)
        critical_count = len([c for c in capability_scores if c["priority_level"] == "CRITICAL"])
        high_count = len([c for c in capability_scores if c["priority_level"] == "HIGH"])

        def _real(items):
            return [c["investment_need"]["estimated_cost"] for c in items
                    if c["investment_need"]["estimated_cost"] is not None]

        # Total of the recorded investment figures only
        costed = _real(capability_scores)
        total_investment = sum(costed) if costed else None

        def _band(level):
            figures = _real(c for c in capability_scores if c["priority_level"] == level)
            return sum(figures) if figures else None

        # A priority level with no recorded figure has no total, not a total of zero
        critical_investment = _band("CRITICAL")
        high_investment = _band("HIGH")
        medium_investment = _band("MEDIUM")
        low_investment = _band("LOW")

        scored = [c for c in capability_scores if c["strategic_score"] is not None]

        def _average(key, items):
            return round(sum(c[key] for c in items) / len(items), 1) if items else None

        avg_strategic_score = _average("strategic_score", scored)
        avg_coverage_score = _average("coverage_score", capability_scores)
        avg_maturity_score = _average("maturity_score", capability_scores)
        avg_risk_score = _average("risk_score", capability_scores)

        return {
            "total_capabilities": total_capabilities,
            "critical_priorities": critical_count,
            "high_priorities": high_count,
            "total_estimated_investment": total_investment,
            "critical_investment": critical_investment,
            "high_investment": high_investment,
            "medium_investment": medium_investment,
            "low_investment": low_investment,
            "investment_costed_count": len(costed),
            "scored_count": len(scored),
            "derived_count": len([c for c in scored if c["strategic_basis"] == "derived"]),
            "average_strategic_score": avg_strategic_score,
            "average_coverage_score": avg_coverage_score,
            "average_maturity_score": avg_maturity_score,
            "average_risk_score": avg_risk_score,
            "investment_currency": "USD",
        }

    def _generate_investment_recommendations(self, capability_scores: List[Dict]) -> List[Dict]:
        """Generate strategic investment recommendations."""

        recommendations = []

        # Top 5 critical investments
        critical_caps = [c for c in capability_scores if c["priority_level"] == "CRITICAL"][:5]

        for cap in critical_caps:
            recommendations.append(
                {
                    "type": "IMMEDIATE_INVESTMENT",
                    "priority": "CRITICAL",
                    "capability": cap["capability_name"],
                    "justification": f"Strategic {cap['effective_importance']} capability with {cap['coverage_count']} applications",
                    "estimated_cost": cap["investment_need"]["estimated_cost"] if cap["investment_need"]["estimated_cost"] is not None else "Not estimated",
                    "timeframe": cap["investment_need"]["timeframe"] or "Not estimated",
                    "expected_roi": "HIGH"
                    if cap["effective_importance"] == "critical"
                    else "MEDIUM",
                }
            )

        # Consolidation opportunities
        over_served = [c for c in capability_scores if c["coverage_count"] >= 5]
        if over_served:
            recommendations.append(
                {
                    "type": "CONSOLIDATION_OPPORTUNITY",
                    "priority": "HIGH",
                    "capability": f"{len(over_served)} capabilities",
                    "justification": "Multiple applications supporting same capabilities - consolidation opportunity",
                    "estimated_cost": "SAVINGS",
                    "timeframe": "6 - 12 months",
                    "expected_roi": "HIGH",
                }
            )

        # Single point of failures
        spf_caps = [
            c
            for c in capability_scores
            if c["coverage_count"] == 1 and c["effective_importance"] in ["critical", "high"]
        ]
        if spf_caps:
            recommendations.append(
                {
                    "type": "RISK_MITIGATION",
                    "priority": "HIGH",
                    "capability": f"{len(spf_caps)} capabilities",
                    "justification": "Single point of failure risks for critical capabilities",
                    "estimated_cost": sum(c["investment_need"]["estimated_cost"] or 0 for c in spf_caps) or "Not estimated",
                    "timeframe": "6 - 12 months",
                    "expected_roi": "HIGH",
                }
            )

        return recommendations
