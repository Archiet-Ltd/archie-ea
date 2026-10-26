#!/usr/bin/env python
"""Capture the proof screenshots the public site shows: home hero, Applications, ARB, role workspaces.

Why this exists (marketing visual design review v1, S3 / P-03 / P-06): nine of the
eleven revenue pages showed no product at all, and the two that did showed a
whole 1200x750 application window squeezed into a 720px column -- 60% scale,
7px text, four zero tiles. This script takes the replacement crops.

Rules it enforces, so the next re-shoot cannot quietly regress:

* the demonstration organisation is seeded first (``flask seed-demo-company``);
  a crop whose headline tiles read zero is refused, not shipped;
* every crop is a *region* of the real screen (no app sidebar), taken at 2x
  device pixel ratio and written with a CSS width equal to the region's own CSS
  width, so the template displays it at 1:1 and the UI's 12-14px text stays
  12-14px;
* a desktop crop and a mobile crop are taken from the product at its own
  viewport width (not shrunk), because a 1000px crop shrunk to a 390px phone is
  the original defect again.

Usage (the app must already be running against the seeded database):

    DEMO_USER_PASSWORD=... python scripts/capture_proof_assets.py \
        --base http://127.0.0.1:5100

The home page's product window (the whole /applications/ screen, sidebar and top bar
included) is written to app/static/img/home/product-portfolio*.webp.

Writes WebP files under app/static/img/ and prints their CSS width/height.
"""
from __future__ import annotations

import argparse
import io
import os
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from capture_screenshots import _dismiss_onboarding, _login  # noqa: E402

DEMO_EMAIL = "demo@lantern-quay.example.com"
IMG = REPO / "app" / "static" / "img"
DPR = 2
SUBJECT = "Calibration Ledger"
CHAT_SUBJECT = "Payments service"

# Role page slug -> the enterprise role whose workspace it shows (tests/smoke/conftest.py ARCHETYPES).
ROLE_PAGES = (
    ("enterprise-architects", "enterprise_architect"),
    ("cto-and-cio", "cto"),
    ("it-portfolio-managers", "portfolio_manager"),
    ("solution-architects", "solution_architect"),
    ("business-architects", "business_architect"),
    ("security-architects", "security_architect"),
    ("data-architects", "data_architect"),
)


def _webp(png: bytes, dest: pathlib.Path) -> tuple[int, int]:
    from PIL import Image

    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(png)) as im:
        im = im.convert("RGB")
        im.save(dest, "WEBP", quality=82, method=6)
        return im.size[0] // DPR, im.size[1] // DPR


def _tidy(page) -> None:
    """Hide floating furniture that is not part of the screen being shown."""
    page.evaluate(
        """() => {
          for (const e of document.querySelectorAll('body *')) {
            const s = getComputedStyle(e);
            if (s.position === 'fixed' && e.getBoundingClientRect().width < 80 &&
                e.getBoundingClientRect().top > window.innerHeight / 2) e.style.display = 'none';
          }
        }"""
    )


def _shoot(page, clip) -> bytes:
    page.evaluate("window.scrollTo(0, 0)")
    return page.screenshot(clip=clip, full_page=True)


def _card(page, text, last=False):
    """Bounding box of the rounded card that contains exactly this text."""
    node = page.get_by_text(text, exact=True)
    node = node.last if last else node.first
    box = node.locator("xpath=ancestor::*[contains(@class,'rounded')][1]").bounding_box()
    if box is None:
        raise SystemExit(f"card containing {text!r} not found on {page.url}")
    return box


def _box(page, selector, nth=0):
    box = page.locator(selector).nth(nth).bounding_box()
    if box is None:
        raise SystemExit(f"selector {selector!r} not found on {page.url}")
    return box


def _open_applications(browser, base, password, viewport):
    """A logged-in page on /applications/ with the onboarding prompt dismissed: (context, page)."""
    ctx = browser.new_context(viewport=viewport, device_scale_factor=DPR)
    page = ctx.new_page()
    _login(page, base, DEMO_EMAIL, password)
    page.goto(base + "/applications/", wait_until="networkidle")
    page.wait_for_timeout(1500)
    _dismiss_onboarding(page)
    return ctx, page


