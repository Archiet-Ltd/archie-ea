"""AI-chat ``create_programme`` tool.

Wraps the exact entry point the /solutions/new-programme wizard's POST
handler uses (ProgrammeSetupService.create_business_first_programme ->
TransformationProgrammeService.create_programme), with an equivalent
ActorContext built from the acting user's real roles, so the tool can never
create a programme the human wizard would refuse and can never be more
permissive. can_create_programme is checked at the door first (audit F-04).
"""

from __future__ import annotations

import uuid

import pytest

from app.modules.ai_chat.tools.executor import ToolExecutor
from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME


def _make_user(db_session, org_id, enterprise_role=None):
    from app.models.user import User

    user = User(
        email=f"prog-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_id,
        enterprise_role=enterprise_role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_tool_registered_with_correct_flags():
    schema = TOOL_SCHEMA_BY_NAME.get("create_programme")
    assert schema is not None, "create_programme is not registered"
    assert schema["mutates"] is True
    assert schema["tier"] == "approve"


def test_tool_dispatches_via_getattr():
    ex = ToolExecutor(user_id=1)
    assert callable(getattr(ex, "_tool_create_programme", None))


def test_create_programme_refuses_a_user_without_create_role(db_session, make_org, tenant_ctx):
    org = make_org("prog-noauth")
    user = _make_user(db_session, org.id, enterprise_role="solution_architect")

    with tenant_ctx(org.id):
        ex = ToolExecutor(user_id=user.id)
        result = ex._tool_create_programme({
            "name": "Should not be created",
            "objective": "Refused before any payload is built",
            "outcome_statement": "N/A",
            "outcome_direction": "increase",
            "metric_name": "n/a",
            "metric_unit": "n/a",
        })

    assert result["success"] is False
    assert "programme" in result["error"].lower() or "authoris" in result["error"].lower()


def test_create_programme_requires_the_core_fields(db_session, make_org, tenant_ctx):
    org = make_org("prog-missing")
    user = _make_user(db_session, org.id, enterprise_role="enterprise_architect")

    with tenant_ctx(org.id):
        ex = ToolExecutor(user_id=user.id)
        result = ex._tool_create_programme({"name": "Incomplete"})

    assert result["success"] is False


# --------------------------------------------------------------------------
# Success path, real commit
#
# create_programme's validate_intake (app/modules/transformation_room/
# programme_service.py) opens its own Session(db.engine) to confirm the owner
# user exists - a genuinely separate database connection from db_session's
# savepoint-wrapped transaction, which that connection cannot see. This test
# therefore follows the same real-commit-and-clean-up pattern already used by
# tests/test_transformation_programme_service.py for the same reason, rather
# than the shared db_session fixture (CLAUDE.md's "write new tests against the
# shared fixtures" guidance is for ordinary model/route tests; this module's
# own tests document the same exception).
# --------------------------------------------------------------------------


@pytest.fixture
def real_committed_org_and_user(app, _schema):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    suffix = uuid.uuid4().hex[:12]
    with app.app_context():
        db.session.remove()
        org = Organization(name=f"AI Tool Programme Org {suffix}", slug=f"ai-tool-programme-{suffix}")
        db.session.add(org)
        db.session.flush()
        user = User(
            email=f"ai-tool-prog-{suffix}@example.test",
            organization_id=org.id,
            confirmed=True,
            enterprise_role="enterprise_architect",
        )
        db.session.add(user)
        db.session.commit()
        org_id, user_id = org.id, user.id
        db.session.remove()
        try:
            yield org_id, user_id
        finally:
            db.session.remove()
            from app.models.organization import Organization
            from app.services.org_deletion_service import OrganizationDeletionService

            if db.session.get(Organization, org_id) is not None:
                OrganizationDeletionService.delete_organization(org_id)
            db.session.remove()


def test_create_programme_creates_a_real_programme(app, tenant_ctx, real_committed_org_and_user):
    from app import db
    from app.models.strategic import StrategicInitiative

    org_id, user_id = real_committed_org_and_user
    name = f"AI Tool Programme {uuid.uuid4().hex[:8]}"

    with app.app_context():
        db.session.remove()
        with tenant_ctx(org_id):
            ex = ToolExecutor(user_id=user_id)
            result = ex._tool_create_programme({
                "name": name,
                "objective": "Rationalise duplicate CRM instances across regions.",
                "workstream_type": "application_rationalisation",
                "business_units": ["Sales", "Support"],
                "target_date_unavailable_reason": "Not yet scheduled",
                "outcome_statement": "One CRM platform serving every region.",
                "outcome_direction": "decrease",
                "metric_name": "Number of CRM instances",
                "metric_unit": "instances",
                "baseline_value": 4,
                "target_value": 1,
            })

        assert result["success"] is True, result
        programme_id = result["result"]["programme_id"]
        assert programme_id is not None
        assert result["result"]["workstream_id"] is not None
        assert result["result"]["redirect_url"] == (
            f"/solutions/programmes/{programme_id}/workstreams/{result['result']['workstream_id']}/objective"
        )

        db.session.remove()
        initiative = StrategicInitiative.query.filter_by(id=programme_id).first()
        assert initiative is not None
        assert initiative.name == name
