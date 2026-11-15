"""L4, "who's accountable for <element>, and can they take on more?": the
accountability question in the AI chat, in a real browser.

The ownership read is WITHDRAWN (a tenant-isolation gap found in external
review of the original PR -- see
IntelligenceQueryService.accountability_for_element's docstring). These
tests pin the withdrawn state itself, including with a real seeded ownership
graph present, to guard against silently re-enabling the unsafe read.

The impact/strategy/risk/portfolio/programme questions must keep working
exactly as before -- these tests also cover that regression, since all six
share one picker.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, sign_in_as

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_accountability_graph(org_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    noun = "Ledger %s" % suffix
    out = {"noun": noun, "org": org_id}
    with app.app_context():
        service = ArchiMateElement(
            name="%s Service" % noun, type="ApplicationComponent", layer="application",
            organization_id=org_id,
        )
        db.session.add(service)
        db.session.commit()

        service_component = ApplicationComponent(
            name="%s Service" % noun, organization_id=org_id, archimate_element_id=service.id,
        )
        db.session.add(service_component)
        db.session.flush()

        unit = OrganizationUnit(organization_id=org_id, name="%s Finance" % noun, unit_type="Department")
        db.session.add(unit)
        db.session.flush()

        # A real, well-formed ownership graph -- exactly the shape the
        # original (unsafe) implementation would have served. The withdrawn
        # method must never return this, regardless of what exists.
        db.session.add(ApplicationOwnership(
            organization_id=org_id,
            application_id=service_component.id,
            organization_unit_id=unit.id,
            ownership_type="Business Owner",
            primary_contact="Jordan Owner",
        ))

        # A second ArchiMate element with a relationship so the impact
        # question has a row to show.
        target = ArchiMateElement(
            name="%s Target" % noun, type="ApplicationComponent", layer="application",
            organization_id=org_id,
        )
        db.session.add(target)
        db.session.commit()
        db.session.add(ArchiMateRelationship(
            type="Serving", source_id=service.id, target_id=target.id,
            organization_id=org_id,
        ))
        db.session.commit()

        out.update(service=service.id, service_name=service.name)
    return out


@pytest.fixture(scope="module")
def accountability_graph(seeded, live_server):
    return _seed_accountability_graph(seeded["ids"]["org"])


def _question(name):
    return "Who's accountable for %s" % name


def test_the_accountability_question_shows_the_withdrawn_state_not_seeded_data(
    page, live_server, seeded, accountability_graph
):
    sign_in_as(page, live_server, seeded, "solution_architect")
    card = ask(page, live_server, _question("%s Service" % accountability_graph["noun"]))
    expect(card.get_by_text("Ownership data is not yet connected")).to_be_visible()
    expect(card.get_by_text("Capacity and availability data is not yet connected.")).to_be_visible()
    assert card.locator("[data-lens-row]").count() == 0


def test_the_accountability_and_impact_questions_keep_their_own_answers_separate(
    page, live_server, seeded, accountability_graph
):
    service = "%s Service" % accountability_graph["noun"]
    sign_in_as(page, live_server, seeded, "solution_architect")
    impact = ask(page, live_server, "What breaks if %s fails" % service)
    assert impact.locator("[data-answer-row]").count() >= 1
    owners = ask(page, live_server, _question(service), fresh=False)
    expect(owners.get_by_text("Capacity and availability data is not yet connected.")).to_be_visible()
    assert owners.locator("[data-answer-row]").count() == 0
    assert impact.locator("[data-lens-row]").count() == 0
    assert impact.locator("[data-answer-row]").count() >= 1
