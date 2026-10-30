"""IBM watsonx.ai: the sign-in token, the chat call and the Granite model list.

watsonx.ai is called with a short-lived token that IBM Cloud IAM issues in exchange for an API key,
so this module holds the one thing the OpenAI-compatible providers do not need: the token, kept per
API key (never shared between two keys, so never between two organisations) until shortly before it
expires. The chat itself is ``POST {url}/ml/v1/text/chat`` with a project id and a model id; its
messages and tool calls have the OpenAI shape, so the agent loop treats it like any other tool provider.

Every failure is a ``WatsonxError`` whose message is plain, names IBM watsonx.ai and carries no key,
token or response body.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

PROVIDER = "watsonx"
NAME = "IBM watsonx.ai"
API_VERSION = "2024-10-08"
IAM_URL = "https://iam.cloud.ibm.com/identity/token"
# A token is renewed this long before IBM says it expires, so a call never starts with one about to lapse.
TOKEN_MARGIN_SECONDS = 60
TIMEOUT_SECONDS = 90
DEFAULT_MODEL = "ibm/granite-3-8b-instruct"

_tokens: Dict[str, Tuple[str, float]] = {}
_lock = threading.Lock()


class WatsonxError(RuntimeError):
    """A watsonx.ai call that failed, in words an administrator can act on."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def iam_url() -> str:
    return os.environ.get("WATSONX_IAM_URL") or IAM_URL


