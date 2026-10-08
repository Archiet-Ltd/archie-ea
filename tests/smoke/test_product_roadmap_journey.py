"""A Solution Architect assigns a real epic to a roadmap horizon and it persists.

/product-roadmap's primary write action is dragging an epic card between the
Now/Next/Later columns, which calls productRoadmap()'s dropEpic(event,
targetHorizon) -> PATCH /api/product-roadmap/epics/<id>/horizon. Real
browser drag-and-drop is unreliable to script; dropEpic is invoked directly
here via Alpine's own component instance (Alpine.$data on the root element)
with a synthetic event object - this exercises the exact same code path a
real drop would, just without simulating the mouse gesture.

An "epic" is a Requirement row with requirement_type='epic' - created here
directly via the ORM (this test suite's established pattern per conftest.py's
`seeded` fixture: several entities have no UI create path, or in this case a
real drag gesture isn't reliably scriptable, so the fixture creates the
starting data directly).

Task-completion shape: seed an unscheduled epic, assign it a horizon through
the real component method, confirm the PATCH succeeds, then reload the page
fresh and confirm the epic is now listed under that horizon - fetched from
GET /api/product-roadmap, not left over in client-side Alpine state.
"""

import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


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
    page.wait_for_timeout(800)
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def test_assign_epic_horizon_survives_reload(browser, live_server, seeded):
    from app import create_app, db
    from app.models.models import Requirement

    title = "SmkEpic %s" % uuid.uuid4().hex[:8]
    app = create_app("testing")
    with app.app_context():
        epic = Requirement(
            title=title, requirement_type="epic", type="functional",
            organization_id=seeded["ids"]["org"],
        )
        db.session.add(epic)
        db.session.commit()
        epic_id = epic.id

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1920, "height": 1080})
    page = context.new_page()
    email = seeded["emails"]["solution_architect"]

    _login(page, live_server, email)
    page.goto(live_server + "/product-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)

    with page.expect_response(
        lambda r: ("/api/product-roadmap/epics/%d/horizon" % epic_id) in r.url and r.request.method == "PATCH",
        timeout=PAGE_TIMEOUT,
    ) as resp_info:
        result = page.evaluate(
            """(epicId) => {
                const root = document.querySelector('[x-data="productRoadmap()"]');
                const comp = window.Alpine.$data(root);
                const epic = (comp.unscheduled || []).find(e => e.id === epicId);
                if (!epic) return {ok: false, reason: 'epic not found in unscheduled', unscheduled: comp.unscheduled};
                comp._dragging = epic;
                comp.dropEpic({preventDefault(){}}, 'now');
                return {ok: true};
            }""",
            epic_id,
        )
    assert result.get("ok"), "could not locate seeded epic in the component's own data: %r" % result
    assert resp_info.value.status < 400, "horizon assignment PATCH failed: %d" % resp_info.value.status
    page.wait_for_timeout(1000)

    # Leave and come back - a real fresh navigation, re-fetches /api/product-roadmap.
    page.goto(live_server + "/product-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)
    body = page.inner_text("body")
    assert title in body, "epic %r not present anywhere on the roadmap after reload" % title

    fresh = page.request.get(live_server + "/api/product-roadmap")
    assert fresh.ok
    data = fresh.json()
    now_titles = [e.get("title") for e in data.get("horizons", {}).get("now", {}).get("epics", [])]
    assert title in now_titles, (
        "epic %r not in the 'now' horizon on a fresh API fetch: %r" % (title, now_titles))
    context.close()
