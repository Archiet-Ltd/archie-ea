"""The Applications portfolio screen and the architecture store must agree.

Production finding (dogfooding entelim.org): the portfolio Applications
screen showed 0 applications for an organisation whose architecture store
showed 26 application elements. Root cause: the AI chat's two generic
"create any ArchiMate element from a (name, type, layer) tuple" tools --
``ToolExecutor._tool_create_archimate_element`` (app/modules/ai_chat/tools/
executor.py) and ``WorkbenchKernel.create_archimate_element_from_chat``
(app/modules/ai_chat/services/workbench_kernel.py) -- accepted
type="ApplicationComponent" like any other ArchiMate type and constructed
only a bare ArchiMateElement row. The Applications portfolio screen reads
``application_components`` directly
(app/modules/applications/routes/list_views.py::application_list, and its
``ApplicationComponent.query``); the architecture store (e.g. the
traceability matrix / the archimate_crud layer-elements endpoint) reads
``archimate_elements`` directly. An element created by either chat tool
existed in the second store and nowhere in the first.

The fix routes both tools through
``app.services.archimate_backbone.domain_model_for_element_type``: a
domain-backed type (ApplicationComponent today) gets its domain row created,
and ``sync_archimate_element`` mirrors it onto archimate_elements -- the same
path every other domain writer in this codebase already uses (see
``_tool_create_driver`` / ``_tool_create_goal`` in executor.py).

These tests prove, for a real organisation:
  1. Both chat tools, given type="ApplicationComponent", create a row in
     ``application_components`` (what the portfolio screen reads) and a
     matching row in ``archimate_elements`` (what the architecture store
     reads), referring to the same application.
  2. The portfolio query and an architecture-store-shaped query agree on
     count and membership after such a create -- no drift.
  3. The pre-fix shape (an ArchiMateElement with no application_components
     row) really does produce the production 0-vs-N disagreement: it is not
     a hypothetical, it is what a bare element looks like to these two
     readers.
  4. A non-domain-backed type (e.g. BusinessProcess, which has no portfolio
     table of its own) is unaffected -- the fix is scoped to
     domain-backed types, not a behaviour change for every element type.

Uses the shared fixtures in tests/conftest.py (db_session rolls back
automatically; app is session-scoped).
"""

from __future__ import annotations

import pytest


def _portfolio_applications(org_id):
    """What the Applications portfolio screen reads (see
    app/modules/applications/routes/list_views.py::application_list -- same
    query shape, modulo UI-only filters that do not matter for these tests:
    ``ApplicationComponent.query``, tenant-scoped automatically by
    TenantMixin's do_orm_execute filter)."""
    from app.models.application_portfolio import ApplicationComponent

    return ApplicationComponent.query.filter(
        ApplicationComponent.organization_id == org_id
    ).all()


def _architecture_store_applications(org_id):
    """What the architecture store reads for "applications" (see
    app/modules/architecture/routes/archimate_crud/routes.py::
    api_layer_elements's archimate_elements supplement, and the traceability
    matrix's ApplicationComponent pivot): ArchiMateElement rows of type
    ApplicationComponent, tenant-scoped the same way."""
    from app.models.archimate_core import ArchiMateElement

    return ArchiMateElement.query.filter(
        ArchiMateElement.organization_id == org_id,
        ArchiMateElement.type == "ApplicationComponent",
    ).all()


# --------------------------------------------------------------------------- #
# 1 & 2: both chat tools create a matching pair, and the two readers agree.   #
# --------------------------------------------------------------------------- #


def test_tool_executor_create_archimate_element_application_creates_portfolio_row(
    db_session, make_org, tenant_ctx
):
    """ToolExecutor._tool_create_archimate_element(type=ApplicationComponent)
    must create the application_components row the portfolio screen reads,
    not just an archimate_elements row."""
    from app.modules.ai_chat.tools.executor import ToolExecutor

    org = make_org("chat-app-dualwrite")
    ex = ToolExecutor(user_id=1)

    with tenant_ctx(org.id):
        result = ex._tool_create_archimate_element(
            {
                "name": "Chat-Created Application",
                "type": "ApplicationComponent",
                "layer": "application",
            }
        )
        assert result["success"] is True, result

        portfolio_rows = _portfolio_applications(org.id)
        store_rows = _architecture_store_applications(org.id)

        assert len(portfolio_rows) == 1, (
            "the application the agent created must appear in the "
            "Applications portfolio screen's own read path"
        )
        assert len(store_rows) == 1, (
            "the application must also appear in the architecture store, "
            "which is how the production disagreement (0 vs 26) was found"
        )

        app_row = portfolio_rows[0]
        element_row = store_rows[0]
        assert app_row.name == "Chat-Created Application"
        assert app_row.archimate_element_id == element_row.id, (
            "the portfolio row and the architecture element must reference "
            "each other -- same application, not two unrelated rows that "
            "happen to both exist"
        )
        assert result["result"]["id"] == element_row.id


def test_workbench_kernel_create_archimate_element_application_creates_portfolio_row(
    db_session, make_org, tenant_ctx
):
    """WorkbenchKernel.create_archimate_element_from_chat(
    type=ApplicationComponent) must create the same matching pair as the
    other AI chat tool -- this is the second of the two call sites the
    production bug was found in."""
    from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel

    org = make_org("workbench-app-dualwrite")
    kernel = WorkbenchKernel()

    with tenant_ctx(org.id):
        result = kernel.create_archimate_element_from_chat(
            workspace_id=0,
            element_name="Workbench-Created Application",
            element_type="ApplicationComponent",
            layer="Application",
        )
        assert result["success"] is True, result

        portfolio_rows = _portfolio_applications(org.id)
        store_rows = _architecture_store_applications(org.id)

        assert len(portfolio_rows) == 1
        assert len(store_rows) == 1
        assert portfolio_rows[0].name == "Workbench-Created Application"
        assert portfolio_rows[0].archimate_element_id == store_rows[0].id
        assert result["element_id"] == store_rows[0].id


