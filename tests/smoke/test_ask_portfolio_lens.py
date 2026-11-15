"""L3, "what is the rationalization status of <element>": the portfolio
question asked in the AI chat, in a real browser.

    Ledger Service   -- ApplicationComponent, has a rationalization score
    Ledger Committee -- BusinessActor, not an application at all

The impact and risk questions must keep working exactly as before -- one
test here also covers that regression, since all three now share one
picker.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, sign_in_as
from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_portfolio_graph(org_id):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    noun = "Ledger %s" % suffix
    out = {"noun": noun, "org": org_id}
    with app.app_context():
        service_element = ArchiMateElement(
            name="%s Service" % noun, type="ApplicationComponent", layer="application",
            organization_id=org_id,
        )
        db.session.add(service_element)
        db.session.commit()

        component = ApplicationComponent(
            name="%s Service" % noun, organization_id=org_id,
            archimate_element_id=service_element.id,
        )
        db.session.add(component)
        db.session.commit()

        committee = ArchiMateElement(
            name="%s Committee" % noun, type="BusinessActor", layer="business",
            organization_id=org_id,
        )
        db.session.add(committee)
        db.session.commit()

        # One relationship so the impact question has a row to show.
        db.session.add(ArchiMateRelationship(
            type="Serving", source_id=service_element.id, target_id=committee.id,
            organization_id=org_id,
        ))
        db.session.commit()

        out.update(
            service_name=service_element.name, component_id=component.id,
            committee_name=committee.name,
        )
    return out


@pytest.fixture(scope="module")
def portfolio_graph(seeded, live_server):
    return _seed_portfolio_graph(seeded["ids"]["org"])


def _question(name):
    return "What is the rationalization status of %s" % name


def test_the_portfolio_question_links_to_rationalization_planning(
    page, live_server, seeded, portfolio_graph
):
    sign_in_as(page, live_server, seeded, "solution_architect")
    card = ask(page, live_server, _question(portfolio_graph["service_name"]))
    link = card.get_by_role("link", name="Open rationalization planning")
    expect(link).to_be_visible()
    href = link.get_attribute("href")
    assert href == "/applications/rationalization/planning/%s" % portfolio_graph["component_id"]
    # The link lands on the planning page for that application, with content.
    page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert portfolio_graph["service_name"] in page.locator("body").inner_text()


def test_a_non_application_element_reads_as_an_honest_not_tracked_state(
    page, live_server, seeded, portfolio_graph
):
    sign_in_as(page, live_server, seeded, "solution_architect")
    card = ask(page, live_server, _question(portfolio_graph["committee_name"]))
    expect(card.get_by_text("This is not something the rationalization view tracks")).to_be_visible()
    assert card.get_by_role("link", name="Open rationalization planning").count() == 0


def test_the_portfolio_and_impact_questions_keep_their_own_answers_separate(
    page, live_server, seeded, portfolio_graph
):
    sign_in_as(page, live_server, seeded, "solution_architect")
    impact = ask(page, live_server, "What breaks if %s fails" % portfolio_graph["service_name"])
    assert impact.locator("[data-answer-row]").count() >= 1
    portfolio = ask(page, live_server, _question(portfolio_graph["service_name"]), fresh=False)
    assert portfolio.locator("[data-lens-portfolio]").count() == 1
    assert portfolio.locator("[data-answer-row]").count() == 0
    assert impact.locator("[data-lens-portfolio]").count() == 0
    assert impact.locator("[data-answer-row]").count() >= 1
