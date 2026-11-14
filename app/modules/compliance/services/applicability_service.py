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
from app.models.application_portfolio import ApplicationComponent
from app.models.regulatory_framework import FrameworkAdoption
from app.models.technology_layer import Node
from app.services.compliance.earlier_catalogue_seeds import article_order

logger = logging.getLogger(__name__)


class NotFoundInOrganisation(LookupError):
    """The record does not exist for the organisation asking; another organisation's record answers the same."""


class FrameworkNotAdopted(ValueError):
    """The requirement belongs to a framework the organisation has not adopted."""


IMPLEMENTATION_STATUSES = ("planned", "in_progress", "implemented", "verified", "not_applicable", "waived")


def _require_organisation(organization_id):
    if organization_id is None:
        raise ValueError("An organisation is required.")


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

    @classmethod
    def evaluate_node(cls, node: Node, framework_code: str) -> bool:
        """Return True if *node* is in scope for *framework_code*."""
        rules = cls.FRAMEWORK_RULES.get(framework_code, [])
        if not rules:
            return True  # no rules = everything in scope

        for rule in rules:
            field_val = getattr(node, rule["field"], None)
            if field_val is None:
                return False
            op = rule["operator"]
            if op == "eq" and field_val != rule["value"]:
                return False
            if op == "in" and field_val not in rule["value"]:
                return False
            if op == "contains":
                # compliance_tags is a JSON text field
                import json
                try:
                    tags = json.loads(field_val) if isinstance(field_val, str) else field_val
                except (json.JSONDecodeError, TypeError):
                    tags = []
                if rule["value"] not in tags:
                    return False
        return True

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
        cls, organization_id: int, framework_id: int, adopted_by_id: int, source: Optional[str] = None
    ) -> FrameworkAdoption:
        """Adopt a framework for an organisation, copying its controls. ``source`` says where the
        adoption came from and is saved with it.

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
            source=source,
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
    def top_up_adoption(cls, organization_id: int, framework_id: int) -> int:
        """Give an adopted framework the controls its catalogue gained since it was adopted; returns how many.

        Rows of the catalogue that still read as an earlier release loaded them are first brought to the
        current catalogue (title, heading, reference, description); a row anyone has edited is left alone.
        """
        # This runs from the job queue as well as from requests, where no tenant filter is applied, so the
        # organisation is named in every query here.
        adoption = FrameworkAdoption.query.filter_by(organization_id=organization_id, framework_id=framework_id).first()
        if adoption is None:
            return 0
        from app.services.compliance.regulatory_framework_service import RegulatoryFrameworkService

        RegulatoryFrameworkService.correct_unedited_controls(framework_id)
        held = {
            row.control_id
            for row in ApplicationComplianceControl.query.filter_by(organization_id=organization_id, adoption_id=adoption.id)
        }
        added = 0
        for control in ComplianceControl.query.filter_by(framework_id=framework_id):
            if control.id not in held:
                db.session.add(ApplicationComplianceControl(
                    organization_id=organization_id, adoption_id=adoption.id, control_id=control.id,
                    implementation_status="planned",
                ))
                added += 1
        db.session.commit()
        return added

    @classmethod
    def requirements_to_link(cls, organization_id: int) -> List[Dict[str, Any]]:
        """The requirements the organisation holds through its adopted frameworks, framework by framework."""
        rows = (
            db.session.query(ComplianceControl, RegulatoryFramework)
            .join(ApplicationComplianceControl, ApplicationComplianceControl.control_id == ComplianceControl.id)
            .join(RegulatoryFramework, RegulatoryFramework.id == ComplianceControl.framework_id)
            .filter(
                ApplicationComplianceControl.organization_id == organization_id,
                ApplicationComplianceControl.application_id.is_(None),
                ApplicationComplianceControl.adoption_id.isnot(None),
            )
            .all()
        )
        rows.sort(key=lambda row: (row[1].code, article_order(row[0].control_code, row[0].id)))
        return [
            {"id": c.id, "code": c.control_code, "title": c.title, "framework": f.code, "framework_name": f.name}
            for c, f in rows
        ]

    @classmethod
    def link_requirement(
        cls, organization_id: int, application_id: int, control_id: int,
        implementation_status: str = "planned", notes: Optional[str] = None, evidence_url: Optional[str] = None,
    ) -> ApplicationComplianceControl:
        """Link one requirement of an adopted framework to an application of the same organisation. This is
        the only place an application-to-requirement link is written.

        Linking it again returns the link already there. Raises ValueError when no organisation is given or
        the organisation has not adopted the requirement's framework, and NotFoundInOrganisation when the
        application is not the organisation's own; nothing is written in either case.
        """
        _require_organisation(organization_id)
        owned = db.session.query(ApplicationComponent.id).filter(
            ApplicationComponent.id == application_id, ApplicationComponent.organization_id == organization_id
        ).first()
        if owned is None:
            raise NotFoundInOrganisation("Application not found")
        held = ApplicationComplianceControl.query.filter(
            ApplicationComplianceControl.organization_id == organization_id,
            ApplicationComplianceControl.control_id == control_id,
            ApplicationComplianceControl.adoption_id.isnot(None),
        ).first()
        if held is None:
            raise FrameworkNotAdopted("That requirement is not in a framework your organisation has adopted.")
        existing = ApplicationComplianceControl.query.filter_by(
            organization_id=organization_id, application_id=application_id, control_id=control_id
        ).first()
        if existing is not None:
            return existing
        link = ApplicationComplianceControl(
            organization_id=organization_id, application_id=application_id, control_id=control_id,
            adoption_id=held.adoption_id, implementation_status=implementation_status,
            notes=notes, evidence_url=evidence_url,
        )
        db.session.add(link)
        db.session.commit()
        return link

    @classmethod
    def requirements_of_application(cls, organization_id: int, application_id: int) -> List[Dict[str, Any]]:
        """The requirements linked to one application, with the framework each belongs to."""
        rows = (
            db.session.query(ApplicationComplianceControl, ComplianceControl, RegulatoryFramework)
            .join(ComplianceControl, ComplianceControl.id == ApplicationComplianceControl.control_id)
            .join(RegulatoryFramework, RegulatoryFramework.id == ComplianceControl.framework_id)
            .filter(
                ApplicationComplianceControl.organization_id == organization_id,
                ApplicationComplianceControl.application_id == application_id,
            )
            .all()
        )
        rows.sort(key=lambda row: (row[2].code, article_order(row[1].control_code, row[1].id)))
        return [
            {
                "code": c.control_code, "title": c.title, "category": c.category, "framework": f.code,
                "framework_id": f.id, "control_id": c.id, "reference": c.official_reference, "status": link.implementation_status,
            }
            for link, c, f in rows
        ]

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
                "notes": ac.notes,
                "evidence_source": None,
            }

            has_own_evidence = (
                ac.evidence_url is not None and ac.evidence_url.strip() != ""
            )

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
                            and partner_ac.evidence_url is not None
                            and partner_ac.evidence_url.strip() != ""
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
                            and partner_ac.evidence_url is not None
                            and partner_ac.evidence_url.strip() != ""
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