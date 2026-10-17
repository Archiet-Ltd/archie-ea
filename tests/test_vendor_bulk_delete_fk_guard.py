"""DEF-073, Capgemini dry-run pass 3: bulk-deleting a vendor with a product
attached raised psycopg2.errors.ForeignKeyViolation straight out of the
DELETE (vendor_products.vendor_organization_id carries no
ondelete=CASCADE) and reached the user as a raw "Internal Server Error"
toast, with the vendor left in place. The route now checks first and
returns a clear, user-safe refusal.
"""

import pytest


@pytest.mark.usefixtures("db_session")
def test_vendor_with_product_is_refused_not_500(app, db_session, make_org, tenant_ctx):
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
    from app.models import Role
    from app.models.user import User

    org = make_org("vendor-bulk-delete-guard")
    with tenant_ctx(org.id):
        vendor = VendorOrganization(name="ZZ-AUDIT Vendor")
        db_session.add(vendor)
        db_session.commit()
        db_session.add(VendorProduct(vendor_organization_id=vendor.id, name="ZZ-AUDIT Product"))
        db_session.commit()
        vendor_id = vendor.id

        user = User(email=f"vbd-{org.id}@example.com", organization_id=org.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        user.is_platform_admin = True
        user.role = Role.query.filter_by(name="Administrator").first()
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            resp = c.delete("/api/vendors/bulk", json={"ids": [vendor_id]})
            body = resp.get_data(as_text=True)
            assert resp.status_code < 500, body
            assert "psycopg2" not in body and "IntegrityError" not in body
            assert db_session.get(VendorOrganization, vendor_id) is not None


@pytest.mark.usefixtures("db_session")
def test_vendor_without_products_deletes_cleanly(app, db_session, make_org, tenant_ctx):
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.models import Role
    from app.models.user import User

    org = make_org("vendor-bulk-delete-clean")
    with tenant_ctx(org.id):
        vendor = VendorOrganization(name="ZZ-AUDIT Vendor Clean")
        db_session.add(vendor)
        user = User(email=f"vbdc-{org.id}@example.com", organization_id=org.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        user.is_platform_admin = True
        user.role = Role.query.filter_by(name="Administrator").first()
        db_session.add(user)
        db_session.commit()
        vendor_id = vendor.id

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            resp = c.delete("/api/vendors/bulk", json={"ids": [vendor_id]})
            assert resp.status_code == 200, resp.get_data(as_text=True)
            assert db_session.get(VendorOrganization, vendor_id) is None
