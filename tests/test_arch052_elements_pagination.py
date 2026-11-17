"""ARCH-052: /architecture/api/elements must be paginated like its siblings.

Before the fix this endpoint returned only ``elements`` + ``status``, ignored
``per_page`` entirely, and never told a caller how many rows exist in total —
so a caller paginating on the response has no way to know whether it received
everything. This asserts the endpoint now matches /applications/api/list's
pagination envelope contract (page/pages/per_page/total, where total is the
full collection size, not the page size) while keeping the legacy keys for
back-compat, and that malformed page/per_page values are rejected rather than
silently ignored.
"""

import uuid

import pytest


@pytest.fixture
def org_client(app, db_session, make_org, login_as):
    from app.models.user import Permission, Role, User

    org = make_org("arch052")
    user = User(
        email=f"arch052-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Arch",
        last_name="Zero52",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="platform_admin",
    )
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter(Role.name.in_(("Administrator", "Admin", "admin"))).first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()
    elif role.permissions is None or (role.permissions & Permission.ADMINISTER) != Permission.ADMINISTER:
        role.permissions = Permission.ADMINISTER
    user.role = role
    db_session.flush()
    client = app.test_client()
    login_as(client, user)
    return org, client, user


def _seed_elements(db_session, tenant_ctx, org_id, n):
    from app import db
    from app.models.archimate_core import ArchiMateElement

    with tenant_ctx(org_id):
        for i in range(n):
            db.session.add(
                ArchiMateElement(
                    name=f"ARCH052 Elem {uuid.uuid4().hex[:6]}-{i}",
                    type="ApplicationComponent",
                    layer="application",
                )
            )
        db.session.flush()


def test_elements_endpoint_returns_pagination_envelope(org_client, db_session, tenant_ctx, login_as):
    """total/page/pages/per_page must be present, and total is the full count."""
    org, client, user = org_client
    _seed_elements(db_session, tenant_ctx, org.id, 3)
    # Seeding flushes outside the client's session and caches an anonymous user on g;
    # sign in again, as the login_as fixture documents, so the request knows who asks.
    login_as(client, user)

    resp = client.get("/architecture/api/elements?page=1&per_page=2")
    assert resp.status_code == 200
    data = resp.get_json()

    # Legacy keys kept for back-compat.
    assert "elements" in data
    assert "status" in data

    # New pagination envelope, matching /applications/api/list's contract.
    assert "total" in data, "endpoint must report a collection total"
    assert "page" in data
    assert "pages" in data
    assert "per_page" in data

    assert data["per_page"] == 2, "per_page must be honoured, not ignored"
    assert len(data["elements"]) <= 2
    assert data["total"] >= 3, "total must be the collection total, not the page size"
    # ARCH-014/ARCH-052: `total` is the collection total, so with a page
    # smaller than the collection it must exceed the rows returned. The
    # previous form chained `... <= 2 is False`, which always evaluates
    # False, leaving the assertion dead.
    assert data["total"] > len(data["elements"]), (
        f'total ({data["total"]}) must exceed the page size '
        f'({len(data["elements"])}), not echo it'
    )


def test_elements_endpoint_rejects_non_integer_page(org_client):
    """A malformed page value must be rejected (400), not silently ignored."""
    _, client, _ = org_client
    resp = client.get("/architecture/api/elements?page=not-a-number")
    assert resp.status_code == 400


def test_elements_endpoint_rejects_non_integer_per_page(org_client):
    _, client, _ = org_client
    resp = client.get("/architecture/api/elements?per_page=lots")
    assert resp.status_code == 400


def test_elements_endpoint_ignores_unrelated_unknown_params(org_client, db_session, tenant_ctx, login_as):
    """Unknown non-pagination query params must not cause a rejection."""
    org, client, user = org_client
    _seed_elements(db_session, tenant_ctx, org.id, 1)
    login_as(client, user)
    resp = client.get("/architecture/api/elements?some_unrelated_flag=1")
    assert resp.status_code == 200
