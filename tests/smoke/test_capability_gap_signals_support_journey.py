"""Journey: an enterprise architect reads the roadmap's gap signals and they agree with the investment matrix.

Goal: "which capabilities have no application behind them?" The roadmap says "No applications mapped" only for
a capability nothing supports, and the application count it shows for a supported capability is the count the
investment matrix shows for the same capability.
"""
import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from . import fresh_org
from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PERSONA = "enterprise_architect"
TWO, ONE, NONE = "Order handling", "Field service", "Warranty claims"
WALK = os.environ.get("CAPABILITY_GAP_SIGNALS_WALK_DIR")


def _shot(page, name):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _seed(org_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.services.capability_support import record_capability_support

    app = create_app("testing")
    with app.app_context():
        tag = uuid.uuid4().hex[:5]
        apps = [ApplicationComponent(name="%s %d" % (n, i), organization_id=org_id, strategic_importance="low")
                for i, n in enumerate(("Orders hub", "Orders portal", "Field kit"))]
        caps = [BusinessCapability(name=n, organization_id=org_id, level=1, code="G%s-%d" % (tag, i))
                for i, n in enumerate((TWO, ONE, NONE))]
        db.session.add_all(apps + caps)
        db.session.flush()
        for a, c in ((apps[0], caps[0]), (apps[1], caps[0]), (apps[2], caps[1])):
            record_capability_support(org_id, a.archimate_element_id, c.archimate_element_id)
        db.session.commit()
        db.session.remove()


@pytest.fixture
def org():
    made = fresh_org.create_fresh_org(PERSONA, org_admin=False)
    _seed(made["org_id"])
    return made


def _row(page, name):
    return page.get_by_test_id("gap-signal-row").filter(has_text=name)


def _matrix_count(page, live_server, name):
    page.goto(live_server + "/strategic/investment-matrix", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    row = page.get_by_test_id("capability-row").filter(has_text=name).first
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    match = re.search(r"Applications covering it: (\d+)", row.inner_text())
    if match is None:
        row.get_by_test_id("evidence-toggle").click()
        match = re.search(r"Applications covering it: (\d+)", row.inner_text())
    assert match, "the matrix shows no application count for " + name
    return int(match.group(1))


def test_gap_signals_name_only_the_unsupported_capability_and_agree_with_the_matrix(page, live_server, org):
    page.set_viewport_size({"width": 1440, "height": 900})
    fresh_org.sign_in(page, live_server, org["emails"][PERSONA])
    page.goto(live_server + "/capability-roadmap", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    table = page.get_by_test_id("gap-signals-table")
    expect(table).to_be_visible(timeout=PAGE_TIMEOUT)
    _shot(page, "gap-signals-1440-light")

    expect(_row(page, NONE)).to_contain_text("No applications mapped")
    expect(table.get_by_text("No applications mapped")).to_have_count(1)
    for name, count in ((TWO, 2), (ONE, 1)):
        row = _row(page, name)
        expect(row).to_have_count(1)
        expect(row).not_to_contain_text("No applications mapped")
        expect(row).to_contain_text("%d tactical app" % count)

    # Every capability name opens a page with content for that capability.
    link = _row(page, TWO).get_by_role("link", name=TWO)
    link.click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("main")).to_contain_text(TWO)

    # The investment matrix counts the same applications.
    assert _matrix_count(page, live_server, TWO) == 2
    assert _matrix_count(page, live_server, ONE) == 1
    assert _matrix_count(page, live_server, NONE) == 0

    # Narrow, dark.
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(live_server + "/capability-roadmap", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.evaluate("try { localStorage.setItem('theme', 'dark'); } catch (e) {} document.documentElement.classList.add('dark');")
    expect(page.get_by_test_id("gap-signals-table")).to_be_visible(timeout=PAGE_TIMEOUT)
    _shot(page, "gap-signals-390-dark")