def _key_id(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def forget_tokens() -> None:
    """Drop every cached token (used by tests and when a key is replaced)."""
    with _lock:
        _tokens.clear()


def _failure(status: int, what: str) -> WatsonxError:
    if status in (401, 403):
        return WatsonxError(
            "%s rejected the API key or its access to this project (HTTP %d). "
            "Check the key, the project id and the service address in Admin, API Settings." % (NAME, status),
            status,
        )
    if status == 429:
        return WatsonxError("%s is rate limiting this account (HTTP 429). Try again shortly." % NAME, status)
    if status >= 500:
        return WatsonxError("%s is not responding (HTTP %d). Try again shortly." % (NAME, status), status)
    return WatsonxError("%s refused the %s (HTTP %d)." % (NAME, what, status), status)


def access_token(api_key: str, force: bool = False) -> str:
    """The IAM token for this API key: the cached one while it has time left, otherwise a fresh one."""
    if not (api_key or "").strip():
        raise WatsonxError("%s has no API key. Add one in Admin, API Settings." % NAME)
    kid = _key_id(api_key)
    now = time.time()
    with _lock:
        cached = _tokens.get(kid)
        if cached and not force and cached[1] - TOKEN_MARGIN_SECONDS > now:
            return cached[0]
    try:
        response = requests.post(
            iam_url(),
            data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": api_key},
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
    except requests.exceptions.Timeout:
        raise WatsonxError("%s did not answer the sign-in request in time. Try again shortly." % NAME) from None
    except requests.exceptions.RequestException:
        raise WatsonxError("%s could not be reached to sign in. Check the network and try again." % NAME) from None
    if response.status_code != 200:
        if response.status_code in (400, 401, 403):
            raise WatsonxError(
                "%s rejected the API key (HTTP %d). Check the key in Admin, API Settings." % (NAME, response.status_code),
                401,
            )
        raise _failure(response.status_code, "sign-in")
    try:
        body = response.json()
        token = body["access_token"]
        expires_at = float(body.get("expiration") or (now + float(body.get("expires_in") or 3600)))
    except (ValueError, KeyError, TypeError):
        raise WatsonxError("%s answered the sign-in request with something unreadable." % NAME) from None
    with _lock:
        _tokens[kid] = (token, expires_at)
    return token


def _chat_url(url: str) -> str:
    return "%s/ml/v1/text/chat?version=%s" % (url.rstrip("/"), API_VERSION)


def chat(
    api_key: str,
    url: str,
    project_id: str,
    model: str,
    messages: List[dict],
    tools: Optional[List[dict]] = None,
    max_tokens: int = 4096,
    temperature: float = 0.0,
) -> dict:
    """One chat completion. Returns the parsed response body; a stale token is renewed once."""
    if not (url or "").strip():
        raise WatsonxError("%s has no service address. Set it in Admin, API Settings." % NAME)
    if not (project_id or "").strip():
        raise WatsonxError("%s has no project id. Set it in Admin, API Settings." % NAME)
    payload: Dict[str, Any] = {
        "model_id": model,
        "project_id": project_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice_option"] = "auto"
    renewed = False
    token = access_token(api_key)
    while True:
        try:
            response = requests.post(
                _chat_url(url),
                json=payload,
                headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.exceptions.Timeout:
            raise WatsonxError("%s did not answer in time. Try again shortly." % NAME) from None
        except requests.exceptions.RequestException:
            raise WatsonxError("%s could not be reached. Check the service address and the network." % NAME) from None
        if response.status_code == 401 and not renewed:
            renewed = True
            token = access_token(api_key, force=True)
            continue
        if response.status_code != 200:
            raise _failure(response.status_code, "request")
        try:
            return response.json()
        except ValueError:
            raise WatsonxError("%s answered with something unreadable." % NAME) from None


def normalise(body: dict) -> dict:
    """The agent loop's shape ({text, tool_calls, raw}) from a chat response body."""
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise WatsonxError("%s answered without a message." % NAME) from None
    tool_calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except ValueError:
            arguments = {}
        tool_calls.append({"id": call.get("id") or "", "name": function.get("name") or "", "arguments": arguments})
    return {"text": message.get("content") or None, "tool_calls": tool_calls, "raw": body}


def list_granite_models(url: str) -> List[dict]:
    """The Granite instruct models the service offers for chat: [{id, name}], sorted by id.

    The specification list is public, so it needs no key.
    """
    if not (url or "").strip():
        raise WatsonxError("%s has no service address. Set it in Admin, API Settings." % NAME)
    try:
        response = requests.get(
            "%s/ml/v1/foundation_model_specs" % url.rstrip("/"),
            params={"version": API_VERSION, "limit": 200, "filters": "function_text_chat"},
            headers={"Accept": "application/json"},
            timeout=30,
        )
    except requests.exceptions.RequestException:
        raise WatsonxError("%s could not be reached to list models. Check the service address." % NAME) from None
    if response.status_code != 200:
        raise _failure(response.status_code, "model list request")
    try:
        resources = response.json().get("resources") or []
    except ValueError:
        raise WatsonxError("%s answered the model list request with something unreadable." % NAME) from None
    models = {}
    for item in resources:
        model_id = str(item.get("model_id") or "")
        if model_id.startswith("ibm/granite") and "instruct" in model_id:
            models[model_id] = {"id": model_id, "name": item.get("label") or model_id}
    return [models[k] for k in sorted(models)]


def settings_row():
    """The current organisation's enabled watsonx settings row, or None. Tenant filtering is the ORM's."""
    from app.models.models import APISettings
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    if LLMService._resolve_org_id() is None:
        return None  # outside a signed-in organisation there is no row that is anyone's to use
    try:
        return APISettings.query.filter_by(provider=PROVIDER, enabled=True).order_by(APISettings.id).first()
    except Exception:  # noqa: BLE001 - no settings table in reach: the platform's environment is all there is
        return None


def organisation_is_paid(org_id: Optional[int]) -> bool:
    """True when the organisation has a subscription that is not the free plan and is in good standing."""
    if not org_id:
        return False
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

    try:
        sub = Subscription.query.filter_by(organization_id=org_id).first()
    except Exception:  # noqa: BLE001 - an unreadable plan is treated as the free one
        return False
    return bool(
        sub
        and sub.plan != SubscriptionPlan.free
        and sub.status in (SubscriptionStatus.active, SubscriptionStatus.trialing)
    )


def entitled(org_id: Optional[int] = None) -> bool:
    """watsonx is a paid provider: the organisation's own key, or the platform's key on a paid plan."""
    if settings_row() is not None:
        return True
    if org_id is None:
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        org_id = LLMService._resolve_org_id()
    return organisation_is_paid(org_id)


def connection() -> Tuple[str, str]:
    """(service address, project id) for the current organisation: its own row, else the platform's settings."""
    row = settings_row()
    url = (row.custom_endpoint_url if row else None) or os.environ.get("WATSONX_URL") or ""
    project = (row.watsonx_project_id if row else None) or os.environ.get("WATSONX_PROJECT_ID") or ""
    return url.strip(), project.strip()


def complete(
    api_key: str, model: str, messages: List[dict], tools: Optional[List[dict]] = None, max_tokens: int = 4096
) -> dict:
    """The agent loop's call: refuses an organisation that may not use watsonx, then chats."""
    if not entitled():
        raise WatsonxError(
            "%s is available on a paid plan or with your organisation's own key. "
            "Add a key in Admin, API Settings." % NAME
        )
    url, project = connection()
    return normalise(chat(api_key, url, project, model, messages, tools=tools, max_tokens=max_tokens))


def check_connection(api_key: str, url: str, project_id: str, model: Optional[str]) -> dict:
    """A one-word chat, for the Test Connection button. Returns {success, message}."""
    try:
        chosen = model or DEFAULT_MODEL
        chat(api_key, url, project_id, chosen, [{"role": "user", "content": "test"}], max_tokens=5)
    except WatsonxError as error:
        return {"success": False, "message": str(error)}
    return {"success": True, "message": "Connection successful. Model: %s" % chosen}


def apply_form(settings, form) -> None:
    """Copy the watsonx fields of the API settings form onto a settings row."""
    settings.custom_endpoint_url = (form.watsonx_url.data or "").strip() or None
    settings.watsonx_project_id = (form.watsonx_project_id.data or "").strip() or None


def fill_form(settings, form) -> None:
    """Show a settings row's watsonx fields in the API settings form."""
    form.watsonx_url.data = settings.custom_endpoint_url or ""
    form.watsonx_project_id.data = settings.watsonx_project_id or ""
