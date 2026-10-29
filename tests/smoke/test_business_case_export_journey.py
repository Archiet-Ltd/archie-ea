"""A business owner exports a business case from its detail page.

Goal: "I open the case, choose Export, see the five formats, and the PDF I ask
for is a PDF." The Export menu once opened as an empty box because its items
carried a key the dropdown does not render; this journey opens the menu the way
a person does and asserts the visible items and the file that comes back.

Signed in as an organisation's own non-admin architect, at 1440 wide in light
and 390 wide in dark.
"""

import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.test_business_case_detail_journey import (
    PAGE_TIMEOUT,
    _login,
    _make_owner,
    _seed_financed_case,
)

FORMATS = [
    "Word document",
    "PDF document",
    "Mermaid",
    "Lucidchart",
    "Archi",
]


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize(
    "width,scheme", [(1440, "light"), (390, "dark")], ids=["1440-light", "390-dark"]
)
def test_export_menu_lists_the_formats_and_pdf_downloads(
    app, browser, live_server, width, scheme
):
    from app import db
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org = Organization(name=f"Export Org {suffix}", slug=f"export-org-{suffix}")
        db.session.add(org)
        db.session.flush()
        owner = _make_owner(db, org, suffix)
        owner_email = owner.email
        business_case, _figures, _links = _seed_financed_case(db, org, owner, suffix)
        case_id = business_case.id

    base = str(live_server)
    context = browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme,
        ignore_https_errors=True,
    )
    page = context.new_page()
    try:
        _login(page, base, owner_email)
        page.goto(f"{base}/business-case/{case_id}", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)

        # The header is the shell's: the case title, and no stray "None".
        expect(page.get_by_role("heading", level=1)).to_be_visible(timeout=PAGE_TIMEOUT)
        header_text = page.get_by_role("heading", level=1).locator("xpath=..").inner_text()
        assert "None" not in header_text

        page.get_by_role("button", name="Export", exact=True).click()
        menu = page.get_by_role("menu")
        expect(menu).to_be_visible(timeout=PAGE_TIMEOUT)
        for name in FORMATS:
            expect(menu.get_by_text(name, exact=False)).to_be_visible(timeout=PAGE_TIMEOUT)
        page.screenshot(path=f"/tmp/bcase01b-export-menu-{width}-{scheme}.png")

        with context.expect_event(
            "response", predicate=lambda r: "format=pdf" in r.url, timeout=PAGE_TIMEOUT
        ) as response_info:
            menu.get_by_text("PDF document", exact=False).click()
        response = response_info.value
        assert response.status == 200, f"PDF export answered {response.status}"
        assert "application/pdf" in response.headers.get("content-type", ""), (
            f"PDF export content type was {response.headers.get('content-type')}"
        )
    finally:
        context.close()
