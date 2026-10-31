"""New vendor analysis: capabilities, APQC processes and value streams each
resolve to mapped vendors, and a selection with nothing mapped says so.

Before this change, the value-streams tab's picker read a hard-coded "standard"
list with no real row behind it, so a value-stream selection could never
resolve to a vendor; the capability bridge never reaches past name equality
because no demo capability carries its unified twin's FK; and a search that
found nothing told the person only that they could proceed, never why nothing
was found or where to fix it. Each scenario below is red on the line this
branch forked from and green here.

Two organisations each carry a capability of the same name; only one has
vendor coverage recorded against it, and the other must see the honest empty
state, never the first organisation's vendors.
"""
import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [(1440, 900), (390, 844)]


def _seed_discovery_org(covered=True):
    """A fresh organisation with one person, and (when `covered`) a
    capability, an APQC process and a value stream that all resolve to the
    same vendor's coverage; an uncovered capability either way, for the
    honest-empty-state case.

    Returns the ids/names the journey selects by, and the vendor's name when
    `covered`.
    """
    ids = create_fresh_org("solution_architect", platform_role="Architect")
    org_id = ids["org_id"]
    suffix = ids["suffix"]

    from app import create_app, db

    app = create_app("testing")
    out = {"org_id": org_id, "suffix": suffix, "emails": ids["emails"]}
    with app.app_context():
        from app.models.apqc_process import APQCProcess
        from app.models.business_capabilities import BusinessCapability
        from app.models.relationship_tables import value_stream_stage_capabilities
        from app.models.unified_capability import (
            UnifiedCapability, ValueStream, ValueStreamStage,
        )
        from app.models.vendor.vendor_organization import (
            VendorOrganization, VendorProduct, VendorProductCapability,
        )
        from app.models.vendor_product_apqc_mapping import VendorProductAPQCMapping

        # The capability nothing is mapped to: present either way, so the
        # same org proves both the honest-empty case and (when `covered`)
        # the found-a-vendor case side by side.
        unmapped_unified = UnifiedCapability(
            name="Unmapped Capability %s" % suffix, code="SMOKE-UNMAPPED-%s" % suffix,
            level=1, organization_id=org_id,
        )
        db.session.add(unmapped_unified)
        db.session.flush()
        unmapped_bc = BusinessCapability(
            name="Unmapped Capability %s" % suffix, organization_id=org_id,
            deprecated_in_favor_of_id=unmapped_unified.id,
        )
        db.session.add(unmapped_bc)
        db.session.commit()
        out["unmapped_capability_name"] = unmapped_unified.name

        if not covered:
            db.session.remove()
            return out

        covered_unified = UnifiedCapability(
            name="Calibration Services %s" % suffix, code="SMOKE-CAL-%s" % suffix,
            level=1, organization_id=org_id,
        )
        db.session.add(covered_unified)
        db.session.flush()
        covered_bc = BusinessCapability(
            name="Calibration Services %s" % suffix, organization_id=org_id,
            deprecated_in_favor_of_id=covered_unified.id,
        )
        db.session.add(covered_bc)
        db.session.commit()
        out["covered_capability_name"] = covered_unified.name

        process = APQCProcess(
            process_code="98.0",
            process_name="Smoke Discovery Process %s" % suffix,
            category_level_1="98.0 Smoke Discovery Process %s" % suffix,
        )
        db.session.add(process)
        db.session.commit()
        out["process_name"] = process.process_name

        value_stream = ValueStream(
            name="Smoke Calibration Stream %s" % suffix,
            code=("SMK%s" % suffix)[:20],
            organization_id=org_id,
        )
        db.session.add(value_stream)
        db.session.commit()
        stage = ValueStreamStage(
            name="Smoke Stage %s" % suffix, value_stream_id=value_stream.id,
            stage_order=1, organization_id=org_id,
        )
        db.session.add(stage)
        db.session.commit()
        db.session.execute(
            value_stream_stage_capabilities.insert().values(
                value_stream_stage_id=stage.id,
                business_capability_id=covered_bc.id,
                capability_role="enabling", importance="high",
            )
        )
        db.session.commit()
        out["value_stream_name"] = value_stream.name

        vendor_org = VendorOrganization(
            name="Smoke Calibration Vendor %s" % suffix,
            display_name="Smoke Calibration Vendor %s" % suffix,
            vendor_type="software_vendor",
        )
        db.session.add(vendor_org)
        db.session.flush()
        vendor_product = VendorProduct(
            vendor_organization_id=vendor_org.id,
            name="Smoke Calibration Suite %s" % suffix,
            product_family_name="FSM",
        )
        db.session.add(vendor_product)
        db.session.flush()
        db.session.add(
            VendorProductCapability(
                organization_id=org_id, vendor_product_id=vendor_product.id,
                business_capability_id=covered_bc.id,
                coverage_percentage=82, maturity_level=4, fit_score=80,
            )
        )
        db.session.add(
            VendorProductAPQCMapping(
                vendor_product_id=vendor_product.id, apqc_process_id=process.id,
                coverage_percentage=75,
            )
        )
        db.session.commit()
        out["vendor_org_name"] = vendor_org.name
        db.session.remove()
    return out


