"""Native Archi model files (.archimate) import through the one ArchiMate
import engine.

Fixtures are Archi's own test models, copied unchanged from the Archi
repository (archimatetool/archi, MIT licence):
- ``archisurance.archimate``: the Archisurance sample, plain XML.
- ``model_zipped.archimate``: the zipped form Archi writes when a model
  carries images (two diagrams and an image, no elements).

Archisurance holds 120 elements, 176 relationships and 17 diagrams. Counted
straight from the file in ``_count`` below, so the expectation is the file,
not a number typed into this test.
"""

import collections
import os
import xml.etree.ElementTree as ET
import zipfile

import pytest

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "archi")
ARCHISURANCE = os.path.join(FIXTURES, "archisurance.archimate")
ZIPPED = os.path.join(FIXTURES, "model_zipped.archimate")
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _count(xml_bytes):
    """Elements, relationships and views in an Archi file, counted directly."""
    root = ET.fromstring(xml_bytes)
    counts = collections.Counter()
    for node in root.iter("element"):
        concept = node.get(XSI_TYPE, "").split(":", 1)[-1]
        if "DiagramModel" in concept or concept in ("SketchModel", "CanvasModel"):
            counts["views"] += 1
        elif concept.endswith("Relationship"):
            counts["relationships"] += 1
        elif node.get("name", "").strip():
            counts["elements"] += 1
    return counts


def _service():
    from app.services.archimate_import_service import ArchiMateImportService

    return ArchiMateImportService()


# ---------------------------------------------------------------------------
# Parsing: no database needed
# ---------------------------------------------------------------------------


def test_archisurance_file_is_what_this_test_says():
    counts = _count(_read(ARCHISURANCE))
    assert counts == {"elements": 120, "relationships": 176, "views": 17}


def test_native_archisurance_parses_every_element_and_relationship():
    parsed = _service().load(_read(ARCHISURANCE))
    assert parsed["source_format"] == "archi"
    assert parsed["model_name"] == "Archisurance"
    assert len(parsed["elements"]) == 120
    assert len(parsed["relationships"]) == 176
    assert parsed["views_not_imported"] == 17
    # Every concept type in Archisurance is a known ArchiMate 3.2 type.
    assert not [e for e in parsed["errors"] if "Unknown element type" in e]
    types = {e.get("declared_type", e["type"]) for e in parsed["elements"]}
    assert {"BusinessActor", "ApplicationComponent", "Node", "Principle"} <= types
    rel_types = {r["type"] for r in parsed["relationships"]}
    assert rel_types == {
        "Access", "Aggregation", "Assignment", "Association", "Composition",
        "Flow", "Realization", "Serving", "Specialization", "Triggering",
    }


def test_native_folders_are_kept_on_each_element():
    parsed = _service().load(_read(ARCHISURANCE))
    by_name = {e["name"]: e for e in parsed["elements"]}
    assert by_name["Handle Claim"]["properties"]["archi_folder"] == "Business/Processes"
    assert all("archi_folder" in e["properties"] for e in parsed["elements"])


def test_native_relationship_endpoints_are_parsed_elements():
    parsed = _service().load(_read(ARCHISURANCE))
    ids = {e["identifier"] for e in parsed["elements"]}
    # Archi allows a relationship to point at another relationship; Archisurance
    # has none, so every endpoint is an element this import creates.
    for rel in parsed["relationships"]:
        assert rel["source"] in ids and rel["target"] in ids, rel


def test_archis_own_zipped_model_is_read():
    """Archi's zipped test model holds two diagrams and an image, no elements."""
    raw = _read(ZIPPED)
    assert raw[:2] == b"PK"
    parsed = _service().load(raw)
    assert parsed["source_format"] == "archi"
    assert parsed["model_name"] == "Zip Test"
    assert parsed["views_not_imported"] == 2


def test_zipped_archisurance_reads_every_element():
    """The zipped form Archi writes for a model with images: model.xml plus
    the images, here built from Archisurance."""
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("model.xml", _read(ARCHISURANCE))
        archive.writestr("images/logo.png", b"PNG")
    parsed = _service().load(buffer.getvalue())
    assert parsed["source_format"] == "archi"
    assert len(parsed["elements"]) == 120
    assert len(parsed["relationships"]) == 176


def test_zip_without_model_xml_is_refused(tmp_path):
    import io

    from app.services.archimate_import_service import ImportRequestError

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("other.txt", "hello")
    with pytest.raises(ImportRequestError):
        _service().load(buffer.getvalue())


def test_open_exchange_documents_are_unchanged():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "oef", "archiet_shaped.xml")
    parsed = _service().load(_read(path))
    assert "source_format" not in parsed
    assert "views_not_imported" not in parsed
    assert parsed["elements"]


# ---------------------------------------------------------------------------
# Import: rows land in the database
# ---------------------------------------------------------------------------


@pytest.fixture
def org_ctx(app, db_session, make_org, tenant_ctx):
    org = make_org("archi-native-import")
    with tenant_ctx(org.id):
        yield org


def test_archisurance_import_writes_its_elements_and_relationships(app, org_ctx):
    from app.models.archimate_core import (
        ArchiMateElement,
        ArchiMateRelationship,
        ArchitectureModel,
    )

    result = _service().import_xml(
        _read(ARCHISURANCE),
        strategy="create_all",
        organization_id=org_ctx.id,
        source_label="archisurance.archimate",
    )

    assert result["source_format"] == "archi"
    assert result["views_not_imported"] == 17
    assert result["created"] == 120
    assert result["failed"] == 0
    assert result["relationships_created"] + result["relationships_skipped"] == 176
    assert result["relationships_failed"] == []

    model = ArchitectureModel.query.get(result["model_id"])
    assert model is not None and model.name == "Archisurance"

    rows = ArchiMateElement.query.filter_by(architecture_id=model.id).all()
    assert len(rows) == 120
    names = {r.name for r in rows}
    assert {"Handle Claim", "Customer", "Home & Away Policy Administration"} <= names

    rels = ArchiMateRelationship.query.filter_by(architecture_id=model.id).all()
    assert len(rels) == result["relationships_created"]
    assert len(rels) >= 170


def test_archisurance_preview_writes_nothing(app, org_ctx):
    from app.models.archimate_core import ArchiMateElement

    before = ArchiMateElement.query.count()
    preview = _service().preview_xml(_read(ARCHISURANCE), organization_id=org_ctx.id)
    assert preview["source_format"] == "archi"
    assert preview["views_not_imported"] == 17
    assert ArchiMateElement.query.count() == before
