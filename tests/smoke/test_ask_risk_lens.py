"""L6, "what could hurt <element>, and what does it touch": the risk
question on the Ask page, in a real browser.

    Ledger Service --serves--> Ledger Gateway
    (a Risk seeded directly on Ledger Service)

The impact question (L1) must keep working exactly as before -- these tests
also cover that regression, since both questions now share one picker.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, type_and_wait

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


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _ready(page, factory):
    page.wait_for_function(
        "(f) => { const el = document.querySelector('[x-data=\"' + f + '()\"]');"
        " return !!(el && el._x_dataStack); }",
        arg=factory,
    )


def _pick_by_click(page, prefix, name):
    page.locator("#%s-picker-listbox [role=option]" % prefix, has_text=name).click()


def test_the_risk_question_shows_the_seeded_risk_with_its_blast_radius(
    page, live_server, seeded, risk_graph
):
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")

    page.locator("#ask-question-risk").click()
    expect(page.locator("#ask-picker-input")).to_be_focused()
    type_and_wait(page, "ask", risk_graph["noun"])
    _pick_by_click(page, "ask", risk_graph["service_name"])

    page.wait_for_selector("[data-ask-risk-row]")
    row = page.locator("[data-ask-risk-row]")
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
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")

    page.locator("#ask-question-risk").click()
    expect(page.locator("#ask-picker-input")).to_be_focused()
    type_and_wait(page, "ask", risk_graph["noun"])
    # Gateway has no risk seeded on it, only Service does.
    page.locator("#ask-picker-listbox [role=option]", has_text="Gateway").click()

    expect(page.get_by_text("No risks recorded against this element.")).to_be_visible()
    assert page.locator("[data-ask-risk-row]").count() == 0


def test_switching_between_impact_and_risk_keeps_each_questions_own_answer_separate(
    page, live_server, seeded, risk_graph
):
    """Regression guard: both questions now share one picker component
    (see ask.js's openKey dispatch) -- opening one must not show or clobber
    the other's already-answered results."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")

    page.locator("#ask-question-impact").click()
    type_and_wait(page, "ask", risk_graph["noun"])
    _pick_by_click(page, "ask", risk_graph["service_name"])
    page.wait_for_selector("[data-ask-row]")
    expect(page.locator("#ask-results")).to_be_visible()
    expect(page.locator("#ask-risk-results")).to_be_hidden()

    page.locator("#ask-question-risk").click()
    type_and_wait(page, "ask", risk_graph["noun"])
    _pick_by_click(page, "ask", risk_graph["service_name"])
    page.wait_for_selector("[data-ask-risk-row]")
    expect(page.locator("#ask-risk-results")).to_be_visible()
    expect(page.locator("#ask-results")).to_be_hidden()
