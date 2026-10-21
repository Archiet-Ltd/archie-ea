"""
Enterprise Genome — SECURITY slice demo seed (REVERSIBLE, idempotent).

The demo tenant has motivation ArchiMate elements but ZERO controls, so the
SECURITY slice has nothing to project. This seed lays down a small, realistic
SOC 2 / ISO 27001 control set (access control, encryption, logging, change
management), each bound to a requirement, each requirement realising a
motivation ``Requirement`` ArchiMate element — the exact provenance chain the
slice reads.

ALL seeded rows are marked DEMO DATA so the seed is fully reversible:
  * ArchiMateElement.custom_properties["genome_demo_seed"] = True
  * ComplianceRequirement.evidence_location = DEMO_MARKER
  * ComplianceControl.subcategory = DEMO_MARKER

``unseed_security_demo`` deletes exactly and only those rows. Shared reference
RegulatoryFramework rows are created-if-missing and left in place (harmless
reference data).

CLI:
    flask --app manage seed-security-genome --org 10
    flask --app manage seed-security-genome --org 10 --remove   # reverse
"""
from __future__ import annotations

from app import db
from app.models.archimate_core import ArchiMateElement
from app.models.compliance_models import (
    ComplianceControl,
    ComplianceRequirement,
    RegulatoryFramework,
)

DEMO_MARKER = "DEMO_SEED:genome_security"

# (framework_code, framework_name, control_code, control_title, category,
#  requirement_title) — the four control families from 04_domains.md across two
#  frameworks = 8 rows.
_FRAMEWORKS = {
    "SOC2": "SOC 2 (Trust Services Criteria)",
    "ISO27001": "ISO/IEC 27001:2022",
}
_CONTROLS = [
    ("SOC2", "CC6.1", "Logical access controls restrict access to information assets",
     "Access Control", "Only authorised users may access production customer data"),
    ("SOC2", "CC6.7", "Data is encrypted in transit and at rest",
     "Encryption", "Customer data must be encrypted at rest and in transit"),
    ("SOC2", "CC7.2", "Security events are logged and monitored",
     "Logging & Monitoring", "All security-relevant events must be logged and retained"),
    ("SOC2", "CC8.1", "Changes are authorised, tested and approved before deployment",
     "Change Management", "Production changes require review and approval"),
    ("ISO27001", "A.9.2.1", "User registration and de-registration",
     "Access Control", "User accounts must be provisioned and revoked under a formal process"),
    ("ISO27001", "A.10.1.1", "Policy on the use of cryptographic controls",
     "Encryption", "A cryptographic controls policy must govern key management"),
    ("ISO27001", "A.12.4.1", "Event logging",
     "Logging & Monitoring", "Event logs recording user activities must be produced and kept"),
    ("ISO27001", "A.12.1.2", "Change management",
     "Change Management", "Changes to systems must follow a controlled change process"),
]


def _get_or_create_framework(session, code: str, name: str) -> RegulatoryFramework:
    fw = session.query(RegulatoryFramework).filter_by(code=code).first()
    if fw is None:
        fw = RegulatoryFramework(code=code, name=name, category="security", status="active")
        session.add(fw)
        session.flush()
    return fw


