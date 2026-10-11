"""Pushing a solution to a DevOps connector uses the caller's own connector only.

The route took a ``connector_id`` from the request body and loaded the connector with no
organisation check, then pushed with that connector's organisation, so the stored token of
whichever organisation owned the connector was used. A connector belonging to another
organisation must be refused exactly like a connector that does not exist.
"""

from __future__ import annotations

import uuid

PASSWORD = "test-password-123"


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Push {label} {suffix}", slug=f"push-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name="Administrator").first()
    suffix = uuid.uuid4().hex[:6]
    user = User(
        first_name="Push", last_name=f"User-{suffix}", email=f"push-{suffix}@example.test",
        password=PASSWORD, confirmed=True, organization_id=org.id, role=role,
        is_org_admin=True, is_platform_admin=False,
    )
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
    db_session.flush()
    return user


def _connector(db_session, org):
    from app.models.connector_config import DevOpsConnectorConfig

    config = DevOpsConnectorConfig(organization_id=org.id, provider="github", enabled=True)
    db_session.add(config)
    db_session.flush()
    return config


def _solution(db_session, org):
    from app.models.solution_models import Solution

    solution = Solution(name=f"Push solution {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(solution)
    db_session.flush()
    return solution


def _post(client, solution, connector_id):
    return client.post(
        f"/api/solutions/{solution.id}/push-to-devops",
        json={"provider": "github", "connector_id": connector_id},
    )


def _patch_push(monkeypatch):
    """Stand in for code generation and the push, recording which organisation was used."""
    from app.modules.codegen.services import fastapi_stub_generator
    from app.services.devops_push_service import DevOpsPushService

    calls = []
    monkeypatch.setattr(fastapi_stub_generator, "generate", lambda solution_id: {"main.py": "print()"})
    monkeypatch.setattr(
        DevOpsPushService, "push_to_github",
        lambda self, org_id, *a, **k: calls.append(org_id) or {"pr_url": "https://example.test/pr/1"},
    )
    return calls


def test_a_connector_from_another_organisation_is_refused_and_never_used(
    app, db_session, login_as, client, monkeypatch
):
    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    user = _user(db_session, org_a)
    solution = _solution(db_session, org_a)
    foreign = _connector(db_session, org_b)
    db_session.commit()
    calls = _patch_push(monkeypatch)

    login_as(client, user)
    resp = _post(client, solution, foreign.id)

    assert resp.status_code == 400
    assert calls == []


def test_the_callers_own_connector_still_works(app, db_session, login_as, client, monkeypatch):
    org_a = _org(db_session, "own")
    user = _user(db_session, org_a)
    solution = _solution(db_session, org_a)
    own = _connector(db_session, org_a)
    db_session.commit()
    calls = _patch_push(monkeypatch)

    login_as(client, user)
    resp = _post(client, solution, own.id)

    assert resp.status_code == 200
    assert calls == [org_a.id]
