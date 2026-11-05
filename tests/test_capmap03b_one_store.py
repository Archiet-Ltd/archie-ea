"""capmap03b: the one store holds every mapping, once, and every screen and every writer agrees with it.

Each test seeds what production holds, not only what the unit seed writes: pairs held twice, mappings held
only in the frozen coverage table or the frozen legacy table, a shared capability, abacus-applied mappings.
"""
import importlib.util
import json
import re
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_capmap03_one_store import _catalog, _flat, _map_rows, _mapping_page, _owner, _seed  # noqa: E402

pytestmark = pytest.mark.usefixtures("db_session")
ROOT = Path(__file__).resolve().parents[1]


def _drop_unique(db_session):
    """Take the unique constraint off inside the test's rolled-back transaction, to seed what old data holds."""
    _sql(db_session, "ALTER TABLE unified_application_capability_mapping "
                     "DROP CONSTRAINT IF EXISTS uq_unified_app_cap_mapping_pair")


def _sql(db_session, sql, **params):
    from sqlalchemy import text

    return db_session.execute(text(sql), params)


def _dup(db_session, unified_id, app_id, **fields):
    """A second copy of a pair, written straight to the table the way old data holds it."""
    cols = dict(unified_capability_id=unified_id, application_component_id=app_id, support_level="partial",
                coverage_percentage=0, support_quality=3, relationship_type="enables", relationship_strength=3,
                dependency_level="medium", gap_status="unknown", is_active=True, is_strategic=False)
    cols.update(fields)
    _sql(db_session, "INSERT INTO unified_application_capability_mapping (%s) VALUES (%s)" % (
        ", ".join(cols), ", ".join(":" + k for k in cols)), **cols)


def _store_count(db_session, org):
    return _sql(db_session, """SELECT COUNT(*) FROM unified_application_capability_mapping m
                               JOIN unified_capabilities u ON u.id = m.unified_capability_id
                               WHERE u.organization_id = :o""", o=org.id).scalar()


def _legacy_only(db_session, org, bc, app):
    """A mapping in the frozen legacy table, written the way it was before the store existed (no mirror)."""
    _sql(db_session, """INSERT INTO application_capability_mapping
            (organization_id, application_component_id, business_capability_id, support_level, coverage_percentage,
             relationship_type, gap_status, is_active, specialization_type)
            VALUES (:o, :a, :b, 'full', 70, 'supports', 'unknown', TRUE, 'APPLICATION')""", o=org.id, a=app.id, b=bc.id)


def _coverage_only(db_session, bc, app):
    _sql(db_session, """INSERT INTO application_capability_coverage
            (application_component_id, capability_id, support_level, coverage_percentage, confidence_score,
             is_strategic, investment_priority) VALUES (:a, :b, 'partial', 55, 0.5, FALSE, 'medium')""",
         a=app.id, b=bc.id)


def _record(client, login_as, user, bc):
    login_as(client, user)
    return client.get("/enterprise/capabilities/%s/fact-sheet" % bc.id).get_data(as_text=True)


# --- D1: one entry per (capability, application) ---------------------------------------------------------

def test_store_returns_each_application_once_even_when_a_pair_is_held_twice(client, login_as, db_session, make_org):
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as M
    from app.modules.capabilities.services import capability_application_store as store

    org = make_org("cb-d1")
    user = _owner(db_session, org)
    bcs, unified, apps = _seed(db_session, org)
    _drop_unique(db_session)
    _dup(db_session, unified["cal"].id, apps["ledger"].id)
    listed = store.applications_by_capability(org.id)[unified["cal"].id]
    assert sorted(a["name"] for a in listed) == sorted(apps[k].name for k in ("ledger", "bench"))
    login_as(client, user)
    api = _map_rows(client)[bcs["cal"].name]
    assert api["applicationCoverage"] == 2
    assert [a["id"] for a in api["applications"]].count(apps["ledger"].id) == 1
    html, rows = _mapping_page(client)
    assert rows[bcs["cal"].name].count(apps["ledger"].name) == 1
    assert _record(client, login_as, user, bcs["cal"]).count(apps["ledger"].name) == 1


