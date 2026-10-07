"""Two-organisation SCIM isolation (R1-B26 PR 1, acceptance criterion 2).

Organisation A's token must not list, read, create, update or deactivate
organisation B's users, nor read or change B's groups. Every assertion checks
both the HTTP answer and the database.
"""

from __future__ import annotations

import pytest

from tests._scim_test_helpers import call, issue_token, make_user, patch_body, user_body

pytestmark = pytest.mark.usefixtures("db_session")

ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


@pytest.fixture
def two_orgs(db_session, make_org):
    from app.models.miscellaneous import SSOGroupRoleMapping

    org_a, org_b = make_org("scim-iso-a"), make_org("scim-iso-b")
    user_a1 = make_user(db_session, org_a, "a1")
    user_a2 = make_user(db_session, org_a, "a2")
    user_b1 = make_user(db_session, org_b, "b1")
    user_b2 = make_user(db_session, org_b, "b2")
    group_a = SSOGroupRoleMapping(organization_id=org_a.id, sso_group_name="A-Group", role_name="arb_member", is_active=True)
    group_b = SSOGroupRoleMapping(organization_id=org_b.id, sso_group_name="B-Group", role_name="arb_member", is_active=True)
    db_session.add_all([group_a, group_b])
    db_session.flush()
    _row_a, token_a = issue_token(db_session, org_a)
    _row_b, token_b = issue_token(db_session, org_b)
    return {
        "org_a": org_a, "org_b": org_b, "a1": user_a1, "a2": user_a2, "b1": user_b1, "b2": user_b2,
        "group_a": group_a, "group_b": group_b, "token_a": token_a, "token_b": token_b,
    }


def _snapshot(user):
    return (user.deactivated_at, user.first_name, user.last_name, user.email, user.external_id, user.organization_id)


def _fresh(db_session, user):
    db_session.expire_all()
    from app.models.user import User

    return User.query.filter(User.id == user.id).one()


def _assert_scim_not_found(resp):
    assert resp.status_code == 404
    assert resp.get_json(force=True)["schemas"] == [ERROR_SCHEMA]


def test_listing_returns_only_the_callers_users(client, two_orgs):
    data = call(client, "GET", "/Users", two_orgs["token_a"]).get_json(force=True)
    ids = {r["id"] for r in data["Resources"]}
    assert ids == {str(two_orgs["a1"].id), str(two_orgs["a2"].id)}
    assert data["totalResults"] == 2
    assert str(two_orgs["b1"].id) not in ids and str(two_orgs["b2"].id) not in ids
    assert two_orgs["b1"].email not in str(data)


def test_a_filter_on_another_organisations_email_finds_nothing(client, two_orgs):
    data = call(
        client, "GET", f'/Users?filter=userName eq "{two_orgs["b1"].email}"', two_orgs["token_a"]
    ).get_json(force=True)
    assert data["totalResults"] == 0 and data["Resources"] == []
    ext = call(client, "GET", '/Users?filter=externalId eq "nothing"', two_orgs["token_a"]).get_json(force=True)
    assert ext["totalResults"] == 0


def test_get_on_another_organisations_user_is_404(client, two_orgs):
    resp = call(client, "GET", f"/Users/{two_orgs['b1'].id}", two_orgs["token_a"])
    _assert_scim_not_found(resp)
    assert two_orgs["b1"].email not in resp.get_data(as_text=True)


def test_put_on_another_organisations_user_is_404_and_changes_nothing(client, db_session, two_orgs):
    before = _snapshot(two_orgs["b1"])
    resp = call(
        client, "PUT", f"/Users/{two_orgs['b1'].id}", two_orgs["token_a"],
        user_body("hijack@example.com", given="Evil", family="Name", active=False),
    )
    _assert_scim_not_found(resp)
    assert _snapshot(_fresh(db_session, two_orgs["b1"])) == before


def test_patch_on_another_organisations_user_is_404_and_changes_nothing(client, db_session, two_orgs):
    before = _snapshot(two_orgs["b1"])
    resp = call(
        client, "PATCH", f"/Users/{two_orgs['b1'].id}", two_orgs["token_a"],
        patch_body({"op": "replace", "path": "active", "value": False},
                   {"op": "replace", "path": "name.givenName", "value": "Evil"}),
    )
    _assert_scim_not_found(resp)
    assert _snapshot(_fresh(db_session, two_orgs["b1"])) == before


def test_delete_on_another_organisations_user_is_404_and_does_not_deactivate(client, db_session, two_orgs):
    resp = call(client, "DELETE", f"/Users/{two_orgs['b1'].id}", two_orgs["token_a"])
    _assert_scim_not_found(resp)
    assert _fresh(db_session, two_orgs["b1"]).deactivated_at is None


def test_post_of_another_organisations_email_is_409_without_naming_it_and_creates_nothing(client, db_session, two_orgs):
    from app.models.user import User

    resp = call(client, "POST", "/Users", two_orgs["token_a"], user_body(two_orgs["b1"].email))
    assert resp.status_code == 409
    body = resp.get_json(force=True)
    assert body["scimType"] == "uniqueness"
    text = resp.get_data(as_text=True)
    assert two_orgs["org_b"].name not in text and str(two_orgs["org_b"].id) not in body["detail"]
    assert User.query.filter(User.email == two_orgs["b1"].email).count() == 1
    assert _fresh(db_session, two_orgs["b1"]).organization_id == two_orgs["org_b"].id
    assert _fresh(db_session, two_orgs["b1"]).provisioned_via is None


