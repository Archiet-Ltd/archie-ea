"""Round 2 of the PR 430 global-tables fix: the specific routes named in the
lead reviewer's ruling (Part 2/3), fixed properly on top of the structural
``before_flush`` guard in ``app.middleware.tenant_isolation``.

Same DEFECT-7 pattern as ``test_jira_routes_platform_admin.py``: a tenant
admin from EITHER of two organisations must be refused (proves the check is
genuinely platform-wide, not an org-specific rule that happens to match one
org by coincidence), and a genuine platform admin must never be refused by
the authorisation gate itself (downstream 400/404/500 from a dummy id or an
empty body is expected and is not what this asserts).
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
        email=f"r2-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Round2",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _world(db_session, make_org):
    org_a = make_org("r2-a")
    org_b = make_org("r2-b")
    tenant_admin_a = _user(db_session, org_a)
    tenant_admin_b = _user(db_session, org_b)
    platform_admin = _user(db_session, org_a, platform=True)
    db_session.commit()
    return tenant_admin_a.id, tenant_admin_b.id, platform_admin.id


def _login(db_session, client, login_as, user_id):
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


# (method, path, json body or None) -- every route Part 2/3 of the lead's
# brief fixes. IDs are placeholders (999999): authorisation runs before any
# resource lookup, so a refusal does not depend on the id existing.
FIXED_ROUTES = [
    # Item 1 (CRITICAL): /api/v1/vendors
    ("post", "/api/v1/vendors/", {"name": "Probe Vendor"}),
    ("put", "/api/v1/vendors/999999", {"name": "Probe Vendor"}),
    ("delete", "/api/v1/vendors/999999", None),
    # Item 2: vendor create/update across three modules
    ("post", "/api/vendors/", {"name": "Probe Vendor"}),
    ("put", "/api/vendors/999999", {"name": "Probe Vendor"}),
    ("post", "/vendor-management/create", {"name": "Probe Vendor"}),
    ("post", "/vendor-management/api/vendors", {"name": "Probe Vendor"}),
    ("put", "/vendor-management/api/vendors/999999", {"name": "Probe Vendor"}),
    ("post", "/vendors/create", None),
    ("put", "/vendors/999999/edit", None),
    ("post", "/applications/vendors/create", {"name": "Probe Vendor"}),
    # Item 3: vendor products + auto-create-vendors
    ("post", "/api/vendor/999999/products", {"name": "Probe Product"}),
    ("post", "/dashboard/applications/auto-create-vendors", {"vendors": ["Probe Co"]}),
    # Item 4: system settings
    ("post", "/api/system-settings/save", {"settings": {"app-name": "x"}}),
    # Item 5: AI persona prompts
    ("post", "/ai-chat/admin/prompts/enterprise_architect/update", {"system_prompt": "x"}),
    ("post", "/ai-chat/admin/prompts/enterprise_architect/reset", None),
    # Item 6: confidence thresholds
    ("post", "/reviews/api/thresholds", {"threshold_name": "Probe"}),
    # Item 7: Jira push/sync (enterprise_api_routes)
    ("post", "/api/enterprise/requirements/999999/push-to-jira", None),
    ("post", "/api/enterprise/requirements/999999/sync-from-jira", None),
    ("get", "/api/enterprise/requirements/sync-all-jira", None),
    # Part 3: custom field definitions
    ("post", "/dashboard/admin/custom-fields/create", None),
    ("post", "/dashboard/admin/custom-fields/999999/edit", None),
    ("post", "/dashboard/admin/custom-fields/999999/delete", None),
    ("post", "/dashboard/admin/custom-fields/bulk-delete", None),
    # Part 3: ArchiMate patterns/templates
    ("post", "/archimate/api/templates", {"name": "Probe", "template_json": {"a": 1}}),
    ("post", "/archimate/api/patterns", {
        "name": "Probe", "pattern_json": {"elements": [{"role": "a", "type": "b", "label": "c"}]},
    }),
]


def _open(client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    return getattr(client, method)(path, **kwargs)


@pytest.mark.parametrize("method,path,body", FIXED_ROUTES)
def test_a_tenant_administrator_is_refused_on_every_fixed_route(
    app, db_session, make_org, client, login_as, method, path, body
):
    tenant_admin_a_id, tenant_admin_b_id, _platform_id = _world(db_session, make_org)

    for admin_id in (tenant_admin_a_id, tenant_admin_b_id):
        _login(db_session, client, login_as, admin_id)
        response = _open(client, method, path, body)
        assert response.status_code == 403, (
            f"{method.upper()} {path} should refuse a plain organisation admin "
            f"(self-registered, no session switch into any other org); got "
            f"{response.status_code}"
        )


@pytest.mark.parametrize("method,path,body", FIXED_ROUTES)
def test_a_platform_administrator_is_never_refused_on_any_fixed_route(
    app, db_session, make_org, client, login_as, method, path, body
):
    """Downstream behaviour (a dummy id, an incomplete body) can legitimately
    answer 400/404/409/500 -- this only asserts the authorisation gate itself
    admits a genuine platform admin."""
    _tenant_a_id, _tenant_b_id, platform_admin_id = _world(db_session, make_org)

    _login(db_session, client, login_as, platform_admin_id)
    response = _open(client, method, path, body)

    assert response.status_code not in (401, 403), (
        f"{method.upper()} {path} refused a genuine platform admin "
        f"({response.status_code})"
    )


def test_activate_vendor_is_refused_by_the_structural_guard_alone(
    app, db_session, make_org, client, login_as
):
    """``POST /vendors/<id>/activate`` carries no ``platform_admin_required``
    of its own (it is not one of the routes the lead's brief names) and still
    writes the global ``VendorOrganization`` row (``contract_status`` etc.).
    This is the case the Part 1 structural guard exists for: a write the
    reviewers have not looked at yet. Unlike the parametrised routes above,
    this one is refused ONLY by the before_flush guard, not by any decorator
    on the view itself -- the red/green proof in the build report disables
    the guard and reruns exactly this test to show that.

    The route's own ``except Exception`` swallows the guard's
    AuthorizationError and turns it into a flash message plus a 302 redirect
    rather than a clean 403 (a real, separately-reported finding -- the write
    is still refused, but the status code does not say so). This asserts the
    security property that actually matters: the vendor's ``contract_status``
    is unchanged.
    """
    from app.models.vendor.vendor_organization import VendorOrganization

    org_b = make_org("activate-b")
    tenant_admin_b = _user(db_session, org_b)
    vendor = VendorOrganization(name=f"Probe Vendor {uuid.uuid4().hex[:8]}")
    db_session.add(vendor)
    db_session.commit()
    # Captured as plain values before any session-clearing operation below
    # (login_as's own docstring: g/session churn between requests can detach
    # an object loaded before the switch).
    vendor_id = vendor.id
    tenant_admin_b_id = tenant_admin_b.id
    original_contract_status = vendor.contract_status

    _login(db_session, client, login_as, tenant_admin_b_id)
    response = client.post(
        f"/vendors/{vendor_id}/activate",
        data={"contract_value_annual": "1000"},
    )
    assert response.status_code != 200, (
        f"a tenant admin with no relationship to the global vendor catalogue "
        f"should not get a clean 200; got {response.status_code}"
    )

    db_session.expunge_all()
    reloaded = db_session.get(VendorOrganization, vendor_id)
    assert reloaded.contract_status == original_contract_status, (
        "the write must be refused regardless of what HTTP status the "
        "route's own exception handling happens to surface"
    )
