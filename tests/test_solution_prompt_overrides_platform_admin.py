"""AIPromptTemplate carries no organization_id -- an override saved against a
solution-prompt key (e.g. "draft_architecture") replaces the prompt every
organisation's Architecture Journey uses, platform-wide. The write routes
(update, reset, rollback) were gated only by @admin_required
(Permission.ADMINISTER), which any organisation's own administrator role
holds -- the same defect class already fixed for scoring configurations
(test_scoring_configuration_platform_admin.py), feature flags, and
sidebar/editor content. A tenant administrator could silently rewrite the
AI prompt every tenant's solution drafting uses.

app.modules.admin.routes.solution_prompt_admin is deliberately a separate
blueprint from admin_routes.py (its own module docstring: "to avoid being
overwritten by deploys to admin_routes.py") -- these tests exercise it, the
one actually registered in production.
"""
from __future__ import annotations

import uuid

import pytest

_PROMPT_KEY = "draft_architecture"


def _user(db_session, org, *, platform=False):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(email=f"sp-{uuid.uuid4().hex[:6]}@example.test", first_name="Prompt", last_name="Tester",
                organization_id=org.id, confirmed=True, role=role)
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _login(db_session, client, login_as, user_id):
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


def _world(db_session, make_org):
    # This suite shares a long-lived fallback test database (no TEST_DATABASE_URL
    # isolation per run -- see app/config.py's own warning) -- clear any override
    # a previous run left behind so each test starts from a known state.
    from app.models.ai_service import AIPromptTemplate, AIPromptTemplateVersion

    override_name = f"solution_prompt_{_PROMPT_KEY}"
    AIPromptTemplateVersion.query.filter_by(template_name=override_name).delete()
    AIPromptTemplate.query.filter_by(name=override_name).delete()

    # Two organisations: the platform admin sits in org A (the override's
    # creator), the refused tenant admin sits in org B -- so a pass here cannot
    # be explained by same-org membership, only by the is_platform_admin gate.
    org_a = make_org("solution-prompt-a")
    org_b = make_org("solution-prompt-b")
    tenant = _user(db_session, org_b)
    platform = _user(db_session, org_a, platform=True)
    db_session.commit()
    return tenant.id, platform.id


def _override_exists(prompt_key):
    from app.models.ai_service import AIPromptTemplate

    return AIPromptTemplate.query.filter_by(name=f"solution_prompt_{prompt_key}").first() is not None


def test_a_tenant_administrator_cannot_update_a_solution_prompt_override(
    app, db_session, make_org, client, login_as
):
    tenant_id, _platform = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_id)
    response = client.post(
        f"/admin/solution-prompts/{_PROMPT_KEY}/update",
        json={"prompt_text": "attacker-controlled prompt"},
    )

    assert response.status_code == 403
    assert _override_exists(_PROMPT_KEY) is False


def test_a_tenant_administrator_cannot_reset_a_solution_prompt_override(
    app, db_session, make_org, client, login_as
):
    tenant_id, platform_id = _world(db_session, make_org)

    _login(db_session, client, login_as, platform_id)
    created = client.post(
        f"/admin/solution-prompts/{_PROMPT_KEY}/update",
        json={"prompt_text": "platform-admin-set prompt"},
    )
    assert created.status_code == 200

    _login(db_session, client, login_as, tenant_id)
    response = client.post(f"/admin/solution-prompts/{_PROMPT_KEY}/reset")

    assert response.status_code == 403
    assert _override_exists(_PROMPT_KEY) is True


def test_a_tenant_administrator_cannot_rollback_a_solution_prompt_override(
    app, db_session, make_org, client, login_as
):
    tenant_id, platform_id = _world(db_session, make_org)

    _login(db_session, client, login_as, platform_id)
    client.post(f"/admin/solution-prompts/{_PROMPT_KEY}/update", json={"prompt_text": "v1"})
    client.post(f"/admin/solution-prompts/{_PROMPT_KEY}/update", json={"prompt_text": "v2"})

    _login(db_session, client, login_as, tenant_id)
    response = client.post(f"/admin/solution-prompts/{_PROMPT_KEY}/rollback/1")

    assert response.status_code == 403


