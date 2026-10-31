"""A capability created on a database built from migrations reaches the pickers.

The goal: on a new install (schema built from empty by the migrations alone) a
business architect adds a capability and then finds it in the capability picker.
Without the provenance index the write-time projection skipped the new
capability without an error and the picker stayed empty while the capability
list showed it.

This boots its own server against a throwaway database, not the shared one,
because the shared database was built by init-db and already has the index.
"""

import json
import os
import subprocess
import sys
import time
import uuid

import pytest
import requests
from playwright.sync_api import expect
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.smoke.conftest import PASSWORD, _free_port

PAGE_TIMEOUT = 60000
LIST_URL = "/enterprise/capability-map/capabilities"
PICKER_URL = "/api/v1/capabilities/"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SEED = r"""
import sys
from manage import app
from app import db
from app.models.organization import Organization
from app.models.user import Role, User
from app.services.billing_plans import set_contract_plan

email, password, suffix = sys.argv[1:4]
with app.app_context():
    Role.insert_roles()
    org = Organization(name="Fresh Org " + suffix, slug="fresh-" + suffix)
    db.session.add(org)
    db.session.flush()
    set_contract_plan(org, "enterprise", None)
    db.session.commit()
    user = User(email=email, first_name="Fresh", last_name="Architect",
                organization_id=org.id, enterprise_role="business_architect", confirmed=True)
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = password
    db.session.add(user)
    db.session.commit()
"""


def _run(env, *args):
    proc = subprocess.run(
        args, cwd=REPO_ROOT, env=env, capture_output=True, timeout=1800,
        encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, f"{args[:4]} failed:\n{(proc.stdout + proc.stderr)[-3000:]}"


@pytest.fixture(scope="module")
def fresh_install():
    base = make_url(os.environ["TEST_DATABASE_URL"])
    name = "archie_fresh_%s" % uuid.uuid4().hex[:8]
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    url = base.set(database=name).render_as_string(hide_password=False)

    env = dict(os.environ)
    env.update(DATABASE_URL=url, TEST_DATABASE_URL=url, FLASK_CONFIG="smoke", FLASK_DEBUG="0",
               MCP_ENABLED="false", ALLOW_INTERNAL_WEBHOOK_TARGETS="1")
    env.setdefault("SECRET_KEY", "smoke-only-not-secret-" + "x" * 16)
    suffix = uuid.uuid4().hex[:8]
    email = "fresh.%s@example.com" % suffix

    proc = None
    try:
        # What a new environment does: the migrations alone build the schema.
        _run(env, sys.executable, "-m", "flask", "--app", "manage", "schema-upgrade")
        _run(env, sys.executable, "-c", _SEED, email, PASSWORD, suffix)

        port = _free_port()
        log = open(os.path.join(os.environ.get("TMPDIR", "/tmp"), "fresh-%d.log" % port), "w+b")
        proc = subprocess.Popen(
            [sys.executable, "-m", "flask", "--app", "manage", "run", "--host", "127.0.0.1",
             "--port", str(port), "--no-reload", "--with-threads"],
            cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        base_url = "http://127.0.0.1:%d" % port
        deadline = time.time() + 180
        while time.time() < deadline:
            assert proc.poll() is None, "server exited during boot"
            try:
                if requests.get(base_url + "/health", timeout=3).status_code == 200:
                    break
            except requests.RequestException:
                time.sleep(1)
        else:
            pytest.fail("server did not become healthy")
        yield {"base": base_url, "email": email, "url": url}
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _picker_names(page, base):
    response = page.request.get(base + PICKER_URL)
    assert response.status == 200, f"picker answered {response.status}: {response.text()[:300]}"
    payload = response.json()
    data = payload.get("data", payload)
    return [c["name"] for c in data["capabilities"]]


def _walk_shots(page, base):
    """Save the capability list at desktop and phone width when WALK_DIR is set."""
    directory = os.environ.get("WALK_DIR")
    if not directory:
        return
    os.makedirs(directory, exist_ok=True)
    for label, width in (("1440", 1440), ("390", 390)):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(base + LIST_URL, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.screenshot(path=os.path.join(directory, "capability-list-%s.png" % label), full_page=True)


@pytest.mark.smoke
@pytest.mark.journey
def test_new_capability_on_a_migrated_database_reaches_the_picker(browser, fresh_install):
    base = fresh_install["base"]
    page = browser.new_context(ignore_https_errors=True).new_page()
    try:
        page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", fresh_install["email"])
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)

        # Empty case: nothing yet, in the list and in the picker alike.
        page.goto(base + LIST_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator(".cap-card").count() == 0
        assert _picker_names(page, base) == []

        name = "Fresh Install Capability %d" % int(time.time() * 1000)
        page.locator('[data-action="openCapCreate"]').click()
        expect(page.locator("#cap-dialog")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.fill("#cap-name", name)
        page.select_option("#cap-category", "strategic")
        page.fill("#cap-owner", "Fresh Owner")
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/enterprise/capabilities" in r.url,
            timeout=PAGE_TIMEOUT,
        ) as created:
            page.locator('[data-action="submitCap"]').click()
        assert created.value.status < 400, f"create failed: {created.value.status}"

        # The list shows it after a reload, and the picker offers the same capability.
        page.goto(base + LIST_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator(".cap-card", has_text=name)).to_be_visible(timeout=PAGE_TIMEOUT)
        assert _picker_names(page, base) == [name]
        _walk_shots(page, base)

        # The projected row says where it came from.
        engine = create_engine(fresh_install["url"])
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT source_table FROM unified_capabilities WHERE name = :n"),
                    {"n": name},
                ).all()
        finally:
            engine.dispose()
        assert [r[0] for r in row] == ["business_capability"]
    finally:
        page.context.close()
