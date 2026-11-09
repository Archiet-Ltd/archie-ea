"""A compliance officer reads one count of controls not in place on every screen that shows it.

The goal: read "Controls Not In Place" on Compliance Tracking and find the same count on Capability Health
and on the compliance dashboard, with the rows beneath it explained; never see a capability with controls still to do read "None needed";
read each recommendation as a short sentence that links to the full list; read the findings on a phone.
Seeded with one application serving two capabilities, a capability whose only open controls are in
progress, long evidence addresses, and a second organisation whose records must never show.
"""

import os
import re
import uuid
from datetime import datetime

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_compliance_one_answer_journey import _login, _open, _org, _shot

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

EVIDENCE = "https://evidence.example.com/audit-files/2026/q3/information-security/%s/access-review-report.pdf"


def _estate(db, org, user, framework, controls, label, suffix):
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capability import BusinessCapability
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    ApplicabilityService.adopt_framework(org.id, framework.id, adopted_by_id=user.id)
    caps = [BusinessCapability(name="%s %s %s" % (label, n, suffix), organization_id=org.id)
            for n in ("Settlement", "Reporting", "Fulfilment", "Archive")]
    ledger = ApplicationComponent(name="%s Calibration Ledger %s" % (label, suffix), organization_id=org.id)
    orders = ApplicationComponent(name="%s Orders %s" % (label, suffix), organization_id=org.id)
    db.session.add_all(caps + [ledger, orders])
    db.session.flush()
    settle, report, fulfil, _archive = caps
    for cap, app_row in ((settle, ledger), (report, ledger), (fulfil, orders)):
        db.session.add(ApplicationCapabilityMapping(
            organization_id=org.id, application_component_id=app_row.id, business_capability_id=cap.id,
            support_level="full", coverage_percentage=80, is_active=True))
    # The ledger: nine controls still to do (planned or in progress), one implemented.
    for n, control in enumerate(controls[:10]):
        status = "implemented" if n == 0 else "in_progress" if n % 2 else "planned"
        ApplicabilityService.link_requirement(org.id, ledger.id, control.id, status,
                                              evidence_url=EVIDENCE % control.control_code)
    # Orders: nineteen controls implemented and one in progress, so 97.5% and still one to do.
    for n, control in enumerate(controls):
        ApplicabilityService.link_requirement(org.id, orders.id, control.id,
                                              "in_progress" if n == 0 else "implemented")
    # An application that supports no capability, with two controls still to do: they are in the one count.
    standalone = ApplicationComponent(name="%s Standalone %s" % (label, suffix), organization_id=org.id)
    db.session.add(standalone)
    db.session.flush()
    for control in controls[:2]:
        ApplicabilityService.link_requirement(org.id, standalone.id, control.id, "planned")
    db.session.commit()
    return caps, ledger, orders


@pytest.fixture
def world(app, live_server):
    from app import db
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.models.user import Role

    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        Role.insert_roles()
        framework = RegulatoryFramework(code="H-%s" % suffix, name="Headline standard %s" % suffix, status="active")
        db.session.add(framework)
        db.session.flush()
        controls = [ComplianceControl(framework_id=framework.id, control_code="A.%02d-%s" % (n, suffix),
                                      title="Control %d %s" % (n, suffix)) for n in range(20)]
        db.session.add_all(controls)
        db.session.commit()
        org_a, user_a = _org(db, "Alpha", suffix)
        org_b, user_b = _org(db, "Bravo", suffix)
        caps, ledger, orders = _estate(db, org_a, user_a, framework, controls, "Alpha", suffix)
        caps_b, ledger_b, _orders_b = _estate(db, org_b, user_b, framework, controls, "Bravo", suffix)
        return {
            "email": user_a.email, "caps": [c.name for c in caps], "ledger": ledger.name, "orders": orders.name,
            "other": [c.name for c in caps_b] + [ledger_b.name],
        }


def _numbers(text):
    return [int(n) for n in re.findall(r"\d+", text)]


def _tracking_rows(page):
    """capability name -> (status, controls not in place as the row words it)."""
    rows = {}
    for row in page.locator('[data-testid="compliance-tracking-row"]').all():
        cells = [" ".join(c.inner_text().split()) for c in row.locator("td").all()]
        rows[cells[0]] = (cells[1], cells[3])
    return rows


