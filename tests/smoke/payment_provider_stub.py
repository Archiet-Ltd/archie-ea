"""Loopback stand-in for the payment provider's customer and invoice API.

Test-only. The browser journey points one server at it through
STRIPE_API_BASE so saving billing details makes the same provider calls it
makes in production (create the customer, modify it, read it back) and the
saved values come back only if the application really sent them.
"""
import json
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from urllib.parse import parse_qs, urlparse

SECRET_KEY = "sk_test_smoke_stub_only"


def _customer_fields(form, customer):
    if "email" in form:
        customer["email"] = form["email"][0] or None
    fields = []
    for key, values in form.items():
        match = re.fullmatch(r"invoice_settings\[custom_fields\]\[(\d+)\]\[(name|value)\]", key)
        if match:
            index = int(match.group(1))
            while len(fields) <= index:
                fields.append({})
            fields[index][match.group(2)] = values[0]
    if fields or "invoice_settings[custom_fields]" in form:
        customer["invoice_settings"] = {"custom_fields": fields}


class PaymentProviderStub:
    """Customers held in memory; every invoice list is empty."""

    def __init__(self):
        self.customers = {}
        self.requests = []
        self._lock = Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _reply(self, status, body):
                payload = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _form(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8") if length else ""
                return parse_qs(raw, keep_blank_values=True)

            def _handle(self):
                path = urlparse(self.path).path
                form = self._form() if self.command == "POST" else {}
                with owner._lock:
                    owner.requests.append((self.command, path))
                    if self.headers.get("Authorization") != "Bearer " + SECRET_KEY:
                        return self._reply(401, {"error": {"message": "bad key", "type": "invalid_request_error"}})
                    if path == "/v1/customers" and self.command == "POST":
                        customer = {"id": "cus_" + uuid.uuid4().hex[:14], "object": "customer",
                                    "email": None, "address": None,
                                    "invoice_settings": {"custom_fields": None}}
                        _customer_fields(form, customer)
                        owner.customers[customer["id"]] = customer
                        return self._reply(200, customer)
                    match = re.fullmatch(r"/v1/customers/([\w]+)", path)
                    if match and match.group(1) in owner.customers:
                        customer = owner.customers[match.group(1)]
                        if self.command == "POST":
                            _customer_fields(form, customer)
                        return self._reply(200, customer)
                    if path == "/v1/invoices" and self.command == "GET":
                        return self._reply(200, {"object": "list", "data": [], "has_more": False,
                                                 "url": "/v1/invoices"})
                    return self._reply(404, {"error": {"message": "not stubbed: " + path,
                                                       "type": "invalid_request_error"}})

            do_GET = _handle
            do_POST = _handle

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self._server.server_address[1]
        self._thread = Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

    def child_environment(self, env):
        env = dict(env)
        env.update({
            "STRIPE_SECRET_KEY": SECRET_KEY,
            "STRIPE_WEBHOOK_SECRET": "whsec_smoke_stub_only",
            "STRIPE_API_BASE": self.base,
            "STRIPE_PRICE_STARTUP_ANNUAL": "price_smoke_startup_year",
            "STRIPE_PRICE_STARTUP_MONTHLY": "price_smoke_startup_month",
            "STRIPE_PRICE_TEAM_ANNUAL": "price_smoke_team_year",
            "STRIPE_PRICE_TEAM_MONTHLY": "price_smoke_team_month",
        })
        env["STRIPE_AUTOMATIC_TAX"] = ""
        return env
