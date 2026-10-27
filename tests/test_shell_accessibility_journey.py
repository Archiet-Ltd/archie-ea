"""A person using a screen reader hears what a sighted person reads, on the pages that failed the sweep.

Written from the goal "a signed-in person can use the organisation switcher, the account menu, the
account forms, the formula register and the dashboard lists with assistive technology". The real
pages are rendered by the application for a signed-in organisation administrator, served over a
loopback socket with the application's own stylesheets and scripts, and checked in Chromium with
axe-core at 1440 and 390 wide, in light and dark. The assertions name the rules the sweep found and
the elements a person meets, so a regression reads as the thing a screen reader user loses.
"""
import json
import mimetypes
import os
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest
from werkzeug.serving import make_server
from werkzeug.wrappers import Request, Response

pytestmark = pytest.mark.usefixtures("db_session")

ROOT = Path(__file__).resolve().parents[1]
SIZES = {"desktop": {"width": 1440, "height": 900}, "phone": {"width": 390, "height": 844}}
SCHEMES = ("light", "dark")

# page -> the rules the sweep found on it that this change removes
PAGES = {
    "/dashboard/overview": ["label-content-name-mismatch", "aria-required-children", "color-contrast"],
    "/account/manage": ["label-content-name-mismatch", "label", "dlitem", "color-contrast"],
    "/account/manage/info": ["label-content-name-mismatch", "label", "dlitem", "color-contrast"],
    "/admin/formula-register/": ["label-content-name-mismatch", "label"],
    "/archimate/composer": ["label-content-name-mismatch", "aria-required-children", "list"],
    "/backlog/prioritisation": ["label-content-name-mismatch", "aria-valid-attr-value"],
    "/dashboard/vendor-analysis/new": ["label-content-name-mismatch", "aria-valid-attr-value", "aria-required-children"],
    "/dashboard/rationalization": ["label-content-name-mismatch", "aria-allowed-attr"],
    "/applications/vendors": ["label-content-name-mismatch", "link-in-text-block"],
    "/applications/rationalization": ["label-content-name-mismatch", "color-contrast"],
    "/dashboard/health": ["label-content-name-mismatch", "link-in-text-block"],
    "/solutions/briefings": ["label-content-name-mismatch", "link-in-text-block"],
    "/vendors/technical": ["label-content-name-mismatch", "link-in-text-block"],
    "/enterprise/data_architecture_dashboard": ["label-content-name-mismatch", "color-contrast"],
    "/architecture/elements": ["label-content-name-mismatch", "color-contrast"],
    "/capability-map/hierarchy": ["label-content-name-mismatch", "color-contrast"],
    "/epics/hierarchy": ["label-content-name-mismatch", "color-contrast"],
    "/solutions/portfolio": ["label-content-name-mismatch", "color-contrast"],
    "/stakeholders/map": ["label-content-name-mismatch", "color-contrast"],
    "/enterprise/policy-monitoring": ["label-content-name-mismatch", "color-contrast"],
    # a11y03: the failures left after the shell and the dashboards
    "/batch-import/new": ["link-name"],
    "/capability-map/trees": ["link-name"],
    "/capability-map": ["button-name", "label-content-name-mismatch"],
    "/adm-kanban/": ["label-content-name-mismatch"],
    "/consolidation-list/": ["label-content-name-mismatch"],
    "/solutions/templates": ["label-content-name-mismatch"],
    "/market-intelligence/": ["label-content-name-mismatch"],
    "/code-generation/dashboard": ["label-content-name-mismatch"],
    "/architecture/": ["label-content-name-mismatch"],
    "/duplicate-detection/dashboard": ["label-content-name-mismatch", "scrollable-region-focusable"],
    "/capability-maturity/batch-update": ["scrollable-region-focusable"],
    "/usage-analytics/dashboard": ["scrollable-region-focusable"],
    "/portfolio/demands": ["scrollable-region-focusable"],
    "/strategic/process-optimization": ["scrollable-region-focusable"],
    "/genome/ai-systems": ["scrollable-region-focusable"],
    "/archimate/traceability": ["color-contrast"],
    "/ai-chat/entity-matching": ["scrollable-region-focusable"],
    # polish01: what QA still found after a11y03
    "/ea-workflows/deliverables/vision": ["link-in-text-block"],
    "/integration/": ["scrollable-region-focusable"],
    "/capability-roadmap": ["scrollable-region-focusable"],
}
RUN = """async () => { const r = await axe.run(document, {runOnly: {type: 'tag',
  values: ['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']}, resultTypes: ['violations']});
  return r.violations.filter(v => ['serious','critical'].includes(v.impact)).map(v => ({id: v.id,
    nodes: v.nodes.map(n => n.target.join(' ') + ' :: ' + (n.failureSummary || '').split('\\n').slice(0, 3).join(' | ').slice(0, 700))})); }"""
