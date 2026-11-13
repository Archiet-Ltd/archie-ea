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


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_editor_name_fields_carry_autofocus_at_both_viewports(
    app, client, browser, width, height
):
    """The stage create, stage edit, and value stream edit dialogs each carry
    autofocus on their name field at both 1440x900 (light) and 390x844 (dark)."""
    from app import db

    with app.app_context():
        org_id = make_org(db, "VsEditAutofocus")
        user_id = make_user(
            db, org_id, "vsa", "enterprise_architect", role_name="Architect"
        )

    login(client, user_id)
    vs_id = _create_value_stream(
        app, client, "Autofocus %s" % uuid.uuid4().hex[:8]
    )

    path = "/value-streams/%d" % vs_id
    document = client.get(path).get_data(as_text=True)
    pg = _open(browser, client, document, path, width, height)
    try:
        # The rendered HTML carries autofocus on each editor's name field.
        stage_name_pos = document.index('id="stage-name"')
        assert 'autofocus' in document[stage_name_pos:stage_name_pos + 500]

        stage_edit_name_pos = document.index('id="stage-edit-name"')
        assert 'autofocus' in document[stage_edit_name_pos:stage_edit_name_pos + 500]

        vs_edit_name_pos = document.index('id="vs-edit-name"')
        assert 'autofocus' in document[vs_edit_name_pos:vs_edit_name_pos + 500]
    finally:
        pg.close()
