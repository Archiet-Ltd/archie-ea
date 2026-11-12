"""A stand-in for Microsoft's sign-in and Graph services, for tests only.

It answers the two hosts the SharePoint connector calls (login.microsoftonline.com
and graph.microsoft.com) from a recorded fixture, in process, by replacing
``requests.Session.request``. No socket is opened and nothing leaves the machine.

Two ways to use it:
  * a unit test calls ``install(monkeypatch)`` and reads ``requests_seen`` afterwards;
  * a browser journey starts the server with ``SITECUSTOMIZE`` in a folder on its
    PYTHONPATH and ``GRAPH_STANDIN_FILE`` (this file) and ``GRAPH_STANDIN_LOG``
    (a file the stand-in appends one line per request to) in its environment.

A token only reaches the lists of the tenant that owns it; any other site answers
404, which is what Microsoft does. The stand-in serves nothing that writes.
"""

from __future__ import annotations

import json
import os
import re
from urllib.parse import parse_qs, unquote, urlparse

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "..", "fixtures", "sharepoint_contoso.json")
LOGIN_HOST = "login.microsoftonline.com"
GRAPH_HOST = "graph.microsoft.com"

# The text a journey writes into a folder on the server's PYTHONPATH as sitecustomize.py.
SITECUSTOMIZE = '''
def _install():
    import importlib.util
    import os

    path = os.environ.get("GRAPH_STANDIN_FILE")
    if not path:
        return
    spec = importlib.util.spec_from_file_location("graph_standin_peer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.install_into_requests()


_install()
'''


def load_world(path: str | None = None) -> dict:
    with open(path or FIXTURE_PATH) as handle:
        return json.load(handle)


class _Response:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body
        self.headers = {}
        self.reason = "OK" if status < 300 else "Error"
        self.text = json.dumps(body)
        self.is_redirect = False

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code} {self.reason}", response=self)


class GraphStandIn:
    def __init__(self, world: dict | None = None, log_path: str | None = None):
        self.world = world or load_world()
        self.log_path = log_path
        self.requests_seen: list[dict] = []

    # -- helpers -----------------------------------------------------------
    def _record(self, method: str, url: str, bearer: str | None) -> None:
        entry = {"method": method, "path": urlparse(url).path, "host": urlparse(url).netloc, "bearer": bearer}
        self.requests_seen.append(entry)
        if self.log_path:
            with open(self.log_path, "a") as handle:
                handle.write(json.dumps(entry) + "\n")

    def _tenant_for_token(self, token: str | None):
        for tenant in self.world["tenants"].values():
            if token and token == tenant["access_token"]:
                return tenant
        return None

    # -- the one entry point ----------------------------------------------
    def answer(self, method: str, url: str, headers: dict | None = None, data=None) -> _Response:
        headers = headers or {}
        auth = headers.get("Authorization") or ""
        bearer = auth[7:] if auth.startswith("Bearer ") else None
        self._record(method, url, bearer)
        parsed = urlparse(url)

        if parsed.netloc == LOGIN_HOST:
            match = re.match(r"^/([^/]+)/oauth2/v2\.0/token$", parsed.path)
            tenant = self.world["tenants"].get(match.group(1)) if match else None
            form = data if isinstance(data, dict) else {k: v[0] for k, v in parse_qs(data or "").items()}
            if (
                method.upper() == "POST"
                and tenant
                and form.get("client_id") == tenant["client_id"]
                and form.get("client_secret") == tenant["client_secret"]
            ):
                return _Response(200, {"access_token": tenant["access_token"], "expires_in": 3600})
            return _Response(401, {"error": "invalid_client"})

        if parsed.netloc != GRAPH_HOST or method.upper() != "GET":
            return _Response(405, {"error": {"code": "methodNotAllowed"}})

        tenant = self._tenant_for_token(bearer)
        if tenant is None:
            return _Response(401, {"error": {"code": "InvalidAuthenticationToken"}})
        path = unquote(parsed.path)
        match = re.match(r"^/v1\.0/sites/([^/]+)/lists(?:/([^/]+)/columns)?$", path)
        if not match:
            return _Response(404, {"error": {"code": "itemNotFound"}})
        site = next((s for s in tenant["sites"] if s["id"] == match.group(1)), None)
        if site is None:
            return _Response(404, {"error": {"code": "itemNotFound"}})
        if match.group(2) is None:
            return _Response(
                200,
                {"value": [{k: v for k, v in item.items() if k != "columns"} for item in site["lists"]]},
            )
        found = next((item for item in site["lists"] if item["id"] == match.group(2)), None)
        if found is None:
            return _Response(404, {"error": {"code": "itemNotFound"}})
        return _Response(200, {"value": found["columns"]})


def _patched_request(standin: GraphStandIn, original):
    def request(self, method=None, url=None, headers=None, data=None, **kw):
        host = urlparse(url or "").netloc
        if host not in (LOGIN_HOST, GRAPH_HOST):
            return original(self, method, url, headers=headers, data=data, **kw)
        merged = {**self.headers, **(headers or {})}
        return standin.answer(method, url, merged, data)

    return request


def install(monkeypatch, *, world: dict | None = None) -> GraphStandIn:
    """Answer Graph and sign-in calls in this process; returns the stand-in to read."""
    import requests

    standin = GraphStandIn(world)
    monkeypatch.setattr(requests.Session, "request", _patched_request(standin, requests.Session.request))
    return standin


def install_into_requests() -> None:
    """Called by the server process's sitecustomize."""
    import requests

    standin = GraphStandIn(log_path=os.environ.get("GRAPH_STANDIN_LOG"))
    requests.Session.request = _patched_request(standin, requests.Session.request)
