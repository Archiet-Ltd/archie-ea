"""Capability support lists count only the organisation's own applications."""
from __future__ import annotations

import uuid

import pytest

from app.models.application_portfolio import ApplicationComponent
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import UnifiedCapability
from app.modules.architecture.services.capability_support import capability_support


def _cap(db_session, org, name):
    cap = UnifiedCapability(name=name, organization_id=org.id, level=1)
    db_session.add(cap)
    db_session.flush()
    return cap


def _app(db_session, org, name):
    a = ApplicationComponent(name=name, organization_id=org.id)
    db_session.add(a)
    db_session.flush()
    return a


def _map(db_session, cap, app_, active=True):
    db_session.add(UnifiedApplicationCapabilityMapping(
        unified_capability_id=cap.id, application_component_id=app_.id,
        support_level="full", coverage_percentage=80, is_active=active,
    ))
    db_session.flush()


@pytest.fixture
def orgs(make_org):
    return make_org("support-a"), make_org("support-b")


def test_none_and_many_lists(db_session, orgs, tenant_ctx):
    org_a, _ = orgs
    bare, busy, fine = (_cap(db_session, org_a, n) for n in ("Bare", "Busy", "Fine"))
    apps = [_app(db_session, org_a, "App %d" % i) for i in range(4)]
    for a in apps[:3]:
        _map(db_session, busy, a)
    _map(db_session, fine, apps[0])
    _map(db_session, fine, apps[1])
    with tenant_ctx(org_a.id):
        result = capability_support()
    assert [c["name"] for c in result["none"]] == ["Bare"]
    assert [(c["name"], c["count"]) for c in result["many"]] == [("Busy", 3)]
    assert result["many"][0]["supporters"] == ["App 0", "App 1", "App 2"]
    assert result["total"] == 3


def test_inactive_mapping_is_not_support(db_session, orgs, tenant_ctx):
    org_a, _ = orgs
    cap = _cap(db_session, org_a, "Dormant")
    _map(db_session, cap, _app(db_session, org_a, "Old app"), active=False)
    with tenant_ctx(org_a.id):
        assert [c["name"] for c in capability_support()["none"]] == ["Dormant"]


def test_other_organisations_application_never_counts(db_session, orgs, tenant_ctx):
    org_a, org_b = orgs
    cap = _cap(db_session, org_a, "Guarded")
    _map(db_session, cap, _app(db_session, org_b, "Foreign app"))
    with tenant_ctx(org_a.id):
        result = capability_support()
    assert [c["name"] for c in result["none"]] == ["Guarded"]


def test_other_organisation_capabilities_are_not_listed(db_session, orgs, tenant_ctx):
    org_a, org_b = orgs
    _cap(db_session, org_b, "B only capability")
    with tenant_ctx(org_a.id):
        names = [c["name"] for c in capability_support()["none"]]
    assert "B only capability" not in names


def test_page_renders_per_organisation(db_session, orgs, client, login_as):
    from app.models.user import User

    org_a, org_b = orgs
    _cap(db_session, org_a, "Zebra capability")
    users = []
    for org in orgs:
        u = User(email="support-%s@example.com" % uuid.uuid4().hex[:8], organization_id=org.id, confirmed=True)
        u.password = "not-used-in-tests-123"
        db_session.add(u)
        users.append(u)
    db_session.commit()
    login_as(client, users[0])
    assert "Zebra capability" in client.get("/architecture/capability-support/").get_data(as_text=True)
    login_as(client, users[1])
    assert "Zebra capability" not in client.get("/architecture/capability-support/").get_data(as_text=True)


def test_page_requires_sign_in(app, db_session):
    assert app.test_client().get("/architecture/capability-support/").status_code in (302, 401)
