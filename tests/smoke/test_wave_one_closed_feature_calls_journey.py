"""Journey: in the first release a CTO opens the TOGAF ADM board and the maturity batch update and nothing on
either page calls a feature that is not open yet.

Written from the goal "the board loads quietly": the board shows its columns and cards, the sprint control is
absent (Sprints opens later) rather than present and broken, and neither page raises a console error, an
unhandled rejection, or a request that answers with the early-access message.
"""

from __future__ import annotations

import pytest

from .conftest import PAGE_TIMEOUT, _seed_standard_org, boot_live_server
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PAGES = ["/adm-kanban/", "/adm-kanban/boards/v2", "/capability-maturity/batch-update"]


@pytest.fixture(scope="module")
def wave_server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": "1"})


@pytest.fixture(scope="module")
def org(request):
    return _seed_standard_org(request, None)


def test_adm_board_and_maturity_batch_update_call_nothing_that_is_closed(browser, wave_server, org):
    page = browser.new_page()
    errors, refused, failed = [], [], []

    def on_response(resp):
        if "/static/" in resp.url:
            return
        if resp.status in (404, 405):
            try:
                body = resp.text()
            except Exception:  # noqa: BLE001
                body = ""
            if "early_access" in body or "early-access-page" in body:
                refused.append("%s %s" % (resp.request.method, resp.url.replace(wave_server, "")))
        elif resp.status >= 500:
            failed.append("%s %s -> %s" % (resp.request.method, resp.url.replace(wave_server, ""), resp.status))

    try:
        page.on("response", on_response)
        page.on("pageerror", lambda e: errors.append("uncaught: %s" % e))
        page.on("console", lambda m: errors.append("console: %s" % m.text) if m.type == "error" else None)
        _login(page, wave_server, org["emails"]["cto"])
        for path in PAGES:
            page.goto(wave_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_timeout(2500)
            assert page.locator('[data-testid="early-access-page"]').count() == 0, "%s opens as early access" % path
            assert len(page.inner_text("main").strip()) > 80, "%s opens without content" % path
            if path.startswith("/adm-kanban"):
                body = page.inner_text("main")
                assert "Sprint Planning" not in body, "the sprint control is offered while Sprints is not open"
        assert not refused, "a first-release page called a closed route:\n" + "\n".join(sorted(set(refused)))
        assert not failed, "\n".join(sorted(set(failed)))
        assert not errors, "\n".join(errors)
        # the sprint script itself, run on the board anyway, treats the early-access answer as "not open"
        page.goto(wave_server + "/adm-kanban/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1500)
        del errors[:]
        page.add_script_tag(url=wave_server + "/static/js/components/sprint_planning.js")
        outcome = page.evaluate(
            "async () => { const p = window.sprintPlanningPanel(); try { await p.init(); return [true, p.boardId]; }"
            " catch (e) { return [false, String(e)]; } }")
        page.wait_for_timeout(500)
        assert outcome == [True, None], outcome
        assert not [e for e in errors if e.startswith("uncaught")], errors
    finally:
        page.close()
