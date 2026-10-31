"""The public sources OperateIQ reads, ported to Python.

Ported from operateiq-backend/src/clients/connectors/ (registry.rs, mod.rs, news_api.rs, sec_edgar.rs,
uspto.rs, open_alex.rs, reddit.rs, hackernews.rs, bluesky.rs, mastodon.rs, rss.rs, yahoo_finance.rs,
alpha_vantage.rs) and the Brave web search the sentiment service uses for news, at OperateIQ commit
cbc6a9e. Each source keeps OperateIQ's endpoint, its source type, its pause between calls and its retry
count. Each one returns the same small record (``Item``) so the service stores them alike.

Free tiers only. A source that needs a key is skipped, and says so, until the key is set in the
environment (``NEWSAPI_KEY``, ``ALPHAVANTAGE_API_KEY``, ``BRAVE_SEARCH_API_KEY``, ``LLMLAYER_API_KEY``);
the keys are only ever read from the environment and sent as request headers or parameters, never stored or logged.

Differences from the Rust, each deliberate: SEC EDGAR full-text search is a GET (a POST body is not what
the endpoint accepts); the SEC filing link is the archive document when the hit names its accession and
file; a hit that does not name the company, or a patent that does not name it as assignee, is dropped
rather than attributed to it.
"""

from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote, urlparse

import requests

SOURCE_COMMIT = "cbc6a9e"
USER_AGENT = "Entelim/1.0 (public company signals; contact via the organisation's workspace)"
SEC_USER_AGENT = "Entelim/1.0 (public filings search; support@entelim.com)"

# OperateIQ's SourceType: Academic, Regulatory, Social, News, Market, Patent.
KIND_BY_TYPE = {"news": "news", "regulatory": "filing", "market": "financial", "patent": "patent",
                "academic": "research", "social": "mention"}

# Default page limit for paginated sources (OperateIQ default).
DEFAULT_PAGE_LIMIT = 3


class SourceFailed(RuntimeError):
    """A source answered with an error or could not be reached; the message says so plainly."""


@dataclass
class Item:
    """One thing a source returned (OperateIQ's ConnectorData)."""
    title: str
    summary: str = ""
    url: Optional[str] = None
    published: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class Http:
    """Rate-limited, retrying HTTP, as OperateIQ's RateLimitConfig describes (a pause per call, up to three retries)."""

    def __init__(self, session: Optional[requests.Session] = None, sleep: Callable[[float], None] = time.sleep):
        self.session = session or requests.Session()
        self.sleep = sleep

    def _call(self, method: str, url: str, pause: float, retries: int, **kw) -> requests.Response:
        kw.setdefault("timeout", 30)
        last = None
        for attempt in range(retries + 1):
            if pause:
                self.sleep(pause)
            try:
                response = self.session.request(method, url, **kw)
            except requests.RequestException as exc:
                last = SourceFailed("could not be reached (%s)" % type(exc).__name__)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last = SourceFailed("answered %s" % response.status_code)
                self.sleep(pause * (attempt + 2) if pause else 1)
                continue
            if response.status_code >= 400:
                raise SourceFailed("answered %s" % response.status_code)
            return response
        raise last or SourceFailed("did not answer")

    def json(self, url: str, *, pause: float = 1.0, retries: int = 3, **kw) -> Any:
        response = self._call("GET", url, pause, retries, **kw)
        try:
            return response.json()
        except ValueError:
            raise SourceFailed("did not answer with data this reader understands")

    def text(self, url: str, *, pause: float = 1.0, retries: int = 3, **kw) -> str:
        return self._call("GET", url, pause, retries, **kw).text


def _utc(stamp: float) -> datetime:
    return datetime.fromtimestamp(stamp, timezone.utc).replace(tzinfo=None)


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def names_company(text: str, company: str) -> bool:
    """True when the company's name stands in the text as whole words ("Go" is not found in "Google", and
    "Linear" is not found in "non-linear", where a hyphen joins it to another word)."""
    wanted = _norm(company)
    if not wanted:
        return False
    for hit in re.finditer(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(wanted).replace(r"\ ", r"[^a-z0-9]+"), (text or "").lower()):
        before, after = (text or "").lower()[:hit.start()], (text or "").lower()[hit.end():]
        if before.endswith("-") and before[:-1][-1:].isalnum() or after.startswith("-") and after[1:2].isalnum():
            continue  # joined to another word by a hyphen: "non-linear", "linear-algebra"
        return True
    return False


