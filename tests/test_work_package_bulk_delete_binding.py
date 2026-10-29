"""DEF-066, Capgemini dry-run pass 3: the bulk-delete confirm dialog's text
input used x-model="deleteConfirmText" while the Alpine component's gate
(bulkDeleteEnabled) and the field it actually declares/resets are both named
bulkConfirmText — two different properties, so typing "DELETE" never enabled
the button and no delete request ever fired on any of the three delete paths
(row, bulk-toolbar, or the component's own executeBulkDelete()). Verifies the
template and JS component now agree on one field name, and that the backend
bulk-delete API works when called directly.
"""

import re

import pytest


def test_template_and_js_component_agree_on_bulk_confirm_field():
    """The page no longer uses the Alpine component: bulk delete is a plain form
    that the selected rows' checkboxes join, with the shared confirmation dialog
    (data-confirm), so no typed field can disagree with a component again."""
    template = open(
        "app/templates/enterprise/work_packages.html", encoding="utf-8"
    ).read()

    assert "deleteConfirmText" not in template, (
        "the mismatched field name should be gone from the template"
    )
    assert "bulkConfirmText" not in template
    form = re.search(r'<form id="wp-bulk-form"[^>]*>', template)
    assert form, "the bulk delete form is missing"
    assert 'method="post"' in form.group(0)
    assert "data-confirm=" in form.group(0)
    # Each row's checkbox submits its id with that form, and the toolbar's
    # Delete selected button submits it.
    assert 'name="ids" value="{{ wp.id }}" form="wp-bulk-form"' in template
    assert "'form': 'wp-bulk-form'" in template


@pytest.mark.usefixtures("db_session")
def test_bulk_delete_form_removes_the_selected_work_packages(app, db_session, make_org, tenant_ctx):
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.models.user import User
    from app.services import work_package_service

    org = make_org("wp-bulk-form")
    other = make_org("wp-bulk-form-other")
    with tenant_ctx(other.id):
        foreign = work_package_service.create_work_package(organization_id=other.id, name="ZZ-AUDIT foreign")
        foreign_id = foreign.id
    with tenant_ctx(org.id):
        doomed = work_package_service.create_work_package(organization_id=org.id, name="ZZ-AUDIT doomed")
        kept = work_package_service.create_work_package(organization_id=org.id, name="ZZ-AUDIT kept")
        user = User(email=f"wpform-{org.id}@example.com", organization_id=org.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        db_session.add(user)
        db_session.commit()
        doomed_id, kept_id = doomed.id, kept.id

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            listed = c.get("/enterprise/implementation/work-packages").get_data(as_text=True)
            assert "ZZ-AUDIT doomed" in listed and "ZZ-AUDIT kept" in listed
            resp = c.post("/enterprise/implementation/work-packages",
                          data={"ids": [str(doomed_id), str(foreign_id)]})
            assert resp.status_code == 302, resp.get_data(as_text=True)

            assert db_session.get(UnifiedWorkPackage, doomed_id) is None
            assert db_session.get(UnifiedWorkPackage, kept_id) is not None
    # Another organisation's id in the form is ignored, never deleted (read in
    # that organisation's own context: the isolation layer hides it from org's).
    db_session.expire_all()
    with tenant_ctx(other.id):
        assert db_session.get(UnifiedWorkPackage, foreign_id) is not None


@pytest.mark.usefixtures("db_session")
def test_bulk_delete_api_removes_the_work_package(app, db_session, make_org, tenant_ctx):
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.models.user import User
    from app.services import work_package_service

    org = make_org("wp-bulk-delete")
    with tenant_ctx(org.id):
        # The screen lists and deletes the one store's work packages.
        wp = work_package_service.create_work_package(
            organization_id=org.id, name="ZZ-AUDIT retire legacy router")
        user = User(email=f"wpdel-{org.id}@example.com", organization_id=org.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        db_session.add(user)
        db_session.commit()
        wp_id = wp.id

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            resp = c.delete("/enterprise/api/work-packages/bulk", json={"ids": [wp_id]})
            assert resp.status_code in (200, 204), resp.get_data(as_text=True)

            assert db_session.get(UnifiedWorkPackage, wp_id) is None
