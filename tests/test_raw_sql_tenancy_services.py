"""Cross-tenant isolation for the raw SQL in app/services.

Multi-tenancy is enforced by the ``do_orm_execute`` listener in
``app/middleware/tenant_isolation.py``, which rewrites ORM statements. A
``db.text(...)`` statement is not an ORM statement, so none of that applies to
it: every query here reached PostgreSQL exactly as written and returned every
organisation's rows.

Several of these services assemble the context handed to the AI assistant. An
unscoped read there does not merely leak a row — the model restates it to the
user in fluent prose as though it were their own portfolio. That is why the
assertions below are about *absence of the other tenant's data*, not just about
counts.

Written against the shared fixtures in tests/conftest.py: ``db_session`` runs
each test inside a transaction that is always rolled back, so nothing lands in
the shared test database.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def two_orgs(db_session, make_org):
    """Two organisations, each with an application, two linked ArchiMate
    elements and a relationship between them.

    Returns a dict of ``{"a": {...}, "b": {...}}`` keyed by organisation.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    built = {}
    for key, label in (("a", "alpha"), ("b", "beta")):
        org = make_org(label)

        src = ArchiMateElement(
            name=f"{label}-src", type="ApplicationComponent", layer="application",
            organization_id=org.id,
        )
        tgt = ArchiMateElement(
            name=f"{label}-tgt", type="ApplicationService", layer="application",
            organization_id=org.id,
        )
        db_session.add_all([src, tgt])
        db_session.flush()

        rel = ArchiMateRelationship(
            type="Serving", source_id=src.id, target_id=tgt.id,
            organization_id=org.id,
        )
        app_row = ApplicationComponent(
            name=f"{label}-app",
            technology_stack="Cobol",
            # Not annual_cost: that column exists in the deployed database but is
            # not mapped by ApplicationComponent, so create_all() does not build
            # it and it is absent from the test schema.
            maintenance_cost=1000,
            number_of_integrations=3,
            user_count=50,
            archimate_element_id=src.id,
            organization_id=org.id,
        )
        db_session.add_all([rel, app_row])
        db_session.flush()

        built[key] = {
            "org": org, "src": src, "tgt": tgt, "rel": rel, "app": app_row,
        }
    return built


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------

def test_org_scope_is_a_no_op_without_a_tenant(app):
    """CLI, scheduler and importer paths must keep working.

    The ORM listener is itself a no-op when g.current_org_id is None. A raw-SQL
    helper that instead emitted ``organization_id = NULL`` would silently return
    zero rows to every CLI command, which is a worse failure than the leak.
    """
    from app.utils.tenant_sql import org_scope

    with app.app_context():
        assert org_scope() == ("", {})


def test_org_scope_survives_having_no_application_context_at_all():
    """flask.g raises RuntimeError, not AttributeError, when unbound — so a
    getattr default does not protect against it. Importers call this at module
    scope."""
    from app.utils.tenant_sql import org_scope

    assert org_scope() == ("", {})


def test_org_scope_emits_the_predicate_and_honours_an_explicit_org(app):
    from app.utils.tenant_sql import org_scope

    with app.app_context():
        clause, params = org_scope(prefix="ae.", org_id=42)
    assert "ae.organization_id = :org_id" in clause
    assert params == {"org_id": 42}


# ---------------------------------------------------------------------------
# Whole-table reads: no key at all, so nothing but the predicate scopes them
# ---------------------------------------------------------------------------

def test_traceability_graph_excludes_another_orgs_elements(two_orgs, tenant_ctx):
    from app.services.traceability_graph_service import build_traceability_graph

    a, b = two_orgs["a"], two_orgs["b"]
    with tenant_ctx(a["org"].id):
        graph = build_traceability_graph()

    assert [b["src"].id, b["tgt"].id] not in graph["edges"]
    assert b["src"].id not in graph["elements"]
    assert b["tgt"].id not in graph["elements"]
    assert a["src"].id in graph["elements"], "own org's elements must still be returned"


def test_traceability_graph_accepts_an_explicit_org_for_system_callers(two_orgs):
    """No request context — the CLI case. Passing the org must scope it anyway."""
    from app.services.traceability_graph_service import build_traceability_graph

    a, b = two_orgs["a"], two_orgs["b"]
    graph = build_traceability_graph(organization_id=a["org"].id)

    assert a["src"].id in graph["elements"]
    assert b["src"].id not in graph["elements"]


def test_dependency_graph_excludes_another_orgs_relationships(two_orgs, tenant_ctx):
    from app.services.dependency_visualization_service import (
        DependencyVisualizationService,
    )

    a, b = two_orgs["a"], two_orgs["b"]
    with tenant_ctx(a["org"].id):
        rels = DependencyVisualizationService()._get_archimate_relationships()

    pairs = {(r["source_element_id"], r["target_element_id"]) for r in rels}
    assert (b["src"].id, b["tgt"].id) not in pairs
    assert (a["src"].id, a["tgt"].id) in pairs


