"""Add cited public signals to an organisation's company profile, and keep them fresh.

For an organisation that agreed at sign-up to have its public footprint read (the website consent), this
reads the sources in ``sources.REGISTRY`` for the company's name, stores each item once as an
``OrganizationSignal`` (source, link, published date, when it was read), has a model score the public
mentions with OperateIQ's sentiment prompt, and tags each signal with the PESTLE factor and SWOT
quadrant it informs. The profile card, and the PESTLE, SWOT and competitor readers below, read that one
store. ``refresh_all`` is the scheduled entry; it visits each consenting organisation in its own scope.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from app import db
from app.models.organization_signal import OrganizationSignal

from ..website_crawl import prompts
from ..website_crawl.rendering import parse_json
from . import sources as src

logger = logging.getLogger(__name__)

SETTINGS_KEY = "public_signals"
REFRESH_EVERY = timedelta(hours=23)  # a daily schedule always finds the last run old enough
MAX_PER_SOURCE = 20
MAX_FOR_SENTIMENT = 40
KEEP_DAYS = 365

COMPANY_ONLY = ("sec_edgar", "uspto", "yahoo_finance", "alpha_vantage")  # the company's own records, not a competitor's

PESTLE = ("political", "economic", "social", "technological", "legal", "environmental")
SWOT = ("strength", "weakness", "opportunity", "threat")

# Which PESTLE factor a signal informs, and why. A filing is a legal and regulatory record, a market figure is an
# economic one, a patent or a paper is technological: the record type is the reason. News and public mentions are
# read by their headline alone, for whole words that name the matter (a lawsuit, a tariff, an emissions target).
# Words that only describe a product (launch, platform, software, cloud, AI) and words in the body text decide
# nothing: where the headline names no matter the signal carries no factor, and is never forced into one.
_KIND_FACTOR = {"filing": ("legal", "it is a regulatory filing"), "financial": ("economic", "it is a market figure"),
                "patent": ("technological", "it is a patent"), "research": ("technological", "it is a research paper")}
_WORDS = (
    ("legal", ("lawsuit", "lawsuits", "sued", "sues", "court", "fined", "antitrust", "gdpr", "regulator", "regulators",
               "regulation", "regulations", "settlement", "license", "licence", "licensing", "relicensing", "trademark",
               "copyright")),
    ("political", ("government", "election", "tariff", "tariffs", "sanction", "sanctions", "minister", "congress",
                   "parliament", "treaty")),
    ("environmental", ("climate", "emission", "emissions", "carbon", "sustainability", "net zero", "renewable", "renewables")),
    ("economic", ("revenue", "profit", "earnings", "inflation", "funding", "investment", "acquisition", "acquires",
                  "acquired", "layoffs", "ipo", "valuation", "raises", "raised", "series a", "series b", "series c",
                  "series d", "pricing")),
    # open versus closed source is a choice about the technology and who may use it: technological
    ("technological", ("open source", "closed source", "source code", "infrastructure", "outage", "breach", "cyberattack",
                       "ransomware", "vulnerability", "vulnerabilities", "api", "sdk")),
    ("social", ("hiring", "hires", "workforce", "boycott", "protest", "diversity")),
)


class SignalsRefused(ValueError):
    """Nothing was read; the message says why in words a person can act on."""


# ----------------------------------------------------------------------------- who and what to read

def consenting(org) -> bool:
    site = ((org.settings or {}).get("onboarding") or {}).get("website") or {}
    return bool(site.get("consent_at"))


PLACEHOLDER_ENDING = "'s Workspace"  # what a new organisation is called until its company name is confirmed


def is_placeholder_name(name: Optional[str]) -> bool:
    """True for the stand-in name given at sign-up ("Casey Startup's Workspace"), which names no company."""
    return str(name or "").strip().endswith(PLACEHOLDER_ENDING)


def company_name(org) -> str:
    """The name the person confirmed; else the organisation's own name; empty while that is still the stand-in."""
    from ..website_crawl.confirm import PROFILE_KEY

    held = ((org.settings or {}).get(PROFILE_KEY) or {}).get("company_name")
    value = (held or {}).get("value") if isinstance(held, dict) else None
    named = str(value or "").strip()
    if named:
        return named
    own = str(org.name or "").strip()
    return "" if is_placeholder_name(own) else own


