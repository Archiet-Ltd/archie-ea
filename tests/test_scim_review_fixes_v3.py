"""The defects of the PR 424 v3 review (R1-B26 PR 1, fix round 3).

Each test names its defect id (V3-01 ... V3-06). Every refusal is tested with an
active subject and with a deactivated one.
"""

from __future__ import annotations

import inspect
import re
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
from tests.test_scim_review_fixes import _callbacks, _run_callback

pytestmark = pytest.mark.usefixtures("db_session")

_LABELS = ["v1", "v2"]
_STATES = [pytest.param(False, id="active"), pytest.param(True, id="deactivated")]
_ROOT = Path(__file__).resolve().parent.parent


def _module(label):
    return {name: mod for name, mod, _ in _callbacks()}[label]


def _orgs(db_session, make_org, label):
    org_c, org_b = make_org(f"{label}c"), make_org(f"{label}b")
    give_unlimited_plan(db_session, org_c)
    give_unlimited_plan(db_session, org_b)
    return org_c, org_b


def _retire(db_session, user, deactivated):
    """Deactivate through the service (the shared reader must still answer)."""
    from app.services import provisioning_service

    if deactivated:
        provisioning_service.deactivate_user(user, reason="leaver_deprovisioned", actor="test")
        db_session.refresh(user)
        assert not user.is_active


def _shared_person(db_session, org_home, org_other, label, *, role="viewer", deactivated=False):
    from app.models.org_role import OrgRole

    person = make_user(db_session, org_home, label)
    db_session.add(OrgRole(organization_id=org_other.id, user_id=person.id, role=role))
    db_session.flush()
    _retire(db_session, person, deactivated)
    return person


def _state(user):
    return (user.email, user.scim_external_id, user.external_id, user.sso_provider, user.deactivated_at,
            user.first_name)


# ---------------------------------------------------------------------------
# V3-01: the readers do not depend on the session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deactivated", _STATES)
def test_v3_01_org_ids_for_and_is_org_admin_anywhere_ignore_login_state(db_session, make_org, deactivated):
    from app.models.org_role import OrgRole
    from app.services.rbac_service import rbac_service

    org_c, org_b = _orgs(db_session, make_org, "v301r")
    person = make_user(db_session, org_b, "reader")
    assert rbac_service.is_org_admin_anywhere(person) is False
    db_session.add(OrgRole(organization_id=org_c.id, user_id=person.id, role="org_admin"))
    db_session.flush()
    _retire(db_session, person, deactivated)
    assert rbac_service.org_ids_for(person) == {org_b.id, org_c.id}
    assert rbac_service.is_org_admin_anywhere(person) is True


def test_v3_01_readers_do_not_touch_the_session_and_the_old_ones_are_gone():
    from app.services import provisioning_service
    from app.services.rbac_service import rbac_service

    for fn in (rbac_service.org_ids_for, rbac_service.is_org_admin_anywhere):
        src = inspect.getsource(fn)
        for banned in ("accessible_organizations", "is_authenticated", "is_active", "current_user", "session"):
            assert banned not in src, (fn.__name__, banned)
        assert not re.search(r"\bg\.", src)
    source = Path(provisioning_service.__file__).read_text(encoding="utf-8")
    for gone in ("is_admin_anywhere", "_has_sso_protocol", "accessible_organizations", "tenant_context"):
        if gone == "is_admin_anywhere":
            assert "def is_admin_anywhere" not in source
        else:
            assert gone not in source, gone
    assert not hasattr(provisioning_service, "is_admin_anywhere")


@pytest.mark.parametrize("deactivated", _STATES)
def test_v3_01_username_change_on_an_administrator_is_refused(client, db_session, make_org, deactivated):
    _org_c, org_b = _orgs(db_session, make_org, "v301u")
    admin = make_user(db_session, org_b, "bossu", administrator=True)
    _row, raw = issue_token(db_session, org_b)
    _retire(db_session, admin, deactivated)
    before = _state(admin)

    resp = call(client, "PATCH", f"/Users/{admin.id}", raw, patch_body(
        {"op": "replace", "path": "userName", "value": "taken-over@example.com"}))
    assert resp.status_code == 403
    resp = call(client, "PUT", f"/Users/{admin.id}", raw, user_body("taken-over@example.com"))
    assert resp.status_code == 403
    db_session.refresh(admin)
    assert _state(admin) == before


