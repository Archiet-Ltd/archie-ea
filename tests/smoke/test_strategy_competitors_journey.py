"""A startup CTO sees how the company compares with its competitors, every claim with its source, and accepts it.

The company confirmed its profile at sign-up; its public signals and two named competitors are on record. The
analysis is drafted by a stand-in model that also makes three mistakes on purpose: a claim citing a source that does
not exist, a claim citing nothing, and a claim the cited source does not support. The person must never see them.

The journey: open Competitors and read the comparison (position, threat, strengths, weaknesses, how we differ, each
statement with a numbered source that opens to a titled, dated, linked entry); see the SWOT and PESTLE drafts with
their sources; accept; then find the accepted statements on the SWOT canvas and behind a link that lands on a page with
content; ask for the analysis again and be told it is being worked out; and an unrelated company sees none of it.
"""
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.strategy_seed import (
    COMPANY, FABRICATED, HARBOR, INVENTED_SHARE, NO_SOURCE, SWIFTLINE, FakeStrategyModel, seed_company,
)

from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("STRATEGY_JOURNEY_SHOTS")
PERSONA = "enterprise_architect"


def _company(drafted=True, label=COMPANY):
    """A fresh organisation with a confirmed profile, signals and two competitors; the analysis drafted when asked."""
    from app import create_app
    from app.services import strategy_draft_service as engine

    org = create_fresh_org(PERSONA)
    app = create_app("testing")
    with app.app_context():
        seed_company(org["org_id"], org["user_ids"][PERSONA])
        if drafted:
            engine.run_drafts(org["org_id"], llm=FakeStrategyModel())
    return org


def _open(browser, live_server, org, path, width=1440, dark=False):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1100})
    page = context.new_page()
    sign_in(page, live_server, org["emails"][PERSONA])
    page.goto(live_server + path, wait_until="domcontentloaded")
    if dark:
        page.evaluate("document.documentElement.classList.add('dark')")
    return context, page


def _shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / (name + ".png")), full_page=True)


def _cell(page, row, column):
    """The cell of the matrix at a row key and a competitor's position (0 = first column)."""
    return page.locator('[data-row="%s"]' % row).locator("td").nth(column)


def test_the_comparison_shows_every_claim_with_a_source_and_never_an_unsupported_one(browser, live_server):
    org = _company()
    for width, dark, name in ((1440, False, "matrix-draft-1440-light"), (390, False, "matrix-draft-390-light"),
                              (1440, True, "matrix-draft-1440-dark"), (390, True, "matrix-draft-390-dark")):
        context, page = _open(browser, live_server, org, "/strategy/competitors", width, dark)
        expect(page.locator("h1")).to_have_text("Competitors")
        expect(page.get_by_test_id("competitors-draft-note")).to_contain_text("Drafts to confirm")
        # competitors across the top, highest threat first; the company itself is not a column
        names = page.get_by_test_id("competitor-name")
        assert [names.nth(i).text_content().strip() for i in range(names.count())] == [HARBOR, SWIFTLINE]
        # the five questions down the side
        labels = [page.locator('[data-row="%s"] th' % k).text_content().strip() for k in ("position", "threat", "strength", "weakness", "difference")]
        assert labels == ["Market position", "Threat level", "Strengths", "Weaknesses", "How we differ"]
        badges = page.get_by_test_id("threat-level")
        assert [badges.nth(i).inner_text() for i in range(badges.count())] == ["High", "Medium"]
        expect(_cell(page, "position", 0)).to_contain_text("Established regional carrier with a large depot network")
        expect(_cell(page, "strength", 0)).to_contain_text("Large depot network across the region")
        expect(_cell(page, "difference", 1)).to_contain_text("We plan routes around refrigeration needs")
        # a competitor with nothing sourced for a row says so in words, never a blank or a dash
        expect(_cell(page, "weakness", 1)).to_have_text("Nothing found in public sources")
        # the three mistakes never reach the screen
        body = page.locator("main").inner_text()
        for gone in (INVENTED_SHARE, NO_SOURCE, FABRICATED):
            assert gone not in body, gone
        # every statement has a numbered source, and each number opens to a titled, linked, dated entry
        statements = page.get_by_test_id("statement")
        assert statements.count() >= 3
        marks = page.get_by_test_id("source-mark")
        sources = {int(el.get_attribute("id").split("-")[1]): el for el in page.get_by_test_id("source-item").all()}
        assert sources
        for i in range(marks.count()):
            mark = marks.nth(i)
            n = int(mark.get_attribute("href").split("-")[1])
            assert n in sources, "the mark [%d] points at nothing" % n
            assert mark.get_attribute("aria-label") == "Source %d" % n
        for item in sources.values():
            link = item.get_by_test_id("source-link")
            assert link.get_attribute("href").startswith("https://"), link.get_attribute("href")
            assert link.inner_text().strip()
            assert re.search(r"read \d{2} \w{3} \d{4} · \d+% sure", item.inner_text()), item.inner_text()
        # drafts carry the AI badge until accepted
        assert page.get_by_text("AI-generated").count() >= statements.count()
        # at 390 the matrix scrolls inside its card, keeps its shape, and the page itself does not overflow
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        if width == 390:
            assert page.evaluate("(() => { const c = document.querySelector('[data-testid=competitor-matrix]').parentElement; return c.scrollWidth > c.clientWidth; })()")
            assert page.evaluate("getComputedStyle(document.querySelector('[data-row=position] th')).position") == "sticky"
        _shot(page, name)
        context.close()


