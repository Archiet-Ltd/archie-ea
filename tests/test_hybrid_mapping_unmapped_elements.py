"""get_unmapped_archimate_elements must run and scope to the caller's org.

Regression for a double ``WHERE`` clause: org_scope(prefix="uc2.",
keyword="WHERE") was appended after a subquery that already opened with
``WHERE uc2.archimate_element_id IS NOT NULL``, producing a Postgres syntax
error on every call. The broad except in the caller swallowed it and
returned ``[]`` silently -- the dashboard just showed no unmapped elements,
indistinguishable from a real empty state. Found via a failing CI browser
journey (double WHERE / syntax error at or near "WHERE").
"""

from app.main.routes_hybrid_mapping import get_unmapped_archimate_elements


def test_runs_without_a_sql_error_and_returns_a_real_unmapped_element(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement

    org = make_org("hybrid-mapping")
    element = ArchiMateElement(
        name="Unmapped Service",
        type="ApplicationService",
        layer="application",
        organization_id=org.id,
    )
    db_session.add(element)
    db_session.flush()

    with tenant_ctx(org.id):
        result = get_unmapped_archimate_elements()

    assert result != []
    assert any(row["id"] == element.id for row in result)


def test_an_element_already_linked_to_a_capability_is_excluded(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement
    from app.models.unified_capability import UnifiedCapability

    org = make_org("hybrid-mapping-mapped")
    element = ArchiMateElement(
        name="Mapped Component",
        type="ApplicationComponent",
        layer="application",
        organization_id=org.id,
    )
    db_session.add(element)
    db_session.flush()

    capability = UnifiedCapability(
        name="Capability using it",
        organization_id=org.id,
        archimate_element_id=element.id,
    )
    db_session.add(capability)
    db_session.flush()

    with tenant_ctx(org.id):
        result = get_unmapped_archimate_elements()

    assert all(row["id"] != element.id for row in result)