_LEGAL_ENDINGS = {"inc", "incorporated", "ltd", "limited", "llc", "llp", "plc", "corp", "corporation", "gmbh", "ag", "bv", "sa", "co"}


def name_variants(company: str) -> List[str]:
    """The name as confirmed, and without a trailing legal ending ("PostHog, Inc." is also "PostHog")."""
    words = _norm(company).split()
    variants = [" ".join(words)] if words else []
    while len(words) > 1 and words[-1] in _LEGAL_ENDINGS:
        words = words[:-1]
        variants.append(" ".join(words))
    return variants


def legal_name_matches(filer: str, company: str) -> bool:
    """True when a filer or assignee name is the company itself: the same name once legal endings (Inc., Corp, Ltd)
    and bracketed tickers or numbers are set aside. "LINEAR TECHNOLOGY CORP" is not "Linear"."""
    filer = re.sub(r"\([^)]*\)", " ", filer or "")
    return bool(set(name_variants(filer)) & set(name_variants(company)))


def names_domain(text: str, domain: Optional[str]) -> bool:
    """True when the company's web address (posthog.com, also www. or a subdomain of it) is written in the text."""
    host = (domain or "").strip().lower()
    host = host[4:] if host.startswith("www.") else host
    return bool(host) and re.search(r"(?<![a-z0-9-])%s(?![a-z0-9-])" % re.escape(host), (text or "").lower()) is not None


def own_link(url: Optional[str], details: Optional[Dict[str, Any]]) -> Optional[str]:
    """The page a hit is about: the story's own link where the source only lists it (Hacker News), else its address."""
    return (details or {}).get("story_url") or url


def link_is_on(link: Optional[str], domain: Optional[str]) -> bool:
    """True when the link's host is the company's web address or a subdomain of it."""
    host = (domain or "").strip().lower()
    host = host[4:] if host.startswith("www.") else host
    if not host or not link:
        return False
    found = (urlparse(link if "://" in link else "https://" + link).hostname or "").lower()
    found = found[4:] if found.startswith("www.") else found
    return found == host or found.endswith("." + host)


_LAUNCH_POST = re.compile(r"^\s*(show|launch|ask|tell)\s+hn\s*:", re.I)
_COMPARING_BEFORE = re.compile(r"(\+|&|/|\bfor|\bvs\.?|\bversus|\blike|\bmeets|\binstead of|\b(alternatives?|competitors?|rivals?|replacement|clone) (to|of|for))\s*$", re.I)


def _named_as_subject(title: str, variant: str) -> bool:
    """True when the name stands in the title as the thing the title is about, not as a comparison.

    A comparison is the name after "for", "+", "&", "vs", "like", "instead of" or "alternative to" ("DataDog +
    PostHog for LLM Apps"), and, in a Show HN or Launch HN post for another product, the name followed by "for"
    ("Posthog for Prompts"). "Launch HN: PostHog (YC W20)" names its own subject and is not one."""
    for hit in re.finditer(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(variant).replace(r"\ ", r"[^a-z0-9]+"), title, re.I):
        before, after = title[:hit.start()].rstrip(), title[hit.end():]
        if _COMPARING_BEFORE.search(before):
            continue
        if _LAUNCH_POST.match(title) and re.match(r"^\s*\)?\s*for\s+\w", after, re.I):
            continue
        return True
    return False


def uses_name_as_comparison(title: str, company: str) -> bool:
    """True when the company's name is in the title only as a comparison to some other product."""
    variants = name_variants(company)
    present = [v for v in variants if names_company(title, v)]
    return bool(present) and not any(_named_as_subject(title, v) for v in present)


_COMPANY_WORDS = {
    "company", "startup", "founder", "founders", "ceo", "yc", "raises", "raised", "funding", "series", "valuation", "ipo",
    "launches", "launched", "ships", "shipped", "releases", "released", "announces", "announced", "opens", "opened",
    "acquires", "acquired", "acquisition", "hiring", "hires", "layoffs", "pricing", "roadmap", "changelog", "license",
    "licence", "licensing", "open source", "closed source", "source code", "customers", "users", "product", "products",
    "platform", "api", "sdk", "saas", "outage", "breach", "profile", "website",
}
_PREFIX = r"(?:(?:show|launch|ask|tell)\s+hn\s*:\s*)?"


