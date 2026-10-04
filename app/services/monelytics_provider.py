"""
MonelyticsProvider — checkout and subscription state through Archiet's shared
billing service, Monelytics, for Entelim organisations.

Monelytics itself syncs plans to Stripe and Flutterwave underneath; this
module never calls either directly. app/services/billing_service.py is the
one entry point that uses this module, and only when MONELYTICS_BASE_URL is
set — the direct Stripe path (or "payment is not set up") stays the fallback
when it is not. See app/services/billing_plans.py for the plan catalogue this
maps onto (ENTELIM's "startup"/"team" plan keys and "month"/"year" intervals).

Credentials come from the environment only, and are never written to a
tracked file:
    MONELYTICS_BASE_URL            Monelytics' own API root, no path suffix
    MONELYTICS_KEYCLOAK_TOKEN_URL  the realm's client-credentials token endpoint
    MONELYTICS_CLIENT_ID
    MONELYTICS_CLIENT_SECRET
With MONELYTICS_BASE_URL unset, ``configured()`` is False and nothing in this
module ever makes a network call.

Staging and production are both reached only through MONELYTICS_BASE_URL: no
Monelytics hostname is ever hardcoded here, staging or production, so which
one a deployment talks to is entirely an operator decision made outside this
code.

Nothing here writes a plan onto the local ``subscriptions`` row before
Monelytics confirms it is live: ``start_checkout`` only returns a checkout
URL, and ``refresh_subscription`` (called from BillingService.
refresh_from_monelytics) is the sole path that reports what Monelytics
currently holds, for the caller to apply.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# The ENTELIM product code, once Monelytics' owner registers it on their side
# and creates ENTELIM's products, features and plans on staging (their work,
# through their own plan-administration tooling, not called from here). Not a
# secret: it is the same constant every Entelim installation sends.
PRODUCT_CODE = "ENTELIM"

REQUEST_TIMEOUT = 10  # seconds; every call this module makes has one.
TOKEN_REFRESH_SKEW = 30  # seconds of safety margin before a cached token expires.

SETTINGS = (
    "MONELYTICS_BASE_URL",
    "MONELYTICS_KEYCLOAK_TOKEN_URL",
    "MONELYTICS_CLIENT_ID",
    "MONELYTICS_CLIENT_SECRET",
)

NOT_CONFIGURED = "Online payment is not set up on this installation."
PROVIDER_REFUSED = "The payment provider did not accept the request. Nothing was changed."
UNREACHABLE = "The payment provider could not be reached. Nothing was changed."

# Entelim's billing_plans plan key <-> Monelytics' plan code.
_PLAN_CODE = {"startup": "STARTUP", "team": "TEAM"}
_PLAN_CODE_REVERSE = {v: k for k, v in _PLAN_CODE.items()}

# Entelim's "month"/"year" intervals <-> Monelytics' variant intervals.
_INTERVAL = {"month": "monthly", "year": "yearly"}
_INTERVAL_REVERSE = {v: k for k, v in _INTERVAL.items()}

# Monelytics subscription status -> Entelim's stored status (app.models.
# subscription.SubscriptionStatus). A status not listed here leaves the
# stored status as it was rather than guessing — the same rule
# BillingService._STATUS_MAP follows for Stripe.
STATUS_MAP = {
    "active": "active",
    "trialing": "trialing",
    "past_due": "past_due",
    "grace_period": "past_due",
    "canceled": "cancelled",
}

# Never call these, on any Monelytics host, from this module: internal
# billing endpoints, plan admin (create/update/sync), the provider webhook
# receivers, metered consumption, or credit-purchase initiation. Every path
# this module calls is listed right below it, and nothing else.
_PATH_PLANS = "/api/billing/plans"
_PATH_SUBSCRIPTIONS = "/api/billing/subscriptions"


class MonelyticsError(Exception):
    """A Monelytics call that did not happen. The message is safe to show."""


class MonelyticsNotConfigured(MonelyticsError):
    """One or more of the four Monelytics settings is absent."""


def configured() -> bool:
    """Whether Monelytics is the active provider for this installation.

    Only MONELYTICS_BASE_URL decides this, matching the brief's single
    switch: the other three settings being absent still makes this True, so
    the billing page shows "payment is not set up" for the right reason
    (Monelytics is the intended provider, but is not fully configured yet)
    rather than silently falling back to the direct Stripe path.
    """
    return bool(os.environ.get("MONELYTICS_BASE_URL"))


def missing_settings() -> List[str]:
    return [name for name in SETTINGS if not os.environ.get(name)]


def configuration_status() -> Dict[str, Any]:
    """Which of the four Monelytics settings are present. Never returns values."""
    missing = missing_settings()
    return {"ready": not missing, "missing": missing}


def _base_url() -> str:
    return (os.environ.get("MONELYTICS_BASE_URL") or "").rstrip("/")


def tenant_id_for(org) -> str:
    """Entelim's organisation, mapped 1:1 onto the tenant id Monelytics keys
    everything on (X-Tenant-Id header / tenantId field).

    Monelytics' own tenant id is free-form; until Ebuka's dedicated Keycloak
    client resolves a real Keycloak organisation id for each Entelim
    organisation, this stable, deterministic value is that mapping — the same
    organisation always sends the same tenant id, and no two organisations
    ever share one.
    """
    return f"entelim-org-{org.id}"


def plan_key_for_code(code: Optional[str]) -> Optional[str]:
    """Entelim's billing_plans plan key for a Monelytics plan code, or None."""
    return _PLAN_CODE_REVERSE.get((code or "").upper())


