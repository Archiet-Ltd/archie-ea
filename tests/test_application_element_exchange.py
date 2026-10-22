"""T-app-03: import and export of the Application (ADR-0014 Decisions 4 and 5).

The Archiet exchange file is the worked example. Counts are derived from the file
itself in these tests, and also pinned to the figures the ADR gives for it.
"""

import io
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter

import pytest

pytestmark = pytest.mark.journey

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "archiet-model-v1.oef.xml")
NS = "http://www.opengroup.org/xsd/archimate/3.0/"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
XSI_TYPE = "{%s}type" % XSI

db_required = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set - exchange tests need PostgreSQL",
)


def _archiet_xml():
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        return fh.read()


def _oef(elements, relationships="", definitions=""):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-m">\n'
        "  <name>Exchange fixture</name>\n"
        f"  <elements>{elements}</elements>\n"
        f"  <relationships>{relationships}</relationships>\n"
        f"  <propertyDefinitions>{definitions}</propertyDefinitions>\n"
        "</model>\n"
    )


PROP_DEF = (
    '<propertyDefinition identifier="pd-spec" type="string"><name>entelim:specialization</name></propertyDefinition>'
)


def _el(ident, name, typ="ApplicationComponent", spec=None, pd="pd-spec"):
    props = ""
    if spec is not None:
        props = (
            f'<properties><property propertyDefinitionRef="{pd}"><value xml:lang="en">{spec}</value>'
            "</property></properties>"
        )
    return f'<element identifier="{ident}" xsi:type="{typ}"><name>{name}</name>{props}</element>'


def _rel(ident, typ, source, target):
    return f'<relationship identifier="{ident}" xsi:type="{typ}" source="{source}" target="{target}"/>'


def _parse(xml, **kw):
    from app.services.archimate_import_service import ArchiMateImportService

    return ArchiMateImportService().parse_oef_xml(xml, **kw)


def _kinds(parsed):
    return {e["name"]: e["type"] for e in parsed["elements"] if "Application" in e["type"]}


def _expected_from_file(xml):
    """Components that no application component composes or aggregates, read from the raw file."""
    root = ET.fromstring(xml)
    comps = {
        e.get("identifier"): e.findtext("{%s}name" % NS)
        for e in root.iter("{%s}element" % NS)
        if e.get(XSI_TYPE) == "ApplicationComponent"
    }
    parts = set()
    for r in root.iter("{%s}relationship" % NS):
        if r.get(XSI_TYPE) in ("Composition", "Aggregation") and r.get("source") in comps and r.get("target") in comps:
            parts.add(r.get("target"))
    return len(comps), sorted(comps[p] for p in parts)


# --------------------------------------------------------------- decisions, no DB


def test_archiet_file_default_gives_54_applications_and_entelim_as_the_one_component():
    xml = _archiet_xml()
    total, parts = _expected_from_file(xml)
    parsed = _parse(xml)
    kinds = _kinds(parsed)
    comps = sorted(n for n, t in kinds.items() if t == "ApplicationComponent")
    apps = [n for n, t in kinds.items() if t == "Application"]
    assert (total, len(parts)) == (55, 1)
    assert comps == parts == ["Entelim"]
    assert len(apps) == total - len(parts) == 54


def test_switch_off_keeps_every_incoming_component_a_component():
    kinds = _kinds(_parse(_archiet_xml(), promote_top_level=False))
    assert Counter(kinds.values()) == {"ApplicationComponent": 55}


def test_reversed_aggregations_give_53_applications_and_2_components():
    fixed = _swap_component_aggregations(_archiet_xml())
    kinds = _kinds(_parse(fixed))
    comps = sorted(n for n, t in kinds.items() if t == "ApplicationComponent")
    assert Counter(kinds.values()) == {"Application": 53, "ApplicationComponent": 2}
    assert comps == _expected_from_file(fixed)[1]


