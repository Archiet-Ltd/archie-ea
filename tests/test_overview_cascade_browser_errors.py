"""The dashboard overview's answer cascade raises no browser errors.

Two browser behaviours, each seen only in one engine, broke journeys after the
north-star look:

* Firefox raised an uncaught page error, ``Object { isFromCancelledTransition }``,
  because the global loading bar faded in and out faster than one transition
  lasts while the cascade fired its requests.
* WebKit logged "Fetch API cannot load ... due to access control checks" for
  every impact request still in flight when a person left the page.

The real overview page (real template, real scripts and stylesheets) is
rendered by the application, then served over a loopback socket next to a small
peer that answers the cascade's three requests, so the requests and the
navigation are real. Only the answers are controlled. The tests assert what a
person's browser reports, not how the markup is written.
"""
import mimetypes
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest
from werkzeug.serving import make_server
from werkzeug.wrappers import Request, Response

pytestmark = pytest.mark.usefixtures("db_session")

ELEMENTS = 8


def _grant_admin(db_session, user):
    from app.models.user import Permission, Role

    role = Role.query.filter(Role.name.in_(("Administrator", "Admin", "admin"))).first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()
    elif role.permissions is None or (role.permissions & Permission.ADMINISTER) != Permission.ADMINISTER:
        role.permissions = Permission.ADMINISTER
    user.role = role
    db_session.flush()


def _make_data_mode(db_session, org):
    """Five applications and one capability mapping: the dashboard's data mode,
    the only mode that renders the answer cascade."""
    from flask import g
    from sqlalchemy import insert

    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability

    g.current_org_id = org.id
    apps = []
    for i in range(6):
        component = ApplicationComponent(name="Cascade App %d-%s" % (i, uuid.uuid4().hex[:6]), organization_id=org.id)
        db_session.add(component)
        apps.append(component)
    db_session.flush()
    name = "Cascade Capability %s" % uuid.uuid4().hex[:6]
    element_id = db_session.execute(
        insert(ArchiMateElement.__table__).values(
            name=name, type="Capability", layer="Strategy", organization_id=org.id
        )
    ).inserted_primary_key[0]
    db_session.flush()
    capability = BusinessCapability(name=name, level=1, organization_id=org.id, archimate_element_id=element_id)
    db_session.add(capability)
    db_session.flush()
    db_session.add(
        ApplicationCapabilityMapping(
            application_component_id=apps[0].id, business_capability_id=capability.id, organization_id=org.id
        )
    )
    db_session.flush()


def _login_user(app, client, db_session, make_org):
    from flask import g

    from app.models.user import User
    from tests._session_test_helpers import mint_test_sid

    org = make_org("cascade")
    user = User(
        email="cascade-%s@example.com" % uuid.uuid4().hex[:8],
        first_name="Cascade",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="platform_admin",
        is_org_admin=True,
    )
    db_session.add(user)
    db_session.flush()
    _grant_admin(db_session, user)
    _make_data_mode(db_session, org)
    sid = mint_test_sid(user.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        if sid:
            sess["_sid"] = sid
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)
    return user


class Peer:
    """The overview page, its static files and the cascade's answers."""

    def __init__(self, app, page_html, impact_delay, page_delay=0):
        self.static = Path(app.static_folder)
        self.page_html = page_html
        self.impact_delay = impact_delay
        self.page_delay = page_delay
        self.impact_requests = []
        self.first_impact = threading.Event()
        self.all_impact = threading.Event()
        self.server = make_server("127.0.0.1", 0, self.wsgi, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.server.server_port

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.all_impact.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    @Request.application
    def wsgi(self, request):
        path = urlparse(request.url).path
        if path == "/dashboard/overview":
            return Response(self.page_html, mimetype="text/html")
        if path == "/elsewhere":
            # A page that is slow to arrive, as on a loaded server: the old page
            # stays alive while the browser waits for it.
            threading.Event().wait(self.page_delay)
            return Response("<!doctype html><title>Elsewhere</title><h1>Elsewhere</h1>", mimetype="text/html")
        if path.startswith("/static/"):
            target = (self.static / path[len("/static/"):]).resolve()
            if self.static.resolve() in target.parents and target.is_file():
                kind = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                return Response(target.read_bytes(), mimetype=kind)
            return Response("", status=404)
        if path == "/archimate/api/elements/search":
            found = [
                {"id": 100 + i, "name": "Application %d" % i, "layer": "application", "relationship_count": 50 - i}
                for i in range(ELEMENTS)
            ]
            return Response(_json({"data": found}), mimetype="application/json")
        if path.startswith("/api/v1/intelligence/impact/"):
            self.impact_requests.append(path)
            self.first_impact.set()
            if len(self.impact_requests) >= ELEMENTS:
                self.all_impact.set()
            threading.Event().wait(self.impact_delay)
            return Response(_json({"data": {"elements": {}, "rows": []}}), mimetype="application/json")
        if path == "/intelligence/api/history/changes":
            return Response(_json({"data": {"changes": []}}), mimetype="application/json")
        return Response(_json({"success": True, "data": []}), mimetype="application/json")


def _json(value):
    import json

    return json.dumps(value)


def _browser(playwright, engine):
    import os

    try:
        return getattr(playwright, engine).launch()
    except Exception as exc:  # the engine binary is not installed here
        message = "%s unavailable: %s" % (engine, str(exc)[:120])
        if os.environ.get("SMOKE_REQUIRE_BROWSER") == "1":
            pytest.fail(message)
        pytest.skip(message)


def _overview_html(app, client, db_session, make_org):
    _login_user(app, client, db_session, make_org)
    response = client.get("/dashboard/overview")
    assert response.status_code == 200, response.get_data(as_text=True)[:500]
    return response.get_data(as_text=True)


def test_firefox_reports_no_cancelled_transition_while_the_cascade_runs(app, client, db_session, make_org):
    from playwright.sync_api import sync_playwright

    html = _overview_html(app, client, db_session, make_org)
    with Peer(app, html, impact_delay=0.08) as peer, sync_playwright() as playwright:
        browser = _browser(playwright, "firefox")
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(peer.base + "/dashboard/overview", wait_until="domcontentloaded")
            assert peer.all_impact.wait(60), "the cascade made %d impact requests" % len(peer.impact_requests)
            page.wait_for_timeout(1500)
            assert len(peer.impact_requests) >= 2
            assert [e for e in errors if "isFromCancelledTransition" in e] == []
            assert errors == [], errors
        finally:
            browser.close()


def test_webkit_reports_no_failed_fetch_when_leaving_mid_cascade(app, client, db_session, make_org):
    from playwright.sync_api import sync_playwright

    html = _overview_html(app, client, db_session, make_org)
    with Peer(app, html, impact_delay=0.4, page_delay=3) as peer, sync_playwright() as playwright:
        browser = _browser(playwright, "webkit")
        try:
            page = browser.new_page()
            messages = []
            page.on(
                "console",
                lambda message: messages.append(message.text) if message.type == "error" else None,
            )
            page.goto(peer.base + "/dashboard/overview", wait_until="domcontentloaded")
            assert peer.first_impact.wait(60), "the cascade never asked an impact question"
            page.evaluate("location.href = '/elsewhere'")
            page.wait_for_url("**/elsewhere", timeout=30000)
            page.wait_for_timeout(500)
            failed = [m for m in messages if "Fetch API cannot load" in m or "access control checks" in m]
            assert failed == [], failed
        finally:
            browser.close()
