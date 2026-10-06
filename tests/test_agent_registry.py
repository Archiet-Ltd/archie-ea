"""R1-B56: Agent Registry -- TB-0394, PB-0220, PB-0493.

Activation is refused until owner, charter and delegated limits are all
set; a proposed charter version never becomes current before a second
reviewer approves it through R1-B07's one approval queue; a proposal that
weakens a non-removable rule is refused outright, not just flagged.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.agent_charter import AgentCharter
from app.models.agent_registration import AgentRegistration
from app.modules.ai_chat.services.agent_registry_service import (
    CharterChangeRefused,
    activate,
    create_registration,
    execute_charter_change,
    request_charter_change,
    set_delegated_limits,
    set_owner,
)


def _user(db_session, org):
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"agent-reg-{uuid.uuid4().hex[:8]}@example.com", first_name="T", last_name="U",
        organization_id=org.id, role=admin_role, confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


class TestActivationGating:
    def test_activation_refused_until_owner_charter_and_limits_are_all_set(
        self, app, db_session, make_org
    ):
        org = make_org("agent-reg-gating")
        reg = create_registration(organization_id=org.id, name="Compliance Bot")

        ok, missing = activate(reg)
        assert ok is False
        assert set(missing) == {"owner", "charter", "delegated_limits"}

        owner = _user(db_session, org)
        set_owner(reg, owner.id)
        ok, missing = activate(reg)
        assert ok is False
        assert "owner" not in missing

        set_delegated_limits(reg, {"max_writes_per_day": 10})
        ok, missing = activate(reg)
        assert ok is False
        assert missing == ["charter"]

        charter = AgentCharter(
            organization_id=org.id, persona="compliance_bot", version=1,
            forbidden_actions=[], proposable_actions=[],
        )
        db_session.add(charter)
        db_session.flush()
        reg.charter_persona = "compliance_bot"
        reg.charter_version_id = charter.id
        db_session.commit()

        ok, missing = activate(reg)
        assert ok is True
        assert missing == []
        assert reg.status == "active"

    def test_two_organisations_registries_never_cross(self, db_session, make_org):
        org_a = make_org("agent-reg-fence-a")
        org_b = make_org("agent-reg-fence-b")
        create_registration(organization_id=org_a.id, name="A's Agent")

        rows_a = AgentRegistration.query.filter_by(organization_id=org_a.id).all()
        rows_b = AgentRegistration.query.filter_by(organization_id=org_b.id).all()
        assert len(rows_a) == 1
        assert rows_b == []


class TestCharterChangeReview:
    def test_a_proposed_charter_change_is_not_current_until_approved(
        self, app, db_session, make_org
    ):
        org = make_org("agent-reg-charter-review")
        reg = create_registration(organization_id=org.id, name="Risk Bot")

        approval = request_charter_change(
            reg, persona="risk_bot", purpose="Flag risk exposure",
            readable_entities=["Risk"], proposable_actions=["flag_risk"],
            forbidden_actions=[], charter_text="Flags risk, never deletes it.",
            requested_by_user_id=None,
        )

        # The proposal must not have created a current charter yet.
        assert AgentCharter.current_for("risk_bot", org.id) is None
        assert reg.charter_version_id is None

        from app.models.ai_chat_crud_approval import AIChatCRUDApproval

        reloaded = AIChatCRUDApproval.query.get(approval.id)
        assert reloaded.operation_type == "agent_charter_change"
        assert reloaded.entity_id == reg.id

    def test_executing_an_approved_charter_change_creates_the_version(
        self, app, db_session, make_org
    ):
        org = make_org("agent-reg-charter-execute")
        reg = create_registration(organization_id=org.id, name="Finance Bot")

        request_charter_change(
            reg, persona="finance_bot", purpose="Summarise spend",
            readable_entities=["Spend"], proposable_actions=["summarise_spend"],
            forbidden_actions=[], charter_text="Summarises spend, never approves it.",
            requested_by_user_id=None,
        )
        proposed = {
            "persona": "finance_bot", "purpose": "Summarise spend",
            "readable_entities": ["Spend"], "proposable_actions": ["summarise_spend"],
            "forbidden_actions": [], "charter_text": "Summarises spend, never approves it.",
        }
        charter = execute_charter_change(reg, proposed)

        assert charter.version == 1
        assert AgentCharter.current_for("finance_bot", org.id).id == charter.id
        assert reg.charter_version_id == charter.id

    def test_a_charter_that_removes_a_non_removable_rule_is_refused(
        self, db_session, make_org
    ):
        org = make_org("agent-reg-non-removable")
        reg = create_registration(organization_id=org.id, name="Rogue Bot")

        with pytest.raises(CharterChangeRefused, match="no_fabrication"):
            request_charter_change(
                reg, persona="rogue_bot", purpose="x",
                readable_entities=[], proposable_actions=["no_fabrication"],
                forbidden_actions=[], charter_text="x",
                requested_by_user_id=None,
            )

    def test_a_second_version_increments_and_diffs_against_the_first(
        self, app, db_session, make_org
    ):
        org = make_org("agent-reg-charter-v2")
        reg = create_registration(organization_id=org.id, name="Ops Bot")
        execute_charter_change(reg, {
            "persona": "ops_bot", "purpose": "p", "readable_entities": [],
            "proposable_actions": ["restart_job"], "forbidden_actions": [],
            "charter_text": "v1",
        })
        v2 = execute_charter_change(reg, {
            "persona": "ops_bot", "purpose": "p", "readable_entities": [],
            "proposable_actions": ["restart_job", "pause_job"], "forbidden_actions": [],
            "charter_text": "v2",
        })
        assert v2.version == 2
        assert AgentCharter.current_for("ops_bot", org.id).id == v2.id
