"""SCIM Groups (R1-B26 PR 1): a group is an ``SSOGroupRoleMapping`` row, and
membership recomputes ``enterprise_role`` through the one resolver."""

from __future__ import annotations

import pytest

from tests._scim_test_helpers import call, issue_token, make_user, patch_body

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def org(make_org):
    return make_org("scim-groups")


@pytest.fixture
def token(db_session, org):
    _row, raw = issue_token(db_session, org)
    return raw


def _mapping(db_session, org, name, role, active=True):
    from app.models.miscellaneous import SSOGroupRoleMapping

    row = SSOGroupRoleMapping(
        organization_id=org.id, sso_group_name=name, role_name=role, is_active=active
    )
    db_session.add(row)
    db_session.flush()
    return row


def _memberships(group_id):
    from app.models.scim import ScimGroupMembership

    return ScimGroupMembership.query.filter(ScimGroupMembership.group_mapping_id == int(group_id)).all()


def _scim_user(client, token):
    from tests._scim_test_helpers import user_body

    return call(client, "POST", "/Users", token, user_body()).get_json(force=True)["id"]


def _reload_role(db_session, user_id):
    from app.models.user import User

    db_session.expire_all()
    return User.query.filter(User.id == int(user_id)).one().enterprise_role


class TestCreate:
    def test_create_returns_an_inactive_least_privilege_mapping_when_none_exists(self, client, token, org):
        from app.models.miscellaneous import SSOGroupRoleMapping

        resp = call(client, "POST", "/Groups", token, {"displayName": "EA-Team"})
        assert resp.status_code == 201, resp.get_data(as_text=True)
        data = resp.get_json(force=True)
        assert data["displayName"] == "EA-Team"
        assert resp.headers["Location"] == data["meta"]["location"]
        row = SSOGroupRoleMapping.query.filter(SSOGroupRoleMapping.id == int(data["id"])).one()
        assert row.organization_id == org.id
        assert row.is_active is False
        assert row.role_name == "non_technical_owner"

    def test_create_returns_the_existing_row_for_that_name(self, client, token, db_session, org):
        mapping = _mapping(db_session, org, "Existing", "enterprise_architect")
        resp = call(client, "POST", "/Groups", token, {"displayName": "Existing"})
        assert resp.status_code == 201
        assert resp.get_json(force=True)["id"] == str(mapping.id)
        db_session.refresh(mapping)
        assert mapping.role_name == "enterprise_architect" and mapping.is_active is True

    def test_create_needs_a_display_name(self, client, token):
        assert call(client, "POST", "/Groups", token, {}).status_code == 400