_PRODUCT_FIELDS = ("tagline", "product", "category", "value_proposition", "business_model", "industry_sector")
_PLAIN = {"about", "their", "which", "where", "while", "these", "those", "other", "software", "company", "business",
          "online", "teams", "people", "everyone", "everything", "great", "better", "simple", "based"}


def product_words(org) -> List[str]:
    """The words the company's own confirmed facts use for what it does ("scheduling", "infrastructure"): a headline
    that names the company beside one of them is about the company. Taken only from facts a person confirmed."""
    from ..website_crawl.confirm import PROFILE_KEY

    mine = (org.settings or {}).get(PROFILE_KEY) or {}
    brand = set(re.findall(r"[a-z0-9]+", company_name(org).lower()))
    words = set()
    for field in _PRODUCT_FIELDS:
        entry = mine.get(field)
        for value in (entry if isinstance(entry, list) else [entry]):
            text = value.get("value") if isinstance(value, dict) else value
            for w in re.findall(r"[a-z]{5,}", str(text or "").lower()):
                if w not in brand and w not in _PLAIN:
                    words.add(w)
    return sorted(words)


def company_domain(org) -> str:
    """The company's web address from the site it gave at sign-up (no scheme, no www), or empty."""
    from urllib.parse import urlparse

    url = (((org.settings or {}).get("onboarding") or {}).get("website") or {}).get("url") or ""
    host = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower() if url else ""
    return host[4:] if host.startswith("www.") else host


def _context(org) -> Dict[str, Any]:
    mine = (org.settings or {}).get(SETTINGS_KEY) or {}
    return {
        "ticker": mine.get("ticker") or ((org.settings or {}).get("company_context") or {}).get("ticker"),
        "rss_feeds": [f for f in mine.get("rss_feeds") or [] if isinstance(f, str) and f.startswith("http")],
        "competitors": [c for c in mine.get("competitors") or [] if isinstance(c, str) and c.strip()],
    }


def pestle_reason(kind: str, title: str, summary: str = "") -> tuple:
    """(factor, the reason in words) for a signal, or (None, None) when no category clearly applies.
    Only the record type and the headline decide it; the summary is not read."""
    if kind in _KIND_FACTOR:
        return _KIND_FACTOR[kind]
    text = " ".join(re.findall(r"[a-z0-9]+", (title or "").lower()))
    for factor, words in _WORDS:
        for word in words:
            if (" %s " % word) in (" %s " % text):
                return factor, 'the headline says "%s"' % word
    return None, None


def pestle_factor(kind: str, title: str, summary: str = "") -> Optional[str]:
    return pestle_reason(kind, title, summary)[0]


def swot_quadrant(subject_is_company: bool, score: Optional[float]) -> Optional[str]:
    """Only a scored mention carries a quadrant: well spoken of is a strength and badly spoken of a weakness
    for the company itself; for a competitor it is the other way about (a threat, an opportunity)."""
    if score is None or -0.2 <= score <= 0.2:
        return None
    if subject_is_company:
        return "strength" if score > 0 else "weakness"
    return "threat" if score > 0 else "opportunity"


