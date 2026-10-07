"""R1-B88: an administrator sees, before sending, what the chosen persona and role will see."""

import pytest

from tests.test_team_invite_acceptance import _make_org, _make_user


def _member(db_session, org):
    """A non-administrator member (Architect role): can sign in, cannot administer."""
    from app.models.user import Role

    user = _make_user(db_session, org)
    user.role = Role.query.filter_by(name="Architect").first()
    user.is_org_admin = False
    db_session.flush()
    return user


def test_every_invitable_persona_and_role_has_a_plain_preview():
    from app.models.org_role import VALID_ORG_ROLES
    from app.modules.account.services.invitation_service import INVITABLE_PERSONAS
    from app.utils.role_access import persona_preview

    assert "finance" in INVITABLE_PERSONAS and "non_technical_owner" in INVITABLE_PERSONAS
    for persona in INVITABLE_PERSONAS:
        for role in VALID_ORG_ROLES:
            preview = persona_preview(persona, role)
            assert preview["description"].strip(), (persona, role)
            assert preview["display_name"].strip()
            assert preview["will_see"], (persona, role)
            assert all(zone["links"] for zone in preview["will_see"])
            assert preview["summary"].strip()
            if role == "viewer":
                assert preview["can_change"] is False
                assert "cannot change records" in preview["summary"]
            if role == "architect":
                assert preview["can_change"] is True and preview["can_administer"] is False
                assert "add and change records" in preview["summary"]
            if role == "org_admin":
                assert preview["can_administer"] is True
                assert "manage the team and settings" in preview["summary"]


def test_every_persona_has_a_description_without_jargon_markers():
    from app.models.user import ROLE_PLAIN_DESCRIPTIONS, VALID_ROLES

    assert set(ROLE_PLAIN_DESCRIPTIONS) == set(VALID_ROLES)
    for persona in ("finance", "non_technical_owner", "compliance", "risk", "operations"):
        text = ROLE_PLAIN_DESCRIPTIONS[persona]
        assert len(text.split(".")) >= 2 and len(text) < 260
        for jargon in ("RBAC", "ArchiMate", "TOGAF", "SIEM"):
            assert jargon not in text


def test_unknown_persona_or_role_is_refused_by_the_function():
    from app.utils.role_access import persona_preview

    with pytest.raises(ValueError):
        persona_preview("wizard", "viewer")
    with pytest.raises(ValueError):
        persona_preview("finance", "owner")


def test_team_page_shows_the_preview_before_the_send_button(app, db_session, login_as, client):
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    db_session.commit()

    login_as(client, admin)
    html = client.get("/admin/team").get_data(as_text=True)

    send = html.index("Send invitation")
    for persona in ("non_technical_owner", "finance"):
        marker = 'data-persona="%s"' % persona
        assert marker in html
        assert html.index(marker) < send
    assert "Sees what each application and contract costs" in html
    # Exactly one pairing is visible without script: the default.
    visible = [
        part for part in html.split("<section data-persona-preview")[1:]
        if "hidden" not in part.split(">")[0]
    ]
    assert len(visible) == 1


def test_preview_submit_re_renders_with_the_chosen_pair_without_script(app, db_session, login_as, client):
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    db_session.commit()

    login_as(client, admin)
    html = client.get("/admin/team?persona=non_technical_owner&role=architect").get_data(as_text=True)

    sections = html.split("<section data-persona-preview")[1:]
    visible = [s for s in sections if "hidden" not in s.split(">")[0]]
    assert len(visible) == 1
    assert 'data-persona="non_technical_owner"' in visible[0]
    assert 'data-role="architect"' in visible[0]
    assert 'formmethod="get"' in html


def test_team_page_has_the_access_review_and_export_buttons(app, db_session, login_as, client):
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    db_session.commit()

    login_as(client, admin)
    html = client.get("/admin/team").get_data(as_text=True)

    assert "/admin/access/reviews" in html and "Access review" in html
    assert "/admin/access/exports" in html and "Export activity" in html


def test_json_preview_route_answers_the_same_content(app, db_session, login_as, client):
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    db_session.commit()

    login_as(client, admin)
    resp = client.get("/admin/team/persona-preview?persona=finance&role=viewer")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["persona"] == "finance"
    assert body["display_name"] == "Finance"
    assert body["can_change"] is False
    assert body["will_see"]


def test_json_preview_route_refuses_unknown_persona_and_role(app, db_session, login_as, client):
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    db_session.commit()

    login_as(client, admin)
    assert client.get("/admin/team/persona-preview?persona=wizard&role=viewer").status_code == 400
    assert client.get("/admin/team/persona-preview?persona=finance&role=owner").status_code == 400
    assert client.get("/admin/team/persona-preview").status_code == 400


def test_json_preview_route_is_closed_to_a_non_administrator(app, db_session, login_as, client):
    org = _make_org(db_session, "A")
    member = _member(db_session, org)
    db_session.commit()

    login_as(client, member)
    assert client.get("/admin/team/persona-preview?persona=finance&role=viewer").status_code == 403
