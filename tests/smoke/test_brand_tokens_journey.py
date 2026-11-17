"""An enterprise architect sees one brand blue and visible control borders on
the dashboard, the chat and a form, in both themes at both widths, and the
logo is drawn in that same blue."""

import os

import pytest

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("SMOKE_WALK_DIR")

# Computed values of the decided tokens, as the browser reports them.
PRIMARY = {"light": "rgb(19, 95, 188)", "dark": "rgb(99, 159, 244)"}
INPUT_BORDER = {"light": "rgb(143, 143, 143)", "dark": "rgb(115, 115, 115)"}


def _role_of(email):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.user import User

        role = User.query.filter_by(email=email).one().enterprise_role
        db.session.remove()
        return role


def _theme(page, theme):
    page.evaluate(
        "t => document.documentElement.classList.toggle('dark', t === 'dark')", theme
    )
    page.wait_for_timeout(600)  # colours ease over 150ms; read them once settled


def _shot(page, name, width, theme):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, theme, width)))


def _style(page, selector, prop):
    return page.locator(selector).first.evaluate(
        "(el, p) => getComputedStyle(el)[p]", prop
    )


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_architect_sees_the_brand_blue_and_visible_control_borders(
    browser, live_server, seeded, width, theme
):
    email = seeded["emails"]["enterprise_architect"]
    assert _role_of(email) == "enterprise_architect"  # the persona, not an administrator

    context = browser.new_context(viewport={"width": width, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        _login(page, live_server, email)

        # Dashboard: the primary action is the brand blue.
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _theme(page, theme)
        assert page.locator(".bg-primary").count() > 0, "dashboard shows no primary-coloured control"
        assert _style(page, ".bg-primary", "backgroundColor") == PRIMARY[theme]
        _shot(page, "dashboard", width, theme)

        # Chat: the same blue, and the message box has a visible boundary.
        page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _theme(page, theme)
        assert _style(page, ".bg-primary", "backgroundColor") == PRIMARY[theme]
        box = page.locator("textarea:visible").first
        assert box.evaluate("el => getComputedStyle(el).borderTopColor") == INPUT_BORDER[theme]
        _shot(page, "chat", width, theme)

        # Form page: the new-application form's fields carry the control border.
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _theme(page, theme)
        page.get_by_test_id("btn-add-application").click()
        field = page.locator("#modal-create #ca-name")
        field.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert field.evaluate("el => getComputedStyle(el).borderTopColor") == INPUT_BORDER[theme]
        _shot(page, "form", width, theme)
    finally:
        context.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_logo_is_the_primary_blue_in_both_themes(browser, live_server, theme):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _theme(page, theme)
        logo = page.locator("svg[aria-label='Entelim'] .brand-logo-main").first
        fill = logo.evaluate("el => getComputedStyle(el).fill")
        assert fill == PRIMARY[theme]
        primary = page.evaluate(
            "() => { const d = document.createElement('i'); d.className = 'bg-primary';"
            "document.body.appendChild(d); const c = getComputedStyle(d).backgroundColor; d.remove(); return c; }"
        )
        assert fill == primary
    finally:
        page.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_login_panel_glows_are_the_primary_blue(browser, live_server, theme):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _theme(page, theme)
        r, g, b = [int(v) for v in PRIMARY[theme][4:-1].split(",")]
        want = "rgba(%d, %d, %d, 0.16)" % (r, g, b)
        for ring in ("tl", "br"):
            got = _style(page, ".login-brand__ring--" + ring, "backgroundColor")
            assert got == want, "%s glow is %s, expected %s" % (ring, got, want)

        _shot(page, "login", 1440, theme)
    finally:
        page.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_architect_skip_links_carry_the_primary_blue(browser, live_server, seeded, theme):
    email = seeded["emails"]["enterprise_architect"]
    assert _role_of(email) == "enterprise_architect"  # the persona, not an administrator

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _theme(page, theme)
        links = page.locator("a.skip-link")
        assert links.count() > 0, "the signed-in shell offers no skip link"
        for i in range(links.count()):
            assert links.nth(i).inner_text().strip() != ""
            assert links.nth(i).evaluate("el => getComputedStyle(el).backgroundColor") == PRIMARY[theme]
        links.first.focus()
        assert links.first.evaluate("el => getComputedStyle(el).top") != "-40px"
        _shot(page, "skip-link", 1440, theme)
    finally:
        context.close()
