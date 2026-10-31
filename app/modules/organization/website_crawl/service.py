"""Read an organisation's consented website into profile proposals.

``crawl_organization_website`` reads in two passes. Pass 1 fetches up to 30 pages of the one site
the organisation agreed to be read (robots.txt respected, a time limit), takes what the pages state
outright (name, site name, contact details, links, technologies) with no model, and stores those
facts at once. Pass 2 hands the pages read to a model in one batched call (OperateIQ's
website-analysis prompt for every page, and the website-inference reading) and stores what it adds,
under its own time limit. Each fact is one pending proposal: ``ai_chat_crud_approvals`` rows with ``source_table = "website_crawler"``,
the same store the GitHub connector's proposals use. Nothing is written to the model or
to the organisation's settings; a person confirming a proposal is a later step.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
from urllib.parse import urldefrag, urljoin, urlparse

from app import db

from . import prompts
from .extraction import extract_static, same_site, social_platform, social_profile_root, trimmed_html
from .fetcher import Browser, FetchRefused, Fetcher
from .rendering import needs_browser, parse_json

logger = logging.getLogger(__name__)

WEBSITE_TASK = "website_profile_crawl"
WEBSITE_RETRY_TASK = "website_profile_crawl_more"
# when the model pass found every provider busy: one more try in 15 minutes, and one in an hour
RETRY_DELAYS_SECONDS = (15 * 60, 60 * 60)
PROPOSAL_SOURCE = "website_crawler"
OP_PROFILE_FACT = "website_profile_fact"
PROPOSAL_DUE_DAYS = 14
MAX_PAGES = 30
TIME_LIMIT_SECONDS = 120.0  # each pass has its own
MODEL_TIMEOUT_SECONDS = 120.0  # the one batched model call
PROMPT_PAGE_CHARS = 15000  # the pages handed to the model in one call, shared between them; one call stays under 40,000 characters
MODEL_PAGES = 9  # the pages the model pass reads: the home page and the first pages after it
MODEL_PAGES_PER_CALL = 3
DEFAULT_CONFIDENCE = 0.3  # the floor of OperateIQ's own scale ("weak inference") when a model gives none

TAXONOMY = (
    "Technology & Software", "E-commerce & Retail", "Professional Services", "Healthcare & Life Sciences",
    "Financial Services", "Education & Training", "Media & Entertainment", "Consumer & Lifestyle",
    "Real Estate & Construction", "Transportation & Logistics", "Manufacturing & Industrial",
    "Non-Profit & Government",
)

# field -> label shown to the person who confirms it, in the order a profile reads
PROFILE_FIELDS: Dict[str, str] = {
    "company_name": "Company name", "tagline": "Tagline", "founded_year": "Founded", "contact_email": "Contact email",
    "contact_phone": "Contact phone", "logo": "Logo", "linkedin_url": "LinkedIn",
    "category": "Category", "business_model": "Business model", "revenue_model": "Revenue model",
    "industry_sector": "Industry sector", "target_market": "Target market", "market_segment": "Market segment",
    "geographic_focus": "Geographic focus", "hq_country": "Headquarters country", "hq_city": "Headquarters city",
    "hq_region": "Headquarters region", "hq_address": "Headquarters address", "team_size": "Team size",
    "company_stage": "Company stage", "key_person": "Key person", "social_profile": "Social profile",
    "technology": "Technology", "product": "Product", "value_proposition": "Value proposition",
    "competitive_advantage": "Competitive advantage", "market_position": "Market position",
    "compliance": "Compliance mention", "integration_partner": "Integration partner", "case_study": "Case study",
    "completeness_score": "Completeness of what was read",
}
_MANY = {"key_person", "social_profile", "technology", "product", "value_proposition", "competitive_advantage",
         "compliance", "integration_partner", "case_study"}

ADDENDUM = """

## Additional output keys (Entelim)
Also add these keys to the JSON object, using only what this page states and empty lists or null otherwise:
- "products": ["product or service names"]
- "value_propositions": ["what the company says it does better"]
- "compliance": ["compliance or certification mentions, e.g. ISO 27001"]
- "integration_partners": ["named integration partners"]
- "case_studies": ["one short line per customer case study"]
- "field_confidence": {"<field>": 0.0-1.0} for each non-null scalar field you filled
  (category, business_model, industry_sector, target_market), on the scale above.
