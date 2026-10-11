"""One OEF import engine behind every entry point (ADR 0008).

The model-import screen (``/architecture/import/oef`` and its preview), the
composer's JSON endpoint (``/archimate/api/import/oef``) and brownfield
programme setup all call ``ArchiMateImportService``. These tests pin what that
engine must keep doing wherever it is reached from: preview before writing,
honour the chosen strategy, refuse an ArchiMate-invalid relationship, read a
Latin-1 file, refuse anything over 10 MB, and give the same answer on the same
file whichever route it came in through.
"""
import inspect
import io
import os

import pytest

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "oef", "archiet_shaped.xml")

db_required = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set - import tests need PostgreSQL",
)


def _fixture_bytes():
    with open(FIXTURE_PATH, "rb") as fh:
        return fh.read()


def _oef(elements, relationships="", encoding="UTF-8"):
    return (
        f'<?xml version="1.0" encoding="{encoding}"?>\n'
        '<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-m">\n'
        "  <name>Engine fixture</name>\n"
        f"  <elements>{elements}</elements>\n"
        f"  <relationships>{relationships}</relationships>\n"
        "</model>\n"
    )


def _user(db_session, make_org, label):
    from app.models import User

    org = make_org(label)
    user = User(email=f"{label}-{org.id}@example.com", organization_id=org.id, confirmed=True)
    user.password = "x"
    db_session.add(user)
    db_session.flush()
    return org, user


# ------------------------------------------------------------ static (no DB)


def test_no_route_or_service_carries_a_second_oef_parser():
    """The composer endpoint, the exchange service and the OEF export service
    used to parse OEF themselves. All now defer to the engine."""
    from app.modules.architecture.routes import archimate_routes
    from app.modules.architecture.services.archimate_exchange_service import (
        ArchiMateExchangeService,
    )

    api_source = inspect.getsource(archimate_routes.api_import_oef)
    assert "ArchiMateImportService" in api_source
    assert "fromstring" not in api_source
    assert not hasattr(archimate_routes, "_OEF_VALID_TYPES")
    for retired in ("import_archimate_xml", "_import_element", "_import_relationship", "_create_domain_model"):
        assert not hasattr(ArchiMateExchangeService, retired), retired

    # The OEF export service carried a third upsert importer with no callers.
    # It exports only; nothing in it may parse or write imported OEF.
    from app.services import archimate_oef_service
    from app.services.archimate_oef_service import ArchiMateOEFService

    for retired in ("import_model", "_element_type_to_archimate", "_TYPE_MAP"):
        assert not hasattr(ArchiMateOEFService, retired), retired
    oef_source = inspect.getsource(archimate_oef_service)
    for parser_call in ("fromstring", "safe_xml", ".parse(", "iterparse"):
        assert parser_call not in oef_source, parser_call
    for name, member in inspect.getmembers(ArchiMateOEFService, inspect.isfunction):
        assert "import" not in name.lower(), name


def test_programme_setup_imports_through_the_engine_entry_point():
    from app.modules.solutions_strategic.v2.services import programme_setup_service

    source = inspect.getsource(programme_setup_service)
    assert "ArchiMateImportService().import_xml(" in source
    assert "parse_oef_xml" not in source


def test_decode_falls_back_to_latin1_and_refuses_over_ten_megabytes():
    from app.services.archimate_import_service import (
        MAX_UPLOAD_BYTES,
        ArchiMateImportService,
        ImportRequestError,
    )

    assert ArchiMateImportService.decode_xml("Café".encode("latin-1")) == "Café"
    assert ArchiMateImportService.decode_xml("Café".encode("utf-8")) == "Café"

    with pytest.raises(ImportRequestError) as exc:
        ArchiMateImportService.decode_xml(b" " * (MAX_UPLOAD_BYTES + 1))
    assert exc.value.status_code == 413

    with pytest.raises(ImportRequestError) as exc:
        ArchiMateImportService().import_xml(_fixture_bytes(), strategy="merge_everything")
    assert exc.value.status_code == 400


# ------------------------------------------------------------ database


