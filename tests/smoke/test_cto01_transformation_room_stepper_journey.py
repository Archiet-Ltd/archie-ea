"""A CTO opens a programme's room, moves across every stage with the stepper, and
each stage shows its content or says why it is not available and what to do next;
then adds a workstream from the table page and sees it listed."""

import os

import pytest

from .conftest import PAGE_TIMEOUT
from .test_transformation_room_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

STAGES = ["Objective", "Discover", "Evidence", "Options", "Decision", "Execute", "Outcomes"]
UNAVAILABLE = {"Decision", "Execute", "Outcomes"}


def _shot(page, name):
    """Walk evidence: set CTO01_SHOTS to a folder to keep a frame of each page."""
    folder = os.environ.get("CTO01_SHOTS")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.screenshot(path=os.path.join(folder, name + ".png"), full_page=True)


def _create_programme(page, name):
    advance = page.get_by_test_id("wizard-next")

    def step(frm, to):
        page.get_by_test_id(f"step-{frm}").wait_for(state="visible", timeout=PAGE_TIMEOUT)
        advance.click()
        page.get_by_test_id(f"step-{to}").wait_for(state="visible", timeout=PAGE_TIMEOUT)

    page.locator('input[name="name"]').fill(name)
    page.locator('textarea[name="objective"]').fill("Reduce avoidable hand-offs")
    step("intent", "ownership")
    owner = page.get_by_role("combobox", name="Programme owner")
    owner.fill("Smoke")
    page.wait_for_selector('#owner-results [role="option"]', timeout=PAGE_TIMEOUT)
    owner.press("ArrowDown")
    owner.press("Enter")
    page.locator('input[name="target_date"]').fill("2027-12-31")
    step("ownership", "workstream")
    page.locator('select[name="workstream_type"]').select_option("process")
    page.locator('input[name="scope_expression"]').fill("Operations")
    step("workstream", "outcome")
    page.locator('input[name="outcome"]').fill("Fewer customer hand-offs")
    step("outcome", "measure")
    page.locator('input[name="metric_name"]').fill("Hand-offs")
    page.locator('input[name="unit"]').fill("count")
    page.locator('input[name="unavailable_reason"]').fill("Baseline requested")
    page.locator('input[name="target_value"]').fill("10")
    step("measure", "review")
    page.get_by_test_id("wizard-submit").click()
    page.wait_for_url("**/workstreams/*/objective", timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("width", [1440, 390])
def test_cto_moves_across_every_stage_with_the_stepper(width, browser, live_server, seeded):
    scheme = "dark" if width == 390 else "light"
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        _create_programme(page, f"CTO room {width}")
        _shot(page, f"A-objective-{width}-{scheme}")
        room_url = page.url
        stepper = page.locator('nav[aria-label="Workstream stages"]')
        assert stepper.count() == 1, "the stage strip is one labelled stepper"
        triggers = stepper.locator('a[data-slot="button"]')
        assert [t.strip() for t in triggers.all_inner_texts()] == STAGES
        # The stepper keeps its left-to-right shape: one row, even on a phone.
        tops = {round(b["y"]) for b in (triggers.nth(i).bounding_box() for i in range(len(STAGES)))}
        assert len(tops) == 1, f"stepper wrapped into rows at {width}: {tops}"
        for label in STAGES:
            page.locator('nav[aria-label="Workstream stages"]').get_by_role("link", name=label, exact=True).click()
            page.wait_for_load_state("domcontentloaded")
            assert page.locator("main h2").count() >= 1
            current = page.locator('nav[aria-label="Workstream stages"] [aria-current="step"]')
            assert current.count() == 1 and current.inner_text().strip() == label, "the open stage is the marked step"
            _shot(page, f"A-stage-{label.lower()}-{width}-{scheme}")
            if label in UNAVAILABLE:
                assert page.get_by_text("Not available in this release", exact=True).count() == 1
                nxt = page.get_by_test_id("stage-next-step")
                assert nxt.count() == 1
                nxt.click()
                page.wait_for_url(room_url, timeout=PAGE_TIMEOUT)
                assert page.get_by_role("heading", name="Objective and scope").count() == 1
        # Every stage page is built from the library card, not a hand-drawn section.
        page.goto(room_url, wait_until="domcontentloaded")
        assert page.locator('main [data-slot="card"]').count() >= 2
        assert page.locator("main section.rounded-lg.bg-card").count() == 0
    finally:
        context.close()


def test_workstreams_page_lists_and_adds(browser, live_server, seeded):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        _create_programme(page, "CTO workstreams")
        page.get_by_role("link", name="Transform", exact=True).first.click()
        page.wait_for_load_state("domcontentloaded")
        page.get_by_role("link", name="View workstreams").click()
        page.wait_for_url("**/workstreams", timeout=PAGE_TIMEOUT)
        table = page.locator('[data-slot="data-table"]')
        assert table.count() == 1
        assert table.get_by_test_id("workstream-row").count() == 1
        page.locator("#ws-objective").fill("Cut the order-to-cash hand-offs")
        page.locator("#ws-target-reason").fill("Dates follow the budget round")
        page.get_by_role("button", name="Add workstream").click()
        page.wait_for_load_state("domcontentloaded")
        assert table.get_by_test_id("workstream-row").count() == 2
        _shot(page, "A-workstreams-1440-light")
        assert page.get_by_text("Cut the order-to-cash hand-offs").count() >= 1
        row = table.get_by_test_id("workstream-row").filter(has_text="Cut the order-to-cash hand-offs").first
        row.get_by_role("link").first.click()
        page.wait_for_url("**/objective", timeout=PAGE_TIMEOUT)
        assert page.get_by_role("heading", name="Objective and scope").count() == 1
    finally:
        context.close()
