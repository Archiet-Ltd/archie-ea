"""
Plan catalogue and plan limits.

The organisation's plan lives on its ``subscriptions`` row (written by
BillingService from checkout and from the payment provider's signed events).
This module is the only place that turns that row into limits, so the billing
page, the add-user screens and the seat counter all give the same answer.

Price ids are never in code: each purchasable plan names the environment
variables that hold its monthly and annual price ids.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

INTERVALS = ("year", "month")


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    summary: str
    # True when the plan can be bought online; Enterprise is sold by contract.
    purchasable: bool
    # Fixed number of people the plan admits; None with per_seat=True means
    # the number of seats bought, None with per_seat=False means no limit.
    user_limit: Optional[int]
    per_seat: bool = False
    default_seats: int = 1
    # "people" counts every member; "editors" leaves read-only members free.
    counts: str = "people"
    price_env: Dict[str, str] = field(default_factory=dict)

    # ----------------------------------------------------------------- #
    # Display price: the human-facing dollar figures shown on the        #
    # marketing site (pricing page, home page) and emitted in every       #
    # page's JSON-LD structured data. This is the one place those        #
    # figures live; it is never read by checkout or by the plan-limit    #
    # enforcement above, which use price_env (the Stripe price ids) and  #
    # user_limit/per_seat instead -- nothing about what is actually      #
    # charged changes by adding these fields.                            #
    # ----------------------------------------------------------------- #
    display_currency: str = "USD"
    # Flat price in whole dollars for that billing interval, or -- when
    # display_price_per_unit is True -- the per-unit (e.g. per editor)
    # price in whole dollars. None when the plan has no fixed figure to
    # show for that interval; Enterprise has neither (see
    # display_price_floor_annual below), since it has no flat price at all.
    display_price_monthly: Optional[int] = None
    display_price_annual: Optional[int] = None
    # True when display_price_monthly/annual is a price *per seat*
    # (e.g. per editor), not a flat plan price. A caller rendering this
    # must use a UnitPriceSpecification with a referenceQuantity, not a
    # bare Offer.price, so it is never read as a flat charge.
    display_price_per_unit: bool = False
    display_price_unit: Optional[str] = None  # e.g. "editor"
    # Enterprise only: a floor on an annual contract price. It is not a
    # fixed price and the plan is not purchasable online (purchasable is
    # False above) -- a caller must represent this as a minimum / "from"
    # price (e.g. schema.org AggregateOffer.lowPrice) or a contact-sales
    # offer, never as a flat, purchasable Offer.price.
    display_price_floor_annual: Optional[int] = None


PLANS: Tuple[Plan, ...] = (
    Plan(
        key="free",
        name="Community",
        summary="One organisation, three people. Every question, the Business Model Canvas, the twin map.",
        purchasable=False,
        user_limit=3,
        display_price_monthly=0,
    ),
    Plan(
        key="startup",
        name="Startup",
        summary=(
            "Ten people. Everything in Community, plus audit export and email support."
        ),
        purchasable=True,
        user_limit=10,
        price_env={"month": "STRIPE_PRICE_STARTUP_MONTHLY", "year": "STRIPE_PRICE_STARTUP_ANNUAL"},
        display_price_monthly=49,
        display_price_annual=490,
    ),
    Plan(
        key="team",
        name="Team",
        summary=(
            "Priced per editor; people who only ask questions are free. Single "
            "sign-on, the review-board workflow, and your own model key."
        ),
        purchasable=True,
        user_limit=None,
        per_seat=True,
        default_seats=15,
        counts="editors",
        price_env={"month": "STRIPE_PRICE_TEAM_MONTHLY", "year": "STRIPE_PRICE_TEAM_ANNUAL"},
        display_price_monthly=29,
        display_price_annual=290,
        display_price_per_unit=True,
        display_price_unit="editor",
    ),
    Plan(
        key="enterprise",
        name="Enterprise",
        summary="Annual contract. Unlimited editors, SAML, audit export, supported self-hosting.",
        purchasable=False,
        user_limit=None,
        display_price_floor_annual=24000,
    ),
)

_BY_KEY = {p.key: p for p in PLANS}
# Rows written before this catalogue carry "pro"; it bought seats like Team.
_LEGACY_KEYS = {"pro": "team"}

CONTACT_SALES_URL = "/contact"


def get_plan(key: Optional[str]) -> Plan:
    """Return the plan for a stored key; an unknown or empty key is Community."""
    key = _LEGACY_KEYS.get(key or "", key or "")
    return _BY_KEY.get(key, _BY_KEY["free"])


def purchasable_plans() -> Tuple[Plan, ...]:
    return tuple(p for p in PLANS if p.purchasable)


def price_id_for(plan_key: str, interval: str) -> Optional[str]:
    plan = _BY_KEY.get(plan_key)
    if plan is None or interval not in plan.price_env:
        return None
    return os.environ.get(plan.price_env[interval]) or None


def plan_for_price(price_id: Optional[str]) -> Optional[Tuple[Plan, str]]:
    """Return (plan, interval) for a configured price id, or None when unknown."""
    if not price_id:
        return None
    for plan in PLANS:
        for interval, env_name in plan.price_env.items():
            if os.environ.get(env_name) == price_id:
                return plan, interval
    return None


def configuration_status() -> Dict:
    """Which billing settings are present. Never returns the values."""
    missing = [
        name
        for name in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET")
        if not os.environ.get(name)
    ]
    try:
        import stripe  # noqa: F401
    except ImportError:
        missing.insert(0, "stripe (Python package)")
    prices = {
        (p.key, interval): bool(os.environ.get(env))
        for p in purchasable_plans()
        for interval, env in p.price_env.items()
    }
    if not any(prices.values()):
        missing.extend(env for p in purchasable_plans() for env in p.price_env.values())
    return {"ready": not missing, "missing": missing, "prices": prices}


# --------------------------------------------------------------------------- #
# Plan limits                                                                  #
# --------------------------------------------------------------------------- #


def _subscription_row(org_id: int):
    from app.models.subscription import Subscription

    return Subscription.query.filter_by(organization_id=org_id).first()


def effective_plan(sub) -> Plan:
    """The plan whose limits apply now.

    A deleted subscription (status cancelled) is back on Community; a
    subscription whose payment failed keeps its plan while the provider
    retries. A cancellation scheduled for the period end keeps the plan until
    the provider sends the deletion.
    """
    if sub is None:
        return _BY_KEY["free"]
    from app.models.subscription import SubscriptionStatus

    if sub.status == SubscriptionStatus.cancelled:
        return _BY_KEY["free"]
    return get_plan(sub.plan.value if sub.plan is not None else None)


def _seed_plan(legacy_plan: Optional[str], legacy_max_users: Optional[int]):
    """(SubscriptionPlan, seats) for an organisation that has no subscriptions
    row yet: one created before billing existed, whose plan a platform
    administrator had recorded on ``organizations.plan`` / ``max_users``.

    Those two columns are read only here, and only until the organisation's
    subscriptions row exists; nothing writes them any more.
    """
    from app.models.subscription import SubscriptionPlan

    legacy = (legacy_plan or "").lower()
    if legacy == "enterprise":
        return SubscriptionPlan.enterprise, legacy_max_users or 0
    if legacy in ("pro", "team"):
        return SubscriptionPlan.team, legacy_max_users or _BY_KEY["team"].default_seats
    if legacy == "startup":
        return SubscriptionPlan.startup, _BY_KEY["startup"].user_limit
    return SubscriptionPlan.free, _BY_KEY["free"].user_limit


def current_subscription(org):
    """The organisation's subscriptions row, without writing anything.

    When the row does not exist yet, returns an unsaved row holding the plan
    it would be created with, so pages that only read (the billing page, the
    organisation list, the add-user forms) never write.
    """
    from app.models.subscription import Subscription, SubscriptionStatus

    sub = _subscription_row(org.id)
    if sub is not None:
        return sub
    plan, seats = _seed_plan(getattr(org, "plan", None), getattr(org, "max_users", None))
    return Subscription(
        organization_id=org.id, plan=plan, status=SubscriptionStatus.active, seats_purchased=seats
    )


def ensure_subscription(org):
    """Return the organisation's subscriptions row, creating it when absent.

    For write paths only (checkout, provider events, plan changes): the row is
    flushed, not committed, and is committed with the caller's own change.
    """
    from sqlalchemy.exc import IntegrityError

    from app import db

    sub = _subscription_row(org.id)
    if sub is not None:
        return sub
    sub = current_subscription(org)
    try:
        with db.session.begin_nested():
            db.session.add(sub)
    except IntegrityError:
        # Another request created it first.
        sub = _subscription_row(org.id)
    return sub


def set_contract_plan(org, plan_key: str, seats: Optional[int]) -> bool:
    """Record a plan a platform administrator sold by contract on the
    organisation's subscriptions row. Returns False, changing nothing, while
    the organisation pays online: the payment provider owns that plan.
    The caller commits.
    """
    from app.models.subscription import SubscriptionPlan, SubscriptionStatus
    from app.services.billing_service import BillingService

    sub = ensure_subscription(org)
    if BillingService.has_live_subscription(sub):
        return False
    plan = get_plan(plan_key)
    sub.plan = SubscriptionPlan[plan.key]
    if plan.per_seat:
        sub.seats_purchased = seats if seats and seats > 0 else plan.default_seats
    else:
        sub.seats_purchased = plan.user_limit or 0
    sub.status = SubscriptionStatus.active
    return True


def _limit_for(plan: Plan, seats: Optional[int]) -> Optional[int]:
    return seats if plan.per_seat else plan.user_limit


def _count_members(connection, org_id: int, counts: str) -> int:
    """People (or, on an editors plan, people who are not read-only) in *org_id*.

    Runs on a Core connection so it gives the same answer inside a flush.
    """
    from sqlalchemy import and_, distinct, func, or_, select

    from app.models.org_role import OrgRole
    from app.models.user import User

    users = User.__table__
    roles = OrgRole.__table__
    membership = (
        select(distinct(users.c.id).label("user_id"))
        .select_from(
            users.outerjoin(
                roles,
                and_(roles.c.user_id == users.c.id, roles.c.organization_id == org_id),
            )
        )
        .where(or_(users.c.organization_id == org_id, roles.c.organization_id == org_id))
    ).subquery()

    stmt = select(func.count()).select_from(membership)
    if counts == "editors":
        readers = select(roles.c.user_id).where(
            roles.c.organization_id == org_id, roles.c.role == "viewer"
        )
        stmt = stmt.where(membership.c.user_id.not_in(readers))
    return connection.execute(stmt).scalar_one()


def _status(plan: Plan, limit: Optional[int], used: int) -> Dict:
    return {
        "plan_key": plan.key,
        "plan_name": plan.name,
        "counts": plan.counts,
        "limit": limit,
        "used": used,
        "remaining": None if limit is None else max(limit - used, 0),
        "limit_reached": limit is not None and used >= limit,
    }


def user_limit_status(org_id: int) -> Dict:
    """The people limit that applies to *org_id* and how much of it is used.

    ``limit`` is None when the plan has no limit on people. Reads only.
    """
    from app import db
    from app.models.organization import Organization

    org = db.session.get(Organization, org_id)
    sub = current_subscription(org) if org is not None else None
    plan = effective_plan(sub)
    limit = _limit_for(plan, sub.seats_purchased if sub is not None else None)
    return _status(plan, limit, _count_members(db.session.connection(), org_id, plan.counts))


class PlanLimitReached(Exception):
    """Raised when adding someone would take an organisation past its plan."""

    def __init__(self, status: Dict):
        self.status = status
        people = "editors" if status["counts"] == "editors" else "people"
        super().__init__(
            f"This organisation's {status['plan_name']} plan admits {status['limit']} "
            f"{people} and it already has {status['used']}, so no one else can join it. "
            "An administrator can upgrade the plan on the billing page."
        )


# --------------------------------------------------------------------------- #
# The one enforcement point                                                    #
# --------------------------------------------------------------------------- #

# Users with no organisation are placed in the shared fallback organisation by
# User's before_insert listener. It is the platform's holding area, not a
# customer, and has no plan.
_UNPLANNED_ORG_SLUGS = frozenset({"default"})


@dataclass
class _Change:
    """What one flush does to an organisation's head count."""

    joining: int = 0  # people added to, or moved into, the organisation
    promoted: int = 0  # members who stop being read-only
    demoted: int = 0  # members who become read-only


