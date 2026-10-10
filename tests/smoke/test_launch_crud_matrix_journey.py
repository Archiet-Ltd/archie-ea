"""A data architect sees which applications create, read, update or delete a
data entity.

Fresh organisation. Two applications are added on the application portfolio
page and a data entity on the entity catalog, all through their own forms. The
entity's page starts with no access recorded; the architect records that one
application creates and updates it and the other reads it, through the
page's own application search and operation boxes. The CRUD matrix shows
exactly that, still after a reload, and a second organisation can reach
neither the entity nor its matrix.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _new_page(browser):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1600, "height": 1000})
    # A first sign-in shows the one-time welcome tour over every page; this
    # person has already been through it.
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _add_application(page, base, name):
    page.goto(base + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("btn-add-application").click()
    modal = page.locator("#modal-create")
    expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
    modal.locator("#ca-name").fill(name)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        with page.expect_response(
            lambda r: "/applications/create" in r.url and r.request.method == "POST", timeout=PAGE_TIMEOUT,
        ) as resp:
            modal.get_by_role("button", name="Add Application", exact=True).click()
    assert resp.value.status < 400, "application create failed: %d %s" % (resp.value.status, resp.value.text()[:300])
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)


def _record(page, app_name, operations):
    form = page.locator("[data-record-access]")
    search = form.get_by_label("Application")
    search.fill(app_name)
    option = form.get_by_role("option").filter(has_text=app_name)
    expect(option).to_be_visible(timeout=PAGE_TIMEOUT)
    option.get_by_role("button").click()
    for label in operations:
        form.get_by_label(label, exact=True).check()
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        form.get_by_role("button", name="Record access").click()


def _cells(page, app_name):
    row = page.locator("[data-crud-row]", has_text=app_name)
    expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
    return {
        letter: row.locator('[data-crud-cell="%s"]' % letter).get_attribute("data-crud-value")
        for letter in "CRUD"
    }


def test_data_architect_records_and_reads_the_crud_matrix(browser, live_server):
    base = live_server
    tag = uuid.uuid4().hex[:6]
    crm = "CRM %s" % tag
    billing = "Billing %s" % tag
    entity_name = "Customer %s" % tag

    org1 = create_fresh_org("data_architect")
    org2 = create_fresh_org("data_architect")
    context, page = _new_page(browser)
    sign_in(page, base, org1["emails"]["data_architect"])

    _add_application(page, base, crm)
    _add_application(page, base, billing)

    page.goto(base + "/architecture/data-entities/create", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator("#name").fill(entity_name)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name="Create Entity").click()

    # From the catalog to the entity's own page.
    page.get_by_role("link", name=entity_name).click()
    page.wait_for_url(lambda url: "/architecture/data-entities/" in url and url.rstrip("/").split("/")[-1].isdigit(),
                      timeout=PAGE_TIMEOUT)
    detail_url = page.url
    matrix = page.locator("[data-crud-matrix]")
    expect(matrix.get_by_role("heading", name="CRUD matrix")).to_be_visible(timeout=PAGE_TIMEOUT)
    # Nothing recorded yet: said so, with no rows of zeros.
    expect(matrix.locator("[data-crud-empty]")).to_contain_text("No application access recorded")
    expect(matrix.locator("[data-crud-row]")).to_have_count(0)

    _record(page, crm, ["Create", "Update"])
    _record(page, billing, ["Read"])

    def _assert_matrix():
        expect(page.locator("[data-crud-row]")).to_have_count(2, timeout=PAGE_TIMEOUT)
        assert _cells(page, crm) == {"C": "yes", "R": "no", "U": "yes", "D": "no"}
        assert _cells(page, billing) == {"C": "no", "R": "yes", "U": "no", "D": "no"}
        expect(page.locator("[data-crud-empty]")).to_have_count(0)

    _assert_matrix()
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _assert_matrix()

    # Changing an application's access replaces its row rather than adding one.
    _record(page, billing, ["Read", "Delete"])
    expect(page.locator("[data-crud-row]")).to_have_count(2, timeout=PAGE_TIMEOUT)
    assert _cells(page, billing) == {"C": "no", "R": "yes", "U": "no", "D": "yes"}
    context.close()

    # A second organisation reaches neither the entity nor its matrix.
    context2, page2 = _new_page(browser)
    sign_in(page2, base, org2["emails"]["data_architect"])
    page2.goto(base + "/architecture/data-entities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page2.locator("body")).not_to_contain_text(entity_name)
    response = page2.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 404, "other org opened the entity: %s" % response.status
    context2.close()
