"""The shared, platform-wide vendor catalogue must fail closed when a vendor has no
recorded creator.

``VendorOrganization`` (``app/models/vendor/vendor_organization.py``) is deliberately
not tenant-scoped -- one shared reference catalogue for every organisation on the
platform (ADR-0003). Six write routes in ``app/application_mgmt/vendor_routes.py``
carried this exact pattern::

    if not (hasattr(current_user, 'is_admin') and current_user.is_admin()):
        if getattr(vendor, 'created_by_id', None) and vendor.created_by_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403  # or flash+redirect

Two separate bugs (refuter review of PR 428, vendor-catalogue findings):

1. ``current_user.is_admin()`` treated any organisation's own admin as authorised to
   edit/delete the shared catalogue -- the same global-flag problem as everywhere else
   in this review.
2. The real, additional bug: when ``vendor.created_by_id`` is ``None`` (every vendor
   seeded by a migration or seed script, never created through the UI) *and* the
   caller is not an admin at all, the inner ``if`` is never entered, so *no denial
   happens at all* -- an ordinary, non-admin, authenticated user could edit or delete
   any seeded vendor.

Both are replaced with ``_vendor_write_denied``: allowed only for a genuine platform
admin or the vendor's own actual creator; a missing ``created_by_id`` denies, it never
admits.
"""

from __future__ import annotations

import uuid

import pytest