"""


class CrawlRefused(ValueError):
    """The website was not read. ``reason`` is why, as a code the sign-up page turns into a sentence:
    no_site, no_consent, blocked, robots, no_answer, script_only or nothing_found."""

    def __init__(self, message: str, reason: str = "no_answer", status: Optional[int] = None):
        super().__init__(message)
        self.reason = reason
        self.status = status


# ----------------------------------------------------------------------------- model

def default_model(timeout: float = MODEL_TIMEOUT_SECONDS) -> Callable[[str], str]:
    """The model call: the organisation's own provider first, then the free ones, through the
    one provider chain the assistant uses. The call is given ``timeout`` seconds."""
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    def ask(prompt: str) -> str:
        # the pages are public text, so free providers may read them
        # room for the answer about three pages: 4,096 tokens cut the JSON off in the middle
        return LLMService.generate_from_prompt(prompt, use_cache=False, timeout=timeout, public_input=True, max_tokens=8192)

    return ask


def _scoped(model: Callable[[str], str], org_id: int) -> Callable[[str], str]:
    from app.jobs.tenant_safe_job import tenant_scope

    def ask(prompt: str) -> str:
        with tenant_scope(org_id):
            return model(prompt)

    return ask


# ----------------------------------------------------------------------------- consent

def _consented_site(org_id: int) -> Tuple[Any, str]:
    from app.models.organization import Organization
    from app.utils.ssrf_guard import BlockedOutboundURL, validate_user_supplied_url

    org = db.session.get(Organization, org_id)
    if org is None:
        raise CrawlRefused("That organisation does not exist.", "no_site")
    site = ((org.settings or {}).get("onboarding") or {}).get("website") or {}
    url, consented = site.get("url"), site.get("consent_at")
    if not url:
        raise CrawlRefused("No company website is recorded, so there is nothing to read.", "no_site")
    if not consented:
        raise CrawlRefused("The website has not been agreed to be read. Record consent first.", "no_consent")
    try:
        validate_user_supplied_url(url)
    except BlockedOutboundURL:
        raise CrawlRefused("That website address cannot be read from here.", "blocked")
    return org, url


# ----------------------------------------------------------------------------- reading pages

BATCH_ADDENDUM = """

## This request holds several pages (Entelim)
The HTML above is not one page: it is several pages of the same website, each introduced by a line
"=== PAGE <n>: <address> ===". Read each page on its own and return ONE JSON object of this shape:
{"pages": [{"url": "<the page address, exactly as given>", <all the keys above, for that page only>}, ...],
 "inference": {<the business-level reading below>}}
Use only what each page states. Give a page you learned nothing from an empty object.