SHELL = """() => Object.fromEntries(['org-switcher-trigger', 'user-menu-btn'].map(id => {
  const el = document.getElementById(id);
  return [id, el ? {label: el.getAttribute('aria-label'), text: el.innerText.replace(/\\s+/g, ' ').trim()} : null]; }))"""


def _axe_source():
    candidates = [os.environ.get("AXE_CORE_JS", ""), str(ROOT / "node_modules/axe-core/axe.min.js")]
    for path in candidates:
        if path and Path(path).is_file():
            return Path(path).read_text(encoding="utf-8")
    pytest.fail("axe-core is not installed: run `npm install` (axe-core is a devDependency) or set AXE_CORE_JS")


def _seed_applications(db_session, org):
    """A portfolio with systems that have no vendor recorded, so the pages that point at the
    vendor mapping step show the link a person would meet."""
    from app.models.application_portfolio import ApplicationComponent

    for index in range(4):
        db_session.add(ApplicationComponent(name="Ledger %d %s" % (index, uuid.uuid4().hex[:6]), organization_id=org.id))
    db_session.flush()


def _signed_in_pages(client, db_session, make_org):
    from flask import g

    from app.models.user import Permission, Role, User
    from tests._session_test_helpers import mint_test_sid

    from app import db
    from app.models.organization_signal import OrganizationSignal

    OrganizationSignal.__table__.create(db.session.connection(), checkfirst=True)
    org = make_org("axe")
    _seed_applications(db_session, org)
    user = User(email="axe-%s@example.com" % uuid.uuid4().hex[:8], first_name="Axel", last_name="Reader",
                organization_id=org.id, confirmed=True, enterprise_role="platform_admin", is_org_admin=True)
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter(Role.name.in_(("Administrator", "Admin", "admin"))).first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()
    user.role = role
    db_session.flush()
    pages = {}
    sid = mint_test_sid(user.id)
    for path in PAGES:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
            sess["_sid"] = sid
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)
        response = client.get(path, follow_redirects=True)
        assert response.status_code == 200, "%s answered %s" % (path, response.status_code)
        pages[path] = response.get_data(as_text=True)
    return pages


class Peer:
    def __init__(self, app, pages):
        self.static = Path(app.static_folder)
        self.pages = pages
        self.server = make_server("127.0.0.1", 0, self.wsgi, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.server.server_port

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    @Request.application
    def wsgi(self, request):
        path = urlparse(request.url).path
        if path in self.pages:
            return Response(self.pages[path], mimetype="text/html")
        if path.startswith("/static/"):
            target = (self.static / path[len("/static/"):]).resolve()
            if self.static.resolve() in target.parents and target.is_file():
                kind = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                return Response(target.read_bytes(), mimetype=kind)
            return Response("", status=404)
        return Response(json.dumps({"success": True, "data": []}), mimetype="application/json")


_SWEPT = {}


@pytest.fixture
def swept(app, client, db_session, make_org):
    """{(path, size, scheme): shell words and violations} for every page, size and scheme."""
    from playwright.sync_api import sync_playwright

    if _SWEPT:
        return _SWEPT
    pages = _signed_in_pages(client, db_session, make_org)
    axe = _axe_source()
    found = {}
    with Peer(app, pages) as peer, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for size, viewport in SIZES.items():
                for scheme in SCHEMES:
                    context = browser.new_context(viewport=viewport, color_scheme=scheme)
                    page = context.new_page()
                    for path in PAGES:
                        page.goto(peer.base + path, wait_until="load")
                        page.wait_for_timeout(600)
                        if scheme == "dark":
                            page.evaluate("() => document.documentElement.classList.add('dark')")
                        page.add_script_tag(content=axe)
                        found[(path, size, scheme)] = {"violations": page.evaluate(RUN), "shell": page.evaluate(SHELL)}
                    context.close()
        finally:
            browser.close()
    _SWEPT.update(found)
    return _SWEPT


def test_the_organisation_switcher_and_account_menu_are_announced_with_the_words_shown_on_them(swept):
    for (path, size, scheme), result in swept.items():
        for ident, element in result["shell"].items():
            assert element, "%s has no #%s at %s %s" % (path, ident, size, scheme)
            assert element["text"], "#%s shows no words on %s" % (ident, path)
            name = " ".join((element["label"] or "").lower().split())
            for word in element["text"].lower().split():
                assert word in name, "#%s says %r aloud but shows %r on %s" % (ident, element["label"], element["text"], path)


@pytest.mark.parametrize("rule", sorted({r for rules in PAGES.values() for r in rules}))
def test_no_page_loses_a_screen_reader_user_to_this_rule(swept, rule):
    failures = []
    for (path, size, scheme), result in swept.items():
        if rule not in PAGES[path]:
            continue
        for violation in result["violations"]:
            if violation["id"] == rule:
                failures.append("%s %s %s: %s" % (path, size, scheme, violation["nodes"][:2]))
    assert failures == [], "\n".join(failures)
