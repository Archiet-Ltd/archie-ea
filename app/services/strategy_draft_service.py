"""The strategy drafting engine: one engine for every analysis drafted from an organisation's public footprint.

A company confirms its profile at sign-up. From that profile, the public signals read about it and about up to
five competitors, the competitors' own home and pricing pages, and (on a paid plan) the facts in its connected
sources, this engine drafts a competitor analysis, a SWOT and a PESTLE. Each statement names the numbered
sources it rests on. A two-stage critique removes every statement that has no source in the ledger, then every
statement a second reading finds the cited source does not support; neither stage adds or rewrites a word.

Nothing is recorded until a person accepts. Drafts are proposals in the existing approvals store
(``ai_chat_crud_approvals``, ``source_table = "strategy_draft"``), one row per statement, the same store the
website read uses. Accepting a statement writes an ArchiMate element carrying its sources, their links, the date
each was read and how sure the reading is:

- competitor analysis: one Assessment per statement, filed under an external BusinessActor for the competitor;
- SWOT: an Assessment placed in its box of the organisation's SWOT canvas;
- PESTLE: a Driver placed in its box of the organisation's PESTLE canvas.

The model call is a job (``strategy_drafts``), never inline in a request. The route follows the plan: a free-plan
organisation is answered by the free providers and is given public input only; a paid plan is answered by its
own provider and may add facts from its connected sources.

Adding an analysis is a prompt file under app/prompts/strategy/ and an entry in ``ANALYSES``; the Porter, BCG,
VRIO, Ansoff, Blue Ocean and 7S analyses follow on this engine.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from app import db

logger = logging.getLogger(__name__)

_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "strategy")

STRATEGY_TASK = "strategy_drafts"
DRAFT_SOURCE = "strategy_draft"
OP_DRAFT = "strategy_draft_statement"
DRAFT_DUE_DAYS = 14

MAX_COMPETITORS = 5
MAX_PER_LIST = 3  # statements shown per list; the cap applies after the critique, so a removed one does not use a place
MAX_READ_PER_LIST = 6  # statements read from an answer per list, before the critique
MAX_WORDS = 25  # the prompt asks for 15; a statement beyond this is removed rather than shortened
MODEL_TIMEOUT_SECONDS = 90.0
PROMPT_SOURCE_CHARS = 30000  # the sources handed to the model together; crawl08 caps whole prompts near 40,000
SOURCE_TEXT_CHARS = 700
MAX_SOURCES = 60

SIGNAL_CONFIDENCE = 0.8  # a public record exists; what it supports is the model's reading
COMPETITOR_PAGE_CONFIDENCE = 0.7
CONNECTED_CONFIDENCE = 1.0  # the organisation's own recorded facts
UNTICKED_BELOW_PERCENT = 50

# analysis key -> (title, canvas template key or None, element type written on accept)
ANALYSES: Dict[str, Dict[str, Any]] = {
    "competitors": {"title": "Competitor analysis", "canvas": None, "element": "Assessment"},
    "swot": {"title": "SWOT", "canvas": "swot_analysis", "element": "Assessment"},
    "pestle": {"title": "PESTLE", "canvas": "pestle_analysis", "element": "Driver"},
}
SWOT_BOXES = (("strengths", "Strengths"), ("weaknesses", "Weaknesses"), ("opportunities", "Opportunities"),
              ("threats", "Threats"))
PESTLE_BOXES = (("political", "Political"), ("economic", "Economic"), ("social", "Social"),
                ("technological", "Technological"), ("legal", "Legal"), ("environmental", "Environmental"))
BOXES = {"swot": SWOT_BOXES, "pestle": PESTLE_BOXES}

# the five questions of the competitor matrix, in the order the matrix reads them
COMPETITOR_ROWS = (("position", "Market position"), ("threat", "Threat level"), ("strength", "Strengths"),
                   ("weakness", "Weaknesses"), ("difference", "How we differ"))
_ROW_KEY = {"strengths": "strength", "weaknesses": "weakness", "differences": "difference"}
THREAT_LEVELS = {"high": "High", "medium": "Medium", "low": "Low"}
THREAT_ORDER = {"High": 0, "Medium": 1, "Low": 2}


class DraftsRefused(ValueError):
    """The drafts were not started; the message says why in words a person can act on. ``reason`` is a code."""

    def __init__(self, message: str, reason: str = "refused"):
        super().__init__(message)
        self.reason = reason


class DraftError(Exception):
    """The model's answer could not be read as the analysis asked for. Never replaced by an invented fallback."""


# ----------------------------------------------------------------------------- who and what

def company_profile(org) -> Dict[str, Any]:
    from app.modules.organization.website_crawl.confirm import PROFILE_KEY

    return (org.settings or {}).get(PROFILE_KEY) or {}


def company_name(org) -> str:
    from app.modules.organization import public_signals

    return public_signals.company_name(org)


def profile_confirmed(org) -> bool:
    """True once a person has confirmed the company profile (its name at least)."""
    held = company_profile(org).get("company_name")
    return bool(isinstance(held, dict) and str(held.get("value") or "").strip() and held.get("confirmed_by"))