### The "inference" object
- "business_model": {"revenue_model": "", "target_market": "", "market_segment": "", "geographic_focus": "", "confidence": 0.0-1.0}
- "company_stage": {"stage": "", "confidence": 0.0-1.0}
- "technology_stack": {"<kind, e.g. frontend>": ["names"]}
- "competitive_positioning": {"value_proposition": "", "competitive_advantages": [""], "market_position": ""}
- "data_quality": {"completeness_score": 0.0-1.0, "reliability_score": 0.0-1.0}
"""


PAGES_ONLY_ADDENDUM = BATCH_ADDENDUM[:BATCH_ADDENDUM.index("### The \"inference\" object")].replace(
    ',\n "inference": {<the business-level reading below>}}', "}")


def _batched_prompt(pages: List[Dict[str, Any]], with_inference: bool = True) -> str:
    """One prompt for a few pages: OperateIQ's analysis prompt over them, then (once per read) the inference."""
    share = max(1000, min(12000, PROMPT_PAGE_CHARS // max(1, len(pages))))
    body = "\n\n".join(
        "=== PAGE %d: %s ===\n%s" % (i, page["url"], trimmed_html(page["html"] or "", share))
        for i, page in enumerate(pages, 1))
    first = pages[0]
    return prompts.render("website_analysis", {"url": first["url"], "title": first["static"]["title"],
                                               "html_content": body}) + ADDENDUM + (BATCH_ADDENDUM if with_inference else PAGES_ONLY_ADDENDUM)


def _model_chunks(pages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    selected = pages[:MODEL_PAGES]
    return [selected[i:i + MODEL_PAGES_PER_CALL] for i in range(0, len(selected), MODEL_PAGES_PER_CALL)]


def _no_model(prompt: str) -> str:
    raise RuntimeError("pass 1 reads without a model")


def _tell(progress: Optional[Callable[[Dict[str, Any]], None]], **state: Any) -> None:
    """Report how far the reading has got. A reporter that fails never stops the reading."""
    if progress is None:
        return
    try:
        progress(state)
    except Exception:
        logger.info("crawl progress could not be reported", exc_info=True)


def _read_site(site: str, max_pages: int, time_limit: float, delay: float,
               progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """Pass 1: fetch the pages and take what they state outright. No model is called."""
    started = time.monotonic()
    fetcher = Fetcher(site)
    browser = Browser(site)
    result: Dict[str, Any] = {"pages": [], "rendered": [], "unrendered": [], "skipped_by_robots": [], "failed": [],
                              "stopped": None, "model_errors": 0}
    queue: List[str] = [site]
    queued = {site}
    attempts = 0
    try:
        while queue and attempts < max_pages:
            remaining = time_limit - (time.monotonic() - started)
            if remaining <= 0:
                result["stopped"] = "time_limit"
                break
            url = queue.pop(0)
            if not fetcher.allowed(url):
                result["skipped_by_robots"].append(url)
                continue
            if attempts and (delay or fetcher.crawl_delay):
                time.sleep(max(delay, fetcher.crawl_delay))
            attempts += 1
            _tell(progress, stage="reading", pages_read=len(result["pages"]), limit=max_pages, current=url)
            try:
                page = fetcher.get(url, timeout=remaining)
            except (FetchRefused, requests.RequestException) as exc:
                result["failed"].append({"url": url, "reason": str(exc)[:200]})
                continue
            if page.status >= 400 or "html" not in page.content_type.lower():
                result["failed"].append({"url": url, "reason": "status %s" % page.status})
                continue
            html, final_url = page.text, page.url
            if needs_browser(final_url, html, page.status, _no_model):
                try:
                    rendered = browser.render(final_url, max(1.0, min(30.0, time_limit - (time.monotonic() - started))))
                    html = rendered.text
                    result["rendered"].append(final_url)
                except Exception as exc:  # the static page is still read
                    logger.warning("browser render failed for %s: %s", final_url, exc)
                    result["unrendered"].append(final_url)
            fetched_at = datetime.utcnow().isoformat(timespec="seconds")
            static = extract_static(final_url, html, site)
            result["pages"].append({"url": final_url, "fetched_at": fetched_at, "static": static,
                                    "analysis": {}, "html": html})
            _tell(progress, stage="reading", pages_read=len(result["pages"]), limit=max_pages, current=final_url)
            for link in static["links"]:
                link = urldefrag(urljoin(final_url, link))[0]
                if link not in queued and same_site(link, site):
                    queued.add(link)
                    queue.append(link)
        if queue and attempts >= max_pages and not result["stopped"]:
            result["stopped"] = "page_limit"
    finally:
        browser.close()
    result["robots_unanswered"] = fetcher.robots_unanswered
    return result


# ----------------------------------------------------------------------------- facts

def _norm(value: Any) -> str:
    return " ".join(str(value).lower().split())


def _number(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if 0.0 <= number <= 1.0 else default


def _text(value: Any) -> Optional[str]:
    if isinstance(value, (dict, list)) or value is None:
        return None
    text = str(value).strip()
    return text or None


class _Facts:
    """Every fact read, one best entry per (field, value), each with where it came from."""

    def __init__(self):
        self.rows: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.scalar: Dict[str, Tuple[str, str]] = {}

    def add(self, field: str, value: Any, url: str, fetched_at: str, confidence: Optional[float], basis: str):
        if value in (None, "", [], {}):
            return
        confidence_value = _number(confidence)
        if confidence_value is None:
            confidence_value, basis = DEFAULT_CONFIDENCE, "default"
        key = (field, _norm(value) if not isinstance(value, dict) else _norm(sorted(value.items())))
        if field not in _MANY:
            held = self.scalar.get(field)
            if held and held != key:
                if self.rows[held]["confidence"] >= confidence_value:
                    return
                del self.rows[held]
            self.scalar[field] = key
        existing = self.rows.get(key)
        if existing and existing["confidence"] >= confidence_value:
            return
        self.rows[key] = {"field": field, "label": PROFILE_FIELDS[field], "value": value, "source_url": url,
                          "fetched_at": fetched_at, "confidence": round(confidence_value, 2),
                          "confidence_basis": basis}

    def ordered(self) -> List[Dict[str, Any]]:
        order = {name: i for i, name in enumerate(PROFILE_FIELDS)}
        return sorted(self.rows.values(), key=lambda row: (order[row["field"]], -row["confidence"]))


def _category(value: Any) -> Optional[str]:
    wanted = _norm(str(value or "").replace("&", "and"))
    for family in TAXONOMY:
        if _norm(family.replace("&", "and")) == wanted:
            return family
    return None


def _from_analysis(facts: _Facts, url: str, at: str, a: Dict[str, Any]) -> None:
    confidences = a.get("field_confidence") if isinstance(a.get("field_confidence"), dict) else {}
    company = a.get("company") if isinstance(a.get("company"), dict) else {}
    add = lambda field, value, conf, basis="model": facts.add(field, value, url, at, conf, basis)  # noqa: E731
    add("company_name", _text(company.get("name")), confidences.get("company_name"))
    add("tagline", _text(company.get("description")), confidences.get("description"))
    founded = company.get("founded_year")
    if isinstance(founded, (int, float)) and 1800 <= founded <= datetime.utcnow().year:
        add("founded_year", int(founded), confidences.get("founded_year"))
    for key, field in (("email", "contact_email"), ("phone", "contact_phone"), ("logo", "logo"),
                       ("linkedin_url", "linkedin_url")):
        add(field, _text(company.get(key)), confidences.get(key))
    add("category", _category(a.get("category")), confidences.get("category"))
    for key in ("business_model", "industry_sector", "target_market"):
        add(key, _text(a.get(key)), confidences.get(key))
    location = a.get("location") if isinstance(a.get("location"), dict) else {}
    for key, field in (("country", "hq_country"), ("city", "hq_city"), ("region", "hq_region"), ("address", "hq_address")):
        add(field, _text(location.get(key)), confidences.get("location") or confidences.get(key))
    size = a.get("team_size") if isinstance(a.get("team_size"), dict) else {}
    if size.get("min") is not None or size.get("max") is not None:
        add("team_size", {"min": size.get("min"), "max": size.get("max")}, size.get("confidence"))
    for tech in a.get("technologies") or []:
        if isinstance(tech, dict) and _text(tech.get("name")):
            add("technology", {"name": _text(tech["name"]), "type": _text(tech.get("type"))}, tech.get("confidence"))
    for person in a.get("people") or []:
        if isinstance(person, dict) and _text(person.get("name")):
            add("key_person", {"name": _text(person["name"]), "title": _text(person.get("title"))},
                person.get("confidence"))
    for key, field in (("products", "product"), ("value_propositions", "value_proposition"),
                       ("compliance", "compliance"), ("integration_partners", "integration_partner"),
                       ("case_studies", "case_study")):
        for entry in a.get(key) or []:
            add(field, _text(entry), confidences.get(key))


def _from_static(facts: _Facts, url: str, at: str, s: Dict[str, Any]) -> None:
    for field in ("company_name", "tagline", "founded_year", "contact_email", "contact_phone", "logo", "linkedin_url"):
        if field in s:
            facts.add(field, s[field]["value"], url, at, s[field]["confidence"], "page")
    for tech in s["technologies"]:
        facts.add("technology", {"name": tech["name"], "type": tech["type"]}, url, at, tech["confidence"], "page")


def _from_inference(facts: _Facts, url: str, at: str, inferred: Dict[str, Any]) -> None:
    quality = inferred.get("data_quality") if isinstance(inferred.get("data_quality"), dict) else {}
    reliability = _number(quality.get("reliability_score"))
    business = inferred.get("business_model") if isinstance(inferred.get("business_model"), dict) else {}
    confidence = business.get("confidence")
    for key, field in (("revenue_model", "revenue_model"), ("market_segment", "market_segment"),
                       ("geographic_focus", "geographic_focus")):
        facts.add(field, _text(business.get(key)), url, at, confidence, "model")
    if not any(r["field"] == "target_market" for r in facts.rows.values()):
        facts.add("target_market", _text(business.get("target_market")), url, at, confidence, "model")
    stage = inferred.get("company_stage") if isinstance(inferred.get("company_stage"), dict) else {}
    facts.add("company_stage", _text(stage.get("stage")), url, at, stage.get("confidence"), "model")
    stack = inferred.get("technology_stack") if isinstance(inferred.get("technology_stack"), dict) else {}
    for kind, names in stack.items():
        for name in names if isinstance(names, list) else []:
            if _text(name):
                facts.add("technology", {"name": _text(name), "type": str(kind)}, url, at, reliability, "reliability")
    position = inferred.get("competitive_positioning")
    position = position if isinstance(position, dict) else {}
    facts.add("value_proposition", _text(position.get("value_proposition")), url, at, reliability, "reliability")
    for advantage in position.get("competitive_advantages") or []:
        facts.add("competitive_advantage", _text(advantage), url, at, reliability, "reliability")
    facts.add("market_position", _text(position.get("market_position")), url, at, reliability, "reliability")
    score = _number(quality.get("completeness_score"))
    if score is not None:
        facts.add("completeness_score", score, url, at, reliability, "reliability")


# ----------------------------------------------------------------------------- the store

def _display(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join("%s" % v for v in value.values() if v not in (None, ""))
    return str(value)


def _store(org_id: int, facts: List[Dict[str, Any]], crawl_id: str, replace: bool = True) -> int:
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    if replace:
        for old in AIChatCRUDApproval.query.filter_by(
            organization_id=org_id, source_table=PROPOSAL_SOURCE, status=ApprovalStatus.PENDING
        ).all():
            old.status = ApprovalStatus.EXPIRED
            old.rejected_reason = "Replaced by a newer reading of the website."
    for fact in facts:
        create_approval_record(
            organization_id=org_id,
            operation_type=OP_PROFILE_FACT,
            entity_type="organization_profile",
            summary="%s: %s (read from %s)" % (fact["label"], _display(fact["value"]), fact["source_url"]),
            operation_payload={**fact, "crawl_id": crawl_id},
            source_table=PROPOSAL_SOURCE,
            expiry_minutes=PROPOSAL_DUE_DAYS * 24 * 60,
        )
    db.session.commit()
    return len(facts)


_HOME_NAME_BREAK = re.compile(r"[|•·–—\-\u2013\u2014\u00b7\u2022]\s*(?:Home|Welcome|Official|Website|Site|\w+\.\w+).*$")


_TITLE_SPLIT = re.compile(r"\s+[|\u2013\u2014-]\s+")  # " | ", " - ", " \u2013 ", " \u2014 "
TITLE_MATCHED_CONFIDENCE = 0.9  # a title part that is the site's own address or declared site name
TITLE_FIRST_PART_CONFIDENCE = 0.7  # a short first part before a separator, with nothing to match it against


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _domain_names(url: str) -> set:
    """What a company's own web address spells: ``cal.com`` is "calcom" and "cal"; ``acme-robotics.test`` is "acmerobotics"."""
    host = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower() if url else ""
    host = host[4:] if host.startswith("www.") else host
    labels = host.split(".")
    names = {_alnum(host)}
    if len(labels) >= 2:
        label = labels[-3] if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "gov", "ac") else labels[-2]
        names.add(_alnum(label))
    return {n for n in names if n and not n.isdigit()}


def _name_from_title(title: str, url: str, site_name: Optional[str] = None) -> Optional[Tuple[str, Optional[float]]]:
    """The brand in a home-page title such as "Cal.com | Scheduling Software for Online Meetings".

    The title is split on " | ", " \u2013 ", " - " and " \u2014 ". The part that is the site's address or its declared
    site name is the name (high confidence). With no such part, the first part of a title that has a separator
    and is short enough to be a name is offered (lower confidence, still ticked at sign-up). None otherwise."""
    parts = [p.strip() for p in _TITLE_SPLIT.split(title.strip()) if p.strip()]
    if len(parts) < 2:
        only = parts[0] if parts else ""
        if only and _alnum(only) in (_domain_names(url) | ({_alnum(site_name)} if site_name else set())):
            return only, TITLE_MATCHED_CONFIDENCE
        return None
    known = _domain_names(url) | ({_alnum(site_name)} if site_name else set())
    for part in parts:
        if _alnum(part) in known:
            return part, TITLE_MATCHED_CONFIDENCE
    if len(parts[0].split()) <= 4:
        return parts[0], TITLE_FIRST_PART_CONFIDENCE
    return None


def _unmatched_cap(value: str, confidence: Optional[float], url: str, site_name: Optional[str] = None) -> Optional[float]:
    """A name that is not the site's address is at most TITLE_FIRST_PART_CONFIDENCE sure, whatever the page says
    about itself (structured data and og:site_name both say "Ifiok" on archiet.dev, and neither is the address)."""
    known = _domain_names(url)
    mine = _alnum(value)
    if mine in known or any(mine.startswith(k) and len(k) >= 3 for k in known):
        return confidence
    return min(TITLE_FIRST_PART_CONFIDENCE, confidence if confidence is not None else TITLE_FIRST_PART_CONFIDENCE)


def _read_home_name(first_page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The company name from the home page: ``og:site_name``, organisation name in JSON-LD,
    or the page title, with the suffix after a separator (|, -, —, etc.) dropped.

    Returns ``{"value", "confidence", "basis"}`` when a name was read, ``None`` when the
    home page gave no name at all (not even a title), or a dict with ``"confidence": None``
    when there is a candidate but the page declares no confidence."""
    static = first_page.get("static") or {}
    # site_name from meta or organisation name from JSON-LD
    name = static.get("company_name")
    if name:
        value = _HOME_NAME_BREAK.sub("", str(name["value"]).strip()).strip()
        if value:
            return {"value": value, "basis": "page",
                    "confidence": _unmatched_cap(value, name["confidence"], first_page.get("url") or "", static.get("site_name"))}
    # title as fallback
    title = static.get("title") or ""
    if title.strip():
        brand = _name_from_title(title, first_page.get("url") or "", (static.get("company_name") or {}).get("value"))
        if brand:
            return {"value": brand[0], "confidence": brand[1], "basis": "page"}
        value = _HOME_NAME_BREAK.sub("", title.strip()).strip()
        if value and static.get("text_length", 0) > 200:
            return {"value": value, "confidence": None, "basis": "page"}
    # model analysis of the home page as last resort
    analysis = first_page.get("analysis") or {}
    company = analysis.get("company") if isinstance(analysis.get("company"), dict) else {}
    model_name = _text(company.get("name"))
    if model_name:
        value = _HOME_NAME_BREAK.sub("", model_name).strip()
        if value:
            confidences = analysis.get("field_confidence") if isinstance(analysis.get("field_confidence"), dict) else {}
            return {"value": value, "basis": "model",
                    "confidence": _unmatched_cap(value, _number(confidences.get("company_name")), first_page.get("url") or "",
                                                 static.get("site_name"))}
    return None


def _home_page_company_name(first_page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The company name of the home page, from whichever part of it gave one, never more than 0.7 sure unless it is
    the site's own address. This is the one place the proposal's confidence is settled."""
    got = _read_home_name(first_page)
    if got and got["confidence"] is not None:
        got["confidence"] = _unmatched_cap(got["value"], got["confidence"], first_page.get("url") or "")
    return got


def _replace_company_name(facts: _Facts, name: str, url: str, at: str, confidence: Optional[float],
                          basis: str) -> None:
    """Replace any company_name fact with one from the home page. Confidence that is None
    means the page gave no confidence, so it falls to the default floor."""
    # Remove every existing company_name row
    keys = [k for k in facts.rows if k[0] == "company_name"]
    for k in keys:
        del facts.rows[k]
    facts.scalar.pop("company_name", None)
    # Only add when we have a genuine name
    if name:
        facts.add("company_name", name, url, at, confidence, basis)


def _cap_company_name_confidence(facts: _Facts, home_url: str) -> None:
    """When the home page gave no name at all, lower the confidence of whatever name was
    found on another page, so it is never reported at 100%."""
    for key, row in list(facts.rows.items()):
        if key[0] != "company_name":
            continue
        if row.get("source_url") == home_url:
            continue
        if row["confidence"] > DEFAULT_CONFIDENCE:
            row["confidence"] = DEFAULT_CONFIDENCE
            row["confidence_basis"] = "default"


def _add_social_profiles(facts: _Facts, pages: List[Dict[str, Any]]) -> None:
    """At most one social profile per network, each the company's own account at its profile address.

    A candidate is a profile root on a known network. One linked from the site's header or footer (or its
    structured data) is the company's; any other must carry a handle that spells the company's name or web
    address. When several qualify the header/footer link wins, then the home page's, then the first found."""
    site = pages[0]["url"]
    names = set(_domain_names(site))
    name_row = next((r for r in facts.rows.values() if r["field"] == "company_name"), None)
    if name_row:
        names.add(_alnum(str(name_row["value"])))
    names.discard("")
    ranked: List[Tuple[Tuple[int, int, int], Dict[str, Any], str]] = []
    order = 0
    for page in pages:
        found = [(i["url"], i["handle"], i["chrome"], "page") for i in page["static"].get("social", [])]
        for item in (page.get("analysis") or {}).get("social_media") or []:
            root = social_profile_root(str(item.get("url") or "")) if isinstance(item, dict) else None
            if root:
                found.append((root[1], root[2], False, "model"))
        for address, handle, chrome, source in found:
            spelled = _alnum(handle)
            matches = any(n and (n == spelled or (len(n) > 3 and (n in spelled or spelled in n))) for n in names) \
                if spelled else False
            if not (chrome and source == "page") and not matches:
                continue
            home = same_site(page["url"], site) and urlparse(page["url"]).path in ("", "/")
            order += 1
            ranked.append(((0 if chrome and source == "page" else 1, 0 if home else 1, order), page, address))
    best: Dict[str, Tuple[Tuple[int, int, int], Dict[str, Any], str]] = {}
    for rank, page, address in sorted(ranked, key=lambda r: r[0]):
        platform = social_platform(address)
        best.setdefault(platform, (rank, page, address))
    for platform, (_, page, address) in best.items():
        facts.add("social_profile", {"platform": platform, "url": address}, page["url"], page["fetched_at"], 1.0,
                  "page")


def _row_key(row: Dict[str, Any]) -> str:
    value = row["value"]
    return _norm(value) if not isinstance(value, dict) else _norm(sorted(value.items()))


def _facts_from(pages: List[Dict[str, Any]]) -> _Facts:
    """Every fact the pages give: what they state and, once the model has read them, what it took from them.

    The company name comes from the home page, not a sub-page whose title or heading may differ
    (e.g. "Cal.com Docs" read from the docs page): the home page's site name or title, with the suffix
    after a separator ("Acme | Home" becomes "Acme") dropped. When the home page gives no name at all the
    confidence of any other name is lowered to the default floor rather than reported at 100%."""
    facts = _Facts()
    for page in pages:
        _from_static(facts, page["url"], page["fetched_at"], page["static"])
        _from_analysis(facts, page["url"], page["fetched_at"], page["analysis"])
    first = pages[0]
    home_name = _home_page_company_name(first)
    if home_name:
        _replace_company_name(facts, home_name["value"], first["url"], first["fetched_at"],
                              home_name["confidence"], home_name["basis"])
    else:
        _cap_company_name_confidence(facts, first["url"])
    _add_social_profiles(facts, pages)
    return facts


def _decided_or_pending_proposals(org_id: int) -> List[Dict[str, Any]]:
    """Every fact read from the website that is waiting or that a person has confirmed or left out.
    A retry of the model pass proposes none of them again."""
    import json

    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    rows = AIChatCRUDApproval.query.filter(
        AIChatCRUDApproval.organization_id == org_id, AIChatCRUDApproval.source_table == PROPOSAL_SOURCE,
        AIChatCRUDApproval.operation_type == OP_PROFILE_FACT,
        AIChatCRUDApproval.status.in_([ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.REJECTED]),
    ).all()
    return [json.loads(row.operation_payload or "{}") for row in rows]


def pending_website_proposals(org_id: int) -> List[Dict[str, Any]]:
    """The profile facts read from the organisation's website that wait for a person, in profile order."""
    import json

    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    rows = AIChatCRUDApproval.query.filter_by(
        organization_id=org_id, source_table=PROPOSAL_SOURCE, operation_type=OP_PROFILE_FACT,
        status=ApprovalStatus.PENDING,
    ).order_by(AIChatCRUDApproval.id).all()
    order = {name: i for i, name in enumerate(PROFILE_FIELDS)}
    out = []
    for row in rows:
        payload = json.loads(row.operation_payload or "{}")
        out.append({"id": row.id, **payload})
    return sorted(out, key=lambda r: (order.get(r.get("field"), 99), -r.get("confidence", 0), r["id"]))


# ----------------------------------------------------------------------------- entry points

def crawl_organization_website(
    org_id: int,
    *,
    llm: Optional[Callable[[str], str]] = None,
    max_pages: int = MAX_PAGES,
    time_limit_seconds: float = TIME_LIMIT_SECONDS,
    delay_seconds: float = 0.0,
    progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    more_only: bool = False,
    retry_attempt: int = 0,
    defer_model_pass: bool = False,
) -> Dict[str, Any]:
    """Read the organisation's consented website and store what was found as pending proposals.

    ``progress`` is told how far the reading has got (pages read, the limit, the page being read).
    Raises CrawlRefused, with a reason code, when there is no consented site or nothing could be
    read or found. Returns what was read, not the pages.
    """
    org, site = _consented_site(org_id)
    model = _scoped(llm or default_model(timeout=float(time_limit_seconds)), org_id)
    max_pages = max(1, min(int(max_pages), MAX_PAGES))
    _tell(progress, stage="reading", pages_read=0, limit=max_pages, current=site)
    read = _read_site(site, max_pages, float(time_limit_seconds), float(delay_seconds), progress)
    if not read["pages"] and more_only:
        raise CrawlRefused("Nothing could be read from %s on the retry." % site, "no_answer")
    if not read["pages"]:
        reason = "; ".join("%s (%s)" % (f["url"], f["reason"]) for f in read["failed"][:3])
        if read["skipped_by_robots"] and not read["failed"] and not read["robots_unanswered"]:
            raise CrawlRefused("Nothing could be read from %s: the site's robots.txt excludes it." % site, "robots")
        status = next((int(f["reason"].split()[1]) for f in read["failed"]
                       if f["reason"].startswith("status ") and f["reason"].split()[1].isdigit()), None)
        raise CrawlRefused("Nothing could be read from %s: %s." % (site, reason or "it did not answer"),
                           "no_answer", status)
    first = read["pages"][0]
    crawl_id = uuid.uuid4().hex
    from app.jobs.tenant_safe_job import tenant_scope

    # Pass 1 facts: what the pages state outright, proposed now so the person sees them without waiting.
    facts = _facts_from(read["pages"])
    stored = 0
    if more_only:
        # a retry of the model pass: what is already proposed stays, and only what is new is added
        with tenant_scope(org_id):
            existing = _decided_or_pending_proposals(org_id)
        held = {(row["field"], _row_key(row)) for row in existing}
        held_scalars = {row["field"] for row in existing if row["field"] not in _MANY}
    else:
        if facts.rows:
            with tenant_scope(org_id):
                stored = _store(org_id, facts.ordered(), crawl_id)
        held = {(row["field"], _row_key(row)) for row in facts.rows.values()}
        held_scalars = {row["field"] for row in facts.rows.values() if row["field"] not in _MANY}
    if not more_only:
        _detect_vendors(org_id, site, read["pages"])
    if not more_only:
        _detect_vendors(org_id, site, read["pages"])
    _tell(progress, stage="facts_ready" if stored else "working_out", pages_read=len(read["pages"]),
          limit=max_pages, current=None)

    if defer_model_pass and not more_only and stored:
        # The fast facts are in and proposed. The model pass is its own job, after this one, so it never holds the
        # worker while another organisation's first read waits.
        _queue_model_pass(org_id)
        return {
            "retry_queued": None, "model_pass_queued": True, "model_error": None, "added": 0,
            "crawl_id": crawl_id, "site": site, "pages_read": len(read["pages"]),
            "rendered": read["rendered"], "skipped_by_robots": read["skipped_by_robots"], "failed": read["failed"],
            "stopped": read["stopped"], "model_errors": 0, "proposals": stored,
        }

    # Pass 2: the model reads the pages a few at a time (the first call also gives the business-level reading);
    # what it adds is proposed when it arrives.
    inferred: Dict[str, Any] = {}
    model_error: Optional[Dict[str, str]] = None
    for number, chunk in enumerate(_model_chunks(read["pages"])):
        try:
            answer = parse_json(model(_batched_prompt(chunk, with_inference=(number == 0))))
            entries = [entry for entry in (answer.get("pages") or []) if isinstance(entry, dict)]
            by_url = {str(entry.get("url")): entry for entry in entries}
            for position, page in enumerate(chunk):
                entry = by_url.get(page["url"])
                if entry is None and len(entries) == len(chunk):
                    entry = entries[position]
                page["analysis"] = entry or {}
            if number == 0:
                inferred = answer.get("inference") if isinstance(answer.get("inference"), dict) else {}
        except Exception as exc:
            # the type and a short message go on the job's result, so a failed model pass is visible without the log
            model_error = model_error or {"type": type(exc).__name__, "message": str(exc)[:200]}
            logger.warning("website reading by the model failed for organisation %s (pages %d to %d): %s: %s",
                           org_id, number * MODEL_PAGES_PER_CALL + 1, number * MODEL_PAGES_PER_CALL + len(chunk),
                           type(exc).__name__, str(exc)[:200], exc_info=True)
            read["model_errors"] += 1
            if not isinstance(exc, ValueError):  # a timeout or a busy provider is tried again later; a bad answer is not
                read["retry_later"] = True
            if number == 0:
                break  # the providers are failing: the rest are not asked, and the pass is tried again later
    facts = _facts_from(read["pages"])
    _from_inference(facts, first["url"], first["fetched_at"], inferred)
    added = [row for row in facts.ordered()
             if (row["field"], _row_key(row)) not in held and row["field"] not in held_scalars]
    if added:
        with tenant_scope(org_id):
            stored += _store(org_id, added, crawl_id, replace=False)
    retry_queued = None
    if read.get("retry_later") and retry_attempt < len(RETRY_DELAYS_SECONDS):
        retry_queued = _queue_retry(org_id, retry_attempt + 1)
    if not stored and not more_only:
        raise CrawlRefused(
            "%d page(s) of %s were read and no fact could be taken from them." % (len(read["pages"]), site),
            "script_only" if read["unrendered"] else "nothing_found")
    return {
        "retry_queued": retry_queued, "model_pass_queued": False, "model_error": model_error,
        "added": len(added) if added else 0,
        "crawl_id": crawl_id, "site": site, "pages_read": len(read["pages"]),
        "rendered": read["rendered"], "skipped_by_robots": read["skipped_by_robots"], "failed": read["failed"],
        "stopped": read["stopped"], "model_errors": read["model_errors"], "proposals": stored,
    }


def _detect_vendors(org_id: int, site: str, pages: List[Dict[str, Any]]) -> None:
    """The vendors the pages load and the site's domain records name, proposed for a person to confirm.
    A failure here never stops the reading of the website."""
    from flask import current_app

    from app.jobs.tenant_safe_job import tenant_scope
    from app.services.vendor_detection import detect_for_site

    resolver = None if current_app.config.get("VENDOR_DNS_LOOKUPS", True) else (lambda _name, _type: [])
    try:
        with tenant_scope(org_id):
            detect_for_site(org_id, site, pages, resolver)
    except Exception:
        db.session.rollback()
        logger.warning("vendor detection failed for organisation %s", org_id, exc_info=True)


def _queue_model_pass(org_id: int) -> None:
    """Queue the model pass for one organisation as its own job, unless one is already waiting."""
    from app.services.job_queue_service import get_job_queue_service

    queue = get_job_queue_service()
    waiting = queue.list_jobs(org_id, limit=5, task=WEBSITE_RETRY_TASK, statuses=["pending"])
    if not waiting:
        queue.create_job("Work out what the company website says", WEBSITE_RETRY_TASK,
                         {"retry_attempt": 0}, organization_id=org_id)


def _queue_retry(org_id: int, attempt: int) -> str:
    """Queue the model pass to run again later: attempt 1 in 15 minutes, attempt 2 in an hour.
    Returns when it is due (UTC, ISO)."""
    from app.services.job_queue_service import get_job_queue_service

    due = (datetime.utcnow() + timedelta(seconds=RETRY_DELAYS_SECONDS[attempt - 1])).isoformat(timespec="seconds")
    get_job_queue_service().create_job(
        "Read the company website again", WEBSITE_RETRY_TASK,
        {"retry_attempt": attempt, "not_before": due}, organization_id=org_id)
    return due


def queue_website_crawl(org_id: int):
    """Queue the reading as a background job for one organisation (refused unless it consented)."""
    from app.services.job_queue_service import get_job_queue_service

    _consented_site(org_id)
    return get_job_queue_service().create_job(
        "Read the company website", WEBSITE_TASK, {}, organization_id=org_id)


def _job_progress(job_id: Optional[int]) -> Optional[Callable[[Dict[str, Any]], None]]:
    """A reporter that keeps how far the reading has got on the job's own record, which the sign-up
    page polls. It holds counts and the address of the page being read, never what the page says."""
    if job_id is None:
        return None

    def write(state: Dict[str, Any]) -> None:
        from app.models.job import Job

        job = db.session.get(Job, job_id)
        if job is None:
            return
        job.result = {"progress": {key: state.get(key) for key in ("stage", "pages_read", "limit", "current")}}
        db.session.commit()

    return write


def _tell_the_bell(org_id: int) -> None:
    """Post to the notifications bell of the person who gave the website that its facts are ready to
    confirm, with a link to the screen where they do. Failing to post never fails the reading."""
    from flask import current_app

    from app.jobs.tenant_safe_job import tenant_scope
    from app.models.organization import Organization
    from app.services.notification_service import NotificationService

    from .confirm import proposals

    try:
        with tenant_scope(org_id):
            org = db.session.get(Organization, org_id)
            site = (((org.settings or {}).get("onboarding") or {}).get("website") or {}) if org else {}
            user_id = site.get("consent_by")
            facts, _completeness = proposals(org_id)
            if not user_id or not facts:
                return
            url = current_app.url_map.bind("localhost").build("account.welcome", {"step": 3})
            NotificationService.create(
                user_id,
                "We read your website and %d %s ready for you to confirm." % (len(facts), "fact is" if len(facts) == 1 else "facts are"),
                url,
                organization_id=org_id,
                event_type="website_read",
                entity_type="organization",
                entity_id=org_id,
            )
            db.session.commit()
    except Exception:
        logger.warning("could not post the website reading to the bell for organisation %s", org_id, exc_info=True)


def run_website_retry_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The retry job body: read the site again for what the busy model pass missed, and add only that.
    Another retry is queued (once more, an hour later) if the providers are still busy."""
    from app.modules.organization import website_crawl

    org_id = payload.get("organization_id")
    if org_id is None:
        raise ValueError("%s job requires organization_id in payload" % WEBSITE_RETRY_TASK)
    try:
        summary = website_crawl.crawl_organization_website(
            org_id, llm=website_crawl.default_model(), more_only=True,
            retry_attempt=int(payload.get("retry_attempt", 1)))
    except CrawlRefused as refused:
        return {"success": False, "reason": refused.reason, "status": refused.status}
    if summary["added"]:
        _tell_the_bell(org_id)
    return {"added": summary["added"], "model_errors": summary["model_errors"], "model_error": summary["model_error"],
            "retry_queued": summary["retry_queued"], "crawl_id": summary["crawl_id"]}


def run_website_crawl_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The job body: read one organisation's site. Returns counts only, never page content.

    A site that cannot be read is an answer, not a crash: the job finishes with ``success: False``
    and the reason, which the sign-up page turns into a sentence and a next step."""
    from app.modules.organization import website_crawl

    org_id = payload.get("organization_id")
    if org_id is None:
        raise ValueError("%s job requires organization_id in payload" % WEBSITE_TASK)
    try:
        summary = website_crawl.crawl_organization_website(
            org_id, llm=website_crawl.default_model(), progress=_job_progress(payload.get("job_id")),
            defer_model_pass=True)
    except CrawlRefused as refused:
        logger.info("website not read for organisation %s: %s", org_id, refused.reason)
        return {"success": False, "reason": refused.reason, "status": refused.status}
    _tell_the_bell(org_id)
    return {
        "pages_read": summary["pages_read"], "pages_rendered": len(summary["rendered"]),
        "pages_skipped_by_robots": len(summary["skipped_by_robots"]), "pages_failed": len(summary["failed"]),
        "stopped": summary["stopped"], "model_errors": summary["model_errors"], "proposals": summary["proposals"],
        "model_error": summary["model_error"],
        "crawl_id": summary["crawl_id"], "model_pass_queued": bool(summary["model_pass_queued"]),
        "more_to_come": bool(summary["retry_queued"] or summary["model_pass_queued"]),
    }
