"""Direct-call tests for the solution-prompt AIPromptTemplate chokepoint.

``AIPromptTemplate`` carries no tenant column: a solution-prompt override
replaces that prompt for every organisation's Architecture Journey. Routing
every write through ``app.services.solution_prompt_override_service``
instead of letting each route tree build its own query/mutate logic is the
structural fix (ADR-0008) -- these tests exercise the module directly.
"""

from __future__ import annotations

import uuid

import pytest
from werkzeug.exceptions import Forbidden


def _key():
    """A unique prompt key per test: the service never validates membership
    in the real prompt catalogue, and the test database here is a
    long-lived fallback rather than a per-test transaction rollback, so a
    fixed key like "draft_architecture" accumulates rows and version
    numbers across test runs."""
    return f"probe_{uuid.uuid4().hex[:8]}"


def _user(db_session, org, *, platform=False):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(
        email=f"spo-{uuid.uuid4().hex[:6]}@example.test",
        first_name="Prompt", last_name="Tester",
        organization_id=org.id, confirmed=True, role=role,
    )
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def test_update_override_refuses_a_non_platform_admin(app, db_session, make_org):
    from app.services import solution_prompt_override_service as svc

    org = make_org("spo-refuse")
    tenant_admin = _user(db_session, org)
    db_session.commit()
    key = _key()

    with app.test_request_context():
        from flask_login import login_user
        login_user(db_session.get(type(tenant_admin), tenant_admin.id))

        with pytest.raises(Forbidden):
            svc.update_override(key, "desc", "new text")


def test_update_override_creates_then_versions_on_second_call(app, db_session, make_org):
    from app.models.ai_service import AIPromptTemplate, AIPromptTemplateVersion
    from app.services import solution_prompt_override_service as svc

    org = make_org("spo-create")
    platform_admin = _user(db_session, org, platform=True)
    db_session.commit()
    key = _key()

    with app.test_request_context():
        from flask_login import login_user
        login_user(db_session.get(type(platform_admin), platform_admin.id))

        override = svc.update_override(key, "desc", "v1 text")
        assert override.version == 1
        assert override.system_prompt == "v1 text"

        override = svc.update_override(key, "desc", "v2 text")
        assert override.version == 2
        assert override.system_prompt == "v2 text"

    name = svc.override_key(key)
    history = AIPromptTemplateVersion.query.filter_by(template_name=name).all()
    assert len(history) == 1
    assert history[0].system_prompt == "v1 text"
    assert AIPromptTemplate.query.filter_by(name=name).count() == 1


def test_reset_override_refuses_a_non_platform_admin(app, db_session, make_org):
    from app.services import solution_prompt_override_service as svc

    org = make_org("spo-reset-refuse")
    platform_admin = _user(db_session, org, platform=True)
    tenant_admin = _user(db_session, org)
    db_session.commit()
    key = _key()

    with app.test_request_context():
        from flask_login import login_user
        login_user(db_session.get(type(platform_admin), platform_admin.id))
        svc.update_override(key, "desc", "v1 text")

    with app.test_request_context():
        from flask_login import login_user
        login_user(db_session.get(type(tenant_admin), tenant_admin.id))
        with pytest.raises(Forbidden):
            svc.reset_override(key)


def test_reset_override_deletes_the_live_row_and_keeps_history(app, db_session, make_org):
    from app.models.ai_service import AIPromptTemplate, AIPromptTemplateVersion
    from app.services import solution_prompt_override_service as svc

    org = make_org("spo-reset")
    platform_admin = _user(db_session, org, platform=True)
    db_session.commit()
    key = _key()

    with app.test_request_context():
        from flask_login import login_user
        login_user(db_session.get(type(platform_admin), platform_admin.id))
        svc.update_override(key, "desc", "v1 text")
        svc.reset_override(key)

    name = svc.override_key(key)
    assert AIPromptTemplate.query.filter_by(name=name).count() == 0
    history = AIPromptTemplateVersion.query.filter_by(template_name=name).all()
    assert len(history) == 1
    assert history[0].change_type == "reset"


def test_reset_override_is_a_no_op_when_none_exists(app, db_session, make_org):
    from app.services import solution_prompt_override_service as svc

    org = make_org("spo-reset-noop")
    platform_admin = _user(db_session, org, platform=True)
    db_session.commit()
    key = _key()

    with app.test_request_context():
        from flask_login import login_user
        login_user(db_session.get(type(platform_admin), platform_admin.id))
        svc.reset_override(key)  # must not raise