def _require_seeded_portfolio(page, label) -> None:
    """Stop when the demo portfolio is not seeded: empty vendor or capability columns make a poor capture."""
    text = page.inner_text("body")
    if re.search(r"(?<!\d)0 of \d+ applications in the portfolio have a vendor", text) or "Not mapped" in text:
        raise SystemExit(f"{label}: vendor or capability columns are empty; run seed-demo-company first")


def capture_applications(browser, base, password, wide: bool) -> tuple[int, int]:
    vw, vh = (1408, 1600) if wide else (390, 1400)
    ctx, page = _open_applications(browser, base, password, {"width": vw, "height": vh})
    _tidy(page)
    _require_seeded_portfolio(page, "applications")
    main = _box(page, "main")
    # A 1408px viewport leaves a 1104px content column: exactly the public page figure width
    # (72rem less padding), so the crop is shown 1:1 and never shrunk.
    pad = 24 if wide else 16
    if wide:
        h1 = _box(page, "h1")
        rows = page.locator("[data-testid^='app-row-']")
        last = _box(page, "[data-testid^='app-row-']", min(rows.count(), 6) - 1)
        top, bottom = h1["y"] - 16, last["y"] + last["height"] + 4
    else:
        tiles = _card(page, "Active Portfolio")
        banner = _card(page, "Data Quality")
        top, bottom = tiles["y"] - 8, banner["y"] + banner["height"] + 8
    clip = {"x": main["x"] + pad, "y": top, "width": main["width"] - 2 * pad, "height": bottom - top}
    name = "applications.webp" if wide else "applications-mobile.webp"
    size = _webp(_shoot(page, clip), IMG / "modules" / name)
    ctx.close()
    return size


def capture_arb(browser, base, password, wide: bool) -> tuple[int, int]:
    vw, vh = (1280, 1400) if wide else (390, 2000)
    ctx = browser.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=DPR)
    page = ctx.new_page()
    _login(page, base, DEMO_EMAIL, password)
    page.goto(base + "/arb/", wait_until="networkidle")
    page.wait_for_timeout(2500)
    _dismiss_onboarding(page)
    _tidy(page)
    text = page.inner_text("body")
    if re.search(r"Cycle time\s*—", text):
        raise SystemExit("arb: cycle time reads an em dash; run seed-demo-company first")
    main = _box(page, "main")
    if wide:
        h1 = _box(page, "h1")
        chart = _card(page, "Review status")
        x = h1["x"]
        width = (main["x"] + main["width"]) - x - 24
        top, bottom = h1["y"] - 16, chart["y"] + chart["height"] + 4
    else:
        first = _card(page, "Total reviews")
        last = _card(page, "Cycle time")
        x = first["x"] - 4
        width = first["width"] + 8
        top, bottom = first["y"] - 8, last["y"] + last["height"] + 8
    clip = {"x": x, "y": top, "width": width, "height": bottom - top}
    name = "arb.webp" if wide else "arb-mobile.webp"
    size = _webp(_shoot(page, clip), IMG / "modules" / name)
    ctx.close()
    return size


