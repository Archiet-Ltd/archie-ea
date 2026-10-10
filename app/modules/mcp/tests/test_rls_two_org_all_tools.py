"""All ten MCP tools, two organisations, under real PostgreSQL row-level security.

Every other isolation test in this package (test_isolation.py) calls through the
``app``/``db_session`` fixtures, which share one outer transaction that the
fixture's own teardown always rolls back (see its docstring in tests/conftest.py)
-- real coverage of the application-layer organisation filter, but it connects
as the table owner throughout, a role RLS never restricts by Postgres design,
and nothing created through it is ever actually committed for a second,
independent connection to see.

This test instead runs two genuinely separate processes against the same
database, each with its own real commits -- the same shape as the manual
two-organisation probe already posted on pr:455 (board note 1290, evidence
scripts on sdlc-orchestrator origin/qa/pr455-review-2026-10-10):

1. ``_SEED`` (run as the table owner): two organisations, an admin user each,
   one ArchiMateElement/BusinessModelCanvas/BusinessCase per organisation with
   a SECRET-tagged name, and a PKCE authorization code for organisation A's
   user scoped to ``/mcp``.
2. ``_PROBE`` (run as a role that is NOSUPERUSER, NOBYPASSRLS and not the
   table owner): redeems the code at ``/oauth/token``, then calls all ten
   listed MCP tools with that token against both organisations' data.

Skips cleanly, rather than failing, when the ambient connection cannot create
or configure a lesser-privileged role (a managed/locked-down Postgres the
suite happens to be pointed at, for example) -- an environment limit, not a
defect in the connector.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

# app/modules/mcp/tests/test_rls_two_org_all_tools.py -> repo root is four
# directories up (tests, mcp, modules, app).
REPO_ROOT = Path(__file__).resolve().parents[4]

ALL_TEN_TOOLS = [
    "ask_impact", "ask_strategy", "ask_portfolio", "ask_programme",
    "ask_risk", "ask_accountability", "get_element", "search_elements",
    "list_canvases", "get_canvas",
]

ROLE = "pr455_rls_probe_runtime"

_SEED = r'''
import base64, hashlib, json, os, secrets, sys, uuid
from app import create_app
from app.extensions import db

app = create_app("testing")
out = {}
with app.app_context():
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.models import ArchiMateElement
    from app.models.business_model import BusinessModelCanvas
    from app.models.business_case import BusinessCase
    from app.modules.oauth_provider.models import OAuthClient, OAuthAuthorizationCode
    Role.insert_roles()
    admin = Role.query.filter_by(name="Administrator").first()
    sfx = uuid.uuid4().hex[:8]
    for tag in ("ALPHA", "BRAVO"):
        org = Organization(name=f"RLS probe {tag} {sfx}", slug=f"rls-probe-{tag.lower()}-{sfx}")
        db.session.add(org); db.session.flush()
        u = User(email=f"{tag.lower()}-{sfx}@example.com", first_name=tag, last_name="User",
                 organization_id=org.id, role=admin, is_org_admin=True, confirmed=True)
        u.password = "pw-original"
        db.session.add(u); db.session.flush()
        el = ArchiMateElement(name=f"{tag}-SECRET-ELEMENT-{sfx}", description=f"{tag}-SECRET-DESC",
                              type="ApplicationComponent", layer="application", organization_id=org.id)
        db.session.add(el); db.session.flush()
        bmc = BusinessModelCanvas(name=f"{tag}-SECRET-CANVAS-{sfx}", description="x", organization_id=org.id)
        db.session.add(bmc); db.session.flush()
        bc = BusinessCase(title=f"{tag}-SECRET-CASE-{sfx}", organization_id=org.id)
        db.session.add(bc); db.session.flush()
        out[tag] = dict(org=org.id, user=u.id, email=u.email, element=el.id, bmc=bmc.id, bc=bc.id)
    db.session.commit()
    c = OAuthClient.register(client_name="pytest RLS probe", redirect_uris="http://localhost/cb")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    raw, _ = OAuthAuthorizationCode.issue(client_id=c.client_id, user_id=out["ALPHA"]["user"],
        redirect_uri="http://localhost/cb", scope="mcp:read",
        resource=app.config["PUBLIC_BASE_URL"].rstrip("/") + "/mcp", code_challenge=challenge,
        organization_id=out["ALPHA"]["org"], lifetime_seconds=600)
    out.update(client_id=c.client_id, code=raw, verifier=verifier, sfx=sfx)
json.dump(out, open(sys.argv[1], "w"))
'''

_PROBE = r'''
import json, sys
from app import create_app
state = json.load(open(sys.argv[1]))
app = create_app("testing")
with app.app_context():
    from app.extensions import db
    row = db.session.execute(db.text(
        "select current_user, (select rolsuper from pg_roles where rolname=current_user), "
        "(select rolbypassrls from pg_roles where rolname=current_user)"
    )).fetchone()
    print("ROLE_CHECK", json.dumps(list(row)))
    db.session.remove()
c = app.test_client()
res = app.config["PUBLIC_BASE_URL"].rstrip("/") + "/mcp"
A, B = state["ALPHA"], state["BRAVO"]
r = c.post("/oauth/token", data=dict(grant_type="authorization_code", code=state["code"],
    redirect_uri="http://localhost/cb", client_id=state["client_id"], code_verifier=state["verifier"], resource=res))
if r.status_code != 200:
    print("TOKEN_FAILED", r.status_code, r.get_data(as_text=True)[:500]); sys.exit(1)
token = r.get_json()["access_token"]
H = {"Authorization": "Bearer " + token}

def call(name, args):
    rr = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, headers=H)
    return rr.status_code, rr.get_data(as_text=True)

lr = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=H)
if lr.status_code != 200 or "result" not in (lr.get_json() or {}):
    print("TOOLS_LIST_FAILED", lr.status_code, lr.get_data(as_text=True)[:500]); sys.exit(1)
tools = sorted(t["name"] for t in lr.get_json()["result"]["tools"])
print("TOOLS_LIST", json.dumps(tools))

calls = []
for eid_tag, eid in (("A", A["element"]), ("B", B["element"])):
    for lens in ("impact", "strategy", "portfolio", "programme", "risk", "accountability"):
        calls.append((f"ask_{lens}", {"element_id": eid}, eid_tag))
    calls.append(("get_element", {"element_id": eid}, eid_tag))
calls += [("search_elements", {"query": "SECRET", "limit": 200}, "*"),
          ("list_canvases", {}, "*"),
          ("get_canvas", {"canvas_type": "business_model", "canvas_id": A["bmc"]}, "A"),
          ("get_canvas", {"canvas_type": "business_model", "canvas_id": B["bmc"]}, "B"),
          ("get_canvas", {"canvas_type": "business_case", "canvas_id": A["bc"]}, "A"),
          ("get_canvas", {"canvas_type": "business_case", "canvas_id": B["bc"]}, "B")]
leaks = 0; alpha_hits = 0; called = set()
bad_status = []
for name, args, tag in calls:
    st, body = call(name, args); called.add(name)
    if st != 200:
        bad_status.append((name, tag, st))
    bravo = "BRAVO-SECRET" in body or B["email"] in body
    alpha = "ALPHA-SECRET" in body
    alpha_hits += alpha; leaks += bravo
print("CALLED", json.dumps(sorted(called)))
print("BAD_STATUS", json.dumps(bad_status))
print(f"SUMMARY calls={len(calls)} bravo_leaks={leaks} alpha_hits={alpha_hits}")
'''


@pytest.fixture
def probe_role(app):
    """Create (idempotently) a NOSUPERUSER/NOBYPASSRLS/non-owner role on the
    live database, via a genuinely separate, autocommitting connection --
    not ``db_session``, whose work this module's docstring explains never
    actually persists. Skips the test cleanly if the ambient connection
    cannot create or configure it.
    """
    password = uuid.uuid4().hex
    base_uri = app.config["SQLALCHEMY_DATABASE_URI"]
    owner_engine = create_engine(base_uri, poolclass=NullPool)
    try:
        with owner_engine.begin() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ROLE}
            ).scalar()
            if not exists:
                connection.execute(text(f"CREATE ROLE {ROLE}"))  # nosec B608 - module constant
            connection.execute(
                text(
                    f"ALTER ROLE {ROLE} WITH LOGIN PASSWORD '{password}' "  # nosec B608 - module constant, local throwaway password; ALTER ROLE's PASSWORD clause is DDL, takes no bind parameter
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
                )
            )
            connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {ROLE}"))  # nosec B608
            connection.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROLE}")  # nosec B608
            )
            connection.execute(
                text(f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {ROLE}")  # nosec B608
            )
            is_super, is_bypass = connection.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"), {"r": ROLE}
            ).one()
            if (is_super, is_bypass) != (False, False):
                pytest.skip(f"{ROLE} ended up super={is_super} bypass={is_bypass}; cannot verify RLS with it")
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"cannot create/configure a non-superuser probe role here: {exc}")
    finally:
        owner_engine.dispose()

    scheme_end = base_uri.index("://") + 3
    host_part = base_uri[scheme_end:].split("@", 1)[-1]
    return f"postgresql://{ROLE}:{password}@{host_part}"


def _cleanup_seeded_rows(base_uri, state):
    """Delete the ``usage_events`` and OAuth rows ``_SEED``/``_PROBE``
    committed for real, via a plain autocommitting connection (not
    ``db_session`` -- see the module docstring).

    Both subprocesses commit directly, so without this ``usage_events`` rows
    outlive the test indefinitely in a shared database, and an unrelated
    test's own query can pick one up if it isn't scoped to its own
    organisation (confirmed: app/modules/mcp/tests/test_tools.py's
    ``test_mcp_tool_call_metering_has_correct_org_id`` failed against a
    leftover event from a previous run of *this* test -- now fixed on both
    sides, this cleanup and that test's query).

    Deliberately leaves the seeded organisations/users/elements/canvases in
    place: deleting them would mean unwinding an enormous, unrelated web of
    foreign keys onto ``users`` across the codebase (hundreds of
    ``created_by_id``-style columns), and nothing in the suite does an
    unscoped query over organisations the way the metering test did over
    usage events -- each CI run gets a fresh database regardless.
    """
    org_ids = [state["ALPHA"]["org"], state["BRAVO"]["org"]]
    client_id = state["client_id"]
    engine = create_engine(base_uri, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM usage_events WHERE organization_id IN (:a, :b)"),
                {"a": org_ids[0], "b": org_ids[1]},
            )
            connection.execute(text("DELETE FROM oauth_tokens WHERE client_id = :c"), {"c": client_id})  # nosec B608 - bound parameter
            connection.execute(text("DELETE FROM oauth_authorization_codes WHERE client_id = :c"), {"c": client_id})  # nosec B608
            connection.execute(text("DELETE FROM oauth_clients WHERE client_id = :c"), {"c": client_id})  # nosec B608
    finally:
        engine.dispose()


def test_all_ten_tools_see_zero_rows_from_the_other_organisation_under_rls(
    app, probe_role, tmp_path
):
    """Seed as the owner (one real process), probe as the restricted role
    (a second real process): zero rows from organisation B, across every one
    of the ten listed tools, reaches organisation A's token.
    """
    state_path = tmp_path / "state.json"
    base_env = dict(__import__("os").environ)
    base_env.update(
        FLASK_CONFIG="testing",
        SECRET_KEY=base_env.get("SECRET_KEY", "ci-only-not-secret"),
        PUBLIC_BASE_URL=app.config["PUBLIC_BASE_URL"],
        MCP_ENABLED="1",
    )

    seed_env = dict(base_env)
    seed_env["TEST_DATABASE_URL"] = app.config["SQLALCHEMY_DATABASE_URI"]
    seed_env["DATABASE_URL"] = app.config["SQLALCHEMY_DATABASE_URI"]
    seed_proc = subprocess.run(
        [sys.executable, "-c", _SEED, str(state_path)],
        cwd=str(REPO_ROOT), env=seed_env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert seed_proc.returncode == 0, f"seed failed:\nSTDOUT:{seed_proc.stdout}\nSTDERR:{seed_proc.stderr}"
    state = json.loads(state_path.read_text())

    try:
        probe_env = dict(base_env)
        probe_env["TEST_DATABASE_URL"] = probe_role
        probe_env["DATABASE_URL"] = probe_role
        probe_proc = subprocess.run(
            [sys.executable, "-c", _PROBE, str(state_path)],
            cwd=str(REPO_ROOT), env=probe_env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        out = probe_proc.stdout
        assert probe_proc.returncode == 0, f"probe failed:\nSTDOUT:{out}\nSTDERR:{probe_proc.stderr}"

        role_line = next(line for line in out.splitlines() if line.startswith("ROLE_CHECK"))
        role_name, is_super, is_bypass = json.loads(role_line.split(" ", 1)[1])
        assert (role_name, is_super, is_bypass) == (ROLE, False, False)

        tools_line = next(line for line in out.splitlines() if line.startswith("TOOLS_LIST"))
        listed_tools = json.loads(tools_line.split(" ", 1)[1])
        assert listed_tools == sorted(ALL_TEN_TOOLS), f"tool list drifted: {listed_tools}"

        called_line = next(line for line in out.splitlines() if line.startswith("CALLED"))
        called_tools = set(json.loads(called_line.split(" ", 1)[1]))
        assert called_tools == set(ALL_TEN_TOOLS), f"not all ten tools were exercised: missing {set(ALL_TEN_TOOLS) - called_tools}"

        bad_status_line = next(line for line in out.splitlines() if line.startswith("BAD_STATUS"))
        bad_status = json.loads(bad_status_line.split(" ", 1)[1])
        assert bad_status == [], f"non-200 responses: {bad_status}"

        summary_line = next(line for line in out.splitlines() if line.startswith("SUMMARY"))
        parts = dict(p.split("=") for p in summary_line.split()[1:])
        assert int(parts["bravo_leaks"]) == 0, f"organisation B's data leaked through: {summary_line}\nFull output:\n{out}"
        assert int(parts["alpha_hits"]) > 0, (
            "organisation A's own data never appeared -- the tools may be failing closed "
            f"for everyone rather than isolating correctly: {summary_line}"
        )
    finally:
        _cleanup_seeded_rows(app.config["SQLALCHEMY_DATABASE_URI"], state)
