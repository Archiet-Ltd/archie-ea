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
WEBSITE_TASK = "website_profile_crawl"
MORE_FACTS_LATER = "More facts will be added when our reading service is less busy."
MORE_FACTS_FOLLOW = "More facts follow in a few minutes, as we finish working out what your pages say."

# (key, label, starter capability model). The model is the one starter set the industry picks
# later; this round only records the choice.
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

FRAMEWORKS = (
    ("iso27001", "ISO 27001", "Information security management"),
    ("soc2", "SOC 2", "Trust services criteria for service providers"),
    ("nistcsf", "NIST CSF", "Cybersecurity risk management"),
    ("gdpr", "GDPR", "Personal data protection in the EU"),
    ("safe", "SAFe", "Scaled agile delivery"),
    ("togaf", "TOGAF", "Enterprise architecture method"),
    ("archimate", "ArchiMate", "Enterprise architecture modelling language"),
    ("itil4", "ITIL 4", "IT service management"),
    ("cobit2019", "COBIT 2019", "IT governance"),
)

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
    return list(dict.fromkeys(keys))


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
    return choices(org)


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


def save_website(org, raw: Optional[str], consent: bool, user_id: Optional[int]) -> Optional[str]:
    """Record the site and the person's consent to read it. The reading itself comes later."""
    url = normalise_website(raw)
    if url is None:
        return None
    if not consent:
        raise ValueError("Tick the box to let us read this site.")
    state = _onboarding(org)
    state["website"] = {"url": url, "consent_by": user_id, "consent_at": datetime.utcnow().isoformat()}
    _write(org, "onboarding", state)
    db.session.commit()
    _queue_reading(org.id)
    return url


def _queue_reading(org_id: int) -> None:
    """Start reading the site in the background. A queue that is down never stops the person moving on."""
    from app.modules.organization.website_crawl import CrawlRefused, queue_website_crawl

    try:
        queue_website_crawl(org_id)
    except CrawlRefused:
        _log.info("website not read for organisation %s", org_id, exc_info=True)
    except Exception:
        _log.warning("could not queue the website reading for organisation %s", org_id, exc_info=True)


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
        return {"state": "reading", "text": text, "progress": {"read": read, "limit": limit, "percent": percent}}
    from app.services.job_queue_service import get_job_queue_service

    if get_job_queue_service().all_workers_busy():
        return {"state": "waiting", "text": "Company website is connected. Other companies' websites are being read first, so yours starts a little later; carry on."}
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
