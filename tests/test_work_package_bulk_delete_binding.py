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
    template = open(
        "app/templates/enterprise/work_packages.html", encoding="utf-8"
    ).read()
    js = open(
        "app/static/js/enterprise/work_packages_table.js", encoding="utf-8"
    ).read()

    assert "deleteConfirmText" not in template, (
        "the mismatched field name should be gone from the template"
    )
    assert 'x-model="bulkConfirmText"' in template
    assert "bulkConfirmText" in js
    # The JS component must actually declare the field the template binds to.
    assert re.search(r"bulkConfirmText:\s*''", js)


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
