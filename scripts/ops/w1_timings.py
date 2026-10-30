"""Time every wave-1 page, as each of three personas, against a LOCAL server.

    python scripts/ops/w1_timings.py --seed-personas --requests 20 --output w1-timings.csv

Without ``--base-url`` it starts the app itself on a free loopback port and stops it
afterwards. With ``--base-url`` it targets a server already running on this machine
(for a restored copy of production on the build machine). It refuses any host that is
not loopback: a timing run must never be pointed at production or any other host.

Reads only. Every request is a GET. ``--seed-personas`` is the one write: it creates
three users (new CTO, invited Viewer, organisation administrator) in the demonstration
organisation of the database the app points at, so it is for a sandbox or a restored
copy, never a live database.

Results file (CSV, one row per route per persona), columns in ``COLUMNS``:
route, persona, status, requests, p50_ms, p95_ms, max_ms, load_avg_1m, commit, note.
``status`` is the HTTP status after redirects, or ``skipped`` with the reason in
``note`` when a placeholder in the route had no sample id on this server. A route a
persona cannot open (403, 404, redirect to sign-in) is still timed and kept, with
its status, so the table shows who can open what. Timings and routes only; no names.
"""
from __future__ import annotations

import argparse
import csv
import ipaddress
import os
import re
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests" / "load"))

import w1_routes  # noqa: E402

COLUMNS = ["route", "persona", "status", "requests", "p50_ms", "p95_ms", "max_ms",
           "load_avg_1m", "commit", "note"]
DEMO_ORG_SLUG = "lantern-quay"


def require_loopback(base_url: str) -> None:
    host = urlparse(base_url).hostname or ""
    try:
        ok = ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = host == "localhost"
    if not ok:
        raise SystemExit("refusing %s: timings run against a server on this machine only" % base_url)


def commit_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    except Exception:  # noqa: BLE001 - a missing git is reported as unknown, not fatal
        return "unknown"


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    rank = max(0, min(len(ordered) - 1, int(round(pct / 100.0 * len(ordered) + 0.5)) - 1))
    return ordered[rank]


def seed_personas(password: str, org_slug: str = DEMO_ORG_SLUG, app=None) -> dict[str, dict]:
    """Create (or reset the password of) one user per persona; return their ids."""
    from app import create_app, db
    from app.jobs.tenant_safe_job import tenant_scope
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.services import mfa_service
    import pyotp

    app = app or create_app(os.getenv("FLASK_CONFIG") or "default")
    out: dict[str, dict] = {}
    with app.app_context():
        org = Organization.query.filter_by(slug=org_slug).first()
        if org is None:
            raise SystemExit("no organisation %r; run `flask seed-demo-company` first" % org_slug)
        org_id = org.id
        Role.insert_roles()
        with tenant_scope(org_id):
            for persona in w1_routes.PERSONAS:
                totp_secret = None
                email = "w1-timings.%s@example.com" % persona.key.replace("_", "-")
                user = User.query.filter_by(email=email, organization_id=org_id).first()
                if user is None:
                    user = User(email=email, first_name="Timing", last_name=persona.key[:12],
                                organization_id=org_id, confirmed=True)
                    db.session.add(user)
                user.enterprise_role = persona.enterprise_role
                user.role = Role.query.filter_by(name=persona.role_name).one()
                user.is_org_admin = persona.org_admin
                user.password = password
                db.session.commit()
                if mfa_service.required_for(user):
                    # An administrator signs in through the two-factor step, so enrol one
                    # authenticator secret and keep it in memory for the sign-in.
                    if not user.mfa_enabled:
                        secret = mfa_service.generate_secret()
                        mfa_service.enroll(user, secret, pyotp.TOTP(secret).now())
                        db.session.commit()
                    totp_secret = user.mfa_secret
                out[persona.key] = {"email": email, "user_id": user.id, "totp_secret": totp_secret}
    return out


