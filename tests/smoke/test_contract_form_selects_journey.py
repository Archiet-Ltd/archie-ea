"""Journey: the procurement contract form's four selects hold their options
and keep the stored value.

Goal of the person: a procurement lead records a contract, choosing a vendor,
a type, a category and a status, sees those four choices on the saved contract,
reopens it to amend it and finds every choice still in place, and saves again
without losing any of them.

Before the fix the four fields rendered their options as loose <option> tags
printed as bare text below each select, and saving an existing contract cleared
all four (data loss). This journey fails on 46064fd54 and passes after the fix:
 - on create it asserts each select actually contains its options (not loose
   text) and that no vendor name is printed outside a select;
 - it asserts all four choices appear on the detail page, survive a reload, are
   preselected when the contract is reopened for editing, and survive a save
   with no changes.

Vendors are shared reference data (not tenant-scoped, VendorOrganization), and
the shared smoke seed creates none, so this test seeds two vendors of its own
(the brief requires at least two) and removes them again at the end so the tree
is left clean.
"""

import re
import time

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


def _assert_vendor_names_only_in_select(page, vendor_names):
    """Fail if any vendor name is rendered outside the vendor <select>.

    Walks every element whose OWN text (its direct text nodes, not those of
    descendants) contains the name. Each such element must be an <option> that
    sits inside #vendor_id. The pre-fix defect printed the names as loose
    <option> text outside the select; those loose options are not inside
    #vendor_id, so they are reported here.
    """
    offenders = page.evaluate(
        """(names) => {
            const bad = [];
            const vendorSelect = document.querySelector('#vendor_id');
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
            let el = walker.currentNode;
            while (el) {
                let ownText = '';
                for (const node of el.childNodes) {
                    if (node.nodeType === Node.TEXT_NODE) ownText += node.textContent;
                }
                for (const name of names) {
                    if (ownText.includes(name)) {
                        const insideVendorSelect =
                            el.tagName === 'OPTION' &&
                            vendorSelect &&
                            vendorSelect.contains(el);
                        if (!insideVendorSelect) {
                            bad.push(name + ' :: <' + el.tagName.toLowerCase() + '>');
                        }
                    }
                }
                el = walker.nextNode();
            }
            return bad;
        }""",
        list(vendor_names),
    )
    assert not offenders, \
        "Vendor name(s) rendered outside the vendor select (loose text): %r" % offenders


@pytest.fixture
def two_vendors():
    """Two shared vendors the contract form can offer, removed afterwards.

    VendorOrganization is shared reference data with a globally unique name, so
    the names are suffixed with a run-unique token to avoid colliding with a
    parallel run or a leftover row. Yields the two names in menu (alphabetical)
    order, which is how the form lists them.
    """
    from app import create_app, db
    from app.models.vendor.vendor_organization import VendorOrganization

    token = "%d" % int(time.time() * 1000)
    names = sorted(["Selects Journey Vendor A %s" % token,
                    "Selects Journey Vendor B %s" % token])
    app = create_app("testing")
    created_ids = []
    with app.app_context():
        for name in names:
            vendor = VendorOrganization(name=name, vendor_type="software_vendor")
            db.session.add(vendor)
            db.session.flush()
            created_ids.append(vendor.id)
        db.session.commit()
    try:
        yield names
    finally:
        with app.app_context():
            for vid in created_ids:
                row = db.session.get(VendorOrganization, vid)
                if row is not None:
                    db.session.delete(row)
            db.session.commit()


