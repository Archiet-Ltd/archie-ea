"""An enterprise architect opens their account page and sees the public signals read about their company.

The company agreed at sign-up to its public footprint being read. The page lists what the public sources
said, each with where it came from and when, and which PESTLE factor or SWOT quadrant it informs. Nothing
reaches the internet: the sources answer from recorded bodies, as in tests/test_public_signals.py. A second
company has its own signals and the first never sees them; a company that agreed but has nothing yet is told
why and can ask for a reading; a company that did not agree sees no card.
"""

import json
import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_public_signals import COMPANY, FakeHttp, _answers, _model, load

from .chat_ask import role_of
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("SIGNALS_JOURNEY_SHOTS")
PERSONA = "enterprise_architect"


def _prepare(org_id, company, consent=True, read=None):
    """Record the company name and consent; optionally read the sources from recorded answers."""
    from sqlalchemy.orm.attributes import flag_modified

    from app import create_app, db
    from app.models.organization import Organization
    from app.modules.organization import public_signals

    app = create_app("testing")
    with app.app_context():
        org = db.session.get(Organization, org_id)
        settings = {"company_profile": {"company_name": {"value": company}},
                    "onboarding": {"website": {"url": "https://example.test"}}}
        if consent:
            settings["onboarding"]["website"]["consent_at"] = "2026-10-01T00:00:00"
        settings["public_signals"] = {"rss_feeds": ["https://logweekly.example/feed.xml"], "ticker": "NWF"}
        org.settings = settings
        flag_modified(org, "settings")
        db.session.commit()
        if read:
            public_signals.refresh_organization(org_id, http=FakeHttp(read), llm=_model)


@pytest.fixture(scope="module", autouse=True)
def _keys():
    names = {"NEWSAPI_KEY": "k", "BRAVE_SEARCH_API_KEY": "k"}  # no Alpha Vantage key: that source is left out
    before = {n: os.environ.get(n) for n in ("NEWSAPI_KEY", "BRAVE_SEARCH_API_KEY", "ALPHAVANTAGE_API_KEY")}
    os.environ.pop("ALPHAVANTAGE_API_KEY", None)
    os.environ.update(names)
    yield
    for n, v in before.items():
        os.environ.pop(n, None) if v is None else os.environ.__setitem__(n, v)


def _open(browser, live_server, org, width=1440, dark=False):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1100})
    page = context.new_page()
    email = org["emails"][PERSONA]
    assert role_of(email) == PERSONA  # the persona, not an administrator or platform role
    sign_in(page, live_server, email)
    page.goto(live_server + "/account/manage", wait_until="domcontentloaded")
    if dark:
        page.evaluate("document.documentElement.classList.add('dark')")
    return context, page


def _shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.locator('[data-testid="company-signals-card"]').scroll_into_view_if_needed()
        page.screenshot(path=str(Path(SHOTS) / (name + ".png")))


