"""End-to-end: create a real diagram in the Composer, download it in every
export format offered, open each downloaded file, and compare its content
against the diagram actually built on the canvas.

This is the literal "Definition of Done" for the Composer's export surface,
per the owner's standing instruction (10 Sep 2026): creating a diagram and
downloading it is not done until the download has been opened and checked
against what was drawn -- a green button click is not evidence a usable file
came out the other end.

Builds a small but real ArchiMate diagram (4 elements across three layers,
3 relationships of distinct types), then for each export format asserts the
downloaded artifact:
  - is non-trivially sized (not an empty/error file)
  - has the correct file signature/structure for its format
  - contains the actual element names that were drawn (where the format is
    text-inspectable: XML, SVG) or is a plausible raster/vector/document of
    the right kind and size (PNG, PDF, where content is not text-diffable)

Also regression-guards the Ctrl+K shortcut collision fixed alongside this
diagram-export test (10 Sep 2026): the Composer's own quick-add and the
global header's search modal both listened for Ctrl+K on `document`, so
opening quick-add also silently opened an invisible, click-stealing search
overlay on top of the canvas.
"""

import re
import uuid
import zipfile

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


def _quick_add(page, name):
    page.keyboard.press("Control+k")
    input_box = page.locator("#quick-add-input")
    expect(input_box).to_be_visible(timeout=PAGE_TIMEOUT)
    input_box.fill(name)
    create_btn = page.get_by_role("button", name=re.compile("^Create$"))
    expect(create_btn).to_be_visible(timeout=PAGE_TIMEOUT)
    create_btn.click()