def check_capacity(connection, org_id: int, change: "_Change") -> None:
    """Refuse, with PlanLimitReached, a flush that takes *org_id* past its plan.

    Locks the organisation's row (SELECT ... FOR UPDATE) before counting, so
    two requests adding the last place at once are serialised: the second
    counts after the first commits, and is refused.

    On a plan that counts editors, a new person takes an editor place until
    they are made read-only, and a read-only member made an editor takes one.
    """
    from sqlalchemy import select

    from app.models.organization import Organization
    from app.models.subscription import Subscription, SubscriptionStatus

    orgs = Organization.__table__
    columns = select(orgs.c.slug, orgs.c.plan, orgs.c.max_users).where(orgs.c.id == org_id)
    org = connection.execute(columns).first()
    # The shared fallback organisation is never locked: it has no plan, and
    # every sign-up without an organisation lands in it.
    if org is None or org.slug in _UNPLANNED_ORG_SLUGS:
        return
    org = connection.execute(columns.with_for_update()).first()
    subs = Subscription.__table__
    row = connection.execute(
        select(subs.c.plan, subs.c.status, subs.c.seats_purchased)
        .where(subs.c.organization_id == org_id)
    ).first()
    if row is None:
        plan_enum, seats = _seed_plan(org.plan, org.max_users)
        plan = get_plan(plan_enum.value)
    elif row.status == SubscriptionStatus.cancelled:
        plan, seats = _BY_KEY["free"], None
    else:
        plan, seats = get_plan(row.plan.value if row.plan is not None else None), row.seats_purchased
    limit = _limit_for(plan, seats)
    if limit is None:
        return
    used = _count_members(connection, org_id, plan.counts)
    adding = change.joining
    if plan.counts == "editors":
        adding += change.promoted
        used -= change.demoted
    if adding > 0 and used + adding > limit:
        raise PlanLimitReached(_status(plan, limit, used))


