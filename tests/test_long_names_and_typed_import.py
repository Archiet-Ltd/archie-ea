"""Tests for long names, typed property import, the not-imported report and
organisation scoping of import and export.

Tests marked ``db_required`` need PostgreSQL and are skipped without
``TEST_DATABASE_URL``; the parser tests need no database.
"""
import os

import pytest

from app.services.archimate_import_service import ArchiMateImportService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LONG_150 = "N" * 150
LONG_256 = "M" * 256

OEF_LONG_NAMES = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"
       xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-long-names">
  <name xml:lang="en">Long-name test</name>
  <elements>
    <element identifier="E1" xsi:type="BusinessService">
      <name xml:lang="en">{name_150}</name>
      <documentation>A service with a 150-character name.</documentation>
    </element>
    <element identifier="E2" xsi:type="BusinessService">
      <name xml:lang="en">{name_256}</name>
      <documentation>This name is too long.</documentation>
    </element>
  </elements>
  <relationships/>
</model>
""".format(name_150=LONG_150, name_256=LONG_256)

OEF_TYPED_PROPERTIES = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"
       xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-typed-props">
  <name xml:lang="en">Typed property test</name>
  <elements>
    <element identifier="E1" xsi:type="ApplicationInterface">
      <name xml:lang="en">API Gateway</name>
      <properties>
        <property propertyDefinitionRef="pid-rate"><value xml:lang="en">5000 req/min</value></property>
        <property propertyDefinitionRef="pid-auth"><value xml:lang="en">OAuth2</value></property>
      </properties>
    </element>
    <element identifier="E2" xsi:type="ApplicationInterface">
      <name xml:lang="en">Bad Rate Limit</name>
      <properties>
        <property propertyDefinitionRef="pid-rate"><value xml:lang="en">not-a-number</value></property>
      </properties>
    </element>
  </elements>
  <relationships/>
  <propertyDefinitions>
    <propertyDefinition identifier="pid-rate" type="string"><name xml:lang="en">rate_limit</name></propertyDefinition>
    <propertyDefinition identifier="pid-auth" type="string"><name xml:lang="en">authentication</name></propertyDefinition>
  </propertyDefinitions>
</model>
"""

OEF_ROUNDTRIP = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"
       xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-roundtrip">
  <name xml:lang="en">Round-trip test</name>
  <elements>
    <element identifier="E1" xsi:type="BusinessService">
      <name xml:lang="en">{name_150}</name>
      <documentation>Long name round-trip.</documentation>
      <properties>
        <property propertyDefinitionRef="pid-status"><value xml:lang="en">active</value></property>
      </properties>
    </element>
    <element identifier="E2" xsi:type="BusinessService">
      <name xml:lang="en">Short Name</name>
    </element>
  </elements>
  <relationships>
    <relationship identifier="R1" source="E1" target="E2" xsi:type="Association"/>
  </relationships>
  <propertyDefinitions>
    <propertyDefinition identifier="pid-status" type="string"><name xml:lang="en">status</name></propertyDefinition>
  </propertyDefinitions>
