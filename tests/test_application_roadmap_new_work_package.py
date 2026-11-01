"""A work package created on an application's roadmap is linked only to what the person chose.

With no ArchiMate element chosen it carries the element of the application whose
roadmap it was made on (or none), never the organisation's first application
component. The strategic work package list resolves a capability by name among
the capabilities not retired into another.
"""

import datetime
import uuid

import pytest

from tests.test_ba_tenant_and_authz import _cleanup_ids, _login, _make_org_id, _make_user_id


@pytest.fixture(scope="module")
def app():
    from app import create_app, db

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _application(db, org_id, name):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(name=name, type="ApplicationComponent", layer="Application", organization_id=org_id)
    db.session.add(element)
    db.session.flush()
    comp = ApplicationComponent(name=name, organization_id=org_id, lifecycle_status="operational")
    db.session.add(comp)
    db.session.flush()
    comp.archimate_element_id = element.id
    db.session.commit()
    return comp.id, element.id


def _payload(name, **extra):
    today = datetime.date.today()
    body = {
        "name": name,
        "transformation_type": "Modernization",
        "start_date": today.isoformat(),
        "target_date": (today + datetime.timedelta(days=30)).isoformat(),
    }
    body.update(extra)
    return body


def test_new_work_package_is_not_linked_to_another_application(client, app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "RMF")
        other_org = _make_org_id(db, "RMG")
        uid = _make_user_id(db, org, "RmfOwner", enterprise_role="enterprise_architect")
        # Billing Hub is created first, so it is the organisation's first component.
        billing_id, billing_element = _application(db, org, "Billing Hub %s" % uuid.uuid4().hex[:6])
        ledger_id, ledger_element = _application(db, org, "Ledger Core")
        _application(db, other_org, "Other Org App")
        billing_name = db.session.get(__import__("app.models.archimate_core", fromlist=["x"]).ArchiMateElement, billing_element).name
    try:
        with app.app_context():
            _login(client, uid)
            r = client.post("/api/applications/%d/work-packages" % ledger_id, json=_payload("Retire the old ledger"))
            assert r.status_code == 200, r.get_data(as_text=True)
            created = r.get_json()["work_package"]
            assert created["archimate_element_id"] != billing_element
            assert created["archimate_element_name"] != billing_name
            assert created["archimate_element_id"] == ledger_element

            page = client.get("/applications/%d/roadmap" % ledger_id)
            assert page.status_code == 200
            html = page.get_data(as_text=True)
            assert "Retire the old ledger" in html
            row = next(r for r in html.split('data-testid="roadmap-work-package-row"')[1:] if "Retire the old ledger" in r)
            row = row.split("</tr>")[0]
            assert billing_name not in row

            # A person's own choice is kept.
            r = client.post(
                "/api/applications/%d/work-packages" % ledger_id,
                json=_payload("Chosen link", archimate_element_id=billing_element),
            )
            assert r.status_code == 200, r.get_data(as_text=True)
            assert r.get_json()["work_package"]["archimate_element_id"] == billing_element
    finally:
        with app.app_context():
            _cleanup_ids(db, User, [uid])
            _cleanup_ids(db, Organization, [org, other_org])


def test_strategic_work_packages_read_the_active_capability(client, app):
    from app import db
    from app.models.organization import Organization
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "RMS")
        uid = _make_user_id(db, org, "RmsOwner", enterprise_role="enterprise_architect")
        name = "Payments %s" % uuid.uuid4().hex[:6]
        # The retired copy is stored first, so an unfiltered name lookup finds it first.
        retired = UnifiedCapability(name=name, level=1, organization_id=org)
        db.session.add(retired)
        db.session.flush()
        kept = UnifiedCapability(name=name, level=2, organization_id=org)
        db.session.add(kept)
        db.session.flush()
        retired.retired_into_id = kept.id
        wp = UnifiedWorkPackage(
            name="Modernise payments", organization_id=org, layer="application", business_capability=name,
        )
        db.session.add(wp)
        db.session.commit()
        kept_id = kept.id
    try:
        with app.app_context():
            _login(client, uid)
            r = client.get("/api/strategic-work-packages")
            assert r.status_code == 200, r.get_data(as_text=True)
            rows = [w for w in r.get_json()["work_packages"] if w["name"] == "Modernise payments"]
            assert rows and rows[0]["capability_level"] == "L2"
    finally:
        with app.app_context():
            db.session.query(UnifiedWorkPackage).filter_by(organization_id=org).delete()
            db.session.query(UnifiedCapability).filter_by(organization_id=org, retired_into_id=kept_id).delete()
            db.session.query(UnifiedCapability).filter_by(organization_id=org).delete()
            db.session.commit()
            _cleanup_ids(db, User, [uid])
            _cleanup_ids(db, Organization, [org])
