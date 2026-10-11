"""IndexNow: tell search engines a public URL changed instead of waiting for
re-crawl.

IndexNow is a free, no-account protocol shared by Bing, Yandex and other
participating search engines (https://www.indexnow.org/documentation): the
site operator generates its own key (any string -- nobody issues it), hosts
a verification file containing that key at ``/<key>.txt`` (see the
``public_indexnow_key_file`` view in app/main/views.py), and POSTs the list
of changed URLs to the one shared endpoint. No payment, no third-party
account, no API key issued by anyone -- the "key" is self-generated proof of
domain control, the same shape as GOOGLE_SITE_VERIFICATION / Bing's own
verification already used in this app.

No-op when INDEXNOW_API_KEY is unset -- the same settings-driven pattern as
GA4_MEASUREMENT_ID / CLARITY_PROJECT_ID.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"


def indexnow_key(app) -> str:
    return (app.config.get("INDEXNOW_API_KEY") or "").strip()


def is_enabled(app) -> bool:
    return bool(indexnow_key(app))


def key_location(base_url: str, key: str) -> str:
    return base_url.rstrip("/") + "/" + key + ".txt"


def build_payload(base_url: str, key: str, urls: Iterable[str]) -> Dict[str, Any]:
    host = urlparse(base_url).netloc
    return {
        "host": host,
        "key": key,
        "keyLocation": key_location(base_url, key),
        "urlList": list(urls),
    }


def ping_indexnow(app, urls: List[str], base_url: Optional[str] = None) -> Optional[dict]:
    """POST *urls* to IndexNow. Returns the response status/body, or ``None``
    when INDEXNOW_API_KEY is unset (no-op) or *urls* is empty."""
    key = indexnow_key(app)
    if not key or not urls:
        return None

    base = base_url or app.config.get("PREFERRED_URL_SCHEME_HOST") or "https://entelim.org"
    payload = build_payload(base, key, urls)
    try:
        response = requests.post(INDEXNOW_ENDPOINT, json=payload, timeout=10)
        return {"status_code": response.status_code, "body": response.text[:500]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("IndexNow ping failed (non-blocking): %s", exc)
        return {"status_code": 0, "body": str(exc)}


__all__ = [
    "INDEXNOW_ENDPOINT",
    "indexnow_key",
    "is_enabled",
    "key_location",
    "build_payload",
    "ping_indexnow",
]
