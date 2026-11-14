"""A person adds a risk on /risks/ with what it touches, and the AI chat finds it.

Goal: an architect records "Vendor lock-in on billing" against the application "Billing API", then asks
the chat "what could hurt Billing API" and reads the risk with its level, owner and what it touches, and
opens the risk from the answer. Run at 1440 light and 390 dark. Set RISK_LINK_SHOTS to a folder to keep
the screenshots.
"""

import os
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, sign_in_as
from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("RISK_LINK_SHOTS")
TITLE = "Vendor lock-in on billing"
VIEWPORTS = [
    pytest.param({"width": 1440, "height": 900}, "light", id="1440-light"),
    pytest.param({"width": 390, "height": 844}, "dark", id="390-dark"),
]


def _shot(page, name, width):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / ("%s-%d.png" % (name, width))), full_page=True)


def _seed_application(org_id, name):
    """An application with its ArchiMate element, in the organisation."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application",
                                   organization_id=org_id)
        db.session.add(element)
        db.session.flush()
        component = ApplicationComponent(name=name, organization_id=org_id,
                                         archimate_element_id=element.id)
        db.session.add(component)
        db.session.commit()
        ids = {"app": component.id, "element": element.id}
        db.session.remove()
        return ids


def _element_name(element_id):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    with create_app("testing").app_context():
        name = db.session.get(ArchiMateElement, element_id).name
        db.session.remove()
        return name


def _drop_risks(org_id, title):
    from app import create_app, db
    from app.models.risk import Risk

    app = create_app("testing")
    with app.app_context():
        for risk in Risk.query.filter_by(organization_id=org_id, title=title).all():
            db.session.delete(risk)
        db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def billing(seeded, live_server):
    org_id = seeded["ids"]["org"]
    name = "Billing API %s" % uuid.uuid4().hex[:5]
    return {"name": name, "org": org_id, **_seed_application(org_id, name)}


@pytest.mark.parametrize("viewport, scheme", VIEWPORTS)
def test_a_risk_added_with_what_it_touches_is_found_by_the_chat(
    browser, live_server, seeded, billing, viewport, scheme
):
    title = "%s %s" % (TITLE, scheme)
    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    page = context.new_page()
    width = viewport["width"]
    try:
        sign_in_as(page, live_server, seeded, "solution_architect")
        page.goto(live_server + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Add Risk").first.click()
        dialog = page.locator("#create-risk-modal")
        expect(dialog).to_be_visible()
        dialog.locator("#risk-title").fill(title)
        dialog.locator("#risk-likelihood").select_option("4")
        dialog.locator("#risk-impact").select_option("4")
        dialog.locator("#risk-owner").fill("Head of Engineering")

        picker = dialog.get_by_test_id("add-risk-link-picker")
        picker.get_by_label("Search entities to link").fill(billing["name"])
        picker.get_by_test_id("risk-link-choice").first.click()
        chip = picker.get_by_test_id("risk-link-chip")
        expect(chip).to_have_count(1)
        expect(chip).to_contain_text(billing["name"])
        # a chip is removable, and choosing again puts it back
        chip.get_by_role("button").click()
        expect(chip).to_have_count(0)
        picker.get_by_label("Search entities to link").fill(billing["name"])
        picker.get_by_test_id("risk-link-choice").first.click()
        expect(chip).to_have_count(1)
        _shot(page, "risk-link-add-dialog", width)

        with page.expect_response(lambda r: r.url.endswith("/api/risks") and r.request.method == "POST") as sent:
            dialog.get_by_role("button", name="Add Risk").click()
        assert sent.value.status == 201

        page.wait_for_load_state("domcontentloaded")
        ranked = page.locator("#ranked-risks")
        expect(ranked).to_contain_text("Linked to %s" % billing["name"], timeout=PAGE_TIMEOUT)
        _shot(page, "risk-link-ranked", width)

        answer = ask(page, live_server, "what could hurt %s" % billing["name"])
        card = answer.locator("[data-lens-row]").filter(has_text=title)
        expect(card).to_have_count(1)
        expect(card).to_contain_text("Head of Engineering")
        expect(card).to_contain_text("16")  # 4 x 4
        expect(card.get_by_test_id("risk-answer-touches")).to_contain_text(billing["name"])
        _shot(page, "risk-link-chat-answer", width)

        card.get_by_test_id("risk-answer-link").click()
        page.wait_for_url("**/risks/*", timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text(title)
        expect(page.locator("main")).to_contain_text("Head of Engineering")
        expect(page.locator("main")).to_contain_text(billing["name"])
        _shot(page, "risk-link-risk-page", width)
    finally:
        context.close()
        _drop_risks(billing["org"], title)


def test_an_application_with_no_risk_says_so(browser, live_server, seeded, billing):
    other = _seed_application(billing["org"], "Quiet Service %s" % uuid.uuid4().hex[:5])
    context = browser.new_context()
    page = context.new_page()
    try:
        sign_in_as(page, live_server, seeded, "solution_architect")
        name = _element_name(other["element"])
        card = ask(page, live_server, "what could hurt %s" % name)
        expect(card.get_by_text("No risks recorded against this element")).to_be_visible()
    finally:
        context.close()
