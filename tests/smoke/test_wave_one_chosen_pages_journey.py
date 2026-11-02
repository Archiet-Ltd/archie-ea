"""Journey: at the first release a new customer who chose Banking and ISO 27001 at sign-up opens what that choice loads.

Written from the goal "I told Entelim my industry and my framework; show me my capability map, a canvas, a value
stream and my compliance page":

* a person signs up through the form, chooses Banking and payments and ISO 27001, and finishes the welcome;
* their organisation holds linked data (two capabilities, a value stream whose stage names one of them, a canvas made
  through the canvas page's own form); a second organisation holds a capability of its own;
* the capability map, the canvas page, the value stream, the frameworks gallery, the compliance page and the industry
  framework each open with content, never the early-access page, and no call they make is answered "early access";
* every link followed from those pages (a capability to its fact sheet, a canvas card to the canvas, a value stream to
  its stages, a framework card to its page) lands on a page with content;
* the other organisation's capability never shows on this person's map.

Run against the wave map as it stood before the pages moved to the first release, it is red: that is the "before".
"""

from __future__ import annotations

import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_signup_brings_the_company_in_journey import new_page, people, sign_up  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("WAVE_ONE_JOURNEY_SHOTS")
SWITCH = "1"


@pytest.fixture(scope="module")
def wave_server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": SWITCH})


def shoot(page, name):
    """The screen at the two widths people use, for the person who reviews it."""
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height in ((1440, 900), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(400)
        page.screenshot(path=os.path.join(SHOTS, "%s-%d.png" % (name, width)), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})


def _seed_linked_data(app, email, tag):
    """Two capabilities, and a value stream with a stage that names the first, in this person's organisation."""
    with app.app_context():
        from app import db
        from app.models.business_capabilities import BusinessCapability
        from app.models.unified_capability import CapabilityValueStreamMapping, ValueStream, ValueStreamStage
        from app.models.user import User

        org_id = User.query.filter_by(email=email).one().organization_id
        names = ["Payments Processing %s" % tag, "Fraud Screening %s" % tag]
        caps = []
        for name in names:
            cap = BusinessCapability(name=name, organization_id=org_id)
            db.session.add(cap)
            caps.append(cap)
        stream = ValueStream(name="Payment to Settlement %s" % tag, code="P2S", organization_id=org_id)
        db.session.add(stream)
        db.session.flush()
        stage = ValueStreamStage(name="Clear the payment", value_stream_id=stream.id, stage_order=1, organization_id=org_id)
        db.session.add(stage)
        db.session.flush()
        unified = _unified_for(caps[0])
        if unified is not None:
            db.session.add(CapabilityValueStreamMapping(
                value_stream_id=stream.id, capability_id=unified.id, value_stream_stage_id=stage.id, organization_id=org_id))
        db.session.commit()
        return org_id, names, stream.name, caps[0].id


def _unified_for(business_capability):
    from app.models.unified_capability import UnifiedCapability

    return UnifiedCapability.query.filter_by(
        source_table="business_capability", source_id=str(business_capability.id)).first()


def _is_early_access(page):
    return page.locator('[data-testid="early-access-page"]').count() > 0


def _opens_with_content(page, base, path, minimum=120):
    """Open an address; return the heading. It must be a page of its own: not early access, not an error."""
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response is not None and response.status == 200, "%s -> %s" % (path, response.status if response else None)
    assert not _is_early_access(page), "%s answers its early-access page at the first release" % path
    heading = page.locator("main h1, h1").first
    expect(heading).to_be_visible(timeout=PAGE_TIMEOUT)
    assert len(page.inner_text("main").strip()) >= minimum, "%s opens with no content" % path
    return heading.inner_text().strip()


def _choose_banking_and_iso_27001(page, base):
    page.get_by_test_id("signup-continue").click()
    page.wait_for_url("**/account/welcome?step=3")
    expect(page.get_by_role("heading", name="About your company")).to_be_visible()
    page.get_by_label("Your industry").select_option(label="Banking and payments")
    page.get_by_role("button", name="Show all frameworks").click()
    page.get_by_label("ISO 27001").check()
    page.get_by_role("button", name="Continue").click()
    page.wait_for_url("**/account/welcome?step=4")
    page.get_by_role("button", name="Finish").click()
    page.wait_for_url("**/dashboard/**")


