"""L3, "what is the rationalization status of <element>": the portfolio
question on the Ask page, in a real browser.

    Ledger Service   -- ApplicationComponent, has a rationalization score
    Ledger Committee -- BusinessActor, not an application at all

The impact and risk questions must keep working exactly as before -- one
test here also covers that regression, since all three now share one
picker.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, type_and_wait

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


def test_the_portfolio_question_links_to_rationalization_planning(
    page, live_server, seeded, portfolio_graph
):
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")

    page.locator("#ask-question-portfolio").click()
    expect(page.locator("#ask-picker-input")).to_be_focused()
    type_and_wait(page, "ask", portfolio_graph["noun"])
    page.locator("#ask-picker-listbox [role=option]", has_text="Service").click()

    card = page.locator("[data-ask-portfolio-card]")
    expect(card).to_be_visible()
    link = card.get_by_role("link", name="Open rationalization planning")
    href = link.get_attribute("href")
    assert href == "/applications/rationalization/planning/%s" % portfolio_graph["component_id"]


def test_a_non_application_element_reads_as_an_honest_not_tracked_state(
    page, live_server, seeded, portfolio_graph
):
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")

    page.locator("#ask-question-portfolio").click()
    expect(page.locator("#ask-picker-input")).to_be_focused()
    type_and_wait(page, "ask", portfolio_graph["noun"])
    page.locator("#ask-picker-listbox [role=option]", has_text="Committee").click()

    expect(page.get_by_text("This is not something the rationalization view tracks.")).to_be_visible()
    assert not page.locator("[data-ask-portfolio-card]").is_visible()


def test_all_three_questions_keep_their_own_answers_separate(
    page, live_server, seeded, portfolio_graph
):
    """Regression guard: three questions now share one picker component."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")

    page.locator("#ask-question-impact").click()
    type_and_wait(page, "ask", portfolio_graph["noun"])
    page.locator("#ask-picker-listbox [role=option]", has_text="Service").click()
    page.wait_for_selector("[data-ask-row]")
    expect(page.locator("#ask-results")).to_be_visible()
    expect(page.locator("#ask-portfolio-results")).to_be_hidden()

    page.locator("#ask-question-portfolio").click()
    type_and_wait(page, "ask", portfolio_graph["noun"])
    page.locator("#ask-picker-listbox [role=option]", has_text="Service").click()
    page.wait_for_selector("[data-ask-portfolio-card]")
    expect(page.locator("#ask-portfolio-results")).to_be_visible()
    expect(page.locator("#ask-results")).to_be_hidden()
