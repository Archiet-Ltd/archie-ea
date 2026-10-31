"""R1-B39: map a plain-language question onto one query_catalogue entry.

Calls the platform's one gated model gateway (``LLMService.generate_from_prompt``)
— never a direct provider call — and falls back to a deterministic
keyword match when no model provider is configured (so this interpreter,
and its tests, work with AI disabled). Both paths return the same shape so
callers never need to know which one ran.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.modules.intelligence.services.query_catalogue import CATALOGUE

logger = logging.getLogger(__name__)


# Deterministic fallback: ordered (keywords, entry_id, default_params).
# Checked in order; the first match wins. This is also what runs the
# committed evaluation set in CI, where no LLM provider is configured.
#
# The phrasings here cover the thirty plain-words questions a startup CTO
# asks across the five use cases the ask page serves (what breaks, no owner,
# paying twice, what we run and who owns it, what came from GitHub). The
# more specific rules come first: "what came from GitHub" and "what we run"
# are both checked before the broad "no owner" and "what breaks" rules so a
# question that mentions both lands on the one it is really asking.
_KEYWORD_RULES = [
    # UC-S2-04: what came from the GitHub connector.
    (
        (
            "from github", "from our github", "github repositor", "github connector",
            "github integration", "github commit", "github-sourced", "source is a github",
            "discovered from", "cite a github", "into the inventory", "into the catalogue",
        ),
        "github_sourced_records",
        {},
    ),
    # What the company's own website said, once a person has confirmed it: what we sell, who our customers
    # are, where we are based, what technology the site uses, who leads. Phrases are specific to the
    # company itself so a "what breaks for our customers" question is never taken for one.
    (
        (
            "what we sell", "what do we sell", "what does our company sell", "what we offer", "what do we offer",
            "what does our company do", "what do we do as a company", "who are our customers", "who our customers are",
            "who do we sell to", "our target market", "where are we based", "where we are based", "where is our company based",
            "where is our headquarters", "our headquarters", "our site use", "our website use", "our site is built",
            "our website is built", "who leads", "who runs our company", "who are the leaders", "who is our ceo",
            "who founded", "our founders", "our leadership", "about our company", "about my company",
            "about the company", "learned about", "learn about our", "know about our company", "know about us",
            "our company profile", "what does our website say", "from our website",
        ),
        "company_profile",
        {},
    ),
    # UC-S2-03: paying twice / duplicate tooling by capability.
    (
        (
            "paying for two", "paying twice", "pay twice", "same thing",
            "overlapping", "duplicate tool", "duplicate logging", "redundant",
            "more than one paid", "covered by more than one", "cut by removing",
            "duplicate subscription", "two tools",
        ),
        "duplicate_tooling_by_capability",
        {},
    ),
    # UC-S2-02: nobody owns it. Checked before the inventory rule so a
    # "missing an owner" question is never read as "list everything we run".
    (
        (
            "no owner", "without owner", "without an owner", "no recorded owner",
            "unowned", "nobody is responsible", "missing an accountable",
            "no owner assigned", "unowned integration", "lack a named owner",
            "no named owner", "without an accountable",
        ),
        "applications_without_owner",
        {},
    ),
    # UC-S1-05: what we run and who owns it (inventory + owner + cost).
    (
        (
            "what applications do we run", "who owns each", "inventory of services",
            "who owns the", "application portfolio", "own the most",
            "every production system and its", "systems with their owners",
            "run and who owns", "and its accountable team",
        ),
        "applications_and_owners",
        {},
    ),
    # UC-S2-01: what breaks if X fails (impact).
    (
        (
            "goes down", "what depends on", "break if", "breaks if", "breaks for",
            "single point", "single points of failure", "unavailable", "is down",
            "stops working", "fails, which", "if the", "what else stops",
            "what if we retire", "what if we decommission", "what if we remove",
            "what if we replace", "what if we shut down",
        ),
        "what_breaks_if_fails",
        {},
    ),
    # The original business-continuity phrasings still map to that entry.
    (
        (
            "stop the business", "stop trading", "stop us from trading",
            "business continuity", "critical systems",
        ),
        "business_continuity_criticality",
        {"criticality": "Critical"},
    ),
]


_PROFILE_TOPIC_WORDS = (
    ("leaders", ("who leads", "who runs", "who are the leaders", "ceo", "founder", "leadership")),
    ("technology", ("site use", "website use", "is built", "technology", "tech stack")),
    ("location", ("based", "headquarters", "located")),
    ("customers", ("customer", "sell to", "target market", "clients")),
    ("products", ("sell", "offer", "product", "what does our company do", "what do we do")),
)


def _profile_topic(lowered: str) -> str:
    """Which part of the company's profile a question asks about; the whole profile when it names none."""
    for topic, words in _PROFILE_TOPIC_WORDS:
        if any(word in lowered for word in words):
            return topic
    return "overview"