class Server:
    """The app on a free loopback port, started and stopped by the runner."""

    def __init__(self) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = "http://127.0.0.1:%d" % self.port
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        import requests
        env = dict(os.environ)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "flask", "--app", "manage", "run", "--host", "127.0.0.1",
             "--port", str(self.port), "--no-reload"],
            cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(120):
            if self.proc.poll() is not None:
                raise SystemExit("the local server exited while starting")
            try:
                requests.get(self.url + "/", timeout=2)
                return
            except requests.RequestException:
                time.sleep(1)
        raise SystemExit("the local server did not answer within 120 s")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def sign_in(base_url: str, email: str, password: str, totp_secret: str | None = None):
    import requests
    s = requests.Session()
    page = s.get(base_url + "/account/login", timeout=30)
    payload = {"email": email, "password": password}
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text or "")
    if m:
        payload["csrf_token"] = m.group(1)
    for _ in range(5):
        r = s.post(base_url + "/account/login", data=payload, timeout=30)
        if r.status_code == 429:
            time.sleep(min(float(r.headers.get("Retry-After", 5) or 5), 30))
            continue
        break
    if "/account/mfa-challenge" in r.url:
        if not totp_secret:
            raise SystemExit("a persona needs a two-factor secret (ARCHIE_W1_<PERSONA>_TOTP_SECRET)")
        import pyotp
        page = s.get(base_url + "/account/mfa-challenge", timeout=30)
        form = {"code": pyotp.TOTP(totp_secret).now()}
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text or "")
        if m:
            form["csrf_token"] = m.group(1)
        r = s.post(base_url + "/account/mfa-challenge", data=form, timeout=30)
    if "/account/login" in r.url or "/account/mfa-challenge" in r.url:
        raise SystemExit("sign-in rejected for a persona")
    return s


def discover_samples(session, base_url: str, extra: dict[str, str]) -> w1_routes.Samples:
    """Find one real id or slug for each placeholder from pages the server serves."""
    found = dict(extra)

    def first(paths: list[str], pattern: str, key: str) -> None:
        for path in paths:
            if key in found:
                return
            try:
                body = session.get(base_url + path, timeout=30).text
            except Exception:  # noqa: BLE001
                continue
            m = re.search(pattern, body or "")
            if m:
                found[key] = m.group(1)

    first(["/applications/"], r'/applications/(\d+)(?:/|")', "application_id")
    first(["/vs", "/"], r'href="/vs/([a-z0-9-]+)"', "vs_slug")
    first(["/connectors", "/"], r'href="/connectors/([a-z0-9-]+)"', "connector_slug")
    first(["/roles", "/"], r'href="/roles/([a-z0-9-]+)"', "role_slug")
    first(["/use-cases", "/"], r'href="/use-cases/([a-z0-9-]+)"', "use_case_slug")
    first(["/use-cases", "/modules/", "/"], r'href="/modules/([a-z0-9-]+)"', "module_slug")
    # a public content page the repository ships, when no index page links one
    if "role_slug" not in found:
        pages = sorted((REPO / "content" / "pages" / "roles").glob("*.md"))
        if pages:
            found["role_slug"] = pages[0].stem
    return w1_routes.Samples(found)


def time_route(session, base_url: str, path: str, requests_per_route: int) -> tuple[str, list[float]]:
    session.get(base_url + path, timeout=60)  # warm-up, not counted
    durations: list[float] = []
    status = "error"
    for _ in range(requests_per_route):
        t0 = time.perf_counter()
        try:
            r = session.get(base_url + path, timeout=60)
            status = str(r.status_code)
            if "/account/login" in r.url and not path.startswith("/account/"):
                status += " (sign-in)"
        except Exception as exc:  # noqa: BLE001
            status = type(exc).__name__
        durations.append(time.perf_counter() - t0)
    return status, durations


