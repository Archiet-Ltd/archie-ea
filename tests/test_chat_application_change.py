"""Changing an application from the AI chat: shown first, saved on Save, by the form's own rules.

Two organisations throughout. A person who may not edit applications is refused
in words and nothing changes; a change is bound to the person and the
organisation it was shown to.
"""

from __future__ import annotations

import html as html_module
import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, persona="enterprise_architect", platform_role="User"):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", first_name=label.capitalize(),
        last_name="Test", organization_id=org.id, confirmed=True, enterprise_role=persona,
    )
    user.role = Role.query.filter_by(name=platform_role).one()
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def world(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent

    org_a, org_b = make_org("chg-a"), make_org("chg-b")
    name = f"Ifiok {uuid.uuid4().hex[:5]}"
    apps = {}
    for key, org in (("a", org_a), ("b", org_b)):
        row = ApplicationComponent(
            name=name, organization_id=org.id, description=f"Old description {key}",
            deployment_status="development",
        )
        db_session.add(row)
        db_session.flush()
        apps[key] = row
    return {
        "org_a": org_a, "org_b": org_b, "apps": apps, "name": name,
        "editor": _user(db_session, org_a, "editor"),
        "viewer": _user(db_session, org_a, "viewer", platform_role="Viewer"),
        "other": _user(db_session, org_b, "other"),
    }


def _ask(client, text):
    resp = client.post("/ai-chat/estate-answer", json={"message": text, "remember": False})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _token(html):
    return re.search(r'data-change-token="([^"]+)"', html).group(1)


def _stored(db_session, application, column="description"):
    db_session.expire_all()
    return getattr(type(application).query.get(application.id), column)


def test_a_change_is_shown_first_and_saved_only_when_saved(world, client, login_as, db_session):
    application = world["apps"]["a"]
    login_as(client, world["editor"])
    shown = _ask(client, f"set {world['name']}'s description to Runs tenant billing for finance")
    assert shown["found"] is True and shown["change_state"] == "preview"
    html = shown["html"]
    assert "Old description a" in html and "Runs tenant billing for finance" in html
    assert 'data-testid="application-change-save"' in html
    assert _stored(db_session, application) == "Old description a", "saved before the person pressed Save"

    saved = client.post("/ai-chat/application-change/confirm", json={"token": _token(html)}).get_json()
    assert saved["success"] is True and saved["change_state"] == "saved"
    assert f"/applications/{application.id}" in saved["html"]
    assert _stored(db_session, application) == "Runs tenant billing for finance"

    page = client.get(f"/applications/{application.id}").get_data(as_text=True)
    assert "Runs tenant billing for finance" in page
    assert _stored(db_session, world["apps"]["b"]) == "Old description b", "the other organisation's row changed"


def test_the_other_phrasings_and_fields_are_understood(world, client, login_as, db_session):
    login_as(client, world["editor"])
    html = _ask(client, f'Change the version of {world["name"]} to "9.1"')["html"]
    assert "9.1" in html and "Change the version of" in html
    html = _ask(client, f"update {world['name']}'s status to production")["html"]
    assert "production" in html and "deployment status" in html


def test_a_person_who_may_not_edit_is_refused_in_words_and_nothing_changes(world, client, login_as, db_session):
    application = world["apps"]["a"]
    login_as(client, world["viewer"])
    shown = _ask(client, f"set {world['name']}'s description to Overwritten by a viewer")
    assert shown["change_state"] == "refused"
    assert "I have not changed anything" in shown["html"]
    assert "can look at applications but not edit them" in shown["html"]
    assert "data-change-token" not in shown["html"] and "Save" not in shown["html"]
    assert _stored(db_session, application) == "Old description a"

    # A token minted for the editor is no use to the viewer.
    login_as(client, world["editor"])
    token = _token(_ask(client, f"set {world['name']}'s description to Editor text")["html"])
    login_as(client, world["viewer"])
    refused = client.post("/ai-chat/application-change/confirm", json={"token": token}).get_json()
    assert refused["success"] is False
    assert _stored(db_session, application) == "Old description a"


def test_a_change_belongs_to_the_person_and_organisation_it_was_shown_to(world, client, login_as, db_session):
    login_as(client, world["editor"])
    token = _token(_ask(client, f"set {world['name']}'s description to Mine")["html"])

    login_as(client, world["other"])
    stolen = client.post("/ai-chat/application-change/confirm", json={"token": token}).get_json()
    assert stolen["success"] is False
    assert _stored(db_session, world["apps"]["a"]) == "Old description a"
    assert _stored(db_session, world["apps"]["b"]) == "Old description b"

    forged = client.post("/ai-chat/application-change/confirm", json={"token": token[:-3] + "xyz"}).get_json()
    assert forged["success"] is False

    # Asking for the other organisation's application by name changes only the asker's own.
    shown = _ask(client, f"set {world['name']}'s description to Theirs")
    saved = client.post("/ai-chat/application-change/confirm", json={"token": _token(shown["html"])}).get_json()
    assert saved["success"] is True
    assert _stored(db_session, world["apps"]["b"]) == "Theirs"
    assert _stored(db_session, world["apps"]["a"]) == "Old description a"


def test_what_the_form_would_refuse_is_refused_in_words_before_it_is_shown(world, client, login_as, db_session):
    login_as(client, world["editor"])
    shown = _ask(client, f"set {world['name']}'s description to " + "x" * 5001)
    assert shown["change_state"] == "invalid"
    assert "5000 characters or fewer" in shown["html"]
    shown = _ask(client, f"set {world['name']}'s status to nonsense")
    assert shown["change_state"] == "invalid"
    from app.services.application_status import refusal_for

    assert refusal_for("nonsense") in html_module.unescape(shown["html"])
    assert _stored(db_session, world["apps"]["a"]) == "Old description a"


def test_an_unknown_application_and_an_unchanged_value_say_so(world, client, login_as):
    login_as(client, world["editor"])
    # A subject that is no application of this organisation is not an application change: the
    # request goes on to the model instead of being answered "I could not find an application".
    nowhere = _ask(client, "set Nowhere platform's description to X")
    assert nowhere["found"] is False and "change_state" not in nowhere
    same = _ask(client, f"set {world['name']}'s description to Old description a")
    assert same["change_state"] == "unchanged" and "already has that description" in same["html"]


def test_a_question_from_an_element_card_is_never_a_change_request(world, client, login_as, db_session):
    """A card names its element, so a question worded like a change is answered about that element.

    The same words typed in the chat are a change request; sent from a card of this
    organisation they are not, and a card id from another organisation counts for nothing.
    """
    from app.models.archimate_core import ArchiMateElement

    mine = ArchiMateElement(name=world["name"], type="ApplicationComponent", layer="Application",
                            organization_id=world["org_a"].id)
    theirs = ArchiMateElement(name=world["name"], type="ApplicationComponent", layer="Application",
                              organization_id=world["org_b"].id)
    db_session.add_all([mine, theirs])
    db_session.flush()
    application = world["apps"]["a"]
    words = f"set {world['name']}'s version to 3.1"
    login_as(client, world["editor"])

    def ask(**extra):
        resp = client.post("/ai-chat/estate-answer", json={"message": words, "remember": False, **extra})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        return resp.get_json()

    typed = ask()
    assert typed["change_state"] == "preview" and "data-change-token" in typed["html"]

    from_card = ask(element_id=mine.id)
    assert "change_state" not in from_card, "a card question was treated as a change request"
    assert "data-change-token" not in (from_card.get("html") or "")
    assert "lens" not in from_card, "a card question was answered by a lens"

    other_card = ask(element_id=theirs.id)
    assert other_card["change_state"] == "preview", "another organisation's card id changed how the question is read"
    assert _stored(db_session, application, "version") != "3.1", "nothing was asked to be saved"


def test_an_owner_who_has_no_account_can_be_set_from_the_chat(world, client, login_as, db_session):
    from app.models.application_owner import ApplicationOwner

    login_as(client, world["editor"])
    html = _ask(client, f"set {world['name']}'s business owner to Ifiok Etim")["html"]
    saved = client.post("/ai-chat/application-change/confirm", json={"token": _token(html)}).get_json()
    assert saved["success"] is True and "Ifiok Etim" in saved["html"]
    row = ApplicationOwner.query.filter_by(application_id=world["apps"]["a"].id).one()
    assert row.user_id is None and row.person_name == "Ifiok Etim"


def test_a_colon_after_to_is_accepted_in_both_phrasings():
    """"Change the description of X to: Y" is how a person writes the request in the chat.

    The colon after "to" (or "as"), with or without a space before it, is accepted in the
    of-form and the possessive form alike, and the saved value starts after it.
    """
    from app.modules.ai_chat.services.application_change import parse_change_request

    for joiner in ("to", "to:", "to :"):
        of_form = parse_change_request(f"Change the description of Payments {joiner} Runs billing")
        assert of_form == {
            "application": "Payments",
            "key": "description",
            "label": "description",
            "value": "Runs billing",
        }, f"of-form with {joiner!r}: {of_form}"
        possessive = parse_change_request(f"set Payments's version {joiner} 9.1")
        assert possessive == {
            "application": "Payments",
            "key": "version",
            "label": "version",
            "value": "9.1",
        }, f"possessive with {joiner!r}: {possessive}"


def test_crafted_message_parses_in_linear_time():
    """The reviewer's crafted message of several thousand characters parses in linear time.

    The old field pattern ([a-z ]+?) and lazy value tail backtracked polynomially on
    "set a" followed by repeats of twenty spaces and "on": 225 characters took 0.24 s,
    885 characters 4.43 s, 1765 characters 19.56 s. The route reads up to
    MAX_CHANGE_CHARS of every chat message before any role check, and Python's regex
    engine holds the interpreter lock while it matches, so the gunicorn worker's other
    threads stopped. The field is now matched against the known names in FIELDS
    (longest first) after whitespace is collapsed, and the value is a greedy tail
    stripped afterwards, so the parse is linear for every input.
    """
    import time

    from app.modules.ai_chat.services.application_change import parse_change_request

    # "set a" followed by repeats of twenty spaces and "on": 225, 885, 1765 and about
    # 8000 characters, the reviewer's measured sizes.
    for size in (225, 885, 1765, 8013):
        repeat = (size - 5) // 22
        message = "set a" + (" " * 20 + "on") * repeat
        start = time.perf_counter()
        result = parse_change_request(message)
        elapsed = time.perf_counter() - start
        assert result is None, f"{len(message)}-character message should not parse as a change"
        assert elapsed < 0.1, (
            f"parsed {len(message)} characters in {elapsed:.3f}s, expected under 0.1 s"
        )


def test_a_value_is_returned_exactly_as_typed_over_several_lines():
    from app.modules.ai_chat.services.application_change import parse_change_request

    typed = "Runs billing.\n\nOwned by finance:\n- invoices\n- refunds"
    found = parse_change_request(f"  set Payments's description to \n  {typed}\n\n ")
    assert found is not None and found["application"] == "Payments"
    assert found["value"] == typed, "inner line breaks and blank lines are kept; only the ends are stripped"
    of_form = parse_change_request(f"set description of Payments to {typed}")
    assert of_form is not None and of_form["value"] == typed


def test_a_value_with_runs_of_spaces_keeps_them_and_its_typed_length():
    from app.modules.ai_chat.services.application_change import parse_change_request

    typed = "two  spaces,\tone tab,   three   spaces"
    found = parse_change_request(f"set Payments's description to {typed}")
    assert found["value"] == typed and len(found["value"]) == len(typed)
    # A description the form refuses as too long is still too long once parsed: spaces are not dropped.
    long_value = ("ab  " * 1350).strip()
    assert len(long_value) > 5000
    parsed = parse_change_request(f"set Payments's description to {long_value}")
    assert parsed["value"] == long_value and len(parsed["value"]) > 5000


def test_asking_for_the_stored_multi_line_description_again_is_unchanged(world, client, login_as, db_session):
    application = world["apps"]["a"]
    typed = "Runs billing.\n\nOwned by finance:\n- invoices\n- refunds"
    application.description = typed
    db_session.flush()
    login_as(client, world["editor"])
    same = _ask(client, f"set {world['name']}'s description to {typed}")
    assert same["change_state"] == "unchanged" and "already has that description" in same["html"]
    assert _stored(db_session, application) == typed


def test_a_multi_line_description_from_the_chat_is_saved_as_typed(world, client, login_as, db_session):
    application = world["apps"]["a"]
    login_as(client, world["editor"])
    typed = "Runs billing.\n\nOwned by finance:\n- invoices\n-  refunds (two  spaces)"
    shown = _ask(client, f"set {world['name']}'s description to {typed}")
    assert shown["change_state"] == "preview"
    assert _stored(db_session, application) == "Old description a"
    saved = client.post("/ai-chat/application-change/confirm", json={"token": _token(shown["html"])}).get_json()
    assert saved["success"] is True
    assert _stored(db_session, application) == typed

