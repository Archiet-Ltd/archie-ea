from __future__ import annotations


def test_detail_page_renders_external_identifier_section(
    app, db_session, make_org, tenant_ctx
):
    from app.models.archimate_core import ArchiMateElement
    from app.models.user import User
    from app.modules.intelligence.services.crosswalk_service import CrosswalkService
    from tests.test_ba_tenant_and_authz import _login

    org = make_org("detail-external-ids")

    with tenant_ctx(org.id):
        element = ArchiMateElement(
            name="MES Control Plane",
            type="ApplicationComponent",
            layer="Application",
            organization_id=org.id,
        )
        db_session.add(element)
        db_session.flush()
        CrosswalkService.write_link("jira", "MES-1001", element.id)

        user = User(
            email=f"ext-{org.id}@example.com",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        db_session.add(user)
        db_session.commit()

        client = app.test_client()
        with app.app_context():
            _login(client, user.id)
            response = client.get(
                f"/architecture/application/ApplicationComponent/{element.id}"
            )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "External identifiers" in html
    assert "Source system" in html
    assert "jira" in html
    assert "MES-1001" in html


def test_detail_page_uses_linked_archimate_element_for_external_identifiers(
    app, db_session, make_org, tenant_ctx
):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.user import User
    from app.modules.intelligence.services.crosswalk_service import CrosswalkService
    from tests.test_ba_tenant_and_authz import _login

    org = make_org("detail-linked-external-ids")

    with tenant_ctx(org.id):
        archimate_element = ArchiMateElement(
            name="Factory Scheduler",
            type="ApplicationComponent",
            layer="Application",
            organization_id=org.id,
        )
        db_session.add(archimate_element)
        db_session.flush()

        element = ApplicationComponent(
            name="Factory Scheduler",
            organization_id=org.id,
            archimate_element_id=archimate_element.id,
        )
        db_session.add(element)
        db_session.flush()

        CrosswalkService.write_link("jira", "APP-9001", archimate_element.id)

        user = User(
            email=f"linked-ext-{org.id}@example.com",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        db_session.add(user)
        db_session.commit()

        client = app.test_client()
        with app.app_context():
            _login(client, user.id)
            response = client.get(
                f"/architecture/application/ApplicationComponent/{element.id}"
            )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "External identifiers" in html
    assert "jira" in html
    assert "APP-9001" in html
