"""L5, "what's changing at <element>, and is it on track?": the programme
question on the Ask page, in a real browser.

    Ledger Service --serves--> Ledger Gateway
    (a UnifiedWorkPackage seeded directly on Ledger Service)

The impact/risk/portfolio questions must keep working exactly as before --
these tests also cover that regression, since all four now share one
picker.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, sign_in_as

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_programme_graph(org_id):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.unified_work_package import UnifiedWorkPackage

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

        gateway = ArchiMateElement(
            name="%s Gateway" % noun, type="ApplicationComponent", layer="application",
            organization_id=org_id,
        )
        db.session.add(gateway)
        db.session.commit()
        db.session.add(ArchiMateRelationship(
            type="Serving", source_id=service.id, target_id=gateway.id, organization_id=org_id,
        ))

        wp = UnifiedWorkPackage(
            name="%s cloud migration" % noun,
            archimate_element_id=service.id,
            organization_id=org_id,
            business_capability="Ledger",
            status="in_progress",
            progress_percentage=55.0,
            estimated_cost=200000.0,
            actual_cost=180000.0,
        )
        db.session.add(wp)
        db.session.commit()

        out.update(service=service.id, gateway=gateway.id, service_name=service.name)
    return out


@pytest.fixture(scope="module")
def programme_graph(seeded, live_server):
    return _seed_programme_graph(seeded["ids"]["org"])


def _question(name):
    return "What's changing at %s and is it on track" % name


def test_the_programme_question_shows_the_seeded_work_package(
    page, live_server, seeded, programme_graph
):
    service = "%s Service" % programme_graph["noun"]
    # The CTO has budget authority and sees the financial figure.
    sign_in_as(page, live_server, seeded, "cto")
    row = ask(page, live_server, _question(service)).locator("[data-lens-row]")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("cloud migration")
    expect(row).to_contain_text("55% complete")
    expect(row).to_contain_text("Cost variance")
    expect(row).to_contain_text("1 connection")

    # The solution architect has none: the restricted note, never the figure.
    sign_in_as(page, live_server, seeded, "solution_architect")
    row = ask(page, live_server, _question(service)).locator("[data-lens-row]")
    expect(row).to_contain_text("cloud migration")
    expect(row).to_contain_text("55% complete")
    expect(row).to_contain_text("Restricted to roles with budget authority")
    expect(row).not_to_contain_text("Cost variance")
    expect(row).to_contain_text("1 connection")


def test_an_element_with_no_work_packages_reads_as_an_honest_empty_state(
    page, live_server, seeded, programme_graph
):
    sign_in_as(page, live_server, seeded, "solution_architect")
    card = ask(page, live_server, _question("%s Gateway" % programme_graph["noun"]))
    expect(card.get_by_text("No work packages recorded against this element")).to_be_visible()
    assert card.locator("[data-lens-row]").count() == 0


def test_the_programme_and_impact_questions_keep_their_own_answers_separate(
    page, live_server, seeded, programme_graph
):
    service = "%s Service" % programme_graph["noun"]
    sign_in_as(page, live_server, seeded, "solution_architect")
    impact = ask(page, live_server, "What breaks if %s fails" % service)
    assert impact.locator("[data-answer-row]").count() >= 1
    assert impact.locator("[data-lens-row]").count() == 0
    programme = ask(page, live_server, _question(service), fresh=False)
    assert programme.locator("[data-lens-row]").count() == 1
    assert programme.locator("[data-answer-row]").count() == 0
    assert impact.locator("[data-answer-row]").count() >= 1