def has_company_context(title: str, company: str, words=()) -> bool:
    """A name that is also an ordinary word needs company around it before a headline is about the company: the
    headline opens with the name as its subject ("Linear: ...", "Linear's ...", "Launch HN: Linear"), or names what a
    company does (raises, ships, pricing, open source) or one of its product words from the confirmed facts.
    "Linear algebra", "Linear A" and "integer linear programming" have none of these."""
    text = " ".join(re.findall(r"[a-z0-9]+", (title or "").lower()))
    for v in name_variants(company):
        shape = r"[^a-z0-9]*".join(re.escape(w) for w in v.split())
        if re.match(r"^\W*%s%s(?:['\u2019]s\b|\s*[:\u2013\u2014|-](?:\s|$))" % (_PREFIX, shape), title or "", re.I):
            return True
        if re.match(r"^\W*(?:show|launch|ask|tell)\s+hn\s*:\s*%s(?=\s*(?:\(|[:\u2013\u2014|-]|$))" % shape, title or "", re.I):
            return True
    padded = " %s " % text
    return any((" %s " % w) in padded for w in set(_COMPANY_WORDS) | set(words or ()))


def is_single_name(company: str) -> bool:
    """True when the name is one word (Linear, Cal.com, PostHog, Brand): other people's headlines reuse it, so it is
    never enough alone. A name of two words or more (Northwind Freight) says which company it is."""
    words = [w for w in re.split(r"[\s,]+", (company or "").strip()) if w]
    while len(words) > 1 and words[-1].lower().strip(".") in _LEGAL_ENDINGS:
        words.pop()
    return len(words) == 1


def counts_as_news(title: str, company: str, domain: Optional[str] = None, link: Optional[str] = None, words=()) -> bool:
    """What a free-text search may keep: the company's web address is in the title or the story's own link is on it,
    or the name is in the title as the company and not as a comparison, with company context around it."""
    if link_is_on(link, domain):
        return True
    if is_integration_target(title, company, domain, link):
        return False
    host = (domain or "").strip().lower()
    host = host[4:] if host.startswith("www.") else host
    if names_domain(title, host) and _named_as_subject(title, host) and _norm(host) not in name_variants(company):
        return True  # its web address, written in the title, when that address is not also the name others reuse (cal.com)
    if not is_single_name(company):  # a name of two words or more says which company it is
        return is_about(title, company)
    return is_about(title, company) and has_company_context(title, company, words) \
        and not is_integration_target(title, company, domain, link)


_INTEGRATION_TARGET = re.compile(r"\b(?:to|with|for|into|from|on)\s+(?:sync|integrat\w*)?\s*", re.I)


def is_integration_target(title: str, company: str, domain: Optional[str], link: Optional[str]) -> bool:
    """True for a launch post by someone else (its own address is not the company's) that names the company as the thing
    it connects to: "Synclinear.com to Sync Linear.app with GitHub", "Show HN: Sync X with Linear"."""
    if link_is_on(link, domain) or not link:
        return False
    text = " ".join(re.findall(r"[a-z0-9.]+", (title or "").lower()))
    for v in name_variants(company):
        shape = r"\s+".join(re.escape(w) for w in v.split())
        if re.search(r"\b(?:sync|syncs|syncing|connect|connects|import|imports|export|exports|integrat\w*|plugin|extension|bridge|bot)\b.*\b%s\b" % shape, text) \
                or re.search(r"\b(?:to|with|into|for)\s+(?:sync\s+)?%s(?:\.\w+)?\b" % shape, text) and re.search(r"\bsync\w*\b", text):
            return True
    return False


def is_about(title: str, company: str, domain: Optional[str] = None, link: Optional[str] = None) -> bool:
    """A hit is kept only when the company's name or web address is in its title, or the story's own link is on
    the company's domain. A link to the company inside someone else's post is not enough, and neither is a title
    that uses the name only as a comparison ("Open-Source DataDog + PostHog for LLM Apps")."""
    if link_is_on(link, domain) or names_domain(title, domain):
        return True
    return any(names_company(title, v) for v in name_variants(company)) and not uses_name_as_comparison(title, company)


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


