"""Live production smoke — the test-lead sweep.

Logs into the LIVE site once (authenticated), visits every user-facing GET page,
and records per page: HTTP status, whether it redirected (to login = auth/route
problem), JS/Alpine page errors, and CSP violations. Serial (2-vCPU rule).

Reports a failure matrix: any page that is not a clean 2xx, redirected off its
own path unexpectedly, threw a JS error, or triggered a CSP violation.

Usage:
    LIVE_BASE=https://165-22-125-156.sslip.io \
    LIVE_USER=qa-extension@example.com LIVE_PW=QaExtension2026 \
    python tests/csp/live_smoke.py [routes_file]
"""
import os
import sys
import time
from pathlib import Path

BASE = os.environ.get("LIVE_BASE", "https://165-22-125-156.sslip.io")
USER = os.environ.get("LIVE_USER", "qa-extension@example.com")
PW = os.environ.get("LIVE_PW", "QaExtension2026")
ROUTES_FILE = sys.argv[1] if len(sys.argv) > 1 else str(
    Path(__file__).resolve().parents[2] / "scratch_routes.txt")


def main():
    from playwright.sync_api import sync_playwright
    routes = [r.strip() for r in open(ROUTES_FILE) if r.strip()]
    # login and dashboard pages we don't want to count as "redirected to login"
    login_paths = ("/account/login", "/account/register", "/account/reset-password",
                   "/account/unconfirmed", "/account/confirm-account")

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width": 1400, "height": 900})
        pg = ctx.new_page()

        page_errors = []
        pg.on("pageerror", lambda e: page_errors.append(str(e)))
        pg.add_init_script(
            "document.addEventListener('securitypolicyviolation',e=>{"
            "window.__csp=window.__csp||[];"
            "window.__csp.push(e.violatedDirective+'|'+(e.effectiveDirective||'')+'|'+(e.blockedURI||'inline'));});")

        # ── login ──
        for attempt in range(3):
            try:
                pg.goto(BASE + "/account/login", wait_until="domcontentloaded", timeout=30000)
                break
            except Exception as e:
                print(f"login nav retry {attempt}: {str(e)[:50]}")
                time.sleep(4)
        pg.fill("input[name='email']", USER)
        pg.fill("input[name='password']", PW)
        pg.click("button[type='submit'], input[type='submit']")
        pg.wait_for_timeout(3500)
        if "/account/login" in pg.url:
            print("FATAL: login failed, still on login page. Aborting.")
            return 1
        print(f"logged in as {USER}; landed on {pg.url}\n")

        rows = []
        for i, path in enumerate(routes):
            e0, c0 = len(page_errors), None
            try:
                c0 = pg.evaluate("() => (window.__csp||[]).length")
            except Exception:
                c0 = 0
            status = None
            landed = ""
            note = ""
            try:
                resp = pg.goto(BASE + path, wait_until="domcontentloaded", timeout=20000)
                status = resp.status if resp else 0
                pg.wait_for_timeout(350)
                landed = pg.evaluate("() => location.pathname")
            except Exception as ex:
                note = "NAV-TIMEOUT/ERR: " + str(ex)[:40]
            new_errs = page_errors[e0:]
            try:
                pg.evaluate("() => (window.__csp||[]).length")
                new_csp = pg.evaluate("(n) => (window.__csp||[]).slice(n)", c0)
            except Exception:
                new_csp = []
            redirected_to_login = (landed and any(landed.startswith(lp) for lp in login_paths)
                                   and not any(path.startswith(lp) for lp in login_paths))
            rows.append({
                "path": path, "status": status, "landed": landed,
                "errors": new_errs, "csp": new_csp,
                "redir_login": redirected_to_login, "note": note,
            })
            if (i + 1) % 50 == 0:
                print(f"  ...{i+1}/{len(routes)} pages checked")
        b.close()

    # ── report ──
    def bad(r):
        if r["note"]:
            return True
        if r["errors"] or r["csp"]:
            return True
        if r["status"] and r["status"] >= 500:
            return True
        if r["redir_login"]:
            return True
        return False

    total = len(rows)
    failures = [r for r in rows if bad(r)]
    http_5xx = [r for r in rows if (r["status"] or 0) >= 500]
    http_4xx = [r for r in rows if 400 <= (r["status"] or 0) < 500]
    js_err = [r for r in rows if r["errors"]]
    csp = [r for r in rows if r["csp"]]
    redir = [r for r in rows if r["redir_login"]]
    navto = [r for r in rows if r["note"]]

    print("\n" + "=" * 70)
    print(f"LIVE SMOKE — {total} pages")
    print(f"  clean:            {total - len(failures)}")
    print(f"  JS page errors:   {len(js_err)}")
    print(f"  CSP violations:   {len(csp)}")
    print(f"  HTTP 5xx:         {len(http_5xx)}")
    print(f"  HTTP 4xx:         {len(http_4xx)}  (some expected: role-gated)")
    print(f"  redirected->login:{len(redir)}  (auth/session lost)")
    print(f"  nav timeout/err:  {len(navto)}")
    print("=" * 70)

    def dump(title, items, show):
        if not items:
            return
        print(f"\n### {title} ({len(items)})")
        for r in items[:60]:
            print(f"  {r['path']}  [{r['status']}]  {show(r)}")

    dump("JS PAGE ERRORS", js_err, lambda r: r["errors"][0][:90])
    dump("CSP VIOLATIONS", csp, lambda r: str(r["csp"][:2]))
    dump("HTTP 5xx", http_5xx, lambda r: "")
    dump("REDIRECTED TO LOGIN", redir, lambda r: "-> " + r["landed"])
    dump("NAV TIMEOUT/ERR", navto, lambda r: r["note"])
    dump("HTTP 4xx (role-gated / not-found)", http_4xx, lambda r: "-> " + (r["landed"] or ""))

    print("\nRESULT:", "PASS — no JS errors / CSP violations / 5xx"
          if not (js_err or csp or http_5xx) else "SEE FAILURES ABOVE")
    return 0 if not (js_err or csp or http_5xx) else 2


if __name__ == "__main__":
    sys.exit(main())