def capture_home_chat(browser, base, password, wide: bool) -> tuple[int, int]:
    """The AI chat answering "what breaks if ... fails": the question, then a card per connection
    with where it was read from. The first screen of the public home shows this crop, so it is kept
    to the question and the first cards (two on desktop, one on a phone).
    """
    vw, vh = (1180, 1400) if wide else (390, 1400)
    cards_shown = 2 if wide else 1
    ctx = browser.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=DPR)
    page = ctx.new_page()
    try:
        _login(page, base, role_email("startup_cto_chat"), password)
        page.goto(base + "/dashboard/overview", wait_until="networkidle")
        _dismiss_onboarding(page)
        if page.get_by_test_id("first-action-sample").count():
            page.get_by_test_id("first-action-sample").click()
        page.wait_for_selector('[data-testid="sample-company-indicator"]', timeout=90000)
        page.goto(base + "/ai-chat", wait_until="networkidle")
        _dismiss_onboarding(page)
        page.locator("#user-input").fill("What breaks if %s fails" % CHAT_SUBJECT)
        page.locator("#user-input").press("Enter")
        try:
            page.wait_for_selector('[data-testid="estate-answer"] [data-answer-row]', timeout=30000)
        except Exception:
            raise SystemExit("the chat gave no cited answer: run seed-demo-company first")
        page.wait_for_timeout(1000)
        _tidy(page)
        rows = page.locator('[data-testid="estate-answer"] [data-answer-row]')
        if rows.count() < cards_shown:
            raise SystemExit("the chat answer has too few cards to photograph: run seed-demo-company first")
        for i in range(cards_shown):
            source = rows.nth(i).locator("xpath=ancestor::*[@data-slot='card'][1]").locator("[data-citation]").inner_text()
            if not source.strip() or "not recorded" in source:
                raise SystemExit("a card in the chat answer has no recorded source: refusing to photograph it")
        page.evaluate("document.getElementById('messages-container').scrollTop = 0")
        page.wait_for_timeout(300)
        pane = _box(page, "#messages-container")
        last = rows.nth(cards_shown - 1).locator("xpath=ancestor::*[@data-slot='card'][1]").bounding_box()
        clip = {"x": pane["x"], "y": pane["y"], "width": pane["width"], "height": last["y"] + last["height"] + 12 - pane["y"]}
        if clip["height"] <= 0 or clip["y"] + clip["height"] > vh:
            raise SystemExit("the chat crop does not fit the viewport: %s" % clip)
        name = "chat-answer.webp" if wide else "chat-answer-mobile.webp"
        png = page.screenshot(clip=clip)
        return _webp(png, IMG / "home" / name)
    finally:
        ctx.close()


def capture_home_overview(browser, base, password, wide: bool) -> tuple[int, int]:
    """The Overview of the sample company, as a startup CTO sees it right after loading the sample.

    The first screen of the public home shows this screen. A capture whose page carries an error
    or that has no figures is refused; the capture date is the file's own date.
    """
    vw, vh = (1280, 800) if wide else (390, 844)
    ctx = browser.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=DPR)
    page = ctx.new_page()
    try:
        _login(page, base, role_email("startup_cto_chat"), password)
        page.goto(base + "/ai-chat", wait_until="networkidle")
        _dismiss_onboarding(page)
        try:
            page.get_by_test_id("chat-first-sample").first.wait_for(state="attached", timeout=15000)
            page.get_by_test_id("chat-first-sample").first.evaluate("el => el.closest('form').submit()")
        except Exception:
            pass  # the sample company is already loaded in this organisation
        page.wait_for_selector('[data-testid="sample-company-indicator"]', timeout=90000)
        page.goto(base + "/dashboard/overview", wait_until="networkidle")
        _dismiss_onboarding(page)
        page.wait_for_timeout(1500)
        _tidy(page)
        text = " ".join(page.inner_text("body").split())
        if re.search(r"Traceback|Internal Server Error|Something went wrong", text):
            raise SystemExit("the overview shows an error: refusing to photograph it")
        if not re.search(r"\d", text):
            raise SystemExit("the overview shows no figures: run the sample company first")
        name = "overview.webp" if wide else "overview-mobile.webp"
        png = page.screenshot(clip={"x": 0, "y": 0, "width": vw, "height": vh})
        return _webp(png, IMG / "home" / name)
    finally:
        ctx.close()