def _select_capability(page, name):
    page.get_by_role("tab", name="Capabilities").click()
    page.locator('[role="group"][aria-label="L1 capabilities"]').get_by_text(
        name, exact=True
    ).click()


def _select_process(page, name):
    page.get_by_role("tab", name="APQC Processes").click()
    page.locator('[role="listbox"][aria-label="L1 APQC processes"]').get_by_text(
        name, exact=True
    ).click()


def _select_value_stream(page, stream_name):
    page.get_by_role("tab", name="Value Streams").click()
    page.get_by_label("Business Domain").select_option(index=1)
    page.locator("#vs-stream").get_by_text(stream_name, exact=True).wait_for(timeout=PAGE_TIMEOUT)
    page.locator("#vs-stream").select_option(label=stream_name)
    page.locator("#vs-stream").dispatch_event("change")


def _goto_new_analysis(page, live_server):
    page.goto(live_server + "/dashboard/vendor-analysis/new", timeout=PAGE_TIMEOUT)
    page.get_by_role("heading", name="New Vendor Options Analysis", level=1).wait_for(
        timeout=PAGE_TIMEOUT
    )


def _discover(page):
    """Click Discover and wait for the search to settle (the button's own
    label is restored), without assuming which outcome follows -- callers
    assert on whichever of the results list or the empty state they expect.
    """
    button = page.get_by_role("button", name="Discover Matching Vendors")
    button.click()
    button.wait_for(timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(300)


@pytest.mark.parametrize("width,height", VIEWPORTS)
@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_capability_mode_finds_a_vendor_with_coverage(browser, live_server, width, height, color_scheme):
    org = _seed_discovery_org(covered=True)
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=color_scheme)
    page = ctx.new_page()
    try:
        sign_in(page, live_server, org["emails"]["solution_architect"])
        _goto_new_analysis(page, live_server)
        _select_capability(page, org["covered_capability_name"])
        _discover(page)

        results = page.locator('[aria-label="Discovered vendors"]')
        results.wait_for(timeout=PAGE_TIMEOUT)
        assert org["vendor_org_name"] in results.inner_text()
        assert "1/1" in results.inner_text() or "1 /1" in results.inner_text().replace(" ", "")
        assert "% coverage" in results.inner_text()
        page.screenshot(path="tests/smoke/vendor-map01-shots/capability-%sx%s-%s.png"
                         % (width, height, color_scheme))
    finally:
        ctx.close()


def test_process_mode_finds_a_vendor(browser, live_server):
    org = _seed_discovery_org(covered=True)
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        sign_in(page, live_server, org["emails"]["solution_architect"])
        _goto_new_analysis(page, live_server)
        _select_process(page, org["process_name"])
        _discover(page)

        results = page.locator('[aria-label="Discovered vendors"]')
        results.wait_for(timeout=PAGE_TIMEOUT)
        assert org["vendor_org_name"] in results.inner_text()
        page.screenshot(path="tests/smoke/vendor-map01-shots/process-1440x900-light.png")
    finally:
        ctx.close()