def _split_competitor(entry: str) -> Tuple[str, Optional[str]]:
    """``"Acme Rival (acme-rival.example)"`` -> ``("Acme Rival", "acme-rival.example")``; a bare name has no address."""
    match = re.match(r"^\s*(.+?)\s*\(\s*((?:https?://)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}[^\s)]*)\s*\)\s*$", entry or "")
    if match:
        host = urlparse(match.group(2) if "://" in match.group(2) else "https://" + match.group(2)).hostname
        return match.group(1).strip(), (host or None)
    return (entry or "").strip(), None


def competitors_named(org) -> List[Dict[str, Optional[str]]]:
    """The competitors to compare, at most five: the ones the person named in the public-signals settings first,
    then the ones the confirmed profile holds. Each is ``{"name", "domain"}``; the domain is None when not known."""
    from app.modules.organization.public_signals.service import SETTINGS_KEY

    named: List[str] = [c for c in ((org.settings or {}).get(SETTINGS_KEY) or {}).get("competitors") or []
                        if isinstance(c, str) and c.strip()]
    held = company_profile(org).get("competitor")
    for entry in (held if isinstance(held, list) else [held] if held else []):
        value = entry.get("value") if isinstance(entry, dict) else None
        if isinstance(value, str) and value.strip():
            named.append(value)
    out: List[Dict[str, Optional[str]]] = []
    seen = set()
    own = company_name(org).lower()
    for raw in named:
        name, domain = _split_competitor(raw)
        key = name.lower()
        if not name or key in seen or key == own:
            continue
        seen.add(key)
        out.append({"name": name, "domain": domain})
        if len(out) == MAX_COMPETITORS:
            break
    return out


def plan_route(org) -> str:
    """``"public"`` for a free-plan organisation (free providers, public input only) or ``"paid"``."""
    from app.services.billing_plans import current_subscription, effective_plan

    try:
        plan = effective_plan(current_subscription(org))
    except Exception:  # a billing read that fails never gives a free plan paid-route access
        logger.warning("could not read the plan for organisation %s; using the free route", org.id, exc_info=True)
        return "public"
    return "public" if plan.key == "free" else "paid"


def _model_for(org_id: int, route: str, timeout: float = MODEL_TIMEOUT_SECONDS) -> Callable[[str], str]:
    """The model call for this route, through the one provider register the assistant uses."""
    from app.jobs.tenant_safe_job import tenant_scope
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    def ask(prompt: str) -> str:
        with tenant_scope(org_id):
            return LLMService.generate_from_prompt(
                prompt, use_cache=False, timeout=timeout, public_input=(route == "public"))

    return ask


# ----------------------------------------------------------------------------- the source ledger

