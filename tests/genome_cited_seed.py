"""A small, linked model for the genome cited-answer tests and journey.

Per organisation: three capabilities (one supported by an application, two
supported by nothing), one plateau, one gap and one work package that changes
one of the unsupported capabilities, is assigned to the plateau and closes the
gap. Every record has its model element. ``suffix`` lets a second organisation
carry near-identical names, so a leak of its records would read almost the same
and can only be told apart by id.

Written through the canonical writers: ensure_capability_record for the
capability record and work_package_service for the work package and its links.
"""
from __future__ import annotations


def seed_cited_model(session, org_id: int, suffix: str = "") -> dict:
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import Gap, Plateau
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )
    from app.modules.capabilities.services.capability_service import ensure_capability_record
    from app.services import work_package_service

    def element(name, type_, layer):
        row = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
        session.add(row)
        session.flush()
        return row

    names = {
        "supported": "Customer Onboarding" + suffix,
        "planned": "Fraud Screening" + suffix,
        "unplanned": "Partner Payouts" + suffix,
        "app": "Ledger Hub" + suffix,
        "plateau": "Target State 2027" + suffix,
        "gap": "No fraud screening" + suffix,
        "wp": "Fraud engine rollout" + suffix,
    }
    caps = {}
    for key in ("supported", "planned", "unplanned"):
        el = element(names[key], "Capability", "business")
        cap, _ = ensure_capability_record(
            name=names[key], code="GC-%s-%s" % (org_id, key[:3].upper()), level=1,
            organization_id=org_id, archimate_element_id=el.id,
        )
        caps[key] = cap
    app_el = element(names["app"], "ApplicationComponent", "application")
    app = ApplicationComponent(name=names["app"], organization_id=org_id, archimate_element_id=app_el.id)
    session.add(app)
    session.flush()
    session.add(UnifiedApplicationCapabilityMapping(
        application_component_id=app.id, unified_capability_id=caps["supported"].id,
        support_level="full", coverage_percentage=100, relationship_type="supports",
    ))
    plateau_el = element(names["plateau"], "Plateau", "implementation_migration")
    plateau = Plateau(name=names["plateau"], organization_id=org_id, sequence_order=1,
                      archimate_element_id=plateau_el.id)
    gap_el = element(names["gap"], "Gap", "implementation_migration")
    gap = Gap(name=names["gap"], organization_id=org_id, archimate_element_id=gap_el.id)
    session.add_all([plateau, gap])
    session.flush()
    wp = work_package_service.create_work_package(
        organization_id=org_id, name=names["wp"], status="planned",
        capability_id=caps["planned"].id, plateau_id=plateau.id, gap_id=gap.id,
    )
    session.flush()
    return {
        "names": names,
        "cap_ids": {k: c.id for k, c in caps.items()},
        "app_id": app.id,
        "plateau_id": plateau.id,
        "gap_id": gap.id,
        "wp_id": wp.id,
        "wp_element_id": wp.archimate_element_id,
        "cap_element_ids": {k: c.archimate_element_id for k, c in caps.items()},
    }