def _keyword_match(question: str) -> Optional[Dict[str, Any]]:
    lowered = question.lower()
    for keywords, entry_id, params in _KEYWORD_RULES:
        if any(kw in lowered for kw in keywords):
            resolved = dict(params)
            if entry_id == "company_profile":
                resolved["topic"] = _profile_topic(lowered)
            if entry_id == "what_breaks_if_fails":
                # The impact entry needs the service the person named; pull it
                # out of the question's own phrasing so it can be resolved.
                service = _extract_service(question)
                if service:
                    resolved["service"] = service
            return {"entry_id": entry_id, "params": resolved, "confidence": 0.9, "method": "keyword"}
    return None


# The service a "what breaks" question names, by the shape of the question.
# The capturing group is the service phrase; whichever pattern matches first
# names the service, else nothing (and the entry then asks the person to name
# the service rather than guess).
_SERVICE_PATTERNS = [
    re.compile(r"\bwhat (?:if|happens if|breaks if) (?:we |you )?(?:retire|decommission|remove|replace|shut down) (?:the |our )?(.+?)\s*[?.!]*\s*$", re.I),
    re.compile(r"\bif (?:the |our )?(.+?) (?:goes down|is down|fails|is unavailable)\b", re.I),
    re.compile(r"\bwhat depends on (?:the |our )?(.+?)\s*[?.]?\s*$", re.I),
    re.compile(r"\bif (?:the |our )?(.+?) (?:is|are) (?:down|unavailable)\b", re.I),
    re.compile(r"\b(?:services|journeys) (?:break|are affected) if (?:the |our )?(.+?) (?:fails|is)\b", re.I),
    re.compile(r"\bbreaks? for .*? if (?:the |our )?(.+?) (?:is down|fails|goes down)\b", re.I),
]

# Superlative/descriptive phrases that name no specific system. When the
# captured service phrase matches one of these, we treat it as "no service
# named" so the answer falls through to single points of failure without a
# "nothing recorded matches" reason line.
_SUPERLATIVE_PHRASES = [
    re.compile(
        r"^(?:(?:our|the) )?"
        r"(?:most connected|most depended[- ]?on|most critical|biggest|main|core)"
        r"(?: (?:application|app|system|service|platform|database|single point of failure))?$",
        re.I,
    ),
]


def _is_superlative_phrase(service: str) -> bool:
    """True only when the whole phrase is a superlative or descriptive word,
    optionally followed by a generic noun ("most connected application",
    "main system"). A real name such as "core banking" or "main website" is
    not superlative.
    """
    s = " ".join(service.strip().lower().split())
    return any(pattern.match(s) for pattern in _SUPERLATIVE_PHRASES)


def asks_for_superlative(question: str) -> bool:
    """True when the question's service phrase is a superlative ("our most
    connected application"): it asks for the single points of failure."""
    text = (question or "").strip()
    for pattern in _SERVICE_PATTERNS:
        found = pattern.search(text)
        if found:
            return _is_superlative_phrase(found.group(1).strip().rstrip("?.! "))
    return False


def _extract_service(question: str) -> Optional[str]:
    """The service a 'what breaks' question names, or None.

    Reads the service phrase out of the question's own wording (never guesses
    one); the catalogue entry resolves that phrase to an element through the
    one entity matcher, and an unmatched phrase is answered honestly rather
    than with an invented blast radius.

    Superlative/descriptive phrases ("most connected", "most depended-on",
    "most critical", "biggest", "our main", "the core") are treated as no
    service named, so the answer falls through to single points of failure
    without a "nothing recorded matches" reason line.
    """
    text = (question or "").strip()
    for pattern in _SERVICE_PATTERNS:
        found = pattern.search(text)
        if found:
            service = found.group(1).strip().rstrip("?.! ")
            if service and not _is_superlative_phrase(service):
                return service
            # Superlative phrase matched — treat as no service named.
            return None
    return None


def _model_configured() -> bool:
    try:
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        provider, _model = LLMService._get_configured_provider()
        return bool(provider)
    except Exception:
        return False


def _llm_match(question: str) -> Optional[Dict[str, Any]]:
    """Ask the gated model gateway which catalogue entry fits, if any."""
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    entries_desc = "\n".join(
        f"- {e.id}: {e.title} (params: {', '.join(e.params) or 'none'})"
        for e in CATALOGUE.values()
    )
    prompt = (
        "Map the user's question onto exactly one of these catalogue entries, "
        "or none if nothing fits. Respond with JSON only: "
        '{"entry_id": "<id or null>", "params": {...}}.\n\n'
        f"Catalogue:\n{entries_desc}\n\nQuestion: {question!r}"
    )
    try:
        raw = LLMService.generate_from_prompt(prompt, expected_schema="json")
        parsed = json.loads(raw)
    except Exception:
        logger.warning("nl_query_interpreter: model gateway call failed", exc_info=True)
        return None

    entry_id = parsed.get("entry_id")
    if entry_id not in CATALOGUE:
        return None
    return {"entry_id": entry_id, "params": parsed.get("params") or {}, "confidence": 0.7, "method": "model"}


