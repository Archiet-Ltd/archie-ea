"""Journey: an architect follows the links that open the Composer on a model
that already holds work, and the Composer shows that work within seconds.

Three links led to a tab that stopped responding for minutes once the
organisation had a few hundred linked elements: "Viewpoint" on a lane of the
ADM board, "+ Add" on the traceability page, and "ArchiMate Composer" on the
modules directory. The cause was the canvas drawing every node as it was added
and again as it was moved into its layer band.

The goal asserted here is the architect's: after the click, the Composer says
how many elements it is showing, draws that many, and the page still answers.
"""
import time
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ELEMENT_COUNT = 150
OPEN_WITHIN_SECONDS = 3.0
MODEL_WITHIN_SECONDS = 8.0
DRAWN_WITHIN_SECONDS = 30
PERSONA = "enterprise_architect"
LAYERS = [("business", "BusinessProcess"), ("application", "ApplicationComponent"), ("technology", "Node")]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.fixture()
def linked_model(live_server, seeded):
    """A chain of linked elements across three layers in the persona's organisation."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.user import User

    app = create_app("testing")
    org_id = seeded["ids"]["org"]
    email = seeded["emails"][PERSONA]
    tag = uuid.uuid4().hex[:6]
    with app.app_context():
        # The journey is run as the persona, never as a platform administrator.
        user = User.query.filter_by(email=email).first()
        assert user.enterprise_role == "enterprise_architect", user.enterprise_role
        assert not user.is_admin(), "the journey must not run as an administrator"

        elements = []
        for i in range(ELEMENT_COUNT):
            layer, etype = LAYERS[i % 3]
            el = ArchiMateElement(name=f"Linked {tag} {i}", type=etype, layer=layer, organization_id=org_id)
            db.session.add(el)
            elements.append(el)
        db.session.commit()
        relationships = []
        for a, b in zip(elements, elements[1:]):
            rel = ArchiMateRelationship(source_id=a.id, target_id=b.id, type="Serving", organization_id=org_id)
            db.session.add(rel)
            relationships.append(rel)
        db.session.commit()
        element_ids = [e.id for e in elements]
        relationship_ids = [r.id for r in relationships]
    yield {"email": email, "tag": tag}
    with app.app_context():
        ArchiMateRelationship.query.filter(ArchiMateRelationship.id.in_(relationship_ids)).delete(synchronize_session=False)
        ArchiMateElement.query.filter(ArchiMateElement.id.in_(element_ids)).delete(synchronize_session=False)
        db.session.commit()


def _assert_composer_shows_the_model(page, started):
    """The Composer names the number of elements it shows, draws them, and answers."""
    status = page.locator("[x-text='statusText']").first
    status.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    page.wait_for_function(
        "() => /\\d+ elements/.test((document.querySelector(\"[x-text='statusText']\") || {}).textContent || '')",
        timeout=int(MODEL_WITHIN_SECONDS * 1000),
    )
    page.evaluate("1 + 1")  # a frozen tab never returns from this
    elapsed = time.monotonic() - started
    assert elapsed < MODEL_WITHIN_SECONDS, f"the Composer took {elapsed:.1f}s to show the model"
    text = status.inner_text()
    shown = int(text.split("—")[1].split("elements")[0].strip())
    assert shown >= ELEMENT_COUNT, text
    # Drawing continues in batches while the tab stays responsive; all of it lands.
    page.wait_for_function(
        "(n) => document.querySelectorAll('#composer-canvas .joint-element').length >= n",
        arg=ELEMENT_COUNT,
        timeout=DRAWN_WITHIN_SECONDS * 1000,
    )


def _click_and_land(page, locator, expected_path):
    started = time.monotonic()
    with page.expect_navigation(wait_until="commit", timeout=PAGE_TIMEOUT):
        locator.click()
    assert expected_path in page.url, page.url
    page.wait_for_load_state("load", timeout=PAGE_TIMEOUT)
    opened = time.monotonic() - started
    assert opened < OPEN_WITHIN_SECONDS, f"the page took {opened:.1f}s to open"
    return started


def test_lane_viewpoint_link_opens_the_composer_with_the_model(browser, live_server, seeded, linked_model):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, linked_model["email"])
        page.goto(live_server + "/adm-kanban/", wait_until="load", timeout=PAGE_TIMEOUT)
        page.wait_for_selector('[data-testid="adm-lane"]', timeout=PAGE_TIMEOUT)
        # The first lane opens the whole-model viewpoint.
        link = page.locator('[data-testid="adm-lane"] a', has_text="Viewpoint").first
        started = _click_and_land(page, link, "/archimate/composer")
        assert "viewpoint=basic" in page.url, page.url
        _assert_composer_shows_the_model(page, started)
    finally:
        context.close()


def test_traceability_add_link_opens_the_composer(browser, live_server, seeded, linked_model):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, linked_model["email"])
        page.goto(live_server + "/archimate/traceability", wait_until="load", timeout=PAGE_TIMEOUT)
        link = page.get_by_role("link", name="+ Add").first
        started = _click_and_land(page, link, "/archimate/composer")
        page.evaluate("1 + 1")
        assert time.monotonic() - started < OPEN_WITHIN_SECONDS
        # The Composer opened on a canvas the architect can add to, with the layer they chose.
        assert "layer=" in page.url
        assert page.locator("#composer-canvas").count() == 1
    finally:
        context.close()


def test_modules_directory_composer_link_opens_the_model(browser, live_server, seeded, linked_model):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, linked_model["email"])
        page.goto(live_server + "/modules/", wait_until="load", timeout=PAGE_TIMEOUT)
        link = page.locator("main a[href*='/archimate/composer']").first
        started = _click_and_land(page, link, "/archimate/composer")
        _assert_composer_shows_the_model(page, started)
    finally:
        context.close()
