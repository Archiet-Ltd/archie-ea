"""Founder-reported bug: "Open in Composer" links across the app (the element
detail drawer on /architecture/elements, at minimum) did not open the exact
element -- because the composer never had an `element` query parameter to
receive one. Several sites had already independently discovered this and
patched themselves to link generically rather than falsely, each with its own
comment saying so (see the audit that led to this fix); this is the one
capability that makes all of those honest fallbacks correct instead of
degraded.

Fixed: composer_page() (app/modules/architecture/routes/archimate_routes.py)
reads `element`, threaded through to composer.js as
__COMPOSER_CONFIG__.initialElement. composer.js selects, highlights and
centres the matching element once its viewpoint data has loaded (reusing the
existing _highlightCell / paper.translate primitives the click handler and
canvas search already use) -- see composer_page()'s and
_selectInitialElement's own docstrings/comments for the exact reasoning
(including that an element-only link, with no viewpoint given, defaults to
the enterprise-wide 'layered' viewpoint, since that is the one place a bare
element id can reliably be found without also knowing which solution it
lives in).

This drives the real browser end-to-end, matching the pattern
test_composer_opens_layered_viewpoint.py established for the sibling
layered-viewpoint bug.
"""
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
def test_element_only_link_selects_and_centres_that_element(browser, live_server, seeded):
    """The exact scenario reported: open /archimate/composer?element=<id> with
    no solution_id and no viewpoint. The element must end up selected (its
    name showing in the detail drawer), not just present somewhere on an
    otherwise-generic canvas."""
    from app import create_app, db

    email = seeded["emails"]["enterprise_architect"]
    org_id = seeded["ids"]["org"]

    app = create_app("testing")
    target_id = None
    other_id = None
    with app.app_context():
        from app.models.archimate_core import ArchiMateElement

        suffix = uuid.uuid4().hex[:6]
        target = ArchiMateElement(
            name=f"Claims Processing {suffix}", type="BusinessProcess", layer="business",
            organization_id=org_id,
        )
        other = ArchiMateElement(
            name=f"Unrelated Service {suffix}", type="ApplicationComponent", layer="application",
            organization_id=org_id,
        )
        db.session.add_all([target, other])
        db.session.commit()
        target_id, other_id = target.id, other.id

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, email)

        page.goto(
            live_server + "/archimate/composer?element=" + str(target_id),
            wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
        )
        # composer.js resolves the element-only link to the layered viewpoint,
        # loads it, then selects the target -- wait for the loading state to
        # clear rather than a fixed sleep.
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

        body_text = page.inner_text("body")
        assert "Select a solution to view this viewpoint" not in body_text, (
            "an element-only link must not fall back to the scope_required "
            "prompt -- it should default to the enterprise-wide layered "
            "viewpoint, same as a bare ?viewpoint=layered link does"
        )

        # The Alpine component's own selectedNode is the authoritative,
        # non-fragile proof of "selected" -- same access pattern
        # test_composer_bulk_accept_rate_limit.py and
        # test_composer_create_export_inspect_compare.py already use to read
        # composerApp() state directly, rather than scraping innerText off a
        # canvas whose SVG nodes are not reliably included in it.
        page.wait_for_function(
            "() => { const a = Alpine.$data(document.querySelector('[x-data^=\"composerApp\"]'));"
            " return a && a.selectedNode && a.selectedNode.elementId === %d; }" % target_id,
            timeout=PAGE_TIMEOUT,
        )
        selected = page.evaluate(
            "() => Alpine.$data(document.querySelector('[x-data^=\"composerApp\"]')).selectedNode"
        )
        assert selected["elementId"] == target_id, selected
        assert selected["label"] == f"Claims Processing {suffix}", selected

        # The node itself must carry the composer's own "selected" affordance
        # (composer.js's _highlightCell adds this class to cellView.el, the
        # same .joint-element wrapper other composer smoke tests assert on).
        selected_el = page.locator(".joint-element.selected")
        expect(selected_el).to_have_count(1, timeout=PAGE_TIMEOUT)
    finally:
        context.close()
        with app.app_context():
            from app.models.archimate_core import ArchiMateElement

            ArchiMateElement.query.filter(ArchiMateElement.id.in_([target_id, other_id])).delete(
                synchronize_session=False
            )
            db.session.commit()


@pytest.mark.smoke
def test_unknown_element_id_shows_an_honest_notice_not_a_silent_no_op(browser, live_server, seeded):
    """An element id that doesn't exist (or isn't in the loaded viewpoint) must
    say so, not silently render as if nothing were requested at all -- the
    same "honest, not silent" bar the four already-patched fallback sites
    were already holding themselves to before this fix existed."""
    email = seeded["emails"]["enterprise_architect"]

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, email)

        page.goto(
            live_server + "/archimate/composer?element=999999999",
            wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
        )
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

        toast = page.locator("text=not shown in the current view")
        expect(toast).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()
