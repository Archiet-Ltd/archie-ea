"""Every element and relationship records where it came from.

Import path: kind 'import', the file name, a batch id and the import time.
Manual path: kind 'manual', the person's id and display name, the time.
Rows written before the columns existed stay null and are read as before.
"""

import uuid
from pathlib import Path

from app.models import ArchiMateElement, ArchiMateRelationship
from app.modules.intelligence.tests.test_api_routes import _make_element, _make_user

FIXTURE = Path(__file__).parent / "fixtures" / "oef" / "archiet_shaped.xml"
FILE_NAME = "archiet-model-v1.oef.xml"
ELEMENTS, RELATIONSHIPS = 15, 12


def _xml_with_relationship_properties() -> str:
    text = FIXTURE.read_text(encoding="utf-8")
    text = text.replace(
        "</propertyDefinitions>",
        '<propertyDefinition identifier="pd-why" type="string"><name xml:lang="en">why</name>'
        "</propertyDefinition></propertyDefinitions>",
        1,
    )
    line = '<relationship identifier="id-rel-1" xsi:type="Assignment" source="id-elem-1" target="id-elem-2"/>'
    assert line in text
    return text.replace(
        line,
        line[:-2] + '><properties><property propertyDefinitionRef="pd-why">'
        '<value xml:lang="en">ruling constrains offer</value></property></properties></relationship>',
        1,
    )


def _import(service, xml, strategy="skip_duplicates", ref="batch-1", label=FILE_NAME):
    parsed = service.parse_oef_xml(xml)
    return service.execute_import(parsed, strategy=strategy, source_ref=ref, source_label=label)


def test_every_imported_row_carries_the_file_it_came_from(app, db_session, make_org, tenant_ctx):
    from app.services.archimate_import_service import ArchiMateImportService

    org = make_org("prov-import")
    with tenant_ctx(org.id):
        result = _import(ArchiMateImportService(), _xml_with_relationship_properties())
        assert result["created"] == ELEMENTS
        assert result["relationships_created"] == RELATIONSHIPS

        elements = ArchiMateElement.query.filter_by(organization_id=org.id).all()
        relationships = ArchiMateRelationship.query.filter_by(organization_id=org.id).all()
        assert len(elements) == ELEMENTS
        assert len(relationships) == RELATIONSHIPS
        for row in elements + relationships:
            assert row.source_kind == "import"
            assert row.source_label == FILE_NAME
            assert row.source_ref == "batch-1"
            assert row.source_recorded_at is not None

        # Relationship properties from the file are kept, on that relationship only.
        with_props = [r for r in relationships if (r.custom_properties or {}).get("why")]
        assert len(with_props) == 1
        assert with_props[0].custom_properties["why"] == "ruling constrains offer"


def test_a_reimport_updates_the_origin(app, db_session, make_org, tenant_ctx):
    from app.services.archimate_import_service import ArchiMateImportService

    org = make_org("prov-reimport")
    with tenant_ctx(org.id):
        service = ArchiMateImportService()
        xml = FIXTURE.read_text(encoding="utf-8")
        _import(service, xml, ref="batch-1", label="first.oef.xml")
        _import(service, xml, strategy="update_existing", ref="batch-2", label="second.oef.xml")

        rows = (
            ArchiMateElement.query.filter_by(organization_id=org.id).all()
            + ArchiMateRelationship.query.filter_by(organization_id=org.id).all()
        )
        assert len(rows) == ELEMENTS + RELATIONSHIPS
        assert {(r.source_label, r.source_ref) for r in rows} == {("second.oef.xml", "batch-2")}


