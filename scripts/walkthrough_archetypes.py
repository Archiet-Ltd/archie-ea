#!/usr/bin/env python
"""Level 10 archetype walkthrough (docs/TESTING_STANDARD.md).

Levels 0-8 ask "does this break?". Level 9 asks "can this person do their
job?". This is Level 10: "is the journey worth completing?" -- the questions a
gate cannot answer, run against a real browser rather than reasoned about.

It drives a real Chromium at a FIXED 1440x900 viewport, signed in as a
NON-ADMIN user per archetype, and per the standard's own rules it:

  * measures geometry instead of photographing it (rule 3) -- a screenshot
    grows to fit its content and therefore cannot show dead space or overflow;
  * runs as a normal Architect role, never an admin (rule 4) -- an admin
    session satisfies every require_roles guard on the way through and hides
    the authorisation defects this level exists to find;
  * checks DISCOVERABILITY, not just reachability: a page the archetype can
    only reach by typing a URL is broken for real users however green the
    gates are. That is exactly what this harness found on its first run --
    the CTO was authorised to classify technology on the radar
    (/technology/radar/classify names "cto" in its own require_roles list) and
    no CTO sidebar zone linked the page.

It needs a running server and a seeded tenant, so it is not a verify.py gate.
Run it before a deploy, against the deployment you are about to ship:

    python scripts/walkthrough_archetypes.py            # default http://127.0.0.1:5001

Seed data expectations are in the PERSONAS table below; a step whose data is
absent reports the absence rather than passing quietly.
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("WALKTHROUGH_BASE_URL", "http://127.0.0.1:5001")
PASSWORD = os.environ.get("WALKTHROUGH_PASSWORD", "Walk!2026")
VIEWPORT = {"width": 1440, "height": 900}
# A phone, for the shell check below. The sidebar defect that made the app
# desktop-only lived entirely under 1024px, where nothing had ever looked.
MOBILE = {"width": 390, "height": 844}

findings = []


def note(persona, step, verdict, evidence):
    findings.append({"persona": persona, "step": step, "verdict": verdict,
                     "evidence": evidence})
    print("%-20s %-46s %-14s %s" % (persona, step, verdict, evidence), flush=True)


def sign_in(page, email):
    page.goto(BASE + "/account/login", wait_until="domcontentloaded")
    page.fill("input[name='email']", email)
    page.fill("input[name='password']", PASSWORD)
    page.click("button[type='submit']")
    page.wait_for_load_state("domcontentloaded")
    return "/login" not in page.url


def geometry(page):
    """Content height vs viewport, and horizontal overflow. Rule 3."""
    return page.evaluate("""() => {
        const d = document.documentElement, b = document.body;
        return {
            scrollH: Math.max(d.scrollHeight, b.scrollHeight),
            clientH: d.clientHeight,
            scrollW: Math.max(d.scrollWidth, b.scrollWidth),
            clientW: d.clientWidth,
        };
    }""")


def check_page(page, persona, label, path, must_contain=()):
    errors = []

    def handler(message):
        if message.type == "error":
            errors.append(message.text)

    page.on("console", handler)
    page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
    resp = page.goto(BASE + path, wait_until="networkidle")
    status = resp.status if resp else 0
    body = page.content()
    geo = geometry(page)

    problems = []
    if status >= 400:
        problems.append("HTTP %d" % status)
    for token in must_contain:
        if token not in body:
            problems.append("missing %r" % token)
    if geo["scrollW"] > geo["clientW"] + 1:
        problems.append("horizontal overflow %dpx > %dpx" % (geo["scrollW"], geo["clientW"]))
    if geo["scrollH"] > 12000:
        problems.append("page is %dpx tall" % geo["scrollH"])
    if errors:
        problems.append("console: %s" % "; ".join(errors[:3]))

    page.remove_listener("console", handler)
    verdict = "WORKS WELL" if not problems else "BROKEN" if status >= 400 else "WORKS BUT POOR"
    note(persona, label, verdict, "; ".join(problems) or "HTTP %d, %dpx tall" % (status, geo["scrollH"]))
    return not problems


def discoverable(page, persona, needle):
    """Rule: could the archetype find this without reading the source?"""
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    hit = any(needle in (h or "") for h in hrefs)
    note(persona, "discoverable: %s" % needle, "WORKS WELL" if hit else "WORKS BUT POOR",
         "%d nav links, %s" % (len(hrefs), "linked" if hit else "NOT linked from this page"))
    return hit



def cto_classifies_in_the_ui(page, persona):
    """Do the CTO's actual job through the actual controls, not the endpoint.

    A server-side journey test POSTs to /technology/radar/classify and asserts
    the response. It therefore cannot see a control a CSP has made inert, a
    form wired to the wrong action, or -- the defect this step found -- a
    successful submit that navigates the user to a raw JSON body and leaves
    them there with no way back but the browser's Back button.
    """
    page.goto(BASE + "/technology/radar/", wait_until="networkidle")
    # Prefer an unclassified element; fall back to reclassifying one that is
    # already on the radar. Both submit the same form to the same endpoint, and
    # a step that only works on a freshly seeded database is a step that passes
    # once and then reports a product problem that is really fixture exhaustion.
    testid = "radar-classify-"
    if page.locator("[data-testid^='radar-classify-']").count() == 0:
        testid = "radar-reclassify-"
    if page.locator("[data-testid^='%s']" % testid).count() == 0:
        note(persona, "classify a technology", "WORKS BUT POOR",
             "no technology-layer element on the radar to act on")
        return
    row = page.locator("form:has([data-testid^='%s'])" % testid).first
    ring = row.locator("select[name='ring']")
    if ring.count():
        ring.select_option("trial")
    row.locator("[data-testid^='%s']" % testid).click()
    page.wait_for_load_state("networkidle")

    landed = page.url.replace(BASE, "")
    body = page.content()
    on_the_radar = "/technology/radar/" in page.url and "<html" in body.lower()
    is_raw_json = body.strip().startswith("<html><head><meta") and '"success"' in body

    if not on_the_radar or is_raw_json:
        note(persona, "classify a technology", "BROKEN",
             "submit landed on %s, not back on the radar" % landed)
        return
    note(persona, "classify a technology", "WORKS WELL",
         "submitted and returned to %s" % landed)



def can_navigate_on_a_phone(browser, persona, email):
    """Below 1024px, can this archetype reach a second page at all?

    The QA audit of 30 Aug 2026 (High 4) found the sidebar toggle cancelling
    itself: the button sits OUTSIDE the sidebar, so the same click both opened it
    and reached the sidebar's own @click.away, which closed it in the same tick.
    Measured before the fix: store stayed false, sidebar transform stayed
    -256px, 0 of 25 nav links reachable. The application was desktop-only and
    nothing in the suite looked below 1024px.

    Asserts the drawer's geometry AND that links are actually reachable --
    a sidebar that is "open" but rendered off-screen is still unusable. Below 768px the
    library's sidebar is a drawer the header trigger opens.
    """
    ctx = browser.new_context(viewport=MOBILE)
    page = ctx.new_page()
    errors = []

    def on_console(message):
        if message.type == "error":
            errors.append(message.text)

    page.on("console", on_console)
    try:
        if not sign_in(page, email):
            note(persona, "phone: sign in", "BROKEN", "still on the login page at 390px")
            return
        page.goto(BASE + "/applications/", wait_until="networkidle")
        page.wait_for_timeout(600)

        toggle = page.locator("button[data-slot='sidebar-trigger']")
        if not toggle.count():
            note(persona, "phone: open the menu", "BROKEN",
                 "no sidebar trigger rendered below 768px")
            return
        toggle.first.click()
        page.wait_for_timeout(700)

        geometry = page.evaluate("""() => {
            const el = document.querySelector('[data-fb-sidebar-mobile] [data-slot="sheet-content"]');
            if (!el) return {found: false, reachable: 0, left: null};
            const r = el.getBoundingClientRect();
            const links = [...el.querySelectorAll('a[href]')].filter(a => {
                const b = a.getBoundingClientRect();
                return b.width > 0 && b.right > 0 && b.left < window.innerWidth;
            });
            return {found: true, left: Math.round(r.left), reachable: links.length};
        }""")

        problems = []
        if not geometry.get("found"):
            problems.append("no sidebar element")
        if geometry.get("left", -1) < 0:
            problems.append("sidebar still off-screen at %spx" % geometry.get("left"))
        if not geometry.get("reachable"):
            problems.append("0 navigation links reachable")
        if errors:
            problems.append("console: %s" % errors[0][:60])

        note(persona, "phone: open the menu",
             "WORKS WELL" if not problems else "BROKEN",
             "; ".join(problems) or "%d links reachable at 390px" % geometry["reachable"])
    finally:
        ctx.close()


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    for persona, email, steps in [
        ("cto", "cto@walkthrough.example.com", "radar"),
        ("enterprise_architect", "ea@walkthrough.example.com", "capabilities"),
        ("portfolio_manager", "pm@walkthrough.example.com", "rationalization"),
        ("arb_member", "arb@walkthrough.example.com", "arb"),
    ]:
        ctx = browser.new_context(viewport=VIEWPORT)
        page = ctx.new_page()
        if not sign_in(page, email):
            note(persona, "sign in", "BROKEN", "still on the login page")
            ctx.close()
            continue
        note(persona, "sign in", "WORKS WELL", "landed on %s" % page.url.replace(BASE, ""))

        check_page(page, persona, "landing page", "/")
        if steps == "radar":
            discoverable(page, persona, "/technology/radar")
            check_page(page, persona, "technology radar", "/technology/radar/",
                       must_contain=("Kafka 3.x",))
            cto_classifies_in_the_ui(page, persona)
        elif steps == "capabilities":
            discoverable(page, persona, "capability")
            check_page(page, persona, "capability map", "/capability-map/")
        elif steps == "rationalization":
            discoverable(page, persona, "rationalization")
            check_page(page, persona, "rationalization dashboard", "/applications/rationalization")
            check_page(page, persona, "planning for one app",
                       "/applications/rationalization/planning/1")
        elif steps == "arb":
            discoverable(page, persona, "/arb")
            check_page(page, persona, "ARB dashboard", "/arb/")
            check_page(page, persona, "ARB review queue", "/arb/reviews")
        ctx.close()

    # One archetype is enough to prove the shell navigates on a phone: the
    # sidebar is shared, and the defect was in the shell rather than any page.
    can_navigate_on_a_phone(browser, "enterprise_architect", "ea@walkthrough.example.com")
    browser.close()

json.dump(findings, open("walkthrough_report.json", "w"), indent=2)
bad = [f for f in findings if f["verdict"] != "WORKS WELL"]
print("\n%d step(s) not WORKS WELL out of %d" % (len(bad), len(findings)))
sys.exit(1 if bad else 0)
