"""Journey: nothing the capture pipeline writes shows the sidebar's own
"Build <id>" diagnostic line.

scripts/capture_screenshots.py's _dismiss_onboarding() strips the line (and
the first-run overlay) before every still; a recording instead hides it from
the very first paint, because _dismiss_onboarding() runs after the page has
already settled -- too late for a video's opening frames and its poster. This
drives a real rendered admin page through both paths.
"""
from __future__ import annotations

import pathlib
import sys
from urllib.parse import urlparse

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey

STATIC = pathlib.Path(__file__).resolve().parents[2] / "app" / "static"
_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from capture_screenshots import (  # noqa: E402
    BUILD_STAMP_SELECTOR,
    _dismiss_onboarding,
    _hide_build_stamp_script,
)


@pytest.fixture
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        chromium = p.chromium.launch()
        yield chromium
        chromium.close()


def _persona(app):
    """A returning user (onboarding already complete) with a real sidebar."""
    from datetime import datetime

    from app import db
    from app.models.user import User

    with app.app_context():
        org_id = make_org(db, "BuildStampHidden")
        user_id = make_user(db, org_id, "u", "solution_architect", role_name="Architect")
        User.query.filter_by(id=user_id).update({"onboarding_completed_at": datetime.utcnow()})
        db.session.commit()
        return user_id


def _route_handler(client, document, path):
    def handle(route):
        url = urlparse(route.request.url)
        if url.path.startswith("/static/"):
            f = STATIC / url.path[len("/static/"):]
            return route.fulfill(path=str(f)) if f.is_file() else route.fulfill(status=404, body="")
        if url.path == path:
            return route.fulfill(status=200, content_type="text/html", body=document)
        return route.fulfill(status=204, body="")

    return handle


def test_dismiss_onboarding_removes_the_build_line_but_keeps_sidebar_chrome(app, client, browser):
    from app._bootstrap.build_info import get_build_id

    login(client, _persona(app))
    path = "/dashboard/overview"
    document = client.get(path).get_data(as_text=True)

    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    pg.route("http://app.test/**", _route_handler(client, document, path))
    pg.goto("http://app.test" + path)
    pg.wait_for_selector("#sidebar-nav")
    try:
        _dismiss_onboarding(pg)

        assert pg.locator(BUILD_STAMP_SELECTOR).count() == 0

        body_text = pg.evaluate("() => document.body.innerText") or ""
        assert get_build_id() not in body_text

        # Sidebar chrome other than the build line survives _dismiss_onboarding.
        assert pg.locator(".sidebar-footer-text p.font-medium").count() == 1, (
            "the signed-in user's name block should still be present"
        )
        assert pg.locator('a.sidebar-footer-text[aria-label="Log out"]').count() == 1, (
            "the log-out link should still be present"
        )
        assert pg.locator("#sidebar-nav").count() == 1, "the sidebar nav should still be present"
    finally:
        pg.close()


def test_the_recording_init_script_hides_the_build_line_from_first_paint(app, client, browser):
    login(client, _persona(app))
    path = "/dashboard/overview"
    document = client.get(path).get_data(as_text=True)

    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    ctx.add_init_script(_hide_build_stamp_script())
    pg = ctx.new_page()
    pg.route("http://app.test/**", _route_handler(client, document, path))
    try:
        # No _dismiss_onboarding() call here: this exercises the init script
        # alone, on the very first navigation, the way a recording context
        # (which never calls _dismiss_onboarding before its opening frames)
        # relies on it.
        pg.goto("http://app.test" + path, wait_until="commit")
        pg.wait_for_selector("aside")

        assert not pg.locator(BUILD_STAMP_SELECTOR).first.is_visible(), (
            "the build line must not be visible immediately after the page "
            "commits, under the recording context's init script"
        )
    finally:
        pg.close()
        ctx.close()
