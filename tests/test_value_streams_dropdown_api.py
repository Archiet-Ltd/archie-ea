"""The vendor-analysis wizard's value-stream dropdown, and the API it reads.

app/static/js/application_mgmt/vendor_analysis_detail.js's
loadValueStreamOptions() calls GET /dashboard/api/value-streams, with a
domain_id query param only once a domain filter has been chosen — its
initial call, on page load, carries neither domain_id nor domain_code. The
endpoint (app/application_mgmt/vendor_analysis_routes.py) treated "no
domain parameter" the same as "no domain found for that parameter" and
answered 404 for it, so the wizard's first, unfiltered load of this data
always failed.
"""

import uuid

import pytest


@pytest.fixture
def vs_api_org(make_org, db_session):
    return make_org("vsapi")


def _make_user(db_session, org):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Architect").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Architect").first()

    user = User(
        email=f"vsapi-{uuid.uuid4().hex[:8]}@example.com",
        first_name="VS",
        last_name="Api",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex  # generated, not a real credential
    db_session.add(user)
    db_session.flush()
    return user


def test_value_streams_endpoint_with_no_domain_filter_answers_200(
    app, db_session, vs_api_org, login_as
):
    """The page's own initial, unfiltered request must not 404."""
    user = _make_user(db_session, vs_api_org)
    db_session.commit()
    client = app.test_client()
    login_as(client, user)

    resp = client.get("/dashboard/api/value-streams")
    assert resp.status_code == 200, (
        f"the unfiltered value-streams request answered {resp.status_code}, "
        "not 200 — this is the request the wizard makes before any domain "
        "filter is chosen"
    )
    assert resp.get_json() == []


def test_value_streams_endpoint_with_unknown_domain_still_404s(
    app, db_session, vs_api_org, login_as
):
    """A genuine lookup failure (a domain_id that names nothing) must stay a
    404 — the fix narrows the "domain not found" branch to when a domain
    filter really was given, it does not delete that branch.
    """
    user = _make_user(db_session, vs_api_org)
    db_session.commit()
    client = app.test_client()
    login_as(client, user)

    resp = client.get("/dashboard/api/value-streams?domain_id=999999999")
    assert resp.status_code == 404
    assert "error" in resp.get_json()
