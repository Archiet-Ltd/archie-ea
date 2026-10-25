"""Read an organisation's consented website into profile proposals.

``crawl_organization_website`` fetches up to 30 pages of the one site the organisation
agreed to be read (robots.txt respected, a time limit), has a model read each page with
OperateIQ's website-analysis prompt, adds what the pages state outright, asks the
website-inference prompt for the business-level reading, and stores each fact as one
pending proposal: ``ai_chat_crud_approvals`` rows with ``source_table = "website_crawler"``,
the same store the GitHub connector's proposals use. Nothing is written to the model or
to the organisation's settings; a person confirming a proposal is a later step.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
from urllib.parse import urldefrag, urljoin

from app import db

from . import prompts
from .extraction import extract_static, same_site, social_platform, trimmed_html
from .fetcher import Browser, FetchRefused, Fetcher
from .rendering import needs_browser, parse_json

logger = logging.getLogger(__name__)

WEBSITE_TASK = "website_profile_crawl"
PROPOSAL_SOURCE = "website_crawler"
OP_PROFILE_FACT = "website_profile_fact"
PROPOSAL_DUE_DAYS = 14
MAX_PAGES = 30
TIME_LIMIT_SECONDS = 120.0
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

def default_model() -> Callable[[str], str]:
    """The model call: the organisation's own provider first, then the free ones, through the
    one provider chain the assistant uses."""
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    def ask(prompt: str) -> str:
        return LLMService.generate_from_prompt(prompt, use_cache=False, timeout=90)

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

def _analyse(page_url: str, html: str, title: str, hint: str, model) -> Dict[str, Any]:
    prompt = prompts.render("website_analysis", {"url": page_url, "title": title, "html_content": trimmed_html(html)})
    return parse_json(model(prompt + ADDENDUM + hint))


def _discover(page_url: str, html: str, title: str, model) -> str:
    """OperateIQ's structure-discovery step: where on this kind of page the business facts sit.
    Its answer is handed to the analysis as a hint; a failure only means no hint."""
    try:
        found = parse_json(model(prompts.render(
            "html_structure_discovery", {"url": page_url, "title": title, "html_snippet": trimmed_html(html, 30000)})))
        selectors = ((found.get("extraction_strategy") or {}).get("recommended_selectors") or [])[:10]
    except Exception:
        logger.info("structure discovery unavailable for %s", page_url, exc_info=True)
        return ""
    if not selectors:
        return ""
    lines = ["- %s (%s)" % (s.get("selector"), s.get("purpose")) for s in selectors if isinstance(s, dict)]
    return "\n\n## Where the business facts sit on this site (from structure discovery)\n" + "\n".join(lines)


def _tell(progress: Optional[Callable[[Dict[str, Any]], None]], **state: Any) -> None:
    """Report how far the reading has got. A reporter that fails never stops the reading."""
    if progress is None:
        return
    try:
        progress(state)
    except Exception:
        logger.info("crawl progress could not be reported", exc_info=True)


def _read_site(site: str, model, max_pages: int, time_limit: float, delay: float,
               progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    started = time.monotonic()
    fetcher = Fetcher(site)
    browser = Browser(site)
    result: Dict[str, Any] = {"pages": [], "rendered": [], "unrendered": [], "skipped_by_robots": [], "failed": [],
                              "stopped": None, "model_errors": 0}
    queue: List[str] = [site]
    queued = {site}
    attempts = 0
    hint = ""
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
            if needs_browser(final_url, html, page.status, model):
                try:
                    rendered = browser.render(final_url, max(1.0, min(30.0, time_limit - (time.monotonic() - started))))
                    html = rendered.text
                    result["rendered"].append(final_url)
                except Exception as exc:  # the static page is still read
                    logger.warning("browser render failed for %s: %s", final_url, exc)
                    result["unrendered"].append(final_url)
            fetched_at = datetime.utcnow().isoformat(timespec="seconds")
            static = extract_static(final_url, html, site)
            if not result["pages"]:
                hint = _discover(final_url, html, static["title"], model)
            try:
                analysis = _analyse(final_url, html, static["title"], hint, model)
            except Exception:
                logger.info("page analysis failed for %s", final_url, exc_info=True)
                analysis = {}
                result["model_errors"] += 1
            result["pages"].append({"url": final_url, "fetched_at": fetched_at, "static": static,
                                    "analysis": analysis, "html": html if not result["pages"] else None})
            _tell(progress, stage="reading", pages_read=len(result["pages"]), limit=max_pages, current=final_url)
            for link in list(static["links"]) + [item for item in (analysis.get("links") or []) if isinstance(item, str)]:
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
    for item in a.get("social_media") or []:
        if isinstance(item, dict) and _text(item.get("url")):
            platform = social_platform(item["url"]) or _text(item.get("platform")) or "Other"
            add("social_profile", {"platform": platform, "url": item["url"]}, 1.0, "page")
    for key, field in (("products", "product"), ("value_propositions", "value_proposition"),
                       ("compliance", "compliance"), ("integration_partners", "integration_partner"),
                       ("case_studies", "case_study")):
        for entry in a.get(key) or []:
            add(field, _text(entry), confidences.get(key))


def _from_static(facts: _Facts, url: str, at: str, s: Dict[str, Any]) -> None:
    for field in ("company_name", "tagline", "founded_year", "contact_email", "contact_phone", "logo", "linkedin_url"):
        if field in s:
            facts.add(field, s[field]["value"], url, at, s[field]["confidence"], "page")
    for item in s["social"]:
        facts.add("social_profile", {"platform": item["platform"], "url": item["url"]}, url, at, 1.0, "page")
    for tech in s["technologies"]:
        facts.add("technology", {"name": tech["name"], "type": tech["type"]}, url, at, tech["confidence"], "page")


def _merged_for_inference(pages: List[Dict[str, Any]], facts: _Facts) -> Dict[str, Any]:
    def values(field):
        return [r["value"] for r in facts.rows.values() if r["field"] == field]

    def one(field):
        found = values(field)
        return found[0] if found else None

    size = one("team_size")
    return {
        "company": {"name": one("company_name"), "website": pages[0]["url"], "description": one("tagline"),
                    "founded_year": one("founded_year")},
        "category": one("category"),
        "location": {"country": one("hq_country"), "city": one("hq_city"), "region": one("hq_region")},
        "technologies": values("technology"),
        "social_media": values("social_profile"),
        "team_size": size,
        "people": values("key_person"),
        "links": [p["url"] for p in pages[1:]][:20],
        "raw_html_snippet": trimmed_html(pages[0]["html"] or "", 6000) if pages[0].get("html") else None,
    }


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


def _store(org_id: int, facts: List[Dict[str, Any]], crawl_id: str) -> int:
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

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


def _home_page_company_name(first_page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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
            return {"value": value, "confidence": name["confidence"], "basis": "page"}
    # title as fallback
    title = static.get("title") or ""
    if title.strip():
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
            return {"value": value, "confidence": _number(confidences.get("company_name")), "basis": "model"}
    return None


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
) -> Dict[str, Any]:
    """Read the organisation's consented website and store what was found as pending proposals.

    ``progress`` is told how far the reading has got (pages read, the limit, the page being read).
    Raises CrawlRefused, with a reason code, when there is no consented site or nothing could be
    read or found. Returns what was read, not the pages.
    """
    org, site = _consented_site(org_id)
    model = _scoped(llm or default_model(), org_id)
    max_pages = max(1, min(int(max_pages), MAX_PAGES))
    _tell(progress, stage="reading", pages_read=0, limit=max_pages, current=site)
    read = _read_site(site, model, max_pages, float(time_limit_seconds), float(delay_seconds), progress)
    if not read["pages"]:
        reason = "; ".join("%s (%s)" % (f["url"], f["reason"]) for f in read["failed"][:3])
        if read["skipped_by_robots"] and not read["failed"] and not read["robots_unanswered"]:
            raise CrawlRefused("Nothing could be read from %s: the site's robots.txt excludes it." % site, "robots")
        status = next((int(f["reason"].split()[1]) for f in read["failed"]
                       if f["reason"].startswith("status ") and f["reason"].split()[1].isdigit()), None)
        raise CrawlRefused("Nothing could be read from %s: %s." % (site, reason or "it did not answer"),
                           "no_answer", status)
    _tell(progress, stage="working_out", pages_read=len(read["pages"]), limit=max_pages, current=None)

    facts = _Facts()
    for page in read["pages"]:
        _from_static(facts, page["url"], page["fetched_at"], page["static"])
        _from_analysis(facts, page["url"], page["fetched_at"], page["analysis"])
    first = read["pages"][0]

    # The company name must come from the home page, not a sub-page whose title or
    # heading may differ (e.g. "Cal.com Docs" read from the docs page). Take the home
    # page's site_name or title, and drop the suffix after a separator so "Acme | Home"
    # becomes "Acme". When the home page gives no name at all the confidence is lowered
    # to the default floor rather than reported at 100%.
    _home_name = _home_page_company_name(first)
    if _home_name:
        _replace_company_name(facts, _home_name["value"], first["url"], first["fetched_at"],
                              _home_name["confidence"], _home_name["basis"])
    else:
        _cap_company_name_confidence(facts, first["url"])
    inferred: Dict[str, Any] = {}
    try:
        inferred = parse_json(model(prompts.render("website_inference", _merged_for_inference(read["pages"], facts))))
    except Exception:
        logger.info("website inference failed for organisation %s", org_id, exc_info=True)
        read["model_errors"] += 1
    _from_inference(facts, first["url"], first["fetched_at"], inferred)
    if not facts.rows:
        raise CrawlRefused(
            "%d page(s) of %s were read and no fact could be taken from them." % (len(read["pages"]), site),
            "script_only" if read["unrendered"] else "nothing_found")

    crawl_id = uuid.uuid4().hex
    from app.jobs.tenant_safe_job import tenant_scope

    with tenant_scope(org_id):
        stored = _store(org_id, facts.ordered(), crawl_id)
    return {
        "crawl_id": crawl_id, "site": site, "pages_read": len(read["pages"]),
        "rendered": read["rendered"], "skipped_by_robots": read["skipped_by_robots"], "failed": read["failed"],
        "stopped": read["stopped"], "model_errors": read["model_errors"], "proposals": stored,
    }


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
            org_id, llm=website_crawl.default_model(), progress=_job_progress(payload.get("job_id")))
    except CrawlRefused as refused:
        logger.info("website not read for organisation %s: %s", org_id, refused.reason)
        return {"success": False, "reason": refused.reason, "status": refused.status}
    _tell_the_bell(org_id)
    return {
        "pages_read": summary["pages_read"], "pages_rendered": len(summary["rendered"]),
        "pages_skipped_by_robots": len(summary["skipped_by_robots"]), "pages_failed": len(summary["failed"]),
        "stopped": summary["stopped"], "model_errors": summary["model_errors"], "proposals": summary["proposals"],
        "crawl_id": summary["crawl_id"],
    }
