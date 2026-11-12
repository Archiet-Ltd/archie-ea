"""A demonstration architect opens the controls register and follows Evidence document to the control's evidence.

The goal: for a control the demonstration says is implemented, verified or under way, the Evidence document
link opens, inside the app frame, a page headed with that control that shows its evidence, and the way back
returns to the register at that control. A bare file, a host outside the app, an error page or an empty page is
a failure; so is a page that scrolls sideways at 390 or ignores the dark theme.

The register lists the controls that are not yet fully in place, so the in-progress mappings are clicked from
that page, one of them from the middle of the list; an implemented and a verified mapping show their address
on the mapping, and that address is opened in the same signed-in session.
"""
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import sign_in
from .test_demo_architect_acts_journey import DEMO_EMAIL, demo  # noqa: F401  (the seeded demonstration)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SIZES = {"1440-light": ({"width": 1440, "height": 900}, "light"), "390-dark": ({"width": 390, "height": 844}, "dark")}
SHOTS = Path(os.environ.get("SMOKE_WALK_DIR") or Path(tempfile.gettempdir()) / "compliance-evidence-shots")


def _document_text(page):
    return page.inner_text("main")


def _assert_page_is_in_the_app(page, size):
    """The app frame is there, the page fits the width and its background follows the theme."""
    assert page.locator("main").count() == 1 and page.locator("nav, aside").count() >= 1
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth"), "scrolls sideways"
    dark = SIZES[size][1] == "dark"
    assert page.evaluate("() => document.documentElement.classList.contains('dark')") is dark
    red, green, blue = page.evaluate(
        "() => getComputedStyle(document.body).backgroundColor.match(/[\\d.]+/g).slice(0, 3).map(Number)")
    brightness = (red * 299 + green * 587 + blue * 114) / 1000
    assert (brightness < 90) if dark else (brightness > 160), (size, red, green, blue)


def _assert_evidence_page(page, origin, code, application, status, size):
    assert page.url.startswith(origin + "/dashboard/compliance/controls/"), page.url
    assert page.url.endswith("/evidence"), page.url
    heading = page.get_by_role("heading", level=1).inner_text()
    assert heading.startswith("Evidence for %s:" % code), heading
    text = _document_text(page)
    facts = page.get_by_test_id("evidence-facts").inner_text()
    assert application in facts, facts
    assert re.search(r"Status\s*\n?\s*%s" % re.escape(status.replace("_", " ")), facts, re.I), facts
    evidence = page.get_by_test_id("property-evidence").inner_text()
    assert application in evidence and len(evidence.split()) > 8, evidence
    assert "No evidence text is recorded" not in text
    _assert_page_is_in_the_app(page, size)


def _assert_back_lands_on_the_control(page, origin, row_id):
    page.get_by_role("link", name="Back to the register").first.click()
    page.wait_for_load_state("domcontentloaded")
    assert page.url == "%s/dashboard/compliance#control-%s" % (origin, row_id), page.url
    row = page.locator("#control-%s" % row_id)
    row.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    box = row.bounding_box()
    assert box and 0 <= box["y"] <= page.viewport_size["height"], box


@pytest.mark.parametrize("size", sorted(SIZES))
def test_each_kind_of_finished_mapping_opens_its_evidence_document(page, live_server, demo, app, size):  # noqa: F811
    from app import db
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.compliance_models import ComplianceControl
    from app.models.application_portfolio import ApplicationComponent

    viewport, scheme = SIZES[size]
    page.set_viewport_size(viewport)
    page.emulate_media(color_scheme=scheme)
    SHOTS.mkdir(parents=True, exist_ok=True)
    sign_in(page, live_server, DEMO_EMAIL)
    page.goto(live_server + "/dashboard/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # in progress: click the link on the register, from the first, the middle and the last row of its first page
    page.goto(live_server + "/dashboard/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    with_link = page.get_by_role("link", name="Evidence document")
    rows = page.locator("[data-testid=compliance-finding-row]:not([hidden])").filter(has=with_link)
    shown = rows.count()
    assert shown >= 3
    for i in sorted({0, shown // 2, shown - 1}):
        page.goto(live_server + "/dashboard/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.locator("[data-testid=compliance-finding-row]:not([hidden])").filter(has=with_link).nth(i)
        row_id = row.get_attribute("id").replace("control-", "")
        link = row.get_by_role("link", name="Evidence document")
        footer = row.inner_text()
        found = re.search(r"Evidence (\S+) on ([^:]+): in progress", footer)
        assert found, footer
        assert urlsplit(link.get_attribute("href")).netloc in ("", urlsplit(live_server).netloc), link.get_attribute("href")
        link.click()
        page.wait_for_load_state("domcontentloaded")
        _assert_evidence_page(page, live_server, found.group(1), found.group(2), "in progress", size)
        page.screenshot(path=str(SHOTS / ("evidence-in-progress-%d-%s.png" % (i, size))), full_page=True)
        _assert_back_lands_on_the_control(page, live_server, row_id)
        page.screenshot(path=str(SHOTS / ("register-return-%d-%s.png" % (i, size))))

    # a control further down the register than its first page: the way back opens that page at the control
    page.goto(live_server + "/dashboard/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    last = page.locator("[data-testid=compliance-finding-row]").filter(has=with_link).last
    last_id = last.get_attribute("id").replace("control-", "")
    last_address = last.get_by_role("link", name="Evidence document").get_attribute("href")
    last_code = last.inner_text().split(":")[0].strip()
    page.goto(live_server + last_address, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert page.get_by_role("heading", level=1).inner_text().startswith("Evidence for %s:" % last_code)
    _assert_back_lands_on_the_control(page, live_server, last_id)

    # implemented and verified: the address held on the mapping opens the same kind of document
    with app.app_context():
        org_id = demo["org_id"]
        picked = {}
        for status in ("implemented", "verified"):
            row = ApplicationComplianceControl.query.filter(
                ApplicationComplianceControl.organization_id == org_id,
                ApplicationComplianceControl.implementation_status == status,
                ApplicationComplianceControl.evidence_url.isnot(None)).first()
            assert row is not None, status
            picked[status] = (
                row.evidence_url,
                ComplianceControl.query.get(row.control_id).control_code,
                ApplicationComponent.query.get(row.application_id).name,
            )
        db.session.remove()
    for status, (address, code, application) in picked.items():
        assert urlsplit(address).netloc == "", address
        assert re.match(r"^/dashboard/compliance/controls/\d+/evidence$", address), address
        page.goto(live_server + address, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _assert_evidence_page(page, live_server, code, application, status, size)
        page.screenshot(path=str(SHOTS / ("evidence-%s-%s.png" % (status, size))), full_page=True)
