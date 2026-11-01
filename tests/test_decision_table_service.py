"""DMN decision tables: unary tests, hit policies, the link to a process, and isolation."""

import pytest

from app import db
from app.models.archimate_core import ArchiMateElement
from app.modules.architecture.services import decision_table_service as dts

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.mark.parametrize("entry,value,type_ref,expected", [
    ("-", "anything", "string", True),
    ("", "anything", "string", True),
    ('"gold"', "gold", "string", True),
    ('"gold"', "silver", "string", False),
    ('"gold","silver"', "silver", "string", True),
    ('not("gold")', "silver", "string", True),
    ('not("gold")', "gold", "string", False),
    ("<1000", "999", "number", True),
    ("<1000", "1000", "number", False),
    (">=5", "5", "number", True),
    ("[1..10]", "10", "number", True),
    ("(1..10)", "10", "number", False),
    ("(1..10]", "10", "number", True),
    ("42", "42.0", "number", True),
    ("true", "yes", "boolean", True),
    ("false", "true", "boolean", False),
])
def test_unary_tests(entry, value, type_ref, expected):
    assert dts.entry_matches(entry, value, type_ref) is expected


def test_unreadable_input_is_refused_in_plain_words():
    with pytest.raises(dts.DecisionError, match="not a number"):
        dts.entry_matches("<10", "abc", "number")
    assert dts.check_entry("<abc", "number") == '"abc" is not a number.'


def _supplier_decision(org, policy="F"):
    d = dts.create_decision(
        "Approve supplier", policy,
        inputs=[{"label": "Annual spend", "type": "number"}, {"label": "Risk rating", "type": "string"}],
        outputs=[{"label": "Approval", "type": "string"}], org_id=org.id,
    )
    dts.save_rule(d, ["<1000", "-"], ['"auto approve"'])
    dts.save_rule(d, ["[1000..50000]", '"low","medium"'], ['"buyer approves"'], "Standard path")
    dts.save_rule(d, [">50000", "-"], ['"board approves"'])
    return d


def test_first_hit_policy_names_the_matching_rule(make_org, tenant_ctx):
    org = make_org("dmn")
    with tenant_ctx(org.id):
        d = _supplier_decision(org)
        result = dts.evaluate(d, ["5000", "low"])
    assert result["matched"] == [2]
    assert result["outputs"] == [{"Approval": "buyer approves"}]
    assert result["error"] is None


def test_unique_refuses_two_matches_and_collect_lists_them(make_org, tenant_ctx):
    org = make_org("dmn")
    with tenant_ctx(org.id):
        d = dts.create_decision(
            "Overlap", "U", inputs=[{"label": "n", "type": "number"}],
            outputs=[{"label": "out", "type": "string"}], org_id=org.id,
        )
        dts.save_rule(d, [">1"], ['"a"'])
        dts.save_rule(d, [">2"], ['"b"'])
        unique = dts.evaluate(d, ["5"])
        dts.update_decision(d, hit_policy="C")
        collect = dts.evaluate(d, ["5"])
        none = dts.evaluate(d, ["0"])
    assert unique["matched"] == [1, 2] and "allows only one" in unique["error"]
    assert collect["outputs"] == [{"out": "a"}, {"out": "b"}]
    assert none["matched"] == [] and none["message"] == "No rule matched this input."


def test_link_is_visible_from_both_sides_and_stays_in_its_organisation(make_org, tenant_ctx):
    org_a, org_b = make_org("dmn-a"), make_org("dmn-b")
    with tenant_ctx(org_a.id):
        process = ArchiMateElement(name="Onboard supplier", type="BusinessProcess", layer="business",
                                   organization_id=org_a.id)
        db.session.add(process)
        d = _supplier_decision(org_a)
        db.session.flush()
        dts.link_process(d, process.id)
        assert [p.name for p in dts.processes_of(d)] == ["Onboard supplier"]
        assert [x.name for x in dts.decisions_of(process)] == ["Approve supplier"]
        with pytest.raises(dts.DecisionError, match="already applies"):
            dts.link_process(d, process.id)
        decision_id, process_id = d.id, process.id
    with tenant_ctx(org_b.id):
        assert dts.list_decisions(org_b.id) == []
        with pytest.raises(dts.DecisionError) as err:
            dts.get_decision(decision_id, org_b.id)
        assert err.value.status_code == 404
        other = dts.create_decision("Approve supplier", "U", org_id=org_b.id)
        with pytest.raises(dts.DecisionError, match="not found"):
            dts.link_process(other, process_id)


def test_removing_a_column_trims_every_rule(make_org, tenant_ctx):
    org = make_org("dmn")
    with tenant_ctx(org.id):
        d = _supplier_decision(org)
        dts.remove_column(d, "input", 1)
        assert [list(r.input_entries) for r in dts.rules_of(d)] == [["<1000"], ["[1000..50000]"], [">50000"]]
        dts.delete_rule(d, 2)
        assert [r.position for r in dts.rules_of(d)] == [1, 2]