# --------------------------------------------------------------------------- news (NewsAPI, Brave)

def news_api(http: Http, query: str, ctx: Dict[str, Any]) -> List[Item]:
    data = http.json("https://newsapi.org/v2/everything", pause=0.0,
                     params={"q": query, "language": "en", "pageSize": 20, "sortBy": "relevancy"},
                     headers={"X-Api-Key": os.environ["NEWSAPI_KEY"]})
    if data.get("status") != "ok":
        raise SourceFailed("refused the request (%s)" % (data.get("code") or data.get("message") or "error"))
    return [Item(title=a.get("title") or "", summary=a.get("description") or a.get("content") or "", url=a.get("url"),
                 published=a.get("publishedAt"), details={"publisher": (a.get("source") or {}).get("name")})
            for a in data.get("articles") or [] if a.get("title") and a.get("url")]


def _brave_keys() -> List[str]:
    """Return the list of Brave API keys from BRAVE_SEARCH_API_KEYS (comma-separated), falling back to BRAVE_SEARCH_API_KEY."""
    raw = os.environ.get("BRAVE_SEARCH_API_KEYS") or os.environ.get("BRAVE_SEARCH_API_KEY") or ""
    return [k.strip() for k in raw.split(",") if k.strip()]


def brave_search(http: Http, query: str, ctx: Dict[str, Any]) -> List[Item]:
    keys = _brave_keys()
    if not keys:
        raise SourceFailed("needs BRAVE_SEARCH_API_KEY or BRAVE_SEARCH_API_KEYS to be set")
    last_error = None
    for key in keys:
        try:
            data = http.json("https://api.search.brave.com/res/v1/web/search", pause=1.2,
                             params={"q": query, "count": 10},
                             headers={"Accept": "application/json", "X-Subscription-Token": key})
            return [Item(title=r.get("title") or "", summary=_strip_html(r.get("description")), url=r.get("url"),
                         published=r.get("page_age"))
                    for r in (data.get("web") or {}).get("results") or [] if r.get("title") and r.get("url")]
        except SourceFailed as exc:
            last_error = exc
            continue
    raise last_error or SourceFailed("all Brave keys failed")


# --------------------------------------------------------------------------- filings and patents