def test_signals_show_their_source_date_and_what_they_inform(browser, live_server):
    mine = create_fresh_org(PERSONA)
    other = create_fresh_org(PERSONA)
    _prepare(mine["org_id"], COMPANY, read=_answers())
    other_answers = {  # every source answers, with nothing about this company except one Hacker News story
        "https://newsapi.org/": {"status": "ok", "articles": []}, "https://api.search.brave.com/": {"web": {"results": []}},
        "https://efts.sec.gov/": {"hits": {"hits": []}}, "https://developer.uspto.gov/": {"results": []},
        "https://api.openalex.org/": {"results": []}, "https://www.reddit.com/": {"data": {"children": []}},
        "https://public.api.bsky.app/": {"posts": []}, "https://mastodon.social/": {"statuses": []},
        "https://logweekly.example/": (Path(__file__).parents[1] / "fixtures/public_signals/rss.xml").read_text(),
        "https://query1.finance.yahoo.com/": {"quotes": []},
        "https://hn.algolia.com/": {"hits": [{"title": "Other Co ships a thing", "objectID": "7", "created_at": "2026-10-01T00:00:00Z"}]},
    }
    _prepare(other["org_id"], "Other Co", read=other_answers)

    for width, dark, shot in ((1440, False, "signals-1440-light"), (390, False, "signals-390-light"), (1440, True, "signals-1440-dark")):
        context, page = _open(browser, live_server, mine, width, dark)
        card = page.locator('[data-testid="company-signals-card"]')
        expect(card).to_be_visible()
        table = card.locator('[data-testid="company-signals-table"]')
        expect(table.locator("tbody tr").first).to_be_visible()

        filing = table.locator("tr", has_text="10-Q filing")
        expect(filing.locator('[data-testid="signal-cite"]')).to_have_text("SEC EDGAR · 05 Aug 2026")
        expect(filing).to_contain_text("Legal")
        assert filing.locator("a").get_attribute("href").startswith("https://www.sec.gov/Archives/edgar/data/1234567/")

        depot = table.locator("tr", has_text="Rotterdam depot")
        expect(depot.locator('[data-testid="signal-cite"]')).to_have_text("NewsAPI · 20 Oct 2026")
        expect(depot).to_contain_text("Strength")
        assert depot.locator("a").get_attribute("href") == "https://techdaily.example/northwind-rotterdam"
        assert depot.locator("a").get_attribute("rel") == "noopener noreferrer"

        expect(table.locator("tr", has_text="Route planning")).to_contain_text("Technological")
        expect(table.locator("tr", has_text="Market data")).to_contain_text("Economic")
        expect(table.locator("tr", has_text="Public sentiment about %s" % COMPANY)).to_contain_text("Strength")
        # every signal names its source and carries a date, and both are visible without scrolling sideways
        for row in table.locator("tbody tr").all():
            cite = row.locator('[data-testid="signal-cite"]')
            expect(cite).to_be_visible()
            assert " · " in cite.inner_text() and cite.inner_text().split(" · ")[1].strip(), cite.inner_text()
            assert row.bounding_box()["x"] + row.bounding_box()["width"] <= page.viewport_size["width"]
        assert table.evaluate("t => t.scrollWidth <= t.parentElement.clientWidth + 1")
        assert "Other Co ships" not in card.inner_text()
        expect(card.locator('[data-testid="company-signals-left-out"]')).to_have_text(
            "Some public sources could not be read today; we try again tomorrow.")
        assert not re.search(r"_KEY|\b[45]\d\d\b", card.inner_text()), "the card shows a variable name or a status code"
        _shot(page, shot)
        context.close()

    context, page = _open(browser, live_server, other)
    card = page.locator('[data-testid="company-signals-card"]')
    expect(card).to_contain_text("Other Co ships a thing")
    assert "Rotterdam" not in card.inner_text() and "10-Q" not in card.inner_text()
    context.close()


def test_agreed_but_unread_says_why_and_reading_is_asked_for_and_kept(browser, live_server):
    org = create_fresh_org(PERSONA)
    _prepare(org["org_id"], "Fresh Co")
    context, page = _open(browser, live_server, org)
    card = page.locator('[data-testid="company-signals-card"]')
    expect(card).to_contain_text("No public signals have been read yet")
    expect(card).to_contain_text("read once a day")
    _shot(page, "signals-empty-1440-light")
    card.get_by_role("button", name="Read the public sources now").click()
    expect(page.get_by_text("Reading the public sources now")).to_be_visible()
    context.close()

    from app import create_app
    from app.services.job_queue_service import get_job_queue_service

    app = create_app("testing")
    with app.app_context():
        jobs = get_job_queue_service().list_jobs(org["org_id"], task="company_signals_refresh")
        assert len(jobs) == 1


def test_a_company_that_did_not_agree_sees_no_card_and_nothing_is_read(browser, live_server):
    org = create_fresh_org(PERSONA)
    _prepare(org["org_id"], "Private Co", consent=False)
    context, page = _open(browser, live_server, org)
    expect(page.locator('[data-testid="company-signals-card"]')).to_have_count(0)
    context.close()
