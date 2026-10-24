"""L2, "what are we trying to achieve at <element>, and how's it
tracking?": the strategy question asked in the AI chat, in a real browser.

    Ledger Service --serves--> Ledger Gateway
    (a PortfolioInitiative seeded directly on Ledger Service)

An impact question asked after it must keep its own answer: the chat keeps both.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, sign_in_as

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_strategy_graph(org_id):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.enterprise_intelligence import PortfolioInitiative

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

        initiative = PortfolioInitiative(
            organization_id=org_id,
            name="%s modernisation" % noun,
            archimate_element_id=service.id,
            status="Active",
            completion_percentage=45,
            total_budget=200000.0,
            spent_to_date=180000.0,
        )
        db.session.add(initiative)
        db.session.commit()

        out.update(service=service.id, gateway=gateway.id, service_name=service.name)
    return out


@pytest.fixture(scope="module")
def strategy_graph(seeded, live_server):
    return _seed_strategy_graph(seeded["ids"]["org"])


def _question(graph):
    return "What are we trying to achieve at %s and how is it tracking" % graph["service_name"]


def test_the_strategy_question_shows_the_seeded_initiative(
    page, live_server, seeded, strategy_graph
):
    # The CTO has budget authority and sees the financial figure.
    sign_in_as(page, live_server, seeded, "cto")
    card = ask(page, live_server, _question(strategy_graph))
    row = card.locator("[data-lens-row]")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("modernisation")
    expect(row).to_contain_text("45% complete")
    expect(row).to_contain_text("Budget variance")
    expect(row).to_contain_text("1 connection")

    # The solution architect has none: the restricted note, never the figure.
    sign_in_as(page, live_server, seeded, "solution_architect")
    card = ask(page, live_server, _question(strategy_graph))
    row = card.locator("[data-lens-row]")
    expect(row).to_contain_text("modernisation")
    expect(row).to_contain_text("45% complete")
    expect(row).to_contain_text("Restricted to roles with budget authority")
    expect(row).not_to_contain_text("Budget variance")
    expect(row).to_contain_text("1 connection")


def test_an_element_with_no_initiatives_reads_as_an_honest_empty_state(
    page, live_server, seeded, strategy_graph
):
    sign_in_as(page, live_server, seeded, "solution_architect")
    # Gateway has no initiative seeded on it, only Service does.
    gateway = "%s Gateway" % strategy_graph["noun"]
    card = ask(page, live_server, "What are we trying to achieve at %s and how is it tracking" % gateway)
    expect(card.get_by_text("No initiatives recorded against this element")).to_be_visible()
    assert card.locator("[data-lens-row]").count() == 0


def test_the_strategy_and_impact_questions_keep_their_own_answers_separate(
    page, live_server, seeded, strategy_graph
):
    sign_in_as(page, live_server, seeded, "solution_architect")
    impact = ask(page, live_server, "What breaks if %s fails" % strategy_graph["service_name"])
    assert impact.locator("[data-answer-row]").count() >= 1
    assert impact.locator("[data-lens-row]").count() == 0
    strategy = ask(page, live_server, _question(strategy_graph), fresh=False)
    assert strategy.locator("[data-lens-row]").count() == 1
    assert strategy.locator("[data-answer-row]").count() == 0
    # The first answer is still there, unchanged.
    assert impact.locator("[data-answer-row]").count() >= 1
