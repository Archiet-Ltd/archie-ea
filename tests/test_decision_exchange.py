"""DMN XML: the exported file validates against the DMN 1.5 schema and imports into another organisation."""

import pathlib

import pytest
from lxml import etree

from app.modules.architecture.services import decision_exchange_service as ex
from app.modules.architecture.services import decision_table_service as dts
from app.services.archimate_import_service import ArchiMateImportService, ImportRequestError

pytestmark = pytest.mark.usefixtures("db_session")

XSD = pathlib.Path(__file__).parent / "fixtures" / "dmn15" / "DMN15.xsd"

CAMUNDA_STYLE = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="d1" name="Credit" namespace="http://camunda.org/schema/1.0/dmn">
  <decision id="dish" name="Dish">
    <decisionTable id="t1" hitPolicy="COLLECT">
      <input id="i1" label="Season"><inputExpression id="ie1" typeRef="string"><text>season</text></inputExpression></input>
      <output id="o1" label="Dish" name="dish" typeRef="string"/>
      <rule id="r1"><inputEntry id="e1"><text>"Winter"</text></inputEntry><outputEntry id="x1"><text>"Stew"</text></outputEntry></rule>
      <rule id="r2"><inputEntry id="e2"><text>-</text></inputEntry><outputEntry id="x2"><text>"Salad"</text></outputEntry></rule>
    </decisionTable>
  </decision>
  <decision id="rank" name="Rank">
    <decisionTable id="t2" hitPolicy="PRIORITY">
      <input id="i2"><inputExpression id="ie2" typeRef="number"><text>n</text></inputExpression></input>
      <output id="o2" name="r" typeRef="string"/>
      <rule id="r3"><inputEntry id="e3"><text>1</text></inputEntry><outputEntry id="x3"><text>"a"</text></outputEntry></rule>
    </decisionTable>
  </decision>