def test_a_rename_onto_another_organisations_email_is_refused(client, db_session, two_orgs):
    resp = call(
        client, "PATCH", f"/Users/{two_orgs['a1'].id}", two_orgs["token_a"],
        patch_body({"op": "replace", "path": "userName", "value": two_orgs["b1"].email}),
    )
    assert resp.status_code == 409
    assert _fresh(db_session, two_orgs["a1"]).email == two_orgs["a1"].email


def test_group_read_and_patch_across_organisations_is_404(client, db_session, two_orgs):
    from app.models.scim import ScimGroupMembership

    gid = two_orgs["group_b"].id
    _assert_scim_not_found(call(client, "GET", f"/Groups/{gid}", two_orgs["token_a"]))
    resp = call(
        client, "PATCH", f"/Groups/{gid}", two_orgs["token_a"],
        patch_body({"op": "add", "path": "members", "value": [{"value": str(two_orgs["a1"].id)}]}),
    )
    _assert_scim_not_found(resp)
    _assert_scim_not_found(call(client, "PUT", f"/Groups/{gid}", two_orgs["token_a"], {"displayName": "B-Group", "members": []}))
    _assert_scim_not_found(call(client, "DELETE", f"/Groups/{gid}", two_orgs["token_a"]))
    assert ScimGroupMembership.query.filter(ScimGroupMembership.group_mapping_id == gid).count() == 0
    names = [g["displayName"] for g in call(client, "GET", "/Groups", two_orgs["token_a"]).get_json(force=True)["Resources"]]
    assert names == ["A-Group"]
    assert call(client, "GET", '/Groups?filter=displayName eq "B-Group"', two_orgs["token_a"]).get_json(force=True)["totalResults"] == 0


def test_another_organisations_user_cannot_join_my_group(client, db_session, two_orgs):
    from app.models.scim import ScimGroupMembership

    resp = call(
        client, "PATCH", f"/Groups/{two_orgs['group_a'].id}", two_orgs["token_a"],
        patch_body({"op": "add", "path": "members", "value": [{"value": str(two_orgs["b1"].id)}]}),
    )
    assert resp.status_code in (400, 404)
    assert ScimGroupMembership.query.filter(ScimGroupMembership.group_mapping_id == two_orgs["group_a"].id).count() == 0
    resp = call(
        client, "PUT", f"/Groups/{two_orgs['group_a'].id}", two_orgs["token_a"],
        {"displayName": "A-Group", "members": [{"value": str(two_orgs["b1"].id)}]},
    )
    assert resp.status_code in (400, 404)
    assert ScimGroupMembership.query.count() == 0


def test_a_group_with_the_same_name_is_created_per_organisation(client, db_session, two_orgs):
    from app.models.miscellaneous import SSOGroupRoleMapping

    resp = call(client, "POST", "/Groups", two_orgs["token_a"], {"displayName": "B-Group"})
    assert resp.status_code == 201
    new_id = int(resp.get_json(force=True)["id"])
    assert new_id != two_orgs["group_b"].id
    assert SSOGroupRoleMapping.query.filter(SSOGroupRoleMapping.id == new_id).one().organization_id == two_orgs["org_a"].id


def test_missing_or_malformed_authorization_and_revoked_token_are_401(client, db_session, two_orgs):
    from app.services import provisioning_service

    for headers in ({}, {"Authorization": "Bearer"}, {"Authorization": "Token abc"}, {"Authorization": "Bearer nonsense"}):
        resp = client.get("/scim/v2/Users", headers=headers)
        assert resp.status_code == 401
        assert resp.get_json(force=True)["schemas"] == [ERROR_SCHEMA]
        assert resp.headers["WWW-Authenticate"] == "Bearer"
    from app.models.scim import ScimToken

    row = ScimToken.query.filter(ScimToken.organization_id == two_orgs["org_a"].id).one()
    provisioning_service.revoke_scim_token(two_orgs["org_a"].id, row.id, "test")
    resp = call(client, "GET", "/Users", two_orgs["token_a"])
    assert resp.status_code == 401
    assert resp.get_json(force=True)["schemas"] == [ERROR_SCHEMA]


def test_organisation_b_token_sees_organisation_b_users(client, two_orgs):
    data = call(client, "GET", "/Users", two_orgs["token_b"]).get_json(force=True)
    assert {r["id"] for r in data["Resources"]} == {str(two_orgs["b1"].id), str(two_orgs["b2"].id)}
    assert call(client, "GET", f"/Users/{two_orgs['b1'].id}", two_orgs["token_b"]).status_code == 200
    assert call(client, "GET", f"/Users/{two_orgs['a1'].id}", two_orgs["token_b"]).status_code == 404


def test_one_administrator_cannot_list_or_revoke_another_organisations_tokens(client, db_session, two_orgs, login_as):
    from app.models.scim import ScimToken

    admin_a = make_user(db_session, two_orgs["org_a"], "isoadmin", administrator=True)
    token_b_row = ScimToken.query.filter(ScimToken.organization_id == two_orgs["org_b"].id).one()

    login_as(client, admin_a)
    page = client.get("/admin/sso-settings").get_data(as_text=True)
    assert token_b_row.token_prefix not in page

    login_as(client, admin_a)
    resp = client.post(f"/admin/sso-settings/scim-tokens/{token_b_row.id}/revoke")
    assert resp.status_code == 404
    db_session.refresh(token_b_row)
    assert token_b_row.revoked_at is None
    assert call(client, "GET", "/Users", two_orgs["token_b"]).status_code == 200