def _day(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """(iso date, "12 Oct 2026") for a stored date or ISO string, else (None, None)."""
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None, None
    return moment.date().isoformat(), moment.strftime("%d %b %Y")


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _source(kind: str, subject: str, title: str, url: str, when: Any, confidence: float, text: str,
            publisher: Optional[str] = None) -> Dict[str, Any]:
    iso, label = _day(when)
    return {
        "kind": kind, "subject": subject, "title": (title or url)[:200], "url": url,
        "publisher": publisher or _host(url), "read_on": iso, "read_on_label": label,
        "percent": int(round(100 * float(confidence))), "text": (text or "").strip()[:SOURCE_TEXT_CHARS],
    }


def _profile_sources(org) -> List[Dict[str, Any]]:
    """The confirmed profile, one source per page it was read from, each holding the facts read there."""
    from app.modules.organization.website_crawl.confirm import display
    from app.modules.organization.website_crawl.service import PROFILE_FIELDS

    name = company_name(org)
    by_page: Dict[str, Dict[str, Any]] = {}
    held = company_profile(org)
    for field, label in PROFILE_FIELDS.items():
        raw = held.get(field)
        for entry in raw if isinstance(raw, list) else [raw] if raw else []:
            url = entry.get("source_url") if isinstance(entry, dict) else None
            if not url or field in ("completeness_score", "logo", "contact_email", "contact_phone"):
                continue
            page = by_page.setdefault(url, {"lines": [], "when": entry.get("fetched_at"), "confidence": []})
            page["lines"].append("%s: %s" % (label, display(entry.get("value"))))
            page["confidence"].append(float(entry.get("confidence") or 0.5))
    out = []
    for url, page in by_page.items():
        path = urlparse(url).path.strip("/")
        title = "%s website%s" % (name or _host(url), (": /" + path) if path else "")
        confidence = sum(page["confidence"]) / len(page["confidence"])
        out.append(_source("company_page", name, title, url, page["when"], confidence, "; ".join(page["lines"])))
    return out


def _signal_sources(org_id: int, subjects: List[str], per_subject: int = 10) -> List[Dict[str, Any]]:
    from app.models.organization_signal import OrganizationSignal

    out = []
    for subject in subjects:
        rows = (OrganizationSignal.query.filter(
            OrganizationSignal.organization_id == org_id, OrganizationSignal.subject == subject,
            OrganizationSignal.url.isnot(None), OrganizationSignal.kind != "sentiment")
            .order_by(db.func.coalesce(OrganizationSignal.published_at, OrganizationSignal.fetched_at).desc(),
                      OrganizationSignal.id).limit(per_subject * 2).all())
        seen = set()
        for row in rows:
            if row.url in seen or len(seen) >= per_subject:
                continue
            seen.add(row.url)
            out.append(_source(
                "signal", subject, row.title or row.url, row.url, row.fetched_at or row.published_at,
                SIGNAL_CONFIDENCE, "%s. %s" % (row.title or "", row.summary or ""), publisher=row.source_label))
    return out


def _competitor_page_sources(competitors: List[Dict[str, Optional[str]]], fetch: Optional[Callable]) -> List[Dict[str, Any]]:
    """The home and pricing pages of each competitor whose address is known: ten pages at most."""
    out: List[Dict[str, Any]] = []
    read = 0
    for competitor in competitors:
        if not competitor.get("domain"):
            continue
        site = "https://%s/" % competitor["domain"]
        for path in ("", "pricing"):
            if read >= 10:
                return out
            url = site + path
            try:
                got = (fetch or _fetch_page)(url, site)
            except Exception as exc:  # a competitor site that does not answer is simply not a source
                logger.info("competitor page %s not read: %s", url, exc)
                continue
            if not got or not got.get("text"):
                continue
            read += 1
            title = "%s %s" % (competitor["name"], "pricing page" if path else "home page")
            out.append(_source("competitor_page", competitor["name"], got.get("title") or title, got["url"],
                               datetime.utcnow(), COMPETITOR_PAGE_CONFIDENCE, got["text"]))
    return out


def _fetch_page(url: str, site: str) -> Optional[Dict[str, str]]:
    """One public page of a competitor's site, read the way the website read reads one: robots.txt honoured, the
    outbound guard applied, no script run. None when the page is refused or absent."""
    from app.modules.organization.website_crawl.extraction import soup_of, visible_text
    from app.modules.organization.website_crawl.fetcher import Fetcher

    fetcher = Fetcher(site, timeout=10.0)
    if not fetcher.allowed(url):
        return None
    page = fetcher.get(url)
    if page.status >= 400 or "html" not in page.content_type.lower():
        return None
    title = soup_of(page.text).title
    return {"url": page.url, "title": (title.string or "").strip() if title and title.string else "",
            "text": visible_text(page.text)}


def _connected_sources(org_id: int) -> List[Dict[str, Any]]:
    """Paid plans only: the organisation's own recorded facts through the genome slices (capabilities and the
    applications that support them), each a source with a page to land on."""
    from app.modules.genome.slices.registry import get_slice

    try:
        envelope = get_slice("business", org_id)
    except Exception:
        logger.info("connected facts not read for organisation %s", org_id, exc_info=True)
        return []
    out = []
    now = datetime.utcnow()
    for node in envelope["nodes"][:20]:
        if not node.get("link"):
            continue
        text = "%s: %s" % ("Capability" if node.get("kind") == "cap" else "Application", node.get("name"))
        if node.get("supported") is False:
            text += " (nothing supports it)"
        out.append(_source("connected", "", node["name"], node["link"], now, CONNECTED_CONFIDENCE, text,
                           publisher="Your model"))
    return out


def build_ledger(org, competitors: List[Dict[str, Optional[str]]], route: str,
                 fetch: Optional[Callable] = None) -> List[Dict[str, Any]]:
    """Every source a statement may cite, numbered S1, S2, ... A source has a title, an address, who published it,
    the date it was read and how sure the reading is. Free-plan organisations get public input only."""
    name = company_name(org)
    ledger = _profile_sources(org)
    ledger += _signal_sources(org.id, [name] + [c["name"] for c in competitors])
    ledger += _competitor_page_sources(competitors, fetch)
    if route == "paid":
        ledger += _connected_sources(org.id)
    ledger = ledger[:MAX_SOURCES]
    for n, source in enumerate(ledger, 1):
        source["n"] = n
        source["id"] = "S%d" % n
    return ledger


def render_sources(ledger: List[Dict[str, Any]]) -> str:
    """The sources as the prompt reads them, trimmed evenly so the whole stays inside the prompt budget."""
    if not ledger:
        return "(no sources)"
    share = max(200, min(SOURCE_TEXT_CHARS, PROMPT_SOURCE_CHARS // len(ledger) - 120))
    lines = []
    for s in ledger:
        about = (" about %s" % s["subject"]) if s.get("subject") else ""
        lines.append("[%s] %s%s (%s, read %s)\n%s" % (s["id"], s["title"], about, s["publisher"],
                                                      s["read_on_label"] or "date unknown", s["text"][:share]))
    return "\n\n".join(lines)


# ----------------------------------------------------------------------------- prompts and answers

def _load_prompt(name: str) -> str:
    with open(os.path.join(_PROMPT_DIR, name + ".txt"), encoding="utf-8") as fh:
        return fh.read()


def render_prompt(name: str, **values: str) -> str:
    """A prompt file with its ``{{name}}`` tokens replaced. Values are inserted as text and never re-read as tokens."""
    text = _load_prompt(name)
    values = {"common": _load_prompt("_common"), **values}
    for _ in range(2):  # {{common}} itself holds no tokens, but the file may place one inside another
        text = re.sub(r"\{\{(\w+)\}\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)
    return text


def _json(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.startswith("```")).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise DraftError("The model's answer was not a JSON object.")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise DraftError("The model's answer was not valid JSON: %s" % exc) from exc


def _items(value: Any) -> List[Dict[str, Any]]:
    """The ``{"text", "sources"}`` entries of one list in an answer, tolerant of a missing or odd entry."""
    out = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        sources = item.get("sources")
        if isinstance(text, str) and text.strip():
            out.append({"text": " ".join(text.split()),
                        "sources": [str(s).strip().upper() for s in sources] if isinstance(sources, list) else []})
    return out


def parse_competitors(raw: str, names: List[str]) -> List[Dict[str, Any]]:
    """Statements ``{group, row, text, sources}`` from a competitor answer. Only the listed competitors are read."""
    data = _json(raw)
    wanted = {n.lower(): n for n in names}
    out: List[Dict[str, Any]] = []
    for entry in data.get("competitors") if isinstance(data.get("competitors"), list) else []:
        if not isinstance(entry, dict):
            continue
        name = wanted.get(str(entry.get("name") or "").strip().lower())
        if not name:
            continue
        position = entry.get("position")
        for item in _items([position] if isinstance(position, dict) else [])[:1]:
            out.append({"group": name, "row": "position", **item})
        threat = entry.get("threat")
        if isinstance(threat, dict) and str(threat.get("level") or "").strip().lower() in THREAT_LEVELS:
            sources = threat.get("sources")
            out.append({"group": name, "row": "threat", "text": THREAT_LEVELS[str(threat["level"]).strip().lower()],
                        "sources": [str(s).strip().upper() for s in sources] if isinstance(sources, list) else []})
        for key, row in _ROW_KEY.items():
            for item in _items(entry.get(key))[:MAX_READ_PER_LIST]:
                out.append({"group": name, "row": row, **item})
    return out


def parse_boxes(raw: str, boxes: Iterable[str]) -> List[Dict[str, Any]]:
    data = _json(raw)
    out: List[Dict[str, Any]] = []
    for box in boxes:
        value = data.get(box, [])
        if value not in (None, "") and not isinstance(value, list):
            raise DraftError("The answer's %r was not a list." % box)
        for item in _items(value)[:MAX_READ_PER_LIST]:
            out.append({"group": box, "row": None, **item})
    return out


def _number(statements: List[Dict[str, Any]], analysis: str) -> None:
    for i, s in enumerate(statements, 1):
        s["uid"] = "s%d" % i
        s["analysis"] = analysis


# ----------------------------------------------------------------------------- the critique

def drop_unsourced(statements: List[Dict[str, Any]], ledger: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Stage one, deterministic: a statement with no source in the ledger, or too long to be a statement, is
    removed. Sources that are not in the ledger are dropped from the ones kept. Returns (kept, removed)."""
    valid = {s["id"] for s in ledger}
    kept, removed = [], []
    for s in statements:
        sources = [x for x in dict.fromkeys(s.get("sources") or []) if x in valid]
        if not sources:
            removed.append({**s, "why": "no source"})
        elif len(s["text"].split()) > MAX_WORDS:
            removed.append({**s, "why": "too long"})
        else:
            kept.append({**s, "sources": sources})
    return kept, removed


def critique(statements: List[Dict[str, Any]], ledger: List[Dict[str, Any]], model: Callable[[str], str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Stage two: one reading by the model that may only delete. It returns the ids of statements the cited
    sources do not support; nothing else it says is used. If it fails to answer, stage one stands."""
    if not statements:
        return [], []
    by_id = {s["id"]: s for s in ledger}
    lines = ["%s | %s | %s | cites %s" % (
        s["uid"], s["group"] if not s.get("row") else "%s / %s" % (s["group"], s["row"]), s["text"],
        ", ".join(s["sources"])) for s in statements]
    prompt = render_prompt("_critique", sources=render_sources([by_id[i] for i in sorted(
        {x for s in statements for x in s["sources"]}, key=lambda i: by_id[i]["n"])]), statements="\n".join(lines))
    try:
        remove = _json(model(prompt)).get("remove")
    except Exception:
        logger.info("strategy critique pass gave no usable answer; the citation filter stands", exc_info=True)
        return statements, []
    gone = {str(u) for u in remove} if isinstance(remove, list) else set()
    kept = [s for s in statements if s["uid"] not in gone]
    return kept, [{**s, "why": "not supported by its source"} for s in statements if s["uid"] in gone]


# ----------------------------------------------------------------------------- drafting

def _confidence(statement: Dict[str, Any], ledger_by_id: Dict[str, Dict[str, Any]]) -> int:
    values = [ledger_by_id[i]["percent"] for i in statement["sources"]]
    return int(round(sum(values) / len(values))) if values else 0


def _public_source(source: Dict[str, Any]) -> Dict[str, Any]:
    """A source as it is stored with a statement: what a reader needs to follow it, not the text it was read from."""
    return {k: source[k] for k in ("n", "title", "url", "publisher", "read_on", "read_on_label", "percent", "kind")}


def _cap(statements: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """At most MAX_PER_LIST statements per competitor row and per box, the first ones kept."""
    seen: Dict[Tuple[str, Optional[str]], int] = {}
    kept, dropped = [], []
    for s in statements:
        key = (s["group"], s.get("row"))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > MAX_PER_LIST and s.get("row") not in ("position", "threat"):
            dropped.append({**s, "why": "beyond three"})
        else:
            kept.append(s)
    return kept, dropped


def draft_analysis(analysis: str, name: str, ledger: List[Dict[str, Any]], competitors: List[Dict[str, Optional[str]]],
                   model: Callable[[str], str]) -> Dict[str, Any]:
    """One analysis through the whole pipeline: prompt, parse, drop the unsourced, critique. Returns
    ``{"statements": [...], "removed": [...]}``; raises DraftError if the answer could not be read."""
    sources = render_sources(ledger)
    if analysis == "competitors":
        names = [c["name"] for c in competitors]
        prompt = render_prompt("competitors", company=name, competitors="\n".join("- " + n for n in names), sources=sources)
        statements = parse_competitors(model(prompt), names)
    else:
        prompt = render_prompt(analysis, company=name, sources=sources)
        statements = parse_boxes(model(prompt), [k for k, _l in BOXES[analysis]])
    _number(statements, analysis)
    kept, removed = drop_unsourced(statements, ledger)
    kept, cut = critique(kept, ledger, model)
    kept, extra = _cap(kept)
    removed = removed + extra
    by_id = {s["id"]: s for s in ledger}
    for s in kept:
        s["percent"] = _confidence(s, by_id)
        s["sources"] = [_public_source(by_id[i]) for i in s["sources"]]
    return {"statements": kept, "removed": removed + cut}


def store_drafts(org_id: int, drafts: Dict[str, Dict[str, Any]], *, route: str, user_id: Optional[int] = None,
                 run_id: str = "") -> int:
    """Replace the organisation's pending drafts with these: one approval row per statement."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    for old in AIChatCRUDApproval.query.filter_by(
            organization_id=org_id, source_table=DRAFT_SOURCE, status=ApprovalStatus.PENDING).all():
        old.status = ApprovalStatus.EXPIRED
        old.rejected_reason = "Replaced by a newer draft."
    stored = 0
    for analysis, result in drafts.items():
        for s in result["statements"]:
            create_approval_record(
                organization_id=org_id, operation_type=OP_DRAFT, entity_type="strategy_statement",
                summary="%s: %s%s" % (ANALYSES[analysis]["title"], s["group"], (" " + s["row"]) if s.get("row") else ""),
                operation_payload={
                    "analysis": analysis, "group": s["group"], "row": s.get("row"), "text": s["text"],
                    "sources": s["sources"], "percent": s["percent"], "run_id": run_id, "route": route},
                user_id=user_id, source_table=DRAFT_SOURCE, expiry_minutes=DRAFT_DUE_DAYS * 24 * 60)
            stored += 1
    return stored


def run_drafts(org_id: int, *, llm: Optional[Callable[[str], str]] = None, fetch: Optional[Callable] = None,
               user_id: Optional[int] = None) -> Dict[str, Any]:
    """Draft the competitor analysis, SWOT and PESTLE for one organisation and store them as proposals.

    Each analysis stands alone: one that fails is reported and the others still land. Raises DraftsRefused when
    there is no confirmed profile. Counts and states only; never what a source said."""
    import uuid

    from app.jobs.tenant_safe_job import tenant_scope
    from app.models.organization import Organization
    from sqlalchemy.orm.attributes import flag_modified

    with tenant_scope(org_id):
        org = db.session.get(Organization, org_id)
        if org is None:
            raise DraftsRefused("That organisation does not exist.", "no_org")
        if not profile_confirmed(org):
            raise DraftsRefused("The company profile has not been confirmed yet, so there is nothing to draft from.",
                                "no_profile")
        route = plan_route(org)
        competitors = competitors_named(org)
        ledger = build_ledger(org, competitors, route, fetch)
        name = company_name(org)
    model = llm or _model_for(org_id, route)
    run_id = uuid.uuid4().hex
    drafts: Dict[str, Dict[str, Any]] = {}
    states: Dict[str, Dict[str, Any]] = {}
    for analysis in ANALYSES:
        if analysis == "competitors" and not competitors:
            states[analysis] = {"state": "no_competitors", "statements": 0, "removed": 0}
            continue
        try:
            result = draft_analysis(analysis, name, ledger, competitors, model)
        except Exception as exc:
            logger.info("strategy draft %s failed for organisation %s: %s", analysis, org_id, exc, exc_info=True)
            states[analysis] = {"state": "failed", "statements": 0, "removed": 0}
            continue
        drafts[analysis] = result
        states[analysis] = {"state": "ok", "statements": len(result["statements"]), "removed": len(result["removed"])}
    with tenant_scope(org_id):
        stored = store_drafts(org_id, drafts, route=route, user_id=user_id, run_id=run_id)
        org = db.session.get(Organization, org_id)
        settings = dict(org.settings or {})
        settings["strategy_drafts"] = {
            "run_id": run_id, "run_at": datetime.utcnow().isoformat(timespec="seconds"), "route": route,
            "compared": [c["name"] for c in competitors], "sources": len(ledger), "analyses": states}
        org.settings = settings
        flag_modified(org, "settings")
        db.session.commit()
    return {"run_id": run_id, "route": route, "stored": stored, "sources": len(ledger), "analyses": states,
            "removed": [dict(s, analysis=a) for a, r in drafts.items() for s in r["removed"]]}


def queue_drafts(org_id: int, user_id: Optional[int] = None):
    """Queue the drafting as a background job for one organisation; refused until the profile is confirmed."""
    from app.models.organization import Organization
    from app.services.job_queue_service import get_job_queue_service

    org = db.session.get(Organization, org_id)
    if org is None or not profile_confirmed(org):
        raise DraftsRefused("Confirm the company profile first; the drafts are written from it.", "no_profile")
    return get_job_queue_service().create_job(
        "Draft the competitor analysis, SWOT and PESTLE", STRATEGY_TASK, {"requested_by": user_id},
        organization_id=org_id)


def run_drafts_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The job body: one organisation. Returns counts only. A refusal is an answer, not a crash."""
    org_id = payload.get("organization_id")
    if org_id is None:
        raise ValueError("%s job requires organization_id in payload" % STRATEGY_TASK)
    try:
        summary = run_drafts(org_id, user_id=payload.get("requested_by"))
    except DraftsRefused as refused:
        return {"success": False, "reason": refused.reason}
    return {"success": True, "stored": summary["stored"], "sources": summary["sources"],
            "route": summary["route"], "analyses": summary["analyses"], "removed": len(summary["removed"])}


# ----------------------------------------------------------------------------- reading drafts

def pending_statements(org_id: int, analysis: Optional[str] = None) -> List[Dict[str, Any]]:
    """The organisation's drafts that wait for a person, each ``{id, analysis, group, row, text, sources, percent}``."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    rows = AIChatCRUDApproval.query.filter_by(
        organization_id=org_id, source_table=DRAFT_SOURCE, operation_type=OP_DRAFT,
        status=ApprovalStatus.PENDING).order_by(AIChatCRUDApproval.id).all()
    out = []
    for row in rows:
        payload = json.loads(row.operation_payload or "{}")
        if analysis and payload.get("analysis") != analysis:
            continue
        out.append({"id": row.id, **payload})
    return out


def _group_label(analysis: str, group: str) -> str:
    return dict(BOXES.get(analysis, ())).get(group, group)


def grouped_drafts(org_id: int) -> List[Dict[str, Any]]:
    """The drafts grouped for the confirm step (design ruling R39): one group each for Competitors, SWOT and
    PESTLE, sub-headed by competitor name (competitors) or by box in the framework's own order (SWOT, PESTLE).
    Each row carries the text, its sources, "% sure" and whether it starts ticked (50% sure or more)."""
    drafts = pending_statements(org_id)
    groups = []
    for analysis, spec in ANALYSES.items():
        rows = [d for d in drafts if d["analysis"] == analysis]
        if analysis == "competitors":
            order = list(dict.fromkeys(d["group"] for d in rows))
            threat = {d["group"]: THREAT_ORDER.get(d["text"], 3) for d in rows if d.get("row") == "threat"}
            order.sort(key=lambda n: (threat.get(n, 3), n.lower()))
            subs = [(n, n) for n in order]
        else:
            subs = list(BOXES[analysis])
        row_order = {k: i for i, (k, _l) in enumerate(COMPETITOR_ROWS)}
        out_subs = []
        for key, label in subs:
            members = [d for d in rows if d["group"] == key]
            members.sort(key=lambda d: (row_order.get(d.get("row") or "", 0), d["id"]))
            out_subs.append({"key": key, "label": label, "rows": [
                {**d, "label": dict(COMPETITOR_ROWS).get(d.get("row") or "", label),
                 "keep": d["percent"] >= UNTICKED_BELOW_PERCENT} for d in members]})
        out_subs = [s for s in out_subs if s["rows"]]
        groups.append({"key": analysis, "label": spec["title"] if analysis != "competitors" else "Competitors",
                       "count": sum(len(s["rows"]) for s in out_subs), "subgroups": out_subs})
    return groups


# ----------------------------------------------------------------------------- accepting

def _proposal_id(analysis: str, group: str, row: Optional[str], text: str) -> str:
    """A stable id for one accepted statement, so a double click or a second accept lands on the same element."""
    return hashlib.sha256(("\x1f".join([analysis, group, row or "", text.strip().lower()])).encode()).hexdigest()[:32]


def _name_of(user) -> str:
    name = getattr(user, "full_name", None)
    name = name() if callable(name) else name
    return (name or getattr(user, "email", None) or "A member").strip()


def _find_element(org_id: int, element_type: str, name: str):
    from app.models.archimate_core import ArchiMateElement

    return ArchiMateElement.query.filter_by(organization_id=org_id, type=element_type, name=name[:100]).first()


def _by_proposal(org_id: int, element_type: str, proposal_id: str):
    from app.models.archimate_core import ArchiMateElement

    for el in ArchiMateElement.query.filter_by(organization_id=org_id, type=element_type).all():
        if (el.custom_properties or {}).get("proposal_id") == proposal_id:
            return el
    return None


def _stamp(element, sources: List[Dict[str, Any]], corrected: bool, user, label: str) -> None:
    now = datetime.utcnow()
    if corrected:
        element.source_kind, element.source_ref, element.source_label = "manual", str(user.id), _name_of(user)
    else:
        element.source_kind = "import"
        element.source_ref = json.dumps({"system": "strategy_draft", "sources": [s["url"] for s in sources]}, sort_keys=True)
        element.source_label = label
    element.source_recorded_at = now


def _canvas_for(org_id: int, template_key: str):
    """The organisation's canvas of this framework, created on first acceptance."""
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES
    from app.models.business_model import BusinessModelCanvas

    canvas = BusinessModelCanvas.query.filter_by(organization_id=org_id, canvas_type=template_key).order_by(
        BusinessModelCanvas.id).first()
    if canvas is None:
        canvas = BusinessModelCanvas(name=CANVAS_TEMPLATES[template_key]["name"], canvas_type=template_key,
                                     organization_id=org_id)
        db.session.add(canvas)
        db.session.flush()
    return canvas


def _write_statement(org, user, payload: Dict[str, Any], text: str, canvases: Dict[str, Any]):
    from app.modules.business_model_canvas import model_service
    from app.services.archimate_backbone import create_backbone_element

    analysis, group, row = payload["analysis"], payload["group"], payload.get("row")
    corrected = text != payload["text"]
    sources = payload.get("sources") or []
    pid = _proposal_id(analysis, group, row, text)
    spec = ANALYSES[analysis]
    existing = _by_proposal(org.id, spec["element"], pid)
    if existing is not None:
        _clear_retired(existing)  # drafted and accepted again: it is the current reading once more
        return existing
    props: Dict[str, Any] = {
        "origin": "strategy_draft", "framework": analysis, "group": group, "row": row, "proposal_id": pid,
        "sources": sources, "confidence": 1.0 if corrected else payload.get("percent", 0) / 100.0,
        "corrected": corrected, "confirmed_by": user.id, "confirmed_by_name": _name_of(user),
        "confirmed_at": datetime.utcnow().isoformat(timespec="seconds")}
    if analysis == "competitors":
        actor = _find_element(org.id, "BusinessActor", group)
        if actor is None:
            actor = create_backbone_element(
                element_type="BusinessActor", layer="Business", name=group, organization_id=org.id,
                description="A competitor named in the company profile or its public sources",
                provenance={"origin": "competitor_analysis", "external": True, "competitor": True})
            actor.source_kind, actor.source_label = "derived", "Competitor analysis"
            actor.source_recorded_at = datetime.utcnow()
        props["competitor"] = group
        props["competitor_element_id"] = actor.id
    name = text
    if analysis == "competitors":
        name = "%s threat level: %s" % (group, text) if row == "threat" else "%s: %s" % (group, text)
    element = create_backbone_element(
        element_type=spec["element"], layer="Motivation", name=name, description=text, organization_id=org.id,
        provenance=props)
    _stamp(element, sources, corrected, user, "%s, drafted from %s" % (spec["title"], sources[0]["title"] if sources else "public sources"))
    if spec["canvas"]:
        key = spec["canvas"]
        if key not in canvases:
            canvases[key] = _canvas_for(org.id, key)
        model_service.place(canvases[key], group, element)
    return element


RETIRED_AT = "retired_at"  # the service's own retirement mark; the history trigger owns superseded_at and resets
RETIRED_BY = "retired_by_element_id"  # it to NULL on every write, so a reading cannot be retired through that column


def _is_retired(props: Dict[str, Any]) -> bool:
    """True when a reading carries the service's own retirement mark and so is no longer the current analysis."""
    return bool((props or {}).get(RETIRED_AT))


def _mark_retired(element, replaced_by_id: Optional[int]) -> None:
    """Stamp the service's retirement mark onto the element's custom_properties, merging the dict (never replacing
    it) so every other tagged value survives. The history trigger does not own custom_properties, so the mark
    stands after the row is written."""
    from sqlalchemy.orm.attributes import flag_modified

    props = dict(element.custom_properties or {})
    props[RETIRED_AT] = datetime.utcnow().isoformat(timespec="seconds")
    props[RETIRED_BY] = replaced_by_id
    element.custom_properties = props
    flag_modified(element, "custom_properties")


def _clear_retired(element) -> None:
    """Drop the retirement mark (drafted and accepted again: the reading is current once more), merging the dict."""
    from sqlalchemy.orm.attributes import flag_modified

    props = dict(element.custom_properties or {})
    if RETIRED_AT in props or RETIRED_BY in props:
        props.pop(RETIRED_AT, None)
        props.pop(RETIRED_BY, None)
        element.custom_properties = props
        flag_modified(element, "custom_properties")


def _retire_older(org_id: int, competitor: str, keep_ids: set) -> int:
    """A new accepted reading of a competitor replaces the matrix column: the earlier statements stay in the model,
    carrying the service's own retirement mark, and are no longer read as the current analysis. superseded_at is
    not used -- the entity-history trigger owns it and resets it to NULL as the row is written."""
    from app.models.archimate_core import ArchiMateElement

    replaced_by = min(keep_ids) if keep_ids else None
    retired = 0
    for el in ArchiMateElement.query.filter_by(organization_id=org_id, type="Assessment").all():
        props = el.custom_properties or {}
        if _is_retired(props):
            continue
        if props.get("framework") == "competitors" and props.get("competitor") == competitor and el.id not in keep_ids:
            _mark_retired(el, replaced_by)
            retired += 1
    return retired


def accept_drafts(org, user, decisions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    """Record the drafts a person accepted (as drafted, or as they edited them) and close the rest.

    ``decisions`` is a list of ``{"id": draft id, "accept": bool, "value": edited text}``; with none given, every
    pending draft is accepted as drafted. A draft that is not pending in this organisation is ignored. Nothing is
    written for a draft not accepted. Returns counts of what was kept, edited and left out."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    pending = AIChatCRUDApproval.query.filter_by(
        organization_id=org.id, source_table=DRAFT_SOURCE, operation_type=OP_DRAFT,
        status=ApprovalStatus.PENDING).order_by(AIChatCRUDApproval.id).all()
    if decisions is None:
        by_id = {a.id: {"id": a.id, "accept": True} for a in pending}
    else:
        by_id = {int(d["id"]): d for d in decisions if str(d.get("id", "")).isdigit()}
    now = datetime.utcnow()
    kept = edited = left_out = 0
    canvases: Dict[str, Any] = {}
    written: Dict[str, set] = {}
    for approval in pending:
        decision = by_id.get(approval.id)
        if decision is None:
            continue
        payload = json.loads(approval.operation_payload or "{}")
        text = str(decision.get("value") if decision.get("value") is not None else payload["text"]).strip()
        if not decision.get("accept") or not text:
            approval.status = ApprovalStatus.REJECTED
            approval.rejected_reason = "Left out by the person confirming the drafts."
            left_out += 1
            continue
        if payload.get("row") == "threat" and text.capitalize() in THREAT_ORDER:
            text = text.capitalize()
        element = _write_statement(org, user, payload, text, canvases)
        if payload["analysis"] == "competitors":
            written.setdefault(payload["group"], set()).add(element.id)
        approval.status = ApprovalStatus.APPROVED
        approval.approved_at = approval.executed_at = now
        approval.approved_by_id = user.id
        approval.execution_result = json.dumps({"success": True, "element_id": element.id, "corrected": text != payload["text"]})
        kept += 1
        edited += 1 if text != payload["text"] else 0
    for competitor, ids in written.items():
        _retire_older(org.id, competitor, ids)
    db.session.commit()
    return {"kept": kept, "edited": edited, "left_out": left_out}


# ----------------------------------------------------------------------------- reading what was accepted

def accepted_statements(org_id: int, analysis: str) -> List[Dict[str, Any]]:
    """The accepted statements of one analysis, newest first: ``{element_id, group, row, text, sources,
    percent, confirmed_by_name, confirmed_at}``. A reading that carries the retirement mark is not current."""
    from app.models.archimate_core import ArchiMateElement

    out = []
    for el in ArchiMateElement.query.filter_by(organization_id=org_id, type=ANALYSES[analysis]["element"]).order_by(
            ArchiMateElement.id.desc()).all():
        props = el.custom_properties or {}
        if props.get("framework") != analysis or _is_retired(props):
            continue
        out.append({
            "element_id": el.id, "group": props.get("group"), "row": props.get("row"), "text": el.description or el.name,
            "sources": props.get("sources") or [], "percent": int(round(100 * float(props.get("confidence") or 0))),
            "competitor_element_id": props.get("competitor_element_id"),
            "confirmed_by_name": props.get("confirmed_by_name"), "confirmed_at": props.get("confirmed_at")})
    return out
