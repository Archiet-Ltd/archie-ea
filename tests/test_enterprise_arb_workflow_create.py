"""Starting an architecture review workflow through the enterprise API works.

POST /api/v2/enterprise/workflows/arb used to answer 500 on every call because
the route called a creator that does not exist. It now calls the one creator the
architecture review pages use, so the review is stored for the caller's
organisation and is invisible to every other organisation.
"""

from tests.test_permission_enterprise_api import ARB, _org, _user


def _body(title):
    return {
        "title": title,
        "description": "Review of the digital transformation initiatives",
        "reviewers": [1],
    }


def _stored(db_session, title):
    from app.models.architecture_review_board import ARBReviewItem

    db_session.expire_all()
    return ARBReviewItem.query.filter_by(title=title).first()


def test_administrator_creates_a_stored_workflow(app, db_session, login_as, client):
    org = _org(db_session, "arb-a")
    admin = _user(db_session, org, "org_admin", platform_role="Administrator")
    db_session.commit()
    login_as(client, admin)

    response = client.post(ARB, json=_body("ARB create works A"))

    assert response.status_code == 201, response.get_data(as_text=True)
    data = response.get_json()["data"]
    assert data["id"]
    row = _stored(db_session, "ARB create works A")
    assert row is not None
    assert row.id == data["id"]
    assert row.organization_id == org.id
    assert row.submitter_id == admin.id


def test_viewer_gets_403_and_nothing_is_stored(app, db_session, login_as, client):
    org = _org(db_session, "arb-v")
    viewer = _user(db_session, org, "viewer")
    db_session.commit()
    login_as(client, viewer)

    response = client.post(ARB, json=_body("ARB create refused V"))

    assert response.status_code == 403
    assert _stored(db_session, "ARB create refused V") is None


def test_another_organisation_cannot_read_the_workflow(app, db_session, login_as, client):
    org_a = _org(db_session, "arb-iso-a")
    org_b = _org(db_session, "arb-iso-b")
    admin_a = _user(db_session, org_a, "org_admin", platform_role="Administrator")
    admin_b = _user(db_session, org_b, "org_admin", platform_role="Administrator")
    db_session.commit()

    login_as(client, admin_a)
    assert client.post(ARB, json=_body("ARB isolation A only")).status_code == 201
    seen_by_a = client.get("/arb/api/reviews").get_json()["reviews"]
    assert "ARB isolation A only" in [r["title"] for r in seen_by_a]

    login_as(client, admin_b)
    seen_by_b = client.get("/arb/api/reviews").get_json()["reviews"]
    assert "ARB isolation A only" not in [r["title"] for r in seen_by_b]
