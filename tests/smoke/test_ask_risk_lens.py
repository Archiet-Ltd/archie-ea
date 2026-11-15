"""L6, "what could hurt <element>, and what does it touch": the risk
question in the AI chat, in a real browser.

    Ledger Service --serves--> Ledger Gateway
    (a Risk seeded directly on Ledger Service)

The impact question (L1) must keep working exactly as before -- these tests
also cover that regression, since both questions now share one picker.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, sign_in_as

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_risk_graph(org_id):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.risk import Risk

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    noun = "Ledger %s" % suffix
    out = {"noun": noun, "org": org_id}
    with app.app_context():
        def element(name, kind, layer):
            row = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org_id)
            db.session.add(row)
            db.session.commit()
            return row

        service = element("%s Service" % noun, "ApplicationComponent", "application")
        gateway = element("%s Gateway" % noun, "ApplicationComponent", "application")
        db.session.add(ArchiMateRelationship(
            type="Serving", source_id=service.id, target_id=gateway.id, organization_id=org_id,
        ))
        risk = Risk(
            organization_id=org_id,
            archimate_element_id=service.id,
            title="%s single vendor dependency" % noun,
            likelihood=4,
            impact=5,
            owner="platform-team",
            mitigation_plan="Dual-source by Q3",
        )
        db.session.add(risk)
        db.session.commit()
        out.update(service=service.id, gateway=gateway.id, service_name=service.name)
    return out


@pytest.fixture(scope="module")
def risk_graph(seeded, live_server):
    return _seed_risk_graph(seeded["ids"]["org"])


def _question(name):
    return "What could hurt %s and what does it touch" % name


def test_the_risk_question_shows_the_seeded_risk_with_its_blast_radius(
    page, live_server, seeded, risk_graph
):
    sign_in_as(page, live_server, seeded, "solution_architect")
    row = ask(page, live_server, _question(risk_graph["service_name"])).locator("[data-lens-row]")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("single vendor dependency")
    expect(row).to_contain_text("platform-team")
    expect(row).to_contain_text("Dual-source by Q3")
    # Likelihood 4 x Impact 5 = 20 -- the model's own display property, not
    # recomputed in the browser.
    expect(row).to_contain_text("20")
    expect(row).to_contain_text("1 connection")


def test_an_element_with_no_risk_reads_as_an_honest_empty_state(
    page, live_server, seeded, risk_graph
):
    sign_in_as(page, live_server, seeded, "solution_architect")
    # Gateway has no risk seeded on it, only Service does.
    card = ask(page, live_server, _question("%s Gateway" % risk_graph["noun"]))
    expect(card.get_by_text("No risks recorded against this element")).to_be_visible()
    assert card.locator("[data-lens-row]").count() == 0


def test_switching_between_impact_and_risk_keeps_each_questions_own_answer_separate(
    page, live_server, seeded, risk_graph
):
    sign_in_as(page, live_server, seeded, "solution_architect")
    impact = ask(page, live_server, "What breaks if %s fails" % risk_graph["service_name"])
    assert impact.locator("[data-answer-row]").count() >= 1
    risk = ask(page, live_server, _question(risk_graph["service_name"]), fresh=False)
    assert risk.locator("[data-lens-row]").count() == 1
    assert risk.locator("[data-answer-row]").count() == 0
    assert impact.locator("[data-answer-row]").count() >= 1
    assert impact.locator("[data-lens-row]").count() == 0
