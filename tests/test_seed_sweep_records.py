"""The demo company holds a real record of every type the live sweep needs to open.

The live sweep opens every address in the product with a record behind each parameter. Before this seed there was no
solution design, no architecture journey, no business case, no value stream, no decision, no programme and no data domain
in the demo company, so 255 solution addresses and the Architecture Journey pages had nothing to open and the first sweep
reached only the pages with no parameter.

Release-rule lines 2 and 4; checklist Phase 3.
"""

from __future__ import annotations

import os

import pytest

from app.commands.seed_demo_company import seed_demo_company

ARCHITECT = "sage.itops@lantern-quay.example.com"
REVIEWER = "casey.inventory@lantern-quay.example.com"


def _seed():
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        return seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]


def _org_id():
    from app import db
    from app.models.organization import Organization

    return db.session.query(Organization.id).filter_by(slug="lantern-quay").scalar()


def _user(db_session, org_id, email):
    from app.models.user import User

    return User.query.filter_by(email=email, organization_id=org_id).first()


def test_every_record_type_behind_a_route_parameter_exists_after_seeding(app, db_session, make_org):
    _seed()
    org_id = _org_id()
    from app.models.adr import ArchitectureDecisionRecord
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.architecture_journey import ArchitectureJourney
    from app.models.archimate_viewpoint import ArchiMateViewpoint
    from app.models.business_case import BusinessCase
    from app.models.process_data import DataDomain, DataEntity
    from app.models.solution_element import SolutionElement
    from app.models.solution_models import Solution
    from app.models.strategic import StrategicInitiative
    from app.models.unified_capability import ValueStream

    counts = {
        "solution designs": Solution.query.filter_by(organization_id=org_id).count(),
        "architecture journeys": ArchitectureJourney.query.filter_by(organization_id=org_id).count(),
        "business cases": BusinessCase.query.filter_by(organization_id=org_id).count(),
        "value streams": ValueStream.query.filter_by(organization_id=org_id).count(),
        "architecture decisions": ArchitectureDecision.query.filter_by(organization_id=org_id).count(),
        "decision records": ArchitectureDecisionRecord.query.filter_by(organization_id=org_id).count(),
        "programmes": StrategicInitiative.query.filter_by(organization_id=org_id).count(),
        "data domains": DataDomain.query.filter_by(organization_id=org_id).count(),
        "data entities": DataEntity.query.filter_by(organization_id=org_id).count(),
        "viewpoints": ArchiMateViewpoint.query.filter_by(organization_id=org_id).count(),
    }
    empty = [k for k, v in counts.items() if v == 0]
    assert not empty, f"no row for: {empty}"

    in_progress = Solution.query.filter_by(organization_id=org_id, status="in_progress").first()
    assert in_progress is not None and in_progress.adm_phase and in_progress.current_step >= 2, "no solution design in progress"
    assert SolutionElement.query.filter_by(solution_id=in_progress.id).count() >= 4, "the design in progress holds no elements"
    linked = ArchitectureJourney.query.filter_by(organization_id=org_id, solution_id=in_progress.id).first()
    assert linked is not None, "no journey is linked to the design in progress"
    assert ArchitectureJourney.query.filter_by(organization_id=org_id, solution_id=None).count() >= 1, "no journey without a solution"


def test_journey_links_resolve_to_named_records_in_the_same_organisation(app, db_session, make_org):
    _seed()
    org_id = _org_id()
    from app.models.architecture_journey import ArchitectureJourney
    from app.models.architecture_journey_link import ArchitectureJourneyLink, ArchitectureJourneyMember
    from app.modules.solutions_strategic.v2.services.journey_home import JOURNEY_LINK_RESOLVERS

    journeys = ArchitectureJourney.query.filter_by(organization_id=org_id).all()
    assert len(journeys) >= 2
    seen_types = set()
    for j in journeys:
        links = ArchitectureJourneyLink.query.filter_by(journey_id=j.id).all()
        assert links, f"journey '{j.title}' links to nothing"
        for link in links:
            resolved = JOURNEY_LINK_RESOLVERS[link.entity_type](link.entity_id, org_id)
            assert resolved is not None and resolved["label"], f"{link.entity_type} {link.entity_id} on '{j.title}' does not resolve to a named record"
            seen_types.add(link.entity_type)
        members = {m.user_id for m in ArchitectureJourneyMember.query.filter_by(journey_id=j.id).all()}
        assert j.owner_id in members, f"the owner of '{j.title}' is not listed as a member"
    assert {"solution", "decision", "risk", "value_stream", "programme", "application"} <= seen_types


def test_seeding_twice_adds_no_sweep_records(app, db_session, make_org):
    first = _seed()
    assert first["sweep_records_created"] > 0
    second = _seed()
    assert second["sweep_records_created"] == 0, second


def test_the_architect_opens_the_journey_and_sees_the_records_it_links(app, db_session, make_org, client, login_as):
    """A person's goal: open the work, and see which design, decision and risk it rests on, by name."""
    _seed()
    org_id = _org_id()
    from app.models.architecture_journey import ArchitectureJourney
    from app.models.risk import Risk

    architect = _user(db_session, org_id, ARCHITECT)
    journey = ArchitectureJourney.query.filter_by(organization_id=org_id, title="Retire the legacy work-order scheduler").first()
    login_as(client, architect)
    resp = client.get(f"/architecture-journey/work/{journey.id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Retire the legacy work-order scheduler" in html
    assert "Field-service scheduling modernisation" in html, "the linked design is not named on the journey"
    assert "Use the event relay for stock changes" in html, "the linked decision is not named on the journey"
    first_risk = Risk.query.filter_by(organization_id=org_id).order_by(Risk.id).first()
    assert first_risk.title in html, "the linked risk is not named on the journey"
    # the design in progress opens too
    solution_id = journey.solution_id
    assert client.get(f"/architecture-journey/{solution_id}").status_code == 200


def test_a_reviewer_on_the_journey_can_open_it_and_a_stranger_cannot(app, db_session, make_org, client, login_as):
    _seed()
    org_id = _org_id()
    from app.models.architecture_journey import ArchitectureJourney
    from app.models.user import Role, User

    journey = ArchitectureJourney.query.filter_by(organization_id=org_id, title="Retire the legacy work-order scheduler").first()
    reviewer = _user(db_session, org_id, REVIEWER)
    login_as(client, reviewer)
    assert client.get(f"/architecture-journey/work/{journey.id}").status_code == 200

    other = make_org("stranger")
    role = Role.query.filter_by(name="Administrator").first() or Role.query.first()
    stranger = User(email="stranger@example.test", first_name="Stranger", last_name="Elsewhere", organization_id=other.id,
                    confirmed=True, role=role, enterprise_role="enterprise_architect")
    db_session.add(stranger)
    db_session.commit()
    login_as(client, stranger)
    assert client.get(f"/architecture-journey/work/{journey.id}").status_code == 404, "another organisation can open this journey"