@pytest.mark.parametrize("deactivated", _STATES)
def test_v3_01_external_id_change_on_an_administrator_is_refused(client, db_session, make_org, deactivated):
    _org_c, org_b = _orgs(db_session, make_org, "v301e")
    admin = make_user(db_session, org_b, "bosse", administrator=True, scim_external_id="orig-id")
    _row, raw = issue_token(db_session, org_b)
    _retire(db_session, admin, deactivated)
    before = _state(admin)

    assert call(client, "PATCH", f"/Users/{admin.id}", raw, patch_body(
        {"op": "replace", "path": "externalId", "value": "attacker-sub"})).status_code == 403
    assert call(client, "PUT", f"/Users/{admin.id}", raw,
                user_body(admin.email, external_id="attacker-sub")).status_code == 403
    db_session.refresh(admin)
    assert _state(admin) == before


@pytest.mark.parametrize("deactivated", _STATES)
def test_v3_01_the_one_organisation_rule_on_every_user_route(client, db_session, make_org, deactivated):
    org_c, org_b = _orgs(db_session, make_org, "v301o")
    person = _shared_person(db_session, org_b, org_c, "sharedu", deactivated=deactivated)
    _row, raw = issue_token(db_session, org_b)
    before = _state(person)

    assert call(client, "POST", "/Users", raw, user_body(person.email, external_id="x1")).status_code == 403
    assert call(client, "PUT", f"/Users/{person.id}", raw,
                user_body(person.email, given="Changed", external_id="x2")).status_code == 403
    assert call(client, "PATCH", f"/Users/{person.id}", raw, patch_body(
        {"op": "replace", "path": "name.givenName", "value": "Changed"},
        {"op": "replace", "path": "active", "value": True})).status_code == 403
    assert call(client, "DELETE", f"/Users/{person.id}", raw).status_code == 403
    db_session.refresh(person)
    assert _state(person) == before


@pytest.mark.parametrize("deactivated", _STATES)
def test_v3_01_the_one_organisation_rule_on_every_group_operation(client, db_session, make_org, deactivated):
    from app.models.miscellaneous import SSOGroupRoleMapping
    from app.models.scim import ScimGroupMembership
    from app.services import provisioning_service as ps

    org_c, org_b = _orgs(db_session, make_org, "v301g")
    person = _shared_person(db_session, org_b, org_c, "sharedg", deactivated=deactivated)
    other = make_user(db_session, org_b, "plain")
    group = SSOGroupRoleMapping(organization_id=org_b.id, sso_group_name="g", role_name="viewer", is_active=True)
    db_session.add(group)
    db_session.flush()
    db_session.add(ScimGroupMembership(organization_id=org_b.id, group_mapping_id=group.id, user_id=person.id))
    db_session.add(ScimGroupMembership(organization_id=org_b.id, group_mapping_id=group.id, user_id=other.id))
    db_session.flush()
    held = sorted(ps.group_member_ids(org_b.id, group.id))
    third = make_user(db_session, org_b, "third")

    with pytest.raises(ps.ProtectedUserError):
        ps.add_group_members(org_b.id, group, [person.id], "t")
    with pytest.raises(ps.ProtectedUserError):
        ps.remove_group_members(org_b.id, group, [person.id], "t")
    with pytest.raises(ps.ProtectedUserError):
        ps.replace_group_member(org_b.id, group, person.id, [third.id], "t")
    with pytest.raises(ps.ProtectedUserError):
        ps.set_group_members(org_b.id, group, [other.id], "t")
    assert sorted(ps.group_member_ids(org_b.id, group.id)) == held


