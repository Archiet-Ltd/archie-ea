"""The wave-1 timing runner covers every wave-1 route for each of the three personas.

The end-to-end test serves the app on a loopback port over a freshly committed
organisation, signs in as each persona (the administrator through the two-factor
step), and asserts the results file holds one row per route per persona, with a
timing on every page a persona can open and a stated reason on every page the run
had no sample id for.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import sys
import threading
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests" / "load"))
import w1_routes  # noqa: E402


def _load_runner():
    spec = importlib.util.spec_from_file_location("w1_timings", REPO / "scripts" / "ops" / "w1_timings.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


def test_runner_refuses_a_host_that_is_not_loopback():
    for url in ("https://app.example.com", "http://10.0.0.5:5000", "http://192.168.1.2"):
        with pytest.raises(SystemExit):
            runner.require_loopback(url)
    for url in ("http://127.0.0.1:5000", "http://localhost:8000", "http://[::1]:5000"):
        runner.require_loopback(url)


def test_route_with_no_sample_is_reported_not_dropped():
    path, reason = w1_routes.resolve_route("/batch-import/jobs/<job_id>", w1_routes.Samples({}))
    assert path is None and "job_id" in reason
    path, reason = w1_routes.resolve_route("/batch-import/jobs/<job_id>", w1_routes.Samples({"job_id": "7"}))
    assert (path, reason) == ("/batch-import/jobs/7", None)


def test_percentile_and_thresholds():
    assert runner.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert runner.percentile([5.0], 50) == 5.0
    assert w1_routes.limit_for("/dashboard/overview") == 1.5
    assert w1_routes.limit_for("/applications/") == 1.5
    assert w1_routes.limit_for("/admin/users") == 2.0
    rows = [
        {"route": "/dashboard/overview", "persona": "new_cto", "status": "200", "p95_ms": "1600", "max_ms": "1700"},
        {"route": "/admin/users", "persona": "new_cto", "status": "403", "p95_ms": "9000", "max_ms": "9000"},
        {"route": "/vision", "persona": "new_cto", "status": "200", "p95_ms": "300", "max_ms": "8500"},
    ]
    found = runner.breaches(rows)
    assert len(found) == 2 and "overview" in found[0] and "max" in found[1]


def test_wave_1_list_has_three_personas_and_unique_routes():
    assert [p.key for p in w1_routes.PERSONAS] == ["new_cto", "invited_viewer", "org_admin"]
    patterns = [pattern for _id, pattern in w1_routes.W1_ROUTES]
    assert len(patterns) == len(set(patterns))
    assert {"/dashboard/overview", "/applications/"} <= set(patterns)


def test_locust_scenario_defines_a_user_class_per_persona():
    # locust patches the standard library when imported, which deadlocks inside a
    # pytest process, so the scenario is loaded in a child interpreter.
    import json
    import os
    import subprocess

    code = (
        "import json, locustfile as l; "
        "print(json.dumps({'live': sorted(c.persona.key for c in (l.NewCto, l.InvitedViewer, l.OrgAdministrator) "
        "if not c.abstract), 'architect_abstract': l.Architect.abstract}))"
    )
    env = dict(os.environ, ARCHIE_W1_NEW_CTO_EMAIL="x@example.com")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO / "tests" / "load", env=env,
                         capture_output=True, text=True, timeout=120, check=True).stdout
    result = json.loads(out.strip().splitlines()[-1])
    assert result["live"] == ["invited_viewer", "new_cto", "org_admin"]
    assert result["architect_abstract"]  # no architect credentials in a wave-1 run


@pytest.fixture
def seeded_server(app, _schema):
    """A committed organisation with the three personas, served on a loopback port."""
    from werkzeug.serving import make_server

    from app import db
    from app.models.organization import Organization

    slug = "w1-timings-%s" % uuid.uuid4().hex[:8]
    password = "W1-timings-Test-1"
    with app.app_context():
        org = Organization(name="W1 timings %s" % slug, slug=slug)
        db.session.add(org)
        db.session.commit()
        personas = runner.seed_personas(password, org_slug=slug, app=app)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_port, password, personas
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    # The organisation and its three users stay, as the smoke harness's do: sign-in leaves audit
    # rows that reference the users, and every name here carries the per-run slug.


def test_runner_writes_a_row_for_every_w1_route_per_persona(seeded_server):
    base_url, password, personas = seeded_server
    creds = {k: (v["email"], password, v["totp_secret"]) for k, v in personas.items()}
    assert personas["org_admin"]["totp_secret"], "the administrator signs in through two-factor"
    rows = runner.run(base_url, creds, {"user_id": str(personas["org_admin"]["user_id"])}, 1, "test")

    out = io.StringIO()
    runner.write_csv(rows, out)
    parsed = list(csv.DictReader(io.StringIO(out.getvalue())))
    assert list(parsed[0].keys()) == runner.COLUMNS

    expected = {(pattern, p.key) for _id, pattern in w1_routes.W1_ROUTES for p in w1_routes.PERSONAS}
    assert {(r["route"], r["persona"]) for r in parsed} == expected
    assert len(parsed) == len(expected)

    for r in parsed:
        if r["status"] == "skipped":
            assert r["note"].startswith("no sample for"), r
            assert r["p95_ms"] == ""
        else:
            assert int(r["requests"]) == 1 and float(r["p95_ms"]) >= 0, r
    # the three personas signed in: the overview opens for each
    for p in w1_routes.PERSONAS:
        overview = next(r for r in parsed if r["route"] == "/dashboard/overview" and r["persona"] == p.key)
        assert overview["status"] == "200", overview
    # only the administrator reaches the admin console
    admin = {r["persona"]: r["status"] for r in parsed if r["route"] == "/admin/users"}
    assert admin["org_admin"] == "200"
    assert admin["invited_viewer"] == "403"


class _Reply:
    def __init__(self, status, retry_after=None):
        self.status_code = status
        self.url = "http://127.0.0.1/x"
        self.headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}


class _Limited:
    """A page that answers 429 for its first ``limited`` requests, then 200."""

    def __init__(self, limited):
        self.limited, self.calls = limited, 0

    def get(self, url, timeout=0):
        self.calls += 1
        return _Reply(429, 7) if self.calls <= self.limited else _Reply(200)


def test_a_rate_limited_answer_is_waited_out_and_asked_again_not_timed_as_the_page():
    w1_timings = _load_runner()

    waited = []
    # the first request is the uncounted warm-up, so three limited answers are the warm-up and two timed asks
    status, durations = w1_timings.time_route(_Limited(3), "http://127.0.0.1", "/pricing", 3, sleep=waited.append)
    assert status == "200" and len(durations) == 3
    assert waited == [7.0, 7.0], "it should wait the Retry-After the server gave, once per limited answer"


def test_a_page_that_stays_limited_keeps_the_429_status_so_the_row_can_say_so():
    w1_timings = _load_runner()

    waited = []
    status, durations = w1_timings.time_route(_Limited(10_000), "http://127.0.0.1", "/pricing", 1, sleep=waited.append)
    assert status == "429" and len(waited) == w1_timings.RATE_LIMIT_RETRIES