def seed_security_demo(organization_id: int, session=None, created_by_id=None) -> dict:
    """Seed the demo control set for one org. Idempotent.

    Returns a summary: counts of rows created (0 across the board on a re-run).
    Each entity is looked up by its natural key first; if it already exists,
    it's reused (ensuring no duplicate key violations).
    """
    if session is None:
        session = db.session

    frameworks = {code: _get_or_create_framework(session, code, name)
                  for code, name in _FRAMEWORKS.items()}

    # Track which rows we actually create vs reuse.
    created_elements = 0
    created_controls = 0
    created_requirements = 0

    for fw_code, ctrl_code, ctrl_title, category, req_title in _CONTROLS:
        fw = frameworks[fw_code]

        # 1. Get or create ArchiMateElement
        element = session.query(ArchiMateElement).filter_by(
            name=req_title,
            type="Requirement",
            layer="motivation",
            organization_id=organization_id,
        ).first()
        if element is None:
            element = ArchiMateElement(
                name=req_title,
                type="Requirement",
                layer="motivation",
                organization_id=organization_id,
                description=f"[DEMO] {req_title}",
                custom_properties={"genome_demo_seed": True},
            )
            session.add(element)
            session.flush()
            created_elements += 1
        else:
            # Ensure demo marker is set
            if element.custom_properties is None:
                element.custom_properties = {}
            element.custom_properties["genome_demo_seed"] = True
            session.flush()

        # 2. Get or create ComplianceControl (unique on framework_id + control_code)
        control = session.query(ComplianceControl).filter_by(
            framework_id=fw.id,
            control_code=ctrl_code,
        ).first()
        if control is None:
            control = ComplianceControl(
                framework_id=fw.id,
                control_code=ctrl_code,
                title=ctrl_title,
                category=category,
                subcategory=DEMO_MARKER,
                official_reference=f"{fw_code} {ctrl_code}",
                priority="high",
                is_active=True,
            )
            session.add(control)
            session.flush()
            created_controls += 1
        else:
            # Update demo marker if needed
            if control.subcategory != DEMO_MARKER:
                control.subcategory = DEMO_MARKER
                session.flush()

        # 3. Get or create ComplianceRequirement (unique on archimate_element_id + control_id?)
        # Actually, there might be multiple requirements per element? But for demo, we want one.
        requirement = session.query(ComplianceRequirement).filter_by(
            archimate_element_id=element.id,
            control_id=control.id,
        ).first()
        if requirement is None:
            requirement = ComplianceRequirement(
                organization_id=organization_id,
                archimate_element_id=element.id,
                title=req_title,
                description=f"[DEMO] {req_title}",
                requirement_type="security",
                framework_id=fw.id,
                control_id=control.id,
                priority="high",
                status="active",
                implementation_status="completed",
                evidence_location=DEMO_MARKER,
                created_by_id=created_by_id,
            )
            session.add(requirement)
            created_requirements += 1
        else:
            # Ensure demo marker is set
            if requirement.evidence_location != DEMO_MARKER:
                requirement.evidence_location = DEMO_MARKER
                session.flush()

    session.commit()

    # `created` reflects whether this call added any new row (idempotency signal).
    created = (created_elements > 0) or (created_controls > 0) or (created_requirements > 0)

    # Report TOTAL demo rows present for this org, NOT the per-call created delta.
    # ComplianceControl is keyed by (framework_id, control_code) and is NOT
    # org-scoped, so it is reused across orgs — a created delta reads 0 for every
    # org after the first (and for any DB already carrying the frameworks), which
    # misreports the slice's shape. The contract is "how many demo controls does
    # this org's set cover" = len(_CONTROLS). Counted via the same demo-marker
    # join unseed uses, so it stays correct on a re-run.
    demo_reqs = (
        session.query(ComplianceRequirement)
        .join(ArchiMateElement, ComplianceRequirement.archimate_element_id == ArchiMateElement.id)
        .filter(ArchiMateElement.organization_id == organization_id)
        .filter(ComplianceRequirement.evidence_location == DEMO_MARKER)
        .all()
    )
    return {
        "organization_id": organization_id,
        "created": created,
        "elements": len({r.archimate_element_id for r in demo_reqs}),
        "controls": len({r.control_id for r in demo_reqs if r.control_id}),
        "requirements": len(demo_reqs),
    }


def unseed_security_demo(organization_id: int, session=None) -> dict:
    """Remove exactly the rows this seed created for one org. Idempotent."""
    if session is None:
        session = db.session

    # Delete requirements first (FK to control + element), then controls, then elements.
    req_q = (
        session.query(ComplianceRequirement)
        .join(ArchiMateElement, ComplianceRequirement.archimate_element_id == ArchiMateElement.id)
        .filter(ArchiMateElement.organization_id == organization_id)
        .filter(ComplianceRequirement.evidence_location == DEMO_MARKER)
    )
    requirements = req_q.all()
    control_ids = {r.control_id for r in requirements if r.control_id}
    element_ids = {r.archimate_element_id for r in requirements if r.archimate_element_id}
    n_req = len(requirements)
    for r in requirements:
        session.delete(r)
    session.flush()

    n_ctrl = 0
    if control_ids:
        controls = (
            session.query(ComplianceControl)
            .filter(ComplianceControl.id.in_(control_ids))
            .filter(ComplianceControl.subcategory == DEMO_MARKER)
            .all()
        )
        n_ctrl = len(controls)
        for c in controls:
            session.delete(c)

    n_el = 0
    if element_ids:
        elements = (
            session.query(ArchiMateElement)
            .filter(ArchiMateElement.id.in_(element_ids))
            .filter(ArchiMateElement.organization_id == organization_id)
            .all()
        )
        for e in elements:
            props = e.custom_properties or {}
            if props.get("genome_demo_seed"):
                session.delete(e)
                n_el += 1

    session.commit()
    return {
        "organization_id": organization_id,
        "removed": True,
        "elements": n_el,
        "controls": n_ctrl,
        "requirements": n_req,
    }


def _marked_controls_for_org(session, organization_id: int):
    return (
        session.query(ComplianceRequirement.id)
        .join(ArchiMateElement, ComplianceRequirement.archimate_element_id == ArchiMateElement.id)
        .filter(ArchiMateElement.organization_id == organization_id)
        .filter(ComplianceRequirement.evidence_location == DEMO_MARKER)
        .all()
    )


def register_security_genome_commands(app):
    """Register the ``seed-security-genome`` Flask CLI command."""
    import click

    @app.cli.command("seed-security-genome")
    @click.option("--org", "org_id", type=int, required=True, help="Target organization id")
    @click.option("--remove", is_flag=True, help="Reverse the seed instead of applying it")
    def _seed_security_genome(org_id, remove):
        """Seed (or --remove) the SECURITY genome demo control set for one org."""
        if remove:
            result = unseed_security_demo(org_id)
            click.echo(f"Removed demo security genome data: {result}")
        else:
            result = seed_security_demo(org_id)
            click.echo(f"Seeded demo security genome data: {result}")
