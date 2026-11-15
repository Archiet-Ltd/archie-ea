"""A person on a vendor record reaches document upload with that vendor chosen, and the
vendor mapping view, instead of the record embedding those tools itself.

Goal: "I am looking at Northwind and want to analyse its contract document, then map it."
Asserts the landing pages have content for the vendor clicked: the upload panel has the
vendor selected, the mapping tab is the vendor mapping view. Runs at 1440 light and 390 dark.
"""
import pytest

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_vendor(org_id, name):
    from app import create_app, db
    from app.models.vendor.vendor_organization import VendorOrganization

    app = create_app("testing")
    with app.app_context():
        v = VendorOrganization(name=name, vendor_type="software_vendor")
        db.session.add(v)
        db.session.commit()
        vid = v.id
        db.session.remove()
    return vid


@pytest.mark.parametrize("viewport,scheme", [
    ({"width": 1440, "height": 900}, "light"),
    ({"width": 390, "height": 844}, "dark"),
])
def test_vendor_record_hands_off_to_upload_and_mapping(browser, live_server, seeded, viewport, scheme):
    import uuid
    name = "Northwind Handoff %s %s" % (scheme, uuid.uuid4().hex[:6])
    vid = _seed_vendor(seeded["ids"]["org"], name)
    ctx = browser.new_context(viewport=viewport, color_scheme=scheme)
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto("%s/applications/vendors/%d" % (live_server, vid), timeout=PAGE_TIMEOUT)
        page.get_by_role("heading", name=name).first.wait_for(timeout=PAGE_TIMEOUT)
        # the record no longer embeds the analyzer or the mapping dialog
        assert page.locator("#unified-mapping-modal, [x-show='analyzerOpen']").count() == 0

        page.get_by_role("link", name="Analyze Document").click()
        page.wait_for_url(lambda u: "panel=docs" in u and "vendor=" in u, timeout=PAGE_TIMEOUT)
        assert "vendor=%d" % vid in page.url
        panel = page.locator("#document-upload-panel")
        panel.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        select = panel.locator("select[aria-label^='Select vendor']")
        select.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        page.wait_for_function(
            "(id) => { const s = document.querySelector(\"#document-upload-panel select[aria-label^='Select vendor']\");"
            " return s && s.value === id && s.selectedOptions[0] && s.selectedOptions[0].text.length > 0 }",
            arg=str(vid), timeout=PAGE_TIMEOUT)
        assert name in select.locator("option:checked").inner_text()

        page.goto("%s/applications/vendors/%d" % (live_server, vid), timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Map to Architecture").click()
        page.wait_for_url(lambda u: "tab=mapping" in u, timeout=PAGE_TIMEOUT)
        assert "mapping" in page.url
        assert len(page.inner_text("main, body").strip()) > 80
    finally:
        ctx.close()
