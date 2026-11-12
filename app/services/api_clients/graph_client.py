"""
Microsoft Graph client (read only)

Reads lists and columns of a SharePoint site with the customer's own access
token. Built on the same base client as the GitHub client: it sends the token
as a bearer header, refuses a redirect (a followed redirect would carry the
token to wherever the answer points) and keeps the token out of every error
message.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from app.services.m365_service import GRAPH_BASE

from .base_client import APIClientError, BaseAPIClient

logger = logging.getLogger(__name__)

# A site or list id is letters, digits, commas, hyphens, dots, braces and
# underscores. Anything else (a slash, a query, a space) never reaches a path.
_SEGMENT = re.compile(r"^[A-Za-z0-9,\-._{}!]{1,300}$")


class GraphError(RuntimeError):
    """Microsoft Graph did not give an answer; the message is plain and has no token."""


class GraphClient(BaseAPIClient):
    """Read-only Microsoft Graph client for SharePoint lists and columns."""

    def __init__(self, access_token: str, *, base_url: Optional[str] = None):
        super().__init__(
            base_url=base_url or GRAPH_BASE,
            api_key=access_token,
            rate_limit_per_minute=120,
            cache_ttl_seconds=0,
        )

    def _create_session(self):
        """The base session, plus: a redirect is refused, never followed."""
        session = super()._create_session()

        def refuse_redirect(response, *args, **kwargs):
            if response.status_code in (301, 302, 303, 307, 308):
                raise APIClientError("Microsoft Graph answered with a redirect, which is not followed")
            return response

        session.hooks["response"].append(refuse_redirect)
        return session

    def _scrub(self, text: str) -> str:
        """The token never leaves in a log line or an error message."""
        if self.api_key and isinstance(text, str):
            return text.replace(self.api_key, "***")
        return text

    def health_check(self) -> bool:
        try:
            return self.get("sites/root", use_cache=False).success
        except Exception:
            return False

    @staticmethod
    def segment(value: str) -> str:
        """One path segment built from an id, or GraphError when it is not a plain id."""
        if not isinstance(value, str) or not _SEGMENT.match(value):
            raise GraphError("That site or list id is not valid")
        return quote(value, safe=",")

    def _get_json(self, endpoint: str) -> Dict[str, Any]:
        response = self.get(endpoint, use_cache=False)
        if not response.success:
            raise GraphError(self._scrub(response.error or "Microsoft Graph did not answer"))
        if not isinstance(response.data, dict):
            raise GraphError("Microsoft Graph answered with something that is not a list")
        return response.data

    def lists_at(self, path: str, *, include_system: bool = False) -> List[Dict[str, Any]]:
        """The lists at ``path`` (``sites/<site>/lists``) as ``{id, name, display_name}``; system lists are left out."""
        data = self._get_json(path)
        found = []
        for item in data.get("value") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            facet = item.get("list") or {}
            if not include_system and (facet.get("hidden") or item.get("system") is not None):
                continue
            found.append(
                {
                    "id": item["id"],
                    "name": item.get("name") or "",
                    "display_name": item.get("displayName") or item.get("name") or "",
                }
            )
        return found

    def columns_at(self, path: str) -> List[Dict[str, Any]]:
        """The columns at ``path`` (``sites/<site>/lists/<list>/columns``) as ``{name, display_name, hidden, read_only}``."""
        data = self._get_json(path)
        found = []
        for item in data.get("value") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            found.append(
                {
                    "name": item["name"],
                    "display_name": item.get("displayName") or item["name"],
                    "hidden": bool(item.get("hidden")),
                    "read_only": bool(item.get("readOnly")),
                }
            )
        return found