</model>
""".format(name_150=LONG_150)


@pytest.fixture
def parsed_long_names():
    return ArchiMateImportService().parse_oef_xml(OEF_LONG_NAMES)


@pytest.fixture
def parsed_typed_props():
    return ArchiMateImportService().parse_oef_xml(OEF_TYPED_PROPERTIES)


@pytest.fixture
def parsed_roundtrip():
    return ArchiMateImportService().parse_oef_xml(OEF_ROUNDTRIP)


db_required = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set - execute tests need PostgreSQL",
)


# ---------------------------------------------------------------------------
# Acceptance 1: 150-char name is stored whole; 256-char name is refused
# ---------------------------------------------------------------------------

def test_150_char_name_is_valid(parsed_long_names):
    """A 150-character name is under the 255 limit and is not flagged."""
    e1 = next(e for e in parsed_long_names["elements"] if e["identifier"] == "E1")
    assert len(e1["name"]) == 150
    assert "invalid" not in e1


def test_256_char_name_is_refused(parsed_long_names):
    """A 256-character name exceeds the 255 limit and is flagged."""
    e2 = next(e for e in parsed_long_names["elements"] if e["identifier"] == "E2")
    assert len(e2["name"]) == 256
    assert e2["invalid"].startswith("name_too_long")
    assert "256" in e2["invalid"] or "255" in e2["invalid"]


@db_required
def test_150_char_name_is_stored_and_read_back(app, db_session, make_org, tenant_ctx, parsed_long_names):
    """Import a 150-char name and verify it is stored whole in the database."""
    from app.models.archimate_core import ArchiMateElement

    org = make_org("long-name-store")
    with tenant_ctx(org.id):
        result = ArchiMateImportService().execute_import(parsed_long_names, strategy="skip_duplicates")
        assert result["created"] == 1  # only E1 (E2 is refused)
        assert result["failed"] == 1

        e1 = ArchiMateElement.query.filter_by(type="BusinessService").first()
        assert e1 is not None
        assert len(e1.name) == 150
        assert e1.name == LONG_150


# ---------------------------------------------------------------------------
# Acceptance 2: Typed properties — numeric with unit, string refused, report
# ---------------------------------------------------------------------------

def test_parser_reads_properties_by_definition_name(parsed_typed_props):
    e1 = next(e for e in parsed_typed_props["elements"] if e["identifier"] == "E1")
    assert e1["properties"] == {
        "rate_limit": "5000 req/min",
        "authentication": "OAuth2",
    }


@db_required
def test_numeric_property_imports_as_number_with_unit(app, db_session, make_org, tenant_ctx):
    """A numeric property value is coerced through PropertyService and stored
    as a typed entry in acm_properties."""
    from app.commands.seed_viewpoints import seed_property_templates
    from app.models.archimate_core import ArchiMateElement

    seed_property_templates()

    org = make_org("typed-props")
    with tenant_ctx(org.id):
        svc = ArchiMateImportService()
        parsed = svc.parse_oef_xml(OEF_TYPED_PROPERTIES)
        result = svc.execute_import(parsed, strategy="skip_duplicates")
        assert result["created"] >= 1

        e1 = ArchiMateElement.query.filter_by(name="API Gateway").first()
        assert e1 is not None
        # The property is stored in acm_properties via PropertyService
        acm = e1.acm_properties or {}
        assert "rate_limit" in acm
        rate = acm["rate_limit"]
        assert isinstance(rate, dict)
        assert rate.get("value") == 5000
        assert rate.get("unit") == "req/min"


@db_required
def test_string_in_numeric_template_is_refused_and_reported(app, db_session, make_org, tenant_ctx):
    """A string value for a numeric property is refused, listed with a reason,
    kept out of custom_properties and absent from an export."""
    from app.commands.seed_viewpoints import seed_property_templates
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_oef_service import ArchiMateOEFService

    seed_property_templates()

    org = make_org("bad-typed-props")
    with tenant_ctx(org.id):
        svc = ArchiMateImportService()
        parsed = svc.parse_oef_xml(OEF_TYPED_PROPERTIES)
        result = svc.execute_import(parsed, strategy="skip_duplicates")

        # The element is still created; only the property is refused.
        e2 = ArchiMateElement.query.filter_by(name="Bad Rate Limit").first()
        assert e2 is not None
        assert "rate_limit" not in (e2.custom_properties or {})
        assert "rate_limit" not in (e2.acm_properties or {})

        entry = next(
            (u for u in result["unrepresentable"]
             if u.get("key") == "rate_limit" and u.get("element") == "Bad Rate Limit"),
            None,
        )
        assert entry is not None, result["unrepresentable"]
        assert "number" in entry["reason"].lower()

        exported = ArchiMateOEFService().export_model()
        assert "not-a-number" not in exported


@db_required
def test_gate_owned_property_is_reported_not_stored(app, db_session, make_org, tenant_ctx):
    """A property only the release gate may write is refused with that reason
    and is not kept in custom_properties."""
    from app.models.archimate_core import ArchiMateElement

    xml = OEF_TYPED_PROPERTIES.replace("rate_limit", "ai_system_risk_class")
    org = make_org("gate-owned-props")
    with tenant_ctx(org.id):
        svc = ArchiMateImportService()
        result = svc.execute_import(svc.parse_oef_xml(xml), strategy="skip_duplicates")
        e1 = ArchiMateElement.query.filter_by(name="API Gateway").first()
        assert e1 is not None
        assert "ai_system_risk_class" not in (e1.custom_properties or {})
        entry = next(u for u in result["unrepresentable"]
                     if u.get("key") == "ai_system_risk_class" and u["element"] == "API Gateway")
        assert "set by the system" in entry["reason"]


@db_required
def test_non_finite_number_is_refused_and_element_still_created(app, db_session, make_org, tenant_ctx):
    from app.commands.seed_viewpoints import seed_property_templates
    from app.models.archimate_core import ArchiMateElement

    seed_property_templates()
    xml = OEF_TYPED_PROPERTIES.replace("not-a-number", "9" * 400)
    org = make_org("non-finite-props")
    with tenant_ctx(org.id):
        svc = ArchiMateImportService()
        result = svc.execute_import(svc.parse_oef_xml(xml), strategy="skip_duplicates")
        assert ArchiMateElement.query.filter_by(name="Bad Rate Limit").first() is not None
        entry = next(u for u in result["unrepresentable"]
                     if u.get("key") == "rate_limit" and u["element"] == "Bad Rate Limit")
        assert "finite" in entry["reason"]


def test_coerce_number_rejects_non_finite():
    from app.modules.architecture_assistant.property_service import (
        PropertyValidationError,
        _coerce_number,
    )

    for bad in (float("inf"), float("-inf"), float("nan"), "9" * 400):
        with pytest.raises(PropertyValidationError):
            _coerce_number(bad)


OEF_UNSUPPORTED = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"
       xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-unsupported">
  <name xml:lang="en">Unsupported content</name>
  <elements>
    <element identifier="E1" xsi:type="BusinessService">
      <name xml:lang="en">Orders</name>
      <name xml:lang="de">Bestellungen</name>
    </element>
    <element identifier="E2" xsi:type="BusinessService"><name xml:lang="en">Billing</name></element>
  </elements>
  <relationships>
    <relationship identifier="R1" source="E1" target="E2" xsi:type="Association">
      <name xml:lang="en">Feeds</name>
      <properties><property propertyDefinitionRef="pd1"><value>x</value></property></properties>
    </relationship>
  </relationships>
  <organizations>
    <item><label xml:lang="en">Business</label><item identifierRef="E1"/></item>
  </organizations>
  <propertyDefinitions>
    <propertyDefinition identifier="pd1" type="number"><name xml:lang="en">weight</name></propertyDefinition>
  </propertyDefinitions>
  <views><diagrams><view identifier="v1"><name xml:lang="en">Overview</name></view></diagrams></views>
</model>
"""


