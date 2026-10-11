"""T-003 / task 01 acceptance criteria 1, 2, 3, 6, 11, 12.

Reads the real database catalog (``pg_constraint`` / ``pg_indexes``) rather
than the model's Python metadata, per task 01's explicit instruction — a
Python-side assertion would pass even if the DDL never reached the database.
"""

from __future__ import annotations

import datetime as _dt

from app.extensions import db


def _make_element(db_session, org_id, name_hint, type_="ApplicationComponent"):
    from app.models import ArchiMateElement

    row = ArchiMateElement(
        name=f"E-{name_hint}", type=type_, layer="application", organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_relationship(db_session, org_id, source, target, type_):
    from app.models import ArchiMateRelationship

    row = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _insert_derived_row(db_session, org_id, source, target, **overrides):
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    params = dict(
        organization_id=org_id,
        source_element_id=source.id,
        target_element_id=target.id,
        derived_type="Association",
        rule_id="fallback:Access:Access",
        chain=[1, 2],
        chain_element_ids=[source.id, 999, target.id],
        depth=2,
        confidence="1.00",
        provenance="derivation",
        engine_version="1.0.0",
        computed_at=_dt.datetime.utcnow(),
        stale=False,
        stale_since=None,
        stale_reason=None,
    )
    params.update(overrides)
    row = DerivedRelationship(**params)
    db_session.add(row)
    db_session.flush()
    return row


# --- Acceptance item 1: full DA-1 schema, read from the catalog ------------


def test_table_carries_every_constraint_and_index_by_name(app, db_session):
    constraint_rows = db_session.execute(
        db.text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'archimate_derived_relationships'::regclass"
        )
    ).all()
    constraint_names = {r[0] for r in constraint_rows}
    for expected in (
        "uq_derived_rel",
        "ck_derived_depth",
        "ck_derived_conf",
        "ck_derived_stale",
        "ck_derived_chain_len",
    ):
        assert expected in constraint_names, f"missing constraint {expected}"

    index_rows = db_session.execute(
        db.text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename = 'archimate_derived_relationships'"
        )
    ).all()
    index_defs = {r[0]: r[1] for r in index_rows}
    for expected in ("ix_dr_src", "ix_dr_tgt", "ix_dr_stale", "ix_dr_chain"):
        assert expected in index_defs, f"missing index {expected}"

    for partial in ("ix_dr_src", "ix_dr_tgt", "ix_dr_chain"):
        assert "WHERE" in index_defs[partial] and "stale = false" in index_defs[
            partial
        ].lower(), f"{partial} must carry the partial WHERE stale = FALSE predicate"

    assert "USING gin" in index_defs["ix_dr_chain"]

    org_id_col = db_session.execute(
        db.text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'archimate_derived_relationships' "
            "AND column_name = 'organization_id'"
        )
    ).scalar_one()
    assert org_id_col == "NO"

    columns = {
        r[0]
        for r in db_session.execute(
            db.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'archimate_derived_relationships'"
            )
        ).all()
    }
    for expected_col in (
        "id",
        "organization_id",
        "source_element_id",
        "target_element_id",
        "derived_type",
        "rule_id",
        "chain",
        "chain_element_ids",
        "depth",
        "confidence",
        "provenance",
        "engine_version",
        "computed_at",
        "stale",
        "stale_since",
        "stale_reason",
    ):
        assert expected_col in columns, f"missing column {expected_col}"


# --- Acceptance item 2 / brief 15: table genuinely created via create_all --


def test_table_exists_after_create_all_via_real_app_factory(app):
    with app.app_context():
        exists = db.session.execute(
            db.text(
                "SELECT to_regclass('archimate_derived_relationships') IS NOT NULL"
            )
        ).scalar_one()
    assert exists is True


# --- Acceptance item 3 / brief item 2: chain carries provenance ------------


def test_persisted_row_carries_chain_rule_id_confidence_computed_at_depth(
    app, db_session, make_org
):
    org = make_org("dr-chain")
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")
    row = _insert_derived_row(db_session, org.id, a, b, chain=[10, 11], depth=2)

    assert row.chain == [10, 11]
    assert row.rule_id == "fallback:Access:Access"
    assert float(row.confidence) == 1.00
    assert row.computed_at is not None
    assert row.depth == 2

    # tenancy-ok: test query scoped by specific id
    array_len = db_session.execute(
        db.text("SELECT array_length(chain, 1) FROM archimate_derived_relationships WHERE id = :id"),
        {"id": row.id},
    ).scalar_one()
    assert array_len == row.depth


def test_chain_length_must_equal_depth_two_more_scenarios(app, db_session, make_org):
    org = make_org("dr-chain-2")
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")

    for depth, chain in ((3, [1, 2, 3]), (5, [1, 2, 3, 4, 5])):
        row = _insert_derived_row(
            db_session,
            org.id,
            a,
            b,
            rule_id=f"table:Serving:Serving:{depth}",
            chain=chain,
            depth=depth,
        )
        assert len(row.chain) == row.depth


# --- Acceptance item 11 / brief 11: only rule-derived facts ----------------


def test_confidence_and_provenance_pinned_by_constraint(app, db_session, make_org):
    org = make_org("dr-conf")
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")

    row = _insert_derived_row(db_session, org.id, a, b)
    assert float(row.confidence) == 1.00
    assert row.provenance == "derivation"


def test_confidence_out_of_range_raises(app, db_session, make_org):
    import pytest
    from sqlalchemy.exc import IntegrityError

    org = make_org("dr-conf-bad")
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")

    with pytest.raises(IntegrityError):
        _insert_derived_row(db_session, org.id, a, b, confidence="0")
    db_session.rollback()

    with pytest.raises(IntegrityError):
        _insert_derived_row(db_session, org.id, a, b, confidence="1.50")
    db_session.rollback()


# --- Acceptance item 12 / brief 12: tenancy ---------------------------------


def test_cross_tenant_read_returns_no_other_tenants_rows(app, db_session, make_org, tenant_ctx):
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    org_a = make_org("dr-tenant-a")
    org_b = make_org("dr-tenant-b")
    a1 = _make_element(db_session, org_a.id, "a1")
    a2 = _make_element(db_session, org_a.id, "a2")
    b1 = _make_element(db_session, org_b.id, "b1")
    b2 = _make_element(db_session, org_b.id, "b2")
    db_session.commit()

    with tenant_ctx(org_a.id):
        _insert_derived_row(db_session, org_a.id, a1, a2, rule_id="fallback:A:A")
        db_session.commit()

    with tenant_ctx(org_b.id):
        _insert_derived_row(db_session, org_b.id, b1, b2, rule_id="fallback:B:B")
        db_session.commit()

    with tenant_ctx(org_b.id):
        rows = db.session.execute(db.select(DerivedRelationship)).scalars().all()
        assert all(r.organization_id == org_b.id for r in rows)
        assert len(rows) == 1
