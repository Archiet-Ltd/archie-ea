"""A business owner exports a business case from its detail page.

Goal: "I open the case, choose Export, see the five formats, and the PDF I ask
for is a PDF." The Export menu once opened as an empty box because its items
carried a key the dropdown does not render; this journey opens the menu the way
a person does and asserts the visible items and the file that comes back.

Signed in as an organisation's own non-admin architect, at 1440 wide in light
and 390 wide in dark.
"""

import io
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
        business_case_title = business_case.title

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

        # Ruling 1: the control sits in the page header, right of the title, and the
        # old "Export & share" panel is gone.
        trigger = page.get_by_test_id("bc-export")
        expect(trigger).to_be_visible(timeout=PAGE_TIMEOUT)
        header = page.get_by_role("heading", level=1).locator("xpath=../..")
        assert header.get_by_test_id("bc-export").count() == 1, "Export is not in the page header"
        expect(page.get_by_test_id("bc-export-share-panel")).to_have_count(0)
        expect(page.get_by_text("Export & share")).to_have_count(0)
        expect(page.get_by_test_id("bc-export-hint")).to_have_count(0)
        if width == 390:
            title_box = page.get_by_role("heading", level=1).bounding_box()
            export_box = trigger.bounding_box()
            assert export_box["y"] >= title_box["y"] + title_box["height"], (
                "header actions did not wrap below the title at 390 wide"
            )
            assert "Export" in trigger.inner_text(), "Export lost its label at 390 wide"
        page.screenshot(path=f"/tmp/bcase01d-closed-{width}-{scheme}.png")

        trigger.click()
        menu = page.get_by_role("menu")
        expect(menu).to_be_visible(timeout=PAGE_TIMEOUT)
        for name in FORMATS:
            expect(menu.get_by_text(name, exact=False)).to_be_visible(timeout=PAGE_TIMEOUT)
        # Ruling 3: two labelled groups, every item with a testid, no bare divider.
        expect(menu.get_by_text("Document", exact=True)).to_be_visible()
        expect(menu.get_by_text("Send to a diagram tool", exact=True)).to_be_visible()
        assert menu.locator('[role="separator"]').count() == 0
        for testid in ("docx", "pdf", "mermaid", "lucid", "archi"):
            expect(menu.get_by_test_id(f"bc-export-{testid}")).to_be_visible()
        page.screenshot(path=f"/tmp/bcase01d-open-{width}-{scheme}.png")

        case_title = business_case_title
        documents = {}
        for label, query, mime, magic in (
            ("Word document", "format=docx",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"PK"),
            ("PDF document", "format=pdf", "application/pdf", b"%PDF"),
        ):
            if not menu.is_visible():
                trigger.click()
            with page.expect_download(timeout=PAGE_TIMEOUT) as download_info, context.expect_event(
                "response", predicate=lambda r, q=query: q in r.url, timeout=PAGE_TIMEOUT
            ) as response_info:
                menu.get_by_text(label, exact=False).click()
            response = response_info.value
            # The file is named after the case title.
            stem = "".join(c if c.isalnum() or c in "._-" else "-" for c in case_title)
            assert download_info.value.suggested_filename.startswith(stem[:20]), (
                f"{label} saved as {download_info.value.suggested_filename}"
            )
            assert response.status == 200, f"{label} export answered {response.status}"
            assert mime in response.headers.get("content-type", ""), (
                f"{label} export content type was {response.headers.get('content-type')}"
            )
            # The download tab closes once the attachment arrives, so read the
            # body again through the signed-in context.
            body = context.request.get(response.url).body()
            assert body.startswith(magic), f"{label} body does not start with {magic!r}"
            documents[query] = body

        from docx import Document
        from pypdf import PdfReader

        word_text = "\n".join(
            p.text for p in Document(io.BytesIO(documents["format=docx"])).paragraphs
        )
        pdf_text = " ".join(
            pg.extract_text() for pg in PdfReader(io.BytesIO(documents["format=pdf"])).pages
        )
        assert case_title in word_text, "Word export does not carry the case title"
        assert case_title in " ".join(pdf_text.split()), "PDF export does not carry the case title"
    finally:
        context.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_export_failure_shows_a_plain_toast_and_downloads_nothing(app, browser, live_server):
    """When the PDF cannot be made the person reads one plain sentence, never the
    server's exception text, and no file arrives."""
    from app import db
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org = Organization(name=f"Export Fail Org {suffix}", slug=f"export-fail-{suffix}")
        db.session.add(org)
        db.session.flush()
        owner = _make_owner(db, org, suffix)
        owner_email = owner.email
        business_case, _figures, _links = _seed_financed_case(db, org, owner, suffix)
        case_id = business_case.id

    base = str(live_server)
    context = browser.new_context(
        viewport={"width": 1440, "height": 900}, ignore_https_errors=True
    )
    page = context.new_page()
    downloads = []
    page.on("download", lambda d: downloads.append(d))
    try:
        _login(page, base, owner_email)
        page.goto(f"{base}/business-case/{case_id}", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)

        # The server is a separate process, so the PDF writer's failure is
        # delivered as the answer the route gives when the writer raises.
        page.route(
            "**/export?format=pdf*",
            lambda route: route.fulfill(
                status=500, content_type="text/plain",
                body="Traceback: ReportLabError boom in _build_branded_header",
            ),
        )
        page.get_by_test_id("bc-export").click()
        page.get_by_test_id("bc-export-pdf").click()

        toast = page.locator("#platform-toast-container")
        expect(toast).to_contain_text(
            "The PDF could not be made. Nothing was downloaded.", timeout=PAGE_TIMEOUT
        )
        assert "Traceback" not in toast.inner_text() and "boom" not in toast.inner_text()
        assert downloads == [], "a file was downloaded although the PDF failed"
        expect(page.get_by_test_id("bc-export")).to_be_enabled(timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_pdf_writer_failure_answers_an_error_status(app, monkeypatch):
    """With the PDF writer made to raise, the route answers an error status and
    no document body (the page turns that into the plain sentence above)."""
    from app import db
    from app.models.organization import Organization
    from app.services.togaf_deliverable_export_service import TOGAFDeliverableExportService

    def boom(self, *args, **kwargs):
        raise RuntimeError("pdf writer exploded")

    monkeypatch.setattr(TOGAFDeliverableExportService, "export_business_case_to_pdf", boom)

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org = Organization(name=f"Export Raise Org {suffix}", slug=f"export-raise-{suffix}")
        db.session.add(org)
        db.session.flush()
        owner = _make_owner(db, org, suffix)
        business_case, _figures, _links = _seed_financed_case(db, org, owner, suffix)
        db.session.commit()
        case_id, owner_id = business_case.id, owner.id
        from tests._session_test_helpers import mint_test_sid

        sid = mint_test_sid(owner_id, organization_id=org.id)

    previous = app.config.get("PROPAGATE_EXCEPTIONS")
    app.config["PROPAGATE_EXCEPTIONS"] = False
    try:
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(owner_id)
            session["_fresh"] = True
            session["_sid"] = sid
        response = client.get(f"/business-case/{case_id}/export?format=pdf")
    finally:
        app.config["PROPAGATE_EXCEPTIONS"] = previous
    assert response.status_code >= 400, f"answered {response.status_code}"
    assert not response.data.startswith(b"%PDF")
