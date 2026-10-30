"""Public company signals: each source parsed from a recorded answer, stored once with its source, isolated per organisation."""

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app import db
from app.modules.organization.public_signals import sources as src
from app.modules.organization.public_signals import service

FIX = Path(__file__).parent / "fixtures" / "public_signals"
COMPANY = "Northwind Freight"


def load(name):
    return json.loads((FIX / (name + ".json")).read_text())


class FakeHttp(src.Http):
    """Answers each address with a recorded body; nothing leaves the machine."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def _body(self, url, kw):
        self.calls.append((url, kw))
        for prefix, body in self.answers.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return body
        raise AssertionError("unexpected address " + url)

    def json(self, url, **kw):
        return self._body(url, kw)

    def text(self, url, **kw):
        return self._body(url, kw)


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    for name in ("NEWSAPI_KEY", "ALPHAVANTAGE_API_KEY", "BRAVE_SEARCH_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def run(fetch, address, body, monkeypatch=None, ctx=None):
    return fetch(FakeHttp({address: body}), COMPANY, ctx or {})


def test_news_api_keeps_publisher_link_and_date(monkeypatch):
    monkeypatch.setenv("NEWSAPI_KEY", "k")
    http = FakeHttp({"https://newsapi.org/": load("news_api")})
    items = src.news_api(http, COMPANY, {})
    assert [i.url for i in items] == ["https://techdaily.example/northwind-rotterdam", "https://ledger.example/northwind-breach"]
    assert items[0].published == "2026-10-20T09:30:00Z" and items[0].details["publisher"] == "Tech Daily"
    assert http.calls[0][1]["headers"]["X-Api-Key"] == "k"  # the key goes in a header, never in the address


def test_news_api_error_status_is_a_plain_failure(monkeypatch):
    monkeypatch.setenv("NEWSAPI_KEY", "k")
    with pytest.raises(src.SourceFailed, match="rateLimited"):
        run(src.news_api, "https://newsapi.org/", {"status": "error", "code": "rateLimited"})


def test_brave_strips_markup(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "k")
    [item] = run(src.brave_search, "https://api.search.brave.com/", load("brave_search"))
    assert item.summary == "Northwind Freight is a logistics company." and item.url == "https://profiles.example/northwind"


def test_sec_edgar_keeps_only_this_company_and_links_the_document():
    [item] = run(src.sec_edgar, "https://efts.sec.gov/", load("sec_edgar"))
    assert item.title == "Northwind Freight Inc. (NWF) (CIK 0001234567): 10-Q filing"
    assert item.url == "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000012/nwf-20260630x10q.htm"
    assert item.published == "2026-08-05" and item.details["form_type"] == "10-Q"


def test_uspto_keeps_only_patents_assigned_to_the_company():
    [item] = run(src.uspto, "https://developer.uspto.gov/", load("uspto"))
    assert item.title == "Route planning for refrigerated freight" and item.published == "2026-05-12"
    assert item.url == "https://patents.google.com/patent/US11999999"


def test_open_alex_rebuilds_the_abstract_in_order():
    [item] = run(src.open_alex, "https://api.openalex.org/", load("open_alex"))
    assert item.summary == "Cold chain routing improves delivery" and item.details["cited_by_count"] == 14
    assert item.url == "https://doi.org/10.1000/xyz"


def test_reddit_hackernews_bluesky_mastodon_each_keep_link_and_date():
    [r] = run(src.reddit, "https://www.reddit.com/", load("reddit"))
    assert r.url == "https://reddit.com/r/logistics/comments/abc/northwind/" and r.published.startswith("2025-10-19")
    [h] = run(src.hackernews, "https://hn.algolia.com/", load("hackernews"), ctx={"page_limit": 1})
    assert h.url == "https://news.ycombinator.com/item?id=4242" and h.published.startswith("2026-10-15")
    [b] = run(src.bluesky, "https://public.api.bsky.app/", load("bluesky"))
    assert b.url == "https://bsky.app/profile/fan.bsky.social/post/3kabc" and b.title.startswith("@fan.bsky.social: Northwind")
    [m] = run(src.mastodon, "https://mastodon.social/", load("mastodon"))
    assert m.summary == "Northwind Freight is hiring" and m.url == "https://mastodon.social/@a/1"


def test_rss_reads_only_listed_feeds_and_keeps_items_naming_the_company():
    ctx = {"rss_feeds": ["https://logweekly.example/feed.xml"]}
    http = FakeHttp({"https://logweekly.example/": (FIX / "rss.xml").read_text()})
    [item] = src.rss(http, COMPANY, ctx)
    assert item.title == "Northwind Freight wins a rail contract" and item.url == "https://logweekly.example/nwf-rail"
    assert item.published == "2026-10-13T08:00:00" and item.summary == "The contract runs five years."
    assert src.rss(http, COMPANY, {}) == []


def test_yahoo_finance_finds_the_listed_equity_by_name_then_quotes_it():
    http = FakeHttp({"https://query1.finance.yahoo.com/v1/finance/search": load("yahoo_search"),
                     "https://query1.finance.yahoo.com/v7/finance/quote": load("yahoo_finance")})
    [item] = src.yahoo_finance(http, COMPANY, {})
    assert item.title == "Market data: Northwind Freight Inc. (NWF)"
    assert "Price 48.20 USD" in item.summary and "market cap 12.5 billion" in item.summary
    assert item.details["symbol"] == "NWF"


def test_yahoo_finance_gives_nothing_for_an_unlisted_company():
    assert src.yahoo_finance(FakeHttp({"https://query1.finance.yahoo.com/v1/finance/search": {"quotes": []}}), COMPANY, {}) == []


def test_alpha_vantage_overview(monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "k")
    http = FakeHttp({"https://www.alphavantage.co/": None})
    answers = iter([load("alpha_search"), load("alpha_overview")])
    http.json = lambda url, **kw: next(answers)
    [item] = src.alpha_vantage(http, COMPANY, {})
    assert item.title == "Financial overview: Northwind Freight Inc. (NWF)" and "Sector Industrials" in item.summary
    assert item.published == "2026-06-30"


def test_a_source_needing_a_key_is_skipped_plainly_until_the_key_is_set(monkeypatch):
    news = src.BY_KEY["news_api"]
    assert src.skipped_reason(news, {}) == "needs NEWSAPI_KEY to be set"
    monkeypatch.setenv("NEWSAPI_KEY", "k")
    assert src.skipped_reason(news, {}) is None
    assert src.skipped_reason(src.BY_KEY["rss"], {}) == "has no feed addresses listed"


def test_registry_is_every_operateiq_source():
    assert {s.key for s in src.REGISTRY} == {
        "news_api", "brave_search", "sec_edgar", "uspto", "open_alex", "reddit", "hackernews", "bluesky", "mastodon",
        "rss", "yahoo_finance", "alpha_vantage", "llmlayer"}


def test_http_retries_then_says_what_happened():
    class Resp:
        def __init__(self, code):
            self.status_code = code

        def json(self):
            return {"ok": 1}

    class Session:
        def __init__(self, codes):
            self.codes = list(codes)

        def request(self, *a, **k):
            return Resp(self.codes.pop(0))

    slept = []
    assert src.Http(Session([429, 200]), slept.append).json("https://x.example") == {"ok": 1}
    with pytest.raises(src.SourceFailed, match="answered 503"):
        src.Http(Session([503] * 4), slept.append).json("https://x.example")
    with pytest.raises(src.SourceFailed, match="answered 404"):
        src.Http(Session([404]), slept.append).json("https://x.example")


def test_factor_and_quadrant_rules():
    assert service.pestle_factor("filing", "x", "") == "legal"
    assert service.pestle_factor("news", "Firm fined over privacy", "") == "legal"
    assert service.pestle_factor("news", "Firm cuts emissions", "") == "environmental"
    assert service.pestle_factor("news", "A quiet day", "") is None  # no rule matches: no factor is invented
    assert service.swot_quadrant(True, 0.6) == "strength" and service.swot_quadrant(True, -0.7) == "weakness"
    assert service.swot_quadrant(False, 0.6) == "threat" and service.swot_quadrant(False, -0.7) == "opportunity"
    assert service.swot_quadrant(True, 0.1) is None and service.swot_quadrant(True, None) is None


# ----------------------------------------------------------------------------- new mode tests (pagination, subreddit, sort, top/ask, author feed, timeline, key rotation, LLMLayer)

class SequenceFakeHttp:
    """FakeHttp that returns sequential responses for the same prefix."""
    def __init__(self, sequences: Dict[str, List[Any]]):
        self.sequences = {k: list(v) for k, v in sequences.items()}
        self.call_counts: Dict[str, int] = {}

    def _body(self, url: str, kw):
        for prefix, responses in self.sequences.items():
            if url.startswith(prefix):
                idx = self.call_counts.get(prefix, 0)
                if idx >= len(responses):
                    raise AssertionError("no more responses for " + prefix)
                self.call_counts[prefix] = idx + 1
                body = responses[idx]
                if isinstance(body, Exception):
                    raise body
                return body
        raise AssertionError("unexpected address " + url)

    def json(self, url: str, **kw):
        return self._body(url, kw)

    def text(self, url: str, **kw):
        return self._body(url, kw)


def test_reddit_pagination_subreddit_sort(monkeypatch):
    """Reddit: pagination (page_limit), per-subreddit search, and sort order."""
    # Pagination: two pages
    http = SequenceFakeHttp({
        "https://www.reddit.com/r/all/": [load("reddit_pagination"), load("reddit_page2")],
    })
    items = src.reddit(http, COMPANY, {"page_limit": 2})
    assert len(items) == 2
    assert items[0].title == "Northwind Freight page 1 post"
    assert items[1].title == "Northwind Freight page 2 post"
    # Subreddit
    http = FakeHttp({"https://www.reddit.com/r/logistics/": load("reddit_subreddit")})
    items = src.reddit(http, COMPANY, {"subreddit": "logistics", "page_limit": 1})
    assert len(items) == 1
    assert items[0].details["subreddit"] == "logistics"
    # Sort order
    http = FakeHttp({"https://www.reddit.com/r/all/": load("reddit_sort")})
    items = src.reddit(http, COMPANY, {"reddit_sort": "top", "page_limit": 1})
    assert len(items) == 1
    assert items[0].details["score"] == 100


def test_hackernews_top_ask_pagination(monkeypatch):
    """Hacker News: top stories, Ask HN, and pagination."""
    # Top stories
    http = SequenceFakeHttp({
        "https://hacker-news.firebaseio.com/v0/topstories.json": [[1001]],
        "https://hacker-news.firebaseio.com/v0/item/1001.json": [load("hackernews_item")],
    })
    items = src.hackernews(http, COMPANY, {"hn_mode": "top", "page_limit": 1})
    assert len(items) == 1
    assert items[0].title == "Northwind Freight top story"
    assert items[0].details["points"] == 200
    # Ask HN
    http = FakeHttp({"https://hn.algolia.com/": load("hackernews_ask")})
    items = src.hackernews(http, COMPANY, {"hn_mode": "ask", "page_limit": 1})
    assert len(items) == 1
    assert items[0].title == "Ask HN: How does Northwind Freight handle routing?"
    assert items[0].details.get("type") == "ask_hn"
    # Search pagination (two pages)
    http = SequenceFakeHttp({
        "https://hn.algolia.com/": [load("hackernews"), load("hackernews_page2")],
    })
    items = src.hackernews(http, COMPANY, {"hn_mode": "search", "page_limit": 2})
    assert len(items) == 2
    assert items[0].title == "Northwind Freight publishes its routing engine"
    assert items[1].title == "Northwind Freight page 2 story"


def test_bluesky_author_feed(monkeypatch):
    """Bluesky: author feed mode."""
    http = FakeHttp({"https://public.api.bsky.app/": load("bluesky_author")})
    items = src.bluesky(http, COMPANY, {"bluesky_mode": "author", "bluesky_author": "northwind.bsky.social"})
    assert len(items) == 2
    assert items[0].title.startswith("@northwind.bsky.social: Northwind Freight announces")
    assert items[1].title.startswith("@northwind.bsky.social: Northwind Freight hiring")
    assert items[0].url == "https://bsky.app/profile/northwind.bsky.social/post/post1"
    # Search mode still works
    http = FakeHttp({"https://public.api.bsky.app/": load("bluesky")})
    items = src.bluesky(http, COMPANY, {"bluesky_mode": "search"})
    assert len(items) == 1
    assert items[0].title.startswith("@fan.bsky.social: Northwind")


def test_mastodon_timeline_instance(monkeypatch):
    """Mastodon: public timeline and configurable instance."""
    # Public timeline
    http = FakeHttp({"https://mastodon.social/": load("mastodon_timeline")})
    items = src.mastodon(http, COMPANY, {"mastodon_mode": "timeline"})
    assert len(items) == 2
    assert items[0].title.startswith("@northwind: Northwind Freight public")
    assert items[0].details["instance"] == "https://mastodon.social"
    # Configurable instance
    http = FakeHttp({"https://mastodon.example/": load("mastodon_instance")})
    items = src.mastodon(http, COMPANY, {"mastodon_mode": "search", "mastodon_instance": "https://mastodon.example"})
    assert len(items) == 1
    assert items[0].details["instance"] == "https://mastodon.example"
    assert "mastodon.example" in items[0].url


def test_brave_key_rotation(monkeypatch):
    """Brave: rotation over BRAVE_SEARCH_API_KEYS (comma-separated), falling back to BRAVE_SEARCH_API_KEY."""
    # Multiple keys in BRAVE_SEARCH_API_KEYS
    monkeypatch.setenv("BRAVE_SEARCH_API_KEYS", "key1,key2,key3")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    http = FakeHttp({"https://api.search.brave.com/": load("brave_search")})
    items = src.brave_search(http, COMPANY, {})
    assert len(items) == 1
    assert items[0].title == "Northwind Freight logistics profile"
    # Fallback to BRAVE_SEARCH_API_KEY
    monkeypatch.delenv("BRAVE_SEARCH_API_KEYS", raising=False)
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "fallback-key")
    http = FakeHttp({"https://api.search.brave.com/": load("brave_search")})
    items = src.brave_search(http, COMPANY, {})
    assert len(items) == 1
    # No key at all -> skipped
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    http = FakeHttp({})
    with pytest.raises(src.SourceFailed, match="needs BRAVE_SEARCH_API_KEY"):
        src.brave_search(http, COMPANY, {})


def test_llmlayer_skipped_without_key(monkeypatch):
    """LLMLayer: fallback reader that is skipped when LLMLAYER_API_KEY is absent."""
    monkeypatch.delenv("LLMLAYER_API_KEY", raising=False)
    http = FakeHttp({})
    with pytest.raises(src.SourceFailed, match="needs LLMLAYER_API_KEY"):
        src.llmlayer(http, COMPANY, {})
    # With key
    monkeypatch.setenv("LLMLAYER_API_KEY", "test-key")
    http = FakeHttp({"https://api.llmlayer.ai/": load("llmlayer")})
    items = src.llmlayer(http, COMPANY, {})
    assert len(items) == 1
    assert items[0].title == "Northwind Freight LLMLayer result"
    assert items[0].url == "https://llmlayer.example/northwind"
    # Skipped reason
    monkeypatch.delenv("LLMLAYER_API_KEY", raising=False)
    reason = src.skipped_reason(src.BY_KEY["llmlayer"], {})
    assert reason == "needs LLMLAYER_API_KEY to be set"
    monkeypatch.setenv("LLMLAYER_API_KEY", "test-key")
    assert src.skipped_reason(src.BY_KEY["llmlayer"], {}) is None


# ----------------------------------------------------------------------------- service, against the database

def _answers():
    return {
        "https://newsapi.org/": load("news_api"), "https://api.search.brave.com/": load("brave_search"),
        "https://efts.sec.gov/": load("sec_edgar"), "https://developer.uspto.gov/": load("uspto"),
        "https://api.openalex.org/": load("open_alex"), "https://www.reddit.com/": load("reddit"),
        "https://hn.algolia.com/": load("hackernews"), "https://public.api.bsky.app/": load("bluesky"),
        "https://mastodon.social/": load("mastodon"), "https://logweekly.example/": (FIX / "rss.xml").read_text(),
        "https://query1.finance.yahoo.com/v1/": load("yahoo_search"), "https://query1.finance.yahoo.com/v7/": load("yahoo_finance"),
        "https://www.alphavantage.co/": load("alpha_overview"),
    }


def _model(prompt):
    assert "Northwind Freight" in prompt and "techdaily.example" in prompt
    return "```json\n" + json.dumps(load("sentiment")) + "\n```"


def _org(db_session, make_org, consent=True, name=COMPANY):
    org = make_org("signals")
    org.name = name
    settings = {"onboarding": {"website": {"url": "https://northwind.example"}}}
    if consent:
        settings["onboarding"]["website"]["consent_at"] = "2026-10-01T00:00:00"
    settings["public_signals"] = {"rss_feeds": ["https://logweekly.example/feed.xml"], "ticker": "NWF"}
    org.settings = settings
    db_session.commit()
    # the reading runs in a tenant scope that detaches loaded rows, so tests hold the id, not the row
    return SimpleNamespace(id=org.id)


def _fresh(org):
    from app.models.organization import Organization

    return db.session.get(Organization, org.id)


@pytest.fixture
def keys(monkeypatch):
    for name in ("NEWSAPI_KEY", "ALPHAVANTAGE_API_KEY", "BRAVE_SEARCH_API_KEY"):
        monkeypatch.setenv(name, "test-key")


def test_refresh_stores_each_source_with_its_link_and_date(db_session, make_org, keys):
    org = _org(db_session, make_org)
    report = service.refresh_organization(org.id, http=FakeHttp(_answers()), llm=_model)
    assert all(report["sources"][k].get("signals", 0) >= 1 for k in ("news_api", "sec_edgar", "uspto", "open_alex", "reddit", "rss", "yahoo_finance"))
    rows = service.signals_for_profile(org.id)
    kinds = {r["kind"] for r in rows}
    assert {"news", "filing", "financial", "patent", "research", "mention", "sentiment"} <= kinds
    cited = [r for r in rows if r["kind"] != "sentiment"]
    assert cited and all(r["url"] and r["source"] and (r["published_at"] or r["fetched_at"]) for r in cited)
    sec = next(r for r in rows if r["source_key"] == "sec_edgar")
    assert sec["published_at"] == datetime(2026, 8, 5) and sec["pestle_factor"] == "legal"
    depot = next(r for r in rows if r["url"] == "https://techdaily.example/northwind-rotterdam")
    assert depot["swot_quadrant"] == "strength" and depot["sentiment_score"] == 0.6
    breach = next(r for r in rows if r["url"] == "https://ledger.example/northwind-breach")
    assert breach["swot_quadrant"] == "weakness" and breach["pestle_factor"] == "legal"
    overall = next(r for r in rows if r["kind"] == "sentiment")
    assert overall["sentiment_score"] == 0.35 and "Rotterdam depot" in overall["summary"]


def test_a_second_refresh_updates_in_place_and_is_held_to_once_a_day(db_session, make_org, keys):
    org = _org(db_session, make_org)
    service.refresh_organization(org.id, http=FakeHttp(_answers()), llm=_model)
    first = len(service.signals_for_profile(org.id))
    with pytest.raises(service.SignalsRefused, match="once a day"):
        service.refresh_organization(org.id, http=FakeHttp(_answers()), llm=_model)
    service.refresh_organization(org.id, http=FakeHttp(_answers()), llm=_model, now=datetime.utcnow() + timedelta(days=1))
    assert len(service.signals_for_profile(org.id)) == first + 1  # only the new day's sentiment reading is new


def test_without_consent_nothing_is_read(db_session, make_org, keys):
    org = _org(db_session, make_org, consent=False)
    http = FakeHttp(_answers())
    with pytest.raises(service.SignalsRefused, match="not agreed"):
        service.refresh_organization(org.id, http=http, llm=_model)
    assert http.calls == [] and service.signals_for_profile(org.id) == []


def test_missing_keys_skip_those_sources_and_the_card_says_so(db_session, make_org):
    org = _org(db_session, make_org)
    report = service.refresh_organization(org.id, http=FakeHttp(_answers()), llm=_model)
    assert report["sources"]["news_api"]["skipped"] == "needs NEWSAPI_KEY to be set"
    assert report["sources"]["alpha_vantage"]["skipped"] == "needs ALPHAVANTAGE_API_KEY to be set"
    state = service.refresh_state(_fresh(org))
    assert state["some_unread"] is True and state["refreshed_at"] is not None
    assert "NEWSAPI" not in str(state) and "ALPHAVANTAGE" not in str(state) and "_KEY" not in str(_fresh(org).settings)


def test_a_failing_source_is_named_and_the_rest_still_arrive(db_session, make_org, keys):
    org = _org(db_session, make_org)
    answers = _answers()
    answers["https://efts.sec.gov/"] = src.SourceFailed("answered 503")
    report = service.refresh_organization(org.id, http=FakeHttp(answers), llm=_model)
    assert report["sources"]["sec_edgar"]["failed"] == "SEC EDGAR answered 503"
    assert report["sources"]["uspto"]["signals"] == 1
    state = service.refresh_state(_fresh(org))
    assert state["some_unread"] is True
    assert "503" not in str(state) and "503" not in str(_fresh(org).settings)  # the code stays in the server log


def test_signals_feed_pestle_swot_and_competitor_views(db_session, make_org, keys):
    org = _org(db_session, make_org)
    row = _fresh(org)
    row.settings = {**row.settings, "public_signals": {**row.settings["public_signals"], "competitors": ["Contoso Haulage"]}}
    db_session.commit()
    service.refresh_organization(org.id, http=FakeHttp(_answers()), llm=_model)
    pestle = service.framework_view(org.id, "pestle")
    assert any(r["source_key"] == "sec_edgar" for r in pestle["legal"]) and any(r["kind"] == "patent" for r in pestle["technological"])
    assert any(r["kind"] == "financial" for r in pestle["economic"])
    swot = service.framework_view(org.id, "swot")
    assert swot["strength"] and swot["weakness"]
    competitors = service.framework_view(org.id, "competitors")
    assert list(competitors) == ["Contoso Haulage"] and competitors["Contoso Haulage"]
    assert all(r["subject"] == "Contoso Haulage" for r in competitors["Contoso Haulage"])


def test_two_organisations_never_see_each_others_signals(db_session, make_org, tenant_ctx, keys):
    a, b = _org(db_session, make_org), _org(db_session, make_org, name="Other Co")
    service.refresh_organization(a.id, http=FakeHttp(_answers()), llm=_model)
    answers = {k: v for k, v in _answers().items()}
    answers["https://hn.algolia.com/"] = {"hits": [{"title": "Other Co ships", "objectID": "7", "created_at": "2026-10-01T00:00:00Z"}]}
    service.refresh_organization(b.id, http=FakeHttp(answers), llm=lambda p: json.dumps(load("sentiment")))
    with tenant_ctx(a.id):
        a_rows = service.signals_for_profile(a.id)
        assert a_rows and all(r["subject"] == COMPANY for r in a_rows)
        assert not any("news.ycombinator.com/item?id=7" == (r["url"] or "") for r in a_rows)
        assert service.signals_for_profile(b.id) == []  # the scope, not the argument, decides
    with tenant_ctx(b.id):
        assert any(r["url"] == "https://news.ycombinator.com/item?id=7" for r in service.signals_for_profile(b.id))
        assert service.framework_view(a.id, "pestle")["legal"] == []


def test_refresh_all_visits_only_organisations_that_agreed(app, db_session, make_org, keys):
    yes, no = _org(db_session, make_org), _org(db_session, make_org, consent=False)
    db_session.commit()
    results = service.refresh_all(app, http=FakeHttp(_answers()), llm=_model)
    by_org = {r["organization_id"]: r for r in results}
    assert "stored" in by_org[yes.id] and "not agreed" in by_org[no.id]["refused"]


# ----------------------------------------------------------------------------- only the confirmed company is searched, and only hits about it are kept

STAND_IN = "Casey Startup's Workspace"


def _confirmed(db_session, make_org, name, site, stand_in=STAND_IN):
    """An organisation that agreed to be read and confirmed its company name, still called by the stand-in."""
    org = make_org("confirmed")
    org.name = stand_in
    org.settings = {
        "onboarding": {"website": {"url": site, "consent_at": "2026-10-01T00:00:00"}},
        "company_profile": {"company_name": {"value": name}},
    }
    db_session.commit()
    return SimpleNamespace(id=org.id)


def _stored(org_id, **fields):
    from app.models.organization_signal import OrganizationSignal

    row = OrganizationSignal(organization_id=org_id, checksum=uuid.uuid4().hex, fetched_at=datetime.utcnow(), **fields)
    db.session.add(row)
    db.session.commit()
    return row.id


def _rows(org_id):
    from app.models.organization_signal import OrganizationSignal

    return OrganizationSignal.query.filter_by(organization_id=org_id).all()


def _posthog_answers():
    answers = _answers()
    answers["https://hn.algolia.com/"] = {"hits": [
        {"title": "PostHog opens its roadmap", "objectID": "1", "created_at": "2026-10-10T00:00:00Z"},
        {"title": "Show HN: a product analytics stack", "url": "https://app.posthog.com/stack", "objectID": "2", "created_at": "2026-10-11T00:00:00Z"},
        {"title": "Show HN: Booking made simple", "story_text": "Book a call at posthog.com/demo", "url": "https://example.org/booking", "objectID": "5", "created_at": "2026-10-13T00:00:00Z"},
        {"title": "Entrepreneurial passion and venture outcomes", "objectID": "3", "created_at": "2008-03-01T00:00:00Z"},
        {"title": "Post hog roast recipes", "objectID": "4", "created_at": "2026-10-12T00:00:00Z"},
    ]}
    answers["https://api.openalex.org/"] = {"results": [
        {"id": "https://openalex.org/W9", "display_name": "Neuroprotective Effect of Lithium Chloride in Rat Model of Cardiac Arrest",
         "publication_date": "2020-01-01", "abstract_inverted_index": {"Rats": [0], "recover": [1]}},
    ]}
    for prefix, empty in (("https://www.reddit.com/", {"data": {"children": []}}), ("https://public.api.bsky.app/", {"posts": []}),
                          ("https://mastodon.social/", {"statuses": []}), ("https://efts.sec.gov/", {"hits": {"hits": []}}),
                          ("https://developer.uspto.gov/", {"results": []}), ("https://query1.finance.yahoo.com/", {"quotes": []})):
        answers[prefix] = empty
    return answers


def _no_model(prompt):
    return json.dumps(load("sentiment"))


def test_a_stand_in_name_is_never_searched_and_nothing_is_kept_under_it(db_session, make_org, keys):
    org = make_org("standin")
    org.name = STAND_IN
    org.settings = {"onboarding": {"website": {"url": "https://posthog.com", "consent_at": "2026-10-01T00:00:00"}}}
    db_session.commit()
    mine = SimpleNamespace(id=org.id)
    _stored(mine.id, subject=STAND_IN, source="hackernews", kind="mention", title="Show HN from 2008", summary="")
    http = FakeHttp(_answers())
    with pytest.raises(service.SignalsRefused, match="not been confirmed"):
        service.refresh_organization(mine.id, http=http, llm=_no_model)
    assert http.calls == []
    assert _rows(mine.id) == [] and service.signals_for_profile(mine.id) == []
    assert service.queue_signals_refresh(mine.id) is None  # nothing is queued for it either


def test_only_hits_naming_the_company_or_its_address_are_kept_and_the_address_is_searched_too(db_session, make_org, keys):
    org = _confirmed(db_session, make_org, "PostHog", "https://www.posthog.com/about")
    http = FakeHttp(_posthog_answers())
    service.refresh_organization(org.id, http=http, llm=_no_model)
    titles = {r["title"] for r in service.signals_for_profile(org.id) if r["kind"] != "sentiment"}
    assert titles == {"PostHog opens its roadmap", "Show HN: a product analytics stack"}  # by name, and by the story's own link; a link inside another post is not enough
    searched = {(kw.get("params") or {}).get("query") for url, kw in http.calls if url.startswith("https://hn.algolia.com/")}
    assert searched == {"PostHog", "posthog.com"}
    assert not any(STAND_IN in (r.subject or "") for r in _rows(org.id))
    assert all(r.subject == "PostHog" for r in _rows(org.id))


def test_a_paper_that_does_not_name_the_company_is_dropped_and_one_that_does_is_kept(db_session, make_org, keys):
    org = _confirmed(db_session, make_org, "PostHog, Inc.", "https://posthog.com")
    answers = _posthog_answers()
    answers["https://api.openalex.org/"]["results"].append(
        {"id": "https://openalex.org/W10", "display_name": "PostHog and product analytics adoption", "publication_date": "2025-05-01",
         "abstract_inverted_index": {"PostHog": [0], "usage": [1]}})
    report = service.refresh_organization(org.id, http=FakeHttp(answers), llm=_no_model)
    papers = [r["title"] for r in service.signals_for_profile(org.id) if r["kind"] == "research"]
    assert papers == ["PostHog and product analytics adoption"] and report["sources"]["open_alex"]["signals"] == 1


def test_the_company_is_found_as_whole_words_only():
    assert src.is_about("PostHog opens its roadmap", "PostHog")
    assert src.is_about("Docs at blog.posthog.com/launch", "PostHog", "posthog.com")
    assert src.is_about("A note", "PostHog", "posthog.com", "https://blog.posthog.com/launch")  # the story's own link is on its domain
    assert not src.is_about("Posthoggery", "PostHog")
    assert not src.is_about("Look at myposthog.com", "PostHog", "posthog.com")
    assert not src.is_about("A note", "PostHog", "posthog.com", "https://notposthog.com/launch")
    assert not src.is_about("Rat model of cardiac arrest", "PostHog", "posthog.com")


def _calcom_answers():
    answers = _posthog_answers()
    launches = [("Show HN: Praxos", "1"), ("Launch HN: Vendo", "2"), ("Show HN: Posting Machine", "3"), ("Show HN: Extract", "4")]
    answers["https://hn.algolia.com/"] = {"hits": [
        *[{"title": t, "story_text": "Book a call: https://cal.com/someone/30min", "url": "https://%s.example/" % i, "objectID": i,
           "created_at": "2026-10-2%sT00:00:00Z" % i} for t, i in launches],
        {"title": "Cal.com is going closed source", "objectID": "10", "created_at": "2026-10-15T08:00:00Z"},
        {"title": "Cal.com is going closed source", "objectID": "11", "created_at": "2026-10-15T09:00:00Z"},
        {"title": "A scheduling write-up", "url": "https://cal.com/blog/scheduling", "objectID": "12", "created_at": "2026-10-14T00:00:00Z"},
    ]}
    return answers


def test_a_link_to_the_company_inside_someone_elses_post_does_not_make_it_about_the_company(db_session, make_org, keys):
    org = _confirmed(db_session, make_org, "Cal.com", "https://cal.com")
    other = _confirmed(db_session, make_org, "Contoso Haulage", "https://contoso.example")
    service.refresh_organization(org.id, http=FakeHttp(_calcom_answers()), llm=_no_model)
    first_ten = [r["title"] for r in service.signals_for_profile(org.id) if r["kind"] != "sentiment"][:10]
    assert sorted(first_ten) == ["A scheduling write-up", "Cal.com is going closed source"]  # no other company's launch, and the same story once
    assert service.signals_for_profile(other.id) == []


def test_the_same_source_title_and_date_is_stored_and_shown_once_even_when_kept_before(db_session, make_org, tenant_ctx, keys):
    org = _confirmed(db_session, make_org, "Cal.com", "https://cal.com")
    day = datetime(2026, 10, 15, 8)
    for url in ("https://news.ycombinator.com/item?id=10", "https://news.ycombinator.com/item?id=11"):  # kept twice by an earlier reading
        _stored(org.id, subject="Cal.com", source="hackernews", source_label="Hacker News", kind="mention",
                title="Cal.com is going closed source", summary="", url=url, published_at=day)
    _stored(org.id, subject="Cal.com", source="hackernews", source_label="Hacker News", kind="mention",
            title="Show HN: Praxos", summary="Book at cal.com/x", url="https://news.ycombinator.com/item?id=1", published_at=day)
    shown = [r["title"] for r in service.signals_for_profile(org.id)]
    assert shown.count("Cal.com is going closed source") == 1  # shown once before anything is cleaned
    service.refresh_organization(org.id, http=FakeHttp(_calcom_answers()), llm=_no_model, force=True)
    stored = [r.title for r in _rows(org.id) if r.kind != "sentiment"]
    assert sorted(stored) == ["A scheduling write-up", "Cal.com is going closed source"], stored


def test_signals_already_kept_under_the_stand_in_or_off_topic_are_removed_for_their_own_organisation_only(db_session, make_org, tenant_ctx, keys):
    mine = _confirmed(db_session, make_org, "PostHog", "https://posthog.com")
    theirs = _confirmed(db_session, make_org, "Contoso Haulage", "https://contoso.example", stand_in="Robin Startup's Workspace")
    for org, stand_in in ((mine, STAND_IN), (theirs, "Robin Startup's Workspace")):
        _stored(org.id, subject=stand_in, source="hackernews", kind="mention", title="Entrepreneurial passion", summary="")
    _stored(mine.id, subject="PostHog", source="open_alex", kind="research", title="Lithium chloride in rats", summary="")
    _stored(mine.id, subject="PostHog", source="hackernews", kind="mention", title="PostHog ships", summary="")
    service.refresh_organization(mine.id, http=FakeHttp(_answers()), llm=_no_model, force=True)
    assert {r.title for r in _rows(mine.id) if r.source in ("hackernews", "open_alex")} == {"PostHog ships"}
    assert [r.title for r in _rows(theirs.id)] == ["Entrepreneurial passion"]  # not theirs to touch
    with tenant_ctx(theirs.id):
        assert service.signals_for_profile(theirs.id) == []  # shown under their own name, which has none
        assert service.signals_for_profile(mine.id) == []  # and never another organisation's


# ----------------------------------------------------------------------------- crawl03: near-duplicates, comparisons, reasoned tags

def test_a_title_that_uses_the_company_as_a_comparison_is_not_about_it():
    domain = "posthog.com"
    for title in ("Show HN: Laminar – Open-Source DataDog + PostHog for LLM Apps",
                  "Show HN: Phospho – Text analytics for LLM apps (Posthog for Prompts)",
                  "Show HN: A session replay tool, an open-source alternative to PostHog",
                  "Show HN: Mixpanel for PostHog users"):
        assert not src.is_about(title, "PostHog", domain), title
    for title in ("Launch HN: PostHog (YC W20) – open-source product analytics", "PostHog opens its roadmap",
                  "Show HN: PostHog – product analytics you host yourself"):
        assert src.is_about(title, "PostHog", domain), title
    # on the company's own address it is still theirs
    assert src.is_about("Show HN: PostHog for Prompts", "PostHog", domain, "https://posthog.com/prompts")


def test_the_same_story_with_its_words_moved_within_a_few_days_is_stored_and_shown_once(db_session, make_org, tenant_ctx, keys):
    org = _confirmed(db_session, make_org, "PostHog", "https://posthog.com")
    answers = _posthog_answers()
    answers["https://hn.algolia.com/"] = {"hits": [
        {"title": "Posthog's New Website", "objectID": "1", "created_at": "2025-09-11T10:00:00Z"},
        {"title": "New Posthog Website", "objectID": "2", "created_at": "2025-09-10T10:00:00Z"},
        {"title": "New Posthog Website", "objectID": "3", "created_at": "2026-03-01T10:00:00Z"},  # months later: another story
    ]}
    service.refresh_organization(org.id, http=FakeHttp(answers), llm=_no_model)
    stored = sorted(r.published_at.date().isoformat() for r in _rows(org.id) if r.kind != "sentiment")
    assert stored == ["2025-09-11", "2026-03-01"], stored
    shown = [r["published_at"].date().isoformat() for r in service.signals_for_profile(org.id) if r["kind"] != "sentiment"]
    assert shown == ["2026-03-01", "2025-09-11"], shown


def test_rows_kept_twice_before_are_shown_once_and_tidied_away(db_session, make_org, tenant_ctx, keys):
    org = _confirmed(db_session, make_org, "PostHog", "https://posthog.com")
    for title, day in (("Posthog's New Website", datetime(2025, 9, 11)), ("New Posthog Website", datetime(2025, 9, 10))):
        _stored(org.id, subject="PostHog", source="hackernews", source_label="Hacker News", kind="mention", title=title, summary="",
                url="https://news.ycombinator.com/item?id=" + title[:3], published_at=day)
    assert len(service.signals_for_profile(org.id)) == 1
    service.refresh_organization(org.id, http=FakeHttp(_posthog_answers()), llm=_no_model, force=True)
    assert len([r for r in _rows(org.id) if "Website" in r.title]) == 1


def test_every_tag_has_a_reason_and_a_signal_with_no_clear_category_has_no_tag():
    # the headline says it: a lawsuit is legal, whatever else the post says
    factor, reason = service.pestle_reason("news", "Firm sued over privacy breach", "")
    assert factor == "legal" and "sued" in reason
    # a launch post is not a legal matter, and a word in the body text does not decide the tag
    assert service.pestle_factor("mention", "Launch HN: PostHog (YC W20)", "We are open source and care about privacy, GDPR and the court of public opinion") is None
    assert service.pestle_factor("mention", "Show HN: Phospho – analytics for LLM apps", "We raised funding at a valuation") is None
    assert service.pestle_factor("mention", "Show HN: a platform for cloud software", "") is None  # a product word is not a factor
    # a record type is its own reason
    assert service.pestle_reason("filing", "x", "") == ("legal", "it is a regulatory filing")
    assert service.pestle_reason("news", "A quiet day", "") == (None, None)


def test_the_launch_post_is_kept_for_posthog_and_carries_no_tag(db_session, make_org, tenant_ctx, keys):
    org = _confirmed(db_session, make_org, "PostHog", "https://posthog.com")
    answers = _posthog_answers()
    answers["https://hn.algolia.com/"] = {"hits": [
        {"title": "Launch HN: PostHog (YC W20)", "story_text": "Open source analytics. Privacy, GDPR, lawsuits, funding.", "objectID": "1", "created_at": "2020-03-01T00:00:00Z"},
        {"title": "Show HN: Laminar – Open-Source DataDog + PostHog for LLM Apps", "objectID": "2", "created_at": "2025-03-01T00:00:00Z"},
        {"title": "Show HN: Phospho – Text analytics for LLM apps (Posthog for Prompts)", "objectID": "3", "created_at": "2025-03-02T00:00:00Z"},
    ]}
    service.refresh_organization(org.id, http=FakeHttp(answers), llm=_no_model)
    mentions = [r for r in service.signals_for_profile(org.id) if r["kind"] == "mention"]
    assert [r["title"] for r in mentions] == ["Launch HN: PostHog (YC W20)"]
    assert mentions[0]["pestle_factor"] is None


# ----------------------------------------------------------------------------- crawl04: common-word names, one event one row, tags that say why
# Stubbed from what QA found with real sign-ups on 25 Oct 2026: linear.app, cal.com and posthog.com.

def _hn(*hits):
    return [{"title": t, "objectID": str(i), "created_at": d, **extra} for i, (t, d, extra) in enumerate(hits, start=100)]


def _sec(*filings):
    return {"hits": {"hits": [
        {"_id": "000%d-%s-000001:doc%d.htm" % (i, d[2:4], i), "_source": {
            "display_names": ["%s  (CIK 000%d)" % (who, i)], "file_description": "Report", "file_date": d, "form": form,
            "root_form": form, "ciks": ["000%d" % (1000 + i)]}}
        for i, (who, form, d) in enumerate(filings, start=1)]}}


def _with_facts(org_id, **facts):
    from app.models.organization import Organization

    org = db.session.get(Organization, org_id)
    settings = dict(org.settings or {})
    profile = dict(settings.get("company_profile") or {})
    profile.update({k: {"value": v} for k, v in facts.items()})
    settings["company_profile"] = profile
    org.settings = settings
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(org, "settings")
    db.session.commit()


def linear_answers():
    answers = _posthog_answers()
    answers["https://efts.sec.gov/"] = _sec(
        ("LINEAR TECHNOLOGY CORP", "10-Q", "2001-05-10"), ("LINEAR TECHNOLOGY CORP", "10-Q", "2001-08-09"),
        ("LINEAR TECHNOLOGY CORP", "10-K", "2002-09-20"), ("LINEAR TECHNOLOGY CORP", "10-Q", "2003-02-07"),
        ("MICRO LINEAR CORP", "10-K", "2010-03-30"), ("MICRO LINEAR CORP", "10-K", "2011-03-30"))
    answers["https://hn.algolia.com/"] = {"hits": _hn(
        ("Linear algebra for programmers: a visual guide", "2026-04-02T00:00:00Z", {}),
        ("Linear A is still undeciphered after a century", "2026-04-03T00:00:00Z", {}),
        ("Integer linear programming for shift scheduling", "2026-04-04T00:00:00Z", {}),
        ("Linear raises $35M Series B led by Accel", "2026-03-10T00:00:00Z", {}),
        ("Show HN: Linear – issue tracking for software teams", "2026-02-01T00:00:00Z", {}),
        ("Linear changelog: cycles, projects and a new roadmap view", "2026-04-01T00:00:00Z", {}),
        ("Linear's pricing change draws questions from small teams", "2026-01-15T00:00:00Z", {}))}
    return answers


def calcom_answers():
    answers = _posthog_answers()
    answers["https://hn.algolia.com/"] = {"hits": _hn(
        ("Show HN: CalOverlap – Group Scheduling from Multiple Calendly/Cal.com Links", "2026-03-20T00:00:00Z",
         {"url": "https://github.com/someone/caloverlap"}),
        ("Cal.com Calendar Scaffold: T3 Stack and Supabase", "2026-03-22T00:00:00Z", {"url": "https://github.com/someone/scaffold"}),
        ("Cal.com: Open Scheduling Infrastructure", "2025-11-02T00:00:00Z", {}),
        ("Cal.com: Open Scheduling Infrastructure", "2026-02-20T00:00:00Z", {}),  # reposted outside the three days
        ("Cal.com is going closed source", "2026-04-15T08:00:00Z", {}),
        ("Cal.com goes closed source, citing AI scrapers", "2026-04-15T20:00:00Z", {}),
        ("Why Cal.com is closing its source code", "2026-04-16T09:00:00Z", {}),
        ("Cal.com moves to closed source: what changes for self-hosters", "2026-04-17T07:00:00Z", {}))}
    return answers


def posthog_answers():
    answers = _posthog_answers()
    answers["https://hn.algolia.com/"] = {"hits": _hn(
        ("PostHog opens its roadmap", "2026-01-10T00:00:00Z", {}),
        ("Launch HN: PostHog (YC W20)", "2020-03-01T00:00:00Z", {}),
        ("Posthog's New Website", "2025-09-11T10:00:00Z", {}), ("New Posthog Website", "2025-09-10T10:00:00Z", {}),
        ("PostHog raises $70M Series E", "2025-06-01T00:00:00Z", {}),
        ("PostHog moves its enterprise features to a new licence", "2025-08-01T00:00:00Z", {}),
        ("Show HN: Laminar – Open-Source DataDog + PostHog for LLM Apps", "2025-03-01T00:00:00Z", {}),
        ("Show HN: Phospho – Text analytics for LLM apps (Posthog for Prompts)", "2025-03-02T00:00:00Z", {}))}
    return answers


def _read(db_session, make_org, name, site, answers, **facts):
    org = _confirmed(db_session, make_org, name, site)
    _with_facts(org.id, **facts)
    service.refresh_organization(org.id, http=FakeHttp(answers), llm=_no_model)
    return org


def _shown(org):
    return [r for r in service.signals_for_profile(org.id) if r["kind"] != "sentiment"]


def test_linear_keeps_its_own_news_and_not_other_companies_filings_or_linear_algebra(db_session, make_org, tenant_ctx, keys):
    org = _read(db_session, make_org, "Linear", "https://linear.app", linear_answers(),
                tagline="Plan and build products: issue tracking and project management for software teams")
    titles = sorted(r["title"] for r in _shown(org))
    assert titles == sorted([
        "Linear raises $35M Series B led by Accel", "Show HN: Linear – issue tracking for software teams",
        "Linear changelog: cycles, projects and a new roadmap view", "Linear's pricing change draws questions from small teams"]), titles
    assert not [r for r in _rows(org.id) if r.source == "sec_edgar"], "LINEAR TECHNOLOGY CORP and MICRO LINEAR CORP are not Linear"


def test_filings_already_kept_for_other_companies_with_a_shared_word_are_tidied_away(db_session, make_org, tenant_ctx, keys):
    org = _confirmed(db_session, make_org, "Linear", "https://linear.app")
    for i in range(14):  # kept 14 times under 3 titles, as QA found
        who = ("LINEAR TECHNOLOGY CORP", "MICRO LINEAR CORP", "LINEAR TECHNOLOGY CORP")[i % 3]
        _stored(org.id, subject="Linear", source="sec_edgar", source_label="SEC EDGAR", kind="filing", title="%s: 10-K filing" % who,
                summary="", url="https://www.sec.gov/x/%d" % i, published_at=datetime(2001 + i, 5, 1))
    _stored(org.id, subject="Linear", source="hackernews", kind="mention", title="Linear algebra for programmers", summary="",
            published_at=datetime(2026, 4, 2))
    _stored(org.id, subject="Linear", source="hackernews", kind="mention", title="Linear raises $35M Series B", summary="",
            published_at=datetime(2026, 3, 10))
    service.refresh_organization(org.id, http=FakeHttp(linear_answers()), llm=_no_model, force=True)
    assert [r.title for r in _rows(org.id) if r.source == "sec_edgar"] == []
    assert "Linear algebra for programmers" not in [r.title for r in _rows(org.id)]
    assert "Linear raises $35M Series B" in [r.title for r in _rows(org.id)]


def test_the_confirmed_legal_name_has_to_match_the_filer():
    assert src.legal_name_matches("Northwind Freight Inc. (NWF) (CIK 0001234567)", "Northwind Freight")
    assert src.legal_name_matches("PostHog, Inc.", "PostHog")
    assert not src.legal_name_matches("LINEAR TECHNOLOGY CORP", "Linear")
    assert not src.legal_name_matches("MICRO LINEAR CORP", "Linear")


def test_calcom_drops_other_peoples_posts_shows_a_repost_once_and_one_event_as_one_row_with_its_count(db_session, make_org, tenant_ctx, keys):
    org = _read(db_session, make_org, "Cal.com", "https://cal.com", calcom_answers(), tagline="Open Scheduling Infrastructure")
    shown = _shown(org)
    titles = [r["title"] for r in shown]
    assert not any("CalOverlap" in t or "Scaffold" in t for t in titles), titles  # QA-004
    assert titles.count("Cal.com: Open Scheduling Infrastructure") == 1  # QA-005, reposted months apart
    story = [r for r in shown if "closed source" in r["title"] or "source code" in r["title"]]
    assert len(story) == 1 and story[0]["reports"] == 4, [(r["title"], r["reports"]) for r in shown]  # QA-005
    assert len(shown) == 2, titles
    assert len([r for r in _rows(org.id) if r.title == "Cal.com: Open Scheduling Infrastructure"]) == 1


def test_posthog_keeps_its_own_posts_and_not_the_comparisons(db_session, make_org, tenant_ctx, keys):
    org = _read(db_session, make_org, "PostHog", "https://posthog.com", posthog_answers(), tagline="Product analytics")
    titles = [r["title"] for r in _shown(org)]
    assert "Launch HN: PostHog (YC W20)" in titles and "PostHog opens its roadmap" in titles
    assert not any("Laminar" in t or "Phospho" in t for t in titles), titles
    assert len([t for t in titles if "Website" in t]) == 1


def test_tags_for_the_three_companies_are_mostly_matched_and_each_says_why(db_session, make_org, tenant_ctx, keys):
    rows = []
    for name, site, answers, facts in (("Linear", "https://linear.app", linear_answers(), {"tagline": "Issue tracking for software teams"}),
                                       ("Cal.com", "https://cal.com", calcom_answers(), {"tagline": "Open Scheduling Infrastructure"}),
                                       ("PostHog", "https://posthog.com", posthog_answers(), {"tagline": "Product analytics"})):
        rows += _shown(_read(db_session, make_org, name, site, answers, **facts))
    unmatched = [r for r in rows if not r["pestle_factor"]]
    assert len(unmatched) * 2 < len(rows), ([r["title"] for r in unmatched], len(rows))
    for r in rows:
        assert (r["pestle_reason"] is None) == (r["pestle_factor"] is None), r
    closed = next(r for r in rows if r["title"].startswith("Cal.com") and "closed source" in r["title"])
    assert closed["pestle_factor"] == "technological" and "closed source" in closed["pestle_reason"]


def test_a_licence_a_lawsuit_a_regulation_and_open_versus_closed_source_each_get_a_tag_with_its_reason():
    for title, factor, word in (("PostHog moves its enterprise features to a new licence", "legal", "licence"),
                                ("Cal.com sued by a former contractor", "legal", "sued"),
                                ("New EU regulation lands on scheduling apps", "legal", "regulation"),
                                ("Cal.com is going closed source", "technological", "closed source"),
                                ("Why we stay open source", "technological", "open source")):
        got, reason = service.pestle_reason("mention", title)
        assert got == factor and word in reason, (title, got, reason)
    assert service.pestle_reason("mention", "Linear A is still undeciphered after a century") == (None, None)


# ----------------------------------------------------------------------------- polish01: QA-023 leftovers, QA-007 follow-up

def test_a_name_inside_a_hyphenated_word_is_not_the_company():
    assert not src.names_company("Show HN: I built a non-linear UI for ChatGPT", "Linear")
    assert not src.names_company("Linear-algebra flashcards", "Linear")
    assert src.names_company("Linear raises $35M", "Linear")
    assert src.names_company("Show HN: Linear \u2013 issue tracking", "Linear")
    assert src.names_company("Launch HN: PostHog (YC W20) - open source analytics", "PostHog")


def test_someone_elses_tool_that_connects_to_the_company_does_not_count():
    domain = "linear.app"
    other = "https://synclinear.com/"
    assert not src.counts_as_news("Synclinear.com to Sync Linear.app with GitHub", "Linear", domain, other)
    assert not src.counts_as_news("Show HN: I built a non-linear UI for ChatGPT", "Linear", domain,
                                  "https://news.ycombinator.com/item?id=5")
    assert not src.counts_as_news("Show HN: Sync Jira tickets with Linear", "Linear", domain, "https://syncer.example/")
    assert src.counts_as_news("Show HN: Linear \u2013 issue tracking for software teams", "Linear", domain, "https://linear.app/")
    assert src.counts_as_news("Linear raises $35M Series B led by Accel", "Linear", domain, "https://news.ycombinator.com/item?id=6")


def test_an_unsupported_stored_currency_falls_back_to_gbp(app, db_session, make_org):
    from app.services.application_cost_accessor import get_reporting_currency
    from config import CurrencyConfig

    org = make_org("currency")
    org.reporting_currency = "usd"
    db_session.commit()
    assert get_reporting_currency(org.id) == CurrencyConfig.get_org_currency_code(organization_id=org.id) == "USD"
    org.reporting_currency = "ZZZ"
    db_session.commit()
    assert get_reporting_currency(org.id) == "GBP"
    assert CurrencyConfig.get_org_currency_code(organization_id=org.id) == "GBP"