def _date(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    text = str(text).strip().replace("Z", "+00:00")
    for parse in (datetime.fromisoformat, lambda t: datetime.strptime(t[:10], "%Y-%m-%d")):
        try:
            value = parse(text)
            return value.replace(tzinfo=None) if value.tzinfo else value
        except ValueError:
            continue
    return None


def _checksum(source: str, day: str, title: str, subject: str = "") -> str:
    """Once per organisation, per subject: one article about two companies is a signal for each, and the same
    source, title and date is one signal whatever address it was found at."""
    return hashlib.sha256(("|".join([source, subject, day or "", title])).encode("utf8")).hexdigest()


def _day(value: Optional[datetime]) -> str:
    return value.date().isoformat() if value else ""


def _same_item_key(row: OrganizationSignal) -> tuple:
    return (row.source, row.subject, row.title, _day(row.published_at))


NEAR_DAYS = 3  # one story re-posted with its words moved, within this many days, is one signal


def title_words(title: str) -> tuple:
    """The words of a title, lower case, without possessive endings and articles, in order of spelling:
    "Posthog's New Website" and "New Posthog Website" are the same words."""
    text = re.sub(r"['\u2019]s\b", "", (title or "").lower())
    return tuple(sorted(w for w in re.findall(r"[a-z0-9]+", text) if w not in ("a", "an", "the")))


def _near(a: Optional[datetime], b: Optional[datetime], days: int = NEAR_DAYS) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= timedelta(days=days)


EVENT_DAYS = 4
_EVENT_STOP = {"a", "an", "the", "of", "in", "on", "for", "and", "to", "is", "are", "its", "it", "as", "at", "by", "with", "from",
               "why", "how", "what", "going", "goes", "go", "hn", "show", "launch", "ask", "tell", "our", "we", "this", "that",
               "be", "has", "have", "will", "s", "now"}


def _stem(word: str) -> str:
    for ending in ("ing", "ed", "es", "s"):
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            word = word[:-len(ending)]
            break
    return word[:-1] if word.endswith("e") and len(word) > 4 else word


def event_words(title: str, brand: str) -> frozenset:
    """The words that say what happened: the title less the company's name, filler and word endings."""
    left = set(re.findall(r"[a-z0-9]+", (brand or "").lower()))
    return frozenset(_stem(w) for w in re.findall(r"[a-z0-9]+", re.sub(r"['\u2019]s\b", "", (title or "").lower()))
                     if w not in left and w not in _EVENT_STOP)


def _one_event(a: frozenset, b: frozenset) -> bool:
    shared = len(a & b)
    return shared >= 2 and shared / min(len(a), len(b)) >= 0.6


def group_events(rows, brand: str) -> List[list]:
    """Rows, newest first, as groups of ``[first, [every row about the same event]]``: stories published within a few
    days that use the same words for what happened ("is going closed source", "closes its source code") are one event.
    A sentiment summary is never grouped."""
    groups: List[list] = []
    for row in rows:
        words = None if row.kind == "sentiment" else event_words(row.title, brand)
        for group in groups:
            if words is not None and any(
                    held is not None and _near(row.published_at, other.published_at, EVENT_DAYS) and _one_event(words, held)
                    for other, held in group[2]):  # the same event as any story already in the group
                group[1].append(row)
                group[2].append((row, words))
                break
        else:
            groups.append([row, [row], [(row, words)]])
    return [[g[0], g[1]] for g in groups]


def near_repeats(rows) -> List[Any]:
    """Of rows in the order given, those that repeat an earlier one: same source and subject, the same words
    in the title, published within a few days. A sentiment summary is its own thing and is never a repeat."""
    kept: Dict[tuple, List[Optional[datetime]]] = {}
    repeats = []
    for row in rows:
        if row.kind == "sentiment":
            continue
        key = (row.source, row.subject, title_words(row.title))
        if any(_near(row.published_at, day) for day in kept.get(key, [])):
            repeats.append(row)
        else:
            kept.setdefault(key, []).append(row.published_at)
    return repeats


# ----------------------------------------------------------------------------- tidying

def _title_key(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (title or "").lower()))


_RECORD_KINDS = ("sentiment", "filing", "financial")  # a record is told apart by its date, not only its title


