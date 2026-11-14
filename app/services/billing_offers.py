"""Free offers recorded on the organisation's one subscriptions row.

The startup credit is 90 days of Startup, ten people, granted from the billing
page after six checks the product makes itself. It is a state of the same row
every plan lives on (plan, status, period end, seats) plus four offer columns;
no second store holds it, and the lapse rule that ends it is read at use in
``billing_plans.offer_lapsed``.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from html import escape
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError

from app import db

logger = logging.getLogger(__name__)

STARTUP_CREDIT = "startup_credit"
CREDIT_DAYS = 90
CREDIT_SEATS = 10
FOUNDED_WITHIN_MONTHS = 36
RAISED_ANSWERS = {"nothing": "Nothing yet", "up_to_1m": "Up to USD 1M", "more_than_1m": "More than USD 1M"}
_RAISED_OK = ("nothing", "up_to_1m")

REFUSAL_WORK_EMAIL = "Use your work email to claim the credit. You can change your address in your account settings."
REFUSAL_FOUNDED = "The credit is for companies founded in the last 36 months."
REFUSAL_RAISED = "The credit is for companies that have raised up to USD 1M."
REFUSAL_WEBSITE = "Your website's domain must match your email's domain."
REFUSAL_DOMAIN_TAKEN = "This company has already used its 90 days."
REFUSAL_PAID = "This organisation already has a paid plan."
REFUSAL_USED = "This organisation has already used its 90 days of Startup."


@lru_cache(maxsize=1)
def _extractor():
    import tldextract

    # The suffix list shipped inside the package; nothing is fetched at run time.
    return tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True, cache_dir=None)


def registrable_domain(value: Optional[str]) -> Optional[str]:
    """The company domain of an email address, a host or a web address: the
    registrable domain, lower-cased and IDNA-encoded. A host whose suffix is not
    listed gives its last two labels. None when there is no usable host.

    The one spelling both the website check and the one-credit-per-domain check
    use, so ``ACME.com``, ``eu.acme.com`` and the punycode form are one company.
    """
    text = (value or "").strip()
    if not text:
        return None
    if "@" in text:
        text = text.rsplit("@", 1)[-1]
    if "//" in text:
        text = urlsplit(text).hostname or ""
    else:
        text = text.split("/", 1)[0].split(":", 1)[0]
    host = text.strip(". ").lower()
    if not host:
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    registered = _extractor()(host).top_domain_under_public_suffix
    if registered:
        return registered
    labels = [part for part in host.split(".") if part]
    return ".".join(labels[-2:]) if len(labels) >= 2 else None


def startup_credit_domain_taken(domain: str, excluding_organization_id: int) -> bool:
    """Whether any other organisation already used the credit on *domain*.

    A yes or no and nothing else: one Core statement selecting an existence
    flag, run in a platform scope that closes when this function returns.
    """
    from app.jobs.tenant_safe_job import platform_scope
    from app.models.subscription import Subscription

    subs = Subscription.__table__
    statement = select(
        exists().where(
            subs.c.offer == STARTUP_CREDIT,
            subs.c.offer_domain == domain,
            subs.c.organization_id != excluding_organization_id,
        )
    )
    with platform_scope("startup credit: one per company domain"):
        return bool(db.session.execute(statement).scalar())


@dataclass
class CreditResult:
    granted: bool
    reason: Optional[str] = None
    code: Optional[str] = None


def _founded_month(value) -> Optional[datetime]:
    try:
        parsed = datetime.strptime(str(value or "").strip(), "%Y-%m")
    except ValueError:
        return None
    return parsed


def _months_between(earlier: datetime, later: datetime) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def grant_startup_credit(org, user, declaration: Dict, now: Optional[datetime] = None) -> CreditResult:
    """Grant 90 days of Startup, or refuse in plain words and write nothing.

    Six checks run in order and the first to fail refuses. *declaration* holds
    ``website`` (a web address), ``founded`` (``YYYY-MM``) and ``raised`` (one of
    ``RAISED_ANSWERS``). The caller commits a granted result.
    """
    from app.models.subscription import SubscriptionPlan, SubscriptionStatus
    from app.services.billing_plans import (
        current_subscription,
        effective_plan,
        ensure_subscription,
        offer_lapsed,
    )
    from app.services.billing_service import BillingService
    from app.services.signup_guard import is_disposable
    from app.services.sso_service import PUBLIC_MAILBOX_DOMAINS

    now = now or datetime.utcnow()
    results: List[Dict] = []

    def refuse(code: str, reason: str) -> CreditResult:
        return CreditResult(False, reason, code)

    def passed(code: str) -> None:
        results.append({"check": code, "passed": True})

    # 1. No live subscription, no paid plan, and the credit never used before.
    sub = current_subscription(org)
    if sub.offer == STARTUP_CREDIT:
        return refuse("used", REFUSAL_USED)
    if BillingService.has_live_subscription(sub) or (
        effective_plan(sub).key != "free" and not offer_lapsed(sub, now)
    ):
        return refuse("paid", REFUSAL_PAID)
    passed("no_paid_plan")

    # 2. A work address: not a public mailbox and not disposable.
    address = (getattr(user, "email", None) or "").strip().lower()
    host = address.rsplit("@", 1)[-1].strip(". ") if "@" in address else ""
    domain = registrable_domain(address)
    if not domain or host in PUBLIC_MAILBOX_DOMAINS or domain in PUBLIC_MAILBOX_DOMAINS or is_disposable(address):
        return refuse("work_email", REFUSAL_WORK_EMAIL)
    passed("work_email")

    # 3. Founded no more than 36 months ago.
    founded = _founded_month(declaration.get("founded"))
    if founded is None or founded > now or _months_between(founded, now) > FOUNDED_WITHIN_MONTHS:
        return refuse("founded", REFUSAL_FOUNDED)
    passed("founded")

    # 4. Nothing raised, or up to USD 1M.
    raised = declaration.get("raised")
    if raised not in _RAISED_OK:
        return refuse("raised", REFUSAL_RAISED)
    passed("raised")

    # 5. The website belongs to the same company as the address.
    site = declaration.get("website")
    if registrable_domain(site) != domain:
        return refuse("website", REFUSAL_WEBSITE)
    passed("website")

    # 6. One credit per company domain; asked only about this person's own domain.
    if startup_credit_domain_taken(domain, org.id):
        return refuse("domain_taken", REFUSAL_DOMAIN_TAKEN)
    passed("domain_free")

    sub = ensure_subscription(org)
    db.session.flush()  # the row exists before the savepoint, so only the grant can fail inside it
    try:
        with db.session.begin_nested():
            sub.plan = SubscriptionPlan.startup
            sub.status = SubscriptionStatus.trialing
            sub.current_period_end = now + timedelta(days=CREDIT_DAYS)
            sub.seats_purchased = CREDIT_SEATS
            sub.offer = STARTUP_CREDIT
            sub.offer_domain = domain
            sub.offer_granted_at = now
            sub.offer_terms = {
                "declaration": {"website": site, "founded": declaration.get("founded"), "raised": raised},
                "checks": results,
                "granted_by": getattr(user, "id", None),
                "granted_at": now.isoformat(),
                "reminders": [],
            }
            db.session.flush()
    except IntegrityError:
        # Another organisation took this domain between the check and the write.
        return refuse("domain_taken", REFUSAL_DOMAIN_TAKEN)
    logger.info("startup credit granted to organisation %s", org.id)
    return CreditResult(True)


def startup_credit_state(org, now: Optional[datetime] = None) -> Dict:
    """What the billing page says about the credit, from the organisation's own row.

    ``state`` is ``available`` (the credit can be claimed), ``active`` (running),
    ``ended`` (used and over) or ``unavailable`` (a paid plan, or the credit was
    used and then bought over). Never reads another organisation's row.
    """
    from app.services.billing_plans import current_subscription, effective_plan, offer_lapsed
    from app.services.billing_service import BillingService

    now = now or datetime.utcnow()
    sub = current_subscription(org)
    if sub.offer == STARTUP_CREDIT:
        end = sub.current_period_end
        if BillingService.has_live_subscription(sub) or sub.status.value != "trialing" or end is None:
            return {"state": "unavailable", "ends_on": end}
        if offer_lapsed(sub, now):
            return {"state": "ended", "ends_on": end, "days_left": 0}
        return {"state": "active", "ends_on": end, "days_left": max((end - now).days, 0)}
    if BillingService.has_live_subscription(sub) or effective_plan(sub).key != "free":
        return {"state": "unavailable", "ends_on": None}
    return {"state": "available", "ends_on": None}


def declared_answers(sub) -> Dict:
    """What the person declared last time, so a refused claim keeps its form filled in."""
    terms = getattr(sub, "offer_terms", None) or {}
    return dict(terms.get("declaration") or {})


def website_on_file(org) -> Optional[str]:
    """The company website given at sign-up, to prefill the claim form."""
    from app.modules.account.services.onboarding_service import website_url

    return website_url(org)


REMINDER_DAYS = (14, 3)


def _reminder_due(sub, now: datetime) -> Optional[int]:
    """The reminder (14 or 3 days before the end) this running credit owes, if any.
    The nearest threshold already passed; a larger one it skipped counts as sent."""
    terms = sub.offer_terms or {}
    sent = {item.get("days") for item in terms.get("reminders") or []}
    remaining = sub.current_period_end - now
    for days in sorted(REMINDER_DAYS):
        if remaining <= timedelta(days=days):
            return None if days in sent else days
    return None


def remind_one_organisation(app, organization_id: int, now: Optional[datetime] = None) -> Dict:
    """Send the reminder this organisation's credit is due: a bell notification and an
    email to each administrator, recorded on the row so none is sent twice. Runs inside
    the organisation's own scope."""
    from flask import url_for

    from app.models.models import Notification
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.administrator_guard import administrator_ids

    now = now or datetime.utcnow()
    org = db.session.get(Organization, organization_id)
    state = startup_credit_state(org, now) if org is not None else {"state": None}
    if state["state"] != "active":
        return {"organization_id": organization_id, "sent": 0}
    sub = org.subscription
    days = _reminder_due(sub, now)
    if days is None:
        return {"organization_id": organization_id, "sent": 0}

    ends = sub.current_period_end.strftime("%d %b %Y").lstrip("0")
    message = (
        f"Your 90 days of Startup end on {ends}, {days} days from now. "
        "Buy Startup before then to keep ten people."
    )
    with app.test_request_context():
        link = url_for("billing.billing_index")
    # tenant-scoping-ok: this organisation's own administrators, by id
    admins = User.query.filter(User.id.in_(administrator_ids(organization_id))).all()
    for person in admins:
        db.session.add(Notification(
            user_id=person.id, message=message, url=link, organization_id=organization_id,
            event_type="startup_credit_ending", entity_type="subscription", entity_id=sub.id,
        ))
    terms = dict(sub.offer_terms or {})
    terms["reminders"] = list(terms.get("reminders") or []) + [{"days": days, "sent_at": now.isoformat()}]
    sub.offer_terms = terms
    db.session.commit()

    from app._bootstrap._digest_emails import _safe_send_email

    recipients = [p.email for p in admins if p.email]
    address = escape(app.config.get("PUBLIC_BASE_URL", "").rstrip("/") + link)
    body = f"<p>{escape(message)}</p><p><a href=\"{address}\">Open billing</a></p>"
    _safe_send_email(app, "Your 90 days of Startup are ending", recipients, body)
    return {"organization_id": organization_id, "sent": len(admins), "days": days}


def send_offer_reminders(app, *, organization_ids=None, now: Optional[datetime] = None):
    """The daily job: remind each organisation whose startup credit ends in 14 or 3 days."""
    from app.jobs.tenant_safe_job import run_for_each_tenant

    return run_for_each_tenant(
        app, "offer-reminders", lambda organization_id: remind_one_organisation(app, organization_id, now),
        organization_ids=organization_ids,
    )