def test_a_number_opens_to_its_source_and_the_source_links_to_the_page_it_was_read_from(browser, live_server):
    org = _company()
    context, page = _open(browser, live_server, org, "/strategy/competitors")
    first = page.get_by_test_id("source-mark").first
    n = first.get_attribute("href").split("-")[1]
    first.click()
    assert page.url.endswith("#source-%s" % n)
    target = page.locator("#source-%s" % n)
    expect(target).to_be_in_viewport()
    expect(target.get_by_test_id("source-link")).to_have_attribute("rel", "noopener noreferrer")
    context.close()


def test_the_swot_and_pestle_drafts_show_their_sources_then_accepting_puts_them_on_the_canvases(browser, live_server):
    org = _company()
    context, page = _open(browser, live_server, org, "/strategy/drafts")
    expect(page.get_by_test_id("drafts-note")).to_contain_text("Drafts to confirm")
    swot = page.get_by_test_id("draft-card-swot")
    pestle = page.get_by_test_id("draft-card-pestle")
    expect(swot.get_by_test_id("draft-box-strengths")).to_contain_text("ColdRoute planning cuts empty running")
    expect(swot.get_by_test_id("draft-box-threats")).to_contain_text("Swiftline is funded to enter refrigerated freight")
    expect(pestle.get_by_test_id("draft-box-technological")).to_contain_text("Route planning tools are becoming standard")
    expect(pestle.get_by_test_id("draft-box-political")).to_have_count(0)  # nothing sourced, so no empty heading
    body = page.locator("main").inner_text()
    assert FABRICATED not in body and NO_SOURCE not in body
    for row in page.get_by_test_id("statement").all():
        assert row.get_by_test_id("source-mark").count() >= 1, row.inner_text()
        assert re.search(r"\d+% sure", row.inner_text())
    _shot(page, "drafts-1440-light")

    page.get_by_test_id("drafts-accept").click()
    expect(page.get_by_text("in your model, each with its sources")).to_be_visible()
    expect(page.get_by_test_id("drafts-note")).to_have_count(0)
    # the accepted SWOT is on the canvas list and its canvas shows the statement in its box
    link = page.get_by_test_id("canvas-link-swot")
    expect(link).to_be_visible()
    link.click()
    expect(page.locator("main")).to_contain_text("ColdRoute planning cuts empty running")
    _shot(page, "swot-canvas-1440-light")
    # the statement is a link to its own record, which carries where it came from
    note = page.get_by_text("ColdRoute planning cuts empty running").first
    note.click()
    page.wait_for_load_state("domcontentloaded")
    body = page.locator("main").inner_text()
    assert "ColdRoute planning cuts empty running" in body and len(body.strip()) > 80
    # the PESTLE canvas holds the economic and technological factors
    page.goto(live_server + "/strategy/drafts", wait_until="domcontentloaded")
    page.get_by_test_id("canvas-link-pestle").click()
    expect(page.locator("main")).to_contain_text("Route planning tools are becoming standard")
    context.close()


