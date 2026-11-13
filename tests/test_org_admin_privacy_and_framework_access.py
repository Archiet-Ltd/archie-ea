"""An organisation administrator opens Data subject requests and Framework
configuration for their own organisation.

Two real organisations throughout. Each has an administrator whose working
persona is not the Data Protection Officer's, a person with no admin rights, and
records of its own. Nobody reads the other organisation's requests or
configurations; the shared catalogue pages and every change to it stay with the
platform administrator, and the dashboard says so.
"""

from __future__ import annotations

import uuid

import pytest


def _suffix():
    return uuid.uuid4().hex[:8]


def _person(db_session, org, *, admin=False, platform=False, persona="enterprise_architect"):
    from app.models.org_role import OrgRole
    from app.models.user import Permission, Role, User

    Role.insert_roles()
    user = User(
        confirmed=True,
        email="orgadmin-%s@example.test" % _suffix(),
        first_name="Case",
        last_name="Person",
        organization_id=org.id,
        enterprise_role=persona,
        password_hash="scrypt:dummy",
    )
    if platform:
        user.is_platform_admin = True
        user.role = Role.query.filter(
            Role.permissions.op("&")(Permission.ADMINISTER) == Permission.ADMINISTER
        ).first()
    else:
        user.role = Role.query.filter_by(name="Architect").one()
    db_session.add(user)
    db_session.flush()
    if admin:
        user.grant_org_admin()
        OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
        db_session.flush()
    return user


def _config(db_session, org, name):
    from app.models.framework_configuration import CapabilityFrameworkConfiguration

    cfg = CapabilityFrameworkConfiguration(
        configuration_name=name,
        configuration_code="C-%s" % _suffix(),
        organization_id=org.id if org else None,
        organization_name=org.name if org else None,
        status="draft",
    )
    db_session.add(cfg)
    db_session.flush()
    return cfg


@pytest.fixture
def two_orgs(db_session, make_org, tenant_ctx):
    from app.services.gdpr_service import GDPRService

    a, b = make_org("dsr-a"), make_org("dsr-b")
    out = {"a": a, "b": b}
    for key, org in (("a", a), ("b", b)):
        out["admin_" + key] = _person(db_session, org, admin=True)
        out["member_" + key] = _person(db_session, org)
        ref = "Subject %s %s" % (key.upper(), _suffix())
        out["ref_" + key] = ref
        with tenant_ctx(org.id):
            out["req_" + key] = GDPRService.create_request(
                org.id, out["admin_" + key], "access", subject_reference=ref
            )
        name = "Framework %s %s" % (key.upper(), _suffix())
        out["cfg_name_" + key] = name
        out["cfg_" + key] = _config(db_session, org, name)
    out["shared_name"] = "Shared %s" % _suffix()
    out["shared"] = _config(db_session, None, out["shared_name"])
    db_session.flush()
    return out


def _get(client, login_as, user, path):
    login_as(client, user)
    return client.get(path)


# ---------------------------------------------------------------- data subject requests


def test_org_admin_opens_own_requests_and_not_the_other_organisations(two_orgs, client, login_as):
    resp = _get(client, login_as, two_orgs["admin_a"], "/compliance/data-subject-requests")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert two_orgs["ref_a"] in page
    assert two_orgs["ref_b"] not in page

    resp = _get(client, login_as, two_orgs["admin_b"], "/compliance/data-subject-requests")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert two_orgs["ref_b"] in page
    assert two_orgs["ref_a"] not in page


def test_org_admin_opens_own_request_but_other_organisations_reads_as_not_found(two_orgs, client, login_as):
    own = _get(client, login_as, two_orgs["admin_a"], "/compliance/data-subject-requests/%s" % two_orgs["req_a"].id)
    assert own.status_code == 200
    assert two_orgs["ref_a"] in own.get_data(as_text=True)
    other = _get(client, login_as, two_orgs["admin_a"], "/compliance/data-subject-requests/%s" % two_orgs["req_b"].id)
    assert other.status_code == 404
    assert two_orgs["ref_b"] not in other.get_data(as_text=True)


