"""The initiative dialog on the four strategic pages creates what it promises.

A person opens the dialog from the page's own button, fills it in and presses the create
button or Enter. The plan is created in their organisation and is listed on the same page
under "Plans created here". With the name empty, nothing is sent and the dialog stays open.
"""

import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# page path, the page's own button, the dialog's id
PAGES = [
    ("/strategic/capability-health", "Create Plan", "health-remediation-modal"),
    ("/strategic/investment-matrix", "Create Proposal", "investment-proposal-modal"),
    ("/strategic/risk-assessment", "Mitigate Risk", "risk-mitigation-modal"),
    ("/strategic/impact-analysis", "Create Change Request", "change-request-modal"),
]
NAME_PREFIX = "Journey plan "


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


@pytest.fixture(scope="module", autouse=True)
def _remove_journey_plans(app):
    yield
    with app.app_context():
        from app import db
        from app.models.strategic import StrategicInitiative

        StrategicInitiative.query.filter(StrategicInitiative.name.like(NAME_PREFIX + "%")).delete(
            synchronize_session=False)
        db.session.commit()
        db.session.remove()


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_timeout(800)
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _open_dialog(page, base, path, button, dialog_id):
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response is not None and response.status == 200, "%s answered %s" % (path, response and response.status)
    page.wait_for_timeout(500)
    page.get_by_role("button", name=button).first.click()
    dialog = page.locator("#initiative-" + dialog_id)
    dialog.wait_for(state="visible")
    return dialog


def _fill(dialog, name):
    """Fill every field a person would: the name first, the rest with plain values."""
    name_box = dialog.locator("input[type=text]").first
    name_box.fill(name)
    for box in dialog.locator("input[type=text]").all()[1:]:
        if not box.input_value():
            box.fill("Journey owner")
    dialog.locator("textarea").first.fill("Why this plan exists, in a sentence.")
    for select in dialog.locator("select").all():
        values = [v for v in select.locator("option").evaluate_all("os => os.map(o => o.value)") if v]
        if values and not select.input_value():
            select.select_option(values[0])
    for box in dialog.locator("input[type=number]").all():
        box.fill("25000")
    return name_box


def _stored(app, name):
    with app.app_context():
        from app import db
        from app.models.strategic import StrategicInitiative

        row = StrategicInitiative.query.filter_by(name=name).first()
        found = None if row is None else (row.organization_id, row.owner_id, row.budget_allocated)
        db.session.remove()
        return found


@pytest.mark.parametrize("how", ["button", "enter"])
@pytest.mark.parametrize("path,button,dialog_id", PAGES, ids=[p[2] for p in PAGES])
def test_the_dialog_creates_a_plan_shown_on_the_page(page, live_server, seeded, app, path, button, dialog_id, how):
    _login(page, live_server, seeded["emails"]["cto"])
    dialog = _open_dialog(page, live_server, path, button, dialog_id)
    name = NAME_PREFIX + uuid.uuid4().hex[:8]
    name_box = _fill(dialog, name)

    if how == "enter":
        name_box.press("Enter")
    else:
        dialog.get_by_role("button", name="Create").click()

    dialog.wait_for(state="hidden", timeout=15000)
    assert dialog.get_by_text("is required").count() == 0

    listed = page.get_by_test_id("plans-created-here")
    listed.get_by_text(name).wait_for(state="visible", timeout=15000)

    stored = _stored(app, name)
    assert stored is not None, "no plan named %r was stored" % name
    org_id, owner_id, budget = stored
    assert org_id == seeded["ids"]["org"], "the plan is not in the person's organisation"
    assert owner_id is not None, "the owner chosen in the dialog was not stored"


def test_an_empty_name_sends_nothing_and_keeps_the_dialog_open(page, live_server, seeded, app):
    path, button, dialog_id = PAGES[0]
    _login(page, live_server, seeded["emails"]["cto"])
    dialog = _open_dialog(page, live_server, path, button, dialog_id)
    _fill(dialog, "")
    posts = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "/api/initiatives/" in r.url else None)

    dialog.get_by_role("button", name="Create").click()
    page.wait_for_timeout(1500)

    assert posts == [], "the dialog posted with an empty name: %s" % posts
    assert dialog.is_visible(), "the dialog closed with an empty name"
