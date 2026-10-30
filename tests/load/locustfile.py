"""How many architects can use Archie at once? Nobody has ever measured it.

`locust>=2.20.0` has been a pinned dependency of this project with no
locustfile to run — an unused load-testing dependency being the clearest
possible tell that capacity was never tested. Concurrency CORRECTNESS is well
covered (25 test files drive real threads at PostgreSQL; evidence-head
compare-and-swap, command leases, ARB decisions), but that answers "do races
corrupt data", not "does it stay up".

The two are different failure modes and today produced one of each: a TOCTOU
race that duplicated an application under five concurrent creates (fixed with
an advisory lock), and — still unmeasured — whatever happens when twenty
architects open the capability map on a **2 vCPU** production box. That box has
previously been starved into a 12-minute 503 simply by running parallel
containers on it, so the headroom is not theoretical.

What this measures, deliberately in this order:

1. Can N users sign in and read their own screens without the box falling over?
2. Where does p95 cross the threshold at which a page feels broken?
3. Does the connection pool exhaust before the CPU does? (gunicorn.conf.py's
   worker and pool sizing has never been validated against real concurrency.)

READ-ONLY BY DEFAULT. Writes are opt-in via ARCHIE_LOAD_WRITES=1, because this
is designed to be pointed at a real deployment and a load test that quietly
creates a thousand applications in someone's system of record is a worse
outage than the one it was looking for.

    pip install locust
    # against a local instance
    locust -f tests/load/locustfile.py --host http://127.0.0.1:5000
    # headless, 20 users, ramp 2/s, 5 minutes
    locust -f tests/load/locustfile.py --host http://127.0.0.1:5000 \
        --headless -u 20 -r 2 -t 5m --csv=load-results

Credentials come from the environment so no password lands in the repository:

    ARCHIE_LOAD_EMAILS="a@x.com,b@x.com"   # comma-separated; users are cycled
    ARCHIE_LOAD_PASSWORD="..."
    ARCHIE_LOAD_WRITES=1                    # opt in to the write journey

Never run this against production without the owner's explicit agreement, and
never with writes enabled against production at all.
"""
from __future__ import annotations

import itertools
import os
import random
import re

import gevent
from locust import HttpUser, between, events, task
from locust.exception import StopUser

import w1_routes

EMAILS = [e.strip() for e in os.environ.get("ARCHIE_LOAD_EMAILS", "").split(",") if e.strip()]
PASSWORD = os.environ.get("ARCHIE_LOAD_PASSWORD", "")
WRITES_ENABLED = os.environ.get("ARCHIE_LOAD_WRITES") == "1"

_email_cycle = itertools.cycle(EMAILS) if EMAILS else None

# Wave-1 scenario: one user class per persona, each reading every wave-1 page.
# Credentials per persona: ARCHIE_W1_<PERSONA>_EMAIL (+ _TOTP_SECRET for an
# administrator) and the shared ARCHIE_W1_PASSWORD. The classes are inert unless
# ARCHIE_W1_NEW_CTO_EMAIL is set, so the original Architect run is unchanged.
# Ids for pages that need one come from ARCHIE_W1_SAMPLES="key=value,key=value".
W1_ENABLED = bool(os.environ.get("ARCHIE_W1_NEW_CTO_EMAIL"))
W1_PASSWORD = os.environ.get("ARCHIE_W1_PASSWORD", "")
W1_SAMPLES = dict(
    pair.split("=", 1) for pair in os.environ.get("ARCHIE_W1_SAMPLES", "").split(",") if "=" in pair
)

# Weighted to match how an architect actually spends a session: mostly reading
# a handful of screens, occasionally searching, rarely writing. A flat
# distribution across every route would measure a crawler, not a user, and
# would make the p95 meaningless.
READ_PAGES = [
    ("/dashboard/overview", 10),
    ("/capability-map/", 8),
    ("/applications/", 8),
    ("/arb/", 5),
    ("/value-streams/", 3),
    ("/architecture/", 3),
    ("/risks/", 2),
]


@events.test_start.add_listener
def _warn_about_configuration(environment, **_kwargs):
    if not W1_ENABLED and (not EMAILS or not PASSWORD):
        raise SystemExit(
            "Set ARCHIE_LOAD_EMAILS and ARCHIE_LOAD_PASSWORD. This test signs in "
            "as real users; it deliberately has no default credentials."
        )
    host = (environment.host or "").lower()
    if WRITES_ENABLED and not ("localhost" in host or "127.0.0.1" in host):
        raise SystemExit(
            "Refusing to run the write journey against %s. Writes are for a "
            "local or disposable instance only — a load test that creates a "
            "thousand rows in a system of record is worse than the outage it "
            "was looking for." % environment.host
        )


