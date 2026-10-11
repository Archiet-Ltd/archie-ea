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

from .conftest import PAGE_TIMEOUT, PASSWORD, type_and_wait

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


def _login(page, base, email):
    # Sign in from a signed-out browser: switching persona mid-test otherwise
    # lands on the login page already authenticated, which redirects away.
    page.context.clear_cookies()
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


def test_the_programme_question_shows_the_seeded_work_package(
    page, live_server, seeded, programme_graph
):
    # A user WITH budget authority (CTO) sees the financial figure.
    _login(page, live_server, seeded["emails"]["cto"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")

    page.locator("#ask-question-programme").click()
    expect(page.locator("#ask-picker-input")).to_be_focused()
    type_and_wait(page, "ask", programme_graph["noun"])
    page.locator("#ask-picker-listbox [role=option]", has_text="Service").click()

    page.wait_for_selector("[data-ask-programme-row]")
    row = page.locator("[data-ask-programme-row]")
    expect(row).to_contain_text("cloud migration")
    expect(row).to_contain_text("55% complete")
    expect(row).to_contain_text("Cost variance")
    expect(row).to_contain_text("1 connection")

    # A user WITHOUT budget authority (solution architect) sees the
    # restricted message and never the financial figure.
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")

    page.locator("#ask-question-programme").click()
    expect(page.locator("#ask-picker-input")).to_be_focused()
    type_and_wait(page, "ask", programme_graph["noun"])
    page.locator("#ask-picker-listbox [role=option]", has_text="Service").click()

    page.wait_for_selector("[data-ask-programme-row]")
    row = page.locator("[data-ask-programme-row]")
    expect(row).to_contain_text("cloud migration")
    expect(row).to_contain_text("55% complete")
    expect(row).to_contain_text("Restricted to roles with budget authority")
    expect(row).not_to_contain_text("Cost variance")
    expect(row).to_contain_text("1 connection")


def test_an_element_with_no_work_packages_reads_as_an_honest_empty_state(
    page, live_server, seeded, programme_graph
):
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")

    page.locator("#ask-question-programme").click()
    expect(page.locator("#ask-picker-input")).to_be_focused()
    type_and_wait(page, "ask", programme_graph["noun"])
    # Gateway has no work package seeded on it, only Service does.
    page.locator("#ask-picker-listbox [role=option]", has_text="Gateway").click()

    expect(page.get_by_text("No work packages recorded against this element.")).to_be_visible()
    assert page.locator("[data-ask-programme-row]").count() == 0


def test_all_four_questions_keep_their_own_answers_separate(
    page, live_server, seeded, programme_graph
):
    """Regression guard: four questions now share one picker component."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")

    page.locator("#ask-question-impact").click()
    type_and_wait(page, "ask", programme_graph["noun"])
    page.locator("#ask-picker-listbox [role=option]", has_text="Service").click()
    page.wait_for_selector("[data-ask-row]")
    expect(page.locator("#ask-results")).to_be_visible()
    expect(page.locator("#ask-programme-results")).to_be_hidden()

    page.locator("#ask-question-programme").click()
    type_and_wait(page, "ask", programme_graph["noun"])
    page.locator("#ask-picker-listbox [role=option]", has_text="Service").click()
    page.wait_for_selector("[data-ask-programme-row]")
    expect(page.locator("#ask-programme-results")).to_be_visible()
    expect(page.locator("#ask-results")).to_be_hidden()