def tidy(org_id: int, org=None) -> int:
    """Remove what should never have been kept: signals stored under the stand-in name, and, for the confirmed
    company, hits from a free-text search that are not about it, and repeats of one source, title and date. Returns how many went.
    The caller commits. Works only on this organisation's rows."""
    from app.models.organization import Organization

    org = org or db.session.get(Organization, org_id)
    removed = 0
    rows = OrganizationSignal.query.filter(OrganizationSignal.organization_id == org_id).all()
    name, domain = (company_name(org), company_domain(org)) if org is not None else ("", "")
    words = product_words(org) if org is not None else []
    searched = {s.key for s in src.REGISTRY if s.searched}
    for row in rows:
        gone = is_placeholder_name(row.subject) or (
            bool(name) and row.subject == name and row.source in searched
            and not src.counts_as_news(row.title, name, domain, src.own_link(row.url, row.details), words)) or (
            bool(name) and row.subject == name and row.source == "sec_edgar"
            and not src.legal_name_matches(row.title.split(": ")[0], name))
        if gone:
            db.session.delete(row)
            removed += 1
    seen = set()  # the same source, title and date is one signal: keep the first stored
    for row in sorted(rows, key=lambda r: r.id):
        if row in db.session.deleted:
            continue
        key = _same_item_key(row)
        if key in seen:
            db.session.delete(row)
            removed += 1
        seen.add(key)
    titled = set()  # the same title again, on whatever date, is a repeat
    for row in sorted(rows, key=lambda r: r.id):
        if row in db.session.deleted or row.kind in _RECORD_KINDS:
            continue
        key = (row.source, row.subject, _title_key(row.title))
        if key in titled:
            db.session.delete(row)
            removed += 1
        titled.add(key)
    live = [r for r in sorted(rows, key=lambda r: r.id) if r not in db.session.deleted]
    for row in near_repeats(live):  # the same words in another order, a day or two apart
        db.session.delete(row)
        removed += 1
    for row in live:  # a tag kept from an earlier, looser rule is read again by the current one
        if row.kind != "sentiment" and row not in db.session.deleted and row.pestle_factor != pestle_factor(row.kind, row.title):
            row.pestle_factor = pestle_factor(row.kind, row.title)
    return removed


# ----------------------------------------------------------------------------- writing

def _relevant(source: src.Source, subject: str, domain: str, items: List[src.Item], is_company: bool, words=()) -> List[src.Item]:
    """What a free-text search gave back, less each hit that names neither the company nor its web address
    in its title.
    Only the confirmed company is held to this. Sources that already match on the company's own records are left as they are."""
    if not source.searched or not is_company:
        return items
    return [i for i in items if src.counts_as_news(i.title, subject, domain, src.own_link(i.url, i.details), words)]


class _Candidate:
    """A hit not stored yet, shaped like a stored row so the same repeat test reads both."""

    kind = "news"

    def __init__(self, source: str, subject: str, title: str, published_at: Optional[datetime]):
        self.source, self.subject, self.title, self.published_at = source, subject, title, published_at


def _store(org_id: int, source: src.Source, subject: str, items: List[src.Item], is_company: bool, now: datetime) -> int:
    kept = 0
    if is_placeholder_name(subject):
        return 0  # a stand-in name is never searched and nothing is ever kept under it
    for item in items[:MAX_PER_SOURCE]:
        if not item.title:
            continue
        title, published = item.title[:500], _date(item.published)
        digest = _checksum(source.key, _day(published), title, subject)
        row = OrganizationSignal.query.filter_by(organization_id=org_id, checksum=digest).first()
        if row is None:  # an older row for the same source, title and date, stored under another address
            row = next((r for r in OrganizationSignal.query.filter_by(
                organization_id=org_id, source=source.key, subject=subject, title=title).all()
                if _day(r.published_at) == _day(published)), None)
        if row is None and source.kind not in ("filing", "financial") and any(
                _title_key(r.title) == _title_key(title) for r in OrganizationSignal.query.filter_by(
                    organization_id=org_id, source=source.key, subject=subject).all()):
            continue  # reposted: the same title, another date
        if row is None and any(near_repeats([r, _Candidate(source.key, subject, title, published)])
                               for r in OrganizationSignal.query.filter_by(
                                   organization_id=org_id, source=source.key, subject=subject).all()):
            continue  # the same story as one already kept, with its words moved
        if row is None:
            row = OrganizationSignal(organization_id=org_id, checksum=digest, source=source.key, created_at=now)
            db.session.add(row)
        row.kind, row.source_label, row.subject = source.kind, source.label, subject
        row.title, row.summary, row.url = title, (item.summary or "")[:2000], (item.url or None)
        row.published_at, row.fetched_at, row.details = published, now, item.details or None
        row.pestle_factor = pestle_factor(row.kind, row.title, row.summary)
        kept += 1
    return kept


