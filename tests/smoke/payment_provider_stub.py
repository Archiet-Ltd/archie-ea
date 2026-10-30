"""Loopback stand-in for the payment provider's customer, invoice and checkout API.

Test-only. The browser journeys point one server at it through
STRIPE_API_BASE so saving billing details, buying a plan and cancelling it make
the same provider calls they make in production (create the customer, open a
checkout, read the subscription back, schedule its end) and the saved values
come back only if the application really sent them. It also serves the
provider's hosted payment page, so a journey pays the way a person does in the
provider's test mode, and signs the events the provider would send.
"""
import hashlib
import hmac
import json
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from urllib.parse import parse_qs, urlparse

SECRET_KEY = "sk_test_smoke_stub_only"
WEBHOOK_SECRET = "whsec_smoke_stub_only"

# The configured price ids and what each costs, in cents.
PRICES = {
    "price_smoke_startup_year": (49000, "year", "Startup"),
    "price_smoke_startup_month": (4900, "month", "Startup"),
    "price_smoke_team_year": (29000, "year", "Team"),
    "price_smoke_team_month": (2900, "month", "Team"),
}


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
        self.sessions = {}
        self.subscriptions = {}
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
                    pay = re.fullmatch(r"/pay/(cs_[\w]+)", path)
                    if pay and pay.group(1) in owner.sessions:
                        return self._pay_page(owner.sessions[pay.group(1)])
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
                    if path == "/v1/checkout/sessions" and self.command == "POST":
                        return self._reply(200, owner._open_session(form))
                    match = re.fullmatch(r"/v1/checkout/sessions/([\w]+)", path)
                    if match and match.group(1) in owner.sessions:
                        return self._reply(200, owner._session_view(owner.sessions[match.group(1)]))
                    match = re.fullmatch(r"/v1/prices/([\w]+)", path)
                    if match and match.group(1) in PRICES:
                        cents, interval, _name = PRICES[match.group(1)]
                        return self._reply(200, {"id": match.group(1), "object": "price", "currency": "usd",
                                                 "unit_amount": cents, "recurring": {"interval": interval}})
                    match = re.fullmatch(r"/v1/subscriptions/([\w]+)", path)
                    if match and match.group(1) in owner.subscriptions:
                        subscription = owner.subscriptions[match.group(1)]
                        if self.command == "POST" and "cancel_at_period_end" in form:
                            subscription["cancel_at_period_end"] = form["cancel_at_period_end"][0] == "true"
                        return self._reply(200, subscription)
                    if path == "/v1/invoices" and self.command == "GET":
                        return self._reply(200, {"object": "list", "data": [], "has_more": False,
                                                 "url": "/v1/invoices"})
                    return self._reply(404, {"error": {"message": "not stubbed: " + path,
                                                       "type": "invalid_request_error"}})

            def _pay_page(self, session):
                """The provider's hosted payment page: a Pay button, then back to the shop."""
                if self.command == "POST":
                    self._form()
                    target = owner._complete(session)
                    self.send_response(303)
                    self.send_header("Location", target)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                _cents, interval, name = PRICES[session["price"]]
                page = (
                    "<!doctype html><html><head><title>Test payment</title></head><body>"
                    "<main><h1>Pay for %s</h1><p>Test mode: no card is charged.</p>"
                    "<p>%.2f USD per %s</p><form method=\"post\"><button type=\"submit\">Pay</button></form>"
                    "</main></body></html>" % (name, _cents / 100.0, interval)
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)

            do_GET = _handle
            do_POST = _handle

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self._server.server_address[1]
        self._thread = Thread(target=self._server.serve_forever, daemon=True)

    def _open_session(self, form):
        session_id = "cs_test_" + uuid.uuid4().hex[:16]
        session = {
            "id": session_id, "object": "checkout.session", "status": "open",
            "url": "%s/pay/%s" % (self.base, session_id),
            "customer": form["customer"][0],
            "client_reference_id": form["client_reference_id"][0],
            "price": form["line_items[0][price]"][0],
            "quantity": int(form["line_items[0][quantity]"][0]),
            "success_url": form["success_url"][0],
            "subscription": None,
        }
        self.sessions[session_id] = session
        return self._session_view(session)

    def _session_view(self, session):
        return {k: v for k, v in session.items() if k not in ("price", "quantity", "success_url")}

    def _complete(self, session):
        """The payment goes through: a subscription now exists; return where the shop is sent next.

        Called with the stub's lock already held, by the pay page."""
        if session["status"] != "complete":
            sub_id = "sub_" + uuid.uuid4().hex[:14]
            end = int(time.time()) + 365 * 86400
            self.subscriptions[sub_id] = {
                "id": sub_id, "object": "subscription", "status": "active",
                "customer": session["customer"], "cancel_at_period_end": False,
                "current_period_end": end,
                "items": {"object": "list", "data": [{
                    "id": "si_" + uuid.uuid4().hex[:10], "quantity": session["quantity"],
                    "price": {"id": session["price"]}, "current_period_end": end,
                }]},
            }
            session["subscription"] = sub_id
            session["status"] = "complete"
        return session["success_url"].replace("{CHECKOUT_SESSION_ID}", session["id"])

    def period_ended_event(self, subscription_id):
        """The signed event the provider sends when a cancelled subscription's paid period is over.

        Returns (body, signature header) to post to the shop's webhook address.
        """
        subscription = dict(self.subscriptions[subscription_id], status="canceled")
        body = json.dumps({
            "id": "evt_" + uuid.uuid4().hex[:14], "object": "event",
            "type": "customer.subscription.deleted", "created": int(time.time()),
            "data": {"object": subscription},
        }).encode("utf-8")
        stamp = str(int(time.time()))
        digest = hmac.new(WEBHOOK_SECRET.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        return body, "t=%s,v1=%s" % (stamp, digest)

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
            "STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET,
            "STRIPE_API_BASE": self.base,
            "STRIPE_PRICE_STARTUP_ANNUAL": "price_smoke_startup_year",
            "STRIPE_PRICE_STARTUP_MONTHLY": "price_smoke_startup_month",
            "STRIPE_PRICE_TEAM_ANNUAL": "price_smoke_team_year",
            "STRIPE_PRICE_TEAM_MONTHLY": "price_smoke_team_month",
        })
        env["STRIPE_AUTOMATIC_TAX"] = ""
        return env
