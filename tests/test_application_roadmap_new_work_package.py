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


def _work_package_count(db, name):
    from app.models.implementation_migration import WorkPackage

    return db.session.query(WorkPackage).filter(WorkPackage.name == name).count()


def test_another_organisations_application_answers_not_found_and_stores_nothing(client, app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "RMH")
        other_org = _make_org_id(db, "RMI")
        uid = _make_user_id(db, org, "RmhOwner", enterprise_role="enterprise_architect")
        mine_id, _ = _application(db, org, "Mine %s" % uuid.uuid4().hex[:6])
        theirs_id, _ = _application(db, other_org, "Theirs %s" % uuid.uuid4().hex[:6])
        name = "Cross organisation %s" % uuid.uuid4().hex[:8]
    try:
        with app.app_context():
            _login(client, uid)
            r = client.post("/api/applications/%d/work-packages" % theirs_id, json=_payload(name))
            assert r.status_code == 404, r.get_data(as_text=True)
            r = client.post("/api/applications/999999999/work-packages", json=_payload(name))
            assert r.status_code == 404, r.get_data(as_text=True)
            db.session.remove()
            assert _work_package_count(db, name) == 0
    finally:
        with app.app_context():
            _cleanup_ids(db, User, [uid])
            _cleanup_ids(db, Organization, [org, other_org])


def test_a_chosen_element_that_does_not_resolve_answers_400_and_stores_nothing(client, app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "RMJ")
        other_org = _make_org_id(db, "RMK")
        uid = _make_user_id(db, org, "RmjOwner", enterprise_role="enterprise_architect")
        mine_id, _ = _application(db, org, "Mine %s" % uuid.uuid4().hex[:6])
        _, foreign_element = _application(db, other_org, "Theirs %s" % uuid.uuid4().hex[:6])
        name = "Bad element %s" % uuid.uuid4().hex[:8]
    try:
        with app.app_context():
            _login(client, uid)
            for element_id in (foreign_element, 999999999):
                r = client.post(
                    "/api/applications/%d/work-packages" % mine_id,
                    json=_payload(name, archimate_element_id=element_id),
                )
                assert r.status_code == 400, r.get_data(as_text=True)
                assert "element" in r.get_json()["error"].lower()
            db.session.remove()
            assert _work_package_count(db, name) == 0
    finally:
        with app.app_context():
            _cleanup_ids(db, User, [uid])
            _cleanup_ids(db, Organization, [org, other_org])


def test_a_package_on_its_own_application_names_no_element_badge(client, app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    with app.app_context():
        org = _make_org_id(db, "RML")
        uid = _make_user_id(db, org, "RmlOwner", enterprise_role="enterprise_architect")
        own_name = "Own Core %s" % uuid.uuid4().hex[:6]
        own_id, _ = _application(db, org, own_name)
        _, chosen_element = _application(db, org, "Chosen %s" % uuid.uuid4().hex[:6])
    try:
        with app.app_context():
            _login(client, uid)
            r = client.post("/api/applications/%d/work-packages" % own_id, json=_payload("Nothing chosen"))
            assert r.status_code == 200, r.get_data(as_text=True)
            assert r.get_json()["work_package"]["archimate_element_name"] is None
            r = client.post(
                "/api/applications/%d/work-packages" % own_id,
                json=_payload("Something chosen", archimate_element_id=chosen_element),
            )
            assert r.get_json()["work_package"]["archimate_element_name"]

            html = client.get("/applications/%d/roadmap" % own_id).get_data(as_text=True)
            rows = html.split('data-testid="roadmap-work-package-row"')[1:]
            plain = next(x for x in rows if "Nothing chosen" in x).split("</tr>")[0]
            chosen = next(x for x in rows if "Something chosen" in x).split("</tr>")[0]
            assert own_name not in plain
            assert "Chosen " in chosen
    finally:
        with app.app_context():
            _cleanup_ids(db, User, [uid])
            _cleanup_ids(db, Organization, [org])