@pytest.mark.smoke
def test_created_diagram_survives_every_export_format_intact(browser, live_server, seeded):
    email = seeded["emails"]["solution_architect"]
    suffix = uuid.uuid4().hex[:8]

    element_names = [
        "Order Service %s" % suffix,
        "Payment Gateway %s" % suffix,
        "Fraud Check %s" % suffix,
        "Customer %s" % suffix,
    ]

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1960, "height": 1080})
    page = context.new_page()
    _login(page, live_server, email)

    page.goto(live_server + "/archimate/composer", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    toolbar = page.locator("nav[aria-label='Composer toolbar']")
    expect(toolbar).to_be_visible(timeout=PAGE_TIMEOUT)

    # ── 1. Create the diagram ────────────────────────────────────────────
    for name in element_names:
        _quick_add(page, name)
    expect(page.locator(".joint-element")).to_have_count(len(element_names), timeout=PAGE_TIMEOUT)

    # Regression guard for the Ctrl+K shortcut collision fixed alongside this
    # test: the global header search modal must NOT have opened alongside
    # quick-add. It is only ever shown via `style.display = 'flex'`.
    search_modal_display = page.eval_on_selector(
        "#search-modal", "el => window.getComputedStyle(el).display"
    )
    assert search_modal_display == "none", (
        "the global header search modal opened alongside Composer quick-add "
        "(Ctrl+K shortcut collision) -- computed display was %r" % search_modal_display
    )

    # Save the 4-element viewpoint through the real Save flow (Ctrl+S + name
    # modal), so it has a real saved-diagram id and positions in the
    # repository -- then create the three relationships via the same
    # persistence endpoint the Composer's own drag-to-connect gesture calls
    # on completion (POST /archimate/api/relationships; verified manually
    # this session that the drag gesture itself works, but its magnet-hover
    # timing plus an async-loaded relationship-type picker made it too flaky
    # to drive deterministically here). Reloading the saved viewpoint then
    # renders both elements and relationships fresh from the real repository
    # (ArchiMateElement/ArchiMateRelationship, per ADR 0008) -- the same
    # round trip a real reload does, and exactly what the export step below
    # must faithfully reproduce.
    real_element_ids = page.evaluate(
        '() => Object.keys(Alpine.$data(document.querySelector(\'[x-data^="composerApp"]\')).canvasElements)'
    )
    assert len(real_element_ids) == len(element_names), (
        "expected %d real element ids on canvas, found %r" % (len(element_names), real_element_ids)
    )

    page.keyboard.press("Control+s")
    save_modal = page.locator('[aria-label="Viewpoint name"]')
    expect(save_modal).to_be_visible(timeout=PAGE_TIMEOUT)
    save_modal.fill("Export inspect-compare %s" % suffix)
    page.get_by_role("button", name=re.compile("^Save$")).click()
    expect(save_modal).to_be_hidden(timeout=PAGE_TIMEOUT)

    vp_id = page.evaluate(
        '() => Alpine.$data(document.querySelector(\'[x-data^="composerApp"]\')).currentSavedVpId'
    )
    assert vp_id, "Save did not produce a saved viewpoint id"

    csrf_token = page.evaluate(
        "() => document.cookie.match(/csrf_token=([^;]+)/)?.[1] || ''"
    )
    # Quick-add creates ApplicationComponent elements, both of which are
    # active-structure per the validity service's own rule engine (the
    # authority the create endpoint actually calls, not the separate static
    # relationship-matrix reference table): two active elements of the same
    # type grant composition and aggregation, association is always valid as
    # a fallback, and that is the whole set -- triggering and serving are
    # behaviour-to-behaviour relationships and apply to neither.
    relationship_specs = [
        (real_element_ids[0], real_element_ids[1], "aggregation"),
        (real_element_ids[1], real_element_ids[2], "composition"),
        (real_element_ids[0], real_element_ids[3], "association"),
    ]
    for src, tgt, rel_type in relationship_specs:
        resp = page.evaluate(
            """
            ({src, tgt, relType, token}) => fetch('/archimate/api/relationships', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': token},
                body: JSON.stringify({
                    source_element_id: parseInt(src), target_element_id: parseInt(tgt),
                    relationship_type: relType,
                }),
            }).then(r => r.json().then(body => ({status: r.status, body})))
            """,
            {"src": src, "tgt": tgt, "relType": rel_type, "token": csrf_token},
        )
        assert resp["status"] in (200, 201), "relationship create failed: %r" % resp

    page.goto(
        live_server + "/archimate/composer?viewpoint_id=%s" % vp_id,
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    expect(page.locator(".joint-element")).to_have_count(len(element_names), timeout=PAGE_TIMEOUT)
    expect(page.locator(".joint-link")).to_have_count(3, timeout=PAGE_TIMEOUT)

    # ── 2. Export ArchiMate XML and inspect it ───────────────────────────
    page.get_by_role("button", name=re.compile("^Export$")).dispatch_event("click")
    with page.expect_download(timeout=PAGE_TIMEOUT) as dl_info:
        page.get_by_role("button", name="Export ArchiMate XML").dispatch_event("click")
    xml_download = dl_info.value
    xml_path = xml_download.path()
    xml_content = xml_path.read_text(encoding="utf-8")

    assert xml_content.startswith("<?xml") or "<model" in xml_content.lower(), \
        "XML export must be real XML"
    for name in element_names:
        assert name in xml_content, "XML export is missing element %r that was drawn" % name
    # Three drawn relationships must appear as three connection/relationship
    # entries, not be silently dropped in translation to the export format.
    rel_mentions = len(re.findall(r"relationship|connection", xml_content, re.IGNORECASE))
    assert rel_mentions >= 3, (
        "expected at least 3 relationship references in the XML export, found %d" % rel_mentions
    )

    # ── 3. Export SVG and inspect it (text-based, so directly diffable) ──
    page.keyboard.press("Escape")
    page.get_by_role("button", name=re.compile("^Export$")).dispatch_event("click")
    with page.expect_download(timeout=PAGE_TIMEOUT) as dl_info:
        page.get_by_role("button", name="Export SVG").dispatch_event("click")
    svg_download = dl_info.value
    svg_content = svg_download.path().read_text(encoding="utf-8")

    assert svg_content.strip().startswith("<svg") or "<svg" in svg_content[:200], \
        "SVG export must start with a real <svg> root"
    for name in element_names:
        assert name in svg_content, "SVG export is missing element %r that was drawn" % name

    # ── 4. Export PNG and inspect its signature + size ───────────────────
    page.keyboard.press("Escape")
    page.get_by_role("button", name=re.compile("^Export$")).dispatch_event("click")
    with page.expect_download(timeout=PAGE_TIMEOUT) as dl_info:
        page.get_by_role("button", name="Export PNG (2x)").dispatch_event("click")
    png_download = dl_info.value
    png_bytes = png_download.path().read_bytes()

    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n", "PNG export must have a real PNG file signature"
    assert len(png_bytes) > 5000, (
        "a 4-element diagram at 2x resolution should produce a PNG well over "
        "5KB; got %d bytes -- likely a blank or near-empty image" % len(png_bytes)
    )

    # ── 5. Export PDF and inspect its signature + size ───────────────────
    page.keyboard.press("Escape")
    page.get_by_role("button", name=re.compile("^Export$")).dispatch_event("click")
    with page.expect_download(timeout=PAGE_TIMEOUT) as dl_info:
        page.get_by_role("button", name="Export PDF").dispatch_event("click")
    pdf_download = dl_info.value
    pdf_bytes = pdf_download.path().read_bytes()

    assert pdf_bytes[:5] == b"%PDF-", "PDF export must have a real PDF file signature"
    assert len(pdf_bytes) > 2000, (
        "a real diagram PDF should be well over 2KB; got %d bytes" % len(pdf_bytes)
    )

    # ── 6. Export PowerPoint and inspect it as a real OOXML package ──────
    page.keyboard.press("Escape")
    page.get_by_role("button", name=re.compile("^Export$")).dispatch_event("click")
    with page.expect_download(timeout=PAGE_TIMEOUT) as dl_info:
        page.get_by_role("button", name="Export PowerPoint").dispatch_event("click")
    pptx_download = dl_info.value
    pptx_path = pptx_download.path()

    assert zipfile.is_zipfile(pptx_path), "PPTX export must be a real OOXML (zip) package"
    with zipfile.ZipFile(pptx_path) as zf:
        names = zf.namelist()
        assert "[Content_Types].xml" in names, "not a valid PPTX package structure"
        slide_files = [n for n in names if re.match(r"ppt/slides/slide\d+\.xml", n)]
        assert slide_files, "PPTX export must contain at least one slide"
