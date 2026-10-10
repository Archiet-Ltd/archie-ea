"""The demonstration organisation: how a prospect reaches it and how a trial starts.

There is one demonstration organisation (the fictional company created by
``flask seed-demo-company``). It is an ordinary organisation whose settings
carry a ``demonstration`` flag; every other decision here reads that flag.

* A prospect is signed in as the demonstration organisation's read-only
  viewer. The data is invented and stays in that organisation.
* Starting a trial creates a new organisation through the ordinary sign-up
  flow and marks its subscription as a trial on the Community plan, so the
  people limit is the one ``billing_plans`` already enforces. Nothing from the
  demonstration organisation is copied into it.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

DEMO_SLUG = "lantern-quay"
DEMO_USER_EMAIL = "demo@lantern-quay.example.com"
DEMO_SETTINGS_KEY = "demonstration"
TRIAL_DAYS = 14
TRIAL_PLAN_KEY = "free"


def mark_demonstration(org) -> None:
    """Flag *org* as the demonstration organisation. The caller commits."""
    settings = dict(org.settings or {})
    settings[DEMO_SETTINGS_KEY] = True
    org.settings = settings


def is_demonstration(org) -> bool:
    return bool(org is not None and (org.settings or {}).get(DEMO_SETTINGS_KEY))


def is_demonstration_org_id(org_id: Optional[int]) -> bool:
    if not org_id:
        return False
    from app import db
    from app.models.organization import Organization

    return is_demonstration(db.session.get(Organization, org_id))


def demonstration_org():
    """The demonstration organisation, or None when it has not been created."""
    from app.models.organization import Organization

    org = Organization.query.filter_by(slug=DEMO_SLUG).first()
    return org if is_demonstration(org) else None


def demonstration_viewer():
    """The demonstration organisation's read-only viewer, or None."""
    from app.models.user import User

    org = demonstration_org()
    if org is None:
        return None
    return User.query.filter_by(email=DEMO_USER_EMAIL, organization_id=org.id).first()


def enter_demonstration():
    """Sign the visitor in as the demonstration viewer.

    Returns the viewer, or None when the demonstration is not available (so
    the caller says so rather than showing an empty organisation).
    """
    from app.services import session_registry

    viewer = demonstration_viewer()
    if viewer is None or not getattr(viewer, "is_active", True):
        return None
    session_registry.login_and_register(viewer)
    return viewer


def start_trial(user) -> None:
    """Put *user*'s organisation on a trial. The caller commits.

    The trial uses the Community plan's limits (``billing_plans``): the same
    people limit the seat counter and the add-user screens already apply.
    A demonstration organisation never becomes a trial.
    """
    from app import db
    from app.models.organization import Organization
    from app.models.subscription import SubscriptionPlan, SubscriptionStatus
    from app.services.billing_plans import ensure_subscription, get_plan

    org = db.session.get(Organization, user.organization_id)
    if org is None or is_demonstration(org):
        raise ValueError("A trial cannot start in the demonstration organisation.")
    plan = get_plan(TRIAL_PLAN_KEY)
    ends = _dt.datetime.utcnow() + _dt.timedelta(days=TRIAL_DAYS)
    sub = ensure_subscription(org)
    sub.plan = SubscriptionPlan[plan.key]
    sub.status = SubscriptionStatus.trialing
    sub.seats_purchased = plan.user_limit or 0
    sub.current_period_end = ends
    settings = dict(org.settings or {})
    settings["trial"] = {
        "started_from_demonstration": True,
        "ends_on": ends.date().isoformat(),
    }
    org.settings = settings