def test_an_import_into_one_organisation_leaves_another_empty(app, db_session, make_org, tenant_ctx):
    from app.services.archimate_import_service import ArchiMateImportService

    org_a, org_b = make_org("prov-iso-a"), make_org("prov-iso-b")
    with tenant_ctx(org_a.id):
        _import(ArchiMateImportService(), FIXTURE.read_text(encoding="utf-8"))
        assert ArchiMateElement.query.filter_by(organization_id=org_a.id).count() == ELEMENTS
    with tenant_ctx(org_b.id):
        assert ArchiMateElement.query.filter_by(organization_id=org_b.id).count() == 0
        assert ArchiMateRelationship.query.filter_by(organization_id=org_b.id).count() == 0
        assert ArchiMateElement.query.filter(ArchiMateElement.source_kind == "import").count() == 0


def test_the_manual_writer_records_the_person(app, db_session, make_org, tenant_ctx, client, login_as):
    from app.models import ArchitectureModel
    from app.modules.architecture.services.archimate_relationship_service import (
        ArchiMateRelationshipService,
    )

    org = make_org("prov-manual")
    with tenant_ctx(org.id):
        ada = _make_user(db_session, org)
        ada.first_name, ada.last_name = "Ada", "Lovelace"
        model = ArchitectureModel(name="Provenance model", organization_id=org.id)
        db_session.add(model)
        db_session.flush()
        source = _make_element(db_session, org.id, f"Source-{uuid.uuid4().hex[:6]}")
        target = _make_element(db_session, org.id, f"Target-{uuid.uuid4().hex[:6]}")
        source.architecture_id = target.architecture_id = model.id
        db_session.flush()

        login_as(client, ada)
        with client:
            client.get("/")  # binds the signed-in person to this request
            rel = ArchiMateRelationshipService.create_relationship(
                source_element=source, target_element=target, relationship_type="serving",
                architecture_id=model.id, organization_id=org.id,
            )
            assert rel is not None
            assert rel.source_kind == "manual"
            assert rel.source_ref == str(ada.id)
            assert rel.source_label == "Ada Lovelace"
            assert rel.source_recorded_at is not None


def test_rows_written_before_the_columns_stay_null(app, db_session, make_org, tenant_ctx):
    org = make_org("prov-legacy")
    with tenant_ctx(org.id):
        one = _make_element(db_session, org.id, f"One-{uuid.uuid4().hex[:6]}")
        two = _make_element(db_session, org.id, f"Two-{uuid.uuid4().hex[:6]}")
        rel = ArchiMateRelationship(source_id=one.id, target_id=two.id, type="Serving", organization_id=org.id)
        db_session.add(rel)
        db_session.flush()
        assert (one.source_kind, one.source_label, one.source_recorded_at) == (None, None, None)
        assert (rel.source_kind, rel.source_label, rel.source_recorded_at) == (None, None, None)


def _signed_in(db_session, org, role_name):
    from app.models.user import Role, User

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name=role_name).first()
    user = User(
        email=f"prov-{role_name.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=role_name, last_name="Person", organization_id=org.id,
        confirmed=True, role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_only_writers_can_import_an_exchange_file(app, db_session, make_org, tenant_ctx, client, login_as):
    import io

    from flask import url_for

    org = make_org("prov-gate")
    viewer = _signed_in(db_session, org, "Viewer")
    architect = _signed_in(db_session, org, "Architect")
    db_session.commit()
    with app.test_request_context():
        targets = [
            (url_for("archimate.api_import_oef"), "file"),
            (url_for("architect_ui.import_oef"), "oef_file"),
            (url_for("architect_ui.import_oef_preview"), "oef_file"),
        ]
    xml = FIXTURE.read_bytes()

    def post(path, field):
        return client.post(
            path, data={field: (io.BytesIO(xml), FILE_NAME)}, content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )

    for path, field in targets:
        login_as(client, viewer)
        assert post(path, field).status_code == 403, path
    with tenant_ctx(org.id):
        assert ArchiMateElement.query.filter_by(organization_id=org.id).count() == 0

    for path, field in targets[:2]:
        login_as(client, architect)
        assert post(path, field).status_code in (200, 201), path
    with tenant_ctx(org.id):
        assert ArchiMateElement.query.filter_by(organization_id=org.id).count() == ELEMENTS