def capture_home_product(browser, base, password, wide: bool) -> tuple[int, int]:
    """The product window under the home hero: the real /applications/ screen.

    Desktop: the whole 1280x800 viewport (sidebar, top bar, content).
    Desktop: scrolled so the application rows show under the top bar.
    Phone: the 358px content column from the page heading down to the last whole
    block (tiles, banner, cards) that ends within 780 CSS px of crop height.
    """
    vw, vh = (1280, 800) if wide else (390, 844)
    ctx, page = _open_applications(browser, base, password, {"width": vw, "height": vh})
    if wide:
        aside = page.locator("[data-testid='sidebar']")
        if aside.bounding_box()["width"] > 200:
            page.locator("button.sidebar-collapse-btn").click()
            page.wait_for_timeout(600)
    _tidy(page)
    # The build stamp line in the sidebar footer is not part of the product shown.
    page.evaluate(
        """() => document.querySelectorAll("[data-testid='sidebar'] p[title^='Build identifier']")
                 .forEach((e) => { e.style.display = 'none'; })"""
    )
    _require_seeded_portfolio(page, "home product")
    for label in ("Active Portfolio", "Strategic", "Tactical", "Sunset/Decom Pipeline", "Decommissioned"):
        tile = page.get_by_text(label, exact=True).first.locator(
            "xpath=ancestor::*[contains(@class,'rounded')][1]"
        ).inner_text()
        figures = re.findall(r"(?<![\d.])\d[\d,]*(?![\d.])", tile)
        if not figures or int(figures[0].replace(",", "")) == 0:
            raise SystemExit(f"home product: headline tile {label!r} reads zero or empty; run seed-demo-company first")
    if wide:
        # The tiles, banner and filters fill the first 800px; scroll the content the way a
        # user would until the list card sits under the top bar, so the rows show.
        page.evaluate(
            """() => {
              const card = document.querySelector("[data-testid='filter-bar']").parentElement;
              let el = card.parentElement;
              while (el && !/(auto|scroll)/.test(getComputedStyle(el).overflowY)) el = el.parentElement;
              (el || document.scrollingElement).scrollTop += card.getBoundingClientRect().top - 105;
            }"""
        )
        page.wait_for_timeout(500)
        rows = page.locator("[data-testid^='app-row-']")
        visible = sum(1 for i in range(rows.count()) if (b := rows.nth(i).bounding_box()) and b["y"] >= 0 and b["y"] + b["height"] <= vh)
        if visible < 5:
            raise SystemExit(f"home product: only {visible} application rows visible in the window")
        png = page.screenshot(clip={"x": 0, "y": 0, "width": vw, "height": vh})
    else:
        page.evaluate("window.scrollTo(0, 0)")
        main = _box(page, "main")
        top = _box(page, "h1")["y"] - 8
        # Whole blocks only: the tiles, the data-quality banner, then the cards if they fit.
        bottom = top
        for sel in ("[role='region'][aria-label='Portfolio statistics'] > *", "[data-testid^='mobile-card-']"):
            for i in range(page.locator(sel).count()):
                box = page.locator(sel).nth(i).bounding_box()
                if box and box["height"] > 0 and box["y"] + box["height"] + 8 <= vh and box["y"] + box["height"] + 8 - top <= 780:
                    bottom = max(bottom, box["y"] + box["height"] + 8)
        if bottom - top < 400:
            raise SystemExit("home product: phone crop holds no whole tiles block")
        png = _shoot(page, {"x": main["x"] + 16, "y": top, "width": 358, "height": bottom - top})
    name = "product-portfolio.webp" if wide else "product-portfolio-mobile.webp"
    size = _webp(png, IMG / "home" / name)
    ctx.close()
    return size

def role_email(archetype: str) -> str:
    return "smoke.%s.proof@example.com" % archetype.replace("_", "-")


def seed_role_users(password: str) -> None:
    """Give the demonstration organisation one user per role page (idempotent).

    Same shape as the smoke harness's archetype users (tests/smoke/conftest.py), placed on the
    Lantern Quay organisation so each role signs in to the data the other captures photograph.
    """
    from werkzeug.security import generate_password_hash

    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app()
    with app.app_context():
        org = Organization.query.filter_by(name="Lantern Quay Systems").first()
        if org is None:
            raise SystemExit("roles: demonstration organisation missing; run seed-demo-company first")
        Role.insert_roles()
        role = Role.query.filter_by(name="Viewer").first()
        for _, archetype in ROLE_PAGES:
            if User.query.filter_by(email=role_email(archetype)).first():
                continue
            db.session.add(User(
                email=role_email(archetype), first_name="Proof", last_name=archetype[:12],
                organization_id=org.id, enterprise_role=archetype, confirmed=True, role=role,
                password_hash=generate_password_hash(password),
            ))
        db.session.commit()