class Architect(HttpUser):
    """One signed-in architect, reading their own screens."""

    # Inert in a wave-1 run (no architect credentials), so it never signs in blind.
    abstract = W1_ENABLED and not (EMAILS and PASSWORD)

    # A real user reads a page for a few seconds before clicking. Without this
    # the test measures how fast the box can refuse requests, not how many
    # people it can serve.
    wait_time = between(2, 6)

    def on_start(self):
        """Sign in once. A failure here is fatal for this user, not silent."""
        self.client.verify = False
        page = self.client.get("/account/login", name="/account/login")
        token = None
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text or "")
        if match:
            token = match.group(1)

        email = next(_email_cycle)
        payload = {"email": email, "password": PASSWORD}
        if token:
            payload["csrf_token"] = token

        with self.client.post("/account/login", data=payload,
                              name="POST /account/login",
                              allow_redirects=True, catch_response=True) as response:
            # 429 is the product working, not a defect. Archie rate-limits
            # writes at 30/minute and a login POST is a write, so a fast ramp
            # trips it -- 20 sign-ins succeeded and 2 were throttled at 40
            # users ramping 4/s. Counting that as a failure made the load test
            # report a broken product AND (worse) abort the whole run on the
            # first one, which is how an instrument hides the thing it was
            # built to measure.
            if response.status_code == 429:
                # Back off and retry, the way a real client does.
                #
                # The first version called self.stop() here. Locust immediately
                # respawns a replacement user to hold the target count, which
                # signs in, gets throttled, and stops again -- a retry storm
                # that the harness itself creates. It reported 176 throttled
                # logins and read as "the product locks an office out", when
                # most of those requests existed only because of the stop.
                # An instrument that amplifies the thing it is measuring is
                # worse than no instrument.
                response.success()
                wait = float(response.headers.get("Retry-After", 5) or 5)
                gevent.sleep(min(wait, 30) + random.uniform(0, 2))
                return self.on_start()
            if "/account/login" in response.url:
                # A genuine credential rejection. Stop this user only, and let
                # the count drop rather than respawning into the same wall.
                response.failure("sign-in rejected for %s" % email)
                raise StopUser()
            response.success()

    @task(sum(weight for _, weight in READ_PAGES))
    def read_a_page(self):
        path = random.choices(
            [p for p, _ in READ_PAGES],
            weights=[w for _, w in READ_PAGES],
        )[0]
        # name= groups the statistics by ROUTE rather than by URL, so the p95
        # is per screen and actionable.
        with self.client.get(path, name=path, catch_response=True) as response:
            if response.status_code >= 500:
                response.failure("HTTP %d" % response.status_code)
            elif "could not load its data" in (response.text or ""):
                # A 200 that tells the user it is broken is a failure here too;
                # that exact shape is what shipped to production this week.
                response.failure("error banner served with HTTP 200")
            else:
                response.success()

    @task(3)
    def search(self):
        term = random.choice(["order", "customer", "cloud", "payroll", "data"])
        self.client.get("/architecture/search", params={"q": term},
                        name="/architecture/search?q=")

    @task(2)
    def capability_api(self):
        self.client.get("/capability-map/api/roadmap/gaps",
                        name="/capability-map/api/roadmap/gaps")

    @task(1)
    def write_journey(self):
        """Opt-in only. Creates one application, the commonest real write."""
        if not WRITES_ENABLED:
            return
        suffix = random.randint(100000, 999999)
        self.client.post(
            "/applications/create",
            data={"name": "Load test app %d" % suffix,
                  "description": "Created by tests/load/locustfile.py"},
            name="POST /applications/create",
        )


def _w1_sign_in(user: HttpUser, persona: "w1_routes.Persona") -> None:
    """Sign in as one wave-1 persona, through the two-factor step when asked."""
    email = os.environ.get("ARCHIE_W1_%s_EMAIL" % persona.key.upper())
    if not email:
        raise StopUser()
    user.client.verify = False
    page = user.client.get("/account/login", name="/account/login")
    payload = {"email": email, "password": W1_PASSWORD}
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text or "")
    if match:
        payload["csrf_token"] = match.group(1)
    with user.client.post("/account/login", data=payload, name="POST /account/login",
                          catch_response=True) as response:
        if response.status_code == 429:
            response.success()
            gevent.sleep(min(float(response.headers.get("Retry-After", 5) or 5), 30)
                         + random.uniform(0, 2))
            return _w1_sign_in(user, persona)
        if "/account/mfa-challenge" in response.url:
            secret = os.environ.get("ARCHIE_W1_%s_TOTP_SECRET" % persona.key.upper())
            if not secret:
                response.failure("two-factor secret missing for %s" % persona.key)
                raise StopUser()
            import pyotp
            form = {"code": pyotp.TOTP(secret).now()}
            challenge = user.client.get("/account/mfa-challenge", name="/account/mfa-challenge")
            token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', challenge.text or "")
            if token:
                form["csrf_token"] = token.group(1)
            user.client.post("/account/mfa-challenge", data=form, name="POST /account/mfa-challenge")
        elif "/account/login" in response.url:
            response.failure("sign-in rejected for %s" % persona.key)
            raise StopUser()
        response.success()


def _w1_read(user: HttpUser) -> None:
    pattern = random.choice(user.w1_patterns)
    path, _reason = w1_routes.resolve_route(pattern, w1_routes.Samples(W1_SAMPLES))
    if path is None:
        return
    # grouped by the route pattern, so the statistics are per page
    with user.client.get(path, name=pattern, catch_response=True) as response:
        # 403 and 404 are who-can-open-what answers, not failures of this scenario
        if response.status_code >= 500:
            response.failure("HTTP %d" % response.status_code)
        else:
            response.success()


class _W1User(HttpUser):
    abstract = True
    wait_time = between(1, 3)
    persona: "w1_routes.Persona"

    def on_start(self):
        self.w1_patterns = [pattern for _id, pattern in w1_routes.W1_ROUTES]
        _w1_sign_in(self, self.persona)

    @task
    def read_wave_1_page(self):
        _w1_read(self)


class NewCto(_W1User):
    abstract = not W1_ENABLED
    persona = w1_routes.PERSONAS[0]


class InvitedViewer(_W1User):
    abstract = not W1_ENABLED
    persona = w1_routes.PERSONAS[1]


class OrgAdministrator(_W1User):
    abstract = not W1_ENABLED
    persona = w1_routes.PERSONAS[2]
