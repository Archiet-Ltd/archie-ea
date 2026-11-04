"""An architect reads what an import cost in the currency it is kept in, and the page says which currency that is.

The goal, as the Enterprise Architect: "see what my import jobs have cost without the page passing off one
currency as another". AI usage costs and the AI budget limit are kept in US dollars, so they read in US dollars,
labelled "US dollars", in an organisation that reports in pounds or in euros: never with the organisation's
symbol, which would turn a dollar figure into a wrong pound figure. An organisation with no import jobs reads the
total in words, not as a zero amount.
"""
import os
import re
import tempfile
import uuid
from decimal import Decimal

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("CURFMT_WALK_DIR") or tempfile.mkdtemp(prefix="import-cost-walk-")


def _costed_job(user_id, cost, currency=None, org_id=None):
    from app import create_app, db
    from app.models.batch_import import BatchImportJob, BatchJobStatus
    from app.models.organization import Organization

    with create_app("testing").app_context():
        if currency:
            db.session.get(Organization, org_id).reporting_currency = currency
        job = BatchImportJob(user_id=user_id, name="Costed import " + uuid.uuid4().hex[:6], filename="apps.csv",
                             total_applications=3, total_batches=1, status=BatchJobStatus.COMPLETED,
                             estimated_cost_usd=Decimal(cost), actual_cost_usd=Decimal(cost),
                             budget_limit_usd=Decimal("25.00"))
        db.session.add(job)
        db.session.commit()
        job_id = job.id
    return job_id


def _drop(org_id, job_ids):
    from app import create_app, db
    from app.models.batch_import import BatchImportJob

    with create_app("testing").app_context():
        for job_id in job_ids:
            job = db.session.get(BatchImportJob, job_id)
            if job is not None:
                db.session.delete(job)
        db.session.commit()


def _shot(page, name):
    os.makedirs(WALK, exist_ok=True)
    page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


@pytest.fixture
def gbp_org(live_server):
    info = create_fresh_org("enterprise_architect")
    info["job_id"] = _costed_job(info["user_ids"]["enterprise_architect"], "12.5000")
    yield info
    _drop(info["org_id"], [info["job_id"]])


@pytest.fixture
def eur_org(live_server):
    info = create_fresh_org("enterprise_architect")
    info["job_id"] = _costed_job(info["user_ids"]["enterprise_architect"], "7.2500", "EUR", info["org_id"])
    yield info
    _drop(info["org_id"], [info["job_id"]])


@pytest.mark.parametrize("size,label", [({"width": 1440, "height": 900}, "1440"), ({"width": 390, "height": 844}, "390")])
def test_import_costs_read_in_the_currency_they_are_kept_in(browser, live_server, gbp_org, size, label):
    context = browser.new_context(viewport=size)
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT)
    try:
        sign_in(page, live_server, gbp_org["emails"]["enterprise_architect"])
        page.goto(live_server + "/batch-import/", wait_until="networkidle")
        total = page.get_by_text("Total Cost").locator("xpath=ancestor::*[@data-slot='card']")
        expect(total).to_contain_text("$12.50")
        expect(total).to_contain_text("US dollars")
        expect(page.locator("#main-content")).not_to_contain_text("£")
        _shot(page, "batch-import-gbp-" + label)

        expect(page.locator("#main-content .lucide-dollar-sign")).to_have_count(0)
        page.get_by_role("link", name="View").first.click()
        page.wait_for_url("**/batch-import/jobs/%d" % gbp_org["job_id"])
        page.wait_for_load_state("networkidle")
        main = page.locator("#main-content")
        expect(main).to_contain_text("$12.50")
        expect(page.get_by_text("Estimated Cost").locator("xpath=ancestor::div[contains(@class,'justify-between')][1]")).to_contain_text("$12.50")
        expect(page.get_by_text("Actual Cost").locator("xpath=ancestor::div[contains(@class,'justify-between')][1]")).to_contain_text("$12.50")
        expect(main).to_contain_text("Budget: $25.00")
        expect(main).to_contain_text("US dollars")
        expect(main).not_to_contain_text("£")
        _shot(page, "batch-import-job-gbp-" + label)
    finally:
        context.close()


