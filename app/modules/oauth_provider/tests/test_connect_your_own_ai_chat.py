"""The connect page offers a person their own AI chat: the connection address,
a copy button, a line per supported client, and a prompt to ask the same
question there -- or to ask it here instead, with the question filled.

The address is the one configured origin's ``/mcp`` path, shown only to the
signed-in person for their own organisation. Each test fails without the
connect-page additions it names.
"""

from __future__ import annotations

import uuid

from app.modules.oauth_provider.tests.test_oauth_flow import _make_user


def _user(db_session, make_org, label):
    org = make_org(f"connect-{label}")
    user = _make_user(
        db_session, org, f"connect-{label}-{uuid.uuid4().hex[:8]}@example.com"
    )
    db_session.flush()
    return user, org


def _page(client, login_as, user, query=""):
    login_as(client, user)
    resp = client.get("/account/manage/connected-assistants" + query)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    return resp.get_data(as_text=True)


def test_connect_panel_shows_the_connection_address_and_a_copy_button(
    app, client, db_session, make_org, login_as
):
    user, _org = _user(db_session, make_org, "addr")
    html = _page(client, login_as, user)

    base = (app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    address = f"{base}/mcp"

    assert 'data-testid="connect-ai-chat"' in html
    assert 'data-testid="connection-address"' in html
    assert address in html
    # The copy button carries the exact address it copies (the delegated
    # clipboard handler reads this attribute, and a journey asserts it).
    assert f'data-copy-text="{address}"' in html
    assert 'data-testid="copy-connection-address"' in html


def test_connect_panel_lists_one_line_per_supported_client(
    client, db_session, make_org, login_as
):
    user, _org = _user(db_session, make_org, "clients")
    html = _page(client, login_as, user)

    from app.modules.oauth_provider.assistants_routes import SUPPORTED_CLIENTS

    assert 'data-testid="supported-clients"' in html
    assert SUPPORTED_CLIENTS, "at least one supported client must be listed"
    for entry in SUPPORTED_CLIENTS:
        assert entry["name"] in html


def test_try_it_without_a_question_links_to_ask(
    client, db_session, make_org, login_as
):
    user, _org = _user(db_session, make_org, "tryit")
    html = _page(client, login_as, user)

    assert 'data-testid="try-it"' in html
    assert 'data-testid="skip-ask-here"' in html
    assert 'href="/intelligence/ask"' in html


def test_skip_ask_here_carries_the_question_when_an_element_is_handed_across(
    client, db_session, make_org, login_as
):
    """After the first answer, the question travels here as the element id, and
    "Skip, ask here instead" lands on Ask with that same impact question filled."""
    user, _org = _user(db_session, make_org, "skip")
    html = _page(client, login_as, user, query="?element=4242")

    assert 'data-testid="skip-ask-here"' in html
    # The Ask link opens on the impact question for the handed element.
    assert "/intelligence/ask?" in html
    assert "question=impact" in html
    assert "element=4242" in html


def test_a_non_numeric_element_is_ignored_and_ask_opens_plain(
    client, db_session, make_org, login_as
):
    user, _org = _user(db_session, make_org, "badid")
    html = _page(client, login_as, user, query="?element=not-a-number")

    # The connect panel renders, and its "ask here instead" link opens Ask
    # plainly: a non-numeric element is ignored, never echoed into the URL.
    assert 'data-testid="connect-ai-chat"' in html
    assert 'data-testid="skip-ask-here"' in html
    assert "element=not-a-number" not in html
    assert "/intelligence/ask?" not in html


def test_the_setup_checklist_offers_connecting_your_own_ai_chat(
    client, db_session, make_org, login_as
):
    """A fresh organisation's setup checklist carries a last, optional step that
    leads to the connect page. It is additive: the earlier steps are untouched,
    so the first-answer path is unchanged."""
    user, _org = _user(db_session, make_org, "checklist")
    login_as(client, user)
    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)

    assert 'data-testid="guided-setup"' in html
    assert "Ask this in your own AI chat" in html
    assert 'href="/account/manage/connected-assistants"' in html
    # The earlier steps are still there, ahead of it.
    assert "Import applications" in html
    assert html.index("Import applications") < html.index("Ask this in your own AI chat")