def _swap_component_aggregations(xml):
    root = ET.fromstring(xml)
    ids = {e.get("identifier") for e in root.iter("{%s}element" % NS) if e.get(XSI_TYPE) == "ApplicationComponent"}
    ET.register_namespace("", NS)
    ET.register_namespace("xsi", XSI)
    for r in root.iter("{%s}relationship" % NS):
        if r.get(XSI_TYPE) == "Aggregation" and r.get("source") in ids and r.get("target") in ids:
            src, tgt = r.get("source"), r.get("target")
            r.set("source", tgt)
            r.set("target", src)
    return ET.tostring(root, encoding="unicode")


def test_property_bearing_file_applies_rule_one_and_never_promotes():
    xml = _oef(
        _el("a", "Alpha", spec="Application")
        + _el("b", "Bravo")
        + _el("c", "Charlie")
        + _el("d", "Delta", spec="Other"),
        _rel("r1", "Composition", "a", "c"),
        PROP_DEF,
    )
    parsed = _parse(xml)
    kinds = _kinds(parsed)
    # b is top-level, but the file carries the property, so promotion is not applied.
    assert kinds == {
        "Alpha": "Application",
        "Bravo": "ApplicationComponent",
        "Charlie": "ApplicationComponent",
        "Delta": "ApplicationComponent",
    }


def test_property_key_ignores_case_and_a_plain_specialization_key_is_accepted():
    defs = (
        '<propertyDefinition identifier="p1" type="string"><name>Entelim:Specialization</name></propertyDefinition>'
        '<propertyDefinition identifier="p2" type="string"><name>specialization</name></propertyDefinition>'
    )
    xml = _oef(_el("a", "Alpha", spec="Application", pd="p1") + _el("b", "Bravo", spec="Application", pd="p2"), "", defs)
    assert _kinds(_parse(xml)) == {"Alpha": "Application", "Bravo": "Application"}


def test_application_under_an_application_kind_composition_becomes_a_component_with_the_reason():
    xml = _oef(
        _el("a", "Alpha", spec="Application") + _el("d", "Delta", spec="Application"),
        _rel("r1", "Composition", "a", "d"),
        PROP_DEF,
    )
    parsed = _parse(xml)
    assert _kinds(parsed) == {"Alpha": "Application", "Delta": "ApplicationComponent"}
    reason = parsed["application_kind_decisions"]["d"]["reason"]
    assert "Alpha" in reason and "composed" in reason


def test_a_grouping_or_work_package_parent_does_not_make_a_component():
    xml = _oef(
        _el("a", "Alpha") + '<element identifier="g" xsi:type="Grouping"><name>Phase C</name></element>',
        _rel("r1", "Aggregation", "g", "a") + _rel("r2", "Composition", "g", "a"),
    )
    assert _kinds(_parse(xml)) == {"Alpha": "Application"}


def test_every_decision_carries_a_plain_reason_and_a_layer():
    parsed = _parse(_archiet_xml())
    decisions = parsed["application_kind_decisions"]
    assert len(decisions) == 55
    assert all(d["reason"] and d["name"] for d in decisions.values())
    for e in parsed["elements"]:
        if e["type"] == "Application":
            assert e["layer"] == "Application"
            assert e["application_kind_decision"]


# --------------------------------------------------------------- export mapping


def test_exchange_type_for_names_the_specialisation_and_passes_other_types_through():
    from app.models.archimate_element_types import exchange_type_for

    assert exchange_type_for("Application") == ("ApplicationComponent", {"entelim:specialization": "Application"})
    assert exchange_type_for("ApplicationComponent") == ("ApplicationComponent", {})
    assert exchange_type_for("Goal") == ("Goal", {})


def test_the_canonical_exporter_and_the_native_writer_both_use_exchange_type_for():
    from app.modules.architecture.services import archimate_xml_export_service as svc

    assert svc._elem_oef_type("Application") == "ApplicationComponent"
    assert svc._elem_oef_type("ApplicationComponent") == "ApplicationComponent"
    assert svc._elem_oef_type("business_actor") == "BusinessActor"
    assert svc._elem_oef_type(None) == "ApplicationComponent"
    source = open(svc.__file__, encoding="utf-8").read()
    assert source.count("exchange_type_for(") >= 2


# --------------------------------------------------------------- database


def _import(xml, org, db_session, tenant_ctx, strategy="skip_duplicates", **kw):
    from app.services.archimate_import_service import ArchiMateImportService

    with tenant_ctx(org.id):
        result = ArchiMateImportService().import_xml(xml, strategy=strategy, organization_id=org.id, **kw)
        db_session.commit()
    return result


