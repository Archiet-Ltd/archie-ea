"""The quick import creates the applications it is given, and never reports
success for nothing."""

import io
import uuid
from werkzeug.datastructures import FileStorage

HEADER = "name,description,type,vendor,lifecycle_status,criticality\n"


def _csv(rows, name="apps.csv"):
    body = HEADER + "".join(rows)
    return FileStorage(stream=io.BytesIO(body.encode()), filename=name, content_type="text/csv")


def _user(db_session, org):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Architect").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Architect").first()
    user = User(
        email=f"qi-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Quick",
        last_name="Import",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex  # generated, not a real credential
    db_session.add(user)
    db_session.flush()
    return user


def _names(org_id):
    from app.models.application_portfolio import ApplicationComponent

    return sorted(
        a.name
        for a in ApplicationComponent.query.filter_by(organization_id=org_id).all()
    )


def test_two_row_file_creates_two_applications_in_the_importing_org(
    app, db_session, make_org, tenant_ctx
):
    from app.modules.import_batch.services.import_orchestrator import ImportOrchestrator

    org_a, org_b = make_org("qia"), make_org("qib")
    user = _user(db_session, org_a)
    db_session.commit()
    suffix = uuid.uuid4().hex[:6]
    rows = [
        f"Customer Portal {suffix},Portal,Web,Acme,active,high\n",
        f"Billing Engine {suffix},Billing,Service,Acme,active,medium\n",
    ]
    with tenant_ctx(org_a.id):
        result = ImportOrchestrator().start_quick_import(
            _csv(rows), user_id=user.id, enable_ai=False
        )
        assert result.errors == []
        assert result.success is True
        assert result.applications_created == 2
        assert _names(org_a.id) == sorted(
            [f"Billing Engine {suffix}", f"Customer Portal {suffix}"]
        )
    with tenant_ctx(org_b.id):
        assert _names(org_b.id) == []


def test_file_whose_rows_all_fail_reports_failure_with_reasons(
    app, db_session, make_org, tenant_ctx, monkeypatch
):
    from app.modules.import_batch.services import import_orchestrator as mod

    org = make_org("qifail")
    user = _user(db_session, org)
    db_session.commit()

    def _boom(self, row, columns, user_id):
        raise ValueError("row cannot be created")

    monkeypatch.setattr(mod.ImportOrchestrator, "_create_application", _boom)
    rows = ["Alpha,a,Web,V,active,high\n", "Beta,b,Web,V,active,low\n"]
    with tenant_ctx(org.id):
        result = mod.ImportOrchestrator().start_quick_import(
            _csv(rows), user_id=user.id, enable_ai=False
        )
    assert result.success is False
    assert result.applications_created == 0
    assert len(result.errors) == 2
    assert all("row cannot be created" in e for e in result.errors)


def test_route_answers_400_when_every_row_fails(
    app, db_session, make_org, login_as, monkeypatch
):
    from app.modules.import_batch.services import import_orchestrator as mod

    org = make_org("qiroute")
    user = _user(db_session, org)
    db_session.commit()

    def _boom(self, row, columns, user_id):
        raise ValueError("row cannot be created")

    monkeypatch.setattr(mod.ImportOrchestrator, "_create_application", _boom)
    client = app.test_client()
    login_as(client, user)
    resp = client.post(
        "/api/import/quick",
        data={"file": (io.BytesIO((HEADER + "Alpha,a,Web,V,active,high\n").encode()), "apps.csv"),
              "enable_ai": "false"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["success"] is False
    assert any("row cannot be created" in e and "Alpha" in e for e in payload["errors"])


def test_reimport_with_skip_is_success_with_every_row_skipped(
    app, db_session, make_org, tenant_ctx
):
    from app.modules.import_batch.services.import_orchestrator import ImportOrchestrator

    org = make_org("qiskip")
    user = _user(db_session, org)
    db_session.commit()
    suffix = uuid.uuid4().hex[:6]
    rows = [
        f"Skip One {suffix},a,Web,V,active,high\n",
        f"Skip Two {suffix},b,Web,V,active,low\n",
    ]
    with tenant_ctx(org.id):
        first = ImportOrchestrator().start_quick_import(
            _csv(rows), user_id=user.id, enable_ai=False
        )
        assert first.success is True and first.applications_created == 2
        again = ImportOrchestrator().start_quick_import(
            _csv(rows), user_id=user.id, enable_ai=False, conflict_resolution="skip"
        )
    assert again.errors == []
    assert again.success is True
    assert again.applications_created == 0
    assert again.applications_skipped == 2


def test_route_reimport_with_skip_answers_200(app, db_session, make_org, login_as):
    org = make_org("qiskiproute")
    user = _user(db_session, org)
    db_session.commit()
    client = app.test_client()
    login_as(client, user)
    body = HEADER + f"Route Skip {uuid.uuid4().hex[:6]},a,Web,V,active,high\n"

    def _post():
        return client.post(
            "/api/import/quick",
            data={
                "file": (io.BytesIO(body.encode()), "apps.csv"),
                "enable_ai": "false",
                "conflict_resolution": "skip",
            },
            content_type="multipart/form-data",
        )

    assert _post().status_code == 200
    resp = _post()
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["success"] is True
    assert data["applications_skipped"] == 1


def test_same_name_in_another_org_is_not_matched_or_touched(
    app, db_session, make_org, tenant_ctx
):
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.import_batch.services.import_orchestrator import ImportOrchestrator

    org_a, org_b = make_org("qixa"), make_org("qixb")
    user = _user(db_session, org_a)
    db_session.commit()
    name = f"Shared Name {uuid.uuid4().hex[:6]}"
    with tenant_ctx(org_b.id):
        other = ApplicationComponent(name=name, description="org b original")
        db_session.add(other)
        db_session.commit()
        other_id = other.id
    with tenant_ctx(org_a.id):
        result = ImportOrchestrator().start_quick_import(
            _csv([f"{name},org a description,Web,V,active,high\n"]),
            user_id=user.id,
            enable_ai=False,
            conflict_resolution="overwrite",
        )
        assert result.errors == []
        assert result.applications_created == 1
        assert result.applications_updated == 0
        assert _names(org_a.id) == [name]
    with tenant_ctx(org_b.id):
        row = ApplicationComponent.query.filter_by(id=other_id).one()
        assert row.organization_id == org_b.id
        assert row.description == "org b original"


def test_route_scopes_the_import_to_the_signed_in_user_not_a_configured_user(
    app, db_session, make_org, tenant_ctx, login_as, caplog
):
    import logging

    from app.models.application_portfolio import ApplicationComponent

    org_x, org_y = make_org("qiscopex"), make_org("qiscopey")
    user_x = _user(db_session, org_x)
    user_y = _user(db_session, org_y)
    db_session.commit()
    suffix = uuid.uuid4().hex[:6]
    x_name, y_name = f"X-app {suffix}", f"Y-app {suffix}"
    with tenant_ctx(org_x.id):
        db_session.add(ApplicationComponent(name=x_name, description="x original"))
        db_session.commit()
    with tenant_ctx(org_y.id):
        db_session.add(ApplicationComponent(name=y_name, description="y original"))
        db_session.commit()

    # A configured user from another organisation must not decide the scope.
    previous = app.config.get("CURRENT_USER_ID")
    app.config["CURRENT_USER_ID"] = user_x.id
    try:
        client = app.test_client()
        login_as(client, user_y)
        body = HEADER + f"{y_name},y new,Web,V,active,high\n"
        with caplog.at_level(logging.INFO):
            resp = client.post(
                "/api/import/quick",
                data={
                    "file": (io.BytesIO(body.encode()), "apps.csv"),
                    "enable_ai": "false",
                    "conflict_resolution": "overwrite",
                },
                content_type="multipart/form-data",
            )
    finally:
        if previous is None:
            app.config.pop("CURRENT_USER_ID", None)
        else:
            app.config["CURRENT_USER_ID"] = previous
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["applications_created"] == 0
    assert data["applications_updated"] == 1
    assert f"user={user_y.id} " in caplog.text
    with tenant_ctx(org_y.id):
        assert (
            ApplicationComponent.query.filter_by(name=y_name).one().description
            == "y new"
        )
    with tenant_ctx(org_x.id):
        assert (
            ApplicationComponent.query.filter_by(name=x_name).one().description
            == "x original"
        )