@pytest.mark.parametrize("width,height", VIEWPORTS)
@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_value_stream_mode_resolves_to_its_mapped_capability_and_finds_a_vendor(
    browser, live_server, width, height, color_scheme
):
    org = _seed_discovery_org(covered=True)
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=color_scheme)
    page = ctx.new_page()
    try:
        sign_in(page, live_server, org["emails"]["solution_architect"])
        _goto_new_analysis(page, live_server)
        _select_value_stream(page, org["value_stream_name"])
        page.get_by_text("Selected value streams will resolve to their mapped capabilities").wait_for(
            timeout=PAGE_TIMEOUT
        )
        # the toast QA-128 reported never fires once a stream is selected
        assert not page.get_by_text("Please select capabilities or processes").is_visible()

        _discover(page)

        results = page.locator('[aria-label="Discovered vendors"]')
        results.wait_for(timeout=PAGE_TIMEOUT)
        assert org["vendor_org_name"] in results.inner_text()
        assert "% coverage" in results.inner_text()
        page.screenshot(path="tests/smoke/vendor-map01-shots/valuestream-%sx%s-%s.png"
                         % (width, height, color_scheme))

        # "Save Draft" must also resolve a capability_id in value-stream mode
        # (QA-128's other half-build symptom), not just Discover.
        page.get_by_label("Analysis Name", exact=False).fill("Smoke value-stream analysis")
        page.get_by_role("button", name="Save Draft").click()
        page.get_by_text("Draft saved successfully").wait_for(timeout=PAGE_TIMEOUT)
    finally:
        ctx.close()


@pytest.mark.parametrize("width,height", VIEWPORTS)
@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_a_selection_with_nothing_mapped_shows_the_honest_empty_state(
    browser, live_server, width, height, color_scheme
):
    org = _seed_discovery_org(covered=False)
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=color_scheme)
    page = ctx.new_page()
    try:
        sign_in(page, live_server, org["emails"]["solution_architect"])
        _goto_new_analysis(page, live_server)
        _select_capability(page, org["unmapped_capability_name"])
        _discover(page)

        link = page.get_by_role("link", name="Open vendor catalogue")
        link.wait_for(timeout=PAGE_TIMEOUT)
        proceed = page.get_by_role("button", name="Proceed without pre-selected vendors")
        proceed.wait_for(timeout=PAGE_TIMEOUT)
        body = page.locator("main, body").first.inner_text()
        assert "No vendor is mapped to this selection yet" in body
        assert "No matching vendors found" not in body
        page.screenshot(path="tests/smoke/vendor-map01-shots/empty-state-%sx%s-%s.png"
                         % (width, height, color_scheme))

        # the link actually lands on a page with content, not a dead end
        href = link.get_attribute("href")
        assert href
        page.goto(live_server + href, timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator("body").inner_text().strip() != ""
    finally:
        ctx.close()


def test_two_organisations_do_not_see_each_others_vendor_coverage(browser, live_server):
    """Both organisations have a capability of the same name; only the first
    has vendor coverage recorded. The second must get the honest empty
    state, never the first organisation's vendor.
    """
    covered_org = _seed_discovery_org(covered=True)
    isolated_suffix = uuid.uuid4().hex[:8]

    from app import create_app, db

    app = create_app("testing")
    other_ids = create_fresh_org("solution_architect", platform_role="Architect")
    other_org_id = other_ids["org_id"]
    with app.app_context():
        from app.models.business_capabilities import BusinessCapability
        from app.models.unified_capability import UnifiedCapability

        # Same capability NAME as the covered organisation's, a different id,
        # and deliberately no vendor coverage of its own.
        same_name = covered_org["covered_capability_name"]
        other_unified = UnifiedCapability(
            name=same_name, code="SMOKE-OTHERORG-%s" % isolated_suffix,
            level=1, organization_id=other_org_id,
        )
        db.session.add(other_unified)
        db.session.flush()
        db.session.add(
            BusinessCapability(
                name=same_name, organization_id=other_org_id,
                deprecated_in_favor_of_id=other_unified.id,
            )
        )
        db.session.commit()
        db.session.remove()

    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        sign_in(page, live_server, other_ids["emails"]["solution_architect"])
        _goto_new_analysis(page, live_server)
        _select_capability(page, same_name)
        _discover(page)

        page.get_by_text("No vendor is mapped to this selection yet").wait_for(timeout=PAGE_TIMEOUT)
        body = page.locator("main, body").first.inner_text()
        assert covered_org["vendor_org_name"] not in body
    finally:
        ctx.close()
