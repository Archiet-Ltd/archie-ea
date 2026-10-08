"""Regression guards for three HTTP 500s found by a live browser audit.

All three were template defects — nothing the view layer could have caught,
and nothing a unit test of the view function would have executed:

* ``GET /applications/vendors/create`` — ``components/input.html`` carried a
  ``{# … #}`` comment *inside* the ``{% macro input(…) %}`` signature, so the
  whole component file failed to parse (``TemplateSyntaxError: expected token
  'name', got '{'``) and every template importing it 500'd on import.
* ``GET /capability-maturity/heatmap`` — the empty-state branch called
  ``components/empty_state.html``'s ``empty_state`` with ``cta_label=``, but
  that macro's parameter is ``cta_text`` (``cta_label`` belongs to the
  *other*, same-named macro in ``macros/page_shell.html``). Jinja raises
  ``TypeError: macro 'empty_state' takes no keyword argument 'cta_label'``.
  Only an organisation with no capabilities reaches that branch, which is why
  a seeded database hides it.
* ``GET /capability-maturity/<id>/line-of-sight`` — the view itself is sound;
  a capability that does not exist (or belongs to another org) correctly
  flashes and redirects to the heatmap. The audit's 500 was the *redirect
  target* crashing. Hence the follow-the-redirect assertions below: a
  header-only check would have passed the whole time.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def org(make_org):
    return make_org("vendormaturity500")


@pytest.fixture
def logged_in_client(app, db_session, org, login_as):
    """A test client authenticated as an administrator in a fresh, empty org."""
    from app.models.user import Permission, Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()

    user = User(
        email=f"vm500-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Route",
        last_name="Audit",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    # PR 430 route-fixes split, v1 defect 2: /applications/vendors/create
    # (vendor_display_routes.vendors_create) writes the global VendorOrganization
    # catalogue and is now platform_admin_required. This fixture exists to
    # reproduce a template-parsing regression, not an authorisation one, so it
    # is a platform admin rather than a plain org admin.
    user.is_platform_admin = True
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    login_as(client, user)
    return client


@pytest.fixture
def capability(db_session, org):
    """A real `business_capabilities` row owned by the client's org.

    `organization_id` is set explicitly: `TenantMixin`'s before_flush hook
    reads `g.current_org_id`, which `db_session` does not establish.
    """
    from app.models.capability_models import BusinessCapability

    row = BusinessCapability(
        name=f"Route audit capability {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_vendor_create_form_renders(logged_in_client):
    """The vendor create form must render, not 500 on a broken component."""
    resp = logged_in_client.get("/applications/vendors/create")

    assert resp.status_code == 200, resp.status_code


def test_input_component_parses(app):
    """`components/input.html` must parse — every form template imports it."""
    app.jinja_env.get_template("components/input.html")


def test_maturity_heatmap_renders_for_an_org_with_no_capabilities(logged_in_client):
    """The empty-state branch is the one that crashed; assert it renders."""
    resp = logged_in_client.get("/capability-maturity/heatmap")

    assert resp.status_code == 200, resp.status_code


def test_maturity_heatmap_renders_with_a_capability(logged_in_client, capability):
    """The populated branch must keep working after the empty-state fix."""
    resp = logged_in_client.get("/capability-maturity/heatmap")

    assert resp.status_code == 200, resp.status_code


def test_line_of_sight_renders_for_a_real_capability(logged_in_client, capability):
    """An existing capability must render its line-of-sight page."""
    resp = logged_in_client.get(f"/capability-maturity/{capability.id}/line-of-sight")

    assert resp.status_code == 200, resp.status_code


def test_line_of_sight_for_a_missing_capability_lands_on_a_page_that_renders(
    logged_in_client,
):
    """Unknown id redirects to the heatmap — and the heatmap must serve 200.

    Following the redirect is the point: the audit's 500 was the destination,
    so a Location-header assertion alone would have stayed green.
    """
    resp = logged_in_client.get("/capability-maturity/999999999/line-of-sight")
    assert resp.status_code == 302, resp.status_code
    assert resp.headers["Location"].endswith("/capability-maturity/heatmap")

    followed = logged_in_client.get(
        "/capability-maturity/999999999/line-of-sight", follow_redirects=True
    )
    assert followed.status_code == 200, followed.status_code
