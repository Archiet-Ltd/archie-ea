"""A business architect sees how long each value stream stage really takes.

Fresh organisation, a value stream with four stages made through the product's
own screens. With no Jira connection the page says why and accepts a typed
duration, labelled as typed and never shown as measured. Once Jira is connected
the architect maps stages to Jira statuses and measures: the medians equal the
known values of a recorded 30-issue changelog served by a fake Jira, each sits
beside its target, the slowest stage is flagged, a stage with fewer than five
issues says it is too few to measure, and the issues link opens the measured
issue in Jira. A second organisation sees none of it.
"""

import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in
from .test_launch_value_stream_journey import _new_page, _stage_card

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "fixtures", "jira_stage_times_changelog.json")
WALK = os.environ.get("STAGE_TIMES_WALK_DIR")
# stage, Jira status, target in days, the median the fixture is built to give
STAGES = [
    ("Build", "In Progress", 10, "15.5 days"),
    ("Review", "In Review", 5, "6.5 days"),
    ("Test", "QA", 3, "1.9 days"),
    ("Release", "Ready to Release", 2, None),
]


class _FakeJira:
    """A Jira on loopback: the search answers with the recorded issues, /browse/<key> with a page."""

    def __init__(self):
        with open(FIXTURE) as handle:
            self.body = handle.read().encode()
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                parts = urlsplit(self.path)
                fake.requests.append(self.path)
                if parts.path == "/rest/api/3/search":
                    start = [p for p in parts.query.split("&") if p.startswith("startAt=")]
                    body = fake.body if start == ["startAt=0"] else b'{"issues": []}'
                    ctype = "application/json"
                elif parts.path.startswith("/browse/"):
                    key = parts.path.rsplit("/", 1)[1]
                    body = ("<html><body><h1>%s</h1><p>Flow issue in Jira</p></body></html>" % key).encode()
                    ctype = "text/html"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _connect_jira(org_id, url):
    from app import create_app, db
    from app.models.connector_config import ConnectorConfig
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    app = create_app("testing")
    with app.app_context():
        db.session.add(ConnectorConfig(
            id="jira-%s" % uuid.uuid4().hex[:8], organization_id=org_id, connector_type="jira", name="Jira",
            config={"instance_url": url, "email": "svc@example.com", "default_project_key": "FLOW"},
            status="active"))
        db.session.commit()
        OrgCredentialVault().store(org_id, "jira", "api_token", "not-a-real-token")
        db.session.remove()


def _shot(page, name):
    """Screenshot of the stage times card, when a walk folder is given."""
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        card = page.locator("[data-stage-times]")
        card.scroll_into_view_if_needed()
        card.screenshot(path=os.path.join(WALK, name + ".png"))


def _typed_dialog(page, stage):
    return page.get_by_role("dialog", name="Duration and target: %s" % stage)


def _row(page, stage):
    return page.locator('[data-testid="stage-time-row"]', has_text=stage)


