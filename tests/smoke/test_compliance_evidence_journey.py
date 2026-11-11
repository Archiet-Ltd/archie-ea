"""A demonstration architect opens the compliance mappings and follows Evidence document to a real document.

The goal: for a control the demonstration says is implemented, verified or under way, the Evidence document
link opens, inside the app, the document that shows it, and that document names the control, the application
and the status. A link to a host outside the app, an error page or an empty page is a failure.

The compliance page lists the controls that are not yet fully in place, so the in-progress mappings are
clicked from that page; an implemented and a verified mapping show their address on the mapping, and that
address is opened in the same signed-in session.
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
    return page.inner_text("body")


def _assert_document_shows(page, origin, code, application, status):
    assert page.url.startswith(origin + "/applications/documents/"), page.url
    text = _document_text(page)
    assert "ISO 27001 control evidence: %s" % application in text, text[:300]
    block = re.search(r"^%s .*\n(.*)\n" % re.escape(code), text, re.M)
    assert block, "the document does not name control %s" % code
    assert block.group(1).strip() == "Status: %s" % status.replace("_", " "), block.group(1)
    assert len(text.split()) > 40


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

    # in progress: click the link on the page the mappings are listed on
    links = page.get_by_role("link", name="Evidence document")
    assert links.count() >= 3
    for i in range(3):
        page.goto(live_server + "/dashboard/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        link = page.get_by_role("link", name="Evidence document").nth(i)
        footer = link.evaluate("e => e.closest('[data-testid]') ? e.closest('[data-testid]').innerText : e.parentElement.parentElement.innerText")
        found = re.search(r"Evidence (\S+) on ([^:]+): in progress", footer)
        assert found, footer
        assert urlsplit(link.get_attribute("href")).netloc in ("", urlsplit(live_server).netloc), link.get_attribute("href")
        link.click()
        page.wait_for_load_state("domcontentloaded")
        _assert_document_shows(page, live_server, found.group(1), found.group(2), "in progress")
        page.screenshot(path=str(SHOTS / ("evidence-in-progress-%d-%s.png" % (i, size))), full_page=True)

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
        page.goto(live_server + address, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _assert_document_shows(page, live_server, code, application, status)
        page.screenshot(path=str(SHOTS / ("evidence-%s-%s.png" % (status, size))), full_page=True)
