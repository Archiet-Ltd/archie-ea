"""Consumers and providers of an element follow what each relationship means.

"A serves B" means B depends on A, so B is A's consumer; "C accesses D"
means C depends on D. The impact graph's consumer/provider lists follow only
consistent dependency chains (a sibling that shares a provider is not a
consumer), mark each entry's hop count, and the application fact sheet reads
the same direction.
"""

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _element(db_session, name, el_type="ApplicationComponent", layer="Application"):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(name=name, type=el_type, layer=layer)
    db_session.add(el)
    db_session.flush()
    return el


def _relate(db_session, source, target, rel_type):
    from app.models.archimate_core import ArchiMateRelationship

    db_session.add(ArchiMateRelationship(source_id=source.id, target_id=target.id, type=rel_type))
    db_session.flush()


def test_dependency_ends_reads_the_relationship_meaning():
    from app.services.impact_graph import dependency_ends

    assert dependency_ends("serving", 1, 2) == (2, 1)
    assert dependency_ends("ServingRelationship", 1, 2) == (2, 1)
    assert dependency_ends("realization", 1, 2) == (2, 1)
    assert dependency_ends("access", 1, 2) == (1, 2)
    assert dependency_ends("composition", 1, 2) == (1, 2)
    assert dependency_ends("association", 1, 2) is None


def test_consumers_and_providers_follow_consistent_chains(db_session, make_org, tenant_ctx):
    from app.services.impact_graph import build_impact_graph

    org = make_org("impact")
    with tenant_ctx(org.id):
        a, b, c = (_element(db_session, n) for n in ("Ledger", "Billing", "Portal"))
        sibling = _element(db_session, "Reporting")
        data = _element(db_session, "Invoice", "DataObject", "Application")
        _relate(db_session, a, b, "serving")        # B consumes A
        _relate(db_session, b, c, "serving")        # C consumes B
        _relate(db_session, a, sibling, "serving")  # a second consumer of A
        _relate(db_session, a, data, "access")      # A depends on the data object

        graph = build_impact_graph(a.id, depth=2)
        assert [(i["name"], i["hops"]) for i in graph["consumers"]] == [
            ("Billing", 1), ("Reporting", 1), ("Portal", 2)]
        assert [(i["name"], i["hops"]) for i in graph["providers"]] == [("Invoice", 1)]
        assert graph["counts"]["upstream"] == 3 and graph["counts"]["downstream"] == 1

        # From Billing, Reporting is a sibling that shares a provider: not a consumer.
        graph = build_impact_graph(b.id, depth=3)
        assert [(i["name"], i["hops"]) for i in graph["consumers"]] == [("Portal", 1)]
        assert [(i["name"], i["hops"]) for i in graph["providers"]] == [("Ledger", 1), ("Invoice", 2)]
        directions = {n["name"]: n["direction"] for n in graph["nodes"]}
        assert directions["Reporting"] == "related" and directions["Ledger"] == "downstream"

        assert build_impact_graph(c.id, depth=1)["providers"] == [
            {"id": b.id, "name": "Billing", "type": "ApplicationComponent", "layer": "application", "hops": 1}]


def test_fact_sheet_reads_serving_the_same_way(db_session, make_org, tenant_ctx):
    from types import SimpleNamespace

    from app.services.application_fact_sheet import _dependencies

    org = make_org("factsheet")
    with tenant_ctx(org.id):
        a, b = _element(db_session, "Ledger"), _element(db_session, "Billing")
        _relate(db_session, a, b, "serving")
        on_a = _dependencies(SimpleNamespace(archimate_element_id=a.id))
        on_b = _dependencies(SimpleNamespace(archimate_element_id=b.id))
    assert [d["name"] for d in on_a["upstream"]] == ["Billing"] and on_a["downstream"] == []
    assert [d["name"] for d in on_b["downstream"]] == ["Ledger"] and on_b["upstream"] == []


def test_one_application_component_may_serve_another():
    from app.services.archimate_validity_service import ArchimateValidityService

    svc = ArchimateValidityService()
    assert svc.is_valid("ApplicationComponent", "ApplicationComponent", "serving")
    assert svc.is_valid("ApplicationComponent", "ApplicationComponent", "flow")
    assert svc.is_valid("ApplicationService", "ApplicationComponent", "serving")
    assert not svc.is_valid("DataObject", "ApplicationComponent", "serving")
