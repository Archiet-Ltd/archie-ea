"""AI Chat shell redesign (shell-overhaul Wave 1, Task 6).

Product owner screenshotted `/ai-chat` at ~1024px as cluttered: suggestion
cards clip mid-word, the conversation rail eats a third of the width, and
three control rows stack above any content. This file pins the calm
version's structural contract:

- The "Multi-Domain AI Assistant" title block is gone — the assistant/scope
  selectors already say what's selected.
- Exactly one header control row (`data-testid="chat-header-row"`).
- Docs / Match / Export / Approvals move behind a single overflow menu
  (`data-testid="chat-overflow-menu"`).
- The conversation rail (Context/Query/Alerts/Chats) is a collapsible drawer
  (`data-testid="chat-rail"`) whose collapse state is Alpine-driven so it can
  auto-collapse below 1280px and remember the user's choice.

Does not touch transport/streaming behaviour — see test_ai_chat_stream_error.py
for that contract, and test_header_and_login_feedback.py for the pinned
`#model-selector` Settings-panel assertion this file must not break.
"""

from __future__ import annotations

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


def _make_user(db_session, make_org, label, enterprise_role="enterprise_architect"):
    from app.models.user import User

    org = make_org(f"aichat-shell-{label}")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"aichat-shell-{label}-{suffix}@example.com",
        first_name="Chat",
        last_name="Shell",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    user.password = "Sup3rSecret!23"
    db_session.add(user)
    db_session.flush()
    return user.id, org


def _get_chat_html(app, db_session, make_org, enterprise_role="enterprise_architect"):
    user, _ = _make_user(db_session, make_org, "get", enterprise_role)
    client = app.test_client()
    _login(client, user)
    resp = client.get("/ai-chat")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    return resp.get_data(as_text=True)


@pytest.mark.parametrize(
    "enterprise_role,persona",
    [
        ("solution_architect", "solutions_architect"),
        ("enterprise_architect", "enterprise_architect"),
        ("business_architect", "business_architect"),
        ("arb_member", "arb_member"),
        ("portfolio_manager", "portfolio_manager"),
        ("cto", "cio"),
        ("procurement", "procurement"),
        ("application_manager", "application_manager"),
        ("platform_admin", "platform_admin"),
    ],
)
def test_chat_bootstrap_selects_the_signed_in_roles_default_persona(
    app, db_session, make_org, enterprise_role, persona
):
    """The rendered picker and JavaScript bootstrap agree about the role default."""
    html = _get_chat_html(app, db_session, make_org, enterprise_role)
    assert f'window.defaultChatPersona = "{persona}";' in html
    assert f'<option value="{persona}" selected>' in html