def sec_edgar(http: Http, query: str, ctx: Dict[str, Any]) -> List[Item]:
    data = http.json("https://efts.sec.gov/LATEST/search-index", pause=2.0,
                     params={"q": '"%s"' % query, "forms": "10-K,10-Q,8-K", "from": 0, "size": 20},
                     headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"})
    items = []
    for hit in (data.get("hits") or {}).get("hits") or []:
        src = hit.get("_source") or {}
        names = src.get("display_names") or []
        if not any(legal_name_matches(n, query) for n in names):  # a filer that only shares a word with the name is another company
            continue
        form = src.get("form") or src.get("root_form") or "filing"
        ident, ciks = hit.get("_id") or "", src.get("ciks") or []
        accession, _, filename = ident.partition(":")
        if accession and filename and ciks:
            link = "https://www.sec.gov/Archives/edgar/data/%d/%s/%s" % (int(ciks[0]), accession.replace("-", ""), filename)
        else:
            link = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=%s" % (ciks[0] if ciks else quote(query))
        items.append(Item(title="%s: %s filing" % (names[0], form),
                          summary=src.get("file_description") or "%s regulatory filing" % form, url=link,
                          published=src.get("file_date"), details={"form_type": form, "ciks": ciks}))
    return items


def uspto(http: Http, query: str, ctx: Dict[str, Any]) -> List[Item]:
    data = http.json("https://developer.uspto.gov/ibd-api/v1/patent/application", pause=1.0,
                     params={"searchText": query, "rows": 25, "start": 0}, headers={"Accept": "application/json"})
    items = []
    for p in data.get("results") or []:
        assignees = p.get("assigneeEntityNameArrayText") or []
        if not any(names_company(a, query) for a in assignees):
            continue
        number = p.get("patentNumber")
        items.append(Item(
            title=p.get("inventionTitle") or "", summary=((p.get("abstractText") or [""])[0]),
            url="https://patents.google.com/patent/US%s" % number if number else None,
            published=p.get("grantDate") or p.get("filingDate"),
            details={"patent_number": number, "assignee": assignees, "inventors": p.get("inventorNameArrayText"),
                     "filing_date": p.get("filingDate"), "grant_date": p.get("grantDate")}))
    return [i for i in items if i.title]


# --------------------------------------------------------------------------- research

def _abstract(inverted: Optional[Dict[str, List[int]]]) -> str:
    """OpenAlex stores an abstract as word -> positions; put the words back in order."""
    placed = [(pos, word) for word, positions in (inverted or {}).items() for pos in positions]
    return " ".join(word for _pos, word in sorted(placed))


def open_alex(http: Http, query: str, ctx: Dict[str, Any]) -> List[Item]:
    data = http.json("https://api.openalex.org/works", pause=1.0,
                     params={"search": query, "page": 1, "per-page": 10, "sort": "cited_by_count:desc"})
    items = []
    for w in data.get("results") or []:
        title = w.get("display_name") or w.get("title")
        if not title:
            continue
        source = (((w.get("primary_location") or {}).get("source")) or {}).get("display_name")
        items.append(Item(title=title, summary=_abstract(w.get("abstract_inverted_index")), url=w.get("doi") or w.get("id"),
                          published=w.get("publication_date"),
                          details={"doi": w.get("doi"), "cited_by_count": w.get("cited_by_count"), "journal": source}))
    return items


# --------------------------------------------------------------------------- public conversation

def reddit(http: Http, query: str, ctx: Dict[str, Any]) -> List[Item]:
    """Reddit search with pagination, per-subreddit search, and sort order.
    Pagination: up to DEFAULT_PAGE_LIMIT pages using the 'after' token.
    Subreddit: from ctx['subreddit'] (default 'all').
    Sort: from ctx['reddit_sort'] (default 'relevance').
    """
    subreddit = ctx.get("subreddit", "all")
    sort = ctx.get("reddit_sort", "relevance")
    page_limit = ctx.get("page_limit", DEFAULT_PAGE_LIMIT)

    items: List[Item] = []
    after = None
    for _ in range(page_limit):
        params = {"q": query, "sort": sort, "limit": 50, "restrict_sr": 1}
        if after:
            params["after"] = after
        data = http.json("https://www.reddit.com/r/%s/search.json" % subreddit, pause=2.0,
                         params=params, headers={"User-Agent": USER_AGENT})
        for child in ((data.get("data") or {}).get("children")) or []:
            post = child.get("data") or {}
            if not post.get("title") or not post.get("permalink"):
                continue
            stamp = post.get("created_utc")
            items.append(Item(title=post["title"], summary=post.get("selftext") or "",
                              url="https://reddit.com%s" % post["permalink"],
                              published=_utc(stamp).isoformat() if stamp else None,
                              details={"subreddit": post.get("subreddit"), "score": post.get("score"),
                                       "comments": post.get("num_comments")}))
        after = (data.get("data") or {}).get("after")
        if not after:
            break
    return items


def hackernews(http: Http, query: str, ctx: Dict[str, Any]) -> List[Item]:
    """Hacker News search with pagination, top stories, and Ask HN.
    Mode: from ctx['hn_mode'] ('search' default, 'top', 'ask').
    Pagination: up to DEFAULT_PAGE_LIMIT pages for search modes.
    """
    mode = ctx.get("hn_mode", "search")
    page_limit = ctx.get("page_limit", DEFAULT_PAGE_LIMIT)

    items: List[Item] = []

    if mode == "top":
        # Top stories: fetch IDs then details
        data = http.json("https://hacker-news.firebaseio.com/v0/topstories.json", pause=0.5)
        story_ids = data[:100]  # limit to first 100
        for story_id in story_ids[:page_limit * 30]:  # approximate per-page
            try:
                item_data = http.json("https://hacker-news.firebaseio.com/v0/item/%d.json" % story_id, pause=0.1)
                title = item_data.get("title")
                if not title:
                    continue
                text = item_data.get("text") or title
                url = item_data.get("url") or "https://news.ycombinator.com/item?id=%d" % story_id
                stamp = item_data.get("time")
                items.append(Item(title=title, summary=text,
                                  url=url, published=_utc(stamp).isoformat() if stamp else None,
                                  details={"points": item_data.get("score"), "comments": item_data.get("descendants"),
                                           "story_url": item_data.get("url")}))
            except SourceFailed:
                continue
        return items

    if mode == "ask":
        # Ask HN search
        for page in range(page_limit):
            data = http.json("https://hn.algolia.com/api/v1/search", pause=0.5,
                             params={"query": query, "tags": "ask_hn", "page": page})
            for hit in data.get("hits") or []:
                title, ident = hit.get("title"), hit.get("objectID")
                if not title or not ident:
                    continue
                items.append(Item(title=title, summary=hit.get("story_text") or "",
                                  url="https://news.ycombinator.com/item?id=%s" % ident, published=hit.get("created_at"),
                                  details={"points": hit.get("points"), "comments": hit.get("num_comments"),
                                           "story_url": hit.get("url") or None, "type": "ask_hn"}))
            if not data.get("hits"):
                break
        return items

    # Default: search stories with pagination
    for page in range(page_limit):
        data = http.json("https://hn.algolia.com/api/v1/search", pause=0.5,
                         params={"query": query, "tags": "story", "page": page})
        hits = data.get("hits") or []
        if not hits:
            break
        for hit in hits:
            title, ident = hit.get("title"), hit.get("objectID")
            if not title or not ident:
                continue
            items.append(Item(title=title, summary=hit.get("story_text") or "",
                              url="https://news.ycombinator.com/item?id=%s" % ident, published=hit.get("created_at"),
                              details={"points": hit.get("points"), "comments": hit.get("num_comments"),
                                       "story_url": hit.get("url") or None}))
    return items


def bluesky(http: Http, query: str, ctx: Dict[str, Any]) -> List[Item]:
    """Bluesky search and author feed.
    Mode: from ctx['bluesky_mode'] ('search' default, 'author').
    Author: from ctx['bluesky_author'] (handle).
    """
    mode = ctx.get("bluesky_mode", "search")

    if mode == "author":
        handle = ctx.get("bluesky_author")
        if not handle:
            return []
        data = http.json("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed", pause=0.5,
                         params={"actor": handle, "limit": 30})
        items = []
        for feed_item in data.get("feed") or []:
            post = feed_item.get("post") or {}
            text = (post.get("record") or {}).get("text") or ""
            author_handle = (post.get("author") or {}).get("handle")
            uri = post.get("uri") or ""
            if not text or not author_handle or not uri:
                continue
            items.append(Item(title="@%s: %s" % (author_handle, text[:100]), summary=text,
                              url="https://bsky.app/profile/%s/post/%s" % (author_handle, uri.rsplit("/", 1)[-1]),
                              published=(post.get("record") or {}).get("createdAt")))
        return items

    # Default: search posts
    data = http.json("https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts", pause=0.5,
                     params={"q": query, "limit": 25})
    items = []
    for post in data.get("posts") or []:
        text = (post.get("record") or {}).get("text") or ""
        handle, uri = (post.get("author") or {}).get("handle"), post.get("uri") or ""
        if not text or not handle or not uri:
            continue
        items.append(Item(title="@%s: %s" % (handle, text[:100]), summary=text,
                          url="https://bsky.app/profile/%s/post/%s" % (handle, uri.rsplit("/", 1)[-1]),
                          published=(post.get("record") or {}).get("createdAt")))
    return items


def mastodon(http: Http, query: str, ctx: Dict[str, Any]) -> List[Item]:
    """Mastodon search, public timeline, and configurable instance.
    Mode: from ctx['mastodon_mode'] ('search' default, 'timeline').
    Instance: from ctx['mastodon_instance'] (default 'https://mastodon.social').
    """
    mode = ctx.get("mastodon_mode", "search")
    instance = ctx.get("mastodon_instance", "https://mastodon.social").rstrip("/")

    if mode == "timeline":
        data = http.json("%s/api/v1/timelines/public" % instance, pause=0.5,
                         params={"limit": 20})
        items = []
        for status in data or []:
            text = _strip_html(status.get("content"))
            if not text or not status.get("url"):
                continue
            items.append(Item(title="@%s: %s" % ((status.get("account") or {}).get("username"), text[:100]), summary=text,
                              url=status["url"], published=status.get("created_at"),
                              details={"instance": instance}))
        return items

    # Default: search
    data = http.json("%s/api/v2/search" % instance, pause=0.5,
                     params={"q": query, "type": "statuses", "limit": 20, "resolve": "false"})
    items = []
    for status in data.get("statuses") or []:
        text = _strip_html(status.get("content"))
        if not text or not status.get("url"):
            continue
        items.append(Item(title="@%s: %s" % ((status.get("account") or {}).get("username"), text[:100]), summary=text,
                          url=status["url"], published=status.get("created_at"),
                          details={"instance": instance}))
    return items


def _rss_date(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).replace(tzinfo=None).isoformat()
    except (TypeError, ValueError):
        return text


def rss(http: Http, query: str, ctx: Dict[str, Any]) -> List[Item]:
    """OperateIQ's RSS reader is given feed addresses, not a search; the feeds are the organisation's own list
    and an item is kept when it names the company."""
    items: List[Item] = []
    for feed in ctx.get("rss_feeds") or []:
        root = ET.fromstring(http.text(feed, pause=0.5, headers={"User-Agent": USER_AGENT}))
        for node in root.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag not in ("item", "entry"):
                continue
            kid = {c.tag.rsplit("}", 1)[-1]: c for c in node}
            title = (kid["title"].text or "").strip() if "title" in kid else ""
            body_node = next((kid[k] for k in ("description", "summary", "content") if k in kid), None)
            body = _strip_html(body_node.text if body_node is not None else "")
            link = (kid["link"].text or kid["link"].get("href")) if "link" in kid else None
            if not title or not link or not names_company(title + " " + body, query):
                continue
            stamp = next((kid[k] for k in ("pubDate", "updated", "published") if k in kid), None)
            items.append(Item(title=title, summary=body, url=link.strip(), published=_rss_date(stamp.text if stamp is not None else None),
                              details={"feed": feed}))
    return items


# --------------------------------------------------------------------------- listed companies

def _ticker(http: Http, query: str, ctx: Dict[str, Any]) -> Optional[str]:
    if ctx.get("ticker"):
        return str(ctx["ticker"]).upper()
    data = http.json("https://query1.finance.yahoo.com/v1/finance/search", pause=0.5,
                     params={"q": query, "quotesCount": 5, "newsCount": 0}, headers={"User-Agent": USER_AGENT})
    for quote_ in data.get("quotes") or []:
        if quote_.get("quoteType") == "EQUITY" and names_company(quote_.get("longname") or quote_.get("shortname") or "", query):
            return quote_.get("symbol")
    return None


def yahoo_finance(http: Http, query: str, ctx: Dict[str, Any]) -> List[Item]:
    symbol = _ticker(http, query, ctx)
    if not symbol:
        return []
    data = http.json("https://query1.finance.yahoo.com/v7/finance/quote", pause=0.5,
                     params={"symbols": symbol}, headers={"User-Agent": USER_AGENT})
    items = []
    for q in ((data.get("quoteResponse") or {}).get("result")) or []:
        if q.get("regularMarketPrice") is None:
            continue
        cap = q.get("marketCap")
        stamp = q.get("regularMarketTime")
        parts = ["Price %.2f %s" % (q["regularMarketPrice"], q.get("currency") or "")]
        if q.get("regularMarketChangePercent") is not None:
            parts.append("change %.2f%%" % q["regularMarketChangePercent"])
        if cap:
            parts.append("market cap %.1f billion" % (cap / 1e9))
        if q.get("trailingPE") is not None:
            parts.append("P/E %.1f" % q["trailingPE"])
        items.append(Item(title="Market data: %s (%s)" % (q.get("longName") or q.get("shortName") or symbol, symbol),
                          summary=", ".join(parts), url="https://finance.yahoo.com/quote/%s" % symbol,
                          published=_utc(stamp).isoformat() if stamp else None,
                          details={"symbol": symbol, "price": q["regularMarketPrice"], "market_cap": cap}))
    return items


def alpha_vantage(http: Http, query: str, ctx: Dict[str, Any]) -> List[Item]:
    key = os.environ["ALPHAVANTAGE_API_KEY"]
    symbol = (str(ctx["ticker"]).upper() if ctx.get("ticker") else None)
    if not symbol:
        found = http.json("https://www.alphavantage.co/query", pause=12.0,
                          params={"function": "SYMBOL_SEARCH", "keywords": query, "apikey": key})
        for match in found.get("bestMatches") or []:
            if names_company(match.get("2. name") or "", query):
                symbol = match.get("1. symbol")
                break
    if not symbol:
        return []
    data = http.json("https://www.alphavantage.co/query", pause=12.0,
                     params={"function": "OVERVIEW", "symbol": symbol, "apikey": key})
    if not data.get("Symbol"):
        if data.get("Note") or data.get("Information"):
            raise SourceFailed("is at its free-tier limit for today")
        return []
    facts = [("Sector", "Sector"), ("Industry", "Industry"), ("MarketCapitalization", "Market cap"),
             ("PERatio", "P/E"), ("EPS", "EPS"), ("DividendYield", "Dividend yield")]
    summary = "; ".join("%s %s" % (label, data[k]) for k, label in facts if data.get(k) not in (None, "None", "-"))
    return [Item(title="Financial overview: %s (%s)" % (data.get("Name") or symbol, symbol),
                 summary=summary or data.get("Description") or "", url="https://finance.yahoo.com/quote/%s" % symbol,
                 published=data.get("LatestQuarter"), details={"symbol": symbol, "exchange": data.get("Exchange")})]


# --------------------------------------------------------------------------- LLMLayer (fallback web research)

def llmlayer(http: Http, query: str, ctx: Dict[str, Any]) -> List[Item]:
    """LLMLayer web search fallback. Skipped when LLMLAYER_API_KEY is absent."""
    key = os.environ.get("LLMLAYER_API_KEY")
    if not key:
        raise SourceFailed("needs LLMLAYER_API_KEY to be set")
    data = http.json("https://api.llmlayer.ai/api/v2/web_search", pause=2.0,
                     params={"query": query, "count": 10},
                     headers={"Authorization": "Bearer %s" % key, "Accept": "application/json"})
    items = []
    for r in data.get("results") or []:
        title = r.get("title")
        url = r.get("url")
        if not title or not url:
            continue
        items.append(Item(title=title, summary=r.get("snippet") or "", url=url,
                          published=None, details={}))
    return items


# --------------------------------------------------------------------------- the registry

@dataclass(frozen=True)
class Source:
    key: str
    label: str
    source_type: str  # OperateIQ's SourceType, lower case
    fetch: Callable[[Http, str, Dict[str, Any]], List[Item]]
    key_env: Optional[str] = None  # the environment name of the key it needs, if any
    needs_feeds: bool = False  # reads only feeds the organisation listed
    social: bool = False  # feeds the sentiment reading
    searched: bool = False  # a free-text search: what it returns is kept only where it names the company or its address

    @property
    def kind(self) -> str:
        return KIND_BY_TYPE[self.source_type]


REGISTRY: List[Source] = [
    Source("news_api", "NewsAPI", "news", news_api, key_env="NEWSAPI_KEY", social=True, searched=True),
    Source("brave_search", "Brave Search", "news", brave_search, key_env="BRAVE_SEARCH_API_KEY", social=True, searched=True),
    Source("sec_edgar", "SEC EDGAR", "regulatory", sec_edgar),
    Source("uspto", "USPTO patents", "patent", uspto),
    Source("open_alex", "OpenAlex", "academic", open_alex, searched=True),
    Source("reddit", "Reddit", "social", reddit, social=True, searched=True),
    Source("hackernews", "Hacker News", "social", hackernews, social=True, searched=True),
    Source("bluesky", "Bluesky", "social", bluesky, social=True, searched=True),
    Source("mastodon", "Mastodon", "social", mastodon, social=True, searched=True),
    Source("rss", "RSS feeds", "news", rss, needs_feeds=True, social=True),
    Source("yahoo_finance", "Yahoo Finance", "market", yahoo_finance),
    Source("alpha_vantage", "Alpha Vantage", "market", alpha_vantage, key_env="ALPHAVANTAGE_API_KEY"),
    Source("llmlayer", "LLMLayer", "news", llmlayer, key_env="LLMLAYER_API_KEY", social=True, searched=True),
]
BY_KEY = {s.key: s for s in REGISTRY}


def skipped_reason(source: Source, ctx: Dict[str, Any]) -> Optional[str]:
    """Why a source is not read, in words a person can act on, or None when it can be."""
    if source.key_env and not os.environ.get(source.key_env):
        return "needs %s to be set" % source.key_env
    if source.needs_feeds and not ctx.get("rss_feeds"):
        return "has no feed addresses listed"
    return None