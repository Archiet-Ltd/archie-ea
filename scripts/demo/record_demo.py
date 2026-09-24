# -*- coding: utf-8 -*-
"""Record the persona-driven demo walkthrough (see docs/demo-script.md).

Drives a running, seeded Entelim instance with Playwright's record_video and
injects an on-screen caption bar (persona + action), chapter title cards and a
synthetic cursor (Playwright's video can't capture the OS cursor).

    DEMO_BASE=http://localhost:5000 \
    DEMO_USER=demo@example.com DEMO_PASS='...' \
    python scripts/demo/record_demo.py        # writes demo.webm next to this file

For a polished marketing cut, screen-record a headed run with a real cursor
(OBS/Loom) using this as the storyboard, then convert webm -> mp4/gif.
"""
import os

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DEMO_BASE", "http://localhost:5000").rstrip("/")
OUTDIR = os.environ.get("DEMO_OUT", os.path.dirname(os.path.abspath(__file__)))
USER = os.environ.get("DEMO_USER", "demo@example.com")
PASS = os.environ.get("DEMO_PASS", "DemoPass123!")

OVERLAY = r"""
() => {
  const ensure = () => {
    if (!document.getElementById('__cur')) {
      const c = document.createElement('div'); c.id='__cur';
      c.style.cssText='position:fixed;left:720px;top:450px;z-index:2147483646;width:24px;height:24px;margin:-12px 0 0 -12px;border-radius:50%;background:rgba(37,99,235,.22);border:2.5px solid #2563eb;box-shadow:0 0 12px rgba(37,99,235,.75);pointer-events:none;transition:transform .12s ease';
      (document.body||document.documentElement).appendChild(c);
    }
    if (!document.getElementById('__cap')) {
      const b=document.createElement('div'); b.id='__cap';
      b.style.cssText='position:fixed;left:0;right:0;bottom:0;z-index:2147483646;padding:13px 26px;background:linear-gradient(0deg,rgba(15,23,42,.94),rgba(15,23,42,.74));color:#fff;font-family:system-ui,Segoe UI,sans-serif;display:flex;align-items:center;gap:14px;box-shadow:0 -2px 24px rgba(0,0,0,.35)';
      b.innerHTML='<span id="__who" style="background:#2563eb;padding:5px 13px;border-radius:999px;font:600 14px system-ui;white-space:nowrap"></span><span id="__txt" style="font:500 17px system-ui;opacity:.96"></span>';
      (document.body||document.documentElement).appendChild(b);
    }
  };
  if (document.readyState==='loading') document.addEventListener('DOMContentLoaded', ensure); else ensure();
  window.__cur_set=(x,y)=>{const c=document.getElementById('__cur'); if(c){c.style.left=x+'px';c.style.top=y+'px';}};
  window.__cur_pulse=()=>{const c=document.getElementById('__cur'); if(c){c.style.transform='scale(.5)'; setTimeout(()=>c.style.transform='scale(1)',160);}};
  window.__caption=(w,t)=>{ensure(); const W=document.getElementById('__who'),T=document.getElementById('__txt'); if(W)W.textContent=w; if(T)T.textContent=t;};
  window.__title=(t,s)=>{const o=document.createElement('div'); o.id='__tc';
    o.style.cssText='position:fixed;inset:0;z-index:2147483647;background:radial-gradient(circle at 50% 38%,#1e293b,#0b1220);color:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;font-family:system-ui,Segoe UI,sans-serif;opacity:0;transition:opacity .45s';
    o.innerHTML='<div style="font:800 46px/1.15 system-ui;letter-spacing:-1px;text-align:center">'+t+'</div><div style="font:400 22px system-ui;opacity:.82;max-width:780px;text-align:center;line-height:1.4">'+s+'</div>';
    document.body.appendChild(o); requestAnimationFrame(()=>o.style.opacity='1');};
  window.__title_off=()=>{const o=document.getElementById('__tc'); if(o){o.style.opacity='0'; setTimeout(()=>o.remove(),450);}};
}
"""