@pytest.mark.parametrize("deactivated", _STATES)
def test_v3_01_platform_administrator_is_refused(client, db_session, make_org, deactivated):
    _org_c, org_b = _orgs(db_session, make_org, "v301p")
    admin = make_user(db_session, org_b, "padm", administrator=True, is_platform_admin=True)
    if deactivated:
        from datetime import datetime

        admin.deactivated_at = datetime(2026, 1, 1)
        db_session.flush()
    _row, raw = issue_token(db_session, org_b)
    before = _state(admin)

    assert call(client, "PATCH", f"/Users/{admin.id}", raw, patch_body(
        {"op": "replace", "path": "name.givenName", "value": "X"},
        {"op": "replace", "path": "active", "value": True})).status_code == 403
    assert call(client, "PUT", f"/Users/{admin.id}", raw, user_body(admin.email)).status_code == 403
    assert call(client, "DELETE", f"/Users/{admin.id}", raw).status_code == 403
    assert call(client, "POST", "/Users", raw, user_body(admin.email)).status_code == 403
    db_session.refresh(admin)
    assert _state(admin) == before


# ---------------------------------------------------------------------------
# V3-03: the reviewer's chain
# ---------------------------------------------------------------------------


def _chain_patch(client, raw, user):
    return call(client, "PATCH", f"/Users/{user.id}", raw, patch_body(
        {"op": "replace", "path": "userName", "value": "taken-over@example.com"},
        {"op": "replace", "path": "externalId", "value": "attacker-sub"},
        {"op": "replace", "path": "active", "value": True}))


def test_v3_03_delete_then_patch_on_an_organisations_own_administrator(client, db_session, make_org):
    _org_c, org_b = _orgs(db_session, make_org, "v303a")
    admin = make_user(db_session, org_b, "ownadm", administrator=True,
                      scim_external_id="orig", external_id="sub-1", sso_provider="azure")
    _row, raw = issue_token(db_session, org_b)

    assert call(client, "DELETE", f"/Users/{admin.id}", raw).status_code == 204
    db_session.refresh(admin)
    assert admin.deactivated_at is not None
    before = _state(admin)

    assert _chain_patch(client, raw, admin).status_code == 403
    db_session.refresh(admin)
    assert _state(admin) == before
    assert admin.scim_external_id == "orig" and admin.external_id == "sub-1" and admin.sso_provider == "azure"


def test_v3_03_delete_then_patch_on_a_cross_organisation_administrator(client, db_session, make_org):
    from datetime import datetime

    org_c, org_b = _orgs(db_session, make_org, "v303x")
    admin = _shared_person(db_session, org_b, org_c, "xadm", role="org_admin")
    admin.scim_external_id = "orig"
    db_session.flush()
    _row, raw = issue_token(db_session, org_b)

    assert call(client, "DELETE", f"/Users/{admin.id}", raw).status_code == 403
    admin.deactivated_at = datetime(2026, 1, 1)
    db_session.commit()
    before = _state(admin)

    assert _chain_patch(client, raw, admin).status_code == 403
    db_session.refresh(admin)
    assert _state(admin) == before


# ---------------------------------------------------------------------------
# V3-01b / V3-02: SCIM has its own column
# ---------------------------------------------------------------------------


def test_v3_01b_scim_writes_only_scim_external_id(client, db_session, make_org):
    from app.models.user import User

    _org_c, org_b = _orgs(db_session, make_org, "v301b")
    _row, raw = issue_token(db_session, org_b)

    created = call(client, "POST", "/Users", raw, user_body(external_id="ext-1")).get_json(force=True)
    row = User.query.filter(User.id == int(created["id"]), User.organization_id == org_b.id).one()
    assert (row.scim_external_id, row.external_id, row.sso_provider) == ("ext-1", None, None)
    assert created["externalId"] == "ext-1"

    for body in (
        patch_body({"op": "replace", "path": "externalId", "value": "ext-2"}),
        patch_body({"op": "replace", "value": {"externalId": "ext-3"}}),
    ):
        assert call(client, "PATCH", f"/Users/{row.id}", raw, body).status_code == 200
    assert call(client, "PUT", f"/Users/{row.id}", raw, user_body(row.email, external_id="ext-4")).status_code == 200
    db_session.refresh(row)
    assert (row.scim_external_id, row.external_id, row.sso_provider) == ("ext-4", None, None)

    listed = call(client, "GET", '/Users?filter=externalId eq "ext-4"', raw).get_json(force=True)
    assert listed["totalResults"] == 1
    assert call(client, "PATCH", f"/Users/{row.id}", raw, patch_body(
        {"op": "remove", "path": "externalId"})).status_code == 200
    db_session.refresh(row)
    assert not row.scim_external_id and row.external_id is None

    linked = make_user(db_session, org_b, "linkv3")
    assert call(client, "POST", "/Users", raw, user_body(linked.email, external_id="ext-5")).status_code == 201
    db_session.refresh(linked)
    assert (linked.scim_external_id, linked.external_id, linked.sso_provider) == ("ext-5", None, None)


