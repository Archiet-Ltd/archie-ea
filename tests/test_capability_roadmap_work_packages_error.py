"""A failed work-packages query is an error, not a success with an empty list.

Two real organisations: the query is made to fail for one organisation only.
Signed into that organisation, the capability roadmap's work-packages endpoint
answers an error status and no fabricated count; signed into the other
organisation, the same endpoint still answers success with that organisation's
own packages. Uses the shared fixtures in tests/conftest.py.
"""

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label):
    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name="User").first()
    user = User(
        email=f"caproad-err-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Test",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
        role_id=role.id if role else None,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_failed_work_packages_query_answers_error_not_empty_success(
    db_session, make_org, client, login_as, monkeypatch
):
    from app.services import work_package_service

    org_a, org_b = make_org("caproad-err-a"), make_org("caproad-err-b")
    user_a = _user(db_session, org_a, "err-a")
    user_b = _user(db_session, org_b, "err-b")
    work_package_service.create_work_package(
        organization_id=org_b.id, name="Other org package")

    real_query_for = work_package_service.query_for

    def failing_query_for(org_id):
        if int(org_id) == org_a.id:
            raise RuntimeError("the work-packages query failed")
        return real_query_for(org_id)

    monkeypatch.setattr(work_package_service, "query_for", failing_query_for)

    login_as(client, user_a)
    failed = client.get("/capability-map/api/roadmap/work-packages")
    assert failed.status_code == 500, failed.status_code
    body = failed.get_json()
    assert body.get("success") is False, body
    assert "all_count" not in body, body

    login_as(client, user_b)
    ok = client.get("/capability-map/api/roadmap/work-packages?root_only=false")
    assert ok.status_code == 200, ok.status_code
    body_b = ok.get_json()
    assert body_b.get("success") is True, body_b
    assert body_b["all_count"] == 1, body_b
    assert [w["name"] for w in body_b["work_packages"]] == ["Other org package"], body_b
