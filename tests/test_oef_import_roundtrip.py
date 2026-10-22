"""OEF import must round-trip a real model: relationships, properties, layer vocabulary, name limits.

Written first (red) against the customer-zero findings in docs/dogfood/ARCHIET-CUSTOMER-ZERO.md
(DOGFOOD-001 .. -004). The parser tests need no database; the execute tests use the shared
``db_session`` fixture and skip when ``TEST_DATABASE_URL`` is not set.
"""
import os

import pytest

from app.services.archimate_import_service import ArchiMateImportService

OEF = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"
       xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-test">
  <name xml:lang="en">Round-trip fixture</name>
  <elements>
    <element identifier="E1" xsi:type="Constraint">
      <name xml:lang="en">Design partners are free</name>
      <documentation>Founder ruling 2026-07-19.</documentation>
      <properties>
        <property propertyDefinitionRef="propid-status"><value xml:lang="en">RULED</value></property>
        <property propertyDefinitionRef="propid-source"><value xml:lang="en">strategy/10G-ASSESSMENT-OFFER.md</value></property>
      </properties>
    </element>
    <element identifier="E2" xsi:type="BusinessService">
      <name xml:lang="en">Design-partner pilot</name>
    </element>
    <element identifier="E3" xsi:type="WorkPackage">
      <name xml:lang="en">WP-A1 Azure exit</name>
    </element>
    <element identifier="E4" xsi:type="Goal">
      <name xml:lang="en">{long_name}</name>
    </element>
  </elements>
  <relationships>
    <relationship identifier="R1" source="E1" target="E2" xsi:type="Association">
      <documentation>ruling constrains offer</documentation>
    </relationship>
    <relationship identifier="R2" source="E3" target="E9" xsi:type="Realization"/>
  </relationships>
  <propertyDefinitions>
    <propertyDefinition identifier="propid-status" type="string"><name>status</name></propertyDefinition>
    <propertyDefinition identifier="propid-source" type="string"><name>source</name></propertyDefinition>
  </propertyDefinitions>
</model>
""".replace("{long_name}", "G" * 300)


@pytest.fixture
def parsed():
    return ArchiMateImportService().parse_oef_xml(OEF)


# ---------------------------------------------------------------- parser (no DB)

def test_parser_reads_properties_by_definition_name(parsed):
    e1 = next(e for e in parsed["elements"] if e["identifier"] == "E1")
    assert e1["properties"] == {
        "status": "RULED",
        "source": "strategy/10G-ASSESSMENT-OFFER.md",
    }


def test_parser_elements_without_properties_get_an_empty_dict(parsed):
    e2 = next(e for e in parsed["elements"] if e["identifier"] == "E2")
    assert e2["properties"] == {}


def test_parser_keeps_relationships_with_their_documentation(parsed):
    r1 = next(r for r in parsed["relationships"] if r["identifier"] == "R1")
    assert (r1["type"], r1["source"], r1["target"]) == ("Association", "E1", "E2")
    assert r1["description"] == "ruling constrains offer"


def test_parser_maps_implementation_elements_to_the_layer_the_catalog_renders(parsed):
    """DOGFOOD-002: the catalog counts and filters ``Implementation`` (archimate_routes.py layer_order);
    the importer wrote ``Implementation & Migration`` and 35 of 168 elements vanished."""
    e3 = next(e for e in parsed["elements"] if e["identifier"] == "E3")
    assert e3["layer"] == "Implementation"


def test_parser_flags_names_longer_than_the_column_instead_of_letting_execute_abort(parsed):
    """DOGFOOD-001: preview said 0 errors, execute rolled back everything on one 269-char name."""
    e4 = next(e for e in parsed["elements"] if e["identifier"] == "E4")
    assert e4["invalid"].startswith("name_too_long")
    assert any("E4" in err and "255" in err for err in parsed["errors"])


# ---------------------------------------------------------------- execute (DB)

db_required = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set - execute tests need PostgreSQL",
)


@db_required
def test_execute_writes_relationships_properties_and_survives_an_invalid_element(app, db_session, make_org, tenant_ctx, parsed):
    """Runs inside a tenant context, as a logged-in request would: archimate_elements.organization_id
    is NOT NULL and TenantMixin fills it from ``g.current_org_id`` on INSERT."""
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    org = make_org("oef-roundtrip")
    with tenant_ctx(org.id):
        result = ArchiMateImportService().execute_import(parsed, strategy="skip_duplicates")

        # DOGFOOD-001: the three valid elements land; only E4 fails, and it is named.
        assert result["created"] == 3
        assert result["failed"] == 1
        assert any("E4" in err or "name_too_long" in err for err in result["errors"])

        # DOGFOOD-004: provenance survives the import.
        e1 = ArchiMateElement.query.filter_by(name="Design partners are free", type="Constraint").first()
        assert e1 is not None
        assert e1.custom_properties["status"] == "RULED"
        assert e1.custom_properties["source"] == "strategy/10G-ASSESSMENT-OFFER.md"

        # DOGFOOD-003: relationships are written; the one with an unknown endpoint is reported, not lost silently.
        # main's contract (f5362b5): ``relationships_skipped`` counts edges that already existed;
        # an unresolved endpoint is a named entry in the ``relationships_failed`` list.
        assert result["relationships_created"] == 1
        assert result["relationships_skipped"] == 0
        assert [r["identifier"] for r in result["relationships_failed"]] == ["R2"]
        e2 = ArchiMateElement.query.filter_by(name="Design-partner pilot", type="BusinessService").first()
        # Stored type is the lowercase canonical form from _normalize_rel_type
        # (app/modules/architecture/routes/archimate_routes.py), shared by the
        # execute path — the OEF xsi:type "Association" normalises to "association".
        rel = ArchiMateRelationship.query.filter_by(source_id=e1.id, target_id=e2.id, type="association").first()
        assert rel is not None and rel.description == "ruling constrains offer"

        # DOGFOOD-002: the work package is in the layer the catalog renders.
        e3 = ArchiMateElement.query.filter_by(name="WP-A1 Azure exit", type="WorkPackage").first()
        assert e3.layer == "Implementation"


@db_required
def test_execute_is_idempotent_on_reimport(app, db_session, make_org, tenant_ctx, parsed):
    svc = ArchiMateImportService()
    org = make_org("oef-reimport")
    with tenant_ctx(org.id):
        svc.execute_import(parsed, strategy="skip_duplicates")
        second = svc.execute_import(parsed, strategy="skip_duplicates")
    assert second["created"] == 0
    assert second["skipped"] == 3
    assert second["failed"] == 1  # E4 is refused again, not silently dropped
    assert second["relationships_created"] == 0
    assert second["relationships_skipped"] == 1  # R1 already exists
    assert len(second["relationships_failed"]) == 1  # R2 still unresolved
