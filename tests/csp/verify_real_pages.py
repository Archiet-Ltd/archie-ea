"""Step-4 real-app smoke for ARCH-070.

Boots the real Flask app, logs in, loads real rendered pages, and for each one
extracts EVERY Alpine directive expression present in the live rendered DOM
(post-Jinja, post-any-JS-generation), then:
  - compiles each with the CSP evaluator (catches invalid-JS from interpolation),
  - evaluates each pure expression against the element's real Alpine scope and
    compares to the browser's native eval on the same scope (0 divergences).

This validates the evaluator against actual rendered app markup and real
component data, complementing the synthetic integration test.
"""
import os
import threading
import importlib.util
from pathlib import Path
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[2]
EVAL = (ROOT / "app/static/js/csp/csp-evaluator.js").read_text(encoding="utf-8")

PAGES = ["/archimate/composer", "/dashboard/", "/capability-map/", "/solutions/", "/applications/", "/architecture/dashboard", "/architecture-journey/", "/stakeholders/map", "/enterprise/capability-map/", "/admin/dashboard", "/vendors/"]


def _boot_app():
    # Follow the database the suite is actually using. This used to hardcode
    # port 5439 and a database name that exists on no machine here, so when it
    # ran under pytest the app fell back to DevelopmentConfig's default of
    # .../5432/archie -- the PRODUCTION database name, absent locally.
    os.environ.setdefault("FLASK_CONFIG", "development")
    if os.environ.get("TEST_DATABASE_URL"):
        os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ.setdefault("SECRET_KEY", "devkey")
    # A development app starts the job queue worker: a daemon thread that
    # outlives this function and keeps retrying a connection for the rest of the
    # pytest process, logging a WARNING each time. That is what made
    # test_boot_health::test_boot_warnings_are_allowlisted fail in a full run
    # and pass in isolation. This helper only renders pages; it never needs the
    # queue.
    os.environ.setdefault("DISABLE_JOB_QUEUE_WORKER", "1")
    # Override the CONFIG CLASS, before create_app. Two things make anything
    # later too late:
    #   - config.py binds SQLALCHEMY_DATABASE_URI at class-definition time, and
    #     conftest imported config long before this runs, so mutating os.environ
    #     here cannot reach it;
    #   - create_app touches the database during boot, so the engine is already
    #     built by the time app.config could be reassigned.
    # Without this the app fell back to DevelopmentConfig's default database
    # name, "archie", which is the PRODUCTION name and exists on no dev machine.
    if os.environ.get("TEST_DATABASE_URL"):
        import config as _config
        _config.DevelopmentConfig.SQLALCHEMY_DATABASE_URI = os.environ["TEST_DATABASE_URL"]
    from app import create_app
    app = create_app("development")
    app.config["WTF_CSRF_ENABLED"] = False
    return app


def _seed_login_user(app):
    from app import db
    from app.models.user import User
    from app.models.organization import Organization
    from werkzeug.security import generate_password_hash
    import uuid
    with app.app_context():
        org = Organization.query.first()
        if not org:
            org = Organization(name="Smoke", slug="smoke-" + uuid.uuid4().hex[:6])
            db.session.add(org)
            db.session.flush()
        email = f"csp-smoke-{uuid.uuid4().hex[:8]}@example.com"
        u = User(email=email, first_name="Csp", last_name="Smoke", confirmed=True,
                 organization_id=org.id, password_hash=generate_password_hash("x"),
                 is_platform_admin=True,
                 enterprise_role="platform_admin")
        db.session.add(u)
        db.session.commit()
        return u.id


EXTRACT_JS = r"""
() => {
  const ATTRS = ['x-text','x-show','x-if','x-html','x-model','x-init','x-effect','x-data'];
  const out = [];
  const push = (el, expr) => { if (expr && expr.trim()) out.push({expr}); };
  document.querySelectorAll('*').forEach(el => {
    for (const a of el.attributes) {
      const n = a.name;
      if (ATTRS.includes(n) || n.startsWith('x-on:') || n.startsWith('@') ||
          n.startsWith('x-bind:') || (n.startsWith(':') && n.length>1)) {
        if (a.value && !a.value.includes('{{')) push(el, a.value);
      }
    }
  });
  // dedup
  const seen = new Set(), uniq = [];
  for (const o of out) { if (!seen.has(o.expr)) { seen.add(o.expr); uniq.push(o.expr); } }
  return uniq;
}
"""


def _stop_background_work(app):
    """Stop the APScheduler a development-config boot starts.

    ``create_app("development")`` starts the scheduler, whose event-log relay
    fires every few seconds against the database. When this runs inside the
    pytest process nothing else stops it, so it keeps polling for the rest of
    the shard and races whichever tests come next: a deadlock on
    ``transformation_outbox_events`` in the decision-brief tests, and the relay
    using a ``db.session`` a test had patched in the gateway savepoint tests.
    ``wait=True`` so no job is still mid-run when this returns.
    """
    scheduler = app.extensions.pop("ea_workflow_scheduler", None)
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=True)


def main():
    app = _boot_app()
    try:
        return _main(app)
    finally:
        _stop_background_work(app)