def test_unsupported_constructs_are_listed():
    parsed = ArchiMateImportService().parse_oef_xml(OEF_UNSUPPORTED)
    kinds = {}
    for u in parsed["unrepresentable"]:
        kinds.setdefault(u["kind"], []).append(u)
    assert [u["identifier"] for u in kinds["view"]] == ["v1"]
    assert kinds["view"][0]["element"] == "Overview"
    assert kinds["relationship name"][0]["element"] == "Feeds"
    assert kinds["relationship property"][0]["element"] == "weight"
    assert [u["element"] for u in kinds["folder"]] == ["Business"]
    assert kinds["extra language name"][0]["element"] == "Orders"
    assert kinds["declared property type"][0]["element"] == "weight"
    assert len(kinds["element identifier"]) == 2


def test_unsupported_constructs_are_aggregated_above_five():
    items = "".join(
        '<element identifier="E%d" xsi:type="BusinessService"><name>N%d</name></element>' % (i, i)
        for i in range(8)
    )
    xml = (
        '<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><name>m</name>'
        "<elements>%s</elements></model>" % items
    )
    parsed = ArchiMateImportService().parse_oef_xml(xml)
    entries = [u for u in parsed["unrepresentable"] if u["kind"] == "element identifier"]
    assert len(entries) == 1
    assert entries[0]["count"] == 8
    assert len(entries[0]["examples"]) == 5


