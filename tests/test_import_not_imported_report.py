"""What an Open Exchange import cannot keep is reported, never dropped silently.

Parser, aggregation and composer-message tests need no database. Tests marked
``db_required`` need PostgreSQL and are skipped without ``TEST_DATABASE_URL``.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.archimate_import_service import ArchiMateImportService

db_required = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set - needs PostgreSQL",
)

OEF_MODIFIERS = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"
       xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-mod">
  <name xml:lang="en">Modifiers</name>
  <documentation xml:lang="en">About the model</documentation>
  <properties><property propertyDefinitionRef="pd1"><value>v</value></property></properties>
  <elements>
    <element identifier="A1" xsi:type="ApplicationComponent">
      <name>App</name>
      <documentation xml:lang="en">English</documentation>
      <documentation xml:lang="de">Deutsch</documentation>
      <properties>
        <property propertyDefinitionRef="pd1"><value xml:lang="en">one</value><value xml:lang="de">eins</value></property>
      </properties>
    </element>
    <element identifier="D1" xsi:type="DataObject"><name>Data</name></element>
  </elements>
  <relationships>
    <relationship identifier="R1" source="A1" target="D1" xsi:type="Access" accessType="Read"/>
    <relationship identifier="R2" source="A1" target="D1" xsi:type="Association" isDirected="true"/>
    <relationship identifier="R3" source="A1" target="D1" xsi:type="Serving" accessType="Read" custom="x"/>
  </relationships>
  <propertyDefinitions>
    <propertyDefinition identifier="pd1" type="string"><name>tag</name></propertyDefinition>
  </propertyDefinitions>
</model>
"""


def _by_kind(parsed):
    out = {}
    for item in parsed["unrepresentable"]:
        out.setdefault(item["kind"], []).append(item)
    return out


def test_relationship_access_type_is_reported():
    parsed = ArchiMateImportService().parse_oef_xml(OEF_MODIFIERS)
    assert all("access_mode" not in r for r in parsed["relationships"])
    kinds = _by_kind(parsed)
    access = kinds["relationship access type"]
    assert [i["identifier"] for i in access] == ["R1", "R3"]
    assert access[0]["element"] == "R1 (Read)"
    assert access[0]["reason"] == (
        "the access direction is not stored; the relationship imports as a plain Access"
    )
    assert [i["element"] for i in kinds["relationship access type / direction"]] == ["R2"]
    assert [i["element"] for i in kinds["relationship attribute"]] == ["R3 / custom"]


def test_other_unstored_content_is_reported():
    kinds = _by_kind(ArchiMateImportService().parse_oef_xml(OEF_MODIFIERS))
    assert len(kinds["relationship identifier"]) == 3
    assert kinds["extra language documentation"][0]["element"] == "App"
    assert kinds["extra language value"][0]["element"] == "App / tag"
    assert kinds["model documentation"]
    assert kinds["model property"][0]["element"] == "tag"


def test_second_language_relationship_documentation_is_reported():
    xml = OEF_MODIFIERS.replace(
        '<relationship identifier="R2" source="A1" target="D1" xsi:type="Association" isDirected="true"/>',
        '<relationship identifier="R2" source="A1" target="D1" xsi:type="Association">'
        '<documentation xml:lang="en">a</documentation><documentation xml:lang="de">b</documentation>'
        '</relationship>',
    )
    kinds = _by_kind(ArchiMateImportService().parse_oef_xml(xml))
    assert any(i["element"] == "R2" for i in kinds["extra language documentation"])


def test_reported_text_is_bounded():
    entries = [{"kind": "property", "element": "E" * 500, "identifier": "", "key": "k" * 500,
                "value": "v" * 500, "reason": "r" * 500, "count": 1}]
    out = ArchiMateImportService._aggregate_entries(entries)[0]
    assert all(len(out[k]) <= 80 for k in ("element", "key", "value", "reason"))


def test_many_property_refusals_become_one_entry():
    entries = [
        {"kind": "property", "element": "E%d" % i, "identifier": "i%d" % i,
         "key": "rate_limit", "value": "x", "reason": "Enter a number for this property.",
         "count": 1}
        for i in range(400)
    ]
    out = ArchiMateImportService._aggregate_entries(entries)
    assert len(out) == 1
    assert out[0]["count"] == 400
    assert len(out[0]["examples"]) == 5
    assert out[0]["examples"][0]["name"] == "E0 / rate_limit"


def test_few_property_refusals_stay_separate():
    entries = [
        {"kind": "property", "element": "E%d" % i, "identifier": "", "key": "k",
         "value": "x", "reason": "r", "count": 1}
        for i in range(5)
    ]
    assert len(ArchiMateImportService._aggregate_entries(entries)) == 5


# --- composer message (pure JS, run with node) ------------------------------

_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "archimate" / "composer_persistence.js"


