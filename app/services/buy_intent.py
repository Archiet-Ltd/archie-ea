"""Carry a chosen plan from the pricing page through sign-up and sign-in.

The pricing page's Buy buttons link to registration with ``plan`` and
``interval`` in the address. Those values are checked here against the one plan
catalogue (``billing_plans``) and turned into the one billing entry point
(``billing.billing_index``), which shows that plan's checkout step. Nothing the
visitor sends is ever used as a redirect target directly.
"""

from __future__ import annotations

from typing import Optional, Tuple

from flask import current_app, request, session, url_for

from app.utils.safe_redirect import is_safe_next_url

SESSION_KEY = "_buy_intent"


def parse(plan, interval) -> Optional[Tuple[str, str]]:
    """Return ``(plan, interval)`` for a purchasable plan, else None.

    An absent or unknown interval means annual, as the billing page does.
    """
    from app.services import billing_plans

    if not isinstance(plan, str) or plan not in {p.key for p in billing_plans.purchasable_plans()}:
        return None
    if interval not in billing_plans.INTERVALS:
        interval = "year"
    return plan, interval


def target_url(plan, interval) -> Optional[str]:
    """The billing page's checkout step for a valid plan, else None."""
    chosen = parse(plan, interval)
    if chosen is None or "billing.billing_index" not in current_app.view_functions:
        return None
    return url_for("billing.billing_index", plan=chosen[0], interval=chosen[1]) + "#checkout"


def requested() -> Optional[Tuple[str, str]]:
    """The plan named in this request's address, if it is valid."""
    return parse(request.args.get("plan"), request.args.get("interval"))


def plan_given() -> bool:
    return bool(request.args.get("plan"))


def remember(chosen: Tuple[str, str]) -> None:
    session[SESSION_KEY] = {"plan": chosen[0], "interval": chosen[1]}


def remembered_url() -> Optional[str]:
    held = session.get(SESSION_KEY)
    if not isinstance(held, dict):
        return None
    return target_url(held.get("plan"), held.get("interval"))


def pending_url() -> Optional[str]:
    """Checkout step for the plan in this request or remembered in the session."""
    chosen = requested()
    if chosen is not None:
        return target_url(*chosen)
    return remembered_url()


def next_candidate(consume: bool = False) -> Optional[str]:
    """The ``next`` value a sign-in should honour.

    An explicit same-origin ``next`` wins; otherwise the chosen plan's checkout
    step. The result still goes through ``safe_next_url`` at the redirect.
    """
    explicit = request.args.get("next")
    url = explicit if is_safe_next_url(explicit) else pending_url()
    if consume:
        session.pop(SESSION_KEY, None)
    return url
