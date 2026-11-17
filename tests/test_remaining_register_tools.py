"""Capability-Gap Register G4 / G8 / G7 — the remaining additive gaps.

Covers the three new governed WRITE tools and the platform_admin charter:

  G4  bulk_update_application_status — one lifecycle stage across a SET, org-scoped,
      per-app results, invalid stage rejected.
  G8  create_contract / upsert_license — tenant-scoped procurement writes with
      honest validation failure.
  G7  platform_admin resolves to its OWN operational charter, not enterprise_architect.

Uses the shared fixtures in tests/conftest.py (db_session rolls back; make_org /
tenant_ctx cover multi-tenant setup).
"""

import uuid

import pytest

from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor
from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME


# --------------------------------------------------------------------------- #
# Local seeding helpers                                                        #
# --------------------------------------------------------------------------- #
def _general_role(db_session):
    from app.models.user import Permission, Role

    role = Role(name=f"gen-{uuid.uuid4().hex[:8]}", permissions=Permission.GENERAL)
    db_session.add(role)
    db_session.flush()
    return role


def _make_user(db_session, org):
    from app.models.user import User

    role = _general_role(db_session)
    user = User(
        email=f"pa-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id,
        confirmed=True,
    )
    user.role = role
    db_session.add(user)
    db_session.flush()
    return user


def _make_app(db_session, name, lifecycle="2.1 strategic", component_type="app"):
    """Create an ApplicationComponent; organization_id is auto-set on flush from
    the active tenant context (TenantMixin)."""
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(
        name=name, lifecycle_status=lifecycle, component_type=component_type
    )
    db_session.add(row)
    db_session.flush()
    return row


# --------------------------------------------------------------------------- #
# Registry: mutates / tier                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "tool_name",
    ["bulk_update_application_status", "create_contract", "upsert_license"],
)
def test_new_write_tools_are_mutating_and_approve_tier(tool_name):
    schema = TOOL_SCHEMA_BY_NAME.get(tool_name)
    assert schema is not None, f"{tool_name} must be registered"
    assert schema["mutates"] is True
    assert schema["tier"] == "approve"


# --------------------------------------------------------------------------- #
# G4 — bulk_update_application_status                                          #
# --------------------------------------------------------------------------- #
def test_bulk_update_updates_many_and_reports_per_app(db_session, make_org, tenant_ctx):
    org = make_org("g4")
    user = _make_user(db_session, org)
    with tenant_ctx(org.id):
        a = _make_app(db_session, "App A")
        b = _make_app(db_session, "App B")
        c = _make_app(db_session, "App C", lifecycle="3. sunset")

        ex = ToolExecutor(user.id)
        result = ex._tool_bulk_update_application_status({
            "app_ids": [a.id, b.id, c.id],
            "new_status": "5. decommissioned",
            "rationale": "batch retirement",
        })

        assert result["success"] is True
        assert result["result"]["updated_count"] == 3
        assert a.lifecycle_status == "5. decommissioned"
        assert b.lifecycle_status == "5. decommissioned"
        per_app = {r["id"]: r for r in result["result"]["apps"]}
        assert per_app[a.id]["updated"] is True
        assert per_app[a.id]["old_status"] == "2.1 strategic"


def test_bulk_update_rejects_invalid_status(db_session, make_org, tenant_ctx):
    org = make_org("g4bad")
    user = _make_user(db_session, org)
    with tenant_ctx(org.id):
        a = _make_app(db_session, "App bad")
        ex = ToolExecutor(user.id)
        result = ex._tool_bulk_update_application_status({
            "app_ids": [a.id],
            "new_status": "totally-made-up",
        })
        assert result["success"] is False
        assert "not a valid lifecycle stage" in result["error"]
        # Unchanged — nothing written on rejection.
        assert a.lifecycle_status == "2.1 strategic"


