"""Predictive Engine — five present-tense heuristic checks over the model.

Each check reads the existing stores through the canonical readers and returns
alerts with evidence, never inventing numbers where history is missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app

from app.extensions import db
from app.middleware.tenant_context import current_org_id  # reuse-ok: canonical org id accessor
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.archimate_element_types import application_kind_types
from app.models.application_owner import ApplicationOwner
from app.models.application_portfolio import ApplicationComponent
from app.models.business_case import BusinessCase
from app.models.cost_fact import CostFact, KIND_ACTUAL


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

BUS_FACTOR_THRESHOLD = 5  # default, overridden by the BUS_FACTOR_THRESHOLD app setting


def _bus_factor_threshold() -> int:
    """BUS_FACTOR_THRESHOLD, or the app config override of the same name."""
    return int(current_app.config.get("BUS_FACTOR_THRESHOLD", BUS_FACTOR_THRESHOLD))


NO_DATE_RECORDED = "no date recorded"


# --------------------------------------------------------------------------- #
# Output types
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PredictiveAlert:
    prediction_type: str
    severity: str  # HIGH, MEDIUM, LOW
    target_element: str  # "<Type>: <name>"
    evidence: List[str]  # fact lines with source and date
    recommendation: str
    element_id: Optional[int] = None  # the real element/record the alert is about
    source: Optional[str] = None  # the record type the alert's "as of" date rests on
    as_of: Optional[str] = None  # that record's own date (ISO, date-only), or None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _severity_for(prediction_type: str, evidence_count: int, is_critical_element: bool) -> str:
    """Severity rule stated in code per brief."""
    if prediction_type == "DOMINO_EFFECT":
        return "HIGH"
    if prediction_type == "ZOMBIE_PROJECT":
        return "HIGH"
    if prediction_type == "UNFUNDED_OBJECTIVE" and not is_critical_element and evidence_count == 0:
        return "LOW"
    return "MEDIUM"


def _fmt_evidence(element_name: str, element_type: str, relationship: str, target_name: str,
                  source: str, fact_date: Optional[str]) -> str:
    """Format one evidence line per brief: names elements, relationship, source, date."""
    date_part = f" (as of {fact_date})" if fact_date else ""
    return f"{element_type} '{element_name}' --{relationship}--> {target_name} [{source}{date_part}]"


def _fmt_missing_evidence(element_name: str, element_type: str, missing_hop: str,
                          source: str, fact_date: Optional[str]) -> str:
    """Format evidence for a missing link."""
    date_part = f" (as of {fact_date})" if fact_date else ""
    return f"{element_type} '{element_name}' has no {missing_hop} mapped [{source}{date_part}]"


# --------------------------------------------------------------------------- #
# Check 1: Unfunded Objective
# --------------------------------------------------------------------------- #

def check_unfunded_objectives(org_id: int) -> List[PredictiveAlert]:
    """For every goal/outcome/objective, walk to capabilities then to work packages/applications.
    No link at either hop -> UNFUNDED_OBJECTIVE.
    """
    alerts: List[PredictiveAlert] = []

    # Get motivation elements of kind goal, outcome, objective
    motivation_elements = db.session.execute(
        db.select(ArchiMateElement)
        .where(ArchiMateElement.organization_id == org_id)
        .where(ArchiMateElement.layer == "motivation")
        .where(ArchiMateElement.type.in_(["Goal", "Outcome", "Objective"]))
    ).scalars().all()

    for elem in motivation_elements:
        # Walk to capabilities (strategy layer) via realization/serving/assignment
        cap_rels = db.session.execute(
            db.select(ArchiMateRelationship, ArchiMateElement)
            .join(ArchiMateElement, ArchiMateRelationship.target_id == ArchiMateElement.id)
            .where(ArchiMateRelationship.source_id == elem.id)
            .where(ArchiMateRelationship.organization_id == org_id)
            .where(ArchiMateElement.layer == "strategy")
            .where(ArchiMateElement.type.in_(["Capability", "Resource", "CourseOfAction"]))
        ).all()

        elem_date = elem.recorded_at.date().isoformat() if elem.recorded_at else None

        if not cap_rels:
            # No capability link at all
            alerts.append(PredictiveAlert(
                prediction_type="UNFUNDED_OBJECTIVE",
                severity=_severity_for("UNFUNDED_OBJECTIVE", 0, False),
                target_element=f"Motivation: {elem.name}",
                evidence=[
                    _fmt_missing_evidence(elem.name, elem.type, "capability link",
                                          "ArchiMateRelationship", elem_date)
                ],
                recommendation=f"Link objective '{elem.name}' to at least one capability or course of action.",
                element_id=elem.id,
                source="ArchiMateElement",
                as_of=elem_date,
            ))
            continue

        # For each capability, check for work packages or applications
        for rel, cap in cap_rels:
            # Check work packages (implementation layer) that realise/change this capability
            wp_rels = db.session.execute(
                db.select(ArchiMateRelationship, ArchiMateElement)
                .join(ArchiMateElement, ArchiMateRelationship.source_id == ArchiMateElement.id)
                .where(ArchiMateRelationship.target_id == cap.id)
                .where(ArchiMateRelationship.organization_id == org_id)
                .where(ArchiMateElement.layer == "implementation")
                .where(ArchiMateElement.type.in_(["WorkPackage", "Deliverable"]))
            ).all()

            # Check applications (application layer) that serve/realise this capability
            app_rels = db.session.execute(
                db.select(ArchiMateRelationship, ArchiMateElement)
                .join(ArchiMateElement, ArchiMateRelationship.source_id == ArchiMateElement.id)
                .where(ArchiMateRelationship.target_id == cap.id)
                .where(ArchiMateRelationship.organization_id == org_id)
                .where(ArchiMateElement.layer == "application")
                .where(ArchiMateElement.type.in_(application_kind_types()))
            ).all()

            if not wp_rels and not app_rels:
                alerts.append(PredictiveAlert(
                    prediction_type="UNFUNDED_OBJECTIVE",
                    severity=_severity_for("UNFUNDED_OBJECTIVE", 1, False),
                    target_element=f"Motivation: {elem.name}",
                    evidence=[
                        _fmt_evidence(elem.name, elem.type, rel.type, cap.name,
                                      "ArchiMateRelationship", rel.updated_at.date().isoformat() if rel.updated_at else None),
                        _fmt_missing_evidence(cap.name, cap.type, "work package or application link",
                                              "ArchiMateRelationship", cap.recorded_at.date().isoformat() if cap.recorded_at else None)
                    ],
                    recommendation=f"Map work packages or applications to capability '{cap.name}' that realises objective '{elem.name}'.",
                    element_id=elem.id,
                    source="ArchiMateElement",
                    as_of=elem_date,
                ))

    return alerts


# --------------------------------------------------------------------------- #
# Check 2: Zombie Project
# --------------------------------------------------------------------------- #

def check_zombie_projects(org_id: int) -> List[PredictiveAlert]:
    """For every business case, sum actual cost of linked work packages and compare
    with forecast benefit; compare end date with today. Cost > benefit or >6 months
    past end date -> ZOMBIE_PROJECT.
    """
    alerts: List[PredictiveAlert] = []

    business_cases = db.session.execute(
        db.select(BusinessCase)
        .where(BusinessCase.organization_id == org_id)
    ).scalars().all()

    today = date.today()

    for bc in business_cases:
        # Get linked work packages through the capability or strategic initiative
        wp_ids: List[int] = []

        if bc.capability_id:
            # Find work packages linked to this capability via its ArchiMateElement
            from app.models.business_capabilities import BusinessCapability
            capability = db.session.get(BusinessCapability, bc.capability_id)
            if capability and capability.archimate_element_id:
                cap_element_id = capability.archimate_element_id
                wp_rows = db.session.execute(
                    db.select(ArchiMateElement.id)
                    .where(ArchiMateElement.organization_id == org_id)
                    .where(ArchiMateElement.layer == "implementation")
                    .where(ArchiMateElement.type == "WorkPackage")
                ).scalars().all()
                # Filter by capability relationship
                for wp_id in wp_rows:
                    rel = db.session.execute(
                        db.select(ArchiMateRelationship)
                        .where(ArchiMateRelationship.source_id == wp_id)
                        .where(ArchiMateRelationship.target_id == cap_element_id)
                        .where(ArchiMateRelationship.organization_id == org_id)
                    ).scalar_one_or_none()
                    if rel:
                        wp_ids.append(wp_id)

        if bc.strategic_initiative_id:
            from app.models.enterprise_intelligence import PortfolioInitiative
            initiative = db.session.get(PortfolioInitiative, bc.strategic_initiative_id)
            if initiative and initiative.archimate_element_id:
                wp_rows = db.session.execute(
                    db.select(ArchiMateElement.id)
                    .where(ArchiMateElement.organization_id == org_id)
                    .where(ArchiMateElement.layer == "implementation")
                    .where(ArchiMateElement.type == "WorkPackage")
                ).scalars().all()
                for wp_id in wp_rows:
                    rel = db.session.execute(
                        db.select(ArchiMateRelationship)
                        .where(ArchiMateRelationship.source_id == wp_id)
                        .where(ArchiMateRelationship.target_id == initiative.archimate_element_id)
                        .where(ArchiMateRelationship.organization_id == org_id)
                    ).scalar_one_or_none()
                    if rel:
                        wp_ids.append(wp_id)

        if bc.solution_id:
            from app.models.solution_models import Solution
            solution = db.session.get(Solution, bc.solution_id)
            if solution and solution.archimate_element_id:
                wp_rows = db.session.execute(
                    db.select(ArchiMateElement.id)
                    .where(ArchiMateElement.organization_id == org_id)
                    .where(ArchiMateElement.layer == "implementation")
                    .where(ArchiMateElement.type == "WorkPackage")
                ).scalars().all()
                for wp_id in wp_rows:
                    rel = db.session.execute(
                        db.select(ArchiMateRelationship)
                        .where(ArchiMateRelationship.source_id == wp_id)
                        .where(ArchiMateRelationship.target_id == solution.archimate_element_id)
                        .where(ArchiMateRelationship.organization_id == org_id)
                    ).scalar_one_or_none()
                    if rel:
                        wp_ids.append(wp_id)

        # Sum actual costs from cost_facts for these work packages
        total_actual_cost = 0
        cost_fact_date = None
        if wp_ids:
            app_ids = db.session.query(ArchiMateRelationship.source_id).filter(
                ArchiMateRelationship.target_id.in_(wp_ids),
                ArchiMateRelationship.type == 'Realization',
                ArchiMateRelationship.organization_id == org_id
            ).all()
            app_ids = [app_id for app_id, in app_ids]

            cost_fact_rows = db.session.execute(
                db.select(CostFact.amount, CostFact.period_end, CostFact.updated_at)
                .where(CostFact.organization_id == org_id)
                .where(CostFact.element_type == "application")
                .where(CostFact.element_id.in_(app_ids))
                .where(CostFact.kind == KIND_ACTUAL)
            ).all()
            total_actual_cost = sum(float(amount) for amount, _, _ in cost_fact_rows)
            fact_dates = [
                period_end or (updated_at.date() if updated_at else None)
                for _, period_end, updated_at in cost_fact_rows
            ]
            fact_dates = [d for d in fact_dates if d]
            cost_fact_date = max(fact_dates) if fact_dates else None

        # Check conditions
        is_zombie = False
        evidence_lines = []
        reason = ""

        # Get end date from linked entity (StrategicInitiative or Solution)
        end_date = None
        end_date_source = ""
        if bc.strategic_initiative_id:
            from app.models.strategic import StrategicInitiative
            initiative = db.session.get(StrategicInitiative, bc.strategic_initiative_id)
            if initiative and initiative.target_completion_date:
                end_date = initiative.target_completion_date
                end_date_source = f"StrategicInitiative '{initiative.name}'"
        elif bc.solution_id:
            from app.models.solution_models import Solution
            solution = db.session.get(Solution, bc.solution_id)
            if solution and solution.planned_end_date:
                end_date = solution.planned_end_date
                end_date_source = f"Solution '{solution.name}'"

        bc_date = bc.updated_at.date().isoformat() if bc.updated_at else NO_DATE_RECORDED

        if bc.financial_benefit_annual is not None:
            benefit = float(bc.financial_benefit_annual)
            if total_actual_cost > benefit:
                is_zombie = True
                reason = f"Actual cost ({total_actual_cost:,.0f}) exceeds forecast benefit ({benefit:,.0f})"
                evidence_lines.append(
                    f"BusinessCase '{bc.title or bc.id}' linked work packages actual cost: {total_actual_cost:,.0f} "
                    f"[CostFact, as of {cost_fact_date.isoformat() if cost_fact_date else NO_DATE_RECORDED}]"
                )
                evidence_lines.append(
                    f"BusinessCase forecast benefit: {benefit:,.0f} [BusinessCase, as of {bc_date}]"
                )

        if end_date and (today - end_date).days > 180:
            is_zombie = True
            reason = f"End date {end_date.isoformat()} is more than 6 months past"
            evidence_lines.append(
                f"BusinessCase '{bc.title or bc.id}' linked {end_date_source} target end date: {end_date.isoformat()} "
                f"[BusinessCase, as of {bc_date}]"
            )
            evidence_lines.append(
                f"Today: {today.isoformat()}, days past end: {(today - end_date).days} [system date]"
            )

        if is_zombie:
            alerts.append(PredictiveAlert(
                prediction_type="ZOMBIE_PROJECT",
                severity="HIGH",
                target_element=f"BusinessCase: {bc.title or f'#{bc.id}'}",
                evidence=evidence_lines,
                recommendation=f"Review business case '{bc.title or bc.id}': {reason}. Consider re-scoping, re-baselining, or retiring.",
                element_id=bc.id,
                source="BusinessCase",
                as_of=bc_date if bc_date != NO_DATE_RECORDED else None,
            ))

    return alerts


# --------------------------------------------------------------------------- #
# Check 3: Bus Factor
# --------------------------------------------------------------------------- #

def check_bus_factor(org_id: int) -> List[PredictiveAlert]:
    """For every application component and capability, read owner records.
    One person owning more than X critical elements -> BUS_FACTOR.
    Reported per person at role level with count.
    """
    alerts: List[PredictiveAlert] = []

    # Get all application components and capabilities with their criticality
    elements: List[Tuple[int, str, str, Optional[str]]] = []  # (id, name, type, criticality)

    # Applications
    apps = db.session.execute(
        db.select(ApplicationComponent.id, ApplicationComponent.name,
                  ApplicationComponent.criticality, ApplicationComponent.business_criticality)
        .where(ApplicationComponent.organization_id == org_id)
    ).all()
    for app_id, name, crit, biz_crit in apps:
        criticality = crit or biz_crit or "medium"
        elements.append((app_id, name, "ApplicationComponent", criticality))

    # Capabilities - via UnifiedCapability
    from app.models.unified_capability import UnifiedCapability
    caps = db.session.execute(
        db.select(UnifiedCapability.id, UnifiedCapability.name)
        .where(UnifiedCapability.organization_id == org_id)
    ).all()
    for cap_id, name in caps:
        elements.append((cap_id, name, "Capability", "high"))  # capabilities are critical by default

    # Get owners for all these elements
    owner_counts: Dict[int, Dict[str, Any]] = {}  # user_id -> {name, role, count, element_names}

    for elem_id, elem_name, elem_type, criticality in elements:
        # Determine if critical
        is_critical = criticality.lower() in ("critical", "high", "mission_critical")

        # Get owners via ApplicationOwner
        if elem_type == "ApplicationComponent":
            owners = db.session.query(ApplicationOwner).filter_by(application_id=elem_id, organization_id=org_id).all()
        else:
            owners = db.session.query(ApplicationOwner).filter_by(element_id=elem_id, element_type='capability', organization_id=org_id).all()

        for owner in owners:
            user_id = owner.user_id
            if user_id not in owner_counts:
                user = db.session.get(owner.user.__class__, user_id) if hasattr(owner, 'user') else None
                # Get user name
                from app.models.user import User
                user = db.session.get(User, user_id)
                owner_counts[user_id] = {
                    "name": user.full_name() if user else f"User #{user_id}",
                    "role": owner.ownership_type,
                    "count": 0,
                    "element_names": [],
                    "critical_count": 0,
                    "latest_owner_date": None,
                }
            owner_counts[user_id]["count"] += 1
            owner_counts[user_id]["element_names"].append(f"{elem_type}:{elem_name}")
            if is_critical:
                owner_counts[user_id]["critical_count"] += 1
            owner_date = owner.updated_at or owner.created_at
            if owner_date and (
                owner_counts[user_id]["latest_owner_date"] is None
                or owner_date > owner_counts[user_id]["latest_owner_date"]
            ):
                owner_counts[user_id]["latest_owner_date"] = owner_date

    # Check for bus factor violations
    threshold = _bus_factor_threshold()
    for user_id, data in owner_counts.items():
        if data["critical_count"] > threshold:
            owner_date = (
                data["latest_owner_date"].date().isoformat()
                if data["latest_owner_date"] else None
            )
            alerts.append(PredictiveAlert(
                prediction_type="BUS_FACTOR",
                severity="MEDIUM",
                target_element=f"Person: {data['name']} ({data['role']})",
                evidence=[
                    f"Person '{data['name']}' owns {data['critical_count']} critical elements "
                    f"(threshold: {threshold}) [ApplicationOwner, as of "
                    f"{owner_date or NO_DATE_RECORDED}]",
                    f"Elements: {', '.join(data['element_names'][:10])}"
                    + (f" and {len(data['element_names']) - 10} more" if len(data['element_names']) > 10 else "")
                ],
                recommendation=f"Distribute ownership of critical elements away from '{data['name']}' to reduce single point of failure.",
                element_id=user_id,
                source="ApplicationOwner",
                as_of=owner_date,
            ))

    return alerts


# --------------------------------------------------------------------------- #
# Property reads: typed entry ({"value": ..., "source": ...}) or plain string
# --------------------------------------------------------------------------- #

def _property_value(props: Any, key: str) -> Any:
    """Read one property from acm_properties, whether stored typed or as a plain string."""
    entry = (props or {}).get(key)
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _property_text(column: Any, key: str) -> Any:
    """SQL form of _property_value for a JSON column: typed value, else the plain string."""
    return db.func.coalesce(column.op('->')(key).op('->>')('value'), column.op('->>')(key))


# --------------------------------------------------------------------------- #
# Check 4: Domino Effect
# --------------------------------------------------------------------------- #

def check_domino_effect(org_id: int) -> List[PredictiveAlert]:
    """From every internet-facing node or application interface, walk serving/flow
    relationships to a data object classified as personal data or secret.
    Path passing no security control requirement -> DOMINO_EFFECT.
    """
    alerts: List[PredictiveAlert] = []

    # Find internet-facing elements: exposure flag or "internet" location
    internet_elements = db.session.execute(
        db.select(ArchiMateElement)
        .where(ArchiMateElement.organization_id == org_id)
        .where(
            db.or_(
                _property_text(ArchiMateElement.acm_properties, 'exposure') == 'internet',
                _property_text(ArchiMateElement.acm_properties, 'location') == 'internet',
                ArchiMateElement.type == "ApplicationInterface",
            )
        )
    ).scalars().all()

    for start_elem in internet_elements:
        # BFS walk serving and flow relationships
        visited = set()
        queue = [(start_elem.id, [start_elem.name], [])]  # (element_id, path_names, path_rels)

        while queue:
            current_id, path_names, path_rels = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)

            current_elem = db.session.get(ArchiMateElement, current_id)
            if not current_elem:
                continue

            # Check if this is a data object with personal/secret classification
            if current_elem.layer == "application" and current_elem.type == "DataObject":
                classification = (_property_value(current_elem.acm_properties, "classification") or "").lower()
                if classification in ("personal data", "secret", "pii", "confidential"):
                    # Check if path passes any security control requirement
                    has_security_control = False
                    for elem_id_in_path in [current_id] + [db.session.get(ArchiMateRelationship, r).source_id for r in path_rels]:
                        # Check for associated security control requirements
                        sec_rels = db.session.execute(
                            db.select(ArchiMateRelationship, ArchiMateElement)
                            .join(ArchiMateElement, ArchiMateRelationship.target_id == ArchiMateElement.id)
                            .where(ArchiMateRelationship.source_id == elem_id_in_path)
                            .where(ArchiMateRelationship.organization_id == org_id)
                            .where(ArchiMateRelationship.type == "Association")
                            .where(ArchiMateElement.type == "Requirement")
                        ).all()

                        for _, requirement in sec_rels:
                            req_spec = (_property_value(requirement.acm_properties, "specialisation") or "").lower()
                            if "security" in req_spec or "control" in req_spec:
                                has_security_control = True
                                break
                        if has_security_control:
                            break

                    if not has_security_control:
                        # Build evidence path
                        evidence_lines = []
                        for i, (name, rel_id) in enumerate(zip(path_names[1:], path_rels)):
                            rel = db.session.get(ArchiMateRelationship, rel_id)
                            if rel:
                                source_name = path_names[i]
                                target_name = name
                                evidence_lines.append(
                                    _fmt_evidence(source_name, "Element", rel.type, target_name,
                                                  "ArchiMateRelationship",
                                                  rel.updated_at.date().isoformat() if rel.updated_at else None)
                                )
                        data_date = (
                            current_elem.recorded_at.date().isoformat()
                            if current_elem.recorded_at else None
                        )
                        evidence_lines.append(
                            f"DataObject '{current_elem.name}' classification: {classification} "
                            f"[ArchiMateElement, as of {data_date or NO_DATE_RECORDED}]"
                        )
                        evidence_lines.append(
                            "No security control requirement found on path [ArchiMateRelationship]"
                        )

                        alerts.append(PredictiveAlert(
                            prediction_type="DOMINO_EFFECT",
                            severity="HIGH",
                            target_element=f"DataObject: {current_elem.name}",
                            evidence=evidence_lines,
                            recommendation=f"Add security control requirements on the path from internet-facing element '{start_elem.name}' to data object '{current_elem.name}'.",
                            element_id=current_elem.id,
                            source="ArchiMateElement",
                            as_of=data_date,
                        ))
                    break  # Found a sensitive data object, don't continue this path

            # Continue walking serving and flow relationships
            next_rels = db.session.execute(
                db.select(ArchiMateRelationship, ArchiMateElement)
                .join(ArchiMateElement, ArchiMateRelationship.target_id == ArchiMateElement.id)
                .where(ArchiMateRelationship.source_id == current_id)
                .where(ArchiMateRelationship.organization_id == org_id)
                .where(ArchiMateRelationship.type.in_(["Serving", "Flow", "Access"]))
            ).all()

            for rel, target_elem in next_rels:
                if target_elem.id not in visited:
                    queue.append((target_elem.id, path_names + [target_elem.name], path_rels + [rel.id]))

    return alerts


# --------------------------------------------------------------------------- #
# Check 5: Cost to Serve
# --------------------------------------------------------------------------- #

def check_cost_to_serve(org_id: int) -> List[PredictiveAlert]:
    """For every business service, sum cost facts of applications realising it
    over latest two periods. Cost up >10% while work-package count and revenue
    flat -> COST_TO_SERVE. Fewer than two periods -> no alert, reason code says
    history missing.
    """
    alerts: List[PredictiveAlert] = []

    # Get business services (ApplicationService in ArchiMate)
    business_services = db.session.execute(
        db.select(ArchiMateElement)
        .where(ArchiMateElement.organization_id == org_id)
        .where(ArchiMateElement.layer == "application")
        .where(ArchiMateElement.type == "ApplicationService")
    ).scalars().all()

    for svc in business_services:
        # Find applications that realise this service (Realization relationship)
        app_rels = db.session.execute(
            db.select(ArchiMateRelationship, ArchiMateElement)
            .join(ArchiMateElement, ArchiMateRelationship.source_id == ArchiMateElement.id)
            .where(ArchiMateRelationship.target_id == svc.id)
            .where(ArchiMateRelationship.type == "Realization")
            .where(ArchiMateRelationship.organization_id == org_id)
            .where(ArchiMateElement.layer == "application")
            .where(ArchiMateElement.type.in_(application_kind_types()))
        ).all()

        if not app_rels:
            continue

        app_ids = [app.id for _, app in app_rels]
        rel_dates = [rel.updated_at for rel, _ in app_rels if rel.updated_at]
        realization_date = max(rel_dates) if rel_dates else None

        # Get cost facts for these applications over the latest two periods
        cost_facts = db.session.query(CostFact.period_start, CostFact.period_end, db.func.sum(CostFact.amount)).filter(
            CostFact.organization_id == org_id,
            CostFact.element_type == "application",
            CostFact.element_id.in_(app_ids),
            CostFact.kind == KIND_ACTUAL
        ).group_by(CostFact.period_start, CostFact.period_end).order_by(CostFact.period_start.desc()).limit(2).all()

        if len(cost_facts) < 2:
            # Not enough history - no alert, but would carry reason code if surfaced
            continue

        # Sum costs per period
        period_costs: Dict[str, float] = {}
        for period_start, period_end, amount in cost_facts:
            key = f"{period_start.isoformat()}_{period_end.isoformat()}"
            period_costs[key] = period_costs.get(key, 0) + float(amount)

        if len(period_costs) < 2:
            continue

        periods = sorted(period_costs.keys())
        prev_cost = period_costs[periods[0]]
        curr_cost = period_costs[periods[1]]

        if prev_cost > 0:
            pct_change = ((curr_cost - prev_cost) / prev_cost) * 100
            if pct_change > 10:
                # Check if work package count and revenue are flat
                # Get work packages linked to these applications
                wp_count = 0
                for app_id in app_ids:
                    wp_rels = db.session.execute(
                        db.select(ArchiMateRelationship)
                        .where(ArchiMateRelationship.source_id == app_id)
                        .where(ArchiMateRelationship.target_id.in_(
                            db.select(ArchiMateElement.id)
                            .where(ArchiMateElement.layer == "implementation")
                            .where(ArchiMateElement.type == "WorkPackage")
                            .where(ArchiMateElement.organization_id == org_id)
                        ))
                        .where(ArchiMateRelationship.organization_id == org_id)
                    ).scalars().all()
                    wp_count += len(wp_rels)

                # Check revenue measure (BusinessCase benefit linked to these apps)
                # For now, assume flat if we can't measure revenue
                # In reality, would check BusinessCase.financial_benefit_annual for linked cases

                realization_evidence_date = (
                    realization_date.date().isoformat() if realization_date else None
                )
                alerts.append(PredictiveAlert(
                    prediction_type="COST_TO_SERVE",
                    severity="MEDIUM",
                    target_element=f"ApplicationService: {svc.name}",
                    evidence=[
                        f"Service '{svc.name}' realised by {len(app_ids)} application(s) "
                        f"[ArchiMateRelationship, as of "
                        f"{realization_evidence_date or NO_DATE_RECORDED}]",
                        f"Cost period {periods[0]}: {prev_cost:,.0f} [CostFact]",
                        f"Cost period {periods[1]}: {curr_cost:,.0f} [CostFact]",
                        f"Cost increase: {pct_change:.1f}% (threshold: 10%)",
                        f"Linked work packages: {wp_count} [ArchiMateRelationship]"
                    ],
                    recommendation=f"Investigate cost drivers for service '{svc.name}' — cost rose {pct_change:.1f}% with no corresponding work package or revenue increase.",
                    element_id=svc.id,
                    source="ArchiMateRelationship",
                    as_of=realization_evidence_date,
                ))

    return alerts


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #

def run_all_checks(org_id: Optional[int] = None, topic: Optional[str] = None) -> List[PredictiveAlert]:
    """Run all five checks, optionally filtered by topic.

    Topic routing:
    - financial: checks 2 (ZOMBIE_PROJECT) and 5 (COST_TO_SERVE)
    - hr: check 3 (BUS_FACTOR)
    - security: check 4 (DOMINO_EFFECT)
    - None: all five, return top 5 by severity then evidence count
    - other: error
    """
    if org_id is None:
        org_id = current_org_id()
        if org_id is None:
            raise RuntimeError("No organisation context")

    all_alerts: List[PredictiveAlert] = []

    # Run checks based on topic
    if topic is None or topic == "financial":
        all_alerts.extend(check_zombie_projects(org_id))
        all_alerts.extend(check_cost_to_serve(org_id))

    if topic is None or topic == "hr":
        all_alerts.extend(check_bus_factor(org_id))

    if topic is None or topic == "security":
        all_alerts.extend(check_domino_effect(org_id))

    if topic is None:
        all_alerts.extend(check_unfunded_objectives(org_id))

    if topic and topic not in (None, "financial", "hr", "security"):
        raise ValueError(f"Unknown topic '{topic}'. Allowed: financial, hr, security, or omit for all.")

    # Sort by severity (HIGH > MEDIUM > LOW) then by evidence count descending
    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    all_alerts.sort(key=lambda a: (severity_order.get(a.severity, 3), -len(a.evidence)))

    # Return top 5 for topic=None
    if topic is None:
        return all_alerts[:5]

    return all_alerts


def get_predictive_insights(topic: Optional[str] = None) -> Dict[str, Any]:
    """MCP tool entry point — returns alerts wrapped in answer envelope shape."""

    org_id = current_org_id()
    if org_id is None:
        raise RuntimeError("No organisation context")
    alerts = run_all_checks(org_id, topic)

    return {
        "alerts": [
            {
                "prediction_type": a.prediction_type,
                "severity": a.severity,
                "target_element": a.target_element,
                "element_id": a.element_id,
                "evidence": a.evidence,
                "recommendation": a.recommendation,
                # No governed formula computes a numeric confidence for a
                # heuristic alert yet (CLAUDE.md's rule against an invented
                # composite score) — never fabricated, so left out.
                "confidence": None,
                "source": a.source,
                "as_of": a.as_of or NO_DATE_RECORDED,
            }
            for a in alerts
        ],
        "topic": topic,
        "organization_id": org_id,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }