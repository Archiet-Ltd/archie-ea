"""The app shell's chrome must not depend on how tall the page is.

Markup-level checks that run without a browser (tests/smoke/test_app_shell_chrome.py
measures the same things in one):

1. `#admin-header` is `h-16` with `shrink-0`. It is a flex child of a column that
   scrolls; without `shrink-0` a page taller than the viewport squeezes it down to
   its content (about 45 px) while a short page keeps 64 px, so the header changed
   height from page to page.
2. The fixed "Open sidebar" button is `top-3` + `h-10`, which centres it in that
   64 px band. `top-4` + `h-10` spans 16..56 px and hangs out of a shorter header.
3. The closed drawer carries no static `shadow-lg`: a shadow on a panel parked at
   `-translate-x-full` bleeds about 12 px into the viewport as a grey band down the
   left edge of every page below lg. The shadow is drawn only while the drawer is
   open.
4. A page that supplies guided-mode steps shows a floating help button in the
   bottom-right corner; those pages reserve room under their content so the last
   row scrolls clear of it.
5. AI chat (with the provider notice that sits above it on a fresh install) fills
   exactly the area under the header. It opts out of the shell's vertical padding
   through the `main_spacing` block instead of pulling itself back with `-my-6`,
   which left 8 px above and below plus 16 px of scroll, and it gives the chat
   what the notice leaves rather than a full 100vh-4rem of its own.

Follows the ``client.get(...)`` + session-login pattern of
``tests/test_header_and_login_feedback.py``.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _login(client, user_id):
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


def _page(app, db_session, make_org, path, role="enterprise_architect"):
    from app.models.user import User

    org = make_org("shell-chrome-%s" % uuid.uuid4().hex[:6])
    user = User(
        email="shell-chrome-%s@example.com" % uuid.uuid4().hex[:8],
        first_name="Shell", last_name="Chrome",
        organization_id=org.id, confirmed=True, enterprise_role=role,
    )
    user.password = "Sup3rSecret!23"
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    _login(client, user.id)
    resp = client.get(path)
    assert resp.status_code == 200, "%s -> %s: %s" % (path, resp.status_code, resp.get_data(as_text=True)[:500])
    return resp.get_data(as_text=True)


_OPEN_TAG = re.compile(r"""<[a-zA-Z][^\s>/]*(?:[^>"']|"[^"]*"|'[^']*')*>""")


def _tag_around(html, marker, tag_name):
    """The whole opening tag (quoted attribute values may contain `>`) that carries `marker`."""
    at = html.index(marker)
    start = html.rindex("<" + tag_name, 0, at)
    m = _OPEN_TAG.match(html, start)
    assert m and m.end() > at, "could not read the <%s> tag around %r" % (tag_name, marker)
    return m.group(0)


def _static_classes(tag):
    """The static class="..." tokens of a tag (not the Alpine :class binding)."""
    m = re.search(r'(?<![:\w-])class="([^"]*)"', tag)
    assert m, "no static class attribute in %r" % tag[:200]
    return m.group(1).split()


def test_header_is_a_fixed_64px_band_that_cannot_shrink(app, db_session, make_org):
    html = _page(app, db_session, make_org, "/dashboard/overview")
    classes = _static_classes(_tag_around(html, 'id="admin-header"', "header"))
    assert "h-16" in classes, classes
    assert "shrink-0" in classes, (
        "the header is a flex child of a scrolling column: without shrink-0 a page taller "
        "than the viewport squeezes it below 64 px; classes: %r" % classes
    )


def test_mobile_menu_button_is_centred_in_the_header_band(app, db_session, make_org):
    html = _page(app, db_session, make_org, "/dashboard/overview")
    classes = _static_classes(_tag_around(html, 'aria-label="Open sidebar"', "button"))
    assert "top-3" in classes and "h-10" in classes, classes
    assert "top-4" not in classes, (
        "top-4 + h-10 spans 16..56 px and hangs out of a header that is not 64 px tall; "
        "top-3 + h-10 (12..52) sits inside the 64 px band: %r" % classes
    )


def test_closed_drawer_has_no_static_shadow(app, db_session, make_org):
    """A permanent shadow on the off-screen drawer bleeds into the viewport."""
    html = _page(app, db_session, make_org, "/dashboard/overview")
    tag = _tag_around(html, 'id="admin-sidebar"', "aside")
    static = _static_classes(tag)
    assert "shadow-lg" not in static, (
        "the parked drawer must not carry shadow-lg in its static classes: %r" % static
    )
    assert "-translate-x-full" not in static, "the translate pair lives in the :class map only"
    assert "lg:shadow-none" in static, "desktop (docked sidebar) must stay shadowless"
    # ... and it is drawn while open, through the same object map as the translate pair.
    assert re.search(r"'shadow-lg'\s*:\s*\$store\.sidebar\.open\b", tag), tag


def test_pages_with_guided_steps_reserve_room_for_the_help_button(app, db_session, make_org):
    spacer = '<div class="h-10" aria-hidden="true"></div>'
    with_steps = _page(app, db_session, make_org, "/dashboard/overview")
    assert 'id="guided-mode-steps-data"' in with_steps
    assert with_steps.count(spacer) == 1, "guided-mode page must reserve room under its content"
    assert with_steps.index(spacer) < with_steps.index('id="guided-mode-trigger"')


def test_pages_without_guided_steps_gain_no_extra_space(app, db_session, make_org):
    spacer = '<div class="h-10" aria-hidden="true"></div>'
    without = _page(app, db_session, make_org, "/solutions/")
    assert 'id="guided-mode-steps-data"' not in without
    assert spacer not in without


def test_default_page_padding_is_unchanged(app, db_session, make_org):
    html = _page(app, db_session, make_org, "/dashboard/overview")
    assert '<div class="px-6 py-8">' in html


def test_ai_chat_fills_the_area_under_the_header_exactly(app, db_session, make_org):
    html = _page(app, db_session, make_org, "/ai-chat")
    # The shell's vertical padding is switched off for this page ...
    assert '<div class="px-6">' in html and '<div class="px-6 py-8">' not in html
    # ... one frame is 100vh minus the 4rem header and holds the chat AND any notice above it
    # (the "AI provider unavailable" banner is the default state of a fresh install), so the
    # chat takes what is left instead of adding its own 100vh-4rem on top of the banner ...
    frame = _static_classes(_tag_around(html, 'data-testid="ai-chat-frame"', "div"))
    assert "h-[calc(100vh-4rem)]" in frame and "flex-col" in frame, frame
    # ... and the chat itself no longer carries a height of its own or the negative vertical
    # margin it needed to fight the padding.
    chat = _static_classes(_tag_around(html, "railOpen:", "div"))
    assert "flex-1" in chat and "min-h-0" in chat, chat
    assert "-my-6" not in chat and "h-[calc(100vh-4rem)]" not in chat, chat
    assert html.index('data-testid="ai-chat-frame"') < html.index("railOpen:")