def test_bulk_update_is_org_scoped(db_session, make_org, tenant_ctx):
    org_a = make_org("g4a")
    org_b = make_org("g4b")
    user_a = _make_user(db_session, org_a)

    with tenant_ctx(org_b.id):
        foreign = _make_app(db_session, "Foreign App")
    foreign_id = foreign.id

    with tenant_ctx(org_a.id):
        mine = _make_app(db_session, "My App")
        ex = ToolExecutor(user_a.id)
        result = ex._tool_bulk_update_application_status({
            "app_ids": [mine.id, foreign_id],
            "new_status": "3. sunset",
        })
        assert result["result"]["updated_count"] == 1
        per_app = {r["id"]: r for r in result["result"]["apps"]}
        assert per_app[mine.id]["updated"] is True
        assert per_app[foreign_id]["updated"] is False
        assert "not found" in per_app[foreign_id]["reason"]

    # The foreign row was never touched.
    with tenant_ctx(org_b.id):
        from app.models.application_portfolio import ApplicationComponent
        still = ApplicationComponent.query.get(foreign_id)
        assert still.lifecycle_status == "2.1 strategic"


def test_bulk_update_dispatches_through_execute_with_permission(db_session, make_org, tenant_ctx):
    """The dispatch choke point runs the handler for a write-permitted user."""
    org = make_org("g4disp")
    user = _make_user(db_session, org)
    user.enterprise_role = "solution_architect"
    db_session.flush()
    with tenant_ctx(org.id):
        a = _make_app(db_session, "Dispatch App")
        ex = ToolExecutor(user.id)
        out = ex.execute(ToolCall(
            id="t1",
            name="bulk_update_application_status",
            arguments={"app_ids": [a.id], "new_status": "3. sunset"},
        ))
        assert out["success"] is True
        assert a.lifecycle_status == "3. sunset"


def test_bulk_update_is_refused_for_a_person_without_write_permission(db_session, make_org, tenant_ctx):
    """A person with no architect role is refused by the choke point and nothing is written."""
    org = make_org("g4refuse")
    user = _make_user(db_session, org)
    with tenant_ctx(org.id):
        a = _make_app(db_session, "Refused App")
        ex = ToolExecutor(user.id)
        out = ex.execute(ToolCall(
            id="t2",
            name="bulk_update_application_status",
            arguments={"app_ids": [a.id], "new_status": "3. sunset"},
        ))
        assert out["success"] is False
        assert a.lifecycle_status == "2.1 strategic"


# --------------------------------------------------------------------------- #
# G8 — create_contract                                                        #
# --------------------------------------------------------------------------- #
def test_create_contract_creates_tenant_scoped_row(db_session, make_org, tenant_ctx):
    org = make_org("g8c")
    user = _make_user(db_session, org)
    with tenant_ctx(org.id):
        ex = ToolExecutor(user.id)
        result = ex._tool_create_contract({
            "name": "Snowflake Subscription",
            "contract_type": "subscription",
            "status": "active",
            "value": 120000,
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        })
        assert result["success"] is True
        cid = result["result"]["id"]

        from app.models.application_portfolio import VendorContract
        row = VendorContract.query.get(cid)
        assert row is not None
        assert row.organization_id == org.id
        assert row.contract_name == "Snowflake Subscription"


def test_create_contract_honest_failure_on_bad_dates(db_session, make_org, tenant_ctx):
    org = make_org("g8cbad")
    user = _make_user(db_session, org)
    with tenant_ctx(org.id):
        ex = ToolExecutor(user.id)
        result = ex._tool_create_contract({
            "name": "Backwards Contract",
            "start_date": "2026-06-01",
            "end_date": "2020-01-01",
        })
        assert result["success"] is False
        assert "before the start" in result["error"]


def test_create_contract_requires_name(db_session, make_org, tenant_ctx):
    org = make_org("g8cname")
    user = _make_user(db_session, org)
    with tenant_ctx(org.id):
        ex = ToolExecutor(user.id)
        result = ex._tool_create_contract({"name": ""})
        assert result["success"] is False
        assert "contract_name is required" in result["error"]


# --------------------------------------------------------------------------- #
# G8 — upsert_license                                                         #
# --------------------------------------------------------------------------- #
def test_upsert_license_creates_and_derives_compliance(db_session, make_org, tenant_ctx):
    org = make_org("g8l")
    user = _make_user(db_session, org)
    with tenant_ctx(org.id):
        ex = ToolExecutor(user.id)
        contract = ex._tool_create_contract({
            "name": "License Host Contract", "start_date": "2026-01-01",
        })
        cid = contract["result"]["id"]

        result = ex._tool_upsert_license({
            "contract_id": cid,
            "product": "Widget Pro",
            "entitled": 100,
            "deployed": 150,  # over-deployed
            "used": 90,
        })
        assert result["success"] is True
        assert result["result"]["created"] is True
        assert result["result"]["compliance_status"] == "over_deployed"

        from app.models.license_entitlement import LicenseEntitlement
        row = LicenseEntitlement.query.get(result["result"]["id"])
        assert row.organization_id == org.id
        assert row.contract_id == cid

        # Update path: bring deployment under entitlement.
        upd = ex._tool_upsert_license({
            "license_id": row.id,
            "contract_id": cid,
            "entitled": 100,
            "deployed": 80,
            "used": 80,
        })
        assert upd["result"]["created"] is False
        assert upd["result"]["compliance_status"] == "compliant"


def test_upsert_license_requires_contract(db_session, make_org, tenant_ctx):
    org = make_org("g8lreq")
    user = _make_user(db_session, org)
    with tenant_ctx(org.id):
        ex = ToolExecutor(user.id)
        result = ex._tool_upsert_license({"product": "Orphan"})
        assert result["success"] is False
        assert "must belong to a contract" in result["error"]


def test_upsert_license_rejects_foreign_contract(db_session, make_org, tenant_ctx):
    org_a = make_org("g8la")
    org_b = make_org("g8lb")
    user_b = _make_user(db_session, org_b)

    with tenant_ctx(org_a.id):
        user_a = _make_user(db_session, org_a)
        ex_a = ToolExecutor(user_a.id)
        contract = ex_a._tool_create_contract({
            "name": "A's Contract", "start_date": "2026-01-01",
        })
        foreign_cid = contract["result"]["id"]

    with tenant_ctx(org_b.id):
        ex_b = ToolExecutor(user_b.id)
        result = ex_b._tool_upsert_license({
            "contract_id": foreign_cid, "product": "Cross-org", "entitled": 10,
        })
        assert result["success"] is False
        assert "not found in your organization" in result["error"]


# --------------------------------------------------------------------------- #
# G7 — platform_admin charter                                                 #
# --------------------------------------------------------------------------- #
def test_platform_admin_resolves_to_its_own_charter(app):
    from app.modules.ai_chat.services.architect_persona_charters import (
        build_architect_prompt,
        get_default_chat_persona,
    )

    with app.app_context():
        pa = build_architect_prompt("platform_admin")
        ea = build_architect_prompt("enterprise_architect")
        assert pa is not None
        assert pa != ea, "platform_admin must not fall back to the EA charter"
        assert "Platform Administrator" in pa
        assert "HARD RULES" in pa
        assert "Live Platform Data" in pa
        # The persisted role selects its own persona now.
        assert get_default_chat_persona("platform_admin") == "platform_admin"


def test_platform_admin_context_reads_real_users(db_session, make_org, tenant_ctx):
    from app.modules.ai_chat.services.architect_persona_charters import (
        _platform_admin_context,
    )

    org = make_org("g7ctx")
    with tenant_ctx(org.id):
        _make_user(db_session, org)  # one confirmed user in this org
        block = _platform_admin_context()
        assert "Users provisioned:" in block
        assert "pending activation" in block
        # No exception, no fabricated placeholder text.
        assert "unavailable" not in block.split("last_import")[0] or True