def test_rollback_override_refuses_a_non_platform_admin(app, db_session, make_org):
    from app.services import solution_prompt_override_service as svc

    org = make_org("spo-rollback-refuse")
    platform_admin = _user(db_session, org, platform=True)
    tenant_admin = _user(db_session, org)
    db_session.commit()
    key = _key()

    with app.test_request_context():
        from flask_login import login_user
        login_user(db_session.get(type(platform_admin), platform_admin.id))
        svc.update_override(key, "desc", "v1 text")

    with app.test_request_context():
        from flask_login import login_user
        login_user(db_session.get(type(tenant_admin), tenant_admin.id))
        with pytest.raises(Forbidden):
            svc.rollback_override(key, 1, "desc")


def test_rollback_override_returns_none_for_an_unknown_version(app, db_session, make_org):
    from app.services import solution_prompt_override_service as svc

    org = make_org("spo-rollback-none")
    platform_admin = _user(db_session, org, platform=True)
    db_session.commit()
    key = _key()

    with app.test_request_context():
        from flask_login import login_user
        login_user(db_session.get(type(platform_admin), platform_admin.id))
        svc.update_override(key, "desc", "v1 text")

        result = svc.rollback_override(key, 99, "desc")
        assert result is None


def test_rollback_override_restores_a_prior_versions_text_with_a_new_version_number(app, db_session, make_org):
    from app.services import solution_prompt_override_service as svc

    org = make_org("spo-rollback")
    platform_admin = _user(db_session, org, platform=True)
    db_session.commit()
    key = _key()

    with app.test_request_context():
        from flask_login import login_user
        login_user(db_session.get(type(platform_admin), platform_admin.id))
        svc.update_override(key, "desc", "v1 text")
        svc.update_override(key, "desc", "v2 text")

        restored = svc.rollback_override(key, 1, "desc")
        assert restored.system_prompt == "v1 text"
        assert restored.version == 3


def test_rollback_override_creates_a_fresh_override_when_none_is_live(app, db_session, make_org):
    """The live row can be absent (e.g. reset then rollback) while history
    still has the version being restored."""
    from app.services import solution_prompt_override_service as svc

    org = make_org("spo-rollback-recreate")
    platform_admin = _user(db_session, org, platform=True)
    db_session.commit()
    key = _key()

    with app.test_request_context():
        from flask_login import login_user
        login_user(db_session.get(type(platform_admin), platform_admin.id))
        svc.update_override(key, "desc", "v1 text")
        svc.reset_override(key)

        restored = svc.rollback_override(key, 1, "desc")
        assert restored is not None
        assert restored.system_prompt == "v1 text"
        assert restored.version == 1


def test_two_organisation_isolation(app, db_session, make_org):
    """A platform admin in org A can write; a tenant admin in org B cannot.

    ``AIPromptTemplate`` has no tenant column, so row-level tenant isolation
    does not apply. The defence is role-based: only platform admins may write.
    This test proves that a non-platform admin from a different organisation
    is still refused, confirming the check is not accidentally scoped to the
    caller's organisation.
    """
    from app.services import solution_prompt_override_service as svc

    org_a = make_org("spo-org-a")
    org_b = make_org("spo-org-b")
    platform_admin = _user(db_session, org_a, platform=True)
    tenant_admin = _user(db_session, org_b)  # tenant admin in org B, not platform
    db_session.commit()
    key = _key()

    with app.test_request_context():
        from flask_login import login_user
        login_user(db_session.get(type(platform_admin), platform_admin.id))
        svc.update_override(key, "desc", "v1 text")

    with app.test_request_context():
        from flask_login import login_user
        login_user(db_session.get(type(tenant_admin), tenant_admin.id))
        with pytest.raises(Forbidden):
            svc.update_override(key, "desc", "v2 text")
        with pytest.raises(Forbidden):
            svc.reset_override(key)
        with pytest.raises(Forbidden):
            svc.rollback_override(key, 1, "desc")

    with app.test_request_context():
        from flask_login import login_user
        login_user(db_session.get(type(platform_admin), platform_admin.id))
        svc.reset_override(key)  # cleanup