def test_architect_sees_how_long_each_stage_takes_measured_from_jira(browser, live_server):
    base = live_server
    tag = uuid.uuid4().hex[:6]
    vs_name = "Order to Cash %s" % tag
    org = create_fresh_org("business_architect")
    other = create_fresh_org("business_architect")
    jira = _FakeJira()
    try:
        context, page = _new_page(browser)
        page.set_viewport_size({"width": 1440, "height": 1000})
        sign_in(page, base, org["emails"]["business_architect"])

        page.goto(base + "/value-streams/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="New Value Stream").first.click()
        modal = page.locator("#vs-create-modal")
        expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
        modal.locator("#vs-name").fill(vs_name)
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            modal.get_by_role("button", name="Create").click()
        detail_url = page.url

        # An empty stream says what is missing and offers no measuring.
        expect(page.locator("[data-stage-times]")).to_contain_text("Add a stage to see how long it takes")

        for name, _status, _target, _median in STAGES:
            page.get_by_role("button", name="Add Stage").first.click()
            create = page.locator("#stage-create-modal")
            expect(create).to_be_visible(timeout=PAGE_TIMEOUT)
            create.locator("#stage-name").fill(name)
            with page.expect_navigation(timeout=PAGE_TIMEOUT):
                create.get_by_role("button", name="Add Stage").click()

        # No Jira: the page says why, offers no measuring, and accepts a typed duration.
        card = page.locator("[data-stage-times]")
        expect(card.locator("[data-stage-times-no-jira]")).to_contain_text("Jira is not connected")
        expect(card.get_by_role("button", name="Measure from Jira")).to_have_count(0)
        for name, _status, target, _median in STAGES:
            row = _row(page, name)
            expect(row.locator("[data-stage-time-figure]")).to_have_text("Not measured yet")
            expect(row.locator("[data-stage-target]")).to_contain_text("No target set")
        _row(page, "Build").get_by_role("button", name="Set duration or target").click()
        dialog = _typed_dialog(page, "Build")
        expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        dialog.get_by_label("Target").fill("10")
        dialog.get_by_label("Duration (typed)").fill("4")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            dialog.get_by_role("button", name="Save").click()
        build = _row(page, "Build")
        expect(build.locator("[data-stage-time-figure]")).to_have_text("4 days")
        expect(build.locator("[data-stage-time-source]")).to_contain_text("Typed by Launch business_arc")
        assert build.locator("[data-stage-time]").get_attribute("data-source") == "typed"
        expect(build).not_to_contain_text("Measured from Jira")
        expect(build.locator("[data-stage-target]")).to_contain_text("10 days")

        # Jira is connected: targets, then map each stage to a Jira status.
        _connect_jira(org["org_id"], jira.url)
        for name, _status, target, _median in STAGES[1:]:
            _row(page, name).get_by_role("button", name="Set duration or target").click()
            dialog = _typed_dialog(page, name)
            expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
            dialog.get_by_label("Target").fill(str(target))
            with page.expect_navigation(timeout=PAGE_TIMEOUT):
                dialog.get_by_role("button", name="Save").click()
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        card = page.locator("[data-stage-times]")
        expect(card.locator("[data-stage-times-no-jira]")).to_have_count(0)
        card.get_by_role("button", name="Map Jira statuses").click()
        mapping = page.locator("#stage-map-dialog")
        expect(mapping).to_be_visible(timeout=PAGE_TIMEOUT)
        for name, status, _target, _median in STAGES:
            mapping.get_by_label(name, exact=True).fill(status)
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            mapping.get_by_role("button", name="Save mapping").click()
        card = page.locator("[data-stage-times]")
        expect(_row(page, "Build")).to_contain_text("Mapped to In Progress")

        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            card.get_by_role("button", name="Measure from Jira").click()
        assert any("expand=changelog" in r for r in jira.requests), "Jira was not asked for the status history"

        # Medians equal the fixture's known values, each beside its target.
        for name, _status, target, median in STAGES:
            row = _row(page, name)
            expect(row.locator("[data-stage-target]")).to_contain_text("%s days" % target)
            figure = row.locator("[data-stage-time-figure]")
            if median:
                expect(figure).to_have_text(median)
                expect(row.locator("[data-stage-time-source]")).to_contain_text("Measured from Jira: median of 30 issues")
                assert row.locator("[data-stage-time]").get_attribute("data-source") == "jira"
            else:
                expect(figure).to_have_text("Too few to measure")
                expect(row.locator("[data-stage-time-source]")).to_contain_text("3 of the 5 issues needed, as of")
        # The measurement replaced the typed figure and no longer says typed.
        expect(_row(page, "Build")).not_to_contain_text("Typed by")
        # Only the slowest stage is flagged.
        expect(page.get_by_text("Slowest stage")).to_have_count(1)
        expect(_row(page, "Build").get_by_text("Slowest stage")).to_be_visible()
        expect(_stage_card(page, "Build").locator("[data-stage-duration]")).to_contain_text("Time taken: 15.5 days (measured) · Target: 10 days")
        _shot(page, "stage-times-1440-light")

        # The issues link lists the measured issues; each key opens that issue in Jira.
        _row(page, "Build").get_by_role("button", name="30 issues").click()
        issues = page.locator('[data-stage-issues]:visible')
        expect(issues.locator("li")).to_have_count(30, timeout=PAGE_TIMEOUT)
        link = issues.get_by_role("link", name="FLOW-30", exact=True)
        assert link.get_attribute("href") == "%s/browse/FLOW-30" % jira.url
        expect(issues.locator("li", has_text="FLOW-30")).to_contain_text("720 hours")
        with context.expect_page() as popup:
            link.click()
        opened = popup.value
        opened.wait_for_load_state("domcontentloaded")
        expect(opened.locator("body")).to_contain_text("FLOW-30")
        opened.close()

        # Everything persisted: after a reload the figures are the same.
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(_row(page, "Build").locator("[data-stage-time-figure]")).to_have_text("15.5 days")
        expect(_row(page, "Review").locator("[data-stage-time-figure]")).to_have_text("6.5 days")
        context.close()

        # Narrow, dark: the same figures, readable.
        narrow = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844}, color_scheme="dark")
        narrow.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
        page2 = narrow.new_page()
        sign_in(page2, base, org["emails"]["business_architect"])
        page2.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(_row(page2, "Build").locator("[data-stage-time-figure]")).to_have_text("15.5 days")
        expect(_row(page2, "Build").get_by_text("Slowest stage")).to_be_visible()
        expect(_row(page2, "Build").locator("[data-stage-target-narrow]")).to_have_text("Target: 10 days")
        assert page2.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "page scrolls sideways at 390"
        # On a phone every stage shows its issues link and both actions inside the
        # screen, clickable, with nothing in the row cut off by the table's scroll box.
        for stage_name in ("Build", "Review", "Test", "Release"):
            row = _row(page2, stage_name)
            controls = [row.get_by_role("button", name="Set duration or target")]
            controls.append(row.locator("[data-stage-issues-open]:visible"))
            for control in controls:
                expect(control).to_be_visible()
                box = control.bounding_box()
                assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390, "%s: control outside the screen at 390: %s" % (stage_name, box)
                control.click(trial=True)
            clipped = row.evaluate(
                "el => [el, ...el.querySelectorAll('*')].filter(n => n.offsetParent !== null && n.scrollWidth > n.clientWidth + 1).map(n => n.tagName + ':' + (n.textContent || '').trim().slice(0, 30))"
            )
            assert not clipped, "%s: text clipped at 390: %s" % (stage_name, clipped)
        assert page2.evaluate(
            "(() => { const c = document.querySelector('[data-testid=\"stage-times-table\"]').closest('[data-slot=\"table-container\"]'); return c.scrollWidth <= c.clientWidth + 1; })()"
        ), "stage times table scrolls sideways at 390"
        _shot(page2, "stage-times-390-dark")
        _row(page2, "Build").get_by_role("button", name="30 issues").click()
        expect(page2.locator('[data-stage-issues]:visible').locator("li")).to_have_count(30, timeout=PAGE_TIMEOUT)
        page2.wait_for_timeout(500)
        _shot(page2, "stage-times-390-dark-issues")
        for dialog in (
            page2.get_by_role("heading", name="Issues measured: Build"),
            page2.locator('[id^="stage-issues-"]:visible').get_by_role("button", name="Close"),
        ):
            dialog_box = dialog.bounding_box()
            assert dialog_box and dialog_box["x"] >= 0 and dialog_box["x"] + dialog_box["width"] <= 390, "dialog outside the screen at 390: %s" % dialog_box
        narrow.close()

        # A second organisation sees none of it.
        context_b, page_b = _new_page(browser)
        sign_in(page_b, base, other["emails"]["business_architect"])
        page_b.goto(base + "/value-streams/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page_b.locator("body")).not_to_contain_text(vs_name)
        response = page_b.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 404
        expect(page_b.locator("body")).not_to_contain_text("15.5 days")
        context_b.close()
    finally:
        jira.close()


def test_a_viewer_cannot_map_measure_or_type_stage_times(browser, live_server):
    """The three new writes carry the same roles as the stage edit beside them."""
    base = live_server
    org = create_fresh_org("viewer", platform_role="Viewer", extra_roles=("business_architect",))
    context, page = _new_page(browser)
    sign_in(page, base, org["emails"]["business_architect"])
    page.goto(base + "/value-streams/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="New Value Stream").first.click()
    modal = page.locator("#vs-create-modal")
    expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
    modal.locator("#vs-name").fill("Guarded %s" % uuid.uuid4().hex[:6])
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        modal.get_by_role("button", name="Create").click()
    detail_url = page.url
    page.get_by_role("button", name="Add Stage").first.click()
    create = page.locator("#stage-create-modal")
    create.locator("#stage-name").fill("Build")
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        create.get_by_role("button", name="Add Stage").click()
    stage_id = page.locator("[data-stage-card]").first.get_attribute("data-stage-id")
    vs_id = detail_url.rstrip("/").rsplit("/", 1)[1]
    context.close()

    context2, viewer = _new_page(browser)
    sign_in(viewer, base, org["emails"]["viewer"])
    viewer.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    for path, data in (
        ("/value-streams/%s/stage-times/mapping" % vs_id, {"statuses_%s" % stage_id: "In Progress"}),
        ("/value-streams/%s/stage-times/measure" % vs_id, {}),
        ("/value-streams/stages/%s/duration" % stage_id, {"duration": "9"}),
    ):
        status = viewer.evaluate(
            """async ([path, data]) => {
                const token = document.querySelector('meta[name="csrf-token"]').content;
                const body = new URLSearchParams(data);
                const r = await fetch(path, {method: 'POST', credentials: 'same-origin', redirect: 'manual',
                                             headers: {'X-CSRFToken': token}, body});
                return r.status;
            }""",
            [path, data],
        )
        assert status == 403, "%s answered %s to a viewer" % (path, status)
    viewer.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(_row(viewer, "Build").locator("[data-stage-time-figure]")).to_have_text("Not measured yet")
    context2.close()
