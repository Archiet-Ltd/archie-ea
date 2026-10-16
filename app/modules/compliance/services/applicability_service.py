"""
Applicability rule engine for regulatory frameworks.

Evaluates which systems (nodes, applications) are in scope for a given
regulatory framework based on rules over data classification, hosting
location, and business function.  Results are stored for audit.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app import db
from app.models.application_compliance import ApplicationComplianceControl
from app.models.compliance_models import ComplianceControl, RegulatoryFramework
from app.models.regulatory_change import RegulatoryChange, RegulatoryChangeImpact
from app.models.regulatory_framework import FrameworkAdoption
from app.models.technology_layer import Node
from app.modules.compliance.services.control_evidence import evidence_state, has_usable_evidence_link

logger = logging.getLogger(__name__)


def _now_utc_naive():
    """Return naive UTC datetime for TIMESTAMP WITHOUT TIME ZONE columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ApplicabilityService:
    """Evaluates which elements are in scope for a regulatory framework."""

    # Framework-specific applicability rules.
    # Each rule is a dict with:
    #   field: the model attribute to check
    #   operator: 'eq', 'contains', 'in'
    #   value: the value to match
    FRAMEWORK_RULES: Dict[str, List[Dict[str, Any]]] = {
        "ISO-27001": [
            {"field": "node_type", "operator": "in",
             "value": ["Virtual Machine", "Cloud Instance", "Physical Server", "Container"]},
        ],
        "SOC-2": [
            {"field": "deployment_model", "operator": "in",
             "value": ["Cloud", "Hybrid"]},
        ],
        "DORA": [
            {"field": "compliance_tags", "operator": "contains", "value": "DORA"},
        ],
    }

    @staticmethod
    def rule_matches(value: Any, rule: Dict[str, Any]) -> bool:
        """Whether *value* satisfies one ``{"operator": ..., "value": ...}`` rule.

        ``eq``: equal. ``in``: a member of the rule's list. ``contains``: the rule's value is
        one of the tags held in a JSON text field or list. ``mentions_any``: any item of the
        value (a string or a list of strings) contains any of the rule's terms, ignoring case.
        A missing value matches nothing; an unknown operator excludes nothing.
        """
        if value is None:
            return False
        op = rule["operator"]
        if op == "eq":
            return value == rule["value"]
        if op == "in":
            return value in rule["value"]
        if op == "contains":
            # compliance_tags is a JSON text field
            import json
            try:
                tags = json.loads(value) if isinstance(value, str) else value
            except (json.JSONDecodeError, TypeError):
                tags = []
            return rule["value"] in tags
        if op == "mentions_any":
            items = value if isinstance(value, (list, tuple, set)) else [value]
            return any(term in str(item).lower() for item in items for term in rule["value"])
        return True

    @classmethod
    def evaluate_node(cls, node: Node, framework_code: str) -> bool:
        """Return True if *node* is in scope for *framework_code*."""
        rules = cls.FRAMEWORK_RULES.get(framework_code, [])
        if not rules:
            return True  # no rules = everything in scope

        for rule in rules:
            if not cls.rule_matches(getattr(node, rule["field"], None), rule):
                return False
        return True

    # ------------------------------------------------------------------ #
    # Privacy-impact-assessment flags
    # ------------------------------------------------------------------ #

    ASSESSMENT_STATUS_KEY = "privacy_assessment_status"
    ASSESSMENT_STATUSES = ("not_started", "in_progress", "completed", "not_required")

    # A fixed list in code, over recorded facts only: no score and no weighting.
    PRIVACY_ASSESSMENT_CRITERIA: List[Dict[str, Any]] = [
        {
            "key": "special_category_data",
            "label": "Special-category data",
            "rule": {"field": "data_categories", "operator": "mentions_any", "value": [
                "health", "biometric", "genetic", "racial", "ethnic", "political", "religio",
                "philosoph", "trade union", "sex life", "sexual orientation", "criminal",
            ]},
        },
        {
            "key": "automated_evaluation",
            "label": "Automated evaluation by an AI system",
            "rule": {"field": "uses_ai_system", "operator": "eq", "value": True},
        },
        {
            "key": "vulnerable_data_subjects",
            "label": "Vulnerable data subjects",
            "rule": {"field": "data_categories", "operator": "mentions_any",
                     "value": ["child", "patient", "employee"]},
        },
    ]

    @classmethod
    def privacy_assessment_flags(cls, organization_id: Optional[int]) -> List[Dict[str, Any]]:
        """The organisation's processing activities that meet at least one impact-assessment
        criterion, with the criteria named, the facts that met each, the reading and the
        recorded assessment status.

        One criterion met reads "assessment to consider"; two or more read "assessment likely
        required". Facts come from the data slice of the model (data categories on the
        information object, the AI systems that access it); an activity with no recorded
        category and no AI system is absent, never guessed at.
        """
        from app.models.models import ArchiMateElement
        from app.modules.codegen.services.genome_data_slice import build_data_genome_slice
        from app.modules.enterprise_genome.services.ai_system_profile import is_ai_system

        if organization_id is None:
            return []
        activities = build_data_genome_slice(organization_id).get("processing_activities", [])
        element_ids = sorted(
            {a["provenance"]["archimate_element_id"] for a in activities}
            | {s["archimate_element_id"] for a in activities for s in a.get("systems", [])}
        )
        elements = {}
        if element_ids:
            elements = {
                e.id: e
                for e in db.session.execute(
                    db.select(ArchiMateElement)
                    .where(ArchiMateElement.organization_id == organization_id)
                    .where(ArchiMateElement.id.in_(element_ids))
                ).scalars()
            }
        flags: List[Dict[str, Any]] = []
        for activity in activities:
            element_id = activity["provenance"]["archimate_element_id"]
            categories = list(activity.get("data_categories") or [])
            ai_names = [
                s["name"]
                for s in activity.get("systems", [])
                if elements.get(s["archimate_element_id"]) is not None
                and is_ai_system(elements[s["archimate_element_id"]])
            ]
            facts = {"data_categories": categories, "uses_ai_system": bool(ai_names)}
            met = []
            for criterion in cls.PRIVACY_ASSESSMENT_CRITERIA:
                rule = criterion["rule"]
                value = facts.get(rule["field"]) or None
                if not cls.rule_matches(value, rule):
                    continue
                if rule["field"] == "uses_ai_system":
                    shown = list(ai_names)
                else:
                    shown = [c for c in categories if any(t in str(c).lower() for t in rule["value"])]
                met.append({"key": criterion["key"], "label": criterion["label"], "facts": shown})
            if not met:
                continue
            element = elements.get(element_id)
            recorded = ((getattr(element, "acm_properties", None) or {}).get(cls.ASSESSMENT_STATUS_KEY) or {}).get("value")
            flags.append({
                "activity_name": activity["name"],
                "element_id": element_id,
                "criteria": met,
                "reading": "assessment likely required" if len(met) >= 2 else "assessment to consider",
                "status": recorded if recorded in cls.ASSESSMENT_STATUSES else None,
            })
        return flags

    @classmethod
    def set_privacy_assessment_status(
        cls, organization_id: Optional[int], element_id: int, status: str
    ):
        """Record the assessment status on a processing activity through the typed property
        writer. The element must be one of the organisation's own information objects."""
        from app.models.models import ArchiMateElement
        from app.modules.architecture_assistant.property_service import PropertyService
        from app.modules.codegen.services.genome_data_slice import DATA_OBJECT_TYPES, _norm_type

        if organization_id is None:
            raise LookupError("no organisation")
        if status not in cls.ASSESSMENT_STATUSES:
            raise ValueError("status must be one of " + ", ".join(cls.ASSESSMENT_STATUSES))
        element = db.session.execute(
            db.select(ArchiMateElement)
            .where(ArchiMateElement.id == element_id)
            .where(ArchiMateElement.organization_id == organization_id)
        ).scalar_one_or_none()
        if element is None:
            raise LookupError(str(element_id))
        if _norm_type(element.type) not in DATA_OBJECT_TYPES:
            raise ValueError("That element is not a processing activity")
        PropertyService().set_element_property(element, cls.ASSESSMENT_STATUS_KEY, status, source="user")
        db.session.flush()
        return element

    @classmethod
    def get_in_scope_nodes(
        cls, organization_id: int, framework_code: str
    ) -> List[Dict[str, Any]]:
        """Return nodes in scope for *framework_code* within *organization_id*."""
        nodes = Node.query.filter_by(organization_id=organization_id).all()
        in_scope = []
        for node in nodes:
            if cls.evaluate_node(node, framework_code):
                in_scope.append({
                    "id": node.id,
                    "name": node.name,
                    "node_type": node.node_type,
                    "deployment_model": node.deployment_model,
                    "rule_applied": str(cls.FRAMEWORK_RULES.get(framework_code, [])),
                })
        return in_scope

    @classmethod
    def adopt_framework(
        cls, organization_id: int, framework_id: int, adopted_by_id: int
    ) -> FrameworkAdoption:
        """Adopt a framework for an organisation, copying its controls.

        Returns the new FrameworkAdoption row.
        """
        framework = RegulatoryFramework.query.get(framework_id)
        if framework is None:
            raise ValueError(f"Framework {framework_id} not found")

        existing = FrameworkAdoption.query.filter_by(
            organization_id=organization_id, framework_id=framework_id
        ).first()
        if existing:
            raise ValueError(f"Framework {framework.code} already adopted")

        adoption = FrameworkAdoption(
            organization_id=organization_id,
            scope="tenant",
            framework_id=framework_id,
            adopted_by_id=adopted_by_id,
            adopted_at=_now_utc_naive(),
            status="active",
        )
        db.session.add(adoption)
        db.session.flush()

        for control in framework.controls:
            adopted = ApplicationComplianceControl(
                organization_id=organization_id,
                adoption_id=adoption.id,
                control_id=control.id,
                implementation_status="planned",
            )
            db.session.add(adopted)

        db.session.commit()
        return adoption

    @classmethod
    def propose_harmonization(
        cls, control_id: int, target_control_id: int, notes: str = None
    ) -> ComplianceControl:
        """Propose a cross-framework harmonisation between two controls."""
        control = ComplianceControl.query.get(control_id)
        if control is None:
            raise ValueError(f"Control {control_id} not found")
        target = ComplianceControl.query.get(target_control_id)
        if target is None:
            raise ValueError(f"Target control {target_control_id} not found")

        control.harmonized_control_id = target_control_id
        control.harmonization_status = "proposed"
        control.harmonization_notes = notes
        db.session.commit()
        return control

    @classmethod
    def confirm_harmonization(cls, control_id: int) -> ComplianceControl:
        """Confirm a proposed harmonisation."""
        control = ComplianceControl.query.get(control_id)
        if control is None:
            raise ValueError(f"Control {control_id} not found")
        if control.harmonization_status != "proposed":
            raise ValueError("Only proposed harmonisations can be confirmed")
        control.harmonization_status = "confirmed"
        db.session.commit()
        return control

    @classmethod
    def record_regulatory_change(
        cls,
        organization_id: int,
        framework_id: int,
        title: str,
        description: str,
        change_type: str = "amendment",
        effective_date: Optional[datetime] = None,
        recorded_by_id: Optional[int] = None,
    ) -> RegulatoryChange:
        """Record a regulatory amendment and compute affected elements."""
        change = RegulatoryChange(
            organization_id=organization_id,
            framework_id=framework_id,
            change_type=change_type,
            title=title,
            description=description,
            effective_date=effective_date,
            recorded_by_id=recorded_by_id,
        )
        db.session.add(change)
        db.session.flush()

        # Re-run applicability: find affected controls and element owners
        framework = RegulatoryFramework.query.get(framework_id)
        if framework:
            for control in framework.controls:
                impact = RegulatoryChangeImpact(
                    organization_id=organization_id,
                    change_id=change.id,
                    element_type="control",
                    element_id=control.id,
                    element_name=f"{control.control_code}: {control.title}",
                    impact_assessment=f"Control may be affected by regulatory change: {title}",
                )
                db.session.add(impact)

        db.session.commit()
        return change

    @classmethod
    def evidence_status(
        cls, organization_id: int, adoption_id: int
    ) -> List[Dict[str, Any]]:
        """Return evidence status for each control in an adoption.

        For each control, returns its status and evidence.  If a control has no
        evidence of its own but a CONFIRMED harmonised control (either direction)
        whose ApplicationComplianceControl in the SAME organisation has evidence,
        reports that evidence with a marker naming the source control and
        framework.  Never reads another organisation's rows.
        """
        from app.models.application_compliance import ApplicationComplianceControl
        from app.models.compliance_models import ComplianceControl

        adoption = FrameworkAdoption.query.filter_by(
            id=adoption_id, organization_id=organization_id
        ).first()
        if adoption is None:
            raise ValueError(
                f"Adoption {adoption_id} not found for organisation {organization_id}"
            )

        controls = ApplicationComplianceControl.query.filter_by(
            organization_id=organization_id,
            adoption_id=adoption_id,
        ).all()

        results: List[Dict[str, Any]] = []
        for ac in controls:
            control = ac.control
            entry: Dict[str, Any] = {
                "id": ac.id,
                "control_id": control.id,
                "control_code": control.control_code,
                "title": control.title,
                "implementation_status": ac.implementation_status,
                "evidence_url": ac.evidence_url,
                "evidence_state": evidence_state(ac),
                "evidence_valid_until": (
                    ac.evidence_valid_until.isoformat() if ac.evidence_valid_until else None
                ),
                "notes": ac.notes,
                "evidence_source": None,
            }

            has_own_evidence = has_usable_evidence_link(ac)

            if not has_own_evidence:
                source = None

                # Forward: this control points to a harmonised partner
                if (
                    control.harmonization_status == "confirmed"
                    and control.harmonized_control_id is not None
                ):
                    partner = control.harmonized_control
                    if partner is not None:
                        partner_ac = ApplicationComplianceControl.query.filter_by(
                            organization_id=organization_id,
                            control_id=partner.id,
                        ).first()
                        if (
                            partner_ac is not None
                            and has_usable_evidence_link(partner_ac)
                        ):
                            source = {
                                "control_id": partner.id,
                                "control_code": partner.control_code,
                                "framework_code": (
                                    partner.framework.code
                                    if partner.framework
                                    else None
                                ),
                                "evidence_url": partner_ac.evidence_url,
                                "notes": partner_ac.notes,
                            }

                # Backward: another control points to this one
                if source is None:
                    partners = ComplianceControl.query.filter_by(
                        harmonized_control_id=control.id,
                        harmonization_status="confirmed",
                    ).all()
                    for partner in partners:
                        partner_ac = ApplicationComplianceControl.query.filter_by(
                            organization_id=organization_id,
                            control_id=partner.id,
                        ).first()
                        if (
                            partner_ac is not None
                            and has_usable_evidence_link(partner_ac)
                        ):
                            source = {
                                "control_id": partner.id,
                                "control_code": partner.control_code,
                                "framework_code": (
                                    partner.framework.code
                                    if partner.framework
                                    else None
                                ),
                                "evidence_url": partner_ac.evidence_url,
                                "notes": partner_ac.notes,
                            }
                            break

                if source is not None:
                    entry["evidence_url"] = source["evidence_url"]
                    entry["notes"] = source["notes"]
                    entry["evidence_source"] = {
                        "control_id": source["control_id"],
                        "control_code": source["control_code"],
                        "framework_code": source["framework_code"],
                    }

            results.append(entry)

        return results

    @classmethod
    def get_affected_elements(
        cls, change_id: int, organization_id: int
    ) -> List[RegulatoryChangeImpact]:
        """Return elements affected by a regulatory change."""
        return RegulatoryChangeImpact.query.filter_by(
            change_id=change_id, organization_id=organization_id
        ).all()