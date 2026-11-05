"""axe-core label-content-name-mismatch on the five /implementation/ pages.

QA finding (WCAG 2.5.3, Label in Name): the gantt timeline's group rows and
task rows carry an aria-label that leaves out part of their visible text
(the task count on a group, the progress on a task). A Playwright test opens
each page signed in, waits for the timeline's group rows to exist, runs axe
and asserts the rule does not fire; it then reads each group row's accessible
name through the accessibility tree and asserts it begins with the row's
visible text. Light and dark, at 1440 and 390 wide.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_a11y04_detail_pages import TAGS, _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

RULE = "label-content-name-mismatch"
ROW_SELECTOR = "[role=button][aria-expanded]:has(> span.font-semibold)"

PAGES = [
    ("overview", "/implementation/"),
    ("deliverables", "/implementation/deliverables"),
    ("gaps", "/implementation/gaps"),
    ("plateaus", "/implementation/plateaus"),
    ("work packages", "/implementation/work-packages"),
]

VIEWPORTS = [
    ("1440 light", 1440, 900, False),
    ("390 dark", 390, 800, True),
]


@pytest.fixture(scope="module")
def timeline_data(seeded):
    """Work packages in two groups, one with visible progress, so the
    timeline renders group rows and task rows."""
    import datetime

    from app import create_app, db
    from app.models.implementation_planning import ImplementationWorkPackage

    app = create_app("testing")
    with app.app_context():
        today = datetime.datetime.utcnow()
        for i, (name, layer, progress) in enumerate([
            ("Retire legacy billing", "business", 40.0),
            ("Migrate ledger to cloud", "business", 0.0),
            ("Roll out single sign-on", "technology", 65.0),
        ]):
            db.session.add(ImplementationWorkPackage(
                name=name, layer=layer, status="in_progress",
                progress_percentage=progress,
                start_date=today + datetime.timedelta(days=7 * i),
                end_date=today + datetime.timedelta(days=30 + 7 * i),
            ))
        db.session.commit()
        db.session.remove()


def _norm(text):
    return " ".join((text or "").split()).lower()


@pytest.mark.parametrize("page_name,path", PAGES)
@pytest.mark.parametrize("label,width,height,dark", VIEWPORTS)
def test_timeline_group_rows_named_by_visible_text(
    browser, live_server, seeded, timeline_data, page_name, path, label, width, height, dark,
):
    from axe_playwright_python.sync_playwright import Axe

    ctx = browser.new_context(viewport={"width": width, "height": height})
    if dark:
        ctx.add_init_script("document.documentElement.classList.add('dark');")
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status < 400, "%s returned %s" % (page_name, response.status)
        expect(page.locator("main")).to_be_visible()
        rows = page.locator(ROW_SELECTOR)
        # Wait for the group rows: axe must not run before they render.
        rows.first.wait_for(state="attached", timeout=PAGE_TIMEOUT)
        count = rows.count()
        assert count > 0, "%s: no timeline group rows rendered" % page_name

        report = Axe().run(page, options={"runOnly": {"type": "tag", "values": TAGS},
                                         "rules": {RULE: {"enabled": True}}})
        data = report.response if hasattr(report, "response") else report
        hits = [v for v in data.get("violations", []) if v["id"] == RULE]
        assert not hits, "%s (%s): axe reports %s on %d element(s)" % (
            page_name, label, RULE, sum(len(v.get("nodes") or []) for v in hits))

        for i in range(count):
            row = rows.nth(i)
            visible = _norm(row.inner_text())
            name = _norm(row.get_attribute("aria-label"))
            assert name.startswith(visible), (
                "%s (%s): group row accessible name %r does not begin with visible text %r"
                % (page_name, label, name, visible))
    finally:
        ctx.close()
