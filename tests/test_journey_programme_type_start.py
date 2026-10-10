"""R1-04: programme type on the journey start form (US-1).

The Playwright half of SDD 13 R1 test 2 (choose S/4HANA under the override,
see counts, submit, workspace names the type, reload keeps it) lives in
tests/smoke/test_journey_programme_type_start_journey.py. SDD 13 R1 test 1
("default config shows no selector") is proven here instead of there: the
smoke live server is session-scoped and boots once under TestingConfig,
which turns PROGRAMME_TYPES_INCLUDE_UNREVIEWED on for the whole session
(R1-03), so no browser-driven page in that server can ever observe the
override *off*. A Flask test client against the app fixture, with the
override explicitly toggled off for this test, renders the same server-side
template logic and is the only way to observe that state honestly.

Follows tests/test_tenant_isolation.py's pattern: shared db_session/make_org/
login_as fixtures, never a hand-rolled module-scoped app fixture.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def ea_user(db_session, make_org):
    from app.models.user import User

    org = make_org("journey-programme-type")
    user = User(
        email=f"journey-ea-{uuid.uuid4().hex[:10]}@example.test",
        first_name="En",
        last_name="Architect",
        confirmed=True,
        organization_id=org.id,
        enterprise_role="enterprise_architect",
    )
    user.password = "test-password-not-secret"
    db_session.add(user)
    db_session.flush()
    return user


def test_selector_is_absent_under_the_default_config(app, ea_user, login_as):
    """SDD 13 R1 test 1."""
    client = app.test_client()
    login_as(client, ea_user)
    original = app.config.get("PROGRAMME_TYPES_INCLUDE_UNREVIEWED")
    app.config["PROGRAMME_TYPES_INCLUDE_UNREVIEWED"] = False
    try:
        response = client.get("/architecture-journey/")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert 'id="journey-programme-type"' not in body
    finally:
        app.config["PROGRAMME_TYPES_INCLUDE_UNREVIEWED"] = original


def test_selector_is_present_under_the_override(app, ea_user, login_as):
    client = app.test_client()
    login_as(client, ea_user)
    app.config["PROGRAMME_TYPES_INCLUDE_UNREVIEWED"] = True
    response = client.get("/architecture-journey/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="journey-programme-type"' in body
    assert "SAP S/4HANA transformation" in body


def test_unknown_programme_type_key_is_rejected_and_creates_no_journey(
    app, db_session, ea_user, login_as
):
    from app.models.architecture_journey import ArchitectureJourney

    client = app.test_client()
    login_as(client, ea_user)
    before = ArchitectureJourney.query.filter_by(organization_id=ea_user.organization_id).count()

    response = client.post(
        "/architecture-journey/start-architecture",
        json={
            "title": "Unknown programme type probe",
            "intent": "portfolio_change",
            "selected_layers": ["motivation"],
            "programme_type": "not_a_real_type",
        },
    )
    assert response.status_code == 400
    after = ArchitectureJourney.query.filter_by(organization_id=ea_user.organization_id).count()
    assert after == before


def test_offered_programme_type_is_accepted_and_persists(app, db_session, ea_user, login_as):
    from app.models.architecture_journey import ArchitectureJourney

    client = app.test_client()
    login_as(client, ea_user)
    app.config["PROGRAMME_TYPES_INCLUDE_UNREVIEWED"] = True

    response = client.post(
        "/architecture-journey/start-architecture",
        json={
            "title": "S/4HANA probe",
            "intent": "portfolio_change",
            "selected_layers": ["motivation"],
            "programme_type": "s4hana",
        },
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    journey_id = response.get_json()["data"]["journey_id"]

    journey = db_session.get(ArchitectureJourney, journey_id)
    assert journey.programme_type == "s4hana"


def test_untyped_journey_still_works_with_no_programme_type_in_the_body(
    app, db_session, ea_user, login_as
):
    """Existing untyped-journey behaviour is unchanged."""
    from app.models.architecture_journey import ArchitectureJourney

    client = app.test_client()
    login_as(client, ea_user)

    response = client.post(
        "/architecture-journey/start-architecture",
        json={
            "title": "Untyped journey",
            "intent": "portfolio_change",
            "selected_layers": ["motivation"],
        },
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    journey_id = response.get_json()["data"]["journey_id"]
    journey = db_session.get(ArchitectureJourney, journey_id)
    assert journey.programme_type is None
