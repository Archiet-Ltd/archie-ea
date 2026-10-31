"""An administrator reads one import cost estimate before starting an import and
the same figure, in the same currency format, on the job page afterwards.

Goal: "I know what this import will cost before I start it, and the job tells me
the same". The organisation reports in euros, so a bare dollar sign on either
page, or two different figures, fails the journey. Starting the import creates
the job in its awaiting-confirmation state; the language model is not called.
"""

import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

MONEY = re.compile(r"^€\d[\d,]*\.\d{2}$")


def _set_currency(org_id, code):
    from app import create_app, db
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        db.session.get(Organization, org_id).reporting_currency = code
        db.session.commit()
        db.session.remove()


def _shot(page, name):
    out = os.environ.get("IMPORTCOST_SHOTS")
    if out:
        os.makedirs(out, exist_ok=True)
        page.screenshot(path=os.path.join(out, name), full_page=False)


@pytest.mark.parametrize(
    "viewport,scheme,tag",
    [({"width": 1440, "height": 900}, "light", "1440-light"),
     ({"width": 390, "height": 844}, "dark", "390-dark")],
)
def test_the_estimate_before_the_import_equals_the_estimate_on_the_job(
    browser, live_server, tmp_path, viewport, scheme, tag
):
    org = create_fresh_org("data_architect", org_admin=True)
    _set_currency(org["org_id"], "EUR")
    csv = tmp_path / "two-apps.csv"
    csv.write_text("name,description\nCostApp1,First\nCostApp2,Second\n", encoding="utf-8")

    context = browser.new_context(ignore_https_errors=True, viewport=viewport, color_scheme=scheme)
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["data_architect"])

    page.goto(live_server + "/batch-import/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector('[role="tab"]', timeout=PAGE_TIMEOUT)
    page.locator("#jobName").fill("Cost agreement %s" % uuid.uuid4().hex[:6])
    page.locator("#fileUpload").set_input_files(str(csv))

    # Located by what a person reads (the Estimated Cost row), not by a hook the fix adds.
    before = page.get_by_text("Estimated Cost", exact=True).locator("xpath=following-sibling::span").first
    expect(before).to_be_visible(timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)  # the figure may be fetched from the server
    figure_before = before.inner_text().strip()
    before.scroll_into_view_if_needed()
    _shot(page, "a-new-import-cost-estimate-%s.png" % tag)

    button = page.get_by_role("button", name="Create Import Job")
    expect(button.first).to_be_enabled(timeout=PAGE_TIMEOUT)
    button.first.click()
    page.wait_for_url(lambda url: "/batch-import/jobs/" in url, timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("domcontentloaded")

    after = page.get_by_text("Estimated Cost", exact=True).locator("xpath=ancestor::div[1]/following-sibling::span").first
    expect(after).to_be_visible(timeout=PAGE_TIMEOUT)
    after.scroll_into_view_if_needed()
    _shot(page, "a-job-cost-summary-%s.png" % tag)
    figure_after = after.inner_text().strip()
    print("FIGURES new-import=%s job=%s" % (figure_before, figure_after))

    assert figure_before == figure_after, (
        "the new-import page estimated %s but the job page says %s" % (figure_before, figure_after))
    assert MONEY.match(figure_after), "job page figure %r is not in the organisation's format" % figure_after
    assert figure_before == figure_after, (
        "the new-import page estimated %s but the job page says %s" % (figure_before, figure_after))
    context.close()