def test_the_store_keeps_one_row_per_pair(db_session, make_org):
    """A writer adding a pair the store holds changes nothing; the database refuses a second copy outright."""
    from sqlalchemy.exc import IntegrityError

    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as M

    org = make_org("cb-d1u")
    _, unified, apps = _seed(db_session, org)
    pair = dict(unified_capability_id=unified["cal"].id, application_component_id=apps["ledger"].id)
    db_session.add(M(support_level="minimal", **pair))
    db_session.add(M(support_level="minimal", **pair))
    db_session.flush()
    rows = M.query.filter_by(**pair).all()
    assert len(rows) == 1 and rows[0].support_level == "full", "the row already there is kept"
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            _dup(db_session, unified["cal"].id, apps["ledger"].id)


def test_migration_removes_only_the_extra_copies_and_keeps_the_fullest(db_session, make_org):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as M

    org_a, org_b = make_org("cb-m-a"), make_org("cb-m-b")
    _, ua, apps_a = _seed(db_session, org_a)
    _, ub, apps_b = _seed(db_session, org_b)
    _drop_unique(db_session)
    # org A: the pair (cal, ledger) held three times; the fullest copy carries notes and a maturity level.
    _dup(db_session, ua["cal"].id, apps_a["ledger"].id, support_level="partial")
    _dup(db_session, ua["cal"].id, apps_a["ledger"].id, support_level="full", coverage_percentage=90, notes="kept",
         maturity_level=3)
    before_b = _store_count(db_session, org_b)
    before_a = _store_count(db_session, org_a)
    spec = importlib.util.spec_from_file_location("mig", ROOT / "migrations/versions/20261105_one_mapping_per_pair.py")
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    with Operations.context(MigrationContext.configure(db_session.connection())):
        mig.upgrade()
    db_session.expire_all()
    rows = M.query.filter_by(unified_capability_id=ua["cal"].id, application_component_id=apps_a["ledger"].id).all()
    assert len(rows) == 1 and rows[0].notes == "kept"
    assert _store_count(db_session, org_a) == before_a - 2
    assert _store_count(db_session, org_b) == before_b
    # idempotent
    with Operations.context(MigrationContext.configure(db_session.connection())):
        mig.upgrade()
    assert _store_count(db_session, org_a) == before_a - 2


# --- D2: Remove removes the mapping from every screen -----------------------------------------------------

def test_remove_by_coverage_id_removes_the_mapping_everywhere(client, login_as, db_session, make_org):
    from app.models.business_capabilities import ApplicationCapabilityCoverage as Cov

    org = make_org("cb-d2")
    user = _owner(db_session, org)
    bcs, unified, apps = _seed(db_session, org)
    cov = Cov(application_component_id=apps["ledger"].id, capability_id=bcs["cal"].id, support_level="full")
    db_session.add(cov)
    db_session.flush()
    login_as(client, user)
    assert apps["ledger"].name in _record(client, login_as, user, bcs["cal"])
    reply = client.delete("/capability-map/api/mappings/%s" % cov.id)
    assert reply.status_code == 200 and reply.get_json()["success"] is True
    assert "capability map" in reply.get_json()["message"]
    db_session.expire_all()
    login_as(client, user)
    api = _map_rows(client)[bcs["cal"].name]
    assert apps["ledger"].name not in [a["name"] for a in api["applications"]]
    _, rows = _mapping_page(client)
    assert apps["ledger"].name not in rows[bcs["cal"].name]
    assert apps["ledger"].name not in _record(client, login_as, user, bcs["cal"])
    assert _sql(db_session, "SELECT COUNT(*) FROM application_capability_coverage WHERE id = :i", i=cov.id).scalar() == 0


# --- D3: backfill, writers and the guard ------------------------------------------------------------------

