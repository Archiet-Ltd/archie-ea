"""A new owner loads the sample company, removes it, and the Overview reads as an empty model.

The person: a startup CTO who has just signed up. The goal: try the sample, then take it away and
see the Overview say what is true: nothing in the model, nothing added, nothing from a connected
source. A second organisation that keeps its own sample loaded must read the same figures before,
during and after. A record the person added and deleted while the sample was loaded still reads as
one record added and one removed once the sample is gone, agreeing with the organisation's history.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_first_answer_is_clean_journey import _load_sample
from .test_sign_up_first_screen_journey import SIZES, _skip_to_the_dashboard
from .test_signup_brings_the_company_in_journey import (  # noqa: F401  (people is a fixture)
    assert_is_a_startup_cto_not_an_administrator,
    people,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("ONB_WALK_DIR")


def _shot(page, name, width):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%d.png" % (name, width)), full_page=True)


def _context(browser, viewport):
    # the phone is read in the dark, the wide screen in the light
    dark = viewport["width"] < 600
    context = browser.new_context(viewport=viewport, color_scheme="dark" if dark else "light")
    context.set_default_timeout(PAGE_TIMEOUT)
    if dark:
        context.add_init_script(
            "try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
            "document.documentElement.classList.add('dark');"
        )
    return context, context.new_page()


def _text(page, testid):
    found = page.get_by_test_id(testid)
    return found.first.text_content().strip() if found.count() else None


def _overview(page, base):
    page.goto(base + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("overview-dashboard")).to_be_visible()
    chart = page.get_by_test_id("activity-chart").text_content()
    return {
        "totals": {key: _text(page, "activity-total-" + key) for key in ("7d", "30d", "90d")},
        "connected": re.findall(r"From connected sources\s*(\d+)", chart),
        "by_hand": re.findall(r"By hand\s*(\d+)", chart),
        "empty": _text(page, "activity-empty"),
        "year": _text(page, "year-value"),
        "year_empty": _text(page, "year-empty"),
        "week_empty": _text(page, "week-empty"),
        "recent_empty": _text(page, "recent-empty"),
        "recent": _text(page, "recent-changes"),
        "cost": _text(page, "card-run-cost-sentence"),
        "relationships": _text(page, "card-relationships-value"),
        "relationships_sentence": _text(page, "card-relationships-sentence"),
        "owners": _text(page, "card-no-owner-sentence"),
        "welcome": _text(page, "welcome-applications"),
    }


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_removing_the_sample_leaves_every_figure_at_nothing(browser, live_server, people, app, name, viewport):
    width = viewport["width"]
    context_b, page_b = _context(browser, viewport)
    context_a, page_a = _context(browser, viewport)
    try:
        # the other organisation keeps its sample loaded for the whole journey
        email_b, _ = sign_up(page_b, live_server, people)
        assert_is_a_startup_cto_not_an_administrator(app, email_b)
        _skip_to_the_dashboard(page_b)
        _load_sample(page_b, live_server)
        other = _overview(page_b, live_server)
        assert int(other["totals"]["90d"]) > 0, other

        email_a, _ = sign_up(page_a, live_server, people)
        assert_is_a_startup_cto_not_an_administrator(app, email_a)
        _skip_to_the_dashboard(page_a)
        _load_sample(page_a, live_server)

        loaded = _overview(page_a, live_server)
        _shot(page_a, "overview-sample-loaded", width)
        assert int(loaded["totals"]["7d"]) > 0 and int(loaded["totals"]["90d"]) >= int(loaded["totals"]["7d"]), loaded
        assert int(re.match(r"\d+", loaded["welcome"]).group()) >= 8, loaded
        assert int(loaded["relationships"]) > 0, loaded
        # the sample is the sample while it is loaded: no record of it is "from a connected source"
        assert loaded["connected"] and set(loaded["connected"]) == {"0"}, loaded
        # and the figures are the same two organisations' sample, read from their own records
        assert loaded["totals"] == other["totals"], (loaded, other)
        assert _overview(page_b, live_server) == other

        # remove it the way a person does: "Remove it", then the confirmation
        page_a.get_by_role("button", name="Remove it", exact=True).first.click()
        confirm = page_a.get_by_test_id("sample-company-remove-confirm")
        expect(confirm).to_be_visible()
        confirm.click()
        expect(page_a.get_by_text("Sample company removed")).to_be_visible()
        expect(page_a.get_by_test_id("sample-company-indicator")).to_have_count(0)

        gone = _overview(page_a, live_server)
        _shot(page_a, "overview-sample-removed", width)
        # every headline card is in its empty state
        assert "once a contract states its annual cost" in gone["cost"], gone
        assert gone["relationships"] is None and "once a source is connected" in gone["relationships_sentence"], gone
        assert "once applications are in the model" in gone["owners"], gone
        # the records-added panel, the year card, the week and the latest changes agree: nothing
        assert gone["empty"] and "Nothing was added" in gone["empty"], gone
        assert gone["totals"] == {"7d": None, "30d": None, "90d": None}, gone
        assert gone["year"] is None and gone["year_empty"], gone
        assert gone["week_empty"] and gone["recent_empty"] and gone["recent"] is None, gone
        body = page_a.get_by_test_id("overview-dashboard").inner_text()
        for sentence in ("added this year", "From connected sources", "Payments service", "Customer web app"):
            assert sentence not in body, sentence
        # the next step lands on a page with content
        page_a.get_by_test_id("recent-changes-link").click()
        page_a.wait_for_load_state("domcontentloaded")
        assert len(page_a.locator("main").first.inner_text().strip()) > 40

        # the other organisation did not move
        assert _overview(page_b, live_server) == other
    finally:
        context_a.close()
        context_b.close()


def _own_adds_in_the_history(app, email, days=90):
    """How many records the organisation added itself in the period, counted from its own change history."""
    from datetime import datetime, timedelta

    with app.app_context():
        from app.models.audit_log import AuditLog
        from app.models.user import User
        from app.modules.intelligence.services.history_service import AUDITED_TABLES
        from app.services.sample_company_service import written_by_the_sample

        org_id = User.query.filter_by(email=email).one().organization_id
        since = datetime.combine(datetime.utcnow().date() - timedelta(days=days - 1), datetime.min.time())
        sample = written_by_the_sample(org_id)
        rows = AuditLog.query.filter(
            AuditLog.organization_id == org_id, AuditLog.table_name.in_(list(AUDITED_TABLES)),
            AuditLog.action == "create", AuditLog.created_at >= since,
        ).all()
        return sum(1 for r in rows if (r.table_name, r.record_id) not in sample)


def _week(page):
    card = page.locator("[data-slot=card]", has=page.get_by_text("Changes this week", exact=True)).first
    text = card.inner_text()
    found = {k: re.search(r"%s\s*(\d+)" % k, text) for k in ("Added", "Updated", "Removed")}
    return {k: int(m.group(1)) if m else None for k, m in found.items()}


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_a_record_the_person_added_and_deleted_still_counts_after_the_sample_is_removed(
    browser, live_server, people, app, name, viewport
):
    width = viewport["width"]
    context_b, page_b = _context(browser, viewport)
    context_a, page_a = _context(browser, viewport)
    try:
        email_b, _ = sign_up(page_b, live_server, people)
        _skip_to_the_dashboard(page_b)
        _load_sample(page_b, live_server)
        other = _overview(page_b, live_server)
        assert int(other["totals"]["90d"]) > 0, other

        email_a, _ = sign_up(page_a, live_server, people)
        assert_is_a_startup_cto_not_an_administrator(app, email_a)
        _skip_to_the_dashboard(page_a)
        _load_sample(page_a, live_server)
        _overview(page_a, live_server)
        _shot(page_a, "overview-real-record-sample-loaded", width)

        # the person adds one stakeholder of their own, the way the architecture page offers it
        record = "Board audit committee %s" % os.urandom(3).hex()
        page_a.goto(live_server + "/architecture/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page_a.get_by_test_id("btn-create-element").click()
        form = page_a.locator("#archimate-form-modal")
        expect(form).to_be_visible()
        form.get_by_label("Element Type").select_option("Stakeholder")
        form.get_by_label("Element name").fill(record)
        with page_a.expect_response(
            lambda r: r.url.endswith("/architecture/motivation/Stakeholder/new") and r.request.method == "POST"
        ) as created:
            form.get_by_role("button", name="Create", exact=True).click()
        assert created.value.status == 200, created.value.status

        # then opens it and deletes it
        page_a.goto(live_server + "/architecture/motivation/Stakeholder", wait_until="domcontentloaded",
                    timeout=PAGE_TIMEOUT)
        page_a.get_by_role("link", name=record).first.click()
        expect(page_a.locator("main")).to_contain_text(record)
        page_a.get_by_test_id("delete-element").click()
        dialog = page_a.get_by_role("dialog", name="Confirm")
        expect(dialog).to_contain_text(record)
        dialog.get_by_role("button", name="Confirm").click()
        page_a.wait_for_url(re.compile(r"/architecture/motivation/Stakeholder$"), timeout=PAGE_TIMEOUT)

        # and removes the sample
        page_a.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page_a.get_by_role("button", name="Remove it", exact=True).first.click()
        confirm = page_a.get_by_test_id("sample-company-remove-confirm")
        expect(confirm).to_be_visible()
        confirm.click()
        expect(page_a.get_by_text("Sample company removed")).to_be_visible()

        after = _overview(page_a, live_server)
        week = _week(page_a)
        _shot(page_a, "overview-real-record-after-removal", width)
        # what the person did is still there: one record added, and one removed this week
        assert after["totals"] == {"7d": "1", "30d": "1", "90d": "1"}, after
        assert after["by_hand"][-1:] == ["1"] and set(after["connected"]) == {"0"}, after
        assert after["year"] == "1 added this year", after
        assert week == {"Added": 1, "Updated": 0, "Removed": 1}, week
        assert after["recent_empty"] is None and record in (after["recent"] or ""), after
        assert "Removed" in after["recent"], after
        # the records-added figure is the organisation's own history, counted without the sample
        assert int(after["totals"]["90d"]) == _own_adds_in_the_history(app, email_a), after
        body = page_a.get_by_test_id("overview-dashboard").inner_text()
        for sentence in ("From connected sources 1", "Payments service", "Customer web app"):
            assert sentence not in body, sentence
        # "See every change" lands on the change itself
        page_a.get_by_test_id("recent-changes-link").click()
        page_a.wait_for_load_state("domcontentloaded")
        expect(page_a.locator("main").first).to_contain_text(record)

        # the other organisation did not move
        assert _overview(page_b, live_server) == other
    finally:
        context_a.close()
        context_b.close()