def _flush_changes(session) -> Dict[int, _Change]:
    """{organisation id: _Change} for the people and roles this flush writes."""
    from sqlalchemy import inspect as sa_inspect

    from app.models.org_role import OrgRole
    from app.models.user import User

    changes: Dict[int, _Change] = {}

    def at(org_id: int) -> _Change:
        return changes.setdefault(org_id, _Change())

    for obj in session.new:
        if isinstance(obj, User):
            org_id = obj.organization_id
            if org_id is None and obj.organization is not None:
                org_id = obj.organization.id
            # None: the shared fallback organisation, which has no plan, or
            # an organisation created in this same flush, which has no
            # members to count yet.
            if org_id is not None:
                at(org_id).joining += 1
        elif isinstance(obj, OrgRole) and obj.organization_id is not None:
            # A new row's role defaults to viewer. Before it, the member had no
            # role and was counted as an editor.
            if obj.role in (None, "viewer") and obj.user_id is not None:
                at(obj.organization_id).demoted += 1
    for obj in session.dirty:
        if isinstance(obj, User):
            history = sa_inspect(obj).attrs.organization_id.history
            if history.added and history.added[0] is not None:
                if not history.deleted or history.added[0] != history.deleted[0]:
                    at(history.added[0]).joining += 1
        elif isinstance(obj, OrgRole) and obj.organization_id is not None:
            history = sa_inspect(obj).attrs.role.history
            if not (history.added and history.deleted):
                continue
            was, now = history.deleted[0], history.added[0]
            if was == "viewer" and now != "viewer":
                at(obj.organization_id).promoted += 1
            elif was != "viewer" and now == "viewer":
                at(obj.organization_id).demoted += 1
    leaving = {obj.id for obj in session.deleted if isinstance(obj, User)}
    for obj in session.deleted:
        if (isinstance(obj, OrgRole) and obj.organization_id is not None
                and obj.role == "viewer" and obj.user_id not in leaving):
            # Without a role row the member counts as an editor.
            at(obj.organization_id).promoted += 1
    return changes


def _guard_flush(session, flush_context, instances) -> None:  # noqa: ARG001
    changes = {
        org_id: change
        for org_id, change in _flush_changes(session).items()
        if change.joining or change.promoted
    }
    if not changes:
        return
    connection = session.connection()
    for org_id in sorted(changes):  # a fixed order, so two flushes cannot deadlock
        check_capacity(connection, org_id, changes[org_id])


def install_user_limit_guard() -> None:
    """Every flush that adds a person to an organisation, or moves one into
    it, passes check_capacity first: the admin forms, invitations, single
    sign-on provisioning, signup and any future path alike."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    if not event.contains(Session, "before_flush", _guard_flush):
        event.listen(Session, "before_flush", _guard_flush)
