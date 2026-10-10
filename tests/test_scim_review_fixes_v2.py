"""The open defects of the PR 424 v2 review (R1-B26 PR 1, fix round 2).

Each test names its defect id (N-01 ... N-05, D-07).
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

import pytest

from tests._scim_test_helpers import (
    call,
    give_unlimited_plan,
    issue_token,
    make_user,
    patch_body,
    user_body,
)
from tests.test_scim_review_fixes import (
    _PASSWORD,
    _callbacks,
    _header_set,
    _password_user,
    _plan,
    _run_callback,
)

pytestmark = pytest.mark.usefixtures("db_session")

_LABELS = ["v1", "v2"]


def _module(label):
    return {name: mod for name, mod, _ in _callbacks()}[label]


def _two_orgs(db_session, make_org, label):
    org_a, org_b = make_org(f"{label}a"), make_org(f"{label}b")
    give_unlimited_plan(db_session, org_a)
    give_unlimited_plan(db_session, org_b)
    return org_a, org_b


# ---------------------------------------------------------------------------
# N-01: no takeover through externalId
# ---------------------------------------------------------------------------


def test_n01_scim_cannot_set_or_change_the_external_id_of_an_administrator(client, db_session, make_org):
    _org_a, org_b = _two_orgs(db_session, make_org, "n01s")
    admin = make_user(db_session, org_b, "bossb", administrator=True, scim_external_id="orig-id")
    unlinked_admin = make_user(db_session, org_b, "bossb2", administrator=True)
    _row, raw = issue_token(db_session, org_b)

    resp = call(client, "PATCH", f"/Users/{admin.id}", raw, patch_body(
        {"op": "replace", "path": "externalId", "value": "attacker-sub"}))
    assert resp.status_code == 403
    resp = call(client, "PATCH", f"/Users/{unlinked_admin.id}", raw, patch_body(
        {"op": "replace", "value": {"externalId": "attacker-sub"}}))
    assert resp.status_code == 403
    resp = call(client, "PUT", f"/Users/{admin.id}", raw,
                user_body(admin.email, external_id="attacker-sub"))
    assert resp.status_code == 403
    # Linking an existing administrator account through POST is refused the same way.
    resp = call(client, "POST", "/Users", raw, user_body(unlinked_admin.email, external_id="attacker-sub"))
    assert resp.status_code in (403, 409)

    db_session.refresh(admin)
    db_session.refresh(unlinked_admin)
    assert admin.scim_external_id == "orig-id"
    assert unlinked_admin.scim_external_id is None

    # A brand-new user may still arrive with an externalId.
    resp = call(client, "POST", "/Users", raw, user_body(external_id="fresh-id"))
    assert resp.status_code == 201


# (the forced-externalId sign-in test of this section is superseded by V3-02 in
# tests/test_scim_review_fixes_v3.py: SCIM no longer writes the sign-in pair.)


# ---------------------------------------------------------------------------
# N-02: SCIM touches only people who belong only to the token's organisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("other_role", ["org_admin", "viewer"])
def test_n02_scim_refuses_a_person_who_also_belongs_to_another_organisation(
    client, db_session, make_org, other_role
):
    from app.models.miscellaneous import SSOGroupRoleMapping
    from app.models.org_role import OrgRole
    from app.services import provisioning_service

    org_c, org_b = _two_orgs(db_session, make_org, "n02")
    person = make_user(db_session, org_b, "shared")
    db_session.add(OrgRole(organization_id=org_c.id, user_id=person.id, role=other_role))
    group = SSOGroupRoleMapping(organization_id=org_b.id, sso_group_name="g", role_name="viewer", is_active=True)
    db_session.add(group)
    db_session.flush()
    _row, raw = issue_token(db_session, org_b)
    original = person.email

    assert call(client, "PATCH", f"/Users/{person.id}", raw, patch_body(
        {"op": "replace", "path": "userName", "value": "moved@example.com"})).status_code == 403
    assert call(client, "PATCH", f"/Users/{person.id}", raw, patch_body(
        {"op": "replace", "path": "active", "value": False})).status_code == 403
    assert call(client, "PUT", f"/Users/{person.id}", raw,
                user_body(original, given="Changed")).status_code == 403
    assert call(client, "DELETE", f"/Users/{person.id}", raw).status_code == 403
    assert call(client, "PATCH", f"/Groups/{group.id}", raw, patch_body(
        {"op": "add", "path": "members", "value": [{"value": str(person.id)}]})).status_code == 403

    db_session.refresh(person)
    assert person.email == original and person.deactivated_at is None
    assert person.first_name != "Changed"
    assert provisioning_service.group_member_ids(org_b.id, group.id) == []


def test_n02_scim_cannot_remove_a_shared_person_from_a_group(client, db_session, make_org):
    from app.models.miscellaneous import SSOGroupRoleMapping
    from app.models.org_role import OrgRole
    from app.models.scim import ScimGroupMembership
    from app.services import provisioning_service

    org_c, org_b = _two_orgs(db_session, make_org, "n02g")
    person = make_user(db_session, org_b, "sharedg")
    group = SSOGroupRoleMapping(organization_id=org_b.id, sso_group_name="g2", role_name="viewer", is_active=True)
    db_session.add(group)
    db_session.flush()
    db_session.add(ScimGroupMembership(organization_id=org_b.id, group_mapping_id=group.id, user_id=person.id))
    db_session.add(OrgRole(organization_id=org_c.id, user_id=person.id, role="viewer"))
    db_session.flush()
    _row, raw = issue_token(db_session, org_b)

    resp = call(client, "PATCH", f"/Groups/{group.id}", raw, patch_body(
        {"op": "remove", "path": 'members[value eq "%s"]' % person.id}))
    assert resp.status_code == 403
    assert provisioning_service.group_member_ids(org_b.id, group.id) == [person.id]


def test_n02_an_administrators_username_is_refused_and_a_members_is_not(client, db_session, make_org):
    _org_c, org_b = _two_orgs(db_session, make_org, "n02u")
    admin = make_user(db_session, org_b, "adminu", administrator=True)
    member = make_user(db_session, org_b, "memberu")
    _row, raw = issue_token(db_session, org_b)

    assert call(client, "PATCH", f"/Users/{admin.id}", raw, patch_body(
        {"op": "replace", "path": "userName", "value": "x@example.com"})).status_code == 403
    assert call(client, "PATCH", f"/Users/{member.id}", raw, patch_body(
        {"op": "replace", "path": "userName", "value": f"m-{uuid.uuid4().hex[:6]}@example.com"})).status_code == 200


def test_n02_administration_is_judged_across_every_organisation_of_the_person(db_session, make_org):
    from app.models.org_role import OrgRole
    from app.services.rbac_service import rbac_service

    org_c, org_b = _two_orgs(db_session, make_org, "n02a")
    person = make_user(db_session, org_b, "elsewhere")
    assert rbac_service.is_org_admin_anywhere(person) is False
    db_session.add(OrgRole(organization_id=org_c.id, user_id=person.id, role="org_admin"))
    db_session.flush()
    assert rbac_service.is_org_admin_anywhere(person) is True


# ---------------------------------------------------------------------------
# N-03: a global sign-in never rewrites the email
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", _LABELS)
def test_n03_a_mismatching_provider_email_is_audited_and_never_written(
    app, db_session, make_org, monkeypatch, caplog, label
):
    from app.models.audit_log import AuditLog

    _org_a, org_b = _two_orgs(db_session, make_org, f"n03{label}")
    user = make_user(db_session, org_b, "stored")
    sub = f"n03-{uuid.uuid4().hex[:6]}"
    user.external_id, user.sso_provider = sub, "azure"
    db_session.flush()
    stored = user.email
    other = f"different-{uuid.uuid4().hex[:6]}@elsewhere.example"

    with caplog.at_level(logging.DEBUG):
        _resp, snap = _run_callback(
            app, monkeypatch, _module(label), {"email": other, "sub": sub}, provider="azure"
        )

    db_session.refresh(user)
    assert user.email == stored
    assert snap.get("_user_id") == str(user.id) or snap.get("_mfa_pending_user_id") == user.id
    rows = AuditLog.query.filter_by(action="sso_email_mismatch", record_id=user.id).all()
    assert len(rows) == 1
    blob = json.dumps(rows[0].new_value, default=str)
    assert stored not in blob and other not in blob
    assert stored not in caplog.text and other not in caplog.text


@pytest.mark.parametrize("label", _LABELS)
def test_n03_a_case_difference_is_not_a_mismatch(app, db_session, make_org, monkeypatch, label):
    from app.models.audit_log import AuditLog

    _org_a, org_b = _two_orgs(db_session, make_org, f"n03c{label}")
    user = make_user(db_session, org_b, "casey")
    _run_callback(
        app, monkeypatch, _module(label), {"email": user.email.upper(), "sub": "s-case"}, provider="azure"
    )
    assert AuditLog.query.filter_by(action="sso_email_mismatch", record_id=user.id).count() == 0


# ---------------------------------------------------------------------------
# N-04: reactivation re-checks every organisation the person belongs to
# ---------------------------------------------------------------------------


def test_n04_reactivation_is_refused_when_another_organisation_of_theirs_is_full(db_session, make_org):
    from app.models.org_role import OrgRole
    from app.models.subscription import Subscription
    from app.services import provisioning_service
    from app.services.billing_plans import PlanLimitReached

    org_c, org_b = make_org("n04c"), make_org("n04b")
    give_unlimited_plan(db_session, org_b)
    _plan(db_session, org_c, "team", 3)
    make_user(db_session, org_c, "ed1")
    make_user(db_session, org_c, "ed2")
    leaver = make_user(db_session, org_b, "leaver")
    db_session.add(OrgRole(organization_id=org_c.id, user_id=leaver.id, role="architect"))
    db_session.flush()
    provisioning_service.deactivate_user(leaver, reason="leaver_deprovisioned", actor="test")
    sub = Subscription.query.filter_by(organization_id=org_c.id).one()
    sub.seats_purchased = 2  # C is now at 2 of 2
    db_session.flush()

    with pytest.raises(PlanLimitReached):
        provisioning_service.reactivate_user(leaver, actor="test")
    db_session.rollback()
    db_session.refresh(leaver)
    assert leaver.deactivated_at is not None


# ---------------------------------------------------------------------------
# N-05: no forwarder
# ---------------------------------------------------------------------------


def test_n05_provisioning_service_has_no_org_admin_forwarder():
    from app.services import provisioning_service

    assert not hasattr(provisioning_service, "_is_org_admin")
    source = Path(provisioning_service.__file__).read_text(encoding="utf-8")
    assert "_is_org_admin" not in source


# ---------------------------------------------------------------------------
# D-07: /api/auth/login decides before touching the session
# ---------------------------------------------------------------------------


def test_d07_api_login_headers_of_a_deactivated_account_equal_a_wrong_password(
    client, db_session, make_org, monkeypatch
):
    from app.services import provisioning_service

    org = make_org("d07v2")
    give_unlimited_plan(db_session, org)
    leaver = _password_user(db_session, org, "apileaver2")
    provisioning_service.deactivate_user(leaver, reason="leaver_deprovisioned", actor="test")

    from app.services import session_registry

    touched = []
    real = session_registry.login_and_register
    monkeypatch.setattr(
        session_registry, "login_and_register",
        lambda *a, **k: touched.append(1) or real(*a, **k),
    )
    right = client.post("/api/auth/login", json={"email": leaver.email, "password": _PASSWORD})
    assert touched == []  # nothing touched the session for a deactivated account
    wrong = client.post("/api/auth/login", json={"email": leaver.email, "password": "not-the-password"})
    assert right.status_code == wrong.status_code == 401
    assert right.get_data() == wrong.get_data()
    assert _header_set(right) == _header_set(wrong)
    assert right.headers.getlist("Set-Cookie") == wrong.headers.getlist("Set-Cookie")
