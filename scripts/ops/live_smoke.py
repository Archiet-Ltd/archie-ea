"""One command that proves the first wave on a deployed site.

    python scripts/ops/live_smoke.py http://<preview-host>:5193
    python scripts/ops/live_smoke.py https://entelim.org --live https://entelim.org --confirm <word>

It checks the health endpoint (answers within five minutes), the public and signed-in pages of the first wave
with a throwaway ``smoke+<timestamp>@`` account, a sign-up with a company website that must reach the confirmed
facts step within five minutes, and the error rate across everything it asked. One PASS or FAIL line per item
is printed, then any rollback trigger it saw. Every account it made is erased again through the data-subject
erasure pages, and anything it could not erase is listed.

It never pays, never invites anyone, never migrates or seeds, and refuses any host that is not a preview unless
``--live <same url>`` and the confirmation word are given. The first-answer timer keeps its own refusal of a
live site; this script does not change it.
"""
from __future__ import annotations

import argparse
import ipaddress
import re
import secrets
import sys
import time
from urllib.parse import urlparse

import requests

CONFIRM_WORD = "RUN-SMOKE-ON-LIVE"
EMAIL_DOMAIN = "archiet.dev"
PUBLIC_PAGES = ("/", "/pricing", "/account/login")
SIGNED_IN_PAGES = ("/dashboard/overview", "/ai-chat", "/account/manage")
ERROR_RATE_LIMIT = 0.01
ERROR_TEXT = re.compile(r"Traceback \(most recent call|Internal Server Error|Something went wrong", re.I)

lines: list[str] = []
triggers: list[str] = []


def emit(text: str) -> None:
    lines.append(text)
    print(text, flush=True)


def host_allowed(base: str, live: str | None, confirm: str | None) -> bool:
    """A preview (loopback, private address or a name containing 'preview') needs nothing; any other host
    needs ``--live`` naming the same origin and the confirmation word."""
    host = (urlparse(base).hostname or "").lower()
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback or ip.is_private:
            return True
    except ValueError:
        if host == "localhost" or "preview" in host:
            return True
    return bool(live) and live.rstrip("/") == base.rstrip("/") and confirm == CONFIRM_WORD


class Smoke:
    def __init__(self, base: str, args):
        self.base = base.rstrip("/")
        self.args = args
        self.http = requests.Session()
        self.asked = 0
        self.errors = 0
        self.failed = 0
        self.accounts: list[dict] = []

    def get(self, path: str, **kw):
        return self.send("GET", path, **kw)

    def send(self, method: str, path: str, **kw):
        self.asked += 1
        try:
            resp = self.http.request(method, self.base + path, timeout=30, allow_redirects=True, **kw)
        except requests.RequestException:
            self.errors += 1
            return None
        if resp.status_code >= 500:
            self.errors += 1
        return resp

    def item(self, name: str, fn):
        started = time.time()
        try:
            ok, detail = fn()
        except Exception as exc:  # an item that blows up is a failed item, never a crashed run
            ok, detail = False, "%s: %s" % (type(exc).__name__, str(exc)[:160])
        took = time.time() - started
        if not ok:
            self.failed += 1
        emit("%s %s (%.1fs) %s" % ("PASS" if ok else "FAIL", name, took, detail))
        return ok

    @staticmethod
    def csrf(resp) -> str:
        if resp is None:
            return ""
        m = re.search(r'name="csrf-token" content="([^"]+)"', resp.text) or re.search(
            r'name="csrf_token"[^>]*value="([^"]+)"', resp.text)
        return m.group(1) if m else ""

    # --- items -------------------------------------------------------------------------------------------

    def health(self):
        deadline = time.time() + self.args.health_timeout
        last = "no answer"
        while time.time() < deadline:
            resp = self.get("/health")
            if resp is not None and resp.status_code == 200:
                return True, "/health answered 200"
            last = "no answer" if resp is None else "answered %s" % resp.status_code
            time.sleep(min(2, max(0.2, deadline - time.time())))
        triggers.append("health check did not answer 200 within %.0f seconds (%s)" % (self.args.health_timeout, last))
        return False, last

    def schema(self):
        resp = self.get("/api/health/database")
        if resp is None or resp.status_code != 200:
            triggers.append("schema check failed: the database health route did not answer 200")
            return False, "database health answered %s" % (resp.status_code if resp is not None else "nothing")
        return True, "database health answered 200"

    def page_ok(self, path: str):
        resp = self.get(path)
        if resp is None:
            return "%s: no answer" % path
        if resp.status_code != 200:
            return "%s: answered %s" % (path, resp.status_code)
        if "/account/login" in resp.url and path != "/account/login":
            return "%s: sent back to sign-in" % path
        text = re.sub(r"<[^>]+>", " ", resp.text)
        if len(text.split()) < 2:
            return "%s: empty page" % path
        if ERROR_TEXT.search(resp.text):
            return "%s: shows an error" % path
        return None

    def sweep(self, paths):
        bad = [m for m in (self.page_ok(p) for p in paths) if m]
        return (not bad, "; ".join(bad) if bad else "%d pages with content" % len(paths))

    def signup(self):
        stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())
        email = "smoke+%s%s@%s" % (stamp, secrets.token_hex(2), self.args.email_domain)
        password = "S" + secrets.token_urlsafe(14) + "9!"
        form = self.get("/account/register")
        if form is None or form.status_code != 200:
            return False, "the sign-up page did not load"
        resp = self.send("POST", "/account/register", data={
            "csrf_token": self.csrf(form), "first_name": "Smoke", "last_name": "Check", "email": email,
            "password": password, "password2": password})
        if resp is None or resp.status_code >= 400:
            return False, "the sign-up form answered %s" % (resp.status_code if resp is not None else "nothing")
        self.accounts.append({"email": email, "user_id": None})
        if "unconfirmed" in resp.url or "confirm" in resp.url.lower():
            return False, "the account needs an email confirmation this script cannot read: %s" % email
        if "/account/login" in resp.url or "/account/register" in resp.url:
            return False, "the sign-up did not sign the new person in"
        return True, "account %s created and signed in" % email

    def website_read(self):
        if not self.accounts:
            return False, "no signed-in account to read a website for"
        page = self.get("/account/welcome")
        token = self.csrf(page)
        resp = self.send("POST", "/account/welcome/website", json={"url": self.args.website, "consent": True},
                         headers={"X-CSRFToken": token})
        if resp is None or resp.status_code >= 400:
            triggers.append("queue stalled: the website could not be handed to the background job")
            return False, "the website was not accepted (%s)" % (resp.status_code if resp is not None else "no answer")
        started = time.time()
        deadline = started + self.args.site_timeout
        while time.time() < deadline:
            step = self.get("/account/welcome?step=3")
            if step is not None and 'data-testid="signup-facts-list"' in step.text and "data-fact=" in step.text:
                return True, "facts from %s shown after %.0fs" % (self.args.website, time.time() - started)
            time.sleep(min(3, max(0.2, deadline - time.time())))
        triggers.append("queue stalled: the website was not read within %.0f seconds, so the background job worker "
                        "is not running or is stuck" % self.args.site_timeout)
        return False, "no facts shown within %.0f seconds" % self.args.site_timeout

    def cleanup(self):
        if not self.accounts:
            return True, "nothing to delete"
        left = []
        for acct in self.accounts:
            reason = self.erase(acct)
            if reason:
                left.append("%s (%s)" % (acct["email"], reason))
        if left:
            return False, "could not delete: " + "; ".join(left)
        return True, "%d account(s) erased" % len(self.accounts)

    def erase(self, acct) -> str | None:
        """The data-subject pages: record an erasure request, review it, run it. Returns why it failed."""
        index = self.get("/compliance/data-subject-requests")
        if index is None or index.status_code != 200:
            return "the erasure pages did not load"
        m = re.search(r'<option value="(\d+)"[^>]*>\s*[^<]*%s' % re.escape(acct["email"]), index.text)
        if not m:
            return "account not listed in the erasure pages"
        token = self.csrf(index)
        made = self.send("POST", "/compliance/data-subject-requests", data={
            "csrf_token": token, "request_type": "erasure", "subject_user_id": m.group(1)})
        rid = re.search(r"/data-subject-requests/(\d+)", made.url if made is not None else "")
        if not rid:
            return "the erasure request was not recorded"
        for step in ("review", "run"):
            data = {"csrf_token": token}
            if step == "run":
                data["confirm"] = "erase"
            self.send("POST", "/compliance/data-subject-requests/%s/erasure/%s" % (rid.group(1), step), data=data)
        detail = self.get("/compliance/data-subject-requests/%s" % rid.group(1))
        text = detail.text if detail is not None else ""
        if "Erasure complete" in text or "already run" in text:
            return None
        said = re.search(r"(You cannot erase[^<.]*|That person[^<.]*|Review what[^<.]*)", text)
        return said.group(1) if said else "the erasure did not complete"

    def run(self) -> int:
        emit("live smoke against %s" % self.base)
        self.item("health", self.health)
        self.item("schema", self.schema)
        self.item("pages", lambda: self.sweep(PUBLIC_PAGES))
        signed = self.item("signup", self.signup)
        if signed:
            self.item("signed-in-pages", lambda: self.sweep(SIGNED_IN_PAGES))
            self.item("website-read", self.website_read)
        else:
            for name in ("signed-in-pages", "website-read"):
                self.failed += 1
                emit("FAIL %s (0.0s) skipped: sign-up did not complete" % name)
        self.item("cleanup", self.cleanup)
        rate = self.errors / self.asked if self.asked else 0.0
        ok = rate < ERROR_RATE_LIMIT
        if not ok:
            self.failed += 1
            triggers.append("error rate %.1f%% over %d requests is above 1%%" % (rate * 100, self.asked))
        emit("%s error-rate (0.0s) %d of %d requests answered 5xx or nothing" % ("PASS" if ok else "FAIL", self.errors, self.asked))
        for t in triggers:
            emit("ROLLBACK TRIGGER: " + t)
        emit("RESULT %s: %d item(s) failed" % ("FAIL" if self.failed else "PASS", self.failed))
        return 1 if self.failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base_url")
    ap.add_argument("--live", help="the live URL again, to confirm a host that is not a preview")
    ap.add_argument("--confirm", help="the confirmation word printed in the refusal message")
    ap.add_argument("--website", default="https://example.com", help="company website to sign up with")
    ap.add_argument("--email-domain", default=EMAIL_DOMAIN, help="a domain you control for smoke+<time>@ addresses")
    ap.add_argument("--health-timeout", type=float, default=300)
    ap.add_argument("--site-timeout", type=float, default=300)
    args = ap.parse_args(argv)
    if not host_allowed(args.base_url, args.live, args.confirm):
        emit("refused: %s is not a preview. Pass --live %s --confirm %s to run against it."
             % (args.base_url, args.base_url, CONFIRM_WORD))
        return 2
    lines.clear()
    triggers.clear()
    return Smoke(args.base_url, args).run()


if __name__ == "__main__":
    sys.exit(main())
