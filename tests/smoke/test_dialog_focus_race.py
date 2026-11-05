"""A dialog built from components/dialog.html gives the keyboard back to its opener however fast it is closed.

The Alpine focus plugin activates a dialog's focus trap from a 15 ms timer. A dialog dismissed before that timer
fires used to leave the trap active on a hidden dialog: focus fell to the page body and Tab did nothing. Every
caller is opened from the keyboard and closed at once, with no wait for focus to enter, by Escape, Enter on the
Close button and Space on the Close button, 20 times each. After each close focus must be on the opener and Tab
must reach the next control. The standalone fixture covers what no caller does today: a person who moves on
while a save runs, one dialog over another, and an opener that is re-rendered while the dialog is open.
"""
import jinja2
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_adm_board_deliverables_journey import CALIBRATION, DEMO_USER, _chip, demo_architect  # noqa: F401
from .test_billing_journey import _SKIP_WELCOME, _community_org
from .test_bpmn_process_diagram_journey import process_ids  # noqa: F401
from .test_dialog_focus_return import _buy_startup, paid_server  # noqa: F401
from .test_try_sample_company_journey import _empty_organisation_user, _load_sample_from_ask

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

REPEATS = 20
CLOSERS = ["escape", "enter-on-close", "space-on-close"]


def _context(browser, width=1440, scheme="light"):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.add_init_script(_SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    return context


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _where(page):
    return page.evaluate(
        "() => { const a = document.activeElement; return a ? a.tagName + ':' + "
        "((a.getAttribute('aria-label') || a.textContent || '').trim().slice(0, 30)) : 'none'; }")


def _close_now(page, dialog, how):
    if how == "escape":
        page.keyboard.press("Escape")
        return
    close = dialog.locator("button[aria-label='Close']")
    close.focus()
    page.keyboard.press("Enter" if how == "enter-on-close" else "Space")


def _hammer(page, opener, label, repeats=REPEATS):
    """Open from the keyboard and close at once, for every way of closing; return every miss found."""
    dialog = page.get_by_role("dialog")
    misses = []
    for how in CLOSERS:
        for turn in range(repeats):
            opener.focus()
            page.keyboard.press("Enter")
            expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
            _close_now(page, dialog, how)
            expect(dialog).to_be_hidden(timeout=PAGE_TIMEOUT)
            if not opener.evaluate("el => document.activeElement === el"):
                misses.append("%s #%d: focus on %s" % (how, turn, _where(page)))
                opener.click()
                page.keyboard.press("Escape")
                continue
            page.keyboard.press("Tab")
            moved = opener.evaluate("el => document.activeElement !== el && document.activeElement !== document.body")
            if not moved:
                misses.append("%s #%d: Tab went nowhere, focus on %s" % (how, turn, _where(page)))
            page.keyboard.press("Shift+Tab")
    print("DIALOG-RACE %s closes=%d misses=%d %s" % (label, 3 * repeats, len(misses), misses[:6]))
    return misses


def _no_misses(misses, label):
    assert not misses, "%s: %d misses: %s" % (label, len(misses), misses[:8])


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")], ids=["1440-light", "390-dark"])
def test_billing_cancel_dialog_survives_an_instant_close(browser, paid_server, app, width, scheme):
    base, _stub = paid_server
    _org, email = _community_org(app, members=0)
    context = _context(browser, width, scheme)
    page = context.new_page()
    try:
        _login(page, base, email)
        _buy_startup(page, base)
        page.reload(wait_until="domcontentloaded")
        opener = page.get_by_role("button", name="Cancel subscription")
        expect(opener).to_be_visible()
        _no_misses(_hammer(page, opener, "billing"), "billing")
    finally:
        context.close()


def test_rate_limits_dialog_survives_an_instant_close(browser, live_server, seeded):
    context = _context(browser)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/admin/rate-limits", wait_until="domcontentloaded")
        opener = page.get_by_role("button", name="Add an allow or deny entry")
        expect(opener).to_be_visible()
        _no_misses(_hammer(page, opener, "rate_limits"), "rate_limits")
    finally:
        context.close()


def test_sample_company_dialog_survives_an_instant_close(browser, live_server, seeded):
    email, _org = _empty_organisation_user(live_server)
    context = _context(browser)
    page = context.new_page()
    try:
        _login(page, live_server, email)
        _load_sample_from_ask(page, live_server)
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded")
        opener = page.get_by_role("button", name="Remove it", exact=True).first
        _no_misses(_hammer(page, opener, "admin_base"), "admin_base")
    finally:
        context.close()


def test_adm_board_dialog_survives_an_instant_close(browser, live_server, demo_architect):
    context = _context(browser)
    page = context.new_page()
    try:
        _login(page, live_server, demo_architect)
        page.goto(live_server + "/adm-kanban/", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        _no_misses(_hammer(page, _chip(page, CALIBRATION), "adm_board"), "adm_board")
    finally:
        context.close()


def test_process_diagram_import_dialog_survives_an_instant_close(browser, live_server, seeded, process_ids):
    context = _context(browser)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])
        page.goto("%s/business-architecture/processes/%d/diagram" % (live_server, process_ids["main"]),
                  wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        _no_misses(_hammer(page, page.get_by_test_id("bpmn-import-open"), "process_import"), "process_import")
        shortcuts = page.get_by_role("button", name="Keyboard Shortcuts")
        expect(shortcuts.first).to_be_visible()
        _no_misses(_hammer(page, shortcuts.first, "process_shortcuts"), "process_shortcuts")
    finally:
        context.close()


def _seed_batch(email):
    from app import create_app, db
    from app.models.batch_import import (BatchImportApplication, BatchImportBatch, BatchImportElement,
                                         BatchImportJob, BatchJobStatus, BatchStatus)
    from app.models.user import User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        job = BatchImportJob(user_id=user.id, filename="race.csv", name="Dialog race job", total_applications=1,
                             total_batches=1, status=BatchJobStatus.PENDING)
        db.session.add(job)
        db.session.flush()
        batch = BatchImportBatch(job_id=job.id, batch_number=1, status=BatchStatus.READY_FOR_REVIEW,
                                 total_applications=1)
        db.session.add(batch)
        db.session.flush()
        application = BatchImportApplication(batch_id=batch.id, row_number=1, source_data={"name": "Race App"},
                                             application_name="Race App")
        db.session.add(application)
        db.session.flush()
        db.session.add(BatchImportElement(
            batch_id=batch.id, application_id=application.id, element_type="archimate_element",
            element_subtype="application_component", element_name="Race Element", element_description="d",
            element_data={"name": "Race Element"}, archimate_layer="application", confidence_score=90))
        db.session.commit()
        return job.id, batch.id


def test_batch_import_dialogs_survive_an_instant_close(browser, live_server, seeded):
    email = seeded["emails"]["data_architect"]
    job_id, batch_id = _seed_batch(email)
    context = _context(browser)
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/batch-import/jobs/%d" % job_id, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        _no_misses(_hammer(page, page.get_by_role("button", name="Cancel").first, "job_detail"), "job_detail")
        page.goto(live_server + "/batch-import/batches/%d/review" % batch_id, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        _no_misses(_hammer(page, page.get_by_role("button", name="View element details").first, "batch_detail"),
                   "batch_detail")
        _no_misses(_hammer(page, page.locator("button[title='Edit']").first, "batch_edit"), "batch_edit")
    finally:
        context.close()


def test_link_capability_dialog_opens_traps_focus_and_returns_it(browser, live_server, demo_architect):
    context = _context(browser)
    page = context.new_page()
    try:
        _login(page, live_server, DEMO_USER)
        page.goto(live_server + "/enterprise/requirements-traceability", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        opener = page.get_by_role("button", name="Change capability link").first
        expect(opener).to_be_visible()
        dialog = page.get_by_role("dialog")
        opener.focus()
        page.keyboard.press("Enter")
        expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(dialog.get_by_role("heading", name="Link Capability")).to_be_visible()
        handle = dialog.element_handle()
        page.wait_for_function("d => d.contains(document.activeElement)", arg=handle, timeout=3000)
        for key in ["Tab"] * 5 + ["Shift+Tab"] * 5:
            page.keyboard.press(key)
            assert page.evaluate("d => d.contains(document.activeElement)", handle), "focus left the dialog on " + key
        page.keyboard.press("Escape")
        expect(dialog).to_be_hidden(timeout=PAGE_TIMEOUT)
        assert opener.evaluate("el => document.activeElement === el"), "focus is on " + _where(page)
        _no_misses(_hammer(page, opener, "link_capability"), "link_capability")
    finally:
        context.close()


FIXTURE = """
<div x-data="{ o: false, saving: false, gone: false, a: false, b: false }">
  <template x-if="!gone"><button type="button" id="op" :disabled="saving" @click="o = true">Open case</button></template>
  <input id="field" aria-label="field">
  {% call dialog(open_expr='o', close_expr='o = false', title='Case', start_hidden=false) %}
    <button type="button" id="remove-opener" @click="gone = true; $nextTick(() => { gone = false; })">Re-render opener</button>
    <button type="button" id="submit-disable" @click="saving = true; o = false; setTimeout(() => { saving = false; }, 500)">Save</button>
  {% endcall %}
  <section id="region"><h2 id="region-title">Region heading</h2>
    <template x-if="!gone2"><button type="button" id="swap-op" data-case="swap" @click="p = true">Open swap case</button></template>
  </section>
  <button type="button" id="opA" @click="a = true">Open A</button>
  {% call dialog(open_expr='a', close_expr='a = false', title='A', start_hidden=false) %}
    <button type="button" id="overB" @click="b = true">Open B on top</button>
  {% endcall %}
  {% call dialog(open_expr='b', close_expr='b = false', title='B', start_hidden=false) %}
    <button type="button" id="inB">Inside B</button>
  {% endcall %}
  {% call dialog(open_expr='p', close_expr='p = false', title='Swap', start_hidden=false) %}
    <button type="button" id="rerender-hard" @click="gone2 = true; $nextTick(() => { gone2 = false; })">Re-render</button>
    <button type="button" id="drop-opener" @click="gone2 = true">Drop opener</button>
  {% endcall %}
</div>
"""


@pytest.fixture()
def fixture_page(browser, live_server, app):
    from pathlib import Path

    source = (Path(app.root_path) / "templates" / "components" / "dialog.html").read_text()
    html = jinja2.Environment().from_string(source + FIXTURE.replace("x-data=\"{ o: false", "x-data=\"{ gone2: false, p: false, o: false", 1)).render()
    page_html = ("<!doctype html><html><head><meta charset=utf-8><style>[x-cloak]{display:none!important}.hidden{display:none}"
                 "</style><script defer src='/static/vendor/alpine-focus.min.js'></script>"
                 "<script defer src='/static/js/csp/csp-evaluator.js'></script>"
                 "<script defer src='/static/js/csp/alpine-csp-adapter.js'></script>"
                 "<script defer src='/static/vendor/alpine.min.js'></script></head><body><a href='#'>Skip</a>"
                 "<main>" + html + "</main></body></html>")
    context = _context(browser)
    page = context.new_page()
    page.route("**/dialog-fixture", lambda route: route.fulfill(status=200, content_type="text/html", body=page_html))
    page.goto(live_server + "/dialog-fixture", wait_until="domcontentloaded")
    page.wait_for_function("window.Alpine && document.querySelector('#op') && document.querySelector('#op')._x_effects !== undefined")
    yield page
    context.close()


def test_person_who_moves_on_while_a_save_runs_keeps_their_keystrokes(fixture_page):
    page = fixture_page
    page.focus("#op")
    page.keyboard.press("Enter")
    expect(page.get_by_role("dialog")).to_be_visible()
    page.focus("#submit-disable")
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    page.focus("#field")
    page.keyboard.type("ab")
    page.wait_for_timeout(500)
    page.keyboard.type("cd")
    page.wait_for_timeout(500)
    assert page.evaluate("document.activeElement.id") == "field"
    assert page.input_value("#field") == "abcd"


def test_escape_closes_only_the_top_dialog_and_returns_to_its_opener(fixture_page):
    page = fixture_page
    page.focus("#opA")
    page.keyboard.press("Enter")
    page.wait_for_function("document.activeElement.closest('[role=dialog]')")
    page.focus("#overB")
    page.keyboard.press("Enter")
    expect(page.locator("#inB")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#inB")).to_be_hidden()
    expect(page.locator("#overB")).to_be_visible()
    page.wait_for_timeout(300)
    assert page.evaluate("document.activeElement.id") == "overB", _where(page)
    page.keyboard.press("Escape")
    expect(page.locator("#overB")).to_be_hidden()
    page.wait_for_timeout(300)
    assert page.evaluate("document.activeElement.id") == "opA", _where(page)


def test_rerendered_opener_gets_focus_back_by_its_id(fixture_page):
    page = fixture_page
    page.focus("#op")
    page.keyboard.press("Enter")
    page.wait_for_function("document.activeElement.closest('[role=dialog]')")
    page.click("#remove-opener")
    page.wait_for_timeout(100)
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    assert page.evaluate("document.activeElement.id") == "op", _where(page)


def test_vanished_opener_falls_back_to_the_region_heading(fixture_page):
    page = fixture_page
    page.focus("#swap-op")
    page.keyboard.press("Enter")
    page.wait_for_function("document.activeElement.closest('[role=dialog]')")
    page.click("#drop-opener")
    page.wait_for_timeout(100)
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    assert page.evaluate("document.activeElement.id") == "region-title", _where(page)