def seed_import_user(password: str) -> str:
    """One CTO on the demonstration organisation who may write, since the import is a write (idempotent)."""
    from werkzeug.security import generate_password_hash

    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app()
    with app.app_context():
        org = Organization.query.filter_by(name="Lantern Quay Systems").first()
        if org is None:
            raise SystemExit("import dialog: demonstration organisation missing; run seed-demo-company first")
        Role.insert_roles()
        email = role_email("cto_importer")
        if not User.query.filter_by(email=email).first():
            db.session.add(User(
                email=email, first_name="Proof", last_name="CTO", organization_id=org.id,
                enterprise_role="cto", confirmed=True, role=Role.query.filter_by(name="Architect").first(),
                password_hash=generate_password_hash(password),
            ))
            db.session.commit()
        return email


def seed_chat_user(password: str) -> str:
    """A startup CTO in a fresh, empty organisation who loads the sample company, as a visitor would (idempotent)."""
    import uuid

    from werkzeug.security import generate_password_hash

    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.services.billing_plans import set_contract_plan

    app = create_app()
    with app.app_context():
        email = role_email("startup_cto_chat")
        if User.query.filter_by(email=email).first():
            return email
        Role.insert_roles()
        suffix = uuid.uuid4().hex[:8]
        org = Organization(name="Startup %s" % suffix, slug="startup-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        db.session.add(User(
            email=email, first_name="Startup", last_name="CTO", organization_id=org.id,
            enterprise_role="enterprise_architect", confirmed=True, role=Role.query.filter_by(name="Architect").first(),
            password_hash=generate_password_hash(password),
        ))
        db.session.commit()
        return email


def _role_screen_path(slug: str) -> str:
    """The screen ROLE_CAPTURES registers for this role page (Ask for the first four)."""
    from app.services.public_pages import ROLE_CAPTURES

    return next(path for entry_slug, path, *_ in ROLE_CAPTURES if entry_slug == slug)


def capture_role_screen(browser, base, password, slug: str, archetype: str, path: str, wide: bool) -> tuple[bool, str]:
    """The top of the role's own primary screen, as that role sees it.

    Refuses an error response, an error banner or an empty screen, so a role page
    never ships a picture of a broken or empty state.
    """
    vw, vh = (1408, 1600) if wide else (390, 1400)
    ctx = browser.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=DPR)
    page = ctx.new_page()
    try:
        _login(page, base, role_email(archetype), password)
        response = page.goto(base + path, wait_until="networkidle")
        page.wait_for_timeout(1500)
        _dismiss_onboarding(page)
        if response is None or response.status != 200:
            return False, "%s answered HTTP %s for %s" % (path, response.status if response else "none", archetype)
        text = page.inner_text("main")
        for marker in ("could not load", "Something went wrong", "An internal error", "No capabilities yet"):
            if marker.lower() in text.lower():
                return False, "%s shows %r for %s" % (path, marker, archetype)
        _tidy(page)
        page.evaluate("window.scrollTo(0, 0)")
        h1 = _box(page, "h1")
        main = _box(page, "main")
        pad = 24 if wide else 16
        top = h1["y"] - 16
        height = 760 if wide else 1000
        clip = {"x": main["x"] + pad, "y": top, "width": main["width"] - 2 * pad, "height": height}
        name = "%s.webp" % slug if wide else "%s-mobile.webp" % slug
        _webp(_shoot(page, clip), IMG / "roles" / name)
        return True, "captured"
    finally:
        ctx.close()