</definitions>"""


def _supplier(org):
    d = dts.create_decision(
        "Approve supplier", "F",
        inputs=[{"label": "Annual spend", "type": "number"}, {"label": "Risk rating", "type": "string"}],
        outputs=[{"label": "Approval", "type": "string"}], description="Who signs off a new supplier",
        org_id=org.id,
    )
    dts.save_rule(d, ["<1000", "-"], ['"auto approve"'])
    dts.save_rule(d, ["[1000..50000]", '"low","medium"'], ['"buyer approves"'], "Standard path & more")
    dts.save_rule(d, [">50000", "-"], ['"board approves"'])
    return d


def test_exported_file_validates_against_the_dmn_15_schema(make_org, tenant_ctx):
    org = make_org("dmn")
    with tenant_ctx(org.id):
        xml = ex.export_dmn(_supplier(org))
    schema = etree.XMLSchema(etree.parse(str(XSD)))
    document = etree.fromstring(xml.encode("utf-8"))
    schema.assertValid(document)
    assert 'hitPolicy="FIRST"' in xml and "&lt;1000" in xml
    assert document.nsmap[None] == "https://www.omg.org/spec/DMN/20230324/MODEL/"


def test_the_schema_check_has_teeth():
    schema = etree.XMLSchema(etree.parse(str(XSD)))
    bad = etree.fromstring(
        b'<definitions xmlns="https://www.omg.org/spec/DMN/20230324/MODEL/" id="a" name="n" namespace="x">'
        b'<decision id="b" name="n"><decisionTable hitPolicy="SOMETIMES"/></decision></definitions>'
    )
    assert not schema.validate(bad)


def test_export_imports_into_a_second_organisation_with_the_same_rules(make_org, tenant_ctx):
    org_a, org_b = make_org("dmn-a"), make_org("dmn-b")
    with tenant_ctx(org_a.id):
        source = _supplier(org_a)
        xml = ex.export_dmn(source)
        before = [(r.position, list(r.input_entries), list(r.output_entries), r.annotation) for r in dts.rules_of(source)]
    with tenant_ctx(org_b.id):
        assert ex.preview_dmn(xml)["summary"]["new"] == 1
        result = ArchiMateImportService().import_xml(xml, "skip_duplicates", org_b.id)
        assert (result["created"], result["failed"]) == (1, 0)
        (copy,) = dts.list_decisions(org_b.id)
        assert copy.name == "Approve supplier" and copy.description == "Who signs off a new supplier"
        assert dts.definition(copy) == dts.definition(source)
        after = [(r.position, list(r.input_entries), list(r.output_entries), r.annotation) for r in dts.rules_of(copy)]
        assert after == before
        assert dts.evaluate(copy, ["5000", "low"])["matched"] == [2]
        # a second import: skipped, then identical in the preview
        assert ex.preview_dmn(xml)["summary"]["exists"] == 1
        assert ArchiMateImportService().import_xml(xml, "skip_duplicates", org_b.id)["skipped"] == 1
        assert len(dts.list_decisions(org_b.id)) == 1
        assert ArchiMateImportService().import_xml(xml, "create_all", org_b.id)["created"] == 1
        assert len(dts.list_decisions(org_b.id)) == 2
    with tenant_ctx(org_a.id):
        assert len(dts.list_decisions(org_a.id)) == 1


def test_update_existing_replaces_the_rules(make_org, tenant_ctx):
    org = make_org("dmn")
    with tenant_ctx(org.id):
        d = _supplier(org)
        xml = ex.export_dmn(d)
        dts.delete_rule(d, 3)
        assert len(dts.rules_of(d)) == 2
        assert ex.preview_dmn(xml)["summary"]["conflict"] == 1
        result = ArchiMateImportService().import_xml(xml, "update_existing", org.id)
        assert result["updated"] == 1 and len(dts.rules_of(d)) == 3


def test_a_file_from_another_tool_imports_what_it_can_and_says_why_not(make_org, tenant_ctx):
    org = make_org("dmn")
    with tenant_ctx(org.id):
        preview = ex.preview_dmn(CAMUNDA_STYLE)
        assert preview["summary"]["new"] == 1 and preview["summary"]["invalid"] == 1
        assert "Priority hit policy, which is not supported yet" in preview["elements"][1]["application_kind_decision"]
        result = ex.import_dmn(CAMUNDA_STYLE, "skip_duplicates", org.id)
        assert (result["created"], result["failed"]) == (1, 1)
        (dish,) = dts.list_decisions(org.id)
        assert dts.definition(dish)["hit_policy"] == "C"
        assert dts.evaluate(dish, ["Winter"])["outputs"] == [{"Dish": "Stew"}, {"Dish": "Salad"}]


def test_a_file_that_is_not_dmn_is_refused(make_org, tenant_ctx):
    org = make_org("dmn")
    with tenant_ctx(org.id):
        with pytest.raises(ImportRequestError):
            ex.preview_dmn('<definitions xmlns="https://www.omg.org/spec/DMN/20230324/MODEL/"/>')


def _one_table(entry, type_ref="string"):
    return """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20230324/MODEL/" id="d" name="M" namespace="urn:x">
  <decision id="dec" name="Risky">
    <decisionTable id="t" hitPolicy="UNIQUE">
      <input id="i" label="Risk rating"><inputExpression id="ie" typeRef="%s"><text>r</text></inputExpression></input>
      <output id="o" label="Out" name="out" typeRef="string"/>
      <rule id="r"><inputEntry id="e"><text>%s</text></inputEntry><outputEntry id="x"><text>"a"</text></outputEntry></rule>
    </decisionTable>
  </decision>
</definitions>""" % (type_ref, entry.replace('"', "&quot;"))


@pytest.mark.parametrize("entry,type_ref,reason", [
    ("[1..10]", "string", "needs a number column"),
    (">true", "boolean", "needs a number column"),
    ('&quot;a&quot;,&quot;b', "string", "quote that is not closed"),
    ("false", "number", '"false" is not a number'),
    ("true", "number", '"true" is not a number'),
    ('&quot;abc&quot;', "number", '"abc" is not a number'),
])
def test_a_file_with_an_unreadable_entry_is_refused_and_stores_nothing(make_org, tenant_ctx, entry, type_ref, reason):
    org = make_org("dmn-badfile")
    with tenant_ctx(org.id):
        xml = _one_table(entry, type_ref)
        preview = ex.preview_dmn(xml)
        assert preview["summary"]["invalid"] == 1
        assert reason in preview["elements"][0]["application_kind_decision"]
        result = ex.import_dmn(xml, "skip_duplicates", org.id)
        assert (result["created"], result["failed"]) == (0, 1)
        assert reason in result["errors"][0]
        assert dts.list_decisions(org.id) == []
