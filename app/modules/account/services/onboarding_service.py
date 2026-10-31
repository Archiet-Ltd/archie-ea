"""What sign-up asks after the account exists: the company's sources, the industry and the
frameworks it works with, and the AI provider.

Everything is optional and nothing here blocks the person from reaching the dashboard.
The choices live on the organisation (``Organization.settings["company_context"]``, beside
the company kind); the website and the consent to read it live under
``settings["onboarding"]``. Imports run as background jobs and are reported from the job
queue, so this page and the dashboard read one record of how far each source has got.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy.orm.attributes import flag_modified

from app import db

_log = logging.getLogger(__name__)

GITHUB_TASK = "onboarding_github_import"
JIRA_TASK = "onboarding_jira_import"
STARTER_TASK = "onboarding_starter_data"
WEBSITE_TASK = "website_profile_crawl"
MORE_FACTS_LATER = "More facts will be added when our reading service is less busy."
MORE_FACTS_FOLLOW = "More facts follow in a few minutes, as we finish working out what your pages say."

# (key, label, starter capability model). The model is the starter set the industry loads into the
# organisation's capability model (see load_starter_data).
INDUSTRIES = (
    ("banking", "Banking and payments", "BIAN"),
    ("insurance", "Insurance", "APQC"),
    ("software", "Software and technology", "APQC"),
    ("retail", "Retail and consumer goods", "APQC"),
    ("manufacturing", "Manufacturing", "APQC"),
    ("healthcare", "Healthcare and life sciences", "APQC"),
    ("public", "Public sector", "APQC"),
    ("energy", "Energy and utilities", "APQC"),
    ("telecom", "Telecommunications", "APQC"),
    ("other", "Something else", "APQC"),
)

# Only frameworks the product holds a catalogue of controls for are offered; one with no catalogue
# would be a choice that does nothing. SAFe, TOGAF, ArchiMate, ITIL 4, COBIT 2019 and NIST CSF have
# none today. The third column of FRAMEWORK_CATALOGUES is the code of the framework in the catalogue.
FRAMEWORKS = (
    ("iso27001", "ISO 27001", "Information security management"),
    ("soc2", "SOC 2", "Trust services criteria for service providers"),
    ("gdpr", "GDPR", "Personal data protection in the EU"),
)
FRAMEWORK_CATALOGUES = {"iso27001": "ISO - 27001", "soc2": "SOC-2", "gdpr": "GDPR"}

KINDS = (("startup", "Startup"), ("scaleup", "Scale-up"), ("enterprise", "Enterprise"))

# Which frameworks suit a first goal and a company kind; the sign-up step offers only these (the rest stay in Settings).
FRAMEWORKS_FOR_GOAL = {
    "understand": ("archimate", "togaf"),
    "change": ("togaf", "safe", "archimate"),
    "cost": ("itil4", "cobit2019"),
    "audit": ("iso27001", "soc2", "gdpr", "nistcsf"),
    "strategy": ("togaf", "archimate"),
}
FRAMEWORKS_FOR_KIND = {
    "startup": ("soc2", "safe"),
    "scaleup": ("soc2", "iso27001", "safe"),
    "enterprise": ("iso27001", "cobit2019", "itil4", "togaf"),
}


def suggested_frameworks(kind: Optional[str], goal: Optional[str]) -> List[str]:
    """The framework keys worth offering for this goal and company kind, goal first."""
    keys = list(FRAMEWORKS_FOR_GOAL.get(goal or "understand", ())) + list(FRAMEWORKS_FOR_KIND.get(kind or "startup", ()))
    return [key for key in dict.fromkeys(keys) if key in _FRAMEWORK_KEYS]


_KIND_KEYS = {key for key, _label in KINDS}
_INDUSTRY_KEYS = {key for key, _label, _model in INDUSTRIES}
_FRAMEWORK_KEYS = {key for key, _label, _note in FRAMEWORKS}


def _context(org) -> Dict[str, Any]:
    return dict((org.settings or {}).get("company_context") or {})


def _onboarding(org) -> Dict[str, Any]:
    return dict((org.settings or {}).get("onboarding") or {})


def _write(org, section: str, values: Dict[str, Any]) -> None:
    settings = dict(org.settings or {})
    settings[section] = values
    org.settings = settings
    flag_modified(org, "settings")


def choices(org) -> Dict[str, Any]:
    """The industry and frameworks the organisation recorded, or nothing."""
    context = _context(org)
    return {
        "kind": context.get("kind"),
        "industry": context.get("industry"),
        "frameworks": list(context.get("frameworks") or []),
    }


def settings_card(org) -> Dict[str, Any]:
    """What Settings shows so the two choices can be changed after sign-up."""
    from app.modules.organization import public_signals
    from app.modules.organization.website_crawl import confirm

    return {
        "kinds": KINDS, "industries": INDUSTRIES, "frameworks": FRAMEWORKS, "chosen": choices(org),
        "removable": removable_starter_rows(org),
        "profile": confirm.profile_entries(org),
        "company_name": public_signals.company_name(org),
        "signals": public_signals.signals_for_profile(org.id), "signals_state": public_signals.refresh_state(org),
    }


def save_choices(
    org, industry: Optional[str], frameworks: List[str], kind: Optional[str] = None
) -> Dict[str, Any]:
    """Record the company kind, the industry and the frameworks; an unknown value is refused, an empty
    industry or framework list clears. An empty kind leaves the stored answer as it was."""
    kind = (kind or "").strip() or None
    if kind is not None and kind not in _KIND_KEYS:
        raise ValueError("Choose a company kind from the list.")
    industry = (industry or "").strip() or None
    if industry is not None and industry not in _INDUSTRY_KEYS:
        raise ValueError("Choose an industry from the list.")
    wanted = [key for key in dict.fromkeys(frameworks or [])]
    if any(key not in _FRAMEWORK_KEYS for key in wanted):
        raise ValueError("Choose frameworks from the list.")
    context = _context(org)
    if kind is not None:
        context["kind"] = kind
    context["industry"] = industry
    context["frameworks"] = wanted
    _write(org, "company_context", context)
    db.session.commit()
    if industry or wanted:
        _queue_starter_data(org.id)
    return choices(org)


def _queue_starter_data(org_id: int) -> None:
    """Load what the choices name in the background. A queue that is down never stops the person moving on."""
    from app.services.job_queue_service import get_job_queue_service

    try:
        get_job_queue_service().create_job(
            "Starter model and frameworks", STARTER_TASK, {}, organization_id=org_id
        )
    except Exception:
        db.session.rollback()
        _log.warning("could not queue the starter data for organisation %s", org_id, exc_info=True)


def normalise_website(raw: Optional[str]) -> Optional[str]:
    """The company site as an https address, or None when nothing usable was typed."""
    text = (raw or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "https://" + text
    parts = urlparse(text)
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or "." not in host or any(c.isspace() for c in text):
        raise ValueError("Enter your company website, for example acme.com.")
    return "%s://%s%s" % (parts.scheme, parts.netloc, parts.path.rstrip("/"))


class ReadingLimited(ValueError):
    """The organisation has used its readings for the day; the message says what to do instead."""


def save_website(org, raw: Optional[str], consent: bool, user_id: Optional[int]) -> Optional[str]:
    """Record the site and the person's consent to read it. The reading itself comes later.
    Returns a sentence when no new reading was started, saying why, and None otherwise."""
    url = normalise_website(raw)
    if url is None:
        return None
    if not consent:
        raise ValueError("Tick the box to let us read this site.")
    state = _onboarding(org)
    before = state.get("website")
    state["website"] = {"url": url, "consent_by": user_id, "consent_at": datetime.utcnow().isoformat()}
    _write(org, "onboarding", state)
    db.session.commit()
    try:
        outcome = _queue_reading(org.id)
        if outcome == "already_open" and before and before.get("url") != url:
            return CHANGED_ADDRESS_NOTE
        return READING_NOTES.get(outcome)
    except ReadingLimited:
        state = _onboarding(org)  # the refused address is not kept: nothing reads it
        if before is None:
            state.pop("website", None)
        else:
            state["website"] = before
        _write(org, "onboarding", state)
        db.session.commit()
        raise


CHANGED_ADDRESS_NOTE = "A reading of your website is already under way. When it finishes, we will read the new address too."

READING_NOTES = {
    "already_open": "A reading of your website is already under way, so no second one was started. This page shows how far it has got.",
    "already_read": "We read this address within the last day, so we did not read it again. What we found is ready to confirm.",
}


def _queue_reading(org_id: int) -> Optional[str]:
    """Start reading the site in the background; returns the outcome (queued, already_open or already_read).
    A queue that is down never stops the person moving on; the daily limit is told to the person."""
    from app.modules.organization.website_crawl import CrawlRefused, ReadingLimit, request_website_crawl

    try:
        return request_website_crawl(org_id)[1]
    except ReadingLimit as limit:
        raise ReadingLimited(str(limit))
    except CrawlRefused:
        _log.info("website not read for organisation %s", org_id, exc_info=True)
    except Exception:
        _log.warning("could not queue the website reading for organisation %s", org_id, exc_info=True)
    return None


def mark_finished(org) -> None:
    state = _onboarding(org)
    state.setdefault("finished_at", datetime.utcnow().isoformat())
    _write(org, "onboarding", state)
    db.session.commit()


def mark_profile_confirmed(org) -> None:
    """Record that the person went through the company-profile step (the website reading's confirm step)."""
    state = _onboarding(org)
    state.setdefault("profile_confirmed_at", datetime.utcnow().isoformat())
    _write(org, "onboarding", state)
    db.session.commit()


def profile_confirmed_at(org):
    raw = _onboarding(org).get("profile_confirmed_at")
    try:
        return datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def skipped_sources(org) -> bool:
    """True once sign-up is finished and no source was given, so the dashboard can say so."""
    return is_finished(org) and not any_source_given(org)


def is_finished(org) -> bool:
    return bool(_onboarding(org).get("finished_at"))


def on_paying_plan(org) -> bool:
    """True when the organisation is on a plan other than Community."""
    from app.services.billing_plans import current_subscription, effective_plan

    return effective_plan(current_subscription(org)).key != "free"


# --------------------------------------------------------------------------------------------- #
# Imports                                                                                        #
# --------------------------------------------------------------------------------------------- #


def run_github_import(org_id: int, connector_id: str, approver_id: int) -> Dict[str, Any]:
    """Read the repositories, then add what was found to the model.

    The person gave the token themselves, so what the read proposes is approved in the same job:
    the first answer is about their own systems without a review step in between. Every row keeps
    the repository and file it came from. Anything that cannot land yet stays pending, as on the
    connectors page."""
    from app.connectors import github as gh
    from app.jobs.tenant_safe_job import tenant_scope

    result = gh.run_first_sync(org_id, connector_id)
    if not result.get("success"):
        return result
    with tenant_scope(org_id):
        outcome = gh.approve_all_proposals(org_id, approver_id)
        db.session.commit()
    result["added"] = outcome["approved"]
    result["not_added"] = len(outcome["failed"])
    return result


def run_jira_import(org_id: int, connector_id: str) -> Dict[str, Any]:
    from app.jobs.tenant_safe_job import tenant_scope
    from app.services.connector_framework import run_connector_sync

    with tenant_scope(org_id):
        result = run_connector_sync(org_id=org_id, connector_id=connector_id, delegated=True)
        db.session.commit()
    return {
        "success": bool(result.get("success")),
        "issues": result.get("records_processed", 0),
        "error": (result.get("error") or result.get("error_message") or None) if not result.get("success") else None,
    }


def connect_jira(org_id: int, instance_url: str, email: str, token: str, approver_id: int) -> str:
    """Save the Jira connection and queue its import. Returns the connector id."""
    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.services.connector_framework import (
        CONNECTOR_ACTION_CREATE,
        CONNECTOR_ACTION_UPDATE,
        ConnectorURLRefused,
        _load_org_connector,
        apply_connector_change,
        validate_connector_url,
    )
    from app.services.job_queue_service import get_job_queue_service

    instance_url = (instance_url or "").strip().rstrip("/")
    email = (email or "").strip()
    token = (token or "").strip()
    if not instance_url or not email or not token:
        raise ValueError("Jira needs the site address, your email and an API token.")
    try:
        instance_url = validate_connector_url(instance_url)
    except ConnectorURLRefused as exc:
        raise ValueError(str(exc)) from exc
    existing = _load_org_connector(org_id, connector_type="jira")
    payload = {
        "connector_type": "jira",
        "connector_id": existing.id if existing else None,
        "name": "Jira",
        "config": {"instance_url": instance_url, "email": email, "enabled": True},
        "status": "active",
        "credential_provided": True,
    }
    cfg = apply_connector_change(
        org_id=org_id,
        action=CONNECTOR_ACTION_UPDATE if existing else CONNECTOR_ACTION_CREATE,
        payload=payload,
        delegated=True,
    )
    OrgCredentialVault().store(org_id=org_id, connector_type="jira", credential_type="api_token", value=token)
    db.session.commit()
    get_job_queue_service().create_job(
        "Jira: read issues", JIRA_TASK, {"connector_id": cfg.id}, organization_id=org_id
    )
    return cfg.id


def _source_state(org_id: int, task: str, label: str, done_text) -> Dict[str, Any]:
    from app.models.job import JobStatus
    from app.services.job_queue_service import get_job_queue_service

    jobs = get_job_queue_service().list_jobs(org_id, limit=1, task=task)
    if not jobs:
        return {"state": "not_given", "text": "Not connected."}
    job = jobs[0]
    result = job.result or {}
    if job.status == JobStatus.COMPLETED.value and result.get("success") is not False:
        return {"state": "done", "text": done_text(result)}
    if job.status in (JobStatus.FAILED.value, JobStatus.CANCELLED.value) or result.get("success") is False:
        reason = result.get("error") or job.error or "The read did not finish."
        return {"state": "failed", "text": "%s could not be read: %s" % (label, reason)}
    if job.status == JobStatus.IN_PROGRESS.value:
        return {"state": "reading", "text": "Reading %s now. This takes a few minutes; carry on." % label}
    return {"state": "waiting", "text": "%s is connected. Reading starts in a moment; carry on." % label}


def assistant_connection(user) -> Optional[Dict[str, Any]]:
    """The person's own AI chat, once it has signed in: the newest grant Connected assistants lists for them."""
    from app.modules.oauth_provider.assistants_routes import connected_grants

    grants = connected_grants(user.id) if user is not None else []
    if not grants:
        return None
    newest = grants[0]
    connected_at = newest.get("connected_at")
    return {
        "client": newest["client_name"],
        # stored as UTC without a zone mark; the mark lets the browser show the person's own local time
        "connected_at": (connected_at.isoformat() + ("Z" if connected_at.tzinfo is None else "")) if connected_at else None,
    }


def mcp_address() -> str:
    """The address the person adds to their chat: this workspace's /mcp."""
    from flask import request

    return request.host_url.rstrip("/") + "/mcp"


def sources(org) -> List[Dict[str, Any]]:
    """How far each source the person gave has got, one line each, for sign-up and the dashboard."""
    rows = []
    github = _source_state(
        org.id, GITHUB_TASK, "GitHub",
        lambda r: "Read %s repositories; %s items added to your model." % (r.get("repositories", 0), r.get("added", 0)),
    )
    rows.append({"key": "github", "label": "GitHub", **github})
    jira = _source_state(org.id, JIRA_TASK, "Jira", lambda r: "Read %s issues." % r.get("issues", 0))
    rows.append({"key": "jira", "label": "Jira", **jira})
    rows.append({"key": "website", "label": "Company website", **website_state(org)})
    return rows


# Why a site could not be read, in words a person can act on, by the reason the reading gives.
_WEBSITE_REASONS = {
    "robots": "%s asks readers like ours to stay out, in its robots.txt, so we have not read it.",
    "no_answer": "%s did not answer when we tried to read it.",
    "blocked": "%s is an address we cannot read from here.",
    "script_only": "%s builds its pages with scripts, and the browser we use could not show them, so nothing could be read.",
    "nothing_found": "We read %s but found no facts we could state with confidence.",
    "no_consent": "%s has not been agreed to be read yet.",
    "no_site": "No website is recorded yet.",
    "interrupted": "The reading of %s was cut off by a restart on our side, so it has been queued to run again.",
}
_INSTEAD = " You can connect GitHub or Jira instead, or look around the sample company."


def _host(url: str) -> str:
    parts = urlparse(url)
    return parts.netloc + (parts.path if parts.path not in ("", "/") else "")


def _address(url: Optional[str]) -> str:
    """A page address as a person reads it: no scheme."""
    return (url or "").replace("https://", "").replace("http://", "").rstrip("/")


def website_instead() -> List[Dict[str, str]]:
    """What a person can do when their website cannot be read: each opens a page with content."""
    from flask import url_for

    return [
        {"label": "Connect GitHub", "url": url_for("account.welcome") + "#signup-github"},
        {"label": "Connect Jira", "url": url_for("account.welcome") + "#signup-jira"},
        {"label": "See the sample company", "url": url_for("unified_ai_chat.index")},
    ]


def website_state(org) -> Dict[str, Any]:
    """How far the reading of the company website has got, in a sentence a person can read, with the
    counts behind it and what to do next: confirm what was read, or, when the site could not be read,
    what to do instead."""
    from flask import url_for

    from app.models.job import JobStatus
    from app.modules.organization.website_crawl import confirm
    from app.services.job_queue_service import get_job_queue_service

    site = _onboarding(org).get("website")
    if not site:
        return {"state": "not_given", "text": "Not given."}
    host = _host(site["url"])
    jobs = get_job_queue_service().list_jobs(org.id, limit=1, task=WEBSITE_TASK)
    if not jobs:
        return {"state": "waiting", "text": "%s will be read in a moment. Nothing from it is in your model until you confirm it." % site["url"]}
    job = jobs[0]
    result = job.result or {}
    if job.status == JobStatus.COMPLETED.value and result.get("success") is not False:
        facts, _completeness = confirm.proposals(org.id)
        read = result.get("pages_read", 0)
        pages = "%s %s" % (read, "page" if read == 1 else "pages")
        later = (" " + _more_to_come(org, result)) if _more_to_come(org, result) else ""
        if facts:
            return {
                "state": "done",
                "text": "Read %s; %s %s for you to confirm in the next step.%s" % (
                    pages, len(facts), "fact waits" if len(facts) == 1 else "facts wait", later),
                "next": {"label": "Confirm what we read", "url": url_for("account.welcome", step=3)},
                "progress": {"read": read, "limit": read, "percent": 100},
            }
        return {"state": "done", "text": "Read %s. What was found is confirmed and in your profile.%s" % (pages, later)}
    if job.status in (JobStatus.FAILED.value, JobStatus.CANCELLED.value) or result.get("success") is False:
        reason = result.get("reason")
        if reason in _WEBSITE_REASONS:
            sentence = _WEBSITE_REASONS[reason] % host if "%s" in _WEBSITE_REASONS[reason] else _WEBSITE_REASONS[reason]
            if reason == "no_answer" and result.get("status"):
                sentence = "%s answered with an error (status %s) when we tried to read it." % (host, result["status"])
        else:
            sentence = "Something went wrong while reading %s, and nothing was added to your model." % host
        return {"state": "failed", "text": sentence + _INSTEAD, "instead": website_instead()}
    if job.status == JobStatus.IN_PROGRESS.value:
        progress = result.get("progress") or {}
        limit = int(progress.get("limit") or 30)
        read = int(progress.get("pages_read") or 0)
        percent = min(100, int(round(100.0 * read / limit))) if limit else 0
        if progress.get("stage") == "facts_ready":
            facts, _completeness = confirm.proposals(org.id)
            if facts:
                return {
                    "state": "reading", "facts_ready": True,
                    "text": "Read %d of up to %d pages. %d %s ready for you to confirm in the next step; "
                            "more are still being worked out." % (read, limit, len(facts), "fact is" if len(facts) == 1 else "facts are"),
                    "next": {"label": "Confirm what we read", "url": url_for("account.welcome", step=3)},
                    "progress": {"read": read, "limit": limit, "percent": percent},
                }
        if progress.get("stage") in ("working_out", "facts_ready"):
            text = "Read %d of up to %d pages. Working out what they say; this takes a minute." % (read, limit)
        elif read == 0:
            text = "Opening %s. Up to %d pages will be read." % (_address(progress.get("current")) or host, limit)
        else:
            text = "Read %d of up to %d pages. Reading now: %s." % (read, limit, _address(progress.get("current")))
        lead = ("We are reading %s: page %d of up to %d. Answer the questions below now; "
                "the facts we find will be waiting on your Overview to confirm" % (host, max(read, 1), limit)) if read else None
        return {"state": "reading", "text": text, "reading_line": lead, "current": _address(progress.get("current")),
                "progress": {"read": read, "limit": limit, "percent": percent}}
    return {"state": "waiting", "text": "Company website is connected. Reading starts in a moment; carry on."}


def _more_to_come(org, result: Dict[str, Any]) -> Optional[str]:
    """What to say about the facts still on their way after the first read: the model pass is working them out
    (it runs as its own job), or it found every provider busy and a retry is queued; nothing when it is done."""
    from app.models.job import JobStatus
    from app.services.job_queue_service import get_job_queue_service

    if not result.get("more_to_come"):
        return None
    if not result.get("model_pass_queued"):
        return MORE_FACTS_LATER
    latest = get_job_queue_service().list_jobs(org.id, limit=1, task="website_profile_crawl_more")
    if not latest:
        return MORE_FACTS_FOLLOW
    job = latest[0]
    waiting_for_later = bool((job.payload or {}).get("not_before"))
    if job.status == JobStatus.COMPLETED.value and not (job.result or {}).get("retry_queued"):
        return None  # the pass has finished: everything it found is already in the list
    if job.status in (JobStatus.FAILED.value, JobStatus.CANCELLED.value):
        return None
    if waiting_for_later or (job.result or {}).get("retry_queued"):
        return MORE_FACTS_LATER
    return MORE_FACTS_FOLLOW


def more_facts_coming(org) -> Optional[str]:
    """The sentence to show when more facts are on their way: a pass still being worked out, or a retry queued
    because every provider was busy."""
    from app.models.job import JobStatus
    from app.services.job_queue_service import get_job_queue_service

    jobs = get_job_queue_service().list_jobs(org.id, limit=1, task=WEBSITE_TASK)
    if jobs and jobs[0].status == JobStatus.COMPLETED.value:
        return _more_to_come(org, jobs[0].result or {})
    return None


def any_source_given(org) -> bool:
    return any(row["state"] != "not_given" for row in sources(org))


# --------------------------------------------------------------------------------------------- #
# The AI provider                                                                                #
# --------------------------------------------------------------------------------------------- #

PROVIDERS = (("openai", "OpenAI"), ("anthropic", "Anthropic"))
_KEY_PREFIX = {"openai": ("sk-",), "anthropic": ("sk-ant-",)}


def check_key(provider: str, key: str) -> Dict[str, Any]:
    """Test the key with one small call to the provider the person named."""
    from app.services.llm_service import test_api_key

    key = (key or "").strip()
    if provider not in dict(PROVIDERS):
        return {"success": False, "message": "Choose a provider from the list."}
    prefixes = _KEY_PREFIX[provider]
    if not key or not key.startswith(prefixes):
        return {"success": False, "message": "That does not look like a %s key." % dict(PROVIDERS)[provider]}
    return test_api_key(provider, key)


def save_key(provider: str, key: str, user_id: int) -> None:
    """Store a tested key for this organisation only, encrypted, and never read back to the page."""
    from app.models.models import APISettings

    row = APISettings.query.filter_by(provider=provider, key_label="").first()
    if row is None:
        row = APISettings(provider=provider, key_label="", api_key=key, enabled=True, updated_by_id=user_id)
        db.session.add(row)
    else:
        row.api_key = key
        row.enabled = True
        row.updated_by_id = user_id
    row.test_status = "success"
    row.last_tested_at = datetime.utcnow()
    db.session.commit()


def own_key_provider() -> Optional[str]:
    """The provider this organisation has a working key for, or None when it uses the built-in AI."""
    from app.models.models import APISettings

    row = APISettings.query.filter(
        APISettings.provider.in_([p for p, _ in PROVIDERS]), APISettings.enabled.is_(True)
    ).first()
    return dict(PROVIDERS).get(row.provider) if row else None


# --------------------------------------------------------------------------------------------- #
# Starter data: the capability model the industry names and the catalogues of the frameworks      #
# --------------------------------------------------------------------------------------------- #


def _refresh_capability_map(org_id: int) -> None:
    """The capability map keeps an organisation's hierarchy for a few minutes; drop it so a change shows at once."""
    from app.extensions.cache import invalidate_cache

    try:
        invalidate_cache("capability_map:*:%s" % org_id)
    except Exception:
        _log.warning("could not refresh the capability map for organisation %s", org_id, exc_info=True)


def _mark_elements_as_starter(org_id: int) -> None:
    """The model elements mirrored from starter capabilities and adopted controls say they are starter elements,
    so the overview does not mistake them for what the person brought."""
    db.session.execute(
        db.text(
            "UPDATE archimate_elements SET source_kind = 'starter' "
            "WHERE organization_id = :org AND source_kind IS NULL AND id IN ("
            " SELECT archimate_element_id FROM business_capability WHERE organization_id = :org"
            "  AND discovery_source LIKE 'starter:%' AND archimate_element_id IS NOT NULL"
            " UNION SELECT c.archimate_element_id FROM application_compliance_controls c"
            "  JOIN framework_adoptions a ON a.id = c.adoption_id WHERE a.organization_id = :org"
            "  AND c.organization_id = :org AND a.source LIKE 'starter:%' AND c.archimate_element_id IS NOT NULL)"
        ),
        {"org": org_id},
    )
    db.session.commit()


def _starter_source(model: str) -> str:
    return "starter:%s" % model


def _starter_model(industry: Optional[str]) -> Optional[str]:
    return next((model for key, _label, model in INDUSTRIES if key == industry), None)


def _bian_entries() -> List[Dict[str, Any]]:
    """(name, description, level, parent name, domain) for each BIAN starter capability, parents first."""
    from app.seed_data.bian_starter import BIAN_AREA_CODES, BIAN_STARTER

    entries = []
    for area, domains in BIAN_STARTER:
        code = BIAN_AREA_CODES[area]
        entries.append({"name": area, "description": None, "level": 1, "parent": None, "domain": area, "domain_code": code})
        for name, description in domains:
            entries.append(
                {"name": name, "description": description, "level": 2, "parent": area, "domain": area, "domain_code": code}
            )
    return entries


def _apqc_entries() -> List[Dict[str, Any]]:
    """The APQC categories (level 1) and process groups (level 2), from the framework the product holds."""
    from app.modules.industry_apqc.framework_data import CATEGORIES, PROCESS_GROUPS

    categories = dict(CATEGORIES)
    codes = {name: "APQC-%s" % code.split(".")[0] for code, name in CATEGORIES}
    entries = [
        {"name": name, "description": None, "level": 1, "parent": None, "domain": name, "domain_code": codes[name]}
        for _code, name in CATEGORIES
    ]
    for code, name in PROCESS_GROUPS:
        parent = categories[code.split(".")[0] + ".0"]
        entries.append(
            {"name": name, "description": None, "level": 2, "parent": parent, "domain": parent, "domain_code": codes[parent]}
        )
    return entries


_ENTRIES = {"BIAN": _bian_entries, "APQC": _apqc_entries}


def _load_capabilities(org_id: int, model: str) -> int:
    """Add the starter capabilities of ``model`` the organisation does not have yet; returns how many were added.
    A capability is the same one when its name, level and starter source are, so loading twice adds nothing."""
    from app.models.business_capabilities import BusinessCapability

    source = _starter_source(model)
    have = {
        (row.name, row.level): row.id
        for row in BusinessCapability.query.filter_by(organization_id=org_id, discovery_source=source).all()
    }
    added = 0
    for entry in _ENTRIES[model]():
        key = (entry["name"], entry["level"])
        if key in have:
            continue
        parent_id = have.get((entry["parent"], entry["level"] - 1)) if entry["parent"] else None
        row = BusinessCapability(
            organization_id=org_id, name=entry["name"], description=entry["description"], level=entry["level"],
            business_domain=entry["domain"], parent_capability_id=parent_id, discovery_source=source,
        )
        db.session.add(row)
        db.session.flush()
        have[key] = row.id
        added += 1
    return added


def _insert_domain(code: str, name: str):
    from sqlalchemy.dialects.postgresql import insert

    from app.models.unified_capability import BusinessDomain

    return insert(BusinessDomain.__table__).values(code=code, name=name).on_conflict_do_nothing(index_elements=["code"])


def _shared_domain(code: str, name: str):
    """The shared domain row for a framework item's stable code, created if it is not there yet. The insert
    does nothing when the code exists, so two organisations loading at once end up with one row."""
    from app.models.unified_capability import BusinessDomain

    db.session.execute(_insert_domain(code, name))
    return BusinessDomain.query.filter_by(code=code).one()


def _place_on_the_map(org_id: int, model: str) -> None:
    """Give the map rows projected from this organisation's starter capabilities the domain their framework
    names (the BIAN business area, the APQC category), so no card reads Uncategorised."""
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability

    entries = {(e["name"], e["level"]): e for e in _ENTRIES[model]()}
    domains: Dict[str, int] = {}
    ids_by_domain: Dict[str, List[str]] = {}
    for row in BusinessCapability.query.filter_by(organization_id=org_id, discovery_source=_starter_source(model)).all():
        entry = entries.get((row.name, row.level))
        if entry is None:
            continue
        if entry["domain_code"] not in domains:
            domain = _shared_domain(entry["domain_code"], entry["domain"])
            domains[entry["domain_code"]] = domain.id
        ids_by_domain.setdefault(entry["domain_code"], []).append(str(row.id))
    for code, source_ids in ids_by_domain.items():
        UnifiedCapability.query.filter(
            UnifiedCapability.organization_id == org_id,
            UnifiedCapability.source_table == "business_capability",
            UnifiedCapability.source_id.in_(source_ids),
            UnifiedCapability.domain_id.is_(None),
        ).update({"domain_id": domains[code]}, synchronize_session=False)
    db.session.commit()


def _adopt_frameworks(org_id: int, keys: List[str]) -> int:
    """Adopt each chosen framework that has a catalogue and is not adopted yet; returns how many were adopted."""
    from app.models.compliance_models import RegulatoryFramework
    from app.models.regulatory_framework import FrameworkAdoption
    from app.modules.compliance.services.applicability_service import ApplicabilityService
    from app.services.compliance.regulatory_framework_service import RegulatoryFrameworkService

    codes = [FRAMEWORK_CATALOGUES[key] for key in keys if key in FRAMEWORK_CATALOGUES]
    if not codes:
        return 0
    RegulatoryFrameworkService.seed_manufacturing_frameworks(codes=set(codes))
    adopted = 0
    for key in keys:
        code = FRAMEWORK_CATALOGUES.get(key)
        framework = RegulatoryFramework.query.filter_by(code=code).first() if code else None
        if framework is None:
            continue
        if FrameworkAdoption.query.filter_by(organization_id=org_id, framework_id=framework.id).first():
            continue
        ApplicabilityService.adopt_framework(
            org_id, framework.id, adopted_by_id=None,
            source="starter:%s" % dict((k, label) for k, label, _n in FRAMEWORKS)[key],
        )
        adopted += 1
    return adopted


def run_starter_data(org_id: int) -> Dict[str, Any]:
    """Load, for one organisation, the capability model its industry names and the catalogues of the
    frameworks it chose. Safe to run again: whatever is there already is left alone."""
    from app.models.organization import Organization

    org = db.session.get(Organization, org_id)
    if org is None:
        raise ValueError("Organisation %s not found" % org_id)
    chosen = choices(org)
    model = _starter_model(chosen["industry"])
    capabilities = _load_capabilities(org_id, model) if model else 0
    db.session.commit()
    if model:
        _place_on_the_map(org_id, model)
    _refresh_capability_map(org_id)
    adopted = _adopt_frameworks(org_id, chosen["frameworks"])
    _mark_elements_as_starter(org_id)
    return {"success": True, "model": model, "capabilities_added": capabilities, "frameworks_adopted": adopted}


def _unedited(row, entry: Dict[str, Any]) -> bool:
    """A starter row nobody has changed: still the catalogue's name, text and level, with nothing recorded on it
    and no application mapped to it in either mapping store (the capability map reads the unified one)."""
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    if (row.description or None) != entry["description"] or row.business_domain != entry["domain"]:
        return False
    if any(getattr(row, field) is not None for field in ("business_owner", "it_owner", "current_maturity_level", "target_maturity_level", "strategic_importance")):
        return False
    if ApplicationCapabilityMapping.query.filter_by(business_capability_id=row.id).first() is not None:
        return False
    return (
        UnifiedApplicationCapabilityMapping.query.join(
            UnifiedCapability, UnifiedApplicationCapabilityMapping.unified_capability_id == UnifiedCapability.id
        )
        .filter(
            UnifiedCapability.organization_id == row.organization_id,
            UnifiedCapability.source_table == "business_capability",
            UnifiedCapability.source_id == str(row.id),
        )
        .first()
        is None
    )


def _removable_rows(org_id: int, model: str):
    """The starter rows of ``model`` that can go, deepest first: nobody has changed them and no row
    that stays hangs under them."""
    from app.models.business_capabilities import BusinessCapability

    entries = {(e["name"], e["level"]): e for e in _ENTRIES[model]()}
    everything = BusinessCapability.query.filter_by(organization_id=org_id).all()
    children: Dict[int, List[Any]] = {}
    for row in everything:
        if row.parent_capability_id:
            children.setdefault(row.parent_capability_id, []).append(row)
    starter = [r for r in everything if r.discovery_source == _starter_source(model) and (r.name, r.level) in entries]
    goes: Dict[int, bool] = {}
    for row in sorted(starter, key=lambda r: -r.level):
        goes[row.id] = _unedited(row, entries[(row.name, row.level)]) and all(
            goes.get(child.id, False) for child in children.get(row.id, [])
        )
    return [row for row in sorted(starter, key=lambda r: -r.level) if goes[row.id]]


def removable_starter_rows(org) -> List[Dict[str, Any]]:
    """The starter models other than the one the industry now names that still have rows nobody changed:
    what Settings offers to remove after the industry is changed."""
    current = _starter_model(choices(org)["industry"])
    offers = []
    for model in _ENTRIES:
        if model == current:
            continue
        count = len(_removable_rows(org.id, model))
        if count:
            offers.append({"model": model, "count": count})
    return offers


def remove_unedited_starter_rows(org, model: str) -> int:
    """Remove the starter rows of ``model`` nobody changed, and keep any row that still has a changed
    one under it. Returns how many were removed."""
    if model not in _ENTRIES or model == _starter_model(choices(org)["industry"]):
        raise ValueError("Choose a starter model you are no longer using.")
    removed = 0
    mirrored = []
    for row in _removable_rows(org.id, model):
        if row.archimate_element_id:
            mirrored.append((row.archimate_element_id, row.name, row.description))
        db.session.delete(row)
        db.session.flush()
        removed += 1
    _remove_unchanged_elements(org.id, mirrored)
    db.session.commit()
    _refresh_capability_map(org.id)
    return removed


def _remove_unchanged_elements(org_id: int, mirrored) -> None:
    """Remove the model elements mirrored from removed starter capabilities, unless somebody has changed one
    or linked it to something. Each goes through the one element-removal helper, which deletes through the
    session, so the audit log records every removed element."""
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.modules.applications.routes._helpers import _delete_mirror_archimate_element

    if not mirrored:
        return
    wanted = {element_id: (name, description) for element_id, name, description in mirrored}
    linked = set()
    for source_id, target_id in db.session.query(ArchiMateRelationship.source_id, ArchiMateRelationship.target_id).filter(
        db.or_(ArchiMateRelationship.source_id.in_(wanted), ArchiMateRelationship.target_id.in_(wanted))
    ):
        linked.update((source_id, target_id))
    unchanged = []
    for element in ArchiMateElement.query.filter(
        ArchiMateElement.organization_id == org_id, ArchiMateElement.id.in_(wanted)
    ).all():
        name, description = wanted[element.id]
        if (
            element.source_kind == "starter" and element.name == name and element.id not in linked
            and (element.description or None) in (description or None, "Business capability: %s" % name)
        ):
            unchanged.append(element)
    if not unchanged:
        return
    outcome = _delete_mirror_archimate_element([element.id for element in unchanged])
    if outcome["errors"]:
        # One element something else still refers to must not keep the others; take them one at a time.
        for element in unchanged:
            for problem in _delete_mirror_archimate_element(element.id)["errors"]:
                _log.warning("%s (organisation %s)", problem, org_id)