def test_a_platform_administrator_can_still_update_a_solution_prompt_override(
    app, db_session, make_org, client, login_as
):
    _tenant, platform_id = _world(db_session, make_org)

    _login(db_session, client, login_as, platform_id)
    response = client.post(
        f"/admin/solution-prompts/{_PROMPT_KEY}/update",
        json={"prompt_text": "a legitimate platform-admin override"},
    )

    assert response.status_code == 200
    assert _override_exists(_PROMPT_KEY) is True


# --- Direct-handler proof for the solution_prompt_admin blueprint itself ---
#
# PR 307 final-check-v3 (lead ruling, note 631): the duplicate-route cleanup
# (unregistering solution_prompt_admin_bp) is out of scope for this PR --
# admin_bp_v2 only serves /admin/solution-prompts when its guardrail flag
# registers it, so removing the other copy could drop the page entirely in a
# deployment without the guardrail. solution_prompt_admin_bp stays
# registered, and these tests call its own view functions directly through
# app.view_functions, independent of which blueprint's rule wins the URL map
# in this environment -- they fail against an admin_required handler and
# pass against platform_admin_required, regardless of registration order.


def _call_view(app, db_session, login_as, user_id, endpoint, method="POST", path="/",
                json_body=None, **view_kwargs):
    from flask_login import login_user

    from app.models.user import User

    view = app.view_functions[endpoint]
    db_session.expunge_all()
    user = db_session.get(User, user_id)

    with app.test_request_context(
        path, method=method, content_type="application/json", json=json_body or {}
    ):
        login_user(user)
        return view(**view_kwargs)


def _status(response):
    return response[1] if isinstance(response, tuple) else response.status_code


def test_solution_prompt_admin_update_handler_refuses_a_tenant_administrator(
    app, db_session, make_org, client, login_as
):
    tenant_id, _platform_id = _world(db_session, make_org)

    response = _call_view(
        app, db_session, login_as, tenant_id,
        "solution_prompt_admin.solution_prompt_update",
        prompt_key=_PROMPT_KEY,
    )

    assert _status(response) == 403
    assert _override_exists(_PROMPT_KEY) is False


def test_solution_prompt_admin_update_handler_allows_a_platform_administrator(
    app, db_session, make_org, client, login_as
):
    _tenant_id, platform_id = _world(db_session, make_org)

    response = _call_view(
        app, db_session, login_as, platform_id,
        "solution_prompt_admin.solution_prompt_update",
        json_body={"prompt_text": "a legitimate platform-admin override"},
        prompt_key=_PROMPT_KEY,
    )

    assert _status(response) == 200
    assert _override_exists(_PROMPT_KEY) is True


def test_solution_prompt_admin_reset_handler_refuses_a_tenant_administrator(
    app, db_session, make_org, client, login_as
):
    tenant_id, platform_id = _world(db_session, make_org)

    _call_view(
        app, db_session, login_as, platform_id,
        "solution_prompt_admin.solution_prompt_update",
        json_body={"prompt_text": "v1"},
        prompt_key=_PROMPT_KEY,
    )

    response = _call_view(
        app, db_session, login_as, tenant_id,
        "solution_prompt_admin.solution_prompt_reset",
        prompt_key=_PROMPT_KEY,
    )

    assert _status(response) == 403
    assert _override_exists(_PROMPT_KEY) is True


def test_solution_prompt_admin_rollback_handler_refuses_a_tenant_administrator(
    app, db_session, make_org, client, login_as
):
    tenant_id, platform_id = _world(db_session, make_org)

    _call_view(
        app, db_session, login_as, platform_id,
        "solution_prompt_admin.solution_prompt_update",
        json_body={"prompt_text": "v1"},
        prompt_key=_PROMPT_KEY,
    )
    _call_view(
        app, db_session, login_as, platform_id,
        "solution_prompt_admin.solution_prompt_update",
        json_body={"prompt_text": "v2"},
        prompt_key=_PROMPT_KEY,
    )

    response = _call_view(
        app, db_session, login_as, tenant_id,
        "solution_prompt_admin.solution_prompt_rollback",
        prompt_key=_PROMPT_KEY, version=1,
    )

    assert _status(response) == 403
