"""Public company signals: each source parsed from a recorded answer, stored once with its source, isolated per organisation."""

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

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
    [h] = run(src.hackernews, "https://hn.algolia.com/", load("hackernews"))
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
        "rss", "yahoo_finance", "alpha_vantage"}


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
    org.name = "%s %s" % (name, uuid.uuid4().hex[:6]) if name != COMPANY else name
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
    assert state["skipped"]["NewsAPI"] == "needs NEWSAPI_KEY to be set" and state["refreshed_at"] is not None


def test_a_failing_source_is_named_and_the_rest_still_arrive(db_session, make_org, keys):
    org = _org(db_session, make_org)
    answers = _answers()
    answers["https://efts.sec.gov/"] = src.SourceFailed("answered 503")
    report = service.refresh_organization(org.id, http=FakeHttp(answers), llm=_model)
    assert report["sources"]["sec_edgar"]["failed"] == "SEC EDGAR answered 503"
    assert report["sources"]["uspto"]["signals"] == 1
    assert "SEC EDGAR answered 503" in service.refresh_state(_fresh(org))["failed"]


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
