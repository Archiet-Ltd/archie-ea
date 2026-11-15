"""A founder whose company has a common-word name reads only its own news, each story once, with tags that say why.

Three real sign-ups found what one company's tests could not: Linear (SEC filings of two other companies, posts on linear
algebra), cal.com (other people's tools that name it, a repost, four stories of one event) and PostHog (comparisons).
The sources answer from stubbed bodies built from those results; the founder opens the account page and reads the card.
A fourth person asks the chat a question their confirmed facts cannot answer and is told so once.
"""

import json
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_public_signals import FakeHttp, calcom_answers, linear_answers, load, posthog_answers

from .chat_ask import ask, role_of
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("COMMON_NAMES_JOURNEY_SHOTS")
PERSONA = "enterprise_architect"
COMPANIES = {
    "Linear": ("https://linear.app", linear_answers, "Issue tracking and project management for software teams"),
    "Cal.com": ("https://cal.com", calcom_answers, "Open Scheduling Infrastructure"),
    "PostHog": ("https://posthog.com", posthog_answers, "Product analytics"),
}
LINEAR_TITLES = {"Linear raises $35M Series B led by Accel", "Show HN: Linear – issue tracking for software teams",
                 "Linear changelog: cycles, projects and a new roadmap view", "Linear's pricing change draws questions from small teams"}


def _prepare(org_id, company, site, facts):
    from sqlalchemy.orm.attributes import flag_modified

    from app import create_app, db
    from app.models.organization import Organization
    from app.modules.organization import public_signals

    app = create_app("testing")
    with app.app_context():
        org = db.session.get(Organization, org_id)
        org.settings = {"company_profile": {"company_name": {"value": company}, "tagline": {"value": facts}},
                        "onboarding": {"website": {"url": site, "consent_at": "2026-10-01T00:00:00"}}}
        flag_modified(org, "settings")
        db.session.commit()
        public_signals.refresh_organization(
            org_id, http=FakeHttp(COMPANIES[company][1]()), llm=lambda prompt: json.dumps(load("sentiment")), force=True)
        db.session.remove()


@pytest.fixture(scope="module", autouse=True)
def _keys():
    before = {n: os.environ.get(n) for n in ("NEWSAPI_KEY", "BRAVE_SEARCH_API_KEY", "ALPHAVANTAGE_API_KEY")}
    os.environ.pop("ALPHAVANTAGE_API_KEY", None)
    os.environ.update({"NEWSAPI_KEY": "k", "BRAVE_SEARCH_API_KEY": "k"})
    yield
    for n, v in before.items():
        os.environ.pop(n, None) if v is None else os.environ.__setitem__(n, v)


@pytest.fixture(scope="module")
def orgs():
    made = {}
    for company, (site, _answers, facts) in COMPANIES.items():
        made[company] = create_fresh_org(PERSONA)
        _prepare(made[company]["org_id"], company, site, facts)
    return made


def _open(browser, live_server, org, width=1440, dark=False):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1100})
    page = context.new_page()
    email = org["emails"][PERSONA]
    assert role_of(email) == PERSONA
    sign_in(page, live_server, email)
    page.goto(live_server + "/account/manage", wait_until="domcontentloaded")
    if dark:
        page.evaluate("document.documentElement.classList.add('dark')")
    card = page.locator('[data-testid="company-signals-card"]')
    card.scroll_into_view_if_needed()
    return context, page, card


def _rows(card):
    table = card.locator('[data-testid="company-signals-table"]')
    expect(table.locator("tbody tr").first).to_be_visible()
    return [r for r in table.locator("tbody tr").all() if "Public sentiment" not in r.inner_text()]


def _titles(rows):
    return [r.locator("a, span.font-medium").first.inner_text() for r in rows]


VIEWS = [(1440, False), (390, False), (1440, True)]