def interval_for_variant(value: Optional[str]) -> Optional[str]:
    """Entelim's "month"/"year" for a Monelytics variant interval, or None."""
    return _INTERVAL_REVERSE.get(value)


# --------------------------------------------------------------------------- #
# Keycloak client-credentials token: fetched once, cached until shortly       #
# before it expires.                                                          #
# --------------------------------------------------------------------------- #

_token_lock = threading.Lock()
_token_cache: Dict[str, Any] = {"value": None, "expires_at": 0.0}


def reset_token_cache() -> None:
    """Forget any cached token. Tests only."""
    with _token_lock:
        _token_cache["value"] = None
        _token_cache["expires_at"] = 0.0


def _fetch_token() -> str:
    token_url = os.environ.get("MONELYTICS_KEYCLOAK_TOKEN_URL")
    client_id = os.environ.get("MONELYTICS_CLIENT_ID")
    client_secret = os.environ.get("MONELYTICS_CLIENT_SECRET")
    if not (token_url and client_id and client_secret):
        raise MonelyticsNotConfigured(NOT_CONFIGURED)
    try:
        resp = requests.request(
            "POST",
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.error("Monelytics token request failed: %s", exc)
        raise MonelyticsError(UNREACHABLE) from exc
    if resp.status_code >= 400:
        logger.error(
            "Monelytics token request refused: %s %s", resp.status_code, resp.text[:200]
        )
        raise MonelyticsError(PROVIDER_REFUSED)
    try:
        body = resp.json()
    except ValueError as exc:
        raise MonelyticsError(PROVIDER_REFUSED) from exc
    token = body.get("access_token")
    if not token:
        raise MonelyticsError(PROVIDER_REFUSED)
    expires_in = body.get("expires_in", 60)
    try:
        expires_in = int(expires_in)
    except (TypeError, ValueError):
        expires_in = 60
    with _token_lock:
        _token_cache["value"] = token
        _token_cache["expires_at"] = time.monotonic() + max(expires_in - TOKEN_REFRESH_SKEW, 5)
    return token


def _get_token(force: bool = False) -> str:
    if not force:
        with _token_lock:
            cached, expires_at = _token_cache["value"], _token_cache["expires_at"]
        if cached and time.monotonic() < expires_at:
            return cached
    return _fetch_token()


# --------------------------------------------------------------------------- #
# HTTP boundary                                                                #
# --------------------------------------------------------------------------- #


def _request(
    method: str,
    path: str,
    *,
    tenant_id: Optional[str] = None,
    json_body: Optional[Dict] = None,
    params: Optional[Dict] = None,
    not_found_ok: bool = False,
) -> Any:
    """One call to Monelytics, with a bearer token and (when given) a tenant
    header. Retries once with a freshly fetched token on a 401 — the cached
    token may have just expired early. Raises MonelyticsError on anything
    else that is not a plain success, unless *not_found_ok* and the response
    is 404, in which case this returns None.
    """
    if not configured():
        raise MonelyticsNotConfigured(NOT_CONFIGURED)

    url = f"{_base_url()}{path}"
    headers: Dict[str, str] = {}
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id

    def _send(token: str):
        headers["Authorization"] = f"Bearer {token}"
        return requests.request(
            method, url, headers=headers, json=json_body, params=params, timeout=REQUEST_TIMEOUT
        )

    try:
        resp = _send(_get_token())
        if resp.status_code == 401:
            resp = _send(_get_token(force=True))
    except requests.RequestException as exc:
        logger.error("Monelytics %s %s failed: %s", method, path, exc)
        raise MonelyticsError(UNREACHABLE) from exc

    if not_found_ok and resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        message = PROVIDER_REFUSED
        try:
            detail = resp.json().get("error")
            if detail:
                message = str(detail)
        except ValueError:
            pass
        logger.error("Monelytics %s %s refused: %s %s", method, path, resp.status_code, resp.text[:200])
        raise MonelyticsError(message)
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError as exc:
        raise MonelyticsError(PROVIDER_REFUSED) from exc


# --------------------------------------------------------------------------- #
# Plans: resolved fresh for each checkout, never stored locally.               #
# --------------------------------------------------------------------------- #


def _resolve_plan_variant(plan_key: str, interval: str, tenant_id: str) -> Tuple[str, str]:
    plan_code = _PLAN_CODE.get(plan_key)
    variant_interval = _INTERVAL.get(interval)
    if plan_code is None or variant_interval is None:
        raise MonelyticsError(f"{plan_key}/{interval} has no Monelytics plan configured.")
    body = _request("GET", _PATH_PLANS, tenant_id=tenant_id, params={"product": PRODUCT_CODE})
    plans = body if isinstance(body, list) else (body or {}).get("plans") or []
    for plan in plans:
        if str(plan.get("code", "")).upper() != plan_code:
            continue
        for variant in plan.get("variants") or []:
            if variant.get("interval") == variant_interval:
                plan_id, variant_id = plan.get("id"), variant.get("id")
                if plan_id and variant_id:
                    return plan_id, variant_id
    raise MonelyticsError(
        f"The {plan_key} plan is not set up on Monelytics yet. Try again once it is."
    )


# --------------------------------------------------------------------------- #
# Checkout                                                                     #
# --------------------------------------------------------------------------- #


def start_checkout(
    org,
    plan_key: str,
    interval: str,
    seats: Optional[int],
    success_url: str,
    cancel_url: str,
) -> str:
    """Start Monelytics' hosted checkout for *org* and return its URL.

    Writes nothing locally: the organisation's plan is only ever set from a
    refresh call (refresh_subscription), once Monelytics confirms the
    subscription is live. Setting it here, before the shopper has paid, would
    show a plan that might never be paid for — and would wrongly lock the
    organisation out of trying again if they abandon checkout.
    """
    tenant_id = tenant_id_for(org)
    plan_id, variant_id = _resolve_plan_variant(plan_key, interval, tenant_id)
    body: Dict[str, Any] = {
        "tenantId": tenant_id,
        "productCode": PRODUCT_CODE,
        "planId": plan_id,
        "variantId": variant_id,
        "paymentProvider": "stripe",
        "successUrl": success_url,
        "cancelUrl": cancel_url,
    }
    if seats:
        body["seatCount"] = seats
    response = _request("POST", _PATH_SUBSCRIPTIONS, tenant_id=tenant_id, json_body=body)
    checkout_url = (response or {}).get("checkoutUrl")
    if not checkout_url:
        raise MonelyticsError(PROVIDER_REFUSED)
    return checkout_url


# --------------------------------------------------------------------------- #
# Refresh                                                                      #
# --------------------------------------------------------------------------- #


def refresh_subscription(org) -> Optional[Dict[str, Any]]:
    """The organisation's ENTELIM subscription as Monelytics currently holds
    it (the schemas.Subscription JSON object), or None when Monelytics has no
    subscription for this organisation yet — a fresh signup still on
    Community, or a checkout that was never completed.
    """
    tenant_id = tenant_id_for(org)
    return _request(
        "GET", f"{_PATH_SUBSCRIPTIONS}/{PRODUCT_CODE}", tenant_id=tenant_id, not_found_ok=True
    )