def _message(unrepresentable, truncated):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    script = (
        "const vm=require('vm'),fs=require('fs');"
        "const src=fs.readFileSync(process.argv[1],'utf8')+';ComposerPersistence;';"
        "const cp=vm.runInNewContext(src,{});"
        "const a=JSON.parse(process.argv[2]);"
        "process.stdout.write(JSON.stringify(cp.notImportedMessage(a.u,a.t)));"
    )
    out = subprocess.run(
        [node, "-e", script, str(_JS), json.dumps({"u": unrepresentable, "t": truncated})],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def test_composer_message_is_empty_when_nothing_left_out():
    assert _message([], []) == ""


def test_composer_message_has_counts_and_first_five_examples():
    items = [{"kind": "view", "element": "V%d" % i, "identifier": "", "count": 1} for i in range(7)]
    text = _message(items, [{"element_id": "s", "length": 300}])
    assert "7 items in the file were not imported" in text
    assert "1 name cut to fit the 255-character limit" in text
    assert "view: V4" in text and "view: V5" not in text


def test_clean_large_file_gives_no_toast_text():
    items = (
        [{"kind": "element identifier", "element": "%d found" % 1000, "count": 1000}]
        + [{"kind": "relationship identifier", "element": "%d found" % 2000, "count": 2000}]
    )
    assert _message(items, []) == ""


def test_composer_message_uses_aggregate_examples():
    items = [{"kind": "property", "element": "400 found", "count": 400,
              "examples": [{"name": "E1 / k", "identifier": ""}]}]
    text = _message(items, None)
    assert "400 items" in text and "property: E1 / k" in text


def test_composer_scripts_surface_the_report():
    root = _JS.parent
    persistence = _JS.read_text(encoding="utf8")
    composer = (root / "composer.js").read_text(encoding="utf8")
    assert "notImportedMessage(data.unrepresentable, data.truncated_names)" in persistence
    assert "notImportedMessage(null, data.truncated_names)" in persistence
    assert "ComposerPersistence.notImportedMessage(data.unrepresentable, data.truncated_names)" in composer
    assert "ComposerPersistence.notImportedMessage(null, data.truncated_names)" in composer


# --- database-backed ---------------------------------------------------------

@db_required
def test_access_type_is_reported_and_nothing_is_stored(app, db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateRelationship

    org = make_org("access-type")
    with tenant_ctx(org.id):
        svc = ArchiMateImportService()
        result = svc.execute_import(svc.parse_oef_xml(OEF_MODIFIERS), strategy="skip_duplicates")
        rel = ArchiMateRelationship.query.filter_by(type="access").first()
        assert rel is not None and rel.access_mode is None
        assert any(u["kind"] == "relationship access type" for u in result["unrepresentable"])


@db_required
def test_model_name_over_255_is_reported(app, db_session, make_org, tenant_ctx):
    xml = OEF_MODIFIERS.replace("<name xml:lang=\"en\">Modifiers</name>",
                                "<name xml:lang=\"en\">%s</name>" % ("M" * 300), 1)
    org = make_org("long-model-name")
    with tenant_ctx(org.id):
        svc = ArchiMateImportService()
        result = svc.execute_import(svc.parse_oef_xml(xml), strategy="skip_duplicates")
        assert any(u["kind"] == "model name" for u in result["unrepresentable"])


def _login_editor(app, db_session, org, label):
    from app.models.user import User
    from tests.test_ba_tenant_and_authz import _login

    user = User(email="%s-%s@example.com" % (label, org.id), organization_id=org.id,
                enterprise_role="enterprise_architect", confirmed=True)
    db_session.add(user)
    db_session.commit()
    client = app.test_client()
    _login(client, user.id)
    return client


@db_required
def test_import_endpoint_returns_unrepresentable(app, db_session, make_org, tenant_ctx):
    org = make_org("import-endpoint-report")
    with tenant_ctx(org.id):
        client = _login_editor(app, db_session, org, "ie")
        resp = client.post("/archimate/api/import/oef", json={"xml_content": OEF_MODIFIERS})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        kinds = {u["kind"] for u in resp.get_json()["unrepresentable"]}
        assert "relationship access type" in kinds
        assert "model documentation" in kinds


@db_required
def test_materialize_and_viewpoint_routes_report_truncated_names(app, db_session, make_org, tenant_ctx):
    org = make_org("truncated-report")
    with tenant_ctx(org.id):
        client = _login_editor(app, db_session, org, "tr")
        long_el = {"element_id": "src-long", "name": "L" * 300, "el_type": "ApplicationComponent",
                   "x": 1, "y": 1}

        mat = client.post("/archimate/api/elements/materialize", json={"elements": [dict(long_el)]})
        assert mat.status_code == 200, mat.get_data(as_text=True)
        assert [t["element_id"] for t in mat.get_json()["truncated_names"]] == ["src-long"]

        created = client.post("/archimate/api/saved-viewpoints", json={
            "name": "Long names", "elements": [dict(long_el, element_id="src-long-2")],
            "relationships": [],
        })
        assert created.status_code == 201, created.get_data(as_text=True)
        assert created.get_json()["truncated_names"]

        vp_id = created.get_json()["id"]
        updated = client.put("/archimate/api/saved-viewpoints/%s" % vp_id, json={
            "name": "Long names", "elements": [dict(long_el, element_id="src-long-3")],
            "relationships": [],
        })
        assert updated.status_code == 200, updated.get_data(as_text=True)
        assert updated.get_json()["truncated_names"]
