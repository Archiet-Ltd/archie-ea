"""Support written the old way, before support became one relationship, still shows everywhere after the backfill.

Two real organisations: the backfill never joins one organisation's row to the other's elements.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest

from tests.test_capability_support_chat import CAPABILITY, _world
from tests.test_capability_support_writer import seed

pytestmark = pytest.mark.usefixtures("db_session")


def _old_row(db_session, org, app_row, capability_record, **facts):
    """A support row exactly as the older writers left it: no relationship."""
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as Row

    row = Row(organization_id=org.id, application_component_id=app_row.id,
              unified_capability_id=capability_record.id, **facts)
    db_session.add(row)
    db_session.flush()
    assert row.archimate_relationship_id is None
    return row


def _record_of(org, cap):
    from app.models.archimate_core import ArchiMateElement
    from app.services import capability_support as cs

    return cs.capability_for_element(org.id, ArchiMateElement.query.get(cap.archimate_element_id))


def test_an_old_row_shows_on_the_page_the_matrix_and_the_chat_after_the_backfill(
        db_session, make_org, tenant_ctx, client, login_as):
    from app.modules.genome.services.coverage_slice import build_coverage_slice
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor
    from app.services import capability_support as cs

    org, (app_row,), cap, user = _world(db_session, make_org)
    record = _record_of(org, cap)
    _old_row(db_session, org, app_row, record, support_level="partial", coverage_percentage=60, notes="kept")

    def chat_names():
        with tenant_ctx(org.id):
            out = ToolExecutor(user.id, persona="enterprise_architect").execute(
                ToolCall(id="t", name="find_applications_by_capability", arguments={"capability_name": CAPABILITY}))
        return [r["name"] for r in out.get("result", [])]

    login_as(client, user)
    counts = cs.backfill_unprojected_support()
    assert counts["before"] == 1 and counts["linked"] == 1 and counts["after"] == 0

    login_as(client, user)
    page = client.get(f"/applications/{app_row.id}").get_data(as_text=True)
    assert CAPABILITY in page
    cells = build_coverage_slice(org.id)["cells"]
    assert len(cells) == 1
    assert chat_names() == [app_row.name]
    rows = cs.support_rows(org.id)
    assert [(r["application_name"], r["capability_name"], r["support_level"], r["coverage_percentage"], r["notes"])
            for r in rows] == [(app_row.name, CAPABILITY, "partial", 60, "kept")]
    assert [c.name for c in cs.unsupported_capabilities(org.id)] == []


def test_running_the_backfill_again_changes_nothing(db_session, make_org):
    from app.models.archimate_core import ArchiMateRelationship
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as Row
    from app.services import capability_support as cs

    org = make_org()
    app_row, cap = seed(db_session, org)
    _old_row(db_session, org, app_row, _record_of(org, cap))
    first = cs.backfill_unprojected_support()
    snapshot = (Row.query.filter_by(organization_id=org.id).count(),
                ArchiMateRelationship.query.filter_by(organization_id=org.id).count())
    second = cs.backfill_unprojected_support()
    assert first["linked"] == 1
    assert second == {"before": 0, "linked": 0, "duplicates_removed": 0, "left": 0, "after": 0}
    assert snapshot == (Row.query.filter_by(organization_id=org.id).count(),
                        ArchiMateRelationship.query.filter_by(organization_id=org.id).count())


def test_two_old_rows_for_one_pair_become_one_and_keep_the_facts(db_session, make_org):
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as Row
    from app.services import capability_support as cs

    org = make_org()
    app_row, cap = seed(db_session, org)
    record = _record_of(org, cap)
    _old_row(db_session, org, app_row, record, support_level="primary")
    _old_row(db_session, org, app_row, record, notes="from the second", maturity_level=4)
    counts = cs.backfill_unprojected_support()
    assert counts["linked"] == 1 and counts["duplicates_removed"] == 1 and counts["after"] == 0
    rows = cs.support_rows(org.id)
    assert len(rows) == 1 and rows[0]["notes"] == "from the second" and rows[0]["support_level"] == "primary"
    assert Row.query.filter_by(organization_id=org.id).count() == 1


def test_an_old_row_on_a_shared_catalogue_capability_is_carried_by_the_organisations_own_record(db_session, make_org):
    from app.models.unified_capability import UnifiedCapability
    from app.models.application_portfolio import ApplicationComponent
    from app.services import capability_support as cs

    org = make_org()
    app_row = ApplicationComponent(name=f"Shared user {uuid.uuid4().hex[:6]}", organization_id=org.id)
    shared = UnifiedCapability(name=f"Shared catalogue {uuid.uuid4().hex[:6]}", level=1, organization_id=None,
                               scope="reference")
    db_session.add_all([app_row, shared])
    db_session.flush()
    _old_row(db_session, org, app_row, shared)
    counts = cs.backfill_unprojected_support(org.id)
    assert counts["linked"] == 1 and counts["after"] == 0
    assert [r["capability_name"] for r in cs.support_rows(org.id)] == [shared.name]


def test_one_organisations_old_rows_never_reach_the_other(db_session, make_org):
    from app.models.archimate_core import ArchiMateRelationship
    from app.services import capability_support as cs

    org_a, org_b = make_org("a"), make_org("b")
    app_a, cap_a = seed(db_session, org_a, "A")
    app_b, cap_b = seed(db_session, org_b, "B")
    _old_row(db_session, org_a, app_a, _record_of(org_a, cap_a))
    _old_row(db_session, org_b, app_b, _record_of(org_b, cap_b))
    # a row that pairs an application of A with a capability of B belongs to neither
    crossed = _old_row(db_session, org_a, app_a, _record_of(org_b, cap_b))
    counts = cs.backfill_unprojected_support()
    assert counts["linked"] == 2 and counts["after"] == 1
    assert [r["application_name"] for r in cs.support_rows(org_a.id)] == [app_a.name]
    assert [r["application_name"] for r in cs.support_rows(org_b.id)] == [app_b.name]
    assert crossed.archimate_relationship_id is None
    assert ArchiMateRelationship.query.filter(
        ArchiMateRelationship.source_id == app_a.archimate_element_id,
        ArchiMateRelationship.target_id == cap_b.archimate_element_id).count() == 0


def test_the_migration_links_old_rows_prints_the_counts_and_is_safe_to_run_twice(db_session, make_org, capsys):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.services import capability_support as cs

    org = make_org()
    app_row, cap = seed(db_session, org)
    _old_row(db_session, org, app_row, _record_of(org, cap))
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261114_capability_support_backfill.py"
    spec = importlib.util.spec_from_file_location("capability_support_backfill_revision", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for _ in range(2):
        with Operations.context(MigrationContext.configure(db_session.connection())):
            module.upgrade()
    printed = capsys.readouterr().out
    assert "before 1, linked 1, duplicates removed 0, left 0" in printed
    assert "before 0, linked 0, duplicates removed 0, left 0" in printed
    assert [r["application_name"] for r in cs.support_rows(org.id)] == [app_row.name]
