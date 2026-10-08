"""``archimate_elements.layer`` holds two casings, and Postgres ``=`` is case-sensitive.

Production counts at the time of writing::

    [Application] 47   [application] 52
    [Strategy]   274   [strategy]      4
    [Motivation]   8   [motivation]   13
    [Technology]   1   [technology]   24
    [business]    11   <- no capitalised "Business" row exists at all

The code compares both ways: roughly 70 sites use ``layer="business"`` and
~30 use ``layer="Business"``. So nine sites querying the capitalised form
returned zero rows *always*, and thirteen sites querying ``"strategy"`` saw
4 rows out of 274. Writers disagreed too — ``app/models/strategy_layer.py``
writes ``layer="Strategy"`` while most services read ``"strategy"``.

The fix has three halves, and all three are pinned here:

  * writes are canonicalised to lower case, so the split cannot reappear;
  * the column's SQLAlchemy type canonicalises *bind parameters* as well, so a
    comparison written either way matches a canonical row — that is what keeps
    the ~100 existing call sites working, including files this change does not
    touch;
  * a value loaded back from the database compares equal to either casing, so
    the in-memory comparisons (``element.layer == "Strategy"``) keep working.

Every assertion below is of the form "this returns *more* rows than before,
never fewer". Nothing here can be satisfied by a change that narrows a result
set, which is the failure mode a naive "just normalise the data" fix has.
"""

import pytest
from sqlalchemy import text


def _raw_insert(session, org_id, name, layer, type_="BusinessProcess"):
    """Insert a row the way the legacy code did — bypassing every normalisation hook.

    A Core-level INSERT fires no ORM validator, so this is the only way to
    reproduce a pre-existing row whose layer is stored in the wrong casing.
    """
    return session.execute(
        text(
            "INSERT INTO archimate_elements (name, type, layer, organization_id) "
            "VALUES (:n, :t, :l, :o) RETURNING id"
        ),
        {"n": name, "t": type_, "l": layer, "o": org_id},
    ).scalar()


def _stored_layer(session, element_id):
    """Read the layer straight out of the table, unmediated by the ORM type."""
    return session.execute(
        text("SELECT layer FROM archimate_elements WHERE id = :i"), {"i": element_id}
    ).scalar()


# ---------------------------------------------------------------------------
# 1. Write-time normalisation
# ---------------------------------------------------------------------------


def test_orm_write_stores_canonical_lowercase(db_session, make_org, tenant_ctx):
    """A capitalised write must land in the table as the canonical lower case.

    ``_link_strategy_archimate`` writes ``layer="Strategy"``. That is what put
    274 rows out of reach of the thirteen call sites querying ``"strategy"``.
    """
    from app.models.models import ArchiMateElement

    org = make_org("layer-write")
    with tenant_ctx(org.id):
        element = ArchiMateElement(name="Order to Cash", type="ValueStream", layer="Strategy")
        db_session.add(element)
        db_session.flush()

        assert _stored_layer(db_session, element.id) == "strategy", (
            "layer='Strategy' was stored verbatim; every reader using the "
            "lowercase convention will silently miss this row"
        )


def test_write_normalisation_tolerates_padding_and_none(db_session, make_org, tenant_ctx):
    """Whitespace and NULL must not create new variants of the same split."""
    from app.models.models import ArchiMateElement

    org = make_org("layer-write-edge")
    with tenant_ctx(org.id):
        padded = ArchiMateElement(name="Padded", type="BusinessProcess", layer="  Business \n")
        empty = ArchiMateElement(name="Unclassified", type="BusinessProcess", layer=None)
        db_session.add_all([padded, empty])
        db_session.flush()

        assert _stored_layer(db_session, padded.id) == "business"
        assert _stored_layer(db_session, empty.id) is None


# ---------------------------------------------------------------------------
# 2. Reads match regardless of the casing the caller wrote
# ---------------------------------------------------------------------------


def test_lowercase_query_finds_a_capitalised_write(db_session, make_org, tenant_ctx):
    """The 274-rows-invisible bug: write "Strategy", read "strategy"."""
    from app.models.models import ArchiMateElement

    org = make_org("layer-read-lower")
    with tenant_ctx(org.id):
        db_session.add(
            ArchiMateElement(name="Customer Management", type="Capability", layer="Strategy")
        )
        db_session.flush()

        found = db_session.query(ArchiMateElement).filter_by(layer="strategy").all()
        assert [e.name for e in found] == ["Customer Management"], (
            "a row written as layer='Strategy' is invisible to the lowercase "
            "convention used by most of the codebase"
        )