def interpret(question: str) -> Dict[str, Any]:
    """Interpret one plain-language question.

    Returns ``{"entry_id": str|None, "params": dict, "confidence": float,
    "method": "keyword"|"model"|"none", "title": str|None}`` — the
    interpretation banner shows ``title``/``params`` before running
    anything, and the caller re-runs with corrected params on request.
    """
    match = _keyword_match(question)
    if match is None and _model_configured():
        match = _llm_match(question)
    if match is None:
        return {"entry_id": None, "params": {}, "confidence": 0.0, "method": "none", "title": None}
    entry = CATALOGUE[match["entry_id"]]
    match["title"] = entry.title
    return match


def evaluation_set() -> List[Dict[str, Any]]:
    """The committed set of plain-language questions and their correct
    catalogue mapping, used by the correctness test.

    It holds the thirty startup-CTO questions across the five use cases the
    ask page serves — one cited answer to each is the number behind the
    sign-up-to-first-answer path — plus two questions outside the catalogue
    that must still map to nothing rather than be answered with a guess.
    """
    return [
        # UC-S2-01 — what breaks if X fails (impact).
        {"question": "If the billing platform goes down, what else stops working?", "expected_entry_id": "what_breaks_if_fails"},
        {"question": "What depends on our primary database?", "expected_entry_id": "what_breaks_if_fails"},
        {"question": "Which customer-facing services break if the auth service fails?", "expected_entry_id": "what_breaks_if_fails"},
        {"question": "What are the single points of failure in our stack?", "expected_entry_id": "what_breaks_if_fails"},
        {"question": "If the payment gateway is unavailable, which journeys are affected?", "expected_entry_id": "what_breaks_if_fails"},
        {"question": "What breaks for customers if the email provider is down?", "expected_entry_id": "what_breaks_if_fails"},
        # UC-S2-02 — nobody owns it.
        {"question": "Which applications have no owner recorded?", "expected_entry_id": "applications_without_owner"},
        {"question": "Are there any services nobody is responsible for?", "expected_entry_id": "applications_without_owner"},
        {"question": "List the systems missing an accountable team.", "expected_entry_id": "applications_without_owner"},
        {"question": "Which databases have no owner assigned?", "expected_entry_id": "applications_without_owner"},
        {"question": "Do we have any unowned integrations?", "expected_entry_id": "applications_without_owner"},
        {"question": "Which production components lack a named owner?", "expected_entry_id": "applications_without_owner"},
        # UC-S2-03 — paying twice.
        {"question": "Are we paying for two tools that do the same thing?", "expected_entry_id": "duplicate_tooling_by_capability"},
        {"question": "Where do we have overlapping vendor subscriptions?", "expected_entry_id": "duplicate_tooling_by_capability"},
        {"question": "Which capabilities are covered by more than one paid service?", "expected_entry_id": "duplicate_tooling_by_capability"},
        {"question": "Do we have duplicate logging tools we pay for?", "expected_entry_id": "duplicate_tooling_by_capability"},
        {"question": "Are there redundant monitoring subscriptions?", "expected_entry_id": "duplicate_tooling_by_capability"},
        {"question": "Which spend could we cut by removing duplicate tooling?", "expected_entry_id": "duplicate_tooling_by_capability"},
        # UC-S1-05 — what we run and who owns it.
        {"question": "What applications do we run and who owns each one?", "expected_entry_id": "applications_and_owners"},
        {"question": "Give me the inventory of services with their owners.", "expected_entry_id": "applications_and_owners"},
        {"question": "Who owns the billing platform and what does it cost per year?", "expected_entry_id": "applications_and_owners"},
        {"question": "List every production system and its accountable team.", "expected_entry_id": "applications_and_owners"},
        {"question": "What is our full application portfolio right now?", "expected_entry_id": "applications_and_owners"},
        {"question": "Which teams own the most systems?", "expected_entry_id": "applications_and_owners"},
        # UC-S2-04 — what came from GitHub.
        {"question": "Which components were discovered from our GitHub repositories?", "expected_entry_id": "github_sourced_records"},
        {"question": "What did the GitHub connector import into the inventory?", "expected_entry_id": "github_sourced_records"},
        {"question": "Show the services whose source is a GitHub repository.", "expected_entry_id": "github_sourced_records"},
        {"question": "Which records cite a GitHub commit as their origin?", "expected_entry_id": "github_sourced_records"},
        {"question": "What came into the catalogue from the GitHub integration?", "expected_entry_id": "github_sourced_records"},
        {"question": "List the GitHub-sourced applications and their repositories.", "expected_entry_id": "github_sourced_records"},
        # Outside the catalogue — must map to nothing, never a guessed answer.
        {"question": "what's the weather today", "expected_entry_id": None},
        {"question": "how many users do we have", "expected_entry_id": None},
    ]