@pytest.mark.smoke
@pytest.mark.journey
def test_contract_form_selects_persist(browser, live_server, seeded, two_vendors):
    """A procurement lead creates a contract with all four selects set,
    verifies they appear on the detail page, edits the contract (no changes),
    and verifies all four selects are still set correctly.

    Also asserts no vendor name appears as loose text outside the select on the
    form (the defect being fixed).
    """
    vendor_a, vendor_b = two_vendors

    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())
    try:
        _login(page, live_server, seeded["emails"]["procurement"])

        name = "Selects Contract %d" % int(time.time() * 1000)
        create_url = live_server + "/procurement/contracts/new"
        list_url = live_server + "/procurement/contracts"

        # ---- CREATE (GET /procurement/contracts/new) --------------------------
        page.goto(create_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        vendor_select = page.locator("#vendor_id")
        type_select = page.locator("#contract_type")
        category_select = page.locator("#contract_category")
        status_select = page.locator("#status")

        expect(vendor_select).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(type_select).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(category_select).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(status_select).to_be_visible(timeout=PAGE_TIMEOUT)

        # Each select must hold its own options, not print them as loose text.
        # The two seeded vendors must both be options inside the vendor select.
        vendor_option_labels = [o.inner_text().strip()
                                for o in vendor_select.locator("option").all()]
        assert vendor_a in vendor_option_labels, \
            "Vendor %r is not an option inside the vendor select: %r" % (vendor_a, vendor_option_labels)
        assert vendor_b in vendor_option_labels, \
            "Vendor %r is not an option inside the vendor select: %r" % (vendor_b, vendor_option_labels)

        assert len(type_select.locator("option").all()) > 1, \
            "Type select should hold options beyond 'Not specified'"
        assert len(category_select.locator("option").all()) > 1, \
            "Category select should hold options beyond 'Not specified'"
        assert len(status_select.locator("option").all()) > 1, \
            "Status select should hold options beyond 'Not specified'"

        # No vendor name may appear as loose text outside a select. Before the
        # fix the options were printed as bare <option> text under the control,
        # so each vendor name rendered twice: once as an option inside the
        # <select>, once as loose text in the surrounding markup. Every element
        # whose own text (ignoring the text of descendants) contains a vendor
        # name must therefore be an <option>, and it must live inside the vendor
        # select. A match in any other element is the loose text being fixed.
        _assert_vendor_names_only_in_select(page, (vendor_a, vendor_b))

        # Fill the form, choosing a specific option in each of the four selects.
        page.fill("#contract_name", name)
        page.fill("#start_date", "2026-01-01")

        vendor_select.select_option(label=vendor_a)
        selected_vendor = vendor_select.locator("option:checked").inner_text().strip()
        assert selected_vendor == vendor_a

        type_select.select_option("license")
        selected_type = type_select.locator("option:checked").inner_text().strip()

        category_select.select_option("software")
        selected_category = category_select.locator("option:checked").inner_text().strip()

        status_select.select_option("active")
        selected_status = status_select.locator("option:checked").inner_text().strip()

        # Submit the form.
        with page.expect_response(
            lambda r: "/procurement/contracts/new" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as created:
            page.locator('form[action*="/procurement/contracts/new"] button[type="submit"]').first.click()
        assert created.value.status < 400, "create POST failed: %s" % created.value.status
        page.wait_for_url(re.compile(r"/procurement/contracts/\d+$"), timeout=PAGE_TIMEOUT)
        detail_url = page.url
        contract_id = re.search(r"/contracts/(\d+)$", detail_url).group(1)

        # ---- DETAIL PAGE: all four choices are shown --------------------------
        expect(page.get_by_text(name, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(selected_vendor, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(selected_type, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(selected_category, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(selected_status, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)

        # ---- The contract appears in the list ---------------------------------
        page.goto(list_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(name, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)

        # ---- The saved values survive a fresh load of the detail page ---------
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(name, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(selected_vendor, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(selected_type, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(selected_category, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(selected_status, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)

        # ---- EDIT (GET): every choice is preselected --------------------------
        edit_url = live_server + "/procurement/contracts/%s/edit" % contract_id
        page.goto(edit_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        vendor_select = page.locator("#vendor_id")
        type_select = page.locator("#contract_type")
        category_select = page.locator("#contract_category")
        status_select = page.locator("#status")

        expect(vendor_select).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(type_select).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(category_select).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(status_select).to_be_visible(timeout=PAGE_TIMEOUT)

        assert vendor_select.locator("option:checked").inner_text().strip() == selected_vendor, \
            "Vendor not preselected on edit"
        assert type_select.locator("option:checked").inner_text().strip() == selected_type, \
            "Type not preselected on edit"
        assert category_select.locator("option:checked").inner_text().strip() == selected_category, \
            "Category not preselected on edit"
        assert status_select.locator("option:checked").inner_text().strip() == selected_status, \
            "Status not preselected on edit"

        # No vendor name printed as loose text on the edit page either.
        _assert_vendor_names_only_in_select(page, (vendor_a, vendor_b))

        # ---- SAVE WITH NO CHANGES (POST /edit) --------------------------------
        with page.expect_response(
            lambda r: "/edit" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as edited:
            page.locator('form[action*="/edit"] button[type="submit"]').first.click()
        assert edited.value.status < 400, "edit POST failed: %s" % edited.value.status

        # ---- All four choices survive the no-change save ----------------------
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(name, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(selected_vendor, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(selected_type, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(selected_category, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(selected_status, exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)

    finally:
        context.close()