@pytest.mark.parametrize("width, dark", VIEWS, ids=["1440", "390", "1440-dark"])
def test_linear_reads_its_own_news_and_not_other_companies_filings_or_linear_algebra(browser, live_server, orgs, width, dark):
    context, page, card = _open(browser, live_server, orgs["Linear"], width, dark)
    expect(card).to_contain_text("What the public sources say about Linear")
    rows = _rows(card)
    assert set(_titles(rows)) == LINEAR_TITLES, _titles(rows)
    text = card.inner_text()
    for other in ("LINEAR TECHNOLOGY", "MICRO LINEAR", "SEC EDGAR", "algebra", "Linear A ", "Integer linear"):
        assert other not in text, other
    raises = next(r for r in rows if "raises" in r.inner_text())
    expect(raises).to_contain_text("Economic")
    expect(raises.get_by_test_id("signal-reason")).to_have_text('Because the headline says "raises"')
    assert not re.search(r"_KEY|\b[45]\d\d\b", text), "the card shows a variable name or a status code"
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        card.screenshot(path=str(Path(SHOTS) / ("linear-%d%s.png" % (width, "-dark" if dark else ""))))
    context.close()


@pytest.mark.parametrize("width, dark", VIEWS, ids=["1440", "390", "1440-dark"])
def test_calcom_shows_its_own_stories_a_repost_once_and_one_event_as_one_row_with_its_count(browser, live_server, orgs, width, dark):
    context, page, card = _open(browser, live_server, orgs["Cal.com"], width, dark)
    expect(card).to_contain_text("What the public sources say about Cal.com")
    rows = _rows(card)
    titles = _titles(rows)
    assert not any("CalOverlap" in t or "Scaffold" in t for t in titles), titles
    assert titles.count("Cal.com: Open Scheduling Infrastructure") == 1, titles
    assert len(rows) == 2, titles
    story = next(r for r in rows if "source" in r.inner_text() and "Open Scheduling" not in r.inner_text())
    expect(story.get_by_test_id("signal-reports")).to_have_text("4 reports of this story")
    expect(story).to_contain_text("Technological")
    expect(story.get_by_test_id("signal-reason")).to_contain_text("closed source")
    assert story.get_by_role("link").first.get_attribute("href").startswith("https://news.ycombinator.com/item?id=")
    expect(card.get_by_test_id("signal-reports")).to_have_count(1)  # only the one event is a group
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        card.screenshot(path=str(Path(SHOTS) / ("calcom-%d%s.png" % (width, "-dark" if dark else ""))))
    context.close()


@pytest.mark.parametrize("width, dark", VIEWS, ids=["1440", "390", "1440-dark"])
def test_posthog_keeps_its_own_posts_and_drops_the_comparisons(browser, live_server, orgs, width, dark):
    context, page, card = _open(browser, live_server, orgs["PostHog"], width, dark)
    rows = _rows(card)
    titles = _titles(rows)
    assert "Launch HN: PostHog (YC W20)" in titles and "PostHog opens its roadmap" in titles, titles
    assert not any("Laminar" in t or "Phospho" in t for t in titles), titles
    assert len([t for t in titles if "Website" in t]) == 1, titles
    launch = next(r for r in rows if "Launch HN" in r.inner_text())
    expect(launch.get_by_test_id("signal-informs-none")).to_have_text("Not matched to PESTLE or SWOT")
    licence = next(r for r in rows if "licence" in r.inner_text())
    expect(licence).to_contain_text("Legal")
    expect(licence.get_by_test_id("signal-reason")).to_have_text('Because the headline says "licence"')
    context.close()


def test_across_the_three_companies_fewer_than_half_the_rows_are_not_matched(browser, live_server, orgs):
    total = unmatched = 0
    for company in COMPANIES:
        context, page, card = _open(browser, live_server, orgs[company])
        rows = _rows(card)
        total += len(rows)
        unmatched += sum(r.get_by_test_id("signal-informs-none").count() for r in rows)
        context.close()
    assert total and unmatched * 2 < total, (unmatched, total)


def test_the_chat_says_once_that_the_confirmed_facts_say_nothing_about_a_question(browser, live_server):
    from sqlalchemy.orm.attributes import flag_modified

    from app import create_app, db
    from app.models.organization import Organization

    org = create_fresh_org(PERSONA)
    app = create_app("testing")
    with app.app_context():
        row = db.session.get(Organization, org["org_id"])
        row.settings = {"onboarding": {"website": {"url": "https://example.test", "consent_at": "2026-10-01T00:00:00"}}}
        flag_modified(row, "settings")
        db.session.commit()
        db.session.remove()
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    sign_in(page, live_server, org["emails"][PERSONA])
    answer = ask(page, live_server, "What do we sell?")
    text = answer.inner_text()
    assert len(re.findall(r"say nothing about this", text, re.I)) == 1, text
    expect(answer.get_by_role("link", name="Read the website again")).to_be_visible()
    context.close()
