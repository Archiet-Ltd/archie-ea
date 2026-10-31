"""The one decision about which route an organisation's AI requests take.

Free plan, no key of its own: the free models. Paid plan: the paid models the platform pays for.
The organisation's own key: that key only. A route never crosses over: a free plan never spends the
paid route, and a paid plan or own key never falls through to a free model.

Every call site that sends organisation content asks this module, through ``route_for``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import FrozenSet, Optional

logger = logging.getLogger(__name__)

FREE = "free"
PAID = "paid"
OWN = "own"

# What a free-plan person is told before their first request.
FREE_ROUTE_NOTICE = (
    "On the free plan, your questions and the parts of your model needed to answer them are processed "
    "by free third-party models. Add your own AI key or choose a paid plan to keep your content off them."
)


@dataclass(frozen=True)
class Route:
    kind: str
    own_providers: FrozenSet[str] = frozenset()
    # False for work with no organisation behind it (a command, the scheduler).
    scoped: bool = True

    def allows(self, provider: str, model: Optional[str] = "") -> bool:
        """Whether a request to this provider and model stays on the route."""
        from app.modules.ai_chat.services.llm_service_impl import LLMService
        from app.modules.ai_chat.services.model_defaults import is_free_model

        provider = (provider or "").strip().lower()
        if self.kind == OWN:
            return provider in self.own_providers
        if self.kind == FREE:
            return is_free_model(provider, model or "")
        # the platform's paid route: a provider the platform pays for, never a free model
        return provider in LLMService.provider_order() and not is_free_model(provider, model or "")

    @property
    def is_free(self) -> bool:
        return self.kind == FREE

    @property
    def pauses(self) -> bool:
        """True when a failed route ends in the plain pause rather than another provider's error."""
        return self.scoped and self.kind != FREE


def _own_providers(organization_id: int) -> FrozenSet[str]:
    from app.models.models import APISettings
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    rows = APISettings.query.filter_by(organization_id=organization_id, enabled=True).all()
    return frozenset(
        r.provider.lower() for r in rows
        if r.provider and r.provider.lower() in LLMService.PROVIDERS and r.has_key()
    )


def _plan_key(organization_id: int) -> str:
    from app.models.organization import Organization
    from app.services import billing_plans

    org = Organization.query.filter_by(id=organization_id).first()
    if org is None:
        return "free"
    return billing_plans.effective_plan(billing_plans.current_subscription(org)).key


def route_for(organization_id: Optional[int]) -> Route:
    """The route this organisation's requests take.

    An organisation's own key outranks its plan. No organisation (a command, the scheduler) is the
    platform's own paid route: organisation content never goes to a free model by default.
    A plan that cannot be read is treated as paid, never as free: a free route is chosen only on a
    plan that was read.
    """
    if organization_id is None:
        return Route(PAID, scoped=False)
    try:
        own = _own_providers(organization_id)
        if own:
            return Route(OWN, own)
        return Route(FREE if _plan_key(organization_id) == "free" else PAID)
    except Exception:  # noqa: BLE001 - an unreadable plan is never read as free
        logger.warning("route_for: could not read the plan or keys of organisation %s", organization_id, exc_info=True)
        return Route(PAID)


def current_route() -> Route:
    """The route of the organisation making the current request."""
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    return route_for(LLMService._resolve_org_id())


def free_route_notice() -> Optional[dict]:
    """The notice for the person making this request, or None when their organisation is not on the free route.

    ``links`` are (label, address) pairs: the way to add an own key and the way to see plans.
    """
    from flask import current_app, url_for

    route = current_route()
    if not (route.is_free and route.scoped):
        return None
    views = current_app.view_functions
    key_href = url_for("admin.api_settings") if "admin.api_settings" in views else None
    plans_href = url_for("billing.billing_index") + "#plans" if "billing.billing_index" in views else "/pricing"
    links = [("See plans", plans_href), ("How your content is handled", "/privacy#ai-features")]
    if key_href:
        links.insert(0, ("Add your own key", key_href))
    return {"text": FREE_ROUTE_NOTICE, "links": links}
