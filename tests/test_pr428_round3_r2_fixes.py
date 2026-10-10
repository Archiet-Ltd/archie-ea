"""Red/green reproductions for PR 428 round 3 (R2-1 to R2-5).

Each test below reproduces the cloud review's exact attack shape -- a home-org
Administrator who merely accepted a read-only Viewer invitation into a victim
organisation and switched their session's active organisation into it via the
real ``/account/switch-organization`` flow -- and asserts the fix now refuses
them where the pre-fix code let them through. Reuses
``_attacker_switched_into_victim_org`` from
tests/test_admin_rbac_active_org_enforcement.py (the fixture this review's own
probes were run against) rather than hand-rolling a second copy, per this
repo's own "find what already exists" rule.

R2-6 (the fail-open codegen/solution-access helpers) has its own red/green
pair in tests/test_solution_codegen_access_agreement.py, next to the fixture
it corrects.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_admin_rbac_active_org_enforcement import (
    _attacker_switched_into_victim_org,
)


def test_r2_1_confirm_code_spec_refuses_a_viewer_of_the_active_org(
    app, db_session, make_org, client, login_as
):
    """The review's own reproduction: POST .../confirm-code-spec/<element>
    as a Viewer of the active org used to return 200 and write
    ``code_spec`` (``not current_user.is_admin`` -- a bound method
    reference, always truthy, so the guard never refused anyone). Must now
    return 403 and leave the element untouched.
    """
    from app.models.solution_models import Solution
    from app.models.solution_sad_models import SolutionAppElement
    from app.models.user import User

    attacker, home_org, victim_org = _attacker_switched_into_victim_org(
        db_session, make_org, client, login_as
    )

    other_user = User(
        email=f"r2-1-owner-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Victim",
        last_name="Owner",
        organization_id=victim_org.id,
        confirmed=True,
    )
    other_user.password = uuid.uuid4().hex
    db_session.add(other_user)
    db_session.flush()

    solution = Solution(
        name="R2-1 probe solution",
        organization_id=victim_org.id,
        created_by_id=other_user.id,
    )
    db_session.add(solution)
    db_session.flush()

    element = SolutionAppElement(
        solution_id=solution.id,
        element_type="application_component",
        name="R2-1 probe element",
    )
    db_session.add(element)
    db_session.flush()
    assert element.code_spec is None

    response = client.post(
        f"/solutions/api/{solution.id}/confirm-code-spec/{element.id}",
        json={"fields": [{"name": "pwned"}]},
    )

    assert response.status_code == 403, (
        f"expected 403, got {response.status_code}: {response.get_data(as_text=True)}"
    )
    db_session.refresh(element)
    assert element.code_spec is None, "the attacker's fields must not have been written"


def test_r2_2_ai_chat_admin_analytics_and_prompts_refuse_a_viewer_of_the_active_org(
    app, db_session, make_org, client, login_as
):
    """``_require_admin`` (app/modules/ai_chat/routes/chat_admin_routes.py)
    used to be ``hasattr(current_user, "is_admin") and current_user.is_admin``
    -- the same always-true bound-method bug. A Viewer of the active org
    used to read both endpoints with 200; must now get 403.
    """
    attacker, home_org, victim_org = _attacker_switched_into_victim_org(
        db_session, make_org, client, login_as
    )

    analytics = client.get("/ai-chat/admin/analytics/data")
    assert analytics.status_code == 403, (
        f"expected 403, got {analytics.status_code}: {analytics.get_data(as_text=True)}"
    )

    prompts = client.get("/ai-chat/admin/prompts/data")
    assert prompts.status_code == 403, (
        f"expected 403, got {prompts.status_code}: {prompts.get_data(as_text=True)}"
    )


def test_r2_3_global_seed_and_schedule_routes_require_a_platform_admin(
    app, db_session, make_org, client, login_as
):
    """All three routes gated only on ``current_user.is_admin()`` -- true
    for every self-registered org admin, no invitation needed -- and write
    or execute genuinely global state. A freshly self-registered org admin
    (not a platform admin) used to get 200 from all three; must now get 403
    from ``platform_admin_required``.
    """
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")

    own_org = make_org("r2-3-self-registered")
    org_admin = User(
        email=f"r2-3-org-admin-{uuid.uuid4().hex[:8]}@example.test",
        first_name="SelfRegistered",
        last_name="OrgAdmin",
        organization_id=own_org.id,
        confirmed=True,
        role=admin_role,
    )
    org_admin.password = uuid.uuid4().hex
    db_session.add(org_admin)
    db_session.flush()
    assert org_admin.is_admin() is True
    assert getattr(org_admin, "is_platform_admin", False) is False

    login_as(client, org_admin)

    seed_defaults = client.post("/api/ea-workflows/seed-defaults")
    assert seed_defaults.status_code == 403, (
        f"seed-defaults: expected 403, got {seed_defaults.status_code}"
    )

    run_due = client.post("/api/ea-workflows/run-due-schedules")
    assert run_due.status_code == 403, (
        f"run-due-schedules: expected 403, got {run_due.status_code}"
    )

    seed_frameworks = client.post("/industry-apqc/api/seed-frameworks")
    assert seed_frameworks.status_code == 403, (
        f"seed-frameworks: expected 403, got {seed_frameworks.status_code}"
    )


def test_r2_5_require_roles_and_role_required_literal_platform_admin_no_longer_bypasses(
    app, db_session, make_org, client, login_as
):
    """Surfaced by extending the url_map sweep's marker coverage (R2-5)
    itself: ``enterprise_role`` defaults to the literal string
    "platform_admin" for every legacy account ("existing users get full
    access", app/models/user.py), and two decorators trusted that raw
    column value directly once an instance listed "platform_admin" as an
    allowed role --

      * ``require_roles(..., "platform_admin")``
        (tech_radar.routes.classify) via the un-scrubbed "platform_admin"
        entry ``require_roles`` adds to ``user_roles`` from the raw column.
      * ``role_required(*DEFINING_ROLES)``
        (metamodel_property_routes.define, ``DEFINING_ROLES`` includes
        "platform_admin") via the bare
        ``current_user.enterprise_role not in roles`` membership test.

    Both let the switched Viewer-of-the-active-org attacker through
    regardless of their real (non-admin) standing, exactly the bug class
    this whole PR fixes -- just not caught before because nothing swept
    either route. Must now refuse with 403.
    """
    attacker, home_org, victim_org = _attacker_switched_into_victim_org(
        db_session, make_org, client, login_as
    )
    assert attacker.enterprise_role == "platform_admin", (
        "fixture setup: this reproduction only means something if the "
        "attacker still carries the legacy default persona"
    )

    classify = client.post("/technology/radar/classify", json={})
    assert classify.status_code == 403, (
        f"tech_radar.classify: expected 403, got {classify.status_code}: "
        f"{classify.get_data(as_text=True)}"
    )

    define = client.post(
        "/metamodel/properties",
        data={
            "archimate_type": "ApplicationComponent",
            "display_name": "R2-5 probe",
            "property_type": "text",
        },
    )
    assert define.status_code == 403, (
        f"metamodel_properties.define: expected 403, got {define.status_code}"
    )
