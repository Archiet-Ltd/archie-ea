"""Loopback stand-in for Monelytics' tenant billing API and its hosted checkout.

Test-only. The server under test is pointed at it through the four MONELYTICS_*
settings, so the application makes the calls it makes against staging (token,
plans, open checkout, read the subscription, cancel, resume) and nothing leaves
the machine. ``fail_next`` makes the next API call answer 503.
"""
import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from urllib.parse import parse_qs, urlparse

TENANT_PREFIX = "entelim-org-"


class MonelyticsStub:
    def __init__(self):
        self.subs = {}      # tenant -> subscription dict
        self.pending = {}   # checkout id -> (tenant, plan, interval, success_url)
        self.requests = []  # (method, path, tenant)
        self.down = False
        self.cancel_free = False        # DELETE answers 400: the free plan has nothing to cancel
        self.resume_conflict = False    # POST resume answers 409: the cancellation already took effect
        self.get_fails_after_cancel = False  # GET answers 503 once a cancellation is held
        self._lock = Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, status, body=None, ctype="application/json", headers=None):
                raw = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _handle(self, method):
                url = urlparse(self.path)
                path = url.path
                raw = self._body()
                if path == "/token":
                    return self._send(200, {"access_token": "stub-token", "expires_in": 3600})
                if path.startswith("/pay/"):
                    return owner._pay(self, path, url)
                if not path.startswith("/api/"):
                    return self._send(404, b"", "text/plain")
                tenant = self.headers.get("X-Tenant-Id")
                with owner._lock:
                    owner.requests.append((method, path, tenant))
                    if owner.down:
                        return self._send(503, {"error": "upstream down"})
                    return owner._api(self, method, path, tenant, raw)

            def do_GET(self): self._handle("GET")
            def do_POST(self): self._handle("POST")
            def do_DELETE(self): self._handle("DELETE")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self._server.server_address[1]
        self._thread = Thread(target=self._server.serve_forever, daemon=True)

    def _sub_json(self, tenant, sub):
        return {"id": sub["id"], "tenantId": tenant, "productId": "ENTELIM", "planCode": sub["plan"],
                "billingInterval": sub["interval"], "status": "active", "seatCount": 1,
                "currentPeriodEnd": "2030-01-15T00:00:00Z", "cancelAtPeriodEnd": sub["cancel"]}

    def _api(self, h, method, path, tenant, raw):
        if method == "GET" and path == "/api/billing/plans":
            return h._send(200, [
                {"id": "plan-%s" % c.lower(), "code": c, "variants": [
                    {"id": "var-%s-%s" % (c.lower(), i), "interval": i, "price": 49.0}
                    for i in ("monthly", "yearly")]} for c in ("STARTUP", "TEAM")])
        if method == "POST" and path == "/api/billing/subscriptions":
            body = json.loads(raw or b"{}")
            if body.get("tenantId") != tenant:
                return h._send(403, {"error": "tenant mismatch"})
            cid = uuid.uuid4().hex[:10]
            plan = "STARTUP" if "startup" in body["planId"] else "TEAM"
            interval = "monthly" if body["variantId"].endswith("monthly") else "yearly"
            self.pending[cid] = (tenant, plan, interval, body["successUrl"])
            return h._send(202, {"subscription": {"id": cid, "status": "pending"},
                                 "checkoutUrl": "%s/pay/%s" % (self.base, cid)})
        if method == "GET" and path == "/api/billing/subscriptions/ENTELIM":
            sub = self.subs.get(tenant)
            if sub and sub["cancel"] and self.get_fails_after_cancel:
                return h._send(503, {"error": "upstream down"})
            return h._send(200, self._sub_json(tenant, sub)) if sub else h._send(404, {"error": "none"})
        sub = self.subs.get(tenant)
        prefix = "/api/billing/subscriptions/"
        if sub and path.startswith(prefix + sub["id"]):
            if method == "DELETE" and path == prefix + sub["id"]:
                if self.cancel_free:
                    return h._send(400, {"error": "cannot cancel the free Community plan"})
                sub["cancel"] = True
                # The real contract: 204 and no body.
                h.send_response(204)
                h.send_header("Content-Length", "0")
                h.end_headers()
                return None
            if method == "POST" and path == prefix + sub["id"] + "/resume":
                if self.resume_conflict:
                    return h._send(409, {"error": "the cancellation has already taken effect"})
                sub["cancel"] = False
                return h._send(200, self._sub_json(tenant, sub))
        return h._send(404, {"error": "not found"})

    def _pay(self, h, path, url):
        cid = path.split("/")[2]
        entry = self.pending.get(cid)
        if not entry:
            return h._send(404, b"unknown checkout", "text/plain")
        tenant, plan, interval, success = entry
        if path.endswith("/complete"):
            with self._lock:
                self.subs[tenant] = {"id": cid, "plan": plan, "interval": interval, "cancel": False}
            return h._send(303, b"", "text/plain", {"Location": success})
        page = ("<!doctype html><title>Pay</title><main><h1>Pay for %s</h1>"
                "<a href='/pay/%s/complete'>Pay</a></main>" % (plan.title(), cid)).encode()
        return h._send(200, page, "text/html")

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

    def child_environment(self, env):
        env = dict(env)
        env.update({
            "MONELYTICS_BASE_URL": self.base,
            "MONELYTICS_KEYCLOAK_TOKEN_URL": self.base + "/token",
            "MONELYTICS_CLIENT_ID": "stub-client",
            "MONELYTICS_CLIENT_SECRET": "stub-secret-not-real",
        })
        return env
