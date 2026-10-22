"""An update-mode import must never blank a stored value with an empty or unmapped cell.

Only a non-empty incoming value may overwrite. Routed through the real
``POST /applications/import`` handler as a signed-in user.
"""

from __future__ import annotations

import io
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

NAMES = [f"Merge App {i}" for i in range(6)]


def _user(db_session, org_id, label):
    from app.models.user import Role, User

    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter_by(name="Administrator").first() or Role(name="Administrator")
    db_session.add(role)
    user.role = role
    db_session.flush()
    return user


def _seed(db_session, org_id, tag):
    from app.models.application_portfolio import ApplicationComponent

    apps = []
    for n in NAMES:
        a = ApplicationComponent(
            name=f"{n} {tag}",
            business_owner="Pat Owner",
            application_owner="Sam Manager",
            vendor_name="Acme Vendor",
            description="Original description",
            deployment_status="production",
        )
        a.organization_id = org_id
        db_session.add(a)
        apps.append(a)
    db_session.flush()
    return apps


def _post(client, login_as, user, tag, csv_text):
    login_as(client, user)
    payload = (io.BytesIO(csv_text.encode()), "apps.csv")
    resp = client.post(
        "/applications/import",
        data={"file": payload, "import_mode": "update", "quick_mode": "true",
              "enable_ai": "false"},
        content_type="multipart/form-data",
    )
    assert resp.status_code in (200, 302), resp.status_code
    with client.session_transaction() as sess:
        flashes = sess.get("_flashes") or []
    assert any("6 updated" in m for _, m in flashes), flashes
    return resp


def _reload(db_session, org_id, ids):
    from app.models.application_portfolio import ApplicationComponent

    db_session.expire_all()
    return [db_session.get(ApplicationComponent, i) for i in ids]


def test_unmapped_owner_column_keeps_owner_vendor_status(app, db_session, make_org, client, login_as, tenant_ctx):
    org = make_org("a")
    user = _user(db_session, org.id, "a")
    tag = uuid.uuid4().hex[:6]
    with tenant_ctx(org.id):
        apps = _seed(db_session, org.id, tag)
        ids = [a.id for a in apps]
        rows = "\n".join(f"{a.name},Refreshed description" for a in apps)
        _post(client, login_as, user, tag, "Name,Description\n" + rows + "\n")
        for a in _reload(db_session, org.id, ids):
            assert a.business_owner == "Pat Owner"
            assert a.application_owner == "Sam Manager"
            assert a.vendor_name == "Acme Vendor"
            assert a.deployment_status == "production"
            assert a.description == "Refreshed description"


def test_empty_cells_keep_existing_values(app, db_session, make_org, client, login_as, tenant_ctx):
    org = make_org("a")
    user = _user(db_session, org.id, "a")
    tag = uuid.uuid4().hex[:6]
    with tenant_ctx(org.id):
        apps = _seed(db_session, org.id, tag)
        ids = [a.id for a in apps]
        rows = "\n".join(f"{a.name},,,," for a in apps)
        _post(client, login_as, user, tag,
              "Name,Owner,Application Manager,Vendor,Status\n" + rows + "\n")
        for a in _reload(db_session, org.id, ids):
            assert a.business_owner == "Pat Owner"
            assert a.deployment_status == "production"


def test_non_empty_values_still_overwrite(app, db_session, make_org, client, login_as, tenant_ctx):
    org = make_org("a")
    user = _user(db_session, org.id, "a")
    tag = uuid.uuid4().hex[:6]
    with tenant_ctx(org.id):
        apps = _seed(db_session, org.id, tag)
        ids = [a.id for a in apps]
        rows = "\n".join(f"{a.name},New Owner,New Vendor" for a in apps)
        _post(client, login_as, user, tag, "Name,Owner,Vendor\n" + rows + "\n")
        for a in _reload(db_session, org.id, ids):
            assert a.business_owner == "New Owner"


def test_import_into_one_organisation_never_touches_another(app, db_session, make_org, client, login_as, tenant_ctx):
    org_a, org_b = make_org("a"), make_org("b")
    user_a = _user(db_session, org_a.id, "a")
    tag = uuid.uuid4().hex[:6]
    with tenant_ctx(org_b.id):
        apps_b = _seed(db_session, org_b.id, tag)
        ids_b = [a.id for a in apps_b]
        names_b = [a.name for a in apps_b]
    with tenant_ctx(org_a.id):
        apps_a = _seed(db_session, org_a.id, tag)
        ids_a = [a.id for a in apps_a]
        rows = "\n".join(f"{n},Org A owner,Org A description" for n in names_b)
        _post(client, login_as, user_a, tag, "Name,Owner,Description\n" + rows + "\n")
        for a in _reload(db_session, org_a.id, ids_a):
            assert a.business_owner == "Org A owner"
    with tenant_ctx(org_b.id):
        for a in _reload(db_session, org_b.id, ids_b):
            assert a.business_owner == "Pat Owner"
            assert a.description == "Original description"