def test_backfill_shows_hidden_mappings_dry_run_writes_nothing_and_second_apply_inserts_none(
        client, login_as, db_session, make_org):
    from app.commands import backfill_capability_mappings as bf

    org_a, org_b = make_org("cb-d3-a"), make_org("cb-d3-b")
    user = _owner(db_session, org_a)
    bcs, unified, apps = _seed(db_session, org_a)
    bcs_b, unified_b, apps_b = _seed(db_session, org_b)
    # Held only in the frozen tables, as production holds them.
    _legacy_only(db_session, org_a, bcs["sub"], apps["ledger"])
    _coverage_only(db_session, bcs["log"], apps["bench"])
    _coverage_only(db_session, bcs["cal"], apps["ledger"])      # already in the store
    # Cannot be resolved: an application of the other organisation.
    _coverage_only(db_session, bcs["sub"], apps_b["bench"])
    db_session.flush()
    hidden_sub, hidden_log = apps["ledger"].name, apps["bench"].name
    assert hidden_sub not in _record(client, login_as, user, bcs["sub"])
    assert hidden_log not in _record(client, login_as, user, bcs["log"])

    before = (_store_count(db_session, org_a), _store_count(db_session, org_b))
    report = bf.plan(org_a.id)
    assert (_store_count(db_session, org_a), _store_count(db_session, org_b)) == before, "the plan wrote"
    b = report[org_a.id]
    assert len(b["insert"]) == 2 and b["already"] == 1
    assert sum(b["unresolved"].values()) == 1 and bf.R_FOREIGN_APP in b["unresolved"]
    assert org_b.id not in report

    assert bf.apply(report) == 2
    db_session.flush()
    assert bf.apply(bf.plan(org_a.id)) == 0, "a second apply must insert nothing"
    assert _store_count(db_session, org_a) == before[0] + 2
    assert _store_count(db_session, org_b) == before[1], "organisation A's rows landed in organisation B"
    db_session.expire_all()
    assert hidden_sub in _record(client, login_as, user, bcs["sub"])
    assert hidden_log in _record(client, login_as, user, bcs["log"])
    _, rows = _mapping_page(client)
    assert hidden_sub in rows[bcs["sub"].name] and hidden_log in rows[bcs["log"].name]


def test_backfill_cli_is_a_dry_run_unless_told_to_apply(app, db_session, make_org):
    runner = app.test_cli_runner()
    from app.commands.backfill_capability_mappings import backfill_capability_mappings

    org = make_org("cb-cli")
    bcs, _, apps = _seed(db_session, org)
    _coverage_only(db_session, bcs["log"], apps["bench"])
    db_session.flush()
    before = _store_count(db_session, org)
    out = runner.invoke(backfill_capability_mappings, ["--org-id", str(org.id)])
    assert out.exit_code == 0, out.output
    assert "DRY RUN" in out.output and "to insert 1" in out.output
    assert _store_count(db_session, org) == before


def test_a_row_created_in_a_frozen_table_reaches_the_store(db_session, make_org):
    """Every older writer still creates frozen-table rows; the model layer writes them into the store."""
    from app.models.application_capability import ApplicationCapabilityMapping as Legacy
    from app.models.business_capabilities import ApplicationCapabilityCoverage as Cov

    org = make_org("cb-w")
    bcs, unified, apps = _seed(db_session, org)
    before = _store_count(db_session, org)
    db_session.add(Cov(application_component_id=apps["bench"].id, capability_id=bcs["log"].id, support_level="partial"))
    db_session.add(Legacy(organization_id=org.id, application_component_id=apps["ledger"].id,
                          business_capability_id=bcs["log"].id, support_level="full"))
    db_session.flush()
    assert _store_count(db_session, org) == before + 2
    # A pair already in the store is not written again.
    db_session.add(Cov(application_component_id=apps["ledger"].id, capability_id=bcs["cal"].id))
    db_session.flush()
    assert _store_count(db_session, org) == before + 2


def test_abacus_auto_suggest_applied_mappings_appear_on_every_screen(client, login_as, db_session, make_org):
    from app.models.application_layer import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    org = make_org("cb-ab")
    user = _owner(db_session, org)
    tag = uuid.uuid4().hex[:6]
    bc = BusinessCapability(name="Invoice Settlement %s" % tag, code="CBAB-%s" % tag, level=1, category="strategic",
                            organization_id=org.id)
    comp = ApplicationComponent(name="Invoice Settlement Engine %s" % tag, lifecycle_status="active",
                                organization_id=org.id, description="Invoice settlement")
    db_session.add_all([bc, comp])
    db_session.flush()
    login_as(client, user)
    reply = client.post("/capability-map/api/capabilities/abacus-auto-suggest",
                        json={"application_ids": [comp.id], "threshold": 0.1, "auto_apply_threshold": 0.3})
    assert reply.status_code == 200, reply.get_data(as_text=True)
    applied = [s for s in reply.get_json().get("suggestions", []) if s["auto_applied"] and s["capability_id"] == bc.id]
    assert applied, reply.get_json()
    db_session.expire_all()
    login_as(client, user)
    api = _map_rows(client)[bc.name]
    assert comp.name in [a["name"] for a in api["applications"]]
    _, rows = _mapping_page(client)
    assert comp.name in rows[bc.name]
    assert comp.name in _record(client, login_as, user, bc)
    # A second run finds it mapped and applies nothing.
    again = client.post("/capability-map/api/capabilities/abacus-auto-suggest",
                        json={"application_ids": [comp.id], "threshold": 0.1, "auto_apply_threshold": 0.3})
    assert not [s for s in again.get_json()["suggestions"] if s["auto_applied"] and s["capability_id"] == bc.id]