def test_capitalised_query_finds_a_legacy_lowercase_row(db_session, make_org, tenant_ctx):
    """The always-empty bug: nine sites query "Business", zero such rows exist."""
    from app.models.models import ArchiMateElement

    org = make_org("layer-read-upper")
    with tenant_ctx(org.id):
        legacy_id = _raw_insert(db_session, org.id, "Handle Claim", "business")

        found = db_session.query(ArchiMateElement).filter(
            ArchiMateElement.layer == "Business"
        ).all()
        assert legacy_id in [e.id for e in found], (
            "layer == 'Business' matched nothing; no capitalised Business row "
            "has ever existed, so these call sites are permanently empty"
        )


def test_in_clause_matches_either_casing(db_session, make_org, tenant_ctx):
    """``layer.in_(["Application", "Technology"])`` must see lowercase rows.

    Expanding IN parameters are bound through the column type one element at a
    time; if that is not true the capitalised list silently returns nothing.
    """
    from app.models.models import ArchiMateElement

    org = make_org("layer-in")
    with tenant_ctx(org.id):
        app_id = _raw_insert(db_session, org.id, "Billing", "application", "ApplicationComponent")
        tech_id = _raw_insert(db_session, org.id, "Kafka", "technology", "Node")
        _raw_insert(db_session, org.id, "Grow revenue", "motivation", "Goal")

        found = db_session.query(ArchiMateElement).filter(
            ArchiMateElement.layer.in_(["Application", "Technology"])
        ).all()
        assert sorted(e.id for e in found) == sorted([app_id, tech_id])


def test_func_lower_comparison_still_works(db_session, make_org, tenant_ctx):
    """Sites already defending themselves with ``lower(layer) == x`` must not regress."""
    from sqlalchemy import func

    from app.models.models import ArchiMateElement

    org = make_org("layer-func-lower")
    with tenant_ctx(org.id):
        db_session.add(ArchiMateElement(name="ESB", type="Node", layer="Technology"))
        db_session.flush()

        found = db_session.query(ArchiMateElement).filter(
            func.lower(ArchiMateElement.layer) == "technology"
        ).all()
        assert [e.name for e in found] == ["ESB"]


# ---------------------------------------------------------------------------
# 3. In-memory comparisons keep working for both conventions
# ---------------------------------------------------------------------------


def test_loaded_layer_compares_equal_to_either_casing(db_session, make_org, tenant_ctx):
    """``element.layer == "Strategy"`` must stay true after canonicalisation.

    tests/test_value_stream_archimate_sync.py already asserts exactly this, and
    ~30 call sites compare a loaded element's layer against the capitalised
    spelling. Normalising storage without this would break all of them.
    """
    from app.models.models import ArchiMateElement

    org = make_org("layer-inmemory")
    with tenant_ctx(org.id):
        raw_id = _raw_insert(db_session, org.id, "Legacy Goal", "motivation", "Goal")
        db_session.expunge_all()
        element = db_session.query(ArchiMateElement).filter_by(id=raw_id).one()

        assert element.layer == "Motivation"
        assert element.layer == "motivation"
        assert "Motivation" == element.layer
        assert not (element.layer != "Motivation")
        assert element.layer != "Business"


def test_layer_still_behaves_as_a_plain_string(db_session, make_org, tenant_ctx):
    """The value is still a ``str``: templates, JSON and dict keys are unaffected.

    Its hash is the hash of the canonical lower-case text, so dictionary and
    set behaviour is identical to a plain lower-case ``str`` — the comparison
    is more permissive, nothing else is.
    """
    import json

    from app.models.models import ArchiMateElement

    org = make_org("layer-strlike")
    with tenant_ctx(org.id):
        element = ArchiMateElement(name="Portal", type="ApplicationComponent", layer="Application")
        db_session.add(element)
        db_session.flush()
        db_session.expunge_all()
        element = db_session.query(ArchiMateElement).filter_by(name="Portal").one()

        assert isinstance(element.layer, str)
        assert json.dumps({"layer": element.layer}) == '{"layer": "application"}'
        assert {"application": 1}[element.layer] == 1
        assert element.layer in {"application"}
        assert element.layer.title() == "Application"


# ---------------------------------------------------------------------------
# 4. The backfill
# ---------------------------------------------------------------------------