def _user(db_session, org, *, platform=False):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(
        email=f"vendor-{uuid.uuid4().hex[:6]}@example.test",
        first_name="Vendor",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    user.password = uuid.uuid4().hex
    # Deliberately NOT an org admin -- is_admin() depends on Permission.ADMINISTER
    # via the user's Role, so a plain member role keeps this user a non-admin for
    # the scenario the bug gets wrong.
    user.is_org_admin = False
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _plain_member_role(db_session):
    """A Role that does not carry Permission.ADMINISTER, so the user built from it
    is unambiguously a non-admin -- the exact caller the created_by_id-None bug
    mishandles. ``Role.insert_roles()`` (seeded once per test session by the
    ``_schema`` fixture) gives the "User" role no ADMINISTER bit."""
    from app.models import Role

    role = Role.query.filter_by(name="User").first()
    if role is None:
        pytest.skip("no non-admin 'User' role seeded in this database")
    return role


def _seeded_vendor(db_session, *, name=None):
    """A vendor with no recorded creator -- the shape every migration/seed-script
    row has, and the scenario the fix must deny by default."""
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization(
        name=name or f"Seeded Vendor {uuid.uuid4().hex[:10]}",
        created_by_id=None,
    )
    db_session.add(vendor)
    db_session.flush()
    return vendor


def _login(db_session, client, login_as, user_id):
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


# ---------------------------------------------------------------------------
# Unit-level proof of the helper itself (fast, precise, no HTTP plumbing)
# ---------------------------------------------------------------------------


def test_helper_denies_a_non_admin_non_creator_on_a_seeded_vendor(app, db_session, make_org):
    from app.application_mgmt.vendor_routes import _vendor_write_denied
    from flask_login import login_user

    org = make_org("vendor-helper-a")
    member_role = _plain_member_role(db_session)
    from app.models.user import User

    caller = User(
        email=f"member-{uuid.uuid4().hex[:6]}@example.test",
        first_name="Member", last_name="Tester",
        organization_id=org.id, confirmed=True, role=member_role,
    )
    caller.password = uuid.uuid4().hex
    db_session.add(caller)
    db_session.flush()
    vendor = _seeded_vendor(db_session)
    db_session.commit()

    with app.test_request_context("/"):
        login_user(caller)
        assert _vendor_write_denied(vendor) is True


def test_helper_admits_the_vendors_own_creator_even_though_not_an_admin(app, db_session, make_org):
    from app.application_mgmt.vendor_routes import _vendor_write_denied
    from flask_login import login_user
    from app.models.user import User

    org = make_org("vendor-helper-creator")
    member_role = _plain_member_role(db_session)
    creator = User(
        email=f"creator-{uuid.uuid4().hex[:6]}@example.test",
        first_name="Creator", last_name="Tester",
        organization_id=org.id, confirmed=True, role=member_role,
    )
    creator.password = uuid.uuid4().hex
    db_session.add(creator)
    db_session.flush()
    from app.models.vendor.vendor_organization import VendorOrganization
    vendor = VendorOrganization(name=f"Created Vendor {uuid.uuid4().hex[:10]}", created_by_id=creator.id)
    db_session.add(vendor)
    db_session.flush()
    db_session.commit()

    with app.test_request_context("/"):
        login_user(creator)
        assert _vendor_write_denied(vendor) is False


def test_helper_admits_a_genuine_platform_admin_on_a_seeded_vendor(app, db_session, make_org):
    from app.application_mgmt.vendor_routes import _vendor_write_denied
    from flask_login import login_user

    org = make_org("vendor-helper-platform")
    platform_admin = _user(db_session, org, platform=True)
    vendor = _seeded_vendor(db_session)
    db_session.commit()

    with app.test_request_context("/"):
        login_user(platform_admin)
        assert _vendor_write_denied(vendor) is False


# ---------------------------------------------------------------------------
# HTTP-level sweep across all six write routes
# ---------------------------------------------------------------------------

# (method, path-template, response-mode) -- path-template takes vendor_id; two
# routes additionally take a product_id, filled in with 1 (the authorisation
# check runs before the route ever looks the product up).
VENDOR_WRITE_ROUTES = [
    ("post", "/dashboard/api/vendors/{vendor_id}/analyze-document", "json"),
    ("post", "/dashboard/api/vendors/{vendor_id}/apply-analysis", "json"),
    ("get", "/dashboard/applications/vendors/{vendor_id}/edit", "redirect"),
    ("post", "/dashboard/applications/vendors/{vendor_id}/delete", "redirect"),
    ("post", "/dashboard/applications/vendors/{vendor_id}/activate", "redirect"),
    ("post", "/dashboard/applications/vendors/{vendor_id}/products/1/deploy", "redirect"),
]


@pytest.mark.parametrize("method,path_template,mode", VENDOR_WRITE_ROUTES)
def test_a_non_admin_non_creator_is_refused_on_a_seeded_vendor(
    app, db_session, make_org, client, login_as, method, path_template, mode
):
    org = make_org("vendor-route-a")
    member_role = _plain_member_role(db_session)
    from app.models.user import User

    caller = User(
        email=f"member-{uuid.uuid4().hex[:6]}@example.test",
        first_name="Member", last_name="Tester",
        organization_id=org.id, confirmed=True, role=member_role,
    )
    caller.password = uuid.uuid4().hex
    db_session.add(caller)
    db_session.flush()
    vendor = _seeded_vendor(db_session)
    caller_id = caller.id
    vendor_id = vendor.id
    db_session.commit()

    _login(db_session, client, login_as, caller_id)
    path = path_template.format(vendor_id=vendor_id)
    response = getattr(client, method)(path)

    if mode == "json":
        assert response.status_code == 403
        assert response.get_json()["error"] == "Access denied"
    else:
        # A 302 to the vendors dashboard by itself is not a tight enough proof
        # for activate/deploy specifically: both routes also redirect there
        # with a *different* flash ("Error activating/deploying ...") when
        # wrongly allowed through and then fail for an unrelated reason (e.g.
        # missing form fields in this minimal request). Read the actual
        # flashed message from the session to prove denial happened for the
        # right reason, not just that *some* redirect occurred.
        assert response.status_code == 302
        with client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert any("Access denied" in message for _category, message in flashes), (
            f"expected an 'Access denied' flash for {method.upper()} {path}, got {flashes}"
        )

    # The seeded vendor itself must be untouched either way.
    db_session.expunge_all()
    from app.models.vendor.vendor_organization import VendorOrganization
    reloaded = db_session.get(VendorOrganization, vendor_id)
    assert reloaded is not None


def test_delete_vendor_refuses_a_non_admin_non_creator_and_the_vendor_survives(
    app, db_session, make_org, client, login_as
):
    """Focused, end-to-end proof for the specific route named in the brief: a
    non-admin, non-creator caller cannot delete a seeded vendor, and the vendor row
    is still there afterwards."""
    org = make_org("vendor-delete-a")
    member_role = _plain_member_role(db_session)
    from app.models.user import User

    caller = User(
        email=f"member-{uuid.uuid4().hex[:6]}@example.test",
        first_name="Member", last_name="Tester",
        organization_id=org.id, confirmed=True, role=member_role,
    )
    caller.password = uuid.uuid4().hex
    db_session.add(caller)
    db_session.flush()
    vendor = _seeded_vendor(db_session, name="Survives Delete Attempt")
    vendor_id = vendor.id
    db_session.commit()

    _login(db_session, client, login_as, caller.id)
    response = client.post(f"/dashboard/applications/vendors/{vendor_id}/delete")

    assert response.status_code == 302

    db_session.expunge_all()
    from app.models.vendor.vendor_organization import VendorOrganization
    assert db_session.get(VendorOrganization, vendor_id) is not None
