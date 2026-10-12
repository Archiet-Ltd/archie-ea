"""An application manager finds an incomplete application, fixes it from the check and sees it clear.

The manager owns two applications: one missing its technical owner, one complete.
The dashboard links to the completeness check, which lists only the incomplete
one. Following its link to the edit form, saving the missing owner and returning
to the check shows nothing left to fix. A second organisation's manager sees none of it.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org):
    from app import create_app, db
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    full = dict(description="Does things", lifecycle_status="operational", deployment_status="production",
                business_owner="Ada")
    with app.app_context():
        thin = ApplicationComponent(name="Thin ledger %s" % org["suffix"], organization_id=org["org_id"], **full)
        whole = ApplicationComponent(name="Whole portal %s" % org["suffix"], organization_id=org["org_id"],
                                     technical_owner="Grace", **full)
        db.session.add_all([thin, whole])
        db.session.flush()
        for row in (thin, whole):
            db.session.add(ApplicationOwner(application_id=row.id, user_id=org["user_ids"]["application_manager"],
                                            organization_id=org["org_id"], ownership_type="primary"))
        db.session.commit()
        out = {"thin": thin.name, "whole": whole.name}
        db.session.remove()
    return out


def _page(browser):
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return ctx, ctx.new_page()


def test_manager_fixes_a_missing_fact_from_the_completeness_check(browser, live_server):
    org = create_fresh_org("application_manager")
    other = create_fresh_org("application_manager")
    seeded = _seed(org)

    ctx, page = _page(browser)
    sign_in(page, live_server, org["emails"]["application_manager"])
    page.goto(live_server + "/my-applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.get_by_role("link", name="Check completeness").click()
    listing = page.get_by_test_id("completeness-list")
    expect(listing).to_contain_text(seeded["thin"])
    expect(listing).to_contain_text("Technical owner")
    expect(listing).not_to_contain_text(seeded["whole"])

    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.get_by_role("link", name="Fix on the edit form").first.click()
    page.fill("#technical_owner", "Grace Hopper")
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name="Save").click()

    page.goto(live_server + "/my-applications/completeness", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("completeness-list")).to_contain_text("Nothing to fix")
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("completeness-list")).to_contain_text("Nothing to fix")
    ctx.close()

    ctx, page = _page(browser)
    sign_in(page, live_server, other["emails"]["application_manager"])
    page.goto(live_server + "/my-applications/completeness", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("completeness-list")).not_to_contain_text(seeded["thin"])
    ctx.close()
