"""R1-B30 (TB-0037): ``IntelligenceQueryService.set_impact``, the combined,
de-duplicated, ranked blast radius of a set of elements.

The combined answer is built ON ``cross_layer_impact`` (the one canonical
walk), so these tests pin that it equals the union of the single answers, that
shared rows are counted once and ranked first, that a foreign seed reads like
a missing one, and that with no organisation nothing is read.

Fixtures (app, db_session, make_org) come from app/modules/conftest.py.
"""

from __future__ import annotations

from app.modules.intelligence.services import query_service
from app.modules.intelligence.services.query_service import IntelligenceQueryService


def _element(db_session, org_id, name, type_="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _link(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type=type_, organization_id=org_id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _as(app, org_id, call):
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_id
        return call()


def _graph(db_session, org_id):
    """s1 -> x, s1 -> y, s2 -> y, y -> z (Serving): y and z are reached from
    both seeds, x only from s1."""
    s1 = _element(db_session, org_id, "S1")
    s2 = _element(db_session, org_id, "S2")
    x = _element(db_session, org_id, "X")
    y = _element(db_session, org_id, "Y")
    z = _element(db_session, org_id, "Z")
    _link(db_session, org_id, s1, x)
    _link(db_session, org_id, s1, y)
    _link(db_session, org_id, s2, y)
    _link(db_session, org_id, y, z)
    db_session.commit()
    return s1, s2, x, y, z


def test_combined_answer_is_the_union_of_the_single_answers_with_shared_rows_once(app, db_session, make_org):
    org = make_org("set-union")
    s1, s2, x, y, z = _graph(db_session, org.id)

    def run():
        single_1 = IntelligenceQueryService.cross_layer_impact(s1.id, with_owner=False)
        single_2 = IntelligenceQueryService.cross_layer_impact(s2.id, with_owner=False)
        combined = IntelligenceQueryService.set_impact([s1.id, s2.id])
        return single_1, single_2, combined

    single_1, single_2, combined = _as(app, org.id, run)

    union = {r["element_id"] for r in single_1["rows"]} | {r["element_id"] for r in single_2["rows"]}
    assert union == {x.id, y.id, z.id}
    ids = [r["element_id"] for r in combined["rows"]]
    assert sorted(ids) == sorted(union)
    assert len(ids) == len(set(ids)), "a shared row must be counted once"
    by_id = {r["element_id"]: r for r in combined["rows"]}
    assert by_id[y.id]["seed_ids"] == sorted([s1.id, s2.id])
    assert by_id[x.id]["seed_ids"] == [s1.id]
    assert combined["summary"]["shared_row_count"] == 2
    assert combined["summary"]["row_count"] == 3
    assert combined["summary"]["resolved_seed_count"] == 2


def test_rows_are_ranked_by_how_many_failing_seeds_reach_them_then_distance(app, db_session, make_org):
    org = make_org("set-rank")
    s1, s2, x, y, z = _graph(db_session, org.id)

    result = _as(app, org.id, lambda: IntelligenceQueryService.set_impact([s1.id, s2.id]))

    assert [r["element_id"] for r in result["rows"]] == [y.id, z.id, x.id]
    assert [r["rank"] for r in result["rows"]] == [1, 2, 3]


def test_a_seed_reached_from_another_seed_is_marked_as_a_seed(app, db_session, make_org):
    org = make_org("set-seed-flag")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _link(db_session, org.id, a, b)
    db_session.commit()

    result = _as(app, org.id, lambda: IntelligenceQueryService.set_impact([a.id, b.id]))

    row = next(r for r in result["rows"] if r["element_id"] == b.id)
    assert row["is_seed"] is True


def test_a_foreign_seed_reads_like_a_missing_one_and_none_of_its_graph_appears(app, db_session, make_org):
    org_a, org_b = make_org("set-iso-a"), make_org("set-iso-b")
    own_seed = _element(db_session, org_a.id, "OwnSeed")
    own_dep = _element(db_session, org_a.id, "OwnDependant")
    _link(db_session, org_a.id, own_seed, own_dep)
    foreign_seed = _element(db_session, org_b.id, "ForeignSeed")
    foreign_dep = _element(db_session, org_b.id, "ForeignDependant")
    _link(db_session, org_b.id, foreign_seed, foreign_dep)
    # A foreign relationship that points at the CALLER's element id: the foreign
    # row exists, and the caller's element is its target.
    _link(db_session, org_b.id, foreign_seed, own_dep)
    db_session.commit()
    missing = 999_999_999

    result = _as(
        app, org_a.id,
        lambda: IntelligenceQueryService.set_impact([own_seed.id, foreign_seed.id, missing]),
    )

    assert [r["element_id"] for r in result["rows"]] == [own_dep.id]
    assert sorted(result["unresolved_seed_ids"]) == sorted([foreign_seed.id, missing])
    assert result["seed_element_ids"] == [own_seed.id]
    assert str(foreign_dep.id) not in result["elements"]
    assert str(foreign_seed.id) not in result["elements"]
    assert "ForeignDependant" not in repr(result)


def test_only_foreign_seeds_answers_element_not_found_with_no_rows(app, db_session, make_org):
    org_a, org_b = make_org("set-only-foreign-a"), make_org("set-only-foreign-b")
    foreign = _element(db_session, org_b.id, "Foreign")
    db_session.commit()

    result = _as(app, org_a.id, lambda: IntelligenceQueryService.set_impact([foreign.id]))

    assert result["rows"] == []
    assert result["summary"]["resolved_seed_count"] == 0
    assert "element_not_found" in result["reasons"]


def test_with_no_organisation_nothing_is_read(app, db_session, make_org):
    org = make_org("set-no-org")
    s1, s2, *_ = _graph(db_session, org.id)

    result = _as(app, None, lambda: IntelligenceQueryService.set_impact([s1.id, s2.id]))

    assert result["rows"] == []
    assert result["reasons"] == ["no_tenant_context"]
    assert result["elements"] == {}


def test_naming_no_element_is_an_honest_empty_answer(app, db_session, make_org):
    org = make_org("set-none")
    db_session.commit()

    result = _as(app, org.id, lambda: IntelligenceQueryService.set_impact([]))

    assert result["rows"] == []
    assert result["reasons"] == ["no_seed_elements"]


def test_a_filter_selects_seeds_from_the_callers_organisation_only(app, db_session, make_org):
    org_a, org_b = make_org("set-filter-a"), make_org("set-filter-b")
    supplier_a = _element(db_session, org_a.id, "SupplierA", type_="BusinessActor", layer="business")
    service_a = _element(db_session, org_a.id, "ServiceA", type_="BusinessService", layer="business")
    _link(db_session, org_a.id, supplier_a, service_a)
    supplier_b = _element(db_session, org_b.id, "SupplierB", type_="BusinessActor", layer="business")
    service_b = _element(db_session, org_b.id, "ServiceB", type_="BusinessService", layer="business")
    _link(db_session, org_b.id, supplier_b, service_b)
    db_session.commit()

    result = _as(
        app, org_a.id,
        lambda: IntelligenceQueryService.set_impact(element_filter={"type": "BusinessActor"}),
    )

    assert result["seed_element_ids"] == [supplier_a.id]
    assert [r["element_id"] for r in result["rows"]] == [service_a.id]
    assert service_b.id not in [r["element_id"] for r in result["rows"]]


def test_a_filter_with_neither_type_nor_layer_is_refused(app, db_session, make_org):
    import pytest

    org = make_org("set-filter-empty")
    db_session.commit()

    with pytest.raises(ValueError):
        _as(app, org.id, lambda: IntelligenceQueryService.set_impact(element_filter={}))
    with pytest.raises(ValueError):
        _as(app, org.id, lambda: IntelligenceQueryService.set_impact(element_filter={"type": ""}))


def test_more_named_seeds_than_the_ceiling_are_refused_not_silently_cut(app, db_session, make_org, monkeypatch):
    import pytest

    org = make_org("set-seed-cap")
    seeds = [_element(db_session, org.id, f"Seed{i}") for i in range(4)]
    db_session.commit()
    monkeypatch.setattr(query_service, "SET_IMPACT_MAX_SEEDS", 3)

    with pytest.raises(ValueError):
        _as(app, org.id, lambda: IntelligenceQueryService.set_impact([s.id for s in seeds]))


def test_a_filter_fills_only_the_places_the_named_seeds_leave(app, db_session, make_org, monkeypatch):
    org = make_org("set-filter-cap")
    named = _element(db_session, org.id, "Named", type_="Node", layer="technology")
    actors = [_element(db_session, org.id, f"A{i}", type_="BusinessActor", layer="business") for i in range(4)]
    db_session.commit()
    monkeypatch.setattr(query_service, "SET_IMPACT_MAX_SEEDS", 3)

    result = _as(
        app, org.id,
        lambda: IntelligenceQueryService.set_impact([named.id], element_filter={"type": "BusinessActor"}),
    )

    assert "seed_limit_reached" in result["reasons"]
    assert result["seed_element_ids"] == [named.id, actors[0].id, actors[1].id]


def test_rows_beyond_the_ceiling_are_cut_but_the_total_stays_true(app, db_session, make_org, monkeypatch):
    org = make_org("set-row-cap")
    root = _element(db_session, org.id, "Root")
    for i in range(5):
        _link(db_session, org.id, root, _element(db_session, org.id, f"Dep{i}"))
    db_session.commit()
    monkeypatch.setattr(query_service, "SET_IMPACT_MAX_ROWS", 2)

    result = _as(app, org.id, lambda: IntelligenceQueryService.set_impact([root.id]))

    assert len(result["rows"]) == 2
    assert result["total"] == 5
    assert result["truncated"] is True
    assert "rows_truncated" in result["reasons"]


def test_an_element_with_nothing_depending_on_it_is_stated_not_zeroed(app, db_session, make_org):
    org = make_org("set-nothing")
    lonely = _element(db_session, org.id, "Lonely")
    db_session.commit()

    result = _as(app, org.id, lambda: IntelligenceQueryService.set_impact([lonely.id]))

    assert result["rows"] == []
    assert result["reasons"] == ["no_data_recorded"]


def test_owners_are_attached_to_the_combined_rows(app, db_session, make_org):
    from datetime import date

    from app.models.application_portfolio import ApplicationComponent
    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit

    org = make_org("set-owner")
    seed = _element(db_session, org.id, "Seed")
    dependant = _element(db_session, org.id, "Dependant")
    _link(db_session, org.id, seed, dependant)
    component = ApplicationComponent(name="Dependant", organization_id=org.id, archimate_element_id=dependant.id)
    db_session.add(component)
    unit = OrganizationUnit(name="Payments team", organization_id=org.id)
    db_session.add(unit)
    db_session.flush()
    db_session.add(
        ApplicationOwnership(
            application_id=component.id, organization_unit_id=unit.id, ownership_type="business_owner",
            start_date=date.today(), organization_id=org.id,
        )
    )
    db_session.commit()

    result = _as(app, org.id, lambda: IntelligenceQueryService.set_impact([seed.id]))

    row = result["rows"][0]
    assert row["owner"]["name"] == "Payments team"
