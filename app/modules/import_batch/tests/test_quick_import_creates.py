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
    assert resp.get_json()["success"] is False