def _sentiment(org_id: int, subject: str, mentions: List[OrganizationSignal], model: Callable[[str], str], is_company: bool, now: datetime) -> Optional[Dict[str, Any]]:
    """OperateIQ's sentiment reading over the public mentions just read; one summary signal, and a score on each mention the model named."""
    if not mentions:
        return None
    sources: Dict[str, List[Dict[str, Any]]] = {}
    for row in mentions[:MAX_FOR_SENTIMENT]:
        sources.setdefault("news" if row.kind == "news" else "social", []).append({
            "title": row.title, "content": (row.summary or "")[:500], "url": row.url or "",
            "date": row.published_at.date().isoformat() if row.published_at else ""})
    prompt = prompts.render_text(_prompt_text(), {
        "company_name": subject, "company_data": {"overview": None, "industry": None},
        "sources": [{"source_type": kind, "items": items} for kind, items in sources.items()]})
    result = parse_json(model(prompt))
    overall = result.get("overall_sentiment") or {}
    score = overall.get("score")
    if not isinstance(score, (int, float)):
        raise ValueError("the sentiment reading gave no score")
    score = max(-1.0, min(1.0, float(score)))
    by_url = {m.url: m for m in mentions if m.url}
    for note in result.get("notable_mentions") or []:
        row, value = by_url.get(note.get("url")), (note.get("sentiment") or {}).get("score")
        if row is not None and isinstance(value, (int, float)):
            row.sentiment_score = max(-1.0, min(1.0, float(value)))
            row.swot_quadrant = swot_quadrant(is_company, row.sentiment_score)
    topics = [t.get("topic") for t in result.get("trending_topics") or [] if t.get("topic")][:5]
    label = overall.get("label") or ("positive" if score > 0.2 else "negative" if score < -0.2 else "neutral")
    digest = _checksum("sentiment_analysis", "", now.date().isoformat(), subject)
    row = OrganizationSignal.query.filter_by(organization_id=org_id, checksum=digest).first()
    if row is None:
        row = OrganizationSignal(organization_id=org_id, checksum=digest, source="sentiment_analysis", created_at=now)
        db.session.add(row)
    row.kind, row.source_label, row.subject = "sentiment", "Public sentiment (from %d public items)" % len(mentions[:MAX_FOR_SENTIMENT]), subject
    row.title = "Public sentiment about %s: %s" % (subject, str(label).replace("_", " "))
    row.summary = ("Topics: %s." % ", ".join(topics)) if topics else "Read across the public mentions listed beside it."
    row.url, row.published_at, row.fetched_at = None, now, now
    row.sentiment_score, row.swot_quadrant = score, swot_quadrant(is_company, score)
    row.details = {"label": label, "confidence": overall.get("confidence"), "sample_size": len(mentions[:MAX_FOR_SENTIMENT]),
                   "analysis_period": result.get("analysis_period")}
    return {"score": score, "label": label}


def _prompt_text() -> str:
    from pathlib import Path

    return (Path(__file__).parent / "prompts" / "sentiment_analysis.hbs").read_text(encoding="utf-8")