def test_org_admin_records_a_request_in_their_own_organisation(two_orgs, client, login_as):
    from app.models.gdpr_request import GDPRRequest

    ref = "Walk-in %s" % _suffix()
    login_as(client, two_orgs["admin_a"])
    resp = client.post(
        "/compliance/data-subject-requests",
        data={"request_type": "access", "subject_reference": ref},
    )
    assert resp.status_code in (302, 303)
    saved = GDPRRequest.query.filter_by(subject_reference=ref).one()
    assert saved.organization_id == two_orgs["a"].id


def test_person_without_admin_rights_is_still_refused_data_subject_requests(two_orgs, client, login_as):
    resp = _get(client, login_as, two_orgs["member_a"], "/compliance/data-subject-requests")
    assert resp.status_code == 403
    assert two_orgs["ref_a"] not in resp.get_data(as_text=True)


def test_sidebar_offers_data_subject_requests_to_org_admin_only(two_orgs, app):
    from app.decorators.requires_role import may_handle_data_subject_requests

    assert may_handle_data_subject_requests(two_orgs["admin_a"]) is True
    assert may_handle_data_subject_requests(two_orgs["member_a"]) is False


# ---------------------------------------------------------------- framework configuration


def test_org_admin_framework_dashboard_lists_only_own_configurations(two_orgs, client, login_as):
    resp = _get(client, login_as, two_orgs["admin_a"], "/framework-config/")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert two_orgs["cfg_name_a"] in page
    assert two_orgs["cfg_name_b"] not in page

    resp = _get(client, login_as, two_orgs["admin_b"], "/framework-config/")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert two_orgs["cfg_name_b"] in page
    assert two_orgs["cfg_name_a"] not in page


def test_org_admin_framework_dashboard_says_catalogue_changes_are_the_platforms(two_orgs, client, login_as):
    page = _get(client, login_as, two_orgs["admin_a"], "/framework-config/").get_data(as_text=True)
    assert "platform administrator" in page
    # The platform's own controls are not offered to someone who cannot use them.
    assert "framework-config-new" not in page
    assert "/framework-config/templates" not in page
    assert "/framework-config/extensions" not in page


def test_org_admin_reads_own_configuration_but_not_the_other_organisations(two_orgs, client, login_as):
    own = _get(client, login_as, two_orgs["admin_a"], "/framework-config/configuration/%s" % two_orgs["cfg_a"].id)
    assert own.status_code == 200
    other = _get(client, login_as, two_orgs["admin_a"], "/framework-config/configuration/%s" % two_orgs["cfg_b"].id)
    assert other.status_code == 404
    shared = _get(client, login_as, two_orgs["admin_a"], "/framework-config/configuration/%s" % two_orgs["shared"].id)
    assert shared.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/framework-config/templates",
        "/framework-config/extensions",
        "/framework-config/instances",
        "/framework-config/wizard",
        "/framework-config/migration",
        "/framework-config/validation",
        "/framework-config/configuration/{own}/edit",
    ],
)
def test_catalogue_pages_and_edits_stay_with_the_platform_administrator(two_orgs, client, login_as, path):
    resp = _get(client, login_as, two_orgs["admin_a"], path.format(own=two_orgs["cfg_a"].id))
    assert resp.status_code == 403


def test_org_admin_cannot_change_the_platform_wide_catalogue_through_the_interface(two_orgs, client, login_as):
    login_as(client, two_orgs["admin_a"])
    resp = client.post(
        "/api/framework-config/configurations",
        json={"configuration_name": "x", "configuration_code": "X-%s" % _suffix()},
    )
    assert resp.status_code == 403


def test_person_without_admin_rights_is_refused_the_framework_dashboard(two_orgs, client, login_as):
    resp = _get(client, login_as, two_orgs["member_a"], "/framework-config/")
    assert resp.status_code == 403
    assert two_orgs["cfg_name_a"] not in resp.get_data(as_text=True)


def test_platform_administrator_still_sees_every_organisations_configurations(two_orgs, db_session, make_org, client, login_as):
    platform = _person(db_session, make_org("platform"), platform=True)
    page = _get(client, login_as, platform, "/framework-config/").get_data(as_text=True)
    assert two_orgs["cfg_name_a"] in page
    assert two_orgs["cfg_name_b"] in page
    assert "framework-config-new" in page