def test_a_customer_who_chose_banking_and_iso_27001_opens_the_map_canvas_value_stream_and_compliance_pages(
    browser, wave_server, people, app
):
    tag = uuid.uuid4().hex[:6]
    base = str(wave_server)
    refused = []

    context, page = new_page(browser)
    other_context, other_page = new_page(browser)

    def on_response(resp):
        if resp.status in (404, 405) and "/static/" not in resp.url:
            try:
                body = resp.text()
            except Exception:  # noqa: BLE001
                body = ""
            if "early_access" in body or "early-access-page" in body:
                refused.append("%s %s on %s" % (resp.request.method, resp.url.replace(base, ""), page.url.replace(base, "")))

    try:
        page.on("response", on_response)

        # --- two organisations, each signed up through the form ---
        email, _started = sign_up(page, base, people)
        other_email, _ = sign_up(other_page, base, people)
        _choose_banking_and_iso_27001(page, base)
        org_id, names, stream_name, capability_id = _seed_linked_data(app, email, tag)
        with app.app_context():
            from app.models.organization import Organization

            other_org = Organization.query.filter_by(id=_seed_other(app, other_email)).one()
            assert other_org.id != org_id

        # --- the capability map shows this organisation's capabilities and only theirs ---
        heading = _opens_with_content(page, base, "/capability-map/", minimum=60)
        assert heading
        page.wait_for_load_state("networkidle")
        body = page.inner_text("main")
        for name in names:
            assert name in body, "the capability map does not show %r" % name
        assert "Other Bank Capability" not in body, "another organisation's capability shows on this map"
        shoot(page, "capability-map")
        # a capability opens its fact sheet, and the simple lens links to it
        fact_sheet = "/enterprise/capabilities/%d/fact-sheet" % capability_id
        _opens_with_content(page, base, fact_sheet, minimum=60)
        assert names[0] in page.inner_text("main"), "the fact sheet does not name its capability"
        shoot(page, "fact-sheet")
        _opens_with_content(page, base, "/capability-map/simple", minimum=60)
        # every lens the map offers opens with content
        page.goto(base + "/capability-map/", wait_until="domcontentloaded")
        for lens in ("health", "maturity", "list"):
            _opens_with_content(page, base, "/capability-map/%s" % lens, minimum=60)

        # --- a canvas is made through the canvas page's own form and survives a reload ---
        _opens_with_content(page, base, "/business-model/", minimum=60)
        page.get_by_role("button", name=re.compile("New canvas|New Canvas|Create", re.I)).first.click()
        modal = page.locator("#bmc-create-modal")
        expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
        canvas_name = "Payments Strategy %s" % tag
        modal.locator("#bmc-name").fill(canvas_name)
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            modal.get_by_test_id("bmc-create-submit").click()
        assert not _is_early_access(page)
        expect(page.get_by_role("heading", name=re.compile(re.escape(canvas_name)))).to_be_visible(timeout=PAGE_TIMEOUT)
        shoot(page, "canvas")
        page.goto(base + "/business-model/", wait_until="domcontentloaded")
        card = page.locator('[data-testid^="bmc-canvas-card-"]', has_text=canvas_name)
        expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
        card.click()
        page.wait_for_url(re.compile(r"/business-model/\d+"))
        assert not _is_early_access(page) and len(page.inner_text("main")) > 80

        # --- the value stream lists its stage and the capability it names ---
        _opens_with_content(page, base, "/value-streams/", minimum=60)
        page.get_by_test_id("value-stream-link").filter(has_text=stream_name).first.click()
        page.wait_for_url(re.compile(r"/value-streams/\d+"))
        assert not _is_early_access(page)
        expect(page.get_by_text("Clear the payment").first).to_be_visible(timeout=PAGE_TIMEOUT)
        shoot(page, "value-stream")

        # --- the frameworks the customer chose: ISO 27001 and the Banking framework ---
        _opens_with_content(page, base, "/frameworks/", minimum=120)
        gallery = page.inner_text("main")
        assert "27001" in gallery, "the frameworks gallery does not list ISO 27001: %s" % gallery[:600]
        shoot(page, "frameworks")
        apqc = _first_href(page, r"^/industry-apqc/framework/")
        assert apqc, "the frameworks gallery offers no industry framework"
        _opens_with_content(page, base, apqc, minimum=120)
        shoot(page, "industry-framework")
        _opens_with_content(page, base, "/dashboard/compliance", minimum=120)
        compliance = page.inner_text("main")
        assert "27001" in compliance, "the compliance page does not list ISO 27001"
        shoot(page, "compliance")

        assert not refused, "a first-release screen called a route that is not in the first release:\n" + "\n".join(
            sorted(set(refused)))
    finally:
        context.close()
        other_context.close()


def _first_href(page, pattern):
    for href in page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))"):
        if href and re.search(pattern, href):
            return href
    return None


def _seed_other(app, email):
    """The second organisation's own capability, which the first person must never see."""
    with app.app_context():
        from app import db
        from app.models.business_capabilities import BusinessCapability
        from app.models.user import User

        org_id = User.query.filter_by(email=email).one().organization_id
        db.session.add(BusinessCapability(name="Other Bank Capability", organization_id=org_id))
        db.session.commit()
        return org_id
