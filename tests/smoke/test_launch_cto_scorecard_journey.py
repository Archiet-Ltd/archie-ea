"""A CTO opens the scorecard, sees the supported-estate share and an open
exception, escalates it, reloads, and sees it marked escalated.

Covers the one new template introduced with R1-B85 PR1
(architecture/cto_scorecard.html), which had no browser smoke journey
before this.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_open_exception(org_id):
    """One open architecture-change exception for the fresh org, via the
    product's own model -- not through a UI flow this journey isn't
    testing."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.architecture_decision import ArchitectureChangeRequest

        row = ArchitectureChangeRequest(
            organization_id=org_id,
            title="Smoke Exception",
            acr_reference="ACR-SMOKE-1",
            disposition="exception",
        )
        db.session.add(row)
        db.session.commit()
        row_id = row.id
        db.session.remove()
    return row_id


def test_cto_escalates_an_open_exception_and_it_persists(browser, live_server):
    org = create_fresh_org("cto")
    exception_id = _seed_open_exception(org["org_id"])

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    # The first-run onboarding overlay (x-show="showOnboarding") covers the
    # page for a fresh user and would block the Escalate click below; the
    # other launch journeys dismiss it the same way, by pre-seeding the
    # localStorage key the Alpine component itself checks, not by changing
    # the overlay.
    context.add_init_script("try { localStorage.setItem('archie_onboarding_ts', '1'); } catch (e) {}")
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["cto"])

    page.goto(live_server + "/architecture/cto-scorecard/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("supported-version-share")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id(f"exception-{exception_id}")).to_be_visible(timeout=PAGE_TIMEOUT)

    page.get_by_role("button", name="Escalate").click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)

    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("escalated-badge")).to_be_visible(timeout=PAGE_TIMEOUT)
    context.close()