def capture_role(browser, base, password, slug: str, archetype: str, wide: bool = True) -> tuple[bool, str]:
    """The role's workspace: Ask open on the impact question with one answer shown.

    Returns (captured, reason). An empty state or a 403 is reported and nothing is written, so the
    page stays held rather than shipping a bad image.
    """
    # 1408px leaves a 1104px crop, the public figure column, so the desktop crop is shown 1:1 (as capture_applications).
    path = _role_screen_path(slug)
    if path != "/intelligence/ask":
        return capture_role_screen(browser, base, password, slug, archetype, path, wide)
    vw, vh = (1408, 1600) if wide else (390, 1400)
    ctx = browser.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=DPR)
    page = ctx.new_page()
    try:
        _login(page, base, role_email(archetype), password)
        response = page.goto(base + "/intelligence/ask", wait_until="networkidle")
        page.wait_for_timeout(1500)
        _dismiss_onboarding(page)
        if response is None or response.status != 200:
            return False, "Ask answered HTTP %s for %s" % (response.status if response else "none", archetype)
        page.click("#ask-question-impact")
        page.fill("#ask-picker-input", SUBJECT)
        page.wait_for_selector("#ask-picker-listbox [role=option]", timeout=20000)
        page.locator("#ask-picker-listbox [role=option]").first.click()
        try:
            page.wait_for_selector("[data-ask-row]", timeout=30000)
        except Exception:
            return False, "no answer rows appeared for %s (empty state)" % archetype
        button = page.get_by_role("button", name="Work them out now")
        if button.count():
            # A 409 (busy) or 500 leaves the earlier rows on screen, so the row selector alone
            # cannot tell a refreshed answer from a failed recalculation: require the request to succeed.
            try:
                with page.expect_response(lambda r: "/derivation/recompute" in r.url, timeout=60000) as sent:
                    button.click()
            except Exception:
                return False, "recalculation did not complete for %s" % archetype
            if not sent.value.ok:
                return False, "recalculation answered HTTP %s for %s" % (sent.value.status, archetype)
            page.wait_for_load_state("networkidle")
            if page.locator("[data-recompute-button]").count() and page.locator("[data-recompute-button]").first.is_visible():
                return False, "recalculation notice still shown for %s" % archetype
        page.wait_for_selector("[data-ask-row]", timeout=30000)
        page.wait_for_timeout(1000)
        _tidy(page)
        # Ask scrolls to its results; measure from the top, the same position _shoot() clips from.
        page.evaluate("window.scrollTo(0, 0)")
        h1 = _box(page, "h1")
        main = _box(page, "main")
        row = _box(page, "[data-ask-row]", 0)
        pad = 24 if wide else 16
        clip = {
            "x": main["x"] + pad, "y": h1["y"] - 16, "width": main["width"] - 2 * pad,
            "height": row["y"] + row["height"] + 12 - (h1["y"] - 16),
        }
        name = "%s.webp" % slug if wide else "%s-mobile.webp" % slug
        _webp(_shoot(page, clip), IMG / "roles" / name)
        return True, "captured"
    finally:
        ctx.close()


