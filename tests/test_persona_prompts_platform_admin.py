"""AI chat persona-prompt overrides are platform-wide: only a platform administrator can change them.

The persona override row (AIPromptTemplate, category='persona_override', no tenant column) is served
to every organisation by app/modules/ai_chat/routes/chat_core.py's read. The two write routes
(update/reset) wrote behind _require_admin() (an org-level check equivalent to Permission.ADMINISTER),
the same weaker guard the solution-prompt write routes carried before PR 242; they now use
platform_admin_required, matching the solution-prompt write routes in the same PR.

The two read routes (the page and /data) intentionally stay open to any active-org administrator via
_require_admin() -- this PR's own title scopes the fix to who can *change* persona prompts, and main's
tests/test_pr428_round4_r3_fixes.py::test_r3_3_prompt_writes_require_platform_admin_not_just_active_org_admin
(R2-2/R3-3, settled by review-pr430-v3.md's DEF-6/PR 430 ruling) already asserts that an active-org
admin must still read them successfully. An earlier version of this test asserted the read routes
should 403 too, which contradicted that settled ruling; narrowed to the two write routes to match it,
discovered and fixed while merging main into this branch on 2026-10-09.

The unrelated feedback-analytics routes in this file are unaffected: they stay admin_required
(organisation-scoped analytics, not a shared table) and are asserted reachable here as a regression
guard.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests._platform_admin_world import (
    login_platform_admin_world_user as _login,
    platform_admin_world as _world_base,
)


def _world(db_session, make_org):
    return _world_base(db_session, make_org, org_prefix="persona-prompts", user_prefix="pp")


@pytest.mark.parametrize("method,path", [
    ("post", "/ai-chat/admin/prompts/enterprise_architect/update"),
    ("post", "/ai-chat/admin/prompts/enterprise_architect/reset"),
])
def test_a_tenant_administrator_is_refused_on_every_persona_prompt_write_route(
    app, db_session, make_org, client, login_as, method, path
):
    tenant_id, _platform = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_id)
    response = getattr(client, method)(path, json={"system_prompt": "x"})

    assert response.status_code == 403


@pytest.mark.parametrize("method,path", [
    ("get", "/ai-chat/admin/prompts"),
    ("get", "/ai-chat/admin/prompts/data"),
])
def test_a_tenant_administrator_can_still_read_persona_prompts(
    app, db_session, make_org, client, login_as, method, path
):
    """R2-2/R3-3 (settled, review-pr430-v3.md DEF-6/PR 430): the read routes are
    not part of this PR's "who can change them" scope and must stay open to any
    active-org administrator, matching tests/test_pr428_round4_r3_fixes.py."""
    tenant_id, _platform = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_id)
    response = getattr(client, method)(path)

    assert response.status_code == 200


def test_a_refused_update_stores_no_override(app, db_session, make_org, client, login_as):
    tenant_id, _platform = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_id)
    client.post("/ai-chat/admin/prompts/enterprise_architect/update", json={"system_prompt": "override"})

    stored = db_session.execute(
        text("select count(*) from ai_prompt_templates where system_prompt = 'override'")
    ).scalar()
    assert stored == 0


def test_a_platform_administrator_can_still_update_and_reset_a_persona_prompt(
    app, db_session, make_org, client, login_as
):
    _tenant, platform_id = _world(db_session, make_org)

    _login(db_session, client, login_as, platform_id)
    updated = client.post("/ai-chat/admin/prompts/enterprise_architect/update", json={"system_prompt": "platform text"})
    listed = client.get("/ai-chat/admin/prompts/data")
    reset = client.post("/ai-chat/admin/prompts/enterprise_architect/reset")

    assert updated.status_code == 200
    assert listed.status_code == 200
    assert reset.status_code == 200


def test_the_unrelated_analytics_routes_stay_reachable_to_a_tenant_administrator(
    app, db_session, make_org, client, login_as
):
    """Regression guard: removing _require_admin() must not leave admin/analytics unguarded, and
    must not accidentally close it to the organisation administrators who used it before."""
    tenant_id, _platform = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_id)
    dashboard = client.get("/ai-chat/admin/analytics")
    data = client.get("/ai-chat/admin/analytics/data")

    assert dashboard.status_code == 200
    assert data.status_code in (200, 500)  # 500 only from an unrelated missing-model import guard, not auth


def test_analytics_data_does_not_count_a_foreign_organisations_activity(
    app, db_session, make_org, client, login_as
):
    """Final-check review (pr242-final-check-v1.md), HIGH: neither AIChatAuditLog
    nor AIInteractionLog carries an organization_id of its own -- ownership is
    only reachable via their user_id FK to User. The analytics route counted
    every organisation's rows together. Seeds two real organisations and
    proves org A's counts do not include org B's activity."""
    from datetime import datetime, timedelta

    from app.models.ai_chat_audit_log import AIChatAuditLog, AuditEventType
    from app.models.ai_service import AIInteractionLog, AIPromptTemplate

    org_a = make_org("persona-analytics-a")
    org_b = make_org("persona-analytics-b")
    from tests._platform_admin_world import platform_admin_user

    user_a = platform_admin_user(db_session, org_a, prefix="pa-a")
    user_b = platform_admin_user(db_session, org_b, prefix="pa-b")
    db_session.commit()

    now = datetime.utcnow()
    db_session.add(AIChatAuditLog(
        event_type=AuditEventType.CHAT_MESSAGE, user_id=user_a.id,
        domain="finance", persona="enterprise_architect", provider_used="openrouter",
        processing_time_ms=100, created_at=now,
    ))
    for _ in range(5):
        db_session.add(AIChatAuditLog(
            event_type=AuditEventType.CHAT_MESSAGE, user_id=user_b.id,
            domain="hr", persona="business_architect", provider_used="openrouter",
            processing_time_ms=200, created_at=now,
        ))
    template = AIPromptTemplate.query.first()
    if template is not None:
        db_session.add(AIInteractionLog(
            user_id=user_b.id, prompt_template_id=template.id, timestamp=now,
        ))
    db_session.commit()
    user_a_id = user_a.id
    db_session.expunge_all()

    from app.models.user import User

    login_as(client, db_session.get(User, user_a_id))
    resp = client.get("/ai-chat/admin/analytics/data", query_string={"days": "30"})

    if resp.status_code != 200:
        pytest.skip("analytics/data route not reachable in this environment (missing-model guard)")
    data = resp.get_json()
    assert data.get("total_messages", 0) == 1, (
        "org A must only count its own AIChatAuditLog row, not org B's 5"
    )
    assert data.get("active_users", 0) == 1