def test_preview_carries_the_not_imported_list():
    parsed = ArchiMateImportService().parse_oef_xml(OEF_UNSUPPORTED)
    parsed = dict(parsed, elements=[])
    preview = ArchiMateImportService().preview_import(parsed)
    assert any(u["kind"] == "view" for u in preview["unrepresentable"])


@db_required
def test_import_screen_renders_the_not_imported_list(app, db_session, make_org, tenant_ctx):
    from flask import render_template
    from flask_login import login_user

    from app.models.user import User

    org = make_org("screen-unrepresentable")
    with tenant_ctx(org.id):
        parsed = ArchiMateImportService().parse_oef_xml(OEF_UNSUPPORTED)
        result = {
            "created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": [],
            "relationships_created": 0, "relationships_skipped": 0,
            "relationships_failed": [], "model_name": "m", "strategy": "skip_duplicates",
            "unrepresentable": parsed["unrepresentable"],
        }
        user = User(email="scr-%s@example.com" % org.id, organization_id=org.id, confirmed=True)
        db_session.add(user)
        db_session.commit()
        with app.test_request_context("/architecture/import/oef"):
            login_user(user)
            html = render_template(
                "archimate_crud/import_oef.html", result=result, strategy="skip_duplicates"
            )
    assert "import-unrepresentable" in html
    assert "views are not imported" in html


