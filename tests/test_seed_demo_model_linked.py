"""The demonstration organisation is linked end to end, the way a real model is.

A reviewer opening the risk register, the capability list and the traceability page
of the seeded organisation should find every risk tied to something it threatens,
every risk described apart from its plan, and every capability reaching technology
through an application, or a recorded gap that says why it does not.
"""

import os

from app.commands.seed_demo_company import seed_demo_company


def _seed():
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        return seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]


def _demo_org_id(db_session):
    from app.models.organization import Organization

    return db_session.query(Organization.id).filter_by(slug="lantern-quay").scalar()


def test_every_demo_risk_is_described_apart_from_its_plan_and_linked(app, db_session, make_org):
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink

    _seed()
    org_id = _demo_org_id(db_session)
    risks = Risk.query.filter_by(organization_id=org_id).all()
    assert risks
    for risk in risks:
        assert risk.description and risk.description != risk.mitigation_plan, risk.title
        assert RiskEntityLink.query.filter_by(risk_id=risk.id, organization_id=org_id).count() >= 1, risk.title


def test_every_demo_capability_chain_completes_or_is_a_recorded_gap(app, db_session, make_org, tenant_ctx):
    from app.models.implementation_migration import Gap
    from app.modules.intelligence.services.traceability_check_service import broken_chains

    _seed()
    org_id = _demo_org_id(db_session)
    with tenant_ctx(org_id):
        broken = broken_chains(org_id)
        recorded = {g.archimate_element_id for g in Gap.query.filter_by(organization_id=org_id).all()}
    assert broken["checked"] > 0
    unexplained = [row["name"] for row in broken["rows"] if row["id"] not in recorded]
    assert not unexplained, "chains that stop short with no recorded gap: %s" % unexplained


def test_a_second_run_adds_no_relationship_or_mapping(app, db_session, make_org):
    from app.models import ArchiMateRelationship
    from app.models.application_capability import ApplicationCapabilityMapping

    _seed()
    org_id = _demo_org_id(db_session)

    def counts():
        return (
            ArchiMateRelationship.query.filter_by(organization_id=org_id).count(),
            ApplicationCapabilityMapping.query.filter_by(organization_id=org_id).count(),
        )

    before = counts()
    second = _seed()
    assert counts() == before
    assert second["relationships_created"] == 0
    assert second["capability_mappings_created"] == 0


def test_another_organisation_gets_none_of_the_demo_links(app, db_session, make_org):
    from app.models import ArchiMateRelationship

    other_id = make_org("traceability-other").id
    _seed()
    assert ArchiMateRelationship.query.filter_by(organization_id=other_id).count() == 0


def test_every_demo_capability_has_an_owner_on_the_capability_map(app, db_session, make_org):
    from app.models.application_owner import ApplicationOwner
    from app.models.unified_capability import UnifiedCapability
    from app.services.risk_service import capability_owner_names

    other_id = make_org("owners-other").id
    _seed()
    org_id = _demo_org_id(db_session)
    ids = [c.id for c in UnifiedCapability.query.filter_by(organization_id=org_id).all()]
    assert ids
    owners = capability_owner_names(ids, org_id)
    assert [i for i in ids if not owners.get(i)] == [], "capabilities the map would show as having no owner"
    assert ApplicationOwner.query.filter_by(organization_id=other_id, element_type="capability").count() == 0
    before = ApplicationOwner.query.filter_by(organization_id=org_id, element_type="capability").count()
    _seed()
    assert ApplicationOwner.query.filter_by(organization_id=org_id, element_type="capability").count() == before


def test_demo_risk_links_are_written_by_the_risk_service(app, db_session, make_org, monkeypatch):
    from app.models.risk_entity_link import RiskEntityLink
    from app.services import risk_service

    calls = []
    real = risk_service.add_risk_link

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(risk_service, "add_risk_link", spy)
    _seed()
    org_id = _demo_org_id(db_session)
    stored = RiskEntityLink.query.filter_by(organization_id=org_id).count()
    assert stored and len(calls) >= stored