_CTX = []


@pytest.fixture(autouse=True)
def _bind_tenant_ctx(tenant_ctx):
    """Reads are made inside the organisation's own tenant context, as a request would."""
    _CTX.append(tenant_ctx)
    yield
    _CTX.clear()


def _types(org_id):
    from app.models.archimate_core import ArchiMateElement

    with _CTX[0](org_id):
        rows = ArchiMateElement.query.filter(  # model-safety-ok: explicit org predicate
            ArchiMateElement.organization_id == org_id
        ).all()
        return Counter(r.type for r in rows)


def _inventory(org_id):
    from app.models.application_portfolio import ApplicationComponent

    with _CTX[0](org_id):
        return ApplicationComponent.query.filter(  # model-safety-ok: explicit org predicate
            ApplicationComponent.organization_id == org_id
        ).count()


def _rel_count(org_id):
    from app.models.archimate_core import ArchiMateRelationship

    with _CTX[0](org_id):
        return ArchiMateRelationship.query.filter(  # model-safety-ok: explicit org predicate
            ArchiMateRelationship.organization_id == org_id
        ).count()


@db_required
def test_archiet_import_default_stores_54_applications_1_component_and_54_inventory_rows(
    app, db_session, make_org, tenant_ctx
):
    org = make_org("exch-default")
    org_id = org.id
    _import(_archiet_xml(), org, db_session, tenant_ctx)
    types = _types(org_id)
    assert types["Application"] == 54
    assert types["ApplicationComponent"] == 1
    assert _inventory(org_id) == 54


@db_required
def test_archiet_import_with_the_switch_off_stores_55_components_and_no_inventory_rows(
    app, db_session, make_org, tenant_ctx
):
    org = make_org("exch-off")
    org_id = org.id
    _import(_archiet_xml(), org, db_session, tenant_ctx, promote_top_level=False)
    types = _types(org_id)
    assert types["Application"] == 0
    assert types["ApplicationComponent"] == 55
    assert _inventory(org_id) == 0


@db_required
def test_reimport_creates_no_duplicate_and_the_existing_type_is_kept(app, db_session, make_org, tenant_ctx):
    org = make_org("exch-reimport")
    org_id = org.id
    _import(_archiet_xml(), org, db_session, tenant_ctx)
    first = (_types(org_id), _inventory(org_id))
    _import(_archiet_xml(), org, db_session, tenant_ctx)
    assert (_types(org_id), _inventory(org_id)) == first

    # The same names arriving as promotable components land on the components already stored.
    other = make_org("exch-reimport-off")
    other_id = other.id
    _import(_archiet_xml(), other, db_session, tenant_ctx, promote_top_level=False)
    _import(_archiet_xml(), other, db_session, tenant_ctx)  # default: would promote, but the stored type wins
    assert _types(other_id)["ApplicationComponent"] == 55
    assert _types(other_id)["Application"] == 0
    assert _inventory(other_id) == 0


@db_required
def test_reimport_of_a_file_that_states_the_property_upgrades_the_stored_component(
    app, db_session, make_org, tenant_ctx
):
    org = make_org("exch-upgrade")
    org_id = org.id
    _import(_oef(_el("a", "Alpha")), org, db_session, tenant_ctx, promote_top_level=False)
    assert _types(org_id) == {"ApplicationComponent": 1}
    _import(_oef(_el("a", "Alpha", spec="Application"), "", PROP_DEF), org, db_session, tenant_ctx)
    assert _types(org_id) == {"Application": 1}
    assert _inventory(org_id) == 1


@db_required
def test_export_then_import_into_a_second_organisation_is_identical(app, db_session, make_org, tenant_ctx):
    from app.modules.architecture.services.archimate_xml_export_service import export_to_xml

    org_a, org_b = make_org("exch-rt-a"), make_org("exch-rt-b")
    a_id, b_id = org_a.id, org_b.id
    _import(_archiet_xml(), org_a, db_session, tenant_ctx)
    with tenant_ctx(a_id):
        exported = export_to_xml()
    assert 'type="Application"' not in exported
    assert exported.count("entelim:specialization") >= 1

    # The exported file marks Applications with the property, so import into B never promotes.
    _import(exported, org_b, db_session, tenant_ctx)
    assert _types(b_id) == _types(a_id)
    assert _rel_count(b_id) == _rel_count(a_id)
    assert _inventory(b_id) == _inventory(a_id) == 54
    assert _types(b_id)["ApplicationComponent"] == 1