@db_required
def test_canvas_materialize_keeps_names_to_255_and_says_so(app, db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement
    from app.modules.architecture.routes.archimate_routes import _materialize_canvas_items

    org = make_org("canvas-names")
    with tenant_ctx(org.id):
        data = {"elements": [
            {"element_id": "src-1", "name": "C" * 150, "el_type": "ApplicationComponent"},
            {"element_id": "src-2", "name": "D" * 300, "el_type": "ApplicationComponent"},
        ]}
        id_map, _ = _materialize_canvas_items(data)
        assert len(db_session.get(ArchiMateElement, id_map["src-1"]).name) == 150
        assert len(db_session.get(ArchiMateElement, id_map["src-2"]).name) == 255
        assert [t["element_id"] for t in data["truncated_names"]] == ["src-2"]


# ---------------------------------------------------------------------------
# Acceptance 3: Round-trip — import, export, re-import preserves counts and long names
# ---------------------------------------------------------------------------

@db_required
def test_round_trip_preserves_element_and_relationship_counts(app, db_session, make_org, tenant_ctx, parsed_roundtrip):
    """Import a model, export it and parse the export: element and relationship
    counts match, a 150-character name and a property survive."""
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.services.archimate_oef_service import ArchiMateOEFService

    org = make_org("roundtrip-counts")
    with tenant_ctx(org.id):
        svc = ArchiMateImportService()
        result = svc.execute_import(parsed_roundtrip, strategy="skip_duplicates")
        assert result["created"] == 2
        assert result["relationships_created"] == 1

        # Export
        exported_xml = ArchiMateOEFService().export_model()

        # Re-import the export
        reparsed = svc.parse_oef_xml(exported_xml)
        assert len(reparsed["elements"]) == 2
        assert len(reparsed["relationships"]) == 1

        # The long name survives
        e1 = next(e for e in reparsed["elements"] if e["name"] == LONG_150)
        assert e1 is not None
        assert len(e1["name"]) == 150

        # Properties survive
        assert e1["properties"].get("status") == "active"


# ---------------------------------------------------------------------------
# Acceptance 4: Two-organisation isolation
# ---------------------------------------------------------------------------

@db_required
def test_export_never_contains_other_org_elements(app, db_session, make_org, tenant_ctx):
    """With the same model imported into two organisations, an export in
    organisation A holds A's two elements and not B's."""
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_oef_service import ArchiMateOEFService

    org_a = make_org("iso-a")
    org_b = make_org("iso-b")

    svc = ArchiMateImportService()

    # Import into org A
    xml_a = OEF_ROUNDTRIP.replace("Round-trip test", "Org A Model")
    with tenant_ctx(org_a.id):
        parsed_a = svc.parse_oef_xml(xml_a)
        svc.execute_import(parsed_a, strategy="skip_duplicates")

    # Import into org B
    xml_b = OEF_ROUNDTRIP.replace("Round-trip test", "Org B Model")
    with tenant_ctx(org_b.id):
        parsed_b = svc.parse_oef_xml(xml_b)
        svc.execute_import(parsed_b, strategy="skip_duplicates")

    # Export from org A
    with tenant_ctx(org_a.id):
        exported = ArchiMateOEFService().export_model()

    # The export should only contain org A's elements
    assert LONG_150 in exported, "Org A's long name should be in the export"
    assert "Short Name" in exported, "Org A's short name should be in the export"

    # The export should NOT contain org B's elements (same names but different org)
    # Actually both orgs have elements with the same names, so we can't distinguish
    # by name alone. Let's verify by checking the count instead.
    reparsed = svc.parse_oef_xml(exported)
    assert len(reparsed["elements"]) == 2, (
        "Export should contain exactly 2 elements (org A), not 4"
    )


@db_required
def test_import_writes_only_into_caller_org(app, db_session, make_org, tenant_ctx):
    """An import from org A must not create elements visible to org B."""
    from app.models.archimate_core import ArchiMateElement

    org_a = make_org("import-iso-a")
    org_b = make_org("import-iso-b")

    svc = ArchiMateImportService()

    # Import into org A
    with tenant_ctx(org_a.id):
        parsed = svc.parse_oef_xml(OEF_ROUNDTRIP)
        svc.execute_import(parsed, strategy="skip_duplicates")

    # Org B should see no elements
    with tenant_ctx(org_b.id):
        count_b = ArchiMateElement.query.count()
        assert count_b == 0, (
            "Org B should see 0 elements after import into org A, got %d" % count_b
        )

    # Org A should see 2 elements
    with tenant_ctx(org_a.id):
        count_a = ArchiMateElement.query.count()
        assert count_a == 2, (
            "Org A should see 2 elements after import, got %d" % count_a
        )


# ---------------------------------------------------------------------------
# Acceptance 5: 150-char name survives export and re-import
# ---------------------------------------------------------------------------

@db_required
def test_150_char_name_survives_export_reimport(app, db_session, make_org, tenant_ctx):
    """A 150-character name survives export and re-import round-trip."""
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_oef_service import ArchiMateOEFService

    org = make_org("long-name-roundtrip")
    with tenant_ctx(org.id):
        svc = ArchiMateImportService()

        # Import
        parsed = svc.parse_oef_xml(OEF_LONG_NAMES)
        result = svc.execute_import(parsed, strategy="skip_duplicates")
        assert result["created"] == 1  # only E1

        # Export
        exported = ArchiMateOEFService().export_model()

        # Re-import the export
        reparsed = svc.parse_oef_xml(exported)
        e1 = next(e for e in reparsed["elements"] if len(e.get("name", "")) == 150)
        assert e1 is not None
        assert e1["name"] == LONG_150