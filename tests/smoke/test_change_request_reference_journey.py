"""An ARB member sees one reference for a change request on every screen.

The list used to show a number made up from the row id while the detail page
showed the stored reference. The journey reads the reference in the list row,
follows View, and expects the detail page to head the same reference; then it
searches the list for that reference and expects exactly that row.
"""

import os
import pathlib
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("SMOKE_SCREENSHOT_DIR", "test-results/change-request-reference")


@pytest.fixture(scope="module")
def two_change_requests(seeded):
    from app import create_app, db
    from app.models.architecture_decision import ArchitectureChangeRequest
    from app.models.user import User

    app = create_app("testing")
    org_id = seeded["ids"]["org"]
    suffix = uuid.uuid4().hex[:4].upper()
    rows = []
    try:
        with app.app_context():
            user = User.query.filter_by(email=seeded["emails"]["arb_member"]).one()
            raiser = user.full_name()
            for n, title in enumerate(("Retire the legacy payroll interface", "Move the CRM to a new region")):
                cr = ArchitectureChangeRequest(
                    title=title,
                    acr_reference=f"ACR-2026-{suffix}{n}",
                    trigger_type="business_event",
                    status="open",
                    description=title,
                    raised_by_id=user.id,
                    organization_id=org_id,
                )
                db.session.add(cr)
                db.session.flush()
                rows.append({"id": cr.id, "title": title, "ref": cr.acr_reference, "raiser": raiser})
            db.session.commit()
        yield rows
    finally:
        with app.app_context():
            for r in rows:
                cr = db.session.get(ArchitectureChangeRequest, r["id"])
                if cr is not None:
                    db.session.delete(cr)
            db.session.commit()


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _shot(page, name):
    pathlib.Path(WALK_DIR).mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(pathlib.Path(WALK_DIR) / name), full_page=True)


@pytest.mark.parametrize(
    "width,height,dark",
    [(1440, 900, False), (390, 844, True)],
    ids=["1440-light", "390-dark"],
)
def test_list_row_and_detail_page_show_the_same_reference(
    browser, live_server, seeded, two_change_requests, width, height, dark
):
    target, other = two_change_requests
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["arb_member"])
        page.goto(live_server + "/arb/change-requests", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        if dark:
            page.evaluate("document.documentElement.classList.add('dark');")

        row = page.locator("table tbody tr", has_text=target["title"])
        assert row.count() == 1
        headers = [h.strip() for h in page.locator("table thead th").all_inner_texts()]
        shown = row.locator("td").nth(headers.index("Reference")).inner_text().strip()
        assert shown == target["ref"], f"list shows {shown!r}, stored reference is {target['ref']!r}"
        _shot(page, f"list-{width}-{'dark' if dark else 'light'}.png")

        # Search by the stored reference leaves exactly that row.
        page.fill("[data-testid=change-requests-table-pager] input[data-table-filter]", target["ref"])
        visible = page.locator("table tbody tr:visible")
        assert visible.count() == 1
        assert target["title"] in visible.first.inner_text()
        assert other["title"] not in " ".join(visible.all_inner_texts())
        page.fill("[data-testid=change-requests-table-pager] input[data-table-filter]", "")

        # Follow View: the detail page heads the same reference and has content.
        row.locator("a", has_text="View").click()
        page.wait_for_url(f"**/arb/change-requests/{target['id']}", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        assert target["ref"] in page.locator("h1").first.inner_text()
        assert target["title"] in page.locator("main").inner_text()
        raised_by = page.locator("dt", has_text="Raised By").locator("xpath=following-sibling::dd")
        assert raised_by.inner_text().strip() == target["raiser"]
        _shot(page, f"detail-{width}-{'dark' if dark else 'light'}.png")
    finally:
        page.close()
        context.close()
