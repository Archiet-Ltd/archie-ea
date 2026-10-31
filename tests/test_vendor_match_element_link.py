"""Confirming a vendor match gives the application its ArchiMate element through
the one shared writer, and never crashes on an application that already has one.

Both confirm-vendor-matches routes are exercised for real (login, request, commit):

* the API route in app/api/application_routes.py
* the deprecated dashboard route in app/application_mgmt/consolidation_routes.py

The deprecated route prefetches ``ArchiMateElement.query.limit(5000)`` and looks the
application's element up in that window. An application whose element sits outside
the window used to reach ``sync_archimate_element`` (which returns None for an
already-linked row) and then dereference ``None.id``; the per-match ``except`` hid
it, so the response said success while the match was dropped.
"""

import json
import uuid

import pytest
from sqlalchemy import text

API_PATH = "/api/applications/confirm-vendor-matches"
DASHBOARD_PATH = "/dashboard/api/applications/confirm-vendor-matches"
PAD_ELEMENTS = 5000  # the consolidation route's prefetch window


@pytest.fixture
def vendor_match_world(app):
    """A committed organisation with an admin, one vendor product, and cleanup."""
    from app import db
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    marker = uuid.uuid4().hex[:8]
    world = {"marker": marker}
    with app.app_context():
        org = Organization(name="VMatch %s" % marker, slug="vmatch-%s" % marker)
        db.session.add(org)
        db.session.commit()
        role = Role.query.filter_by(name="Administrator").first()
        user = User(
            email="vmatch-%s@example.com" % marker,
            first_name="Vendor",
            last_name="Match",
            confirmed=True,
            organization_id=org.id,
            enterprise_role="platform_admin",
        )
        user.role = role
        user.password = "probe-password-%s" % marker
        db.session.add(user)
        vendor = VendorOrganization(name="VMatch Vendor %s" % marker)
        db.session.add(vendor)
        db.session.flush()
        product = VendorProduct(vendor_organization_id=vendor.id, name="VMatch Product %s" % marker)
        db.session.add(product)
        db.session.commit()
        world.update(
            org_id=org.id, user_id=user.id, vendor_id=vendor.id, product_id=product.id,
            vendor_name=vendor.name,
        )

    yield world

    with app.app_context():
        from app.models.organization import Organization
        from app.services.org_deletion_service import OrganizationDeletionService

        org_id = world["org_id"]
        db.session.rollback()
        if db.session.get(Organization, org_id) is not None:
            OrganizationDeletionService.delete_organization(org_id)
        # The vendor catalogue is shared reference data, not owned by the organisation.
        db.session.execute(text("DELETE FROM vendor_products WHERE id = :p"), {"p": world["product_id"]})
        db.session.execute(text("DELETE FROM vendor_organizations WHERE id = :v"), {"v": world["vendor_id"]})
        db.session.commit()


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid

    sid = mint_test_sid(user_id, app=client.application)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        sess["_sid"] = sid


def _seed_unlinked_application(app, world, name):
    """An application written with plain SQL: no flush listener, so no element."""
    from app import db

    with app.app_context():
        app_id = db.session.execute(
            text(
                "INSERT INTO application_components (name, organization_id) "
                "VALUES (:n, :o) RETURNING id"
            ),
            {"n": name, "o": world["org_id"]},
        ).scalar()
        db.session.commit()
        return app_id


def _confirm(app, world, path, application_id):
    client = app.test_client()
    _login(client, world["user_id"])
    return client.post(
        path,
        json={"matches": [{
            "application_id": application_id,
            "vendor_id": world["vendor_id"],
            "confidence": 85,
        }]},
    )


def _application_state(app, application_id):
    from app import db

    with app.app_context():
        pointer = db.session.execute(
            text("SELECT archimate_element_id FROM application_components WHERE id = :i"),
            {"i": application_id},
        ).scalar()
        element = None
        if pointer is not None:
            element = db.session.execute(
                text("SELECT id, type, layer, properties, custom_properties "
                     "FROM archimate_elements WHERE id = :e"),
                {"e": pointer},
            ).mappings().first()
            element = dict(element) if element is not None else None
        return pointer, element


@pytest.mark.parametrize("path", [API_PATH, DASHBOARD_PATH], ids=["api", "dashboard"])
def test_confirm_creates_element_with_shared_provenance_and_vendor_metadata(
    app, vendor_match_world, path,
):
    """An unlinked application gets an element from the shared writer (its
    provenance in custom_properties) and the vendor-match metadata is kept in the
    legacy properties JSON."""
    world = vendor_match_world
    application_id = _seed_unlinked_application(app, world, "VMatch App %s" % world["marker"])

    response = _confirm(app, world, path, application_id)

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["confirmed_count"] == 1
    pointer, element = _application_state(app, application_id)
    assert pointer is not None and element is not None, "the application was not linked to an element"
    assert element["type"] == "ApplicationComponent"
    assert element["custom_properties"] == {"source_model": "ApplicationComponent"}
    properties = json.loads(element["properties"])
    assert properties["vendor_matched"] is True
    assert properties["vendor_id"] == world["vendor_id"]
    assert properties["vendor_name"] == world["vendor_name"]
    assert properties["match_confidence"] == 85


def test_confirm_on_dashboard_route_survives_element_outside_prefetch_window(app, vendor_match_world):
    """An application whose element exists but is not among the first 5000 elements
    the route prefetches is still confirmed: its relationship is written, its
    existing element is kept (no duplicate) and its metadata is not overwritten."""
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    world = vendor_match_world
    name = "VMatch Window App %s" % world["marker"]
    # 5000 elements first, so the application's own element is row 5001.
    with app.app_context():
        db.session.execute(
            text(
                "INSERT INTO archimate_elements (name, type, layer, organization_id) "
                "SELECT 'pad ' || g, 'ApplicationComponent', 'Application', :o "
                "FROM generate_series(1, :n) AS g"
            ),
            {"o": world["org_id"], "n": PAD_ELEMENTS},
        )
        db.session.commit()

        row = ApplicationComponent(name=name, organization_id=world["org_id"])
        db.session.add(row)
        db.session.commit()
        application_id, original_pointer = row.id, row.archimate_element_id
        assert original_pointer is not None, "the flush listener must have mirrored the application"
        db.session.execute(
            text("UPDATE archimate_elements SET properties = :p WHERE id = :e"),
            {"p": json.dumps({"keep": "me"}), "e": original_pointer},
        )
        db.session.commit()

    response = _confirm(app, world, DASHBOARD_PATH, application_id)

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["confirmed_count"] == 1, (
        "the match was silently dropped: the route dereferenced a missing element")
    pointer, element = _application_state(app, application_id)
    assert pointer == original_pointer, "the application must keep its existing element"
    assert json.loads(element["properties"]) == {"keep": "me"}
    with app.app_context():
        linked = db.session.execute(
            text("SELECT count(*) FROM application_vendor_products "
                 "WHERE archimate_element_id = :e AND vendor_product_id = :p"),
            {"e": original_pointer, "p": world["product_id"]},
        ).scalar()
        same_name = db.session.execute(
            text("SELECT count(*) FROM archimate_elements WHERE organization_id = :o AND name = :n"),
            {"o": world["org_id"], "n": name},
        ).scalar()
    assert linked == 1, "the vendor relationship was not written"
    assert same_name == 1, "a second element was created for the same application"