class TestMembership:
    def test_patch_add_and_remove_members_recompute_the_role(self, client, token, db_session, org):
        mapping = _mapping(db_session, org, "Architects", "enterprise_architect")
        uid = _scim_user(client, token)
        assert _reload_role(db_session, uid) == "non_technical_owner"

        resp = call(client, "PATCH", f"/Groups/{mapping.id}", token, patch_body(
            {"op": "add", "path": "members", "value": [{"value": uid}]}))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert [m["value"] for m in resp.get_json(force=True)["members"]] == [uid]
        assert len(_memberships(mapping.id)) == 1
        assert _reload_role(db_session, uid) == "enterprise_architect"

        resp = call(client, "PATCH", f"/Groups/{mapping.id}", token, patch_body(
            {"op": "remove", "path": f'members[value eq "{uid}"]'}))
        assert resp.status_code == 200
        assert _memberships(mapping.id) == []
        # nothing maps any more: the role is left as it was
        assert _reload_role(db_session, uid) == "enterprise_architect"

    def test_patch_without_path_and_replace(self, client, token, db_session, org):
        mapping = _mapping(db_session, org, "Managers", "portfolio_manager")
        first, second = _scim_user(client, token), _scim_user(client, token)
        call(client, "PATCH", f"/Groups/{mapping.id}", token, patch_body(
            {"op": "add", "value": {"members": [{"value": first}]}}))
        assert [m.user_id for m in _memberships(mapping.id)] == [int(first)]
        call(client, "PATCH", f"/Groups/{mapping.id}", token, patch_body(
            {"op": "replace", "path": "members", "value": [{"value": second}]}))
        assert [m.user_id for m in _memberships(mapping.id)] == [int(second)]
        assert _reload_role(db_session, second) == "portfolio_manager"

    def test_put_replaces_members(self, client, token, db_session, org):
        mapping = _mapping(db_session, org, "Putters", "arb_member")
        first, second = _scim_user(client, token), _scim_user(client, token)
        resp = call(client, "PUT", f"/Groups/{mapping.id}", token, {
            "displayName": "Putters", "members": [{"value": first}, {"value": second}]})
        assert resp.status_code == 200
        assert sorted(m.user_id for m in _memberships(mapping.id)) == sorted([int(first), int(second)])
        resp = call(client, "PUT", f"/Groups/{mapping.id}", token, {"displayName": "Putters", "members": [{"value": second}]})
        assert [m.user_id for m in _memberships(mapping.id)] == [int(second)]

    def test_an_inactive_mapping_grants_nothing(self, client, token, db_session, org):
        mapping = _mapping(db_session, org, "Dormant", "enterprise_architect", active=False)
        uid = _scim_user(client, token)
        call(client, "PATCH", f"/Groups/{mapping.id}", token, patch_body(
            {"op": "add", "path": "members", "value": [{"value": uid}]}))
        assert len(_memberships(mapping.id)) == 1
        assert _reload_role(db_session, uid) == "non_technical_owner"

    def test_display_name_cannot_be_renamed(self, client, token, db_session, org):
        mapping = _mapping(db_session, org, "Fixed", "arb_member")
        resp = call(client, "PUT", f"/Groups/{mapping.id}", token, {"displayName": "Other", "members": []})
        assert resp.status_code == 400
        assert resp.get_json(force=True)["scimType"] == "mutability"

    def test_list_and_filter_groups(self, client, token, db_session, org):
        _mapping(db_session, org, "Alpha", "arb_member")
        _mapping(db_session, org, "Beta", "arb_member")
        listing = call(client, "GET", "/Groups", token).get_json(force=True)
        assert listing["totalResults"] == 2
        one = call(client, "GET", '/Groups?filter=displayName eq "Beta"', token).get_json(force=True)
        assert [g["displayName"] for g in one["Resources"]] == ["Beta"]
        assert call(client, "GET", "/Groups?filter=members eq 1", token).status_code == 400


class TestDeleteAndEscalation:
    def test_delete_removes_memberships_only(self, client, token, db_session, org):
        from app.models.miscellaneous import SSOGroupRoleMapping

        mapping = _mapping(db_session, org, "Temp", "arb_member")
        uid = _scim_user(client, token)
        call(client, "PATCH", f"/Groups/{mapping.id}", token, patch_body(
            {"op": "add", "path": "members", "value": [{"value": uid}]}))
        assert call(client, "DELETE", f"/Groups/{mapping.id}", token).status_code == 204
        assert _memberships(mapping.id) == []
        assert SSOGroupRoleMapping.query.filter(SSOGroupRoleMapping.id == mapping.id).count() == 1

    def test_a_platform_admin_mapping_never_sets_that_role(self, client, token, db_session, org):
        mapping = _mapping(db_session, org, "Platform-Admins", "platform_admin")
        uid = _scim_user(client, token)
        resp = call(client, "PATCH", f"/Groups/{mapping.id}", token, patch_body(
            {"op": "add", "path": "members", "value": [{"value": uid}]}))
        assert resp.status_code == 200
        assert _reload_role(db_session, uid) == "non_technical_owner"
        from app.models.user import User

        assert not User.query.filter(User.id == int(uid)).one().is_platform_admin

    def test_the_highest_priority_mapped_role_wins(self, client, token, db_session, org):
        low = _mapping(db_session, org, "Low", "solution_architect")
        high = _mapping(db_session, org, "High", "arb_member")
        uid = _scim_user(client, token)
        for group in (low, high):
            call(client, "PATCH", f"/Groups/{group.id}", token, patch_body(
                {"op": "add", "path": "members", "value": [{"value": uid}]}))
        assert _reload_role(db_session, uid) == "arb_member"

    def test_platform_admin_users_cannot_be_placed_in_groups(self, client, token, db_session, org):
        mapping = _mapping(db_session, org, "Anyone", "arb_member")
        admin = make_user(db_session, org, "padmin", administrator=True, is_platform_admin=True)
        resp = call(client, "PATCH", f"/Groups/{mapping.id}", token, patch_body(
            {"op": "add", "path": "members", "value": [{"value": str(admin.id)}]}))
        assert resp.status_code == 403
        assert _memberships(mapping.id) == []
