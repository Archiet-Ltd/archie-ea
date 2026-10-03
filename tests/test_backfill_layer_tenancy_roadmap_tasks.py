"""``flask backfill-layer-tenancy`` deriving roadmap_tasks.organization_id.

roadmap_tasks predates TenantMixin and carries no single foreign key to its
owning tenant. The canonical backfill command derives the organisation from
whichever provenance a row actually has -- the work package's creator, the
consolidation entry's application, or the creating user, in that order -- and
a row with none of those is left NULL and reported rather than handed to
whichever organisation happens to be picked for every other orphaned table.
The creating user is checked last because that user's own organisation can
change after the task was created; the other two links do not move the same
way.

There is no existing test of ``repair_layer_tenancy``/``_DERIVABLE_ORG`` on
this branch's base to extend, so this is a new module.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

# No module-level `usefixtures("db_session")`: test_backfill_leaves_unprovenanced_
# roadmap_task_null_with_two_orgs below calls repair_layer_tenancy() twice and
# needs real commits between the two calls, so it deliberately does not take
# db_session. Every other test in this module requests it directly instead.


def _make_user(db_session, org_id, label):
    """Same three-line construction tests/test_arb_ea_tenant_isolation.py uses.

    There is no shared user-factory fixture on this branch's base; each module
    that needs one still builds its own inline.
    """
    from app.models.user import User
    import uuid

    suffix = uuid.uuid4().hex[:10]
    user = User(
        email=f"{label}-{suffix}@example.com",
        first_name="Test",
        last_name=label,
        organization_id=org_id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _relax_not_null(app):
    """Allow NULL organization_id inserts, committed on a connection of its own.

    There is no shared "relax not null" fixture on this branch's base, and it
    cannot be done on db_session's own connection: that fixture's transaction
    is never really committed until the whole test rolls back, so the ACCESS
    EXCLUSIVE lock an ALTER TABLE takes would still be held when
    repair_layer_tenancy reflects roadmap_tasks's columns on a second,
    independent connection a moment later (NullPool -- schema reflection is
    engine-bound, not session-bound, so it never reuses db_session's
    connection) -- same process, same thread, so it deadlocks against itself.
    Doing the ALTER on its own autocommitting connection commits and releases
    the lock immediately; the module-scoped fixture below puts the constraint
    back once every test here, and its own db_session rollback, has finished.
    """
    from app import db

    with app.app_context():
        with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(
                text("ALTER TABLE roadmap_tasks ALTER COLUMN organization_id DROP NOT NULL")
            )


@pytest.fixture(scope="module", autouse=True)
def _restore_roadmap_tasks_not_null(app):
    """Undo `_relax_not_null` after every test (and its db_session rollback,
    which discards the test rows) in this module has finished."""
    from app import db

    yield
    with app.app_context():
        with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(
                text("ALTER TABLE roadmap_tasks ALTER COLUMN organization_id SET NOT NULL")
            )


def _insert_roadmap_task(db_session, *, created_by=None, unified_work_package_id=None, title="task"):
    row_id = db_session.execute(
        text(
            """
            INSERT INTO roadmap_tasks
                (title, organization_id, archimate_element_id, created_by, unified_work_package_id)
            VALUES
                (:title, NULL, NULL, :created_by, :unified_work_package_id)
            RETURNING id
            """
        ),
        {"title": title, "created_by": created_by, "unified_work_package_id": unified_work_package_id},
    ).scalar()
    db_session.flush()
    return row_id


def _org_id_of(db_session, task_id):
    return db_session.execute(
        text("SELECT organization_id FROM roadmap_tasks WHERE id = :id"), {"id": task_id}
    ).scalar()


def test_backfill_derives_roadmap_task_org_from_created_by(db_session, make_org, app):
    from app.commands.backfill_layer_tenancy import repair_layer_tenancy

    org_a, org_b = make_org("bf-cb-a"), make_org("bf-cb-b")
    user_a = _make_user(db_session, org_a.id, "A")
    user_b = _make_user(db_session, org_b.id, "B")

    _relax_not_null(app)
    task_a_id = _insert_roadmap_task(db_session, created_by=user_a.id, title="A task")
    task_b_id = _insert_roadmap_task(db_session, created_by=user_b.id, title="B task")

    stats = repair_layer_tenancy()

    assert _org_id_of(db_session, task_a_id) == org_a.id
    assert _org_id_of(db_session, task_b_id) == org_b.id
    assert "roadmap_tasks" not in stats["unresolved"]


def test_backfill_derives_roadmap_task_org_from_work_package_creator(db_session, make_org, app):
    from app.commands.backfill_layer_tenancy import repair_layer_tenancy
    from app.models.unified_work_package import UnifiedWorkPackage

    org_a, org_b = make_org("bf-wp-a"), make_org("bf-wp-b")
    _make_user(db_session, org_a.id, "A")  # a distractor in the other org
    user_b = _make_user(db_session, org_b.id, "B")

    wp = UnifiedWorkPackage(
        name="Migrate ERP",
        business_capability="Finance",
        created_by=user_b.id,
    )
    db_session.add(wp)
    db_session.flush()

    _relax_not_null(app)
    task_id = _insert_roadmap_task(
        db_session, created_by=None, unified_work_package_id=wp.id, title="WP-derived task"
    )

    stats = repair_layer_tenancy()

    assert _org_id_of(db_session, task_id) == org_b.id
    assert "roadmap_tasks" not in stats["unresolved"]


def test_backfill_prefers_work_package_provenance_over_moved_creator(db_session, make_org, app):
    """A task's own creator can be moved to another organisation after the
    task was created -- the admin route that reassigns a removed user's
    account does exactly this. The work package the task belongs to was
    created by a different, unmoved user, so the per-object provenance
    statement must run before the creating-user statement, or the moved
    user's *current* organisation would win instead of the one the task was
    actually created in.
    """
    from app.commands.backfill_layer_tenancy import repair_layer_tenancy
    from app.models.unified_work_package import UnifiedWorkPackage

    org_a, org_b = make_org("bf-mv-a"), make_org("bf-mv-b")
    wp_creator = _make_user(db_session, org_a.id, "WPC")  # stays in org A
    task_creator = _make_user(db_session, org_a.id, "TC")  # created the task in org A

    wp = UnifiedWorkPackage(
        name="Migrate CRM",
        business_capability="Sales",
        created_by=wp_creator.id,
    )
    db_session.add(wp)
    db_session.flush()

    _relax_not_null(app)
    task_id = _insert_roadmap_task(
        db_session, created_by=task_creator.id, unified_work_package_id=wp.id, title="moved-creator task"
    )

    # the task's own creator is later moved to another organisation
    task_creator.organization_id = org_b.id
    db_session.flush()

    stats = repair_layer_tenancy()

    assert _org_id_of(db_session, task_id) == org_a.id
    assert "roadmap_tasks" not in stats["unresolved"]


def test_backfill_derives_roadmap_task_org_from_consolidation_entry(db_session, make_org, app):
    from app.commands.backfill_layer_tenancy import repair_layer_tenancy
    from app.models.application_component_fast import ApplicationComponent
    from app.models.consolidation_list import ConsolidationListEntry

    org_a, org_b = make_org("bf-ce-a"), make_org("bf-ce-b")
    application = ApplicationComponent(name="Consolidation-linked app", organization_id=org_b.id)
    db_session.add(application)
    db_session.flush()

    _relax_not_null(app)
    task_id = _insert_roadmap_task(
        db_session, created_by=None, unified_work_package_id=None, title="consolidation-derived task"
    )

    entry = ConsolidationListEntry(application_id=application.id, roadmap_item_id=task_id)
    db_session.add(entry)
    db_session.flush()

    stats = repair_layer_tenancy()

    assert _org_id_of(db_session, task_id) == org_b.id
    assert "roadmap_tasks" not in stats["unresolved"]
    # org_a is unused by the derivation itself; it only establishes that a
    # second organisation exists so a wrong-tenant assignment would show up.
    assert org_a.id != org_b.id


def test_backfill_leaves_unprovenanced_roadmap_task_null_with_two_orgs(app):
    """Calls repair_layer_tenancy() twice, so this cannot use the db_session
    fixture. db_session's transaction is never really committed, and
    repair_layer_tenancy hardens (SET NOT NULL on) every other, unrelated,
    already-orphan-free tenant table it visits along the way -- on the first
    call that lock stays held (uncommitted) until the whole test rolls back,
    so the second call's own reflection of that same table deadlocks against
    it, the same shape of self-conflict `_relax_not_null` avoids for
    roadmap_tasks specifically. This test commits for real instead and cleans
    up explicitly.

    Positive controls: two provenanced rows (one per organisation) sit in the
    same pass as the unprovenanced one, so the assertions prove the run
    derives what it can and only leaves the truly unprovenanced row NULL --
    not that nothing in this run happens to be assigned at all.
    """
    from app import db
    from app.commands.backfill_layer_tenancy import repair_layer_tenancy
    from app.models.organization import Organization
    from app.models.user import User
    import uuid

    with app.app_context():
        suffix = uuid.uuid4().hex[:10]
        org_a = Organization(name=f"Test bf-np-a {suffix}", slug=f"test-bf-np-a-{suffix}")
        org_b = Organization(name=f"Test bf-np-b {suffix}", slug=f"test-bf-np-b-{suffix}")
        db.session.add_all([org_a, org_b])
        db.session.commit()

        user_a = User(
            email=f"bf-np-a-{suffix}@example.com",
            first_name="Test",
            last_name="A",
            organization_id=org_a.id,
        )
        user_b = User(
            email=f"bf-np-b-{suffix}@example.com",
            first_name="Test",
            last_name="B",
            organization_id=org_b.id,
        )
        db.session.add_all([user_a, user_b])
        db.session.commit()

        _relax_not_null(app)
        orphan_task_id = db.session.execute(
            text(
                """
                INSERT INTO roadmap_tasks
                    (title, organization_id, archimate_element_id, created_by, unified_work_package_id)
                VALUES
                    ('Orphan task', NULL, NULL, NULL, NULL)
                RETURNING id
                """
            )
        ).scalar()
        provenanced_a_id = db.session.execute(
            text(
                """
                INSERT INTO roadmap_tasks
                    (title, organization_id, archimate_element_id, created_by, unified_work_package_id)
                VALUES
                    ('Provenanced A task', NULL, NULL, :created_by, NULL)
                RETURNING id
                """
            ),
            {"created_by": user_a.id},
        ).scalar()
        provenanced_b_id = db.session.execute(
            text(
                """
                INSERT INTO roadmap_tasks
                    (title, organization_id, archimate_element_id, created_by, unified_work_package_id)
                VALUES
                    ('Provenanced B task', NULL, NULL, :created_by, NULL)
                RETURNING id
                """
            ),
            {"created_by": user_b.id},
        ).scalar()
        db.session.commit()
        task_ids = [orphan_task_id, provenanced_a_id, provenanced_b_id]

        try:
            stats_no_org = repair_layer_tenancy()

            def _org_of(task_id):
                return db.session.execute(
                    text("SELECT organization_id FROM roadmap_tasks WHERE id = :id"), {"id": task_id}
                ).scalar()

            assert _org_of(orphan_task_id) is None
            assert _org_of(provenanced_a_id) == org_a.id
            assert _org_of(provenanced_b_id) == org_b.id
            assert stats_no_org["unresolved"] == {"roadmap_tasks": 1}

            stats_with_org = repair_layer_tenancy(org_id=org_a.id)

            # the orphan is still left NULL, never handed to the operator-
            # chosen organisation, and the already-provenanced rows are
            # untouched (their statements fill NULLs only, and they are no
            # longer NULL)
            assert _org_of(orphan_task_id) is None
            assert _org_of(provenanced_a_id) == org_a.id
            assert _org_of(provenanced_b_id) == org_b.id
            assert stats_with_org["unresolved"] == {"roadmap_tasks": 1}
        finally:
            db.session.rollback()
            db.session.execute(
                text("DELETE FROM roadmap_tasks WHERE id = ANY(:ids)"), {"ids": task_ids}
            )
            db.session.execute(
                text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": user_a.id, "b": user_b.id}
            )
            db.session.execute(
                text("DELETE FROM organizations WHERE id IN (:a, :b)"),
                {"a": org_a.id, "b": org_b.id},
            )
            db.session.commit()


def test_backfill_dry_run_changes_nothing(app):
    """Also proves the dry-run "no provenance" count is accurate: with two
    derivable rows and one truly unprovenanced row in the same pass,
    ``unresolved`` must count only the unprovenanced one, not all three --
    which is what it did before the derivation statements ran in dry-run
    too.

    Does not use the db_session fixture: repair_layer_tenancy(dry_run=True)
    ends with ``db.session.rollback()`` on the same connection db_session
    hands out, so it would discard this test's own INSERTs along with the
    dry run's own changes -- a row rolled back away and a row genuinely
    left unassigned both read back as ``organization_id IS NULL``, so the
    three is-None assertions below would pass either way, proving nothing.
    Commits the three rows for real instead, the same pattern
    test_backfill_leaves_unprovenanced_roadmap_task_null_with_two_orgs
    above uses, so a row count taken straight after the dry run can tell
    "left alone" from "no longer there".
    """
    from app import db
    from app.commands.backfill_layer_tenancy import repair_layer_tenancy
    from app.models.organization import Organization
    from app.models.user import User
    import uuid

    with app.app_context():
        suffix = uuid.uuid4().hex[:10]
        org_a = Organization(name=f"Test bf-dr-a {suffix}", slug=f"test-bf-dr-a-{suffix}")
        org_b = Organization(name=f"Test bf-dr-b {suffix}", slug=f"test-bf-dr-b-{suffix}")
        db.session.add_all([org_a, org_b])
        db.session.commit()

        user_a = User(
            email=f"bf-dr-a-{suffix}@example.com",
            first_name="Test",
            last_name="A",
            organization_id=org_a.id,
        )
        user_b = User(
            email=f"bf-dr-b-{suffix}@example.com",
            first_name="Test",
            last_name="B",
            organization_id=org_b.id,
        )
        db.session.add_all([user_a, user_b])
        db.session.commit()

        _relax_not_null(app)
        task_a_id = db.session.execute(
            text(
                """
                INSERT INTO roadmap_tasks
                    (title, organization_id, archimate_element_id, created_by, unified_work_package_id)
                VALUES
                    ('A task', NULL, NULL, :created_by, NULL)
                RETURNING id
                """
            ),
            {"created_by": user_a.id},
        ).scalar()
        task_b_id = db.session.execute(
            text(
                """
                INSERT INTO roadmap_tasks
                    (title, organization_id, archimate_element_id, created_by, unified_work_package_id)
                VALUES
                    ('B task', NULL, NULL, :created_by, NULL)
                RETURNING id
                """
            ),
            {"created_by": user_b.id},
        ).scalar()
        orphan_task_id = db.session.execute(
            text(
                """
                INSERT INTO roadmap_tasks
                    (title, organization_id, archimate_element_id, created_by, unified_work_package_id)
                VALUES
                    ('orphan task', NULL, NULL, NULL, NULL)
                RETURNING id
                """
            )
        ).scalar()
        db.session.commit()
        task_ids = [task_a_id, task_b_id, orphan_task_id]

        try:
            stats = repair_layer_tenancy(dry_run=True)

            row_count = db.session.execute(
                text("SELECT count(*) FROM roadmap_tasks WHERE id = ANY(:ids)"), {"ids": task_ids}
            ).scalar()
            assert row_count == 3

            def _org_of(task_id):
                return db.session.execute(
                    text("SELECT organization_id FROM roadmap_tasks WHERE id = :id"), {"id": task_id}
                ).scalar()

            assert _org_of(task_a_id) is None
            assert _org_of(task_b_id) is None
            assert _org_of(orphan_task_id) is None
            assert stats["unresolved"] == {"roadmap_tasks": 1}
        finally:
            db.session.rollback()
            db.session.execute(
                text("DELETE FROM roadmap_tasks WHERE id = ANY(:ids)"), {"ids": task_ids}
            )
            db.session.execute(
                text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": user_a.id, "b": user_b.id}
            )
            db.session.execute(
                text("DELETE FROM organizations WHERE id IN (:a, :b)"),
                {"a": org_a.id, "b": org_b.id},
            )
            db.session.commit()