def test_backfill_canonicalises_legacy_rows_and_is_idempotent(db_session, make_org):
    from app.commands.backfill_archimate_layer_casing import canonicalise_layer_rows

    org = make_org("layer-backfill")
    mixed = _raw_insert(db_session, org.id, "Mixed A", "Business")
    upper = _raw_insert(db_session, org.id, "Mixed B", "STRATEGY")
    clean = _raw_insert(db_session, org.id, "Clean", "technology")

    report = canonicalise_layer_rows(org_id=org.id)
    assert report["updated"] == 2, report

    assert _stored_layer(db_session, mixed) == "business"
    assert _stored_layer(db_session, upper) == "strategy"
    assert _stored_layer(db_session, clean) == "technology"

    rerun = canonicalise_layer_rows(org_id=org.id)
    assert rerun["updated"] == 0, "backfill is not idempotent"


def test_backfill_rewrites_implementation_migration_alias(db_session, make_org):
    """The backfill must close the old combined-name split as well as casing."""
    from app.commands.backfill_archimate_layer_casing import canonicalise_layer_rows

    org = make_org("layer-backfill-implementation")
    legacy = _raw_insert(db_session, org.id, "Legacy Impl", "implementation_migration")
    canonical = _raw_insert(db_session, org.id, "Canonical Impl", "implementation")

    dry_run = canonicalise_layer_rows(org_id=org.id, dry_run=True)
    assert dry_run["would_update"] == 1
    assert dry_run["by_value"] == {"implementation_migration": 1}

    report = canonicalise_layer_rows(org_id=org.id)
    assert report["updated"] == 1, report
    assert _stored_layer(db_session, legacy) == "implementation"
    assert _stored_layer(db_session, canonical) == "implementation"


def test_backfill_dry_run_changes_nothing(db_session, make_org):
    from app.commands.backfill_archimate_layer_casing import canonicalise_layer_rows

    org = make_org("layer-backfill-dry")
    row = _raw_insert(db_session, org.id, "Untouched", "Motivation")

    report = canonicalise_layer_rows(org_id=org.id, dry_run=True)
    assert report["updated"] == 0
    assert report["would_update"] == 1
    assert report["by_value"] == {"Motivation": 1}
    assert _stored_layer(db_session, row) == "Motivation", "dry-run wrote to the table"


def test_backfill_org_id_scopes_the_update(db_session, make_org):
    """``archimate_elements`` is tenant-scoped but the backfill is raw SQL.

    Raw SQL is not covered by the ORM tenant filter, so ``--org-id`` has to do
    the scoping itself or an operator repairing one tenant rewrites every other
    tenant's rows in the same statement.
    """
    from app.commands.backfill_archimate_layer_casing import canonicalise_layer_rows

    target = make_org("layer-target")
    bystander = make_org("layer-bystander")
    mine = _raw_insert(db_session, target.id, "Mine", "Business")
    theirs = _raw_insert(db_session, bystander.id, "Theirs", "Business")

    report = canonicalise_layer_rows(org_id=target.id)

    assert report["updated"] == 1
    assert _stored_layer(db_session, mine) == "business"
    assert _stored_layer(db_session, theirs) == "Business", (
        "the backfill rewrote a row belonging to another tenant"
    )


def test_backfill_sql_uses_literal_archimate_elements_statements():
    """Literal SQL keeps the constant table name out of string interpolation."""
    import inspect

    import app.commands.backfill_archimate_layer_casing as command

    source = inspect.getsource(command)

    assert '"SELECT count(*) FROM archimate_elements WHERE TRUE"' in source
    assert '"SELECT id, layer FROM archimate_elements "' in source
    assert '"UPDATE archimate_elements SET layer = :layer WHERE id = :id"' in source
    assert 'f"SELECT id, layer FROM {TABLE} "' not in source
    assert 'f"UPDATE {TABLE} SET layer = :layer "' not in source


def test_backfill_command_is_registered(app):
    """An unregistered CLI command cannot be run during a deployment."""
    assert "backfill-archimate-layer-casing" in app.cli.commands


@pytest.mark.parametrize("stored_layer", ["Motivation", "motivation"])
def test_backfill_command_dry_run_runs_end_to_end(app, db_session, make_org, stored_layer):
    """Exercise dirty and already-clean inputs without relying on leaked rows."""
    org = make_org("layer-cli-dry-run")
    row = _raw_insert(db_session, org.id, "CLI dry-run fixture", stored_layer)
    result = app.test_cli_runner().invoke(args=[
        "backfill-archimate-layer-casing", "--dry-run", "--org-id", str(org.id)
    ])
    assert result.exit_code == 0, result.output
    assert f"scanned 1 element(s) in organization {org.id}" in result.output
    if stored_layer == "Motivation":
        assert "dry-run: would update 1 row(s); no changes committed." in result.output
    else:
        assert "no non-canonical layer values" in result.output
    assert _stored_layer(db_session, row) == stored_layer, "CLI dry-run wrote to the table"
