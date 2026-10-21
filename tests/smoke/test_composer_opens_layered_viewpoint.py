"""Founder-reported bug: the sidebar "ArchiMate Composer" link opened a blank
"Unsaved diagram" canvas instead of the enterprise-wide Layered viewpoint.

Fixed across app/utils/role_access.py (the REAL, live sidebar mechanism --
app/config/navigation_registry_v2.py turned out to be dead code, unreferenced
by any template) and app/services/archimate_viewpoint_service.py (the
'layered'/'basic' viewpoints now declare enterprise_scope=True and no longer
require a solution_id).

This drives the real browser end-to-end: click the sidebar link, land on the
composer, and see real elements -- not a blank canvas, not a "select a
solution" prompt.
"""
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD, all_modules_link

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
def test_composer_sidebar_link_opens_layered_viewpoint_with_elements(browser, live_server, seeded):
    """Seed a few ArchiMateElements for the enterprise_architect's org, click
    the sidebar link, and confirm the composer renders real content on the
    real layered viewpoint rather than the blank-canvas/select-a-solution
    states the founder reported."""
    from app import create_app, db

    email = seeded["emails"]["enterprise_architect"]
    org_id = seeded["ids"]["org"]

    app = create_app("testing")
    seeded_ids = []
    with app.app_context():
        from app.models.archimate_core import ArchiMateElement

        suffix = uuid.uuid4().hex[:6]
        for name, etype, layer in [
            (f"Order Service {suffix}", "ApplicationComponent", "application"),
            (f"Fulfil Order {suffix}", "BusinessProcess", "business"),
            (f"Cloud Platform {suffix}", "Node", "technology"),
        ]:
            el = ArchiMateElement(name=name, type=etype, layer=layer, organization_id=org_id)
            db.session.add(el)
        db.session.commit()
        seeded_ids = [
            el.id
            for el in ArchiMateElement.query.filter(
                ArchiMateElement.name.like(f"%{suffix}")
            ).all()
        ]

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, email)

        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # D7: scope to the real sidebar nav specifically -- with the
        # /modules "All modules" directory page (D3) also carrying a matching
        # href, an unqualified partial-href locator could ambiguously match
        # either render site if this ever ran against a page with both present.
        # Not a launch module: reached from the All modules page, which every sidebar links to.
        composer_link = all_modules_link(page, "ArchiMate Composer")
        expect(composer_link).to_be_visible(timeout=PAGE_TIMEOUT)
        href = composer_link.get_attribute("href")
        assert href and "viewpoint=layered" in href, (
            f"All modules ArchiMate Composer link must carry ?viewpoint=layered, got {href!r}"
        )

        composer_link.click()
        page.wait_for_url(lambda url: "/archimate/composer" in url, timeout=PAGE_TIMEOUT)
        assert "viewpoint=layered" in page.url, page.url

        # composer.js loads the viewpoint asynchronously on landing; wait for the
        # loading state to clear rather than a fixed sleep.
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

        body_text = page.inner_text("body")
        assert "Start from a template" not in body_text, (
            "composer rendered the blank-canvas template picker -- the "
            "founder-reported bug regressed"
        )
        assert "Select a solution to view this viewpoint" not in body_text, (
            "composer rendered the 'select a solution' scope_required prompt "
            "for the enterprise-wide layered viewpoint -- the enterprise_scope "
            "fix regressed"
        )
        suffix_marker = suffix
        assert suffix_marker in body_text, (
            f"none of the seeded elements (suffix {suffix_marker}) are visible on "
            "the rendered composer canvas -- the layered viewpoint did not "
            "actually load this tenant's data"
        )
    finally:
        # D7 (round 3): cleanup must run even when an assertion above fails --
        # otherwise the failing runs, where cleanup matters most, are exactly
        # the ones that leak residue into the shared smoke database.
        context.close()

        # D7: this test seeds real DB rows via a second create_app("testing")
        # instance outside the shared smoke fixtures' own teardown -- clean them
        # up explicitly rather than leaving residue in the shared smoke database.
        with app.app_context():
            from app.models.archimate_core import ArchiMateElement

            if seeded_ids:
                ArchiMateElement.query.filter(ArchiMateElement.id.in_(seeded_ids)).delete(
                    synchronize_session=False
                )
                db.session.commit()