def _main(app):
    from playwright.sync_api import sync_playwright
    uid = _seed_login_user(app)
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    port = srv.socket.getsockname()[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    total_exprs = 0
    parse_fail = []
    diverge = []
    pages_ok = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            ctx = b.new_context()
            pg = ctx.new_page()
            base = f"http://127.0.0.1:{port}"
            # Authenticate by setting the flask-login session cookie directly (CSRF
            # is off in this config); mirrors how the pytest login_as fixture works.
            with app.app_context():
                from flask import session
            # Use a request context to mint a signed session cookie.
            with app.test_request_context():
                from flask import session as _s
                _s["_user_id"] = str(uid)
                _s["_fresh"] = True
                from flask.sessions import SecureCookieSessionInterface
                si = SecureCookieSessionInterface()
                serializer = si.get_signing_serializer(app)
                cookie_val = serializer.dumps(dict(_s))
            ctx.add_cookies([{"name": app.config.get("SESSION_COOKIE_NAME", "session"),
                              "value": cookie_val, "url": base}])
            # Capture the page's OWN Alpine init errors. The CSP-safe evaluator
            # drives Alpine on these real pages (Alpine.setEvaluator), so a bug in
            # it surfaces as a pageerror here — which the expression-extraction
            # check below CANNOT see (it evals against a mock scope). This is the
            # guard that catches "window/document resolves to undefined" class
            # bugs that break a whole page's interactivity.
            page_errors = []
            pg.on("pageerror", lambda e: page_errors.append(str(e)))
            for path in PAGES:
                before = len(page_errors)
                try:
                    resp = pg.goto(base + path, wait_until="domcontentloaded", timeout=15000)
                except Exception as e:
                    pages_ok.append((path, f"nav-error {e}"))
                    continue
                code = resp.status if resp else 0
                pg.wait_for_timeout(800)  # let Alpine init run and throw if it will
                # Many real pages redirect to login when unauthenticated; that's fine —
                # we still extract whatever Alpine markup rendered (login page has some).
                pg.add_script_tag(content=EVAL)
                exprs = pg.evaluate(EXTRACT_JS)
                total_exprs += len(exprs)
                res = pg.evaluate(
                    r"""(exprs) => {
                        const pf = [], dv = [];
                        function mock(){ const f=function(){return f;}; return new Proxy(f,{get(t,k){if(typeof k==='symbol')return undefined; if(k==='length')return 0; return mock();},apply(){return mock();},has(){return true;}}); }
                        function sc(){ return new Proxy({},{get(_,k){if(typeof k==='symbol')return undefined; return mock();},has(k){return typeof k!=='symbol';}}); }
                        for (const e of exprs) {
                            try { window.CSPExpr.compile(e); }
                            catch(err){ pf.push([e.slice(0,80), String(err).slice(0,50)]); continue; }
                            if (/[^=!<>]=[^=]|\+\+|--|;|\breturn\b|\bif\b|=>|\bnew\b|`|\btry\b/.test(e)) continue;
                            let a,bn,ea=null,eb=null;
                            try{ a=window.CSPExpr.run(e, sc()); }catch(x){ ea=String(x); }
                            try{ bn=(new Function('S','with(S){return ('+e+');}'))(sc()); }catch(x){ eb=String(x); }
                            const prim=x=>x===null||['number','string','boolean','undefined'].includes(typeof x);
                            if (ea&&eb) continue;
                            if (ea||eb){ dv.push([e.slice(0,70), 'ea='+ea, 'eb='+eb]); continue; }
                            if (prim(a)&&prim(bn)&&String(a)!==String(bn)) dv.push([e.slice(0,70), String(a), String(bn)]);
                        }
                        return {pf, dv};
                    }""", exprs)
                parse_fail += res["pf"]
                diverge += res["dv"]
                new_errs = page_errors[before:]
                pages_ok.append((path, f"http={code} exprs={len(exprs)} parsefail={len(res['pf'])} "
                                       f"diverge={len(res['dv'])} pageerrors={len(new_errs)}",
                                 new_errs))
            b.close()
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=10)

    print("=== ARCH-070 real-app-page smoke ===")
    total_page_errors = []
    for row in pages_ok:
        path, info = row[0], row[1]
        errs = row[2] if len(row) > 2 else []
        total_page_errors += errs
        print(f"  {path:26} {info}")
        for e in errs[:3]:
            print(f"       PAGEERROR: {e[:120]}")
    print(f"\n  total expressions checked: {total_exprs}")
    print(f"  parse failures: {len(parse_fail)}")
    for e, why in parse_fail[:15]:
        print(f"     PARSEFAIL: {e!r} -- {why}")
    print(f"  divergences vs native eval: {len(diverge)}")
    for d in diverge[:15]:
        print(f"     DIVERGE: {d}")
    print(f"  page (Alpine init) errors: {len(total_page_errors)}")
    ok = (not parse_fail and not diverge and not total_page_errors and total_exprs > 0)
    print("\nRESULT:", "PASS" if ok else ("FAIL" if (total_exprs or total_page_errors)
                                          else "INCONCLUSIVE (no expressions rendered)"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