def test_the_dollar_figure_is_not_relabelled_for_another_organisation(browser, live_server, eur_org):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT)
    try:
        sign_in(page, live_server, eur_org["emails"]["enterprise_architect"])
        page.goto(live_server + "/batch-import/", wait_until="networkidle")
        total = page.get_by_text("Total Cost").locator("xpath=ancestor::*[@data-slot='card']")
        expect(total).to_contain_text("$7.25")
        expect(total).to_contain_text("US dollars")
        expect(page.locator("#main-content")).not_to_contain_text("€")
    finally:
        context.close()


def test_a_cost_nobody_has_recorded_reads_in_words(browser, live_server):
    info = create_fresh_org("enterprise_architect")
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT)
    try:
        sign_in(page, live_server, info["emails"]["enterprise_architect"])
        page.goto(live_server + "/batch-import/", wait_until="networkidle")
        total = page.get_by_text("Total Cost").locator("xpath=..")
        expect(total).to_contain_text("Not recorded")
        expect(page.locator("#main-content")).not_to_contain_text("$0.00")
        expect(page.locator("#main-content")).not_to_contain_text("£0.00")
        _shot(page, "batch-import-empty")
    finally:
        context.close()


def _bare_job(user_id):
    from app import create_app, db
    from app.models.batch_import import BatchImportBatch, BatchImportJob, BatchJobStatus

    with create_app("testing").app_context():
        job = BatchImportJob(user_id=user_id, name="Unpriced import " + uuid.uuid4().hex[:6], filename="apps.csv",
                             total_applications=3, total_batches=1, status=BatchJobStatus.COMPLETED)
        db.session.add(job)
        db.session.flush()
        # One batch that nobody priced: its row must read in words too, not as a zero amount.
        db.session.add(BatchImportBatch(job_id=job.id, batch_number=1, total_applications=3))
        db.session.commit()
        return job.id


def _watch_errors(page):
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    return errors


def test_the_job_and_new_import_pages_load_without_a_console_error(browser, live_server, gbp_org):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT)
    errors = _watch_errors(page)
    try:
        sign_in(page, live_server, gbp_org["emails"]["enterprise_architect"])
        page.goto(live_server + "/batch-import/jobs/%d" % gbp_org["job_id"], wait_until="networkidle")
        expect(page.locator("#main-content")).to_contain_text("Budget: $25.00")
        assert [e for e in errors if "currencyManager" in e or "getSymbol" in e or "format" in e] == [], errors

        page.goto(live_server + "/batch-import/new", wait_until="networkidle")
        page.locator("button", has_text="Set limit").click()
        prefix = page.locator("#main-content span.absolute").filter(has_text="$")
        expect(prefix).to_be_visible()
        assert errors == [], errors
        _shot(page, "batch-import-new-limit")
    finally:
        context.close()


def test_a_job_with_no_recorded_cost_reads_in_words_not_a_zero_amount(browser, live_server):
    info = create_fresh_org("enterprise_architect")
    job_id = _bare_job(info["user_ids"]["enterprise_architect"])
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT)
    try:
        sign_in(page, live_server, info["emails"]["enterprise_architect"])
        page.goto(live_server + "/batch-import/jobs/%d" % job_id, wait_until="networkidle")
        main = page.locator("#main-content")
        expect(main).to_contain_text("Not recorded")
        expect(main).not_to_contain_text("$0.00")
        rows = page.locator("tbody tr")
        expect(rows.first).to_contain_text("Not recorded")
        expect(rows.first).not_to_contain_text("$0.00")
        _shot(page, "batch-import-job-unpriced")

        # The import dashboard agrees with the job page: one unpriced job is not a zero-dollar total.
        page.goto(live_server + "/batch-import/", wait_until="networkidle")
        total = page.get_by_text("Total Cost").locator("xpath=..")
        expect(total).to_contain_text("Not recorded")
        expect(page.locator("#main-content")).not_to_contain_text("$0.00")
        _shot(page, "batch-import-dashboard-unpriced")
    finally:
        context.close()
        _drop(info["org_id"], [job_id])
