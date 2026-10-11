"""Global header cleanup + duplicate login confirmation (shell-overhaul Wave 1, Task 4).

Two defects found by design review:

1. The global header (`components/admin_header.html`, on every authenticated
   page via `layouts/admin_base.html`) rendered an LLM provider selector
   (`components/llm_selector.html`, id ``llm-selector-navbar``) whose options
   ("OpenAI (GPT-4, GPT-3.5)", "DeepSeek", ...) come from
   `/api/v1/llm/available` — a list of *configured* providers that has nothing
   to do with what a page like `/dashboard/overview` is showing. Model choice
   belongs inside the AI Chat surface's own Settings, where it already lives
   (`app/templates/ai_chat/index.html`'s `#model-selector`, populated by
   `ArchieChat.transport.loadModels()` in `app/static/js/ai_chat/app.js`) —
   the fix is simply to stop rendering the selector in the global header.

2. Logging in flashes ``"You are now logged in. Welcome back!"`` with category
   ``"success"`` (`app/modules/account/routes/account_routes.py`). The landing
   page after login, `layouts/admin_base.html`, included BOTH
   `partials/_flashes.html` (an inline banner reading
   ``get_flashed_messages()``) AND `components/toast-container.html` (a script
   that also reads ``get_flashed_messages(with_categories=True)`` and raises a
   `Platform.toast`) — the same flash rendered twice, once as a banner and
   once as a toast. The fix keeps the toast (per DESIGN.md: user-facing
   notifications go through `Platform.toast`) and removes the inline banner
   include from `admin_base.html`.

Follows the ``client.get(...)`` + session-login pattern already proven in
``tests/test_sidebar_render.py`` / ``tests/test_remaining_500_routes.py``.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _login(client, user_id):
    """Standard Flask-Login test-client pattern (see test_sidebar_render.py)."""
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _make_user(db_session, make_org, label, password=None):
    from app.models.user import User
    from tests.smoke.conftest import PASSWORD

    if password is None:
        password = PASSWORD

    org = make_org(f"header-{label}")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"header-{label}-{suffix}@example.com",
        first_name="Header",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    user.password = password
    db_session.add(user)
    db_session.flush()
    return user, password


def test_header_role_badge_shows_the_persona_not_admin_or_user(app, db_session, make_org):
    """E2E-7: the header's role badge read current_user.role.name -- the
    legacy Flask-Base Role/Permission table, which has only a couple of
    rows -- so all 11 enterprise_role personas collapsed onto whatever text
    that table happened to hold, usually just "Admin" or "User". It must
    show the real persona instead (see get_role_display_name /
    ROLE_DISPLAY_NAMES, the vocabulary the sidebar itself already uses)."""
    from app.models.user import User

    org = make_org("header-role-badge")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"header-cto-{suffix}@example.com",
        first_name="Badge", last_name="Tester",
        organization_id=org.id, confirmed=True,
        enterprise_role="cto",
    )
    user.password = "Sup3rSecret!23"
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    _login(client, user.id)

    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)
    assert 'id="user-role-badge"' in html
    badge_start = html.index('id="user-role-badge"')
    badge_fragment = html[badge_start:badge_start + 400]
    assert "CTO" in badge_fragment, (
        "role badge did not render the cto persona's display name — "
        f"fragment: {badge_fragment!r}"
    )


def test_dashboard_header_has_no_model_provider_selector(app, db_session, make_org):
    """The global header must not surface LLM provider choice — that belongs
    in AI Chat's own Settings, not on every page in the app."""
    user, _ = _make_user(db_session, make_org, "dash")
    client = app.test_client()
    _login(client, user.id)

    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)

    assert "GPT-4" not in html, "global header still leaks LLM provider names"
    assert "GPT-3.5" not in html, "global header still leaks LLM provider names"
    assert "llm-selector-navbar" not in html, (
        "global header still renders the LLM provider-selector component "
        "(components/llm_selector.html, selector_id='llm-selector-navbar')"
    )


def test_admin_page_has_exactly_one_mobile_sidebar_trigger(app, db_session, make_org):
    """ARCH-103: layouts/admin_base.html renders a "Global mobile sidebar
    opener" button (x-show gated on the sidebar being closed) AND
    components/admin_header.html used to render its own always-visible
    ``lg:hidden`` hamburger toggling the same ``$store.sidebar.open`` — both
    included on every admin page, so two triggers could render at once on
    mobile. Only one trigger (the global one, which is x-show/x-cloak gated)
    should remain."""
    user, _ = _make_user(db_session, make_org, "hamburger")
    client = app.test_client()
    _login(client, user.id)

    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)

    assert html.count("$store.sidebar.open = true") == 1, (
        "expected exactly one mobile sidebar-open trigger; found "
        f"{html.count('$store.sidebar.open = true')} — a duplicate hamburger "
        "would render alongside the global one from admin_base.html"
    )
    assert "aria-label=\"Open sidebar\"" in html


def test_login_success_message_appears_at_most_once(app, db_session, make_org):
    """A single sign-in must produce a single success confirmation, not an
    inline banner AND a toast both saying the same thing."""
    user, password = _make_user(db_session, make_org, "login")
    client = app.test_client()

    resp = client.post(
        "/account/login",
        data={"email": user.email, "password": password, "submit": "Log in"},
        follow_redirects=True,
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)

    assert html.count("You are now logged in. Welcome back!") <= 1, (
        "login success message rendered more than once — an inline banner "
        "and a toast both show the same flash"
    )


def test_public_page_flash_appears_at_most_once(app, db_session, make_org):
    """Shell-overhaul Wave 2, Task 5: layouts/public_base.html had the same
    dual-flash defect Wave 1 Task 4 fixed on admin_base.html -- it included
    BOTH partials/_flashes.html (an inline banner) AND
    components/toast-container.html (a Platform.toast), both reading the
    same flashed-messages queue. /account/logout flashes "You have been
    logged out." and redirects to main.index, which an unauthenticated
    visitor renders via layouts/public_base.html."""
    user, password = _make_user(db_session, make_org, "logout")
    client = app.test_client()

    login_resp = client.post(
        "/account/login",
        data={"email": user.email, "password": password, "submit": "Log in"},
        follow_redirects=True,
    )
    assert login_resp.status_code == 200, login_resp.get_data(as_text=True)[:2000]

    resp = client.get("/account/logout", follow_redirects=True)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)

    assert html.count("You have been logged out.") <= 1, (
        "logout confirmation rendered more than once on a public page -- "
        "an inline banner and a toast both show the same flash"
    )


def test_ai_chat_settings_still_exposes_model_choice(app, db_session, make_org):
    """Model choice must still live somewhere reachable — AI Chat's Settings
    dropdown, driven by the existing provider registry."""
    user, _ = _make_user(db_session, make_org, "chat")
    client = app.test_client()
    _login(client, user.id)

    resp = client.get("/ai-chat")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)

    assert 'id="model-selector"' in html, (
        "AI Chat Settings no longer exposes a model selector"
    )


def test_ctrl_b_hint_matches_wired_handler(app, db_session, make_org):
    """Wave 1 final review, IMPORTANT 2: the sidebar footer's collapse button
    advertises "Collapse sidebar (Ctrl+B)" (components/admin_sidebar.html).

    layouts/admin_base.html already bound the key
    (`@keydown.ctrl.b.window.prevent="$store.sidebar.toggle()"`, next to the
    root `x-data` at the top of the layout) — that half was NOT broken,
    contrary to the initial finding. The actual gap was
    layouts/composer_base.html, included by every full-bleed tool page
    (ArchiMate Composer, the codegen workflow designer): it renders the same
    admin_sidebar.html footer and the same admin_header.html collapse
    button (both call `$store.sidebar.toggle()`), but its own
    `Alpine.store('sidebar', { open: false })` had no `collapsed` field and
    no `toggle()` method at all — clicking the button there would throw
    "toggle is not a function", and no Ctrl+B binding existed on that
    layout either. Fixed by mirroring admin_base.html's store definition
    (same `archie_sidebar_collapsed` localStorage key) and adding the same
    `@keydown.ctrl.b.window.prevent` directive.

    Pin both layouts: the kbd hint text is present, the store exposes
    `collapsed`/`toggle()`, and the keydown directive exists — on both
    admin_base.html-rendered pages and composer_base.html-rendered pages.
    """
    user, _ = _make_user(db_session, make_org, "ctrlb")
    client = app.test_client()
    _login(client, user.id)

    def _assert_ctrl_b_wired(html, page_label):
        assert "Collapse sidebar (Ctrl+B)" in html or "Expand sidebar (Ctrl+B)" in html, (
            f"{page_label}: sidebar footer/header no longer advertises the Ctrl+B hint"
        )
        assert "$store.sidebar.toggle()" in html, (
            f"{page_label}: expected the sidebar collapse button(s) to call "
            "$store.sidebar.toggle()"
        )
        assert "@keydown.ctrl.b.window.prevent=\"$store.sidebar.toggle()\"" in html, (
            f"{page_label}: no @keydown.ctrl.b binding drives $store.sidebar.toggle() "
            "— the Ctrl+B hint is unwired on this layout"
        )
        assert "collapsed:" in html and "toggle()" in html, (
            f"{page_label}: $store.sidebar must define collapsed/toggle() itself, "
            "not just be toggled by callers"
        )

    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    _assert_ctrl_b_wired(resp.get_data(as_text=True), "admin_base.html (/dashboard/overview)")

    resp = client.get("/archimate/composer")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    _assert_ctrl_b_wired(resp.get_data(as_text=True), "composer_base.html (/archimate/composer)")


# ── T-UI-2: search palette + dark theme ──────────────────────────────

# test_search_trigger_does_not_pass_event_as_query (removed): the string-presence
# version is superseded by the Playwright smoke test of the same name in
# tests/smoke/test_header_search_and_theme.py, which verifies runtime behaviour
# by clicking the trigger and asserting the input value is empty.


def test_theme_init_script_appears_once_in_head(app, db_session, make_org):
    """The pre-paint theme script must appear exactly once, in _head.html,
    not duplicated in composer_base.html."""
    user, _ = _make_user(db_session, make_org, "theme-once")
    client = app.test_client()
    _login(client, user.id)

    # admin_base page
    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)
    theme_count = html.count("localStorage.getItem('theme')")
    assert theme_count == 1, (
        f"admin_base page: expected 1 localStorage.getItem('theme'), "
        f"found {theme_count}"
    )

    # composer_base page
    resp = client.get("/archimate/composer")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)
    theme_count = html.count("localStorage.getItem('theme')")
    assert theme_count == 1, (
        f"composer_base page: expected 1 localStorage.getItem('theme'), "
        f"found {theme_count}"
    )


def test_theme_store_is_registered(app, db_session, make_org):
    """Alpine.store('theme') must be registered with dark/toggle on both layouts."""
    user, _ = _make_user(db_session, make_org, "theme-store")
    client = app.test_client()
    _login(client, user.id)

    for url, label in [("/dashboard/overview", "admin_base"), ("/archimate/composer", "composer_base")]:
        resp = client.get(url)
        assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
        html = resp.get_data(as_text=True)
        assert "Alpine.store('theme'" in html, (
            f"{label}: Alpine.store('theme') not found in rendered output"
        )
        assert "classList.contains('dark')" in html, (
            f"{label}: theme store missing dark initializer"
        )
        assert "classList.toggle('dark'" in html, (
            f"{label}: theme store missing toggle implementation"
        )


def test_dark_theme_switch_in_user_menu(app, db_session, make_org):
    """The user menu must contain a 'Dark theme' menuitem with a switch bound
    to $store.theme.dark, keyboard operable."""
    user, _ = _make_user(db_session, make_org, "dark-switch")
    client = app.test_client()
    _login(client, user.id)

    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)

    assert "Dark theme" in html, (
        "user menu missing 'Dark theme' label"
    )
    assert '$store.theme.toggle()' in html, (
        "dark theme switch not bound to $store.theme.toggle()"
    )
    assert 'role="menuitemcheckbox"' in html, (
        "dark theme toggle missing role='menuitemcheckbox'"
    )
    assert ':aria-checked="$store.theme.dark' in html, (
        "dark theme switch aria-checked not bound to $store.theme.dark"
    )
    # Keyboard operable: @click on a <button> natively fires on Enter and Space,
    # so no separate @keydown handlers are needed (D-5: redundant keydown
    # handlers risk double-toggle in some browsers).
    assert '@click="$store.theme.toggle()"' in html, (
        "dark theme menuitem missing click handler"
    )