def test_v3_01b_no_sign_in_module_reads_scim_external_id():
    targets = list((_ROOT / "app" / "modules" / "account").rglob("*.py"))
    targets += [_ROOT / "app/modules/auth/sso_routes.py", _ROOT / "app/services/sso_service.py",
                _ROOT / "app/_bootstrap/routes.py"]
    existing = [t for t in targets if t.exists()]
    assert len(existing) >= 4
    for path in existing:
        assert "scim_external_id" not in path.read_text(encoding="utf-8"), path


@pytest.mark.parametrize("label", _LABELS)
def test_v3_02_a_scim_external_id_never_resolves_a_global_sign_in(
    app, client, db_session, make_org, monkeypatch, label
):
    from app.models.user import User

    _org_c, org_b = _orgs(db_session, make_org, f"v302{label}")
    member = make_user(db_session, org_b, "member")
    real_sub = f"real-{uuid.uuid4().hex[:6]}"
    _run_callback(app, monkeypatch, _module(label), {"email": member.email, "sub": real_sub}, provider="azure")
    db_session.refresh(member)
    assert (member.external_id, member.sso_provider) == (real_sub, "azure")

    _row, raw = issue_token(db_session, org_b)
    attacker_sub = f"attacker-{uuid.uuid4().hex[:6]}"
    assert call(client, "PATCH", f"/Users/{member.id}", raw, patch_body(
        {"op": "replace", "path": "externalId", "value": attacker_sub})).status_code == 200
    db_session.refresh(member)
    assert member.scim_external_id == attacker_sub
    assert (member.external_id, member.sso_provider) == (real_sub, "azure")

    attacker_email = f"attacker-{uuid.uuid4().hex[:6]}@evil.example"
    member_email = member.email
    _resp, snap = _run_callback(
        app, monkeypatch, _module(label), {"email": attacker_email, "sub": attacker_sub}, provider="azure"
    )
    db_session.refresh(member)
    assert member.email == member_email and member.external_id == real_sub
    assert snap.get("_user_id") != str(member.id)
    found = User.find_by_email(attacker_email)
    assert found is not None and found.id != member.id


# ---------------------------------------------------------------------------
# V3-04: a known address with a different subject is refused
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deactivated", _STATES)
@pytest.mark.parametrize("label", _LABELS)
def test_v3_04_an_email_with_a_different_stored_pair_is_refused(
    app, db_session, make_org, monkeypatch, label, deactivated
):
    from app.models.user import User

    _org_c, org_b = _orgs(db_session, make_org, f"v304r{label}")
    member = make_user(db_session, org_b, "paired", external_id="stored-sub", sso_provider="azure")
    _retire(db_session, member, deactivated)
    before = (member.email, member.external_id, member.sso_provider)
    rows = User.query.count()

    resp, snap = _run_callback(
        app, monkeypatch, _module(label), {"email": member.email, "sub": "unknown-sub"}, provider="azure"
    )
    assert "_user_id" not in snap and "_mfa_pending_user_id" not in snap
    assert resp.status_code == 302
    assert User.query.count() == rows
    db_session.refresh(member)
    assert (member.email, member.external_id, member.sso_provider) == before


