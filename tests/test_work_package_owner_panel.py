"""Work package owner and approver panel.

A project manager opens a work package and sees, for every element it
changes, the recorded owner -- or a flag that no owner is recorded -- and
the pending and approved approvals of the work package and its elements.
The owners come from the one ownership record reader and the approvals from
the one approval queue, both fenced to the caller's organisation: another
organisation's owners and approvals never appear.

This is the owner and approver listing slice of the transformation planning
work package panel. All reads go through services; no new store, no new
reader, no migration.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, role="portfolio_manager"):
    from app.models.user import Role, User

    Role.insert_roles()
    role_obj = Role.query.filter_by(name="User").first()
    user = User(
        email=f"op-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Test",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
        role_id=role_obj.id if role_obj else None,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _capability(db_session, org, name):
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(cap)
    db_session.flush()
    return cap


def _application(db_session, org, name):
    from app.models.application_portfolio import ApplicationComponent

    app = ApplicationComponent(name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(app)
    db_session.flush()
    return app


def _wp(db_session, org, name, **extra):
    from app.services import work_package_service

    return work_package_service.create_work_package(organization_id=org.id, name=name, **extra)


def _set_owner(capability, user, org):
    from app.services.capability_ownership_service import set_capability_owner

    return set_capability_owner(
        capability_id=capability.id, user_id=user.id, organization_id=org.id
    )


def _set_app_owner(db_session, app, user, org):
    from app.models.application_owner import ApplicationOwner

    record = ApplicationOwner(
        application_id=app.id,
        user_id=user.id,
        ownership_type="primary",
        organization_id=org.id,
    )
    db_session.add(record)
    db_session.flush()
    return record


def _approval(db_session, org, entity_type, entity_id, summary, status="pending", user=None):
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    approval = create_approval_record(
        organization_id=org.id,
        operation_type="update" if entity_id else "create",
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        operation_payload={"note": "seeded"},
        user_id=user.id if user else None,
    )
    if status == "approved":
        approval.approve(user.id if user else None)
    elif status == "rejected":
        approval.reject("not now")
    db_session.flush()
    return approval


@pytest.fixture
def two_orgs(db_session, make_org):
    org_a = make_org("panel-a")
    org_b = make_org("panel-b")
    return {
        "a": org_a,
        "b": org_b,
        "user_a": _user(db_session, org_a, "pma"),
        "owner_a": _user(db_session, org_a, "owna", role="enterprise_architect"),
        "user_b": _user(db_session, org_b, "pmb"),
    }


# -- the panel assembly -----------------------------------------------------


def test_panel_lists_each_elements_owner_or_no_owner_flag_and_approvals(two_orgs, db_session):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    owner = two_orgs["owner_a"]
    cap1 = _capability(db_session, a, "CRM")
    cap2 = _capability(db_session, a, "Billing")
    app1 = _application(db_session, a, "ERP")
    wp = _wp(db_session, a, "Billing transformation", application_component_id=app1.id)
    wp.capability_ids = [cap1.id, cap2.id]
    db_session.flush()
    _set_owner(cap1, owner, a)
    _set_app_owner(db_session, app1, owner, a)
    pending = _approval(db_session, a, "work_package", wp.id, "Approve the transformation")
    approved = _approval(
        db_session, a, "capability", cap1.id, "Approve the CRM change",
        status="approved", user=owner,
    )
    _approval(db_session, a, "capability", cap2.id, "A rejected request", status="rejected")

    panel = svc.owner_and_approval_panel(wp, a.id)

    assert panel["work_package"]["id"] == wp.id
    assert panel["work_package"]["name"] == "Billing transformation"
    entries = {(e["kind"], e["element_id"]): e for e in panel["elements"]}
    assert set(entries) == {
        ("capability", cap1.id),
        ("capability", cap2.id),
        ("application", app1.id),
    }

    owned = entries[("capability", cap1.id)]
    assert owned["has_owner"] is True
    assert owned["owners"][0]["user_name"] == "Owna Test"

    unowned = entries[("capability", cap2.id)]
    assert unowned["has_owner"] is False
    assert unowned["owners"] == []

    app_entry = entries[("application", app1.id)]
    assert app_entry["has_owner"] is True
    assert app_entry["owners"][0]["user_name"] == "Owna Test"

    # pending + approved approvals only; rejected and expired never appear
    assert [x["id"] for x in panel["work_package_approvals"]["pending"]] == [pending.id]
    assert [x["id"] for x in entries[("capability", cap1.id)]["approved_approvals"]] == [
        approved.id
    ]
    dumped = json.dumps(panel)
    assert "A rejected request" not in dumped


def test_panel_on_a_work_package_with_no_linked_elements_is_an_empty_roster(
    two_orgs, db_session
):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    wp = _wp(db_session, a, "Unlinked package")
    panel = svc.owner_and_approval_panel(wp, a.id)
    assert panel["elements"] == []
    assert panel["work_package_approvals"] == {"pending": [], "approved": []}


def test_other_organisations_owners_and_approvals_never_appear(two_orgs, db_session):
    from app.services import work_package_service as svc

    a, b = two_orgs["a"], two_orgs["b"]
    cap_a = _capability(db_session, a, "A cap")
    cap_b = _capability(db_session, b, "B cap")
    wp_a = _wp(db_session, a, "A package")
    wp_a.capability_ids = [cap_a.id]
    wp_b = _wp(db_session, b, "B package")
    wp_b.capability_ids = [cap_b.id]
    db_session.flush()
    _set_owner(cap_a, two_orgs["owner_a"], a)
    _set_owner(cap_b, two_orgs["user_b"], b)
    _approval(db_session, a, "capability", cap_a.id, "A's approval")
    _approval(db_session, b, "capability", cap_b.id, "B secret approval")
    _approval(db_session, b, "work_package", wp_b.id, "B work package approval")

    panel = svc.owner_and_approval_panel(wp_a, a.id)
    dumped = json.dumps(panel)
    assert "Owna Test" in dumped
    assert "A's approval" in dumped
    assert "B secret approval" not in dumped
    assert "B work package approval" not in dumped
    assert "Pmb Test" not in dumped


# -- the work package detail screen -----------------------------------------


def test_work_package_detail_page_renders_owner_panel(two_orgs, db_session, client, login_as):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    owner = two_orgs["owner_a"]
    cap1 = _capability(db_session, a, "CRM")
    cap2 = _capability(db_session, a, "Billing")
    wp = _wp(db_session, a, "The visible package")
    wp.capability_ids = [cap1.id, cap2.id]
    db_session.flush()
    _set_owner(cap1, owner, a)
    _approval(db_session, a, "capability", cap1.id, "Approve the CRM change",
              status="approved", user=owner)
    _approval(db_session, a, "work_package", wp.id, "Approve the work package")

    login_as(client, two_orgs["user_a"])
    resp = client.get(f"/implementation/work-packages/{wp.id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'data-testid="owner-panel"' in html
    # the recorded owner of the owned element, the no-owner flag of the other
    assert "Owna Test" in html
    assert "No owner recorded" in html
    # the work package's and the element's approvals
    assert "Approve the work package" in html
    assert "Approve the CRM change" in html


def test_work_package_detail_page_is_404_for_another_organisation(two_orgs, db_session,
                                                                  client, login_as):
    b = two_orgs["b"]
    wp_b = _wp(db_session, b, "B secret detail")
    login_as(client, two_orgs["user_a"])
    assert client.get(f"/implementation/work-packages/{wp_b.id}").status_code == 404