"""Journey: value stream editors open on their first field, and the first and last stage read as trigger and outcome.

Level 9, docs/TESTING_STANDARD.md. The business architect models value streams,
so the stage chevron strip must read "Trigger" before the first stage and
"Outcome" after the last, and each editor dialog must carry autofocus on its
name field.

Verified at 1440x900 (light, desktop) and 390x844 (dark, mobile drawer).
"""

from pathlib import Path
from urllib.parse import urlparse
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey

STATIC = Path(__file__).resolve().parents[2] / "app" / "static"
VIEWPORTS = [(1440, 900), (390, 844)]


def _create_value_stream(app, client, name):
    from app import db
    from app.models.unified_capability import ValueStream, ValueStreamStage

    with app.app_context():
        response = client.post(
            "/value-streams/create",
            data={
                "name": name,
                "description": "Journey value stream for editor focus test.",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200, response.status_code
        db.session.expunge_all()
        vs = db.session.execute(
            db.select(ValueStream).filter_by(name=name)
        ).scalar_one()
        for order, stage_name in enumerate(
            ["Identify", "Fulfill", "Close"], start=1
        ):
            stage = ValueStreamStage(
                name=stage_name, value_stream_id=vs.id, stage_order=order
            )
            db.session.add(stage)
        db.session.commit()
        return vs.id


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        chromium = p.chromium.launch()
        yield chromium
        chromium.close()


def _open(browser, client, document, path, width=1440, height=900):
    pg = browser.new_page(viewport={"width": width, "height": height})

    def handle(route):
        url = urlparse(route.request.url)
        if url.path.startswith("/static/"):
            f = STATIC / url.path[len("/static/"):]
            return route.fulfill(path=str(f)) if f.is_file() else route.fulfill(
                status=404, body=""
            )
        if url.path == path:
            return route.fulfill(
                status=200, content_type="text/html", body=document
            )
        if url.path.startswith("/api/"):
            r = client.get(url.path, query_string=route.request.post_data)
            return route.fulfill(
                status=r.status_code,
                content_type=r.content_type or "application/json",
                body=r.get_data(),
            )
        return route.fulfill(status=204, body="")

    pg.route("http://app.test/**", handle)
    pg.goto("http://app.test" + path)
    pg.wait_for_selector('[data-stage-chevrons]', timeout=15000)
    return pg


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_trigger_and_outcome_visible_at_both_viewports(
    app, client, browser, width, height
):
    """Trigger reads before the first stage and Outcome after the last at
    both 1440x900 (light) and 390x844 (dark)."""
    from app import db

    with app.app_context():
        org_id = make_org(db, "VsEditJourney")
        user_id = make_user(
            db, org_id, "vsj", "enterprise_architect", role_name="Architect"
        )

    login(client, user_id)
    vs_id = _create_value_stream(
        app, client, "Order to Cash %s" % uuid.uuid4().hex[:8]
    )

    path = "/value-streams/%d" % vs_id
    document = client.get(path).get_data(as_text=True)
    pg = _open(browser, client, document, path, width, height)
    try:
        trigger = pg.locator('[data-stage-chevrons] >> text=Trigger')
        outcome = pg.locator('[data-stage-chevrons] >> text=Outcome')
        expect(trigger).to_be_visible()
        expect(outcome).to_be_visible()

        trigger_box = trigger.bounding_box()
        outcome_box = outcome.bounding_box()
        first_card_box = pg.locator('[data-stage-card]').first.bounding_box()
        last_card_box = pg.locator('[data-stage-card]').last.bounding_box()
        assert trigger_box["x"] < first_card_box["x"], (
            "Trigger is not to the left of the first stage card"
        )
        assert outcome_box["x"] > last_card_box["x"], (
            "Outcome is not to the right of the last stage card"
        )
    finally:
        pg.close()


def _signed_in_page(app, client, browser, width, height, stages=3):
    from app import db

    with app.app_context():
        org_id = make_org(db, "VsFocus%s" % uuid.uuid4().hex[:6])
        user_id = make_user(
            db, org_id, "vf" + uuid.uuid4().hex[:6], "enterprise_architect",
            role_name="Architect",
        )
    login(client, user_id)
    vs_id = _create_value_stream(
        app, client, "Focus %s" % uuid.uuid4().hex[:8]
    )
    path = "/value-streams/%d" % vs_id
    document = client.get(path).get_data(as_text=True)
    return _open(browser, client, document, path, width, height)


_FOCUSED = "document.activeElement && document.activeElement.id"


def _shot(pg, name, width):
    import os

    folder = os.environ.get("VSEDIT_SHOTS")
    if folder:
        os.makedirs(folder, exist_ok=True)
        pg.screenshot(path=os.path.join(folder, "%s_%d.png" % (name, width)))


@pytest.mark.parametrize("width,height", VIEWPORTS)
@pytest.mark.parametrize(
    "opener,dialog,field",
    [
        ("Add Stage", "stage-create-modal", "stage-name"),
        ("Edit", "vs-edit-modal", "vs-edit-name"),
        ("Edit stage", "stage-edit-modal", "stage-edit-name"),
    ],
)
def test_editor_opens_with_focus_on_name_and_typing_lands_there(
    app, client, browser, width, height, opener, dialog, field
):
    """Click the button that opens each editor: keyboard focus is on the
    dialog's name field, and typing without clicking goes into that field."""
    pg = _signed_in_page(app, client, browser, width, height)
    try:
        pg.emulate_media(color_scheme="dark" if width < 800 else "light")
        if opener == "Edit stage":
            pg.locator('[data-stage-card] button[aria-label="Edit stage"]').first.click()
        else:
            pg.get_by_role("button", name=opener, exact=True).first.click()
        pg.wait_for_function(
            "id => document.activeElement && document.activeElement.id === id",
            arg=field, timeout=3000,
        )
        assert pg.evaluate(_FOCUSED) == field
        pg.keyboard.press("End")
        pg.keyboard.type("Zq")
        assert pg.locator("#" + field).input_value().endswith("Zq")
        _shot(pg, dialog, width)
    finally:
        pg.close()


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_single_stage_reads_as_trigger_and_outcome_once(
    app, client, browser, width, height
):
    """A stream with one stage names it as both trigger and outcome with a
    single label, not two labels around the same card."""
    from app import db
    from app.models.unified_capability import ValueStream, ValueStreamStage

    pg = _signed_in_page(app, client, browser, width, height)
    pg.close()
    with app.app_context():
        vs = db.session.execute(
            db.select(ValueStream).order_by(ValueStream.id.desc())
        ).scalars().first()
        for stage in db.session.execute(
            db.select(ValueStreamStage).filter_by(value_stream_id=vs.id)
        ).scalars().all()[1:]:
            db.session.delete(stage)
        db.session.commit()
        path = "/value-streams/%d" % vs.id
    document = client.get(path).get_data(as_text=True)
    pg = _open(browser, client, document, path, width, height)
    try:
        strip = pg.locator("[data-stage-chevrons]")
        expect(strip.get_by_text("Trigger and outcome")).to_have_count(1)
        expect(strip.get_by_text("Outcome", exact=True)).to_have_count(0)
        expect(strip.get_by_text("Trigger", exact=True)).to_have_count(0)
        _shot(pg, "single_stage", width)
    finally:
        pg.close()