@db_required
def test_update_existing_updates_a_reimported_element(app, db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_import_service import ArchiMateImportService

    org = make_org("oef-engine-update")
    first = _oef(
        '<element identifier="e1" xsi:type="BusinessService">'
        "<name>Customer Onboarding</name><documentation>Version one</documentation></element>"
    )
    edited = _oef(
        '<element identifier="e1" xsi:type="BusinessService">'
        "<name>Customer Onboarding</name><documentation>Version two</documentation></element>"
    )
    with tenant_ctx(org.id):
        service = ArchiMateImportService()
        assert service.import_xml(first)["created"] == 1

        skipped = service.import_xml(edited, strategy="skip_duplicates")
        assert (skipped["created"], skipped["updated"], skipped["skipped"]) == (0, 0, 1)
        row = ArchiMateElement.query.filter_by(name="Customer Onboarding").one()
        assert row.description == "Version one"

        updated = service.import_xml(edited, strategy="update_existing")
        assert (updated["created"], updated["updated"], updated["skipped"]) == (0, 1, 0)
        db_session.expire_all()
        rows = ArchiMateElement.query.filter_by(name="Customer Onboarding").all()
        assert len(rows) == 1
        assert rows[0].description == "Version two"


@db_required
def test_screen_imports_a_latin1_file(app, client, db_session, login_as, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement

    org, user = _user(db_session, make_org, "oef-engine-latin1")
    body = _oef(
        '<element identifier="e1" xsi:type="BusinessActor"><name>Équipe Réseau</name>'
        "<documentation>Gérée à Montréal</documentation></element>",
        encoding="ISO-8859-1",
    ).encode("latin-1")
    with pytest.raises(UnicodeDecodeError):
        body.decode("utf-8")

    login_as(client, user)
    resp = client.post(
        "/architecture/import/oef",
        data={"oef_file": (io.BytesIO(body), "latin1.xml"), "strategy": "skip_duplicates"},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["created"] == 1
    with tenant_ctx(org.id):
        row = ArchiMateElement.query.filter_by(name="Équipe Réseau").one()
        assert row.description == "Gérée à Montréal"


@db_required
@pytest.mark.parametrize(
    "url",
    ["/architecture/import/oef", "/architecture/import/oef/preview", "/archimate/api/import/oef"],
)
def test_every_entry_point_refuses_a_file_over_ten_megabytes(url, app, client, db_session, login_as, make_org):
    from app.services.archimate_import_service import MAX_UPLOAD_BYTES

    _, user = _user(db_session, make_org, "oef-engine-size")
    padding = "<!--" + "x" * (MAX_UPLOAD_BYTES) + "-->"
    body = (_oef('<element identifier="e1" xsi:type="Goal"><name>Big</name></element>') + padding).encode()

    login_as(client, user)
    field = "file" if url.startswith("/archimate") else "oef_file"
    resp = client.post(
        url,
        data={field: (io.BytesIO(body), "big.xml")},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 413, resp.status_code
    assert "10 MB" in resp.get_data(as_text=True)


@db_required
def test_preview_writes_nothing_and_reports_the_refused_relationship(app, client, db_session, login_as, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement

    org, user = _user(db_session, make_org, "oef-engine-preview")
    login_as(client, user)
    resp = client.post(
        "/architecture/import/oef/preview",
        data={"oef_file": (io.BytesIO(_fixture_bytes()), "fixture.xml")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    summary = resp.get_json()["summary"]
    assert summary["total"] == 15
    assert summary["new"] == 15
    assert summary["relationships_valid"] == 12
    assert summary["relationships_invalid"] == 1
    with tenant_ctx(org.id):
        assert ArchiMateElement.query.count() == 0


@db_required
def test_screen_accepts_json_and_raw_xml_bodies(app, client, db_session, login_as, make_org):
    _, user = _user(db_session, make_org, "oef-engine-bodies")
    doc = _oef('<element identifier="e1" xsi:type="Goal"><name>Raw Body Goal</name></element>')

    login_as(client, user)
    resp = client.post("/architecture/import/oef", json={"xml_content": doc, "strategy": "skip_duplicates"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["created"] == 1

    login_as(client, user)
    resp = client.post(
        "/architecture/import/oef",
        data=doc.encode(),
        content_type="application/xml",
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["skipped"] == 1


@db_required
def test_api_and_screen_produce_identical_results_on_the_fixture(app, client, db_session, login_as, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    screen_org, screen_user = _user(db_session, make_org, "oef-engine-screen")
    api_org, api_user = _user(db_session, make_org, "oef-engine-api")

    login_as(client, screen_user)
    screen = client.post(
        "/architecture/import/oef",
        data={"oef_file": (io.BytesIO(_fixture_bytes()), "fixture.xml")},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert screen.status_code == 200, screen.get_data(as_text=True)
    screen_json = screen.get_json()

    login_as(client, api_user)
    api = client.post(
        "/archimate/api/import/oef",
        data={"file": (io.BytesIO(_fixture_bytes()), "fixture.xml")},
        content_type="multipart/form-data",
    )
    assert api.status_code == 200, api.get_data(as_text=True)
    api_json = api.get_json()

    assert screen_json["created"] == api_json["stats"]["elements_created"] == 15
    assert screen_json["relationships_created"] == api_json["stats"]["relationships_created"] == 12
    assert len(screen_json["relationships_failed"]) == api_json["stats"]["relationships_failed"] == 1
    assert (
        [f["identifier"] for f in screen_json["relationships_failed"]]
        == [f["identifier"] for f in api_json["relationships_failed"]]
        == ["id-rel-13"]
    )

    def _stored(org_id):
        with tenant_ctx(org_id):
            elements = ArchiMateElement.query.all()
            names = {e.id: e.name for e in elements}
            return (
                sorted((e.name, e.type, e.layer, e.description) for e in elements),
                sorted(
                    (names[r.source_id], names[r.target_id], r.type)
                    for r in ArchiMateRelationship.query.all()
                ),
                {e.name: {k: v for k, v in (e.custom_properties or {}).items() if k != "archie:imported_at"} for e in elements},
            )

    assert _stored(screen_org.id) == _stored(api_org.id)


@db_required
def test_created_elements_get_their_domain_rows_once(app, db_session, make_org, tenant_ctx):
    """The screen's former engine created a Driver / Goal / ApplicationComponent
    row for each such element it imported; the one engine keeps doing so, and
    a re-import does not create a second one."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchitectureModel
    from app.models.motivation import Driver
    from app.services.archimate_import_service import ArchiMateImportService

    org = make_org("oef-engine-domain")
    doc = _oef(
        '<element identifier="d1" xsi:type="Driver"><name>Engine Driver</name></element>'
        '<element identifier="a1" xsi:type="ApplicationComponent"><name>Engine App</name></element>'
    )
    with tenant_ctx(org.id):
        service = ArchiMateImportService()
        result = service.import_xml(doc)
        assert result["created"] == 2
        assert result["model_id"] is not None
        assert ArchitectureModel.query.filter_by(id=result["model_id"]).one().name == "Engine fixture"

        driver_el = ArchiMateElement.query.filter_by(name="Engine Driver").one()
        app_el = ArchiMateElement.query.filter_by(name="Engine App").one()
        assert driver_el.architecture_id == result["model_id"]
        assert Driver.query.filter_by(archimate_element_id=driver_el.id).count() == 1
        assert ApplicationComponent.query.filter_by(archimate_element_id=app_el.id).count() == 1

        service.import_xml(doc, strategy="update_existing")
        assert Driver.query.filter_by(archimate_element_id=driver_el.id).count() == 1
        assert ApplicationComponent.query.filter_by(archimate_element_id=app_el.id).count() == 1


@db_required
def test_repeated_create_all_import_keeps_one_portfolio_row_per_application_per_org(
    app, db_session, make_org, tenant_ctx
):
    """``create_all`` makes a fresh element on every run. The portfolio row
    for an application component must not follow it: importing the same
    file twice leaves one row per application, in each organisation."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.motivation import Driver
    from app.services.archimate_import_service import ArchiMateImportService

    doc = _oef(
        '<element identifier="a1" xsi:type="ApplicationComponent"><name>Ledger Core</name></element>'
        '<element identifier="a2" xsi:type="ApplicationComponent"><name>Payments Hub</name></element>'
        '<element identifier="d1" xsi:type="Driver"><name>Ledger Driver</name></element>'
    )
    orgs = [make_org("oef-engine-idem-a"), make_org("oef-engine-idem-b")]
    for org in orgs:
        with tenant_ctx(org.id):
            service = ArchiMateImportService()
            assert service.import_xml(doc, strategy="create_all")["created"] == 3
            assert service.import_xml(doc, strategy="create_all")["created"] == 3

    for org in orgs:
        db_session.expire_all()
        with tenant_ctx(org.id):
            # create_all still creates a second element per run, as it says.
            assert ArchiMateElement.query.filter(
                ArchiMateElement.organization_id == org.id,
                ArchiMateElement.name == "Ledger Core",
            ).count() == 2
            for name in ("Ledger Core", "Payments Hub"):
                rows = ApplicationComponent.query.filter(
                    ApplicationComponent.organization_id == org.id,
                    ApplicationComponent.name == name,
                ).all()
                assert len(rows) == 1, (org.id, name, len(rows))
                assert rows[0].archimate_element_id is not None
            drivers = (
                Driver.query.join(ArchiMateElement, ArchiMateElement.id == Driver.archimate_element_id)
                .filter(ArchiMateElement.organization_id == org.id, Driver.name == "Ledger Driver")
                .count()
            )
            assert drivers == 1