def main():
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=False)
        except Exception:
            browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  record_video_dir=OUTDIR, record_video_size={"width": 1440, "height": 900})
        # OVERLAY is a bare arrow function `() => {...}`; add_init_script evaluates the
        # expression but never calls it, so the caption/cursor/title helpers were never
        # defined and every overlay call silently no-op'd. Wrap in an IIFE so it runs.
        ctx.add_init_script("(" + OVERLAY + ")()")
        page = ctx.new_page()
        page.set_default_timeout(15000)
        cur = [720, 450]

        def move(x, y, steps=24):
            sx, sy = cur
            for i in range(1, steps + 1):
                ix, iy = sx + (x - sx) * i / steps, sy + (y - sy) * i / steps
                page.mouse.move(ix, iy)
                try:
                    page.evaluate("(p)=>window.__cur_set&&window.__cur_set(p[0],p[1])", [ix, iy])
                except Exception:
                    pass
                page.wait_for_timeout(10)
            cur[0], cur[1] = x, y

        def click_at(x, y):
            move(x, y)
            page.wait_for_timeout(160)
            try:
                page.evaluate("()=>window.__cur_pulse&&window.__cur_pulse()")
            except Exception:
                pass
            page.wait_for_timeout(150)
            page.mouse.click(x, y)

        def goto(u, wait=1800):
            # The `try:` was missing, leaving an `except` with nothing to attach to.
            # That is a hard SyntaxError, so this module could not be imported or
            # run at all. Restored to match the try/except shape every other helper
            # in this file uses.
            try:
                page.goto(BASE + u, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(wait)
            except Exception as e:
                print("goto warn", u, str(e)[:40])

        def cap(w, t):
            try:
                page.evaluate("(a)=>window.__caption&&window.__caption(a[0],a[1])", [w, t])
            except Exception:
                pass

        def title(t, s, hold=2700):
            try:
                page.evaluate("(a)=>window.__title&&window.__title(a[0],a[1])", [t, s])
            except Exception:
                pass
            page.wait_for_timeout(hold)
            try:
                page.evaluate("()=>window.__title_off&&window.__title_off()")
            except Exception:
                pass
            page.wait_for_timeout(500)

        def click_el(sel, wait=1400):
            try:
                el = page.wait_for_selector(sel, timeout=5000, state="visible")
                b = el.bounding_box()
                if b:
                    click_at(b["x"] + min(b["width"] / 2, 120), b["y"] + min(b["height"] / 2, 16))
                page.wait_for_timeout(wait)
            except Exception:
                pass

        def hover_el(sel, wait=900):
            try:
                el = page.wait_for_selector(sel, timeout=5000, state="visible")
                b = el.bounding_box()
                if b:
                    move(b["x"] + min(b["width"] / 2, 120), b["y"] + min(b["height"] / 2, 16))
                page.wait_for_timeout(wait)
            except Exception:
                pass

        def wander(pts):
            # `pts` is a list of (x, y) points — call sites pass e.g.
            # wander([(500, 420), (900, 480)]) — but the body referenced bare `x`
            # and `y`, which are bound nowhere, so this raised NameError on every
            # call. It was invisible until the SyntaxError above was fixed, because
            # the linter could not parse this file at all.
            for x, y in pts:
                move(x, y)
                page.wait_for_timeout(500)

        try:
            goto("/", 1200)
            title("Entelim", "Open-source, AI-native enterprise architecture<br>TOGAF 9.2 &middot; ArchiMate 3.2 &middot; AGPL-3.0", 3200)
            cap("Get started", "Self-host the whole platform with one docker compose up")
            wander([(500, 420), (900, 480)])
            page.goto(BASE + "/account/login", wait_until="domcontentloaded")
            page.wait_for_timeout(1000)
            cap("Sign in", "Secure multi-tenant login")
            click_el("#email", 250)
            page.type("#email", USER, delay=50)
            page.wait_for_timeout(400)
            click_el("#password", 250)
            page.type("#password", PASS, delay=50)
            page.wait_for_timeout(400)
            click_el("#submit", 3000)
            goto("/dashboard/overview", 1600)
            # Fail loudly if auth didn't take — otherwise every chapter silently
            # records the login page (a redirect to /account/login still returns 200,
            # so this is the only reliable signal that the recording is valid).
            if "/account/login" in page.url:
                raise RuntimeError(
                    "LOGIN FAILED — recorder is not authenticated (still on %s). "
                    "Check DEMO_USER/DEMO_PASS; aborting so we don't ship a login-only video."
                    % page.url
                )
            title("Enterprise Architect", "Know the estate &mdash; capabilities, applications, health", 2600)
            cap("Enterprise Architect", "Your workspace: portfolio health at a glance")
            wander([(420, 360), (900, 420), (1150, 360)])
            goto("/capability-map", 2400)
            cap("Enterprise Architect", "Business capabilities mapped across the enterprise")
            wander([(400, 400), (760, 500), (1050, 420)])
            goto("/architecture/dashboard", 2200)
            title("ArchiMate Modeler", "The full ArchiMate 3.2 model &mdash; every layer", 2600)
            cap("Modeler", "All elements across Strategy, Business, Application & Technology")
            wander([(440, 380), (820, 460), (1120, 400)])
            goto("/architecture/traceability", 2400)
            cap("Modeler", "Requirements traceability across the architecture")
            wander([(500, 400), (860, 480)])
            goto("/applications/", 1600)
            title("Application Portfolio Manager", "Rationalise the application landscape", 2600)
            cap("Portfolio Manager", "Every application &mdash; owner, lifecycle, business domain")
            wander([(600, 360), (900, 440)])
            goto("/applications/rationalization", 2200)
            cap("Portfolio Manager", "Find redundancy and retirement candidates (TIME / 6R)")
            wander([(520, 400), (880, 460)])
            goto("/duplicate-detection/simple", 2000)
            cap("Portfolio Manager", "Detect duplicate and overlapping applications")
            move(700, 420)
            goto("/solutions/", 1600)
            title("Solution Architect", "Design within governance &mdash; AI-assisted", 2600)
            cap("Solution Architect", "TOGAF solution design: drivers, goals, requirements")
            wander([(500, 360), (820, 420)])
            # Open the first solution dynamically (hard-coding an id breaks on a fresh DB).
            click_el("a[href*='/solutions/']:not([href$='/solutions/'])", 1800)
            cap("Solution Architect", "Drivers, goals and requirements in one place")
            hover_el("text=Requirements", 900)
            goto("/ai-chat", 2200)
            cap("Solution Architect", "AI architect, grounded in your portfolio data")
            wander([(640, 420), (720, 560)])
            goto("/archimate/composer", 2600)
            cap("Solution Architect", "Model the architecture in ArchiMate 3.2")
            wander([(500, 420), (820, 480), (1080, 420)])
            goto("/solutions/programmes", 2200)
            title("Transformation Programme Manager", "Run change as governed programmes", 2600)
            cap("Programme Manager", "Brownfield & greenfield transformation programmes")
            wander([(520, 380), (900, 460)])
            goto("/capability-roadmap", 2400)
            cap("Programme Manager", "Sequence capability uplift on a roadmap")
            wander([(480, 400), (880, 470)])
            goto("/applications/vendors", 2200)
            title("Procurement / Vendor Manager", "Manage the vendor landscape", 2500)
            cap("Procurement", "Vendor portfolio, contracts and concentration risk")
            wander([(560, 380), (900, 460)])
            goto("/arb/reviews", 1600)
            title("Architecture Review Board", "Govern every decision", 2500)
            cap("ARB / Governance", "Submit, review and approve &mdash; with a full audit trail")
            wander([(520, 400), (860, 460)])
            goto("/batch-import/", 2000)
            title("Data Steward", "Bring the estate in &mdash; and keep it clean", 2500)
            cap("Data Steward", "Bulk import applications, capabilities & ArchiMate via Excel/CSV")
            wander([(520, 400), (860, 460)])
            goto("/solutions/data-stewardship", 2200)
            cap("Data Steward", "Classify, baseline and steward architecture data")
            move(700, 430)
            goto("/admin/users", 1800)
            title("Platform Administrator", "Operate the platform", 2500)
            cap("Administrator", "Users, organizations and role-based access")
            wander([(520, 380), (900, 450)])
            goto("/admin/seed-management", 2200)
            cap("Administrator", "System configuration and reference-data seeding")
            move(700, 430)
            goto("/dashboard/health", 2200)
            cap("CTO / Executive", "Architecture & governance maturity, scored")
            wander([(480, 380), (900, 440), (1150, 400)])
            title("Self-host it.", "AGPL-3.0 &middot; github.com/Anioko/archie-ea", 3200)
            page.wait_for_timeout(600)
        finally:
            vid = page.video
            ctx.close()
            print("VIDEO_PATH:", vid.path())
            browser.close()


if __name__ == "__main__":
    main()
