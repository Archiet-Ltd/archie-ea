"""R1-06: lead search (T-I1), CSRF on confirm (T-E5), and the post-confirm
read-authority rule (T-B3 half), on the journey programme-structure routes."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org_id, persona, first, last="Tester", confirmed=True):
    from app.models.user import User

    user = User(
        email=f"ps-{uuid.uuid4().hex[:10]}@example.test", first_name=first, last_name=last,
        confirmed=confirmed, organization_id=org_id, enterprise_role=persona,
    )
    user.password = "test-password-not-secret"
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def world(db_session, make_org):
    from app.models.architecture_journey import ArchitectureJourney

    org, other = make_org("ps-a"), make_org("ps-b")
    ea = _user(db_session, org.id, "enterprise_architect", "Owner")
    viewer = _user(db_session, org.id, "solution_architect", "Viewer")
    _user(db_session, org.id, "portfolio_manager", "Zelda", "Findable")
    _user(db_session, org.id, "portfolio_manager", "Zelda", "Unconfirmed", confirmed=False)
    _user(db_session, other.id, "portfolio_manager", "Zelda", "Foreign")
    journey = ArchitectureJourney(
        owner_id=ea.id, organization_id=org.id, title="Route journey", intent="portfolio_change",
        selected_layers=["motivation"], programme_type="s4hana", journey_state={},
    )
    db_session.add(journey)
    db_session.flush()
    return {"ea": ea, "viewer": viewer, "journey": journey}


def _client(app, login_as, user):
    client = app.test_client()
    login_as(client, user)
    return client


def test_lead_search_rules(app, login_as, world):
    """T-I1."""
    app.config["PROGRAMME_TYPES_INCLUDE_UNREVIEWED"] = True
    client = _client(app, login_as, world["ea"])
    url = f"/architecture-journey/work/{world['journey'].id}/lead-search"

    assert client.get(url + "?q=Z").status_code == 400  # one character

    body = client.get(url + "?q=Zelda").get_json()["data"]["people"]
    names = [p["display_name"] for p in body]
    assert names == ["Zelda Findable"], names  # not the foreign org, not unconfirmed
    for person in body:
        assert set(person) == {"id", "display_name"}, "no email or other field"

    many = client.get(url + "?q=er").get_json()["data"]["people"]
    assert len(many) <= 20


def test_lead_search_refused_for_someone_who_cannot_create(app, login_as, world):
    client = _client(app, login_as, world["viewer"])
    response = client.get(f"/architecture-journey/work/{world['journey'].id}/lead-search?q=Zelda")
    assert response.status_code in (403, 404)


def test_confirm_without_a_csrf_token_is_rejected(app, login_as, world):
    """T-E5: with CSRF protection on, a confirm POST carrying no token is refused."""
    client = _client(app, login_as, world["ea"])
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = client.post(
            f"/architecture-journey/work/{world['journey'].id}/programme-structure",
            data={"command_key": "x"},
        )
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert response.status_code in (400, 403)
    assert response.status_code != 303


def test_confirm_control_is_absent_for_a_persona_that_cannot_create(app, login_as, world):
    app.config["PROGRAMME_TYPES_INCLUDE_UNREVIEWED"] = True
    client = _client(app, login_as, world["viewer"])
    response = client.get(f"/architecture-journey/work/{world['journey'].id}/programme-structure")
    # the viewer is not on the journey -> the journey guard answers first
    assert response.status_code in (403, 404) or 'data-testid="structure-form"' not in response.get_data(as_text=True)