def capture_import_dialog(browser, base, password, wide: bool) -> tuple[bool, str]:
    """The Import Applications dialog with a small owner spreadsheet chosen and its first rows previewed.

    Taken at the width it is shown at (1408 wide for the desktop crop, 390 for the phone crop), so the
    phone image is a real phone capture and not the desktop image scaled down. The dialog markup is
    read from the served page and placed inside the portfolio's own scope before it is opened.
    """
    import tempfile

    vw, vh = (1408, 1600) if wide else (390, 1400)
    ctx = browser.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=DPR)
    page = ctx.new_page()
    try:
        _login(page, base, role_email("cto_importer"), password)
        response = page.goto(base + "/applications/", wait_until="networkidle")
        if response is None or response.status != 200:
            return False, "applications answered HTTP %s" % (response.status if response else "none")
        _dismiss_onboarding(page)
        html = page.request.get(base + "/applications/").text()
        page.evaluate(
            """(html) => {
              const doc = new DOMParser().parseFromString(html, 'text/html');
              const modal = doc.getElementById('application-import-modal');
              document.getElementById('app-portfolio-root').appendChild(document.importNode(modal, true));
            }""",
            html,
        )
        page.wait_for_timeout(500)
        with tempfile.TemporaryDirectory() as tmp:
            sheet = pathlib.Path(tmp) / "applications.csv"
            sheet.write_text(
                "Name,Application Owner,Description\n"
                "Customer Billing,Maya Okoye,Invoices and renewals for paying customers\n"
                "Support Desk,Tomas Brandt,Ticketing and customer messages\n"
                "Data Pipeline,Priya Nair,Nightly ingest into the warehouse\n"
                "Status Monitor,Tomas Brandt,Uptime and alerting for production\n"
                "Deploy Console,Sam Whitaker,Release tooling for engineering\n"
                "People Records,Maya Okoye,Hiring and employee records\n"
            )
            page.evaluate(
                """() => { const m = document.getElementById('application-import-modal');
                           m.removeAttribute('hidden'); m.classList.add('flex'); }"""
            )
            page.wait_for_selector("#application-import-modal", state="visible")
            page.locator("#application-import-modal input[type=file]").first.set_input_files(str(sheet))
            try:
                page.wait_for_selector("#excel-preview-table tbody tr", timeout=20000)
            except Exception:
                return False, "no preview rows appeared"
        page.wait_for_timeout(800)
        panel = page.locator("#application-import-modal [data-modal-panel]")
        box = panel.bounding_box()
        if box is None:
            return False, "the dialog is not visible"
        name = "scale-up-cto-and-vp-engineering.webp" if wide else "scale-up-cto-and-vp-engineering-mobile.webp"
        png = page.screenshot(clip={"x": box["x"], "y": box["y"], "width": box["width"], "height": min(box["height"], 1000 if not wide else 760)})
        _webp(png, IMG / "roles" / name)
        return True, "captured"
    finally:
        ctx.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5100")
    parser.add_argument("--roles", action="store_true",
                        help="capture only the four role-page workspaces (seeds their users first)")
    parser.add_argument("--import-dialog", action="store_true",
                        help="capture only the Import Applications dialog for the scale-up CTO page")
    parser.add_argument("--home-chat", action="store_true",
                        help="capture only the AI chat answer shown in the home hero")
    parser.add_argument("--home-overview", action="store_true",
                        help="capture only the Overview of the sample company shown in the home hero")
    parser.add_argument("--chromium", default=os.environ.get("PROOF_CHROMIUM"))
    args = parser.parse_args()
    password = os.environ.get("DEMO_USER_PASSWORD")
    if not password:
        raise SystemExit("DEMO_USER_PASSWORD is not set")

    from playwright.sync_api import sync_playwright

    if args.roles:
        seed_role_users(password)
    if args.home_chat or args.home_overview:
        seed_chat_user(password)
    if args.import_dialog:
        seed_import_user(password)

    with sync_playwright() as pw:
        launch = {"executable_path": args.chromium, "args": ["--no-sandbox"]} if args.chromium else {}
        browser = pw.chromium.launch(**launch)
        try:
            if args.home_chat:
                for wide in (True, False):
                    w, h = capture_home_chat(browser, args.base, password, wide)
                    print(f"home chat answer     {'desktop' if wide else 'mobile ':8s} {w}x{h} css px")
                return 0
            if args.home_overview:
                for wide in (True, False):
                    w, h = capture_home_overview(browser, args.base, password, wide)
                    print(f"home overview        {'desktop' if wide else 'mobile ':8s} {w}x{h} css px")
                return 0
            if args.import_dialog:
                for wide in (True, False):
                    ok, reason = capture_import_dialog(browser, args.base, password, wide)
                    print(f"import dialog {'desktop' if wide else 'mobile '} {'CAPTURED' if ok else 'REFUSED '} {reason}")
                return 0
            if args.roles:
                for slug, archetype in ROLE_PAGES:
                    for wide in (True, False):
                        ok, reason = capture_role(browser, args.base, password, slug, archetype, wide)
                        print(f"{slug:24s} {'desktop' if wide else 'mobile '} {'CAPTURED' if ok else 'REFUSED '} {reason}")
                return 0
            for label, fn in (
                ("home chat answer", capture_home_chat),
                ("home product window", capture_home_product),
                ("applications", capture_applications),
                ("arb", capture_arb),
            ):
                for wide in (True, False):
                    w, h = fn(browser, args.base, password, wide)
                    print(f"{label:20s} {'desktop' if wide else 'mobile ':8s} {w}x{h} css px")
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
