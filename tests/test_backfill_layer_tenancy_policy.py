"""The orphan policy `flask backfill-layer-tenancy` enforces for every table.

Derive first from whatever provenance a row already carries. Purge a row
whose always-set link names a parent that is gone. What is left NULL is
assigned to the one active, non-default organisation when there is exactly
one; on any other database it stays NULL and is reported, never guessed,
with or without --org-id. Each table commits, or rolls back, on its own, so
one table's failure cannot undo another's work.

Fixtures db_session, make_org, app come from tests/conftest.py. There is no
shared "relax a NOT NULL constraint" fixture on this branch's base yet
(T-S4 adds one, on a branch not merged here), so a module-scoped fixture
below relaxes strategic_roadmap_items once for every test in this module,
the one other table it needs that for, the same way
tests/test_backfill_layer_tenancy_roadmap_tasks.py does inline for
roadmap_tasks. None of these tests need a user: the new derivation this
module exercises reads an initiative's organisation, not a creating user's.
"""

from __future__ import annotations

import uuid

import click
import pytest
from sqlalchemy import text


def _make_inactive_org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:10]
    org = Organization(name=f"Test {label} {suffix}", slug=f"test-{label}-{suffix}", is_active=False)
    db_session.add(org)
    db_session.flush()
    return org


def _make_default_org(db_session, label):
    """One "default" organisation. slug is unique, so this may run once per
    test (each test's db_session rolls the row back at teardown)."""
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:10]
    org = Organization(name=f"Test {label} {suffix}", slug="default")
    db_session.add(org)
    db_session.flush()
    return org


_ROADMAP_MEMBERSHIP_TRIGGER = "trg_transformation_membership"


@pytest.fixture(scope="module", autouse=True)
def _relax_strategic_roadmap_items_for_the_module(app):
    """Give every test in this module a table shaped like it predates
    TenantMixin: nullable organization_id, and the live ownership-consistency
    trigger disabled for the one table this module inserts NULL-org, linked
    rows into.

    That trigger checks a linked initiative's organisation against the row's
    own -- a real invariant for every row the product writes today, but one
    a still-unbackfilled historical row cannot satisfy by definition. Both
    changes run here, once, before any test's db_session opens its own
    transaction, and are undone here, once, after every test's rollback has
    already released it -- not per insert: an ALTER TABLE between a test's
    own INSERT and that same test's later reads would need the ACCESS
    EXCLUSIVE lock db_session's still-open transaction already holds from
    that INSERT, and block on it until the test itself finished. Committed
    on a connection of its own, released immediately, for the same reason:
    db_session's transaction is never really committed until the whole test
    rolls back, so the lock an ALTER TABLE takes on that connection would
    still be held when repair_layer_tenancy reflects the table's columns on
    a second, independent connection a moment later -- same process, same
    thread, so it would block against itself.
    """
    from app import db

    with app.app_context():
        with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(
                text(
                    "ALTER TABLE strategic_roadmap_items ALTER COLUMN organization_id DROP NOT NULL"
                )
            )
            conn.execute(
                text(
                    f"ALTER TABLE strategic_roadmap_items DISABLE TRIGGER {_ROADMAP_MEMBERSHIP_TRIGGER}"
                )
            )
    yield
    with app.app_context():
        with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(
                text(
                    f"ALTER TABLE strategic_roadmap_items ENABLE TRIGGER {_ROADMAP_MEMBERSHIP_TRIGGER}"
                )
            )
            conn.execute(
                text("ALTER TABLE strategic_roadmap_items ALTER COLUMN organization_id SET NOT NULL")
            )


def _is_nullable(db_session, table, column="organization_id"):
    return db_session.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).scalar() == "YES"


def _insert_initiative(db_session, org_id, title="initiative"):
    suffix = uuid.uuid4().hex[:8]
    return db_session.execute(
        text(
            "INSERT INTO strategic_initiatives (name, organization_id) "
            "VALUES (:name, :org) RETURNING id"
        ),
        {"name": f"{title} {suffix}", "org": org_id},
    ).scalar()


def _insert_roadmap_item(db_session, *, initiative_id=None, title="item"):
    """Insert a row carrying no tenant provenance yet -- the shape a row that
    predates TenantMixin takes, which is exactly what every test in this
    module needs to hand to the backfill under test. The module fixture
    above has already relaxed the column and disabled the one trigger a
    NULL-org, initiative-linked row would otherwise fail for the whole
    module, so this is a plain insert.
    """
    suffix = uuid.uuid4().hex[:8]
    return db_session.execute(
        text(
            "INSERT INTO strategic_roadmap_items (title, organization_id, initiative_id) "
            "VALUES (:title, NULL, :initiative_id) RETURNING id"
        ),
        {"title": f"{title} {suffix}", "initiative_id": initiative_id},
    ).scalar()


def _roadmap_item_org(db_session, item_id):
    return db_session.execute(
        text("SELECT organization_id FROM strategic_roadmap_items WHERE id = :id"),
        {"id": item_id},
    ).scalar()


def test_multi_tenant_database_derives_from_initiative_and_defers_the_rest(db_session, make_org, app):
    """Two active, non-default organisations, plus one inactive and the
    default: the row with an initiative link derives that initiative's
    organisation; the row with none stays NULL, is counted as unresolved,
    and the run still exits clean -- NOT NULL is skipped for the table
    while it has one."""
    from app.commands.backfill_layer_tenancy import repair_layer_tenancy

    org_a, _org_b = make_org("mt-a"), make_org("mt-b")  # _org_b: makes the database genuinely multi-tenant
    _make_inactive_org(db_session, "mt-inactive")
    _make_default_org(db_session, "mt-default")
    initiative_id = _insert_initiative(db_session, org_a.id)

    linked_id = _insert_roadmap_item(db_session, initiative_id=initiative_id, title="linked")
    unlinked_id = _insert_roadmap_item(db_session, initiative_id=None, title="unlinked")
    db_session.flush()

    stats = repair_layer_tenancy()

    assert _roadmap_item_org(db_session, linked_id) == org_a.id
    assert _roadmap_item_org(db_session, unlinked_id) is None
    assert stats["unresolved"].get("strategic_roadmap_items") == 1
    assert stats["failed"] == {}
    assert _is_nullable(db_session, "strategic_roadmap_items") is True