def refresh_organization(
    org_id: int, *, http: Optional[src.Http] = None, llm: Optional[Callable[[str], str]] = None,
    force: bool = False, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Read every available source for one organisation and store what came back.

    Raises SignalsRefused when the organisation did not agree to be read, or was read too recently.
    Returns, per source, how many signals it gave or why it was left out, and the sentiment reading."""
    from app.jobs.tenant_safe_job import tenant_scope
    from app.models.organization import Organization

    now = now or datetime.utcnow()
    org = db.session.get(Organization, org_id)
    if org is None:
        raise SignalsRefused("That organisation does not exist.")
    with tenant_scope(org_id):
        if tidy(org_id):
            db.session.commit()
    org = db.session.get(Organization, org_id)  # the scope detached the row loaded above
    if not consenting(org):
        raise SignalsRefused("The organisation has not agreed to its public footprint being read.")
    mine = dict((org.settings or {}).get(SETTINGS_KEY) or {})
    last = _date(mine.get("refreshed_at"))
    if not force and last and now - last < REFRESH_EVERY:
        raise SignalsRefused("The signals were refreshed %s ago; they are read once a day." % _ago(now - last))
    name, ctx = company_name(org), _context(org)
    if not name:
        raise SignalsRefused("The company name has not been confirmed yet, so there is nothing to search for.")
    domain, words = company_domain(org), product_words(org)
    http = http or src.Http()
    if llm is None:
        from ..website_crawl.service import default_model

        llm = default_model()
    report: Dict[str, Any] = {"sources": {}, "sentiment": None, "stored": 0}
    subjects = [(name, True)] + [(c.strip(), False) for c in ctx["competitors"][:5]]
    with tenant_scope(org_id):
        mentions: Dict[str, List[OrganizationSignal]] = {}
        for source in src.REGISTRY:
            reason = src.skipped_reason(source, ctx)
            if reason:
                logger.info("signal source %s left out for organisation %s: %s", source.key, org_id, reason)
                report["sources"][source.key] = {"label": source.label, "skipped": reason, "unread": "needs " in reason}
                continue
            total, failure = 0, None
            for subject, is_company in subjects:
                if source.key in COMPANY_ONLY and not is_company:
                    continue
                try:
                    items = source.fetch(http, subject, ctx)
                    if source.searched and is_company and domain:  # the company's web address is searched as well as its name
                        seen = {(i.url, i.title) for i in items}
                        items = items + [i for i in source.fetch(http, domain, ctx) if (i.url, i.title) not in seen]
                    items = _relevant(source, subject, domain, items, is_company, words)
                except src.SourceFailed as exc:
                    logger.warning("signal source %s failed for organisation %s: %s", source.key, org_id, exc)
                    failure = "%s %s" % (source.label, exc)
                    break
                except Exception as exc:  # one source's bug or changed answer never stops the others
                    logger.info("signal source %s failed for organisation %s", source.key, org_id, exc_info=True)
                    failure = "%s could not be read (%s)" % (source.label, type(exc).__name__)
                    break
                db.session.flush()
                total += _store(org_id, source, subject, items, is_company, now)
                if source.social:
                    db.session.flush()
                    mentions.setdefault(subject, []).extend(OrganizationSignal.query.filter(
                        OrganizationSignal.organization_id == org_id, OrganizationSignal.source == source.key,
                        OrganizationSignal.subject == subject, OrganizationSignal.kind.in_(("news", "mention")),
                        OrganizationSignal.fetched_at == now).all())
            report["sources"][source.key] = {"label": source.label, "signals": total, **({"failed": failure} if failure else {})}
            report["stored"] += total
        for subject, is_company in subjects:
            try:
                reading = _sentiment(org_id, subject, mentions.get(subject, []), llm, is_company, now)
            except Exception:
                logger.info("sentiment reading failed for organisation %s", org_id, exc_info=True)
                reading = None
                report["sentiment_failed"] = True
            if is_company:
                report["sentiment"] = reading
        cutoff = now - timedelta(days=KEEP_DAYS)
        OrganizationSignal.query.filter(
            OrganizationSignal.organization_id == org_id, OrganizationSignal.fetched_at < cutoff).delete(synchronize_session=False)
        org = db.session.get(Organization, org_id)  # the scope detached the row loaded above
        fresh = dict((org.settings or {}).get(SETTINGS_KEY) or {})
        fresh["refreshed_at"] = now.isoformat()
        # what is kept for the page is only whether a source was read; the detail stays in the server log
        fresh["last_report"] = {k: {"label": v["label"], "unread": bool(v.get("unread") or v.get("failed"))}
                                for k, v in report["sources"].items()}
        settings = dict(org.settings or {})
        settings[SETTINGS_KEY] = fresh
        org.settings = settings
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(org, "settings")
        db.session.commit()
    return report


def _ago(delta: timedelta) -> str:
    hours = int(delta.total_seconds() // 3600)
    return "%d hours" % hours if hours >= 1 else "under an hour"


def refresh_all(app, *, http=None, llm=None) -> List[Dict[str, Any]]:
    """The scheduled entry: each consenting organisation, in its own scope. A refusal (read recently) is not an error."""
    from app.jobs.tenant_safe_job import run_for_each_tenant

    results: List[Dict[str, Any]] = []

    def one(organization_id: int):
        try:
            results.append({"organization_id": organization_id, **refresh_organization(organization_id, http=http, llm=llm)})
        except SignalsRefused as exc:
            results.append({"organization_id": organization_id, "refused": str(exc)})

    run_for_each_tenant(app, "company-signals-refresh", one)
    return results


SIGNALS_TASK = "company_signals_refresh"


def queue_signals_refresh(org_id: int):
    """Queue the first reading for an organisation that agreed to it and has a company name to search for; None when not."""
    from app.models.organization import Organization
    from app.services.job_queue_service import get_job_queue_service

    org = db.session.get(Organization, org_id)
    if org is None or not consenting(org) or not company_name(org):
        return None
    return get_job_queue_service().create_job("Read the company's public signals", SIGNALS_TASK, {}, organization_id=org_id)


def run_signals_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The job body: one organisation, counts only. A refusal (read within the day) is an answer, not a failure."""
    org_id = payload.get("organization_id")
    if org_id is None:
        raise ValueError("%s job requires organization_id in payload" % SIGNALS_TASK)
    try:
        report = refresh_organization(org_id)
    except SignalsRefused as exc:
        return {"refused": str(exc)}
    return {"stored": report["stored"], "sources": {k: v.get("signals", v.get("skipped") or v.get("failed")) for k, v in report["sources"].items()}}


# ----------------------------------------------------------------------------- reading

def _row(sig: OrganizationSignal) -> Dict[str, Any]:
    return {
        "id": sig.id, "kind": sig.kind, "source": sig.source_label, "source_key": sig.source, "subject": sig.subject,
        "title": sig.title, "summary": sig.summary, "url": sig.url, "published_at": sig.published_at,
        "fetched_at": sig.fetched_at, "pestle_factor": sig.pestle_factor, "swot_quadrant": sig.swot_quadrant,
        "pestle_reason": pestle_reason(sig.kind, sig.title)[1] if sig.pestle_factor else None,
        "sentiment_score": sig.sentiment_score, "reports": 1,
    }


def signals_for_profile(org_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """The newest signals about the company itself, each with its source and date."""
    from app.models.organization import Organization

    org = db.session.get(Organization, org_id)
    name = company_name(org) if org is not None else None
    if not name:
        return []  # until the company is confirmed there is nothing to show
    query = OrganizationSignal.query.filter(OrganizationSignal.organization_id == org_id, OrganizationSignal.subject == name)
    rows = query.order_by(db.func.coalesce(OrganizationSignal.published_at, OrganizationSignal.fetched_at).desc(), OrganizationSignal.id).all()
    shown, seen = [], set()
    for r in rows:  # the same source, title and date is shown once
        key = _same_item_key(r)
        if key not in seen:
            seen.add(key)
            shown.append(r)
    titled, once = set(), []
    for r in shown:  # the same title again, on whatever date, is shown once
        key = (r.source, r.subject, _title_key(r.title))
        if r.kind in _RECORD_KINDS or key not in titled:
            titled.add(key)
            once.append(r)
    out = []
    for first, members in group_events(once, name):  # stories of one event are one row with a count
        row = _row(first)
        row["reports"] = len(members)
        out.append(row)
    return out[:limit]


def framework_view(org_id: int, framework: str) -> Dict[str, List[Dict[str, Any]]]:
    """Signals grouped for PESTLE (by factor), SWOT (by quadrant), or competitors (by competitor name)."""
    query = OrganizationSignal.query.filter(OrganizationSignal.organization_id == org_id)
    if framework == "pestle":
        groups, column = {f: [] for f in PESTLE}, "pestle_factor"
    elif framework == "swot":
        groups, column = {q: [] for q in SWOT}, "swot_quadrant"
    elif framework == "competitors":
        from app.models.organization import Organization

        org = db.session.get(Organization, org_id)
        names = _context(org)["competitors"] if org is not None else []
        groups, column = {n: [] for n in names}, "subject"
    else:
        raise ValueError("Unknown framework: %s" % framework)
    for sig in query.order_by(db.func.coalesce(OrganizationSignal.published_at, OrganizationSignal.fetched_at).desc()).all():
        key = getattr(sig, column)
        if key in groups:
            groups[key].append(_row(sig))
    return groups


def refresh_state(org) -> Dict[str, Any]:
    """What the profile card says about reading: whether the organisation agreed, when it last ran, and whether
    some sources could not be read (never which, or why: that is for the server log)."""
    mine = (org.settings or {}).get(SETTINGS_KEY) or {}
    unread = any(v.get("unread") for v in (mine.get("last_report") or {}).values())
    return {"agreed": consenting(org), "refreshed_at": _date(mine.get("refreshed_at")), "some_unread": unread}