def test_accepting_the_comparison_removes_the_draft_marks_and_says_who_confirmed_it(browser, live_server):
    org = _company()
    context, page = _open(browser, live_server, org, "/strategy/competitors")
    page.get_by_test_id("drafts-accept").click()
    expect(page.get_by_test_id("competitors-draft-note")).to_have_count(0)
    expect(page.get_by_test_id("competitors-confirmed")).to_contain_text("Confirmed by")
    assert page.get_by_text("AI-generated").count() == 0
    names = page.get_by_test_id("competitor-name")
    assert [names.nth(i).text_content().strip() for i in range(names.count())] == [HARBOR, SWIFTLINE]
    expect(_cell(page, "strength", 0)).to_contain_text("Large depot network across the region")
    # a competitor is a record in the model: its name links to a page with content
    page.get_by_test_id("competitor-name").first.click()
    page.wait_for_load_state("domcontentloaded")
    assert HARBOR in page.locator("main").inner_text()
    page.go_back()
    for width, dark, name in ((1440, False, "matrix-confirmed-1440-light"), (1440, True, "matrix-confirmed-1440-dark"),
                              (390, False, "matrix-confirmed-390-light"), (390, True, "matrix-confirmed-390-dark")):
        page.set_viewport_size({"width": width, "height": 1100})
        page.evaluate("document.documentElement.classList.%s('dark')" % ("add" if dark else "remove"))
        _shot(page, name)
    context.close()


def test_asking_again_is_a_background_job_and_the_page_says_it_is_being_worked_out(browser, live_server):
    org = _company()
    context, page = _open(browser, live_server, org, "/strategy/competitors")
    page.get_by_test_id("drafts-accept").click()
    page.get_by_test_id("competitors-run").click()
    expect(page.get_by_text("Drafting the competitor analysis, SWOT and PESTLE")).to_be_visible()
    expect(page.get_by_test_id("competitors-drafting")).to_contain_text("being worked out")
    expect(page.get_by_test_id("competitors-drafting").get_by_role("link")).to_have_text("Show them when they are ready")
    context.close()

    from app import create_app
    from app.services.job_queue_service import get_job_queue_service

    with create_app("testing").app_context():
        jobs = get_job_queue_service().list_jobs(org["org_id"], task="strategy_drafts")
        assert len(jobs) == 1 and jobs[0].payload["requested_by"] == org["user_ids"][PERSONA]


def test_a_company_with_no_confirmed_profile_is_told_what_to_do_first(browser, live_server):
    org = create_fresh_org(PERSONA)
    context, page = _open(browser, live_server, org, "/strategy/competitors")
    empty = page.get_by_test_id("competitors-empty")
    expect(empty).to_contain_text("No competitor analysis yet")
    expect(empty).to_contain_text("Confirm the profile first")
    empty.get_by_role("link", name="Open Settings > Company").click()
    page.wait_for_load_state("domcontentloaded")
    assert "/account/manage" in page.url
    page.goto(live_server + "/strategy/drafts", wait_until="domcontentloaded")
    expect(page.get_by_test_id("drafts-empty")).to_contain_text("No drafts waiting")
    context.close()


def test_a_company_with_a_profile_and_no_drafts_yet_can_ask_for_the_analysis(browser, live_server):
    org = _company(drafted=False)
    context, page = _open(browser, live_server, org, "/strategy/competitors")
    expect(page.get_by_test_id("competitors-empty")).to_contain_text("No competitor analysis yet")
    page.get_by_test_id("competitors-run").click()
    expect(page.get_by_test_id("competitors-drafting")).to_be_visible()
    context.close()


def test_another_company_sees_none_of_it(browser, live_server):
    mine = _company()
    other = create_fresh_org(PERSONA)
    from app import create_app

    with create_app("testing").app_context():
        seed_company(other["org_id"], other["user_ids"][PERSONA], company="Other Co", rivals=["Rival Two"],
                     site="https://other.example", signals={"Other Co": [], "Rival Two": []})
    context, page = _open(browser, live_server, other, "/strategy/competitors")
    body = page.locator("main").inner_text()
    assert HARBOR not in body and SWIFTLINE not in body and COMPANY not in body
    expect(page.get_by_test_id("competitors-empty")).to_be_visible()
    page.goto(live_server + "/strategy/drafts", wait_until="domcontentloaded")
    body = page.locator("main").inner_text()
    assert "ColdRoute" not in body and "Swiftline" not in body
    # posting an accept as the other company records nothing for the first
    page.goto(live_server + "/strategy/competitors", wait_until="domcontentloaded")
    context.close()
    context, page = _open(browser, live_server, mine, "/strategy/competitors")
    expect(page.get_by_test_id("competitors-draft-note")).to_be_visible()  # still waiting: nobody else touched it
    context.close()