@db_required
def test_no_exporter_entry_point_writes_the_application_type(app, db_session, make_org, tenant_ctx):
    import xml.etree.ElementTree as ET

    from app.models.archimate_core import ArchiMateElement
    from app.modules.architecture.services.archimate_xml_export_service import (
        ArchiMateXMLExportService,
        export_to_xml,
    )

    org = make_org("exch-noapp")
    org_id = org.id
    result = _import(_archiet_xml(), org, db_session, tenant_ctx)
    with tenant_ctx(org_id):
        outputs = [export_to_xml(), export_to_xml(result["model_id"])]
        # The Archi-native writer's element writer (its model-level entry points are not
        # reachable from any route and fail on the model's missing attributes before this change).
        parent = ET.Element("elements")
        for el in ArchiMateElement.query.filter(  # model-safety-ok: explicit org predicate
            ArchiMateElement.organization_id == org_id, ArchiMateElement.type == "Application"
        ).limit(3):
            ArchiMateXMLExportService()._add_element_to_xml(parent, el)
        native = ET.tostring(parent, encoding="unicode")
    for out in outputs + [native]:
        assert not re.search(r'type="Application"', out)
        assert "ApplicationComponent" in out
    assert "entelim:specialization" in outputs[0] and "entelim:specialization" in native


@db_required
def test_two_organisations_never_see_or_match_each_others_elements(app, db_session, make_org, tenant_ctx):
    from app.modules.architecture.services.archimate_xml_export_service import export_to_xml

    org_a, org_b = make_org("exch-iso-a"), make_org("exch-iso-b")
    a_id, b_id = org_a.id, org_b.id
    _import(_oef(_el("b1", "Entelim") + _el("b2", "Only In B")), org_b, db_session, tenant_ctx, promote_top_level=False)
    before_b = _types(b_id)
    _import(_archiet_xml(), org_a, db_session, tenant_ctx)

    # B is untouched by A's import: its same-named "Entelim" stayed a component, no inventory row.
    assert _types(b_id) == before_b
    assert _inventory(b_id) == 0
    assert _types(a_id)["Application"] == 54
    with tenant_ctx(b_id):
        out_b = export_to_xml()
    assert "Only In B" in out_b
    assert "BrainX" not in out_b
    with tenant_ctx(a_id):
        out_a = export_to_xml()
    assert "Only In B" not in out_a


# --------------------------------------------------------------- preview and screen


@db_required
def test_preview_names_every_decision_and_the_switch_changes_them(app, client, db_session, login_as, make_org):
    from app.models import User

    org = make_org("exch-preview")
    user = User(email=f"exch-preview-{org.id}@example.com", organization_id=org.id, confirmed=True)
    user.password = "x"
    db_session.add(user)
    db_session.flush()
    login_as(client, user)

    def post(extra):
        return client.post(
            "/architecture/import/oef/preview",
            data={"oef_file": (io.BytesIO(_archiet_xml().encode()), "archiet.xml"), **extra},
            content_type="multipart/form-data",
        )

    on = post({})
    assert on.status_code == 200, on.get_data(as_text=True)
    body = on.get_json()
    decisions = body["application_kind_decisions"]
    assert len(decisions) == 55
    assert Counter(d["type"] for d in decisions.values()) == {"Application": 54, "ApplicationComponent": 1}
    assert all(d["reason"] for d in decisions.values())

    off = post({"promote_top_level": "0"}).get_json()["application_kind_decisions"]
    assert Counter(d["type"] for d in off.values()) == {"ApplicationComponent": 55}

    page = client.get("/architecture/import/oef").get_data(as_text=True)
    assert "Treat top-level components as applications" in page
    assert 'name="promote_top_level"' in page