def test_chat_client_uses_a_versioned_preference_key_for_explicit_choices():
    """The old automatic localStorage default must not mask the new role default."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/ai_chat/app.js").read_text(
        encoding="utf-8"
    )
    assert "archie_chat_persona_v2" in js
    assert "window.defaultChatPersona" in js
    assert "getItem('archie_chat_persona')" not in js
    assert "_applyPersonaChange(personaSelector.value, false)" in js
    assert "_applyPersonaChange(e.target.value, true)" in js


def test_chat_client_treats_persona_storage_as_optional():
    """Private browsing must keep the signed-in role default usable.

    Browser storage is only a convenience: a denied read must continue with
    the server-rendered role default, and a denied write must keep the choice
    already applied for this visit. Both exceptions need an explicit, bounded
    rationale rather than silently aborting chat initialization.
    """
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/ai_chat/app.js").read_text(
        encoding="utf-8"
    )
    persona_client = js[js.index("function _applyPersonaChange"):js.index("function updatePersonaUI")]

    assert persona_client.count("try {") >= 2
    assert persona_client.count("catch (error)") >= 2
    assert (
        "swallow-ok: localStorage is unavailable; the signed-in role default remains active"
        in persona_client
    )
    assert (
        "swallow-ok: localStorage is unavailable; the chosen persona remains active for this visit"
        in persona_client
    )


def test_picker_renders_every_configured_persona(app, db_session, make_org):
    """The server-driven picker must expose every selectable configuration."""
    from app.modules.ai_chat.services.multi_domain_chat_service import PERSONA_CONFIGS

    html = _get_chat_html(app, db_session, make_org)
    for persona in PERSONA_CONFIGS:
        assert f'<option value="{persona}"' in html


def test_no_multi_domain_title_block(app, db_session, make_org):
    html = _get_chat_html(app, db_session, make_org)
    assert "Multi-Domain AI Assistant" not in html, (
        "the old title block should be gone — the assistant/scope selectors "
        "already say what's selected"
    )


def test_exactly_one_header_row(app, db_session, make_org):
    html = _get_chat_html(app, db_session, make_org)
    assert html.count('data-testid="chat-header-row"') == 1


def test_chat_options_sheet_holds_match_export_approvals_and_the_panels(app, db_session, make_org):
    html = _get_chat_html(app, db_session, make_org)
    assert 'data-testid="chat-options-trigger"' in html

    start = html.index('id="drawer-chat-options"')
    sheet = html[start:]

    for text in ("Match entities", "Export conversation", "Export context log", "Approvals for review",
                 'id="persona-selector"', 'id="domain-selector"', 'id="nl-query-input"', 'id="domain-context"'):
        assert text in sheet, text
    # the upload control is an icon in the composer, not a menu entry
    assert 'data-testid="chat-upload"' in html[:start]


def test_rail_has_alpine_collapse_state(app, db_session, make_org):
    html = _get_chat_html(app, db_session, make_org)
    assert 'data-testid="chat-rail"' in html

    start = html.index('data-testid="chat-rail"')
    # The Alpine binding referencing the collapse var may appear before or
    # after the testid attribute on the same tag — look at the whole opening
    # tag plus a little slack either side.
    tag_slice = html[max(0, start - 400) : start + 400]
    assert "railOpen" in tag_slice, (
        "expected an Alpine binding referencing a rail collapse state var "
        "(e.g. :class=\"railOpen ? ... : ...\") near data-testid=\"chat-rail\""
    )


def test_model_selector_still_present(app, db_session, make_org):
    """Guards against Task 6 breaking the Task 4 Settings-panel contract."""
    html = _get_chat_html(app, db_session, make_org)
    assert 'id="model-selector"' in html


def test_welcome_offers_one_slot_for_four_questions_and_no_disclosure(app, db_session, make_org):
    """The welcome is a greeting and one grid of questions; the old browse-by-area cards and
    their "More suggestions" disclosure (an Alpine variable that once threw ReferenceError) are gone."""
    html = _get_chat_html(app, db_session, make_org)
    assert "showMoreSuggestions" not in html
    assert "More suggestions" not in html
    assert html.count('id="portfolio-briefing"') == 1
    assert 'id="domain-welcome-grid"' in html


def _find_ancestor_ids_and_testids(html, target_id):
    """Walk the DOM (via html.parser, not regex) and return the set of
    (attr, value) markers — id or data-testid — carried by every open
    ancestor element of the node with id=target_id, at the point the parser
    reaches it. Void/self-closing elements are not tracked as containers."""
    from html.parser import HTMLParser

    VOID_ELEMENTS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    class _Walker(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack = []  # list of (tag, attrs_dict)
            self.ancestors_at_target = None

        def handle_starttag(self, tag, attrs):
            attrs_dict = dict(attrs)
            if attrs_dict.get("id") == target_id and self.ancestors_at_target is None:
                # Record ancestors BEFORE pushing the target itself.
                self.ancestors_at_target = list(self.stack)
            if tag not in VOID_ELEMENTS:
                self.stack.append((tag, attrs_dict))

        def handle_startendtag(self, tag, attrs):
            attrs_dict = dict(attrs)
            if attrs_dict.get("id") == target_id and self.ancestors_at_target is None:
                self.ancestors_at_target = list(self.stack)
            # self-closing — never pushed, nothing to pop.

        def handle_endtag(self, tag):
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    del self.stack[i:]
                    break

    walker = _Walker()
    walker.feed(html)
    return walker.ancestors_at_target


def test_approvals_modal_is_not_descendant_of_overflow_menu(app, db_session, make_org):
    """CRITICAL fix (Wave 1 final review): the approvals modal
    (#ai-chat-approvals-modal) used to be nested inside the overflow
    dropdown's x-show="menuOpen" panel. Platform.modal.open() toggles
    classes on the modal element in place — it does not teleport the modal
    elsewhere in the DOM — so the instant the overflow menu closes
    (@click="menuOpen = false" fires on every trigger inside it, including
    the Approvals menu item itself), the whole subtree containing the modal
    goes display:none while Platform.modal still believes it is open: body
    scroll stays locked, Escape stays bound, background stays inert, and the
    user sees nothing. Parsed with html.parser (not regex) because nesting
    depth is exactly what's under test."""
    html = _get_chat_html(app, db_session, make_org)

    assert 'id="ai-chat-approvals-modal"' in html
    assert 'data-testid="chat-overflow-menu"' in html

    ancestors = _find_ancestor_ids_and_testids(html, "ai-chat-approvals-modal")
    assert ancestors is not None, "could not locate #ai-chat-approvals-modal while parsing"

    ancestor_testids = {a.get("data-testid") for _, a in ancestors}

    assert "chat-overflow-menu" not in ancestor_testids, (
        "#ai-chat-approvals-modal must not be a descendant of "
        '[data-testid="chat-overflow-menu"] — that panel is x-show="menuOpen" '
        "and toggling menuOpen would hide the modal out from under "
        "Platform.modal"
    )


def test_approvals_modal_is_not_inside_the_options_sheet(app, db_session, make_org):
    """The sheet closes when Approvals is chosen, so the dialog cannot live inside it."""
    html = _get_chat_html(app, db_session, make_org)
    ancestors = _find_ancestor_ids_and_testids(html, "ai-chat-approvals-modal")
    assert ancestors is not None
    assert "drawer-chat-options" not in {a.get("id") for _, a in ancestors}