# ---------------------------------------------------------------------------
# The AI assistant's context. A leak here is restated to the user as fact.
# ---------------------------------------------------------------------------

def test_portfolio_complexity_does_not_count_another_orgs_applications(
    two_orgs, tenant_ctx, db_session
):
    from app.services.ai_chat_extensions.advanced_analytics_service import (
        AdvancedAnalyticsService,
    )
    from app import db

    svc = AdvancedAnalyticsService()
    with tenant_ctx(two_orgs["a"]["org"].id):
        scoped = svc._analyze_portfolio_complexity()
        scoped_rows = db.session.execute(db.text(
            "SELECT COUNT(*) FROM application_components WHERE organization_id = :o"
        ), {"o": two_orgs["a"]["org"].id}).scalar()

    counted = sum(
        d["count"] for d in scoped["complexity_distribution"].values()
    )
    assert counted == scoped_rows, (
        "the complexity distribution counted rows outside the tenant"
    )


def test_technology_search_does_not_return_another_orgs_applications(
    two_orgs, tenant_ctx
):
    from app.services.ai_chat_extensions.scenario_analysis_service import (
        ScenarioAnalysisService,
    )

    a, b = two_orgs["a"], two_orgs["b"]
    with tenant_ctx(a["org"].id):
        hits = ScenarioAnalysisService()._find_apps_by_technology("cobol")

    ids = {h["id"] for h in hits}
    assert a["app"].id in ids
    assert b["app"].id not in ids


def test_integration_impact_refuses_another_orgs_application_id(
    two_orgs, tenant_ctx
):
    """app_id arrives as an LLM tool argument, i.e. ultimately from chat text.
    Nothing upstream proves it belongs to the caller."""
    from app.services.ai_chat_extensions.scenario_analysis_service import (
        ScenarioAnalysisService,
    )

    a, b = two_orgs["a"], two_orgs["b"]
    svc = ScenarioAnalysisService()
    with tenant_ctx(a["org"].id):
        own = svc._assess_integration_impact(a["app"].id)
        foreign = svc._assess_integration_impact(b["app"].id)

    assert own["count"] == 1, "own org's ArchiMate relationship should be counted"
    assert foreign["count"] == 0, "another org's integration count leaked"


def test_user_impact_refuses_another_orgs_application_id(two_orgs, tenant_ctx):
    from app.services.ai_chat_extensions.scenario_analysis_service import (
        ScenarioAnalysisService,
    )

    a, b = two_orgs["a"], two_orgs["b"]
    svc = ScenarioAnalysisService()
    with tenant_ctx(a["org"].id):
        assert svc._assess_user_impact(a["app"].id)["count"] == 50
        assert svc._assess_user_impact(b["app"].id)["count"] == 0


def _link_element_to_a_new_solution(db_session, org_id, element_id):
    """Create a solution owned by ``org_id`` with ``element_id`` linked to it."""
    from app import db

    sol_id = db_session.execute(db.text(
        "INSERT INTO solutions (name, organization_id) VALUES (:n, :o) RETURNING id"
    ), {"n": "linked-solution", "o": org_id}).scalar()
    db_session.execute(db.text(
        "INSERT INTO solution_archimate_elements "
        "(solution_id, element_id, element_role, organization_id, created_at) "
        "VALUES (:s, :e, 'component', :o, NOW())"
    ), {"s": sol_id, "e": element_id, "o": org_id})
    db_session.flush()
    return sol_id


# These two are deliberately separate tests rather than two calls in one.
# _get_solution_entities rolls the session back when one of its three queries
# fails — which it does here, because vendor_product_details is absent from the
# schema create_all() builds — and that rollback discards the fixture rows a
# second call in the same test would need.

def test_solution_entities_returns_the_owning_orgs_element(
    two_orgs, tenant_ctx, db_session
):
    from app.services.enterprise_context_assembler import EnterpriseContextAssembler

    b = two_orgs["b"]
    sol_id = _link_element_to_a_new_solution(db_session, b["org"].id, b["src"].id)

    with tenant_ctx(b["org"].id):
        entities = EnterpriseContextAssembler()._get_solution_entities(sol_id)

    assert b["src"].id in {e["id"] for e in entities["archimate_elements"]}, (
        "the owner's own element must still reach the LLM context"
    )


def test_solution_entities_do_not_cross_orgs_through_the_junction_table(
    two_orgs, tenant_ctx, db_session
):
    """solution_archimate_elements has no organization_id, so the FK join proves
    nothing on its own — the scope has to come from archimate_elements."""
    b = two_orgs["b"]
    from app.services.enterprise_context_assembler import EnterpriseContextAssembler

    sol_id = _link_element_to_a_new_solution(db_session, b["org"].id, b["src"].id)

    # Org A asks about org B's solution id.
    with tenant_ctx(two_orgs["a"]["org"].id):
        entities = EnterpriseContextAssembler()._get_solution_entities(sol_id)

    leaked = {e["id"] for e in entities.get("archimate_elements", [])}
    assert b["src"].id not in leaked, (
        "another org's ArchiMate element reached the LLM context"
    )