def test_the_headline_is_the_sum_of_the_rows_and_no_open_control_reads_none_needed(browser, live_server, world):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        _open(page, live_server, "/strategic/compliance-tracking")
        rows = _tracking_rows(page)
        assert set(rows) == set(world["caps"])
        settle, report, fulfil, archive = world["caps"]

        # Nine controls to do under both capabilities the ledger serves; one under Fulfilment.
        assert "9 controls not in place" in rows[settle][1] and "9 controls not in place" in rows[report][1]
        assert "1 control not in place" in rows[fulfil][1]
        # A capability scoring well with a control still to do is partly compliant, and is not "None needed".
        assert rows[fulfil][0] == "Partly compliant"
        assert "None needed" not in rows[fulfil][1]
        # Nothing mapped: not assessed, with its next step.
        assert rows[archive] == ("Not assessed", "Map the controls its applications must meet to assess it.")

        in_rows = sum(_numbers(next_step)[0] for _status, next_step in rows.values() if "not in place" in next_step)
        assert in_rows == 19, in_rows
        tile = page.locator('[data-slot="card-header"]', has_text="Controls Not In Place").first
        tile_text = " ".join(tile.inner_text().split())
        headline = int(re.search(r"\d+", tile_text.split("Controls Not In Place", 1)[1]).group())
        # Each control once: 9 on the ledger, 1 on orders, 2 on the application with no capability.
        assert headline == 12, tile_text
        # The rows count a control under each capability it supports, and the tile says why they add up higher.
        card_text = " ".join(tile.locator("xpath=ancestor::*[@data-slot='card'][1]").inner_text().split())
        assert "6 not started, 6 in progress; the rows add up to 19" in card_text, card_text
        words = "12 controls not in place: 6 not started and 6 in progress"
        _shot(page, "compliance-headline-tracking", 1440, "light")

        _open(page, live_server, "/strategic/capability-health")
        health = {}
        for row in page.locator('[data-testid="capability-compliance-row"]').all():
            cells = [" ".join(c.inner_text().split()) for c in row.locator("td").all()]
            health[cells[0]] = cells[3]
        assert health[settle] == health[report] == "9" and health[fulfil] == "1"
        assert health.get(archive, "Not assessed") == "Not assessed"
        assert sum(int(v) for v in health.values() if v.isdigit()) == in_rows
        assert words in " ".join(page.locator('[data-testid="not-in-place-count"]').first.inner_text().split())
        _shot(page, "compliance-headline-health", 1440, "light")

        _open(page, live_server, "/dashboard/compliance")
        assert words in " ".join(page.locator('[data-testid="not-in-place-count"]').first.inner_text().split())
    finally:
        ctx.close()


def test_a_recommendation_names_a_few_controls_and_links_to_the_full_list(browser, live_server, world):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        _open(page, live_server, "/strategic/compliance-tracking")
        recs = [" ".join(li.inner_text().split())
                for li in page.locator('[data-testid="compliance-recommendations"] li').all()]
        ledger_rec = next(r for r in recs if world["caps"][0] in r)
        assert len(re.findall(r"A\.\d\d-", ledger_rec)) <= 3, ledger_rec
        assert "and 6 more" in ledger_rec, ledger_rec
        assert all(len(r) < 220 for r in recs), recs

        # The link lands on the full list, which holds every one of the ledger's controls.
        page.locator('[data-testid="all-controls-link"]').click()
        page.wait_for_selector('[data-testid="compliance-findings-table"]')
        listed = page.locator('[data-testid="compliance-finding-row"]', has_text=world["ledger"]).count()
        assert listed == 9, listed
        assert world["ledger"] in page.locator("main").inner_text()
        for other in world["other"]:
            assert other not in page.locator("main").inner_text()
    finally:
        ctx.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_findings_read_whole_at_phone_width(browser, live_server, world, scheme):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        _open(page, live_server, "/dashboard/compliance")
        assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1
        if os.environ.get("SMOKE_WALK_DIR"):
            page.locator('[data-testid="compliance-findings-table-pager"]').screenshot(
                path=os.path.join(os.environ["SMOKE_WALK_DIR"], "compliance-findings-table-%s-390.png" % scheme))
        row = page.locator('[data-testid="compliance-finding-row"]').first
        row.scroll_into_view_if_needed()
        box = row.bounding_box()
        assert box is not None and box["x"] >= -1 and box["x"] + box["width"] <= 391, box
        # The application is whole and inside the viewport, not cut off at the edge.
        app_link = row.locator('[data-testid="finding-application-compact"]')
        text = app_link.inner_text().strip()
        assert text == world["ledger"], text
        ab = app_link.bounding_box()
        assert ab["x"] >= 0 and ab["x"] + ab["width"] <= 391, ab
        assert page.evaluate("(el) => el.scrollWidth <= el.clientWidth + 1", app_link.element_handle())
        # The evidence is not squeezed into a column of one or two words.
        evidence = row.locator("text=Evidence document").first
        eb = evidence.bounding_box()
        assert eb["width"] >= 150, eb
        _shot(page, "compliance-findings", 390, scheme)
    finally:
        ctx.close()