def run(base_url: str, creds: dict[str, tuple[str, str, str | None]], extra_samples: dict[str, str],
        requests_per_route: int, sha: str) -> list[dict]:
    rows: list[dict] = []
    for persona in w1_routes.PERSONAS:
        email, password, totp_secret = creds[persona.key]
        session = sign_in(base_url, email, password, totp_secret)
        samples = discover_samples(session, base_url, dict(extra_samples))
        for _wave_id, pattern in w1_routes.W1_ROUTES:
            path, reason = w1_routes.resolve_route(pattern, samples)
            row = {"route": pattern, "persona": persona.key, "requests": 0, "p50_ms": "",
                   "p95_ms": "", "max_ms": "", "commit": sha, "note": ""}
            row["load_avg_1m"] = "%.2f" % os.getloadavg()[0]
            if path is None:
                row.update(status="skipped", note=reason)
            else:
                status, durations = time_route(session, base_url, path, requests_per_route)
                ms = [d * 1000 for d in durations]
                row.update(status=status, requests=len(ms),
                           p50_ms="%.0f" % statistics.median(ms),
                           p95_ms="%.0f" % percentile(ms, 95), max_ms="%.0f" % max(ms))
            rows.append(row)
    return rows


def write_csv(rows: list[dict], out) -> None:
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)


def breaches(rows: list[dict]) -> list[str]:
    """Rows that open (HTTP 200) but miss the brief's thresholds."""
    out = []
    for r in rows:
        if r["status"] != "200":
            continue
        limit = w1_routes.limit_for(r["route"])
        if float(r["p95_ms"]) / 1000 > limit:
            out.append("%s as %s: p95 %s ms over %.1f s" % (r["route"], r["persona"], r["p95_ms"], limit))
        if float(r["max_ms"]) / 1000 > w1_routes.MAX_LIMIT_S:
            out.append("%s as %s: max %s ms over %.0f s" % (r["route"], r["persona"], r["max_ms"], w1_routes.MAX_LIMIT_S))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", help="a server already running on this machine (loopback only)")
    ap.add_argument("--requests", type=int, default=20, help="timed GETs per route per persona")
    ap.add_argument("--output", default="-", help="results CSV path, or - for stdout")
    ap.add_argument("--seed-personas", action="store_true",
                    help="create the three persona users in the demonstration organisation")
    ap.add_argument("--password", default=os.environ.get("ARCHIE_W1_PASSWORD", "W1-timings-Sandbox-1"),
                    help="persona password (env ARCHIE_W1_PASSWORD)")
    ap.add_argument("--sample", action="append", default=[], metavar="KEY=VALUE",
                    help="an id or slug for a placeholder, e.g. batch_id=12; repeatable")
    args = ap.parse_args(argv)

    extra = dict(s.split("=", 1) for s in args.sample)
    creds: dict[str, tuple[str, str, str | None]] = {}
    if args.seed_personas:
        for key, info in seed_personas(args.password).items():
            creds[key] = (info["email"], args.password, info["totp_secret"])
            if key == "org_admin":
                extra.setdefault("user_id", str(info["user_id"]))
    else:
        for persona in w1_routes.PERSONAS:
            email = os.environ.get("ARCHIE_W1_%s_EMAIL" % persona.key.upper())
            if not email:
                raise SystemExit("set ARCHIE_W1_%s_EMAIL (or use --seed-personas)" % persona.key.upper())
            creds[persona.key] = (email, args.password,
                                   os.environ.get("ARCHIE_W1_%s_TOTP_SECRET" % persona.key.upper()))

    server = None
    base_url = args.base_url
    if base_url:
        require_loopback(base_url)
    else:
        server = Server()
        server.start()
        base_url = server.url
    try:
        rows = run(base_url.rstrip("/"), creds, extra, args.requests, commit_sha())
    finally:
        if server:
            server.stop()
    if args.output == "-":
        write_csv(rows, sys.stdout)
    else:
        with open(args.output, "w", newline="") as fh:
            write_csv(rows, fh)
    for line in breaches(rows):
        print("OVER THRESHOLD:", line, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
