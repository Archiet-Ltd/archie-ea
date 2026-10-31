"""Executable coverage for high-value destinations exposed in the sidebar."""

import uuid

import pytest


@pytest.fixture
def architect_client(client, db_session, login_as, make_org):
    from app.models.user import User

    org = make_org("navigation-coverage")
    user = User(
        email=f"navigation-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Navigation",
        last_name="Architect",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    login_as(client, user)
    return client


@pytest.mark.parametrize(
    "path, expected_marker",
    [
        ("/adm-kanban/", "ADM"),
        ("/architecture/traceability", "Traceability"),
    ],
)
def test_architect_sidebar_destination_loads(architect_client, path, expected_marker):
    from tests._wave_aware import answers_early_access

    if answers_early_access(architect_client, path):
        return  # the wave closes this page, so no sidebar offers it (see test_wave_links.py)

    response = architect_client.get(path)

    assert response.status_code == 200, response.get_data(as_text=True)
    assert expected_marker in response.get_data(as_text=True)