def test_guard_fails_on_a_new_write_to_a_frozen_table_and_passes_on_the_tree(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import frozen_mapping_writers as guard

    baseline = json.loads(guard.BASELINE.read_text())
    assert not guard.violations(guard.scan(ROOT / "app"), baseline), "a new write to a frozen table"
    scratch = tmp_path / "app"
    scratch.mkdir()
    (scratch / "newcode.py").write_text(
        "from app.models.business_capabilities import ApplicationCapabilityCoverage\n"
        "def f(db):\n    db.session.add(ApplicationCapabilityCoverage(capability_id=1, application_component_id=2))\n"
        "    db.session.execute('INSERT INTO application_capability_mapping (a) VALUES (1)')\n")
    found = guard.scan(scratch)
    assert found == {"newcode.py": 2}
    assert guard.violations(found, baseline) == [("newcode.py", 2, 0)]


# --- D4 / D5: edit form category, one count --------------------------------------------------------------

def test_edit_form_opens_on_the_capability_type_when_the_business_record_has_none(client, login_as, db_session, make_org):
    org = make_org("cb-d4")
    user = _owner(db_session, org)
    bcs, unified, _ = _seed(db_session, org)
    bcs["cal"].category = None
    unified["cal"].capability_type = "operational"
    db_session.flush()
    login_as(client, user)
    html = client.get("/enterprise/capability-map/capabilities").get_data(as_text=True)
    card = re.search(r'data-cap-id="%s".*?data-cap-category="([^"]*)"' % bcs["cal"].id, html, re.S)
    assert card and card.group(1) == "operational"


def test_four_screens_show_the_same_totals_with_duplicates_coverage_only_and_a_shared_capability(
        client, login_as, db_session, make_org):
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as M
    from app.models.unified_capability import UnifiedCapability

    org = make_org("cb-d5")
    user = _owner(db_session, org)
    bcs, unified, apps = _seed(db_session, org)
    _drop_unique(db_session)
    _dup(db_session, unified["cal"].id, apps["ledger"].id)
    _coverage_only(db_session, bcs["sub"], apps["route"])
    shared = UnifiedCapability(name="Shared reference %s" % uuid.uuid4().hex[:6], code="SHR-%s" % uuid.uuid4().hex[:6],
                               level=1, organization_id=None, scope="reference")
    db_session.add(shared)
    db_session.flush()
    login_as(client, user)
    map_total = sum(1 for _ in _map_rows(client))
    login_as(client, user)
    caps = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", client.get("/enterprise/capability-map/capabilities").get_data(as_text=True)))
    total = int(re.search(r"Total Capabilities (\d+)", caps).group(1))
    level1 = int(re.search(r"Level 1 (\d+)", caps).group(1))
    login_as(client, user)
    hier = [c for c in _flat(_catalog(client.get("/capability-map/hierarchy").get_data(as_text=True))["children"])]
    login_as(client, user)
    _, rows = _mapping_page(client)
    assert total == map_total == len(hier), (total, map_total, len(hier))
    assert level1 == sum(1 for c in hier if c["level"] == 1)
    assert len(rows) == map_total, (len(rows), map_total)
    # Per capability: map, hierarchy and mapping page list the same applications, each once.
    login_as(client, user)
    api = _map_rows(client)
    for c in hier:
        if c["name"] in api and c["name"] in rows:
            assert c["mapping_count"] == len(rows[c["name"]]) == len(api[c["name"]]["applications"]), c["name"]