@pytest.mark.parametrize("label", _LABELS)
def test_v3_04_no_stored_pair_links_and_an_unknown_email_creates_one_account(
    app, db_session, make_org, monkeypatch, label
):
    from app.models.user import User

    _org_c, org_b = _orgs(db_session, make_org, f"v304l{label}")
    member = make_user(db_session, org_b, "unpaired")
    # A pair for another provider is not a stored pair for this one.
    other = make_user(db_session, org_b, "otherprov", external_id="g-sub", sso_provider="google")
    rows = User.query.count()

    _resp, snap = _run_callback(
        app, monkeypatch, _module(label), {"email": member.email, "sub": "new-sub"}, provider="azure"
    )
    db_session.refresh(member)
    assert (member.external_id, member.sso_provider) == ("new-sub", "azure")
    assert snap.get("_user_id") == str(member.id)
    _resp, snap = _run_callback(
        app, monkeypatch, _module(label), {"email": other.email, "sub": "az-sub"}, provider="azure"
    )
    assert snap.get("_user_id") == str(other.id)
    assert User.query.count() == rows

    fresh = f"fresh-{uuid.uuid4().hex[:8]}@example.com"
    _resp, snap = _run_callback(
        app, monkeypatch, _module(label), {"email": fresh, "sub": "fresh-sub"}, provider="azure"
    )
    assert User.query.count() == rows + 1
    assert User.find_by_email(fresh) is not None


# ---------------------------------------------------------------------------
# V3-05 / V3-06: group delete honours the rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deactivated", _STATES)
def test_v3_05_group_delete_refuses_a_shared_member_and_removes_nothing(
    client, db_session, make_org, deactivated
):
    from app.models.miscellaneous import SSOGroupRoleMapping
    from app.models.scim import ScimGroupMembership
    from app.services import provisioning_service as ps

    org_c, org_b = _orgs(db_session, make_org, "v305")
    person = _shared_person(db_session, org_b, org_c, "sharedd", deactivated=deactivated)
    plain = make_user(db_session, org_b, "plaind")
    group = SSOGroupRoleMapping(organization_id=org_b.id, sso_group_name="gd", role_name="viewer", is_active=True)
    db_session.add(group)
    db_session.flush()
    for u in (plain, person):
        db_session.add(ScimGroupMembership(organization_id=org_b.id, group_mapping_id=group.id, user_id=u.id))
    db_session.flush()
    held = sorted(ps.group_member_ids(org_b.id, group.id))
    _row, raw = issue_token(db_session, org_b)

    assert call(client, "DELETE", f"/Groups/{group.id}", raw).status_code == 403
    assert sorted(ps.group_member_ids(org_b.id, group.id)) == held


def test_v3_05_group_delete_of_a_plain_group_still_works(client, db_session, make_org):
    from app.models.miscellaneous import SSOGroupRoleMapping
    from app.models.scim import ScimGroupMembership
    from app.services import provisioning_service as ps

    _org_c, org_b = _orgs(db_session, make_org, "v305ok")
    plain = make_user(db_session, org_b, "plaine")
    group = SSOGroupRoleMapping(organization_id=org_b.id, sso_group_name="ge", role_name="viewer", is_active=True)
    db_session.add(group)
    db_session.flush()
    db_session.add(ScimGroupMembership(organization_id=org_b.id, group_mapping_id=group.id, user_id=plain.id))
    db_session.flush()
    _row, raw = issue_token(db_session, org_b)
    assert call(client, "DELETE", f"/Groups/{group.id}", raw).status_code == 204
    assert ps.group_member_ids(org_b.id, group.id) == []


def test_v3_06_require_sole_organisation_uses_the_session_free_reader(db_session, make_org):
    from app.services import provisioning_service as ps

    org_c, org_b = _orgs(db_session, make_org, "v306")
    person = _shared_person(db_session, org_b, org_c, "gone", deactivated=True)
    with pytest.raises(ps.ProtectedUserError):
        ps.require_sole_organisation(person, org_b.id)
    alone = make_user(db_session, org_b, "alone")
    _retire(db_session, alone, True)
    ps.require_sole_organisation(alone, org_b.id)