# --------------------------------------------------------------------------- #
# 3: the pre-fix shape really does produce the production disagreement.      #
# --------------------------------------------------------------------------- #


def test_bare_archimate_element_reproduces_the_production_disagreement(
    db_session, make_org, tenant_ctx
):
    """Documents the actual mechanism of the reported bug.

    An ArchiMateElement of type ApplicationComponent with no matching
    application_components row (the exact shape a direct, un-routed
    ``ArchiMateElement(...)`` construction leaves behind) is invisible to the
    portfolio screen's own query while the architecture store's query finds
    it -- reproducing the production 0-vs-N disagreement on a small scale.
    This is the regression the fix (domain_model_for_element_type) closes
    for new writes; it is not itself fixed retroactively by this change (an
    operator backfill is a separate, data-remediation step), so this orphan
    shape is deliberately created directly here, bypassing the fixed tools.
    """
    from app.models.archimate_core import ArchiMateElement
    from app import db

    org = make_org("orphan-shape")

    with tenant_ctx(org.id):
        orphan = ArchiMateElement(
            name="Orphan Application Element",
            type="ApplicationComponent",
            layer="Application",
            organization_id=org.id,
        )
        db_session.add(orphan)
        db_session.commit()

        portfolio_rows = _portfolio_applications(org.id)
        store_rows = _architecture_store_applications(org.id)

        assert len(portfolio_rows) == 0, (
            "a bare architecture element must not spontaneously create a "
            "portfolio row -- this assertion pins the disagreement shape, "
            "it does not endorse it"
        )
        assert len(store_rows) == 1
        assert store_rows[0].name == "Orphan Application Element"


def test_fixed_tool_does_not_add_to_the_disagreement_alongside_an_old_orphan(
    db_session, make_org, tenant_ctx
):
    """In an organisation that already carries one pre-fix orphan element
    (simulating the 26 found in production), a new application created
    through the fixed chat tool must still agree between the two readers for
    itself -- the fix stops the drift from growing, even though it cannot
    retroactively repair rows that predate it."""
    from app.models.archimate_core import ArchiMateElement
    from app.modules.ai_chat.tools.executor import ToolExecutor

    org = make_org("orphan-plus-fixed")
    ex = ToolExecutor(user_id=1)

    with tenant_ctx(org.id):
        legacy_orphan = ArchiMateElement(
            name="Legacy Orphan Application",
            type="ApplicationComponent",
            layer="Application",
            organization_id=org.id,
        )
        db_session.add(legacy_orphan)
        db_session.commit()

        result = ex._tool_create_archimate_element(
            {
                "name": "New Application Via Chat",
                "type": "ApplicationComponent",
                "layer": "application",
            }
        )
        assert result["success"] is True, result

        portfolio_rows = _portfolio_applications(org.id)
        store_rows = _architecture_store_applications(org.id)

        # The legacy orphan still disagrees (pre-existing drift, untouched by
        # this fix) -- the store has it, the portfolio does not.
        assert len(store_rows) == 2
        assert len(portfolio_rows) == 1

        # But the NEW application, created through the fixed tool, is the one
        # portfolio row, and it is the one store row that is not the legacy
        # orphan -- the fix did not let the new write add a second untracked
        # orphan on top of the old one.
        new_portfolio_names = {row.name for row in portfolio_rows}
        assert new_portfolio_names == {"New Application Via Chat"}
        linked_store_ids = {row.archimate_element_id for row in portfolio_rows}
        new_store_row = next(
            row for row in store_rows if row.name == "New Application Via Chat"
        )
        assert new_store_row.id in linked_store_ids


# --------------------------------------------------------------------------- #
# 4: non-domain-backed types are unaffected by the fix.                      #
# --------------------------------------------------------------------------- #


def test_tool_executor_create_archimate_element_non_application_type_unaffected(
    db_session, make_org, tenant_ctx
):
    """A type with no portfolio table of its own (BusinessProcess) must still
    be created as a bare ArchiMateElement -- the fix is scoped to
    domain-backed types, not a behaviour change for every element type."""
    from app.models.archimate_core import ArchiMateElement
    from app.modules.ai_chat.tools.executor import ToolExecutor

    org = make_org("non-app-type-unaffected")
    ex = ToolExecutor(user_id=1)

    with tenant_ctx(org.id):
        result = ex._tool_create_archimate_element(
            {
                "name": "Chat-Created Process",
                "type": "BusinessProcess",
                "layer": "business",
            }
        )
        assert result["success"] is True, result

        elements = ArchiMateElement.query.filter(
            ArchiMateElement.organization_id == org.id,
            ArchiMateElement.type == "BusinessProcess",
        ).all()
        assert len(elements) == 1
        assert elements[0].name == "Chat-Created Process"

        # No application_components row was created as a side effect.
        assert _portfolio_applications(org.id) == []


def test_domain_model_for_element_type_is_scoped_to_application_component():
    """Pins the current, deliberately narrow scope of the register: only a
    type safely constructible from name/description/organization_id alone is
    listed. A future extension is a deliberate, one-row-at-a-time addition,
    not an accidental widening."""
    from app.services.archimate_backbone import domain_model_for_element_type
    from app.models.application_portfolio import ApplicationComponent

    assert domain_model_for_element_type("ApplicationComponent") is ApplicationComponent
    assert domain_model_for_element_type("BusinessProcess") is None
    assert domain_model_for_element_type("VendorContract") is None
    assert domain_model_for_element_type("Driver") is None