def test_org_id_rejected_before_any_statement_on_a_multi_tenant_database(db_session, make_org, app):
    """The same shape of data as the derive-and-defer test: --org-id naming
    one of the two eligible organisations is still refused, and refused
    before any statement runs -- row counts (here, organization_id values)
    are unchanged."""
    from app.commands.backfill_layer_tenancy import repair_layer_tenancy

    org_a, _org_b = make_org("rej-a"), make_org("rej-b")  # _org_b: makes the database genuinely multi-tenant
    _make_inactive_org(db_session, "rej-inactive")
    _make_default_org(db_session, "rej-default")
    initiative_id = _insert_initiative(db_session, org_a.id)

    linked_id = _insert_roadmap_item(db_session, initiative_id=initiative_id, title="linked")
    unlinked_id = _insert_roadmap_item(db_session, initiative_id=None, title="unlinked")
    db_session.flush()

    with pytest.raises(click.ClickException):
        repair_layer_tenancy(org_id=org_a.id)

    assert _roadmap_item_org(db_session, linked_id) is None
    assert _roadmap_item_org(db_session, unlinked_id) is None


def test_single_active_organisation_receives_the_residual_rows(db_session, make_org, app):
    """One active, non-default organisation, plus the default and an
    inactive one: the row with no provenance is assigned to the active
    organisation, never to the default or the inactive one, and the column
    is hardened once nothing is left NULL."""
    from app.commands.backfill_layer_tenancy import repair_layer_tenancy

    org_active = make_org("single-active")
    _make_inactive_org(db_session, "single-inactive")
    _make_default_org(db_session, "single-default")

    orphan_id = _insert_roadmap_item(db_session, initiative_id=None, title="orphan")
    db_session.flush()

    stats = repair_layer_tenancy()

    assert _roadmap_item_org(db_session, orphan_id) == org_active.id
    assert "strategic_roadmap_items" not in stats["unresolved"]
    assert stats["failed"] == {}
    assert _is_nullable(db_session, "strategic_roadmap_items") is False


def test_purges_a_row_whose_always_set_link_names_a_gone_parent(db_session, make_org, monkeypatch):
    """A purge entry deletes a row whose link is set but names no row in the
    parent table; a row whose link is NULL is not purged -- it has no
    provenance, it is not "gone". Both rows already carry a real
    organization_id, isolating this from derivation/assignment entirely.
    """
    import app.commands.backfill_layer_tenancy as b

    org = make_org("purge")
    monkeypatch.setitem(
        b._PURGE_ORPHANS, "roadmap_tasks", ("unified_work_package_id", "unified_work_packages")
    )

    gone_parent_id = db_session.execute(
        text(
            "INSERT INTO roadmap_tasks "
            "(title, organization_id, archimate_element_id, created_by, unified_work_package_id) "
            "VALUES ('gone-parent task', :org, NULL, NULL, 999999999) RETURNING id"
        ),
        {"org": org.id},
    ).scalar()
    null_link_id = db_session.execute(
        text(
            "INSERT INTO roadmap_tasks "
            "(title, organization_id, archimate_element_id, created_by, unified_work_package_id) "
            "VALUES ('no-link task', :org, NULL, NULL, NULL) RETURNING id"
        ),
        {"org": org.id},
    ).scalar()
    db_session.flush()

    b.repair_layer_tenancy()

    remaining = {
        row[0]
        for row in db_session.execute(
            text("SELECT id FROM roadmap_tasks WHERE id = ANY(:ids)"),
            {"ids": [gone_parent_id, null_link_id]},
        ).fetchall()
    }
    assert gone_parent_id not in remaining
    assert null_link_id in remaining


def test_a_failing_table_rolls_back_alone_and_another_tables_work_persists(
    db_session, make_org, app, monkeypatch
):
    """One table's statement made to fail (an invalid derivation) rolls that
    table back and records it in "failed"; a different table's own,
    unrelated derivation still runs, still persists, and the run continues
    past the failure rather than stopping."""
    import app.commands.backfill_layer_tenancy as b

    org = make_org("iso")
    initiative_id = _insert_initiative(db_session, org.id)

    item_id = _insert_roadmap_item(db_session, initiative_id=initiative_id, title="survives")
    db_session.flush()

    # An undefined column is a plan-time error PostgreSQL raises regardless
    # of how many rows the table holds -- unlike a per-row runtime error
    # (e.g. division by zero), which an empty table would never evaluate,
    # making the "failure" silently vanish and the test prove nothing.
    monkeypatch.setitem(
        b._DERIVABLE_ORG,
        "vendor_product_capabilities",
        "UPDATE vendor_product_capabilities SET organization_id = this_column_does_not_exist",
    )

    stats = b.repair_layer_tenancy()

    assert stats["failed"].keys() == {"vendor_product_capabilities"}
    assert _roadmap_item_org(db_session, item_id) == org.id


def test_provenance_only_quarantines_options_analysis_and_stakeholder_inputs():
    import app.commands.backfill_layer_tenancy as b

    assert hasattr(b, "_PROVENANCE_ONLY")
    assert b._PROVENANCE_ONLY == {"options_analysis", "stakeholder_inputs"}
