"""One command replaces the demonstration company, so every environment's demo history and figures agree.

Written from what the person running the demonstration needs: an environment whose demo was seeded
before the dated-history work shows about 9 changes against 365 relationships, and a plain second seed
is refused. After ``flask reset-demo-company`` the old demonstration is retired (nothing sealed is deleted),
a fresh one is seeded, the demo login opens the fresh one, every seeded table has one dated create entry per
row, the audit chain is intact, the vendors page says how many systems its vendors supply, and a second
organisation is exactly as it was.
"""
import re

import pytest

from app import db

DEMO_SLUG = "lantern-quay"
DEMO_EMAIL = "demo@lantern-quay.example.com"


@pytest.fixture(autouse=True)
def _demo_password(monkeypatch):
    monkeypatch.setenv("DEMO_USER_PASSWORD", "test-password")


def _owned_tables():
    return [
        row[0] for row in db.session.execute(db.text(
            "SELECT c.table_name FROM information_schema.columns c "
            "JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
            "WHERE c.table_schema = 'public' AND c.column_name = 'organization_id' AND t.table_type = 'BASE TABLE' "
            "ORDER BY c.table_name"
        ))
    ]


def _snapshot(org_id):
    """Every row the organisation owns in every table, as JSON text, row for row."""
    taken = {}
    for table in _owned_tables():
        rows = db.session.execute(
            db.text('SELECT to_jsonb(t)::text FROM "%s" t WHERE t.organization_id = :o ORDER BY 1' % table),  # tenancy-ok: the test compares one organisation's rows
            {"o": org_id},
        ).scalars().all()
        if rows:
            taken[table] = rows
    return taken


def _counts(org_id):
    return {table: len(rows) for table, rows in _snapshot(org_id).items()}


def _org(slug):
    return db.session.execute(db.text("SELECT id FROM organizations WHERE slug = :s"), {"s": slug}).scalar()


def _retired_orgs():
    return db.session.execute(db.text(
        "SELECT id, slug, is_active FROM organizations WHERE slug LIKE 'lantern-quay-retired-%' ORDER BY id")).all()


def _neighbour(make_org):
    """A second organisation with its own systems, a vendor the demo also names, and a user."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import Role, User

    org = make_org("neighbour")
    Role.insert_roles()
    user = User(email="neighbour-owner@example.com", first_name="Nell", last_name="Bour", organization_id=org.id,
                confirmed=True, enterprise_role="enterprise_architect")
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "password"
    db.session.add(user)
    for name in ("Neighbour ledger", "Neighbour portal"):
        db.session.add(ApplicationComponent(
            name=name, organization_id=org.id, lifecycle_status="2.2 tactical", vendor_name="Oracle"))
    db.session.commit()
    return org.id


def _create_entries(org_id, table):
    return db.session.execute(
        db.text("SELECT count(*) FROM soc2_audit_log WHERE organization_id = :o AND action = 'create' AND table_name = :t"),
        {"o": org_id, "t": table},
    ).scalar()


def _rows(org_id, table):
    return db.session.execute(db.text('SELECT count(*) FROM "%s" WHERE organization_id = :o' % table), {"o": org_id}).scalar()  # tenancy-ok: counts one organisation's rows


# the audit label of each table the seed writes history for, and the table its rows live in
SEEDED = {
    "archimate_elements": "archimate_elements",
    "archimate_relationships": "archimate_relationships",
    "application_component": "application_components",
    "application_owners": "application_owners",
    "application_ownership": "application_ownership",
    "business_capability": "business_capability",
    "solution": "solutions",
    "user": "users",
}


def _assert_whole(org_id):
    from app.models.audit_log import AuditLog

    for label, table in SEEDED.items():
        assert _create_entries(org_id, label) == _rows(org_id, table) > 0, (
            "%s: %s dated create entries for %s rows" % (label, _create_entries(org_id, label), _rows(org_id, table)))
    result = AuditLog.verify_chain(org_id)
    assert result["status"] == "intact" and result["unsealed"] == 0, result


def test_reset_retires_the_old_demo_without_deleting_a_row_and_seeds_a_fresh_one(app, db_session, make_org):
    from app.commands.reset_demo_company import reset_demo_company
    from app.commands.seed_demo_company import seed_demo_company

    neighbour_id = _neighbour(make_org)
    seed_demo_company()
    old_id = _org(DEMO_SLUG)
    old_counts, old_audit = _counts(old_id), _snapshot(old_id)["soc2_audit_log"]
    neighbour_before = _snapshot(neighbour_id)
    assert neighbour_before["application_components"] and neighbour_before["users"]
    vendor = db_session.execute(db.text("SELECT id, named_by_organization_id FROM vendor_organizations WHERE name = 'Oracle'")).one()

    result = reset_demo_company()

    # the old demonstration still exists, is inactive, and holds every row and every sealed history entry it held
    retired = _retired_orgs()
    assert [(row.id, row.is_active) for row in retired] == [(old_id, False)]
    assert retired[0].slug == result["retired"]["slug"] and re.fullmatch(
        r"lantern-quay-retired-\d{8}T\d{6}Z(-\d+)?", retired[0].slug)
    assert _counts(old_id) == old_counts
    assert _snapshot(old_id)["soc2_audit_log"] == old_audit
    _assert_whole(old_id)
    # the fresh demonstration is whole: one dated create entry per row, an intact chain
    new_id = _org(DEMO_SLUG)
    assert new_id and new_id != old_id
    _assert_whole(new_id)
    assert result["created"]["organization_created"] == 1
    # the demo login opens the fresh organisation
    owner = db_session.execute(db.text("SELECT organization_id FROM users WHERE email = :e"), {"e": DEMO_EMAIL}).scalars().all()
    assert owner == [new_id]
    # the other organisation is exactly as it was, in every table it owns, and so is the vendor both name
    assert _snapshot(neighbour_id) == neighbour_before
    assert db_session.execute(db.text(
        "SELECT id, named_by_organization_id FROM vendor_organizations WHERE name = 'Oracle'")).one() == vendor


def test_reset_twice_keeps_the_first_retired_copy_and_retires_the_second(app, db_session, make_org):
    from app.commands.reset_demo_company import reset_demo_company

    neighbour_id = _neighbour(make_org)
    before = _snapshot(neighbour_id)
    reset_demo_company()  # no demonstration yet: nothing to retire
    first = _org(DEMO_SLUG)
    assert _retired_orgs() == []
    reset_demo_company()
    second = _org(DEMO_SLUG)
    reset_demo_company()
    third = _org(DEMO_SLUG)
    assert len({first, second, third}) == 3
    retired = _retired_orgs()
    assert [row.id for row in retired] == [first, second]
    assert not any(row.is_active for row in retired)
    assert len({row.slug for row in retired}) == 2
    _assert_whole(third)
    assert db_session.execute(db.text("SELECT organization_id FROM users WHERE email = :e"), {"e": DEMO_EMAIL}).scalars().all() == [third]
    assert _snapshot(neighbour_id) == before


def test_reset_refuses_any_other_organisation_and_changes_nothing(app, db_session, make_org):
    from app.commands.reset_demo_company import NotTheDemoCompany, reset_demo_company, reset_demo_company_command
    from app.commands.seed_demo_company import seed_demo_company

    neighbour = _neighbour(make_org)
    seed_demo_company()
    slug = db_session.execute(db.text("SELECT slug FROM organizations WHERE id = :o"), {"o": neighbour}).scalar()
    demo_id = _org(DEMO_SLUG)
    before_neighbour, before_demo = _snapshot(neighbour), _snapshot(demo_id)
    with pytest.raises(NotTheDemoCompany):
        reset_demo_company(slug)
    result = app.test_cli_runner().invoke(reset_demo_company_command, ["--slug", slug])
    assert result.exit_code != 0
    assert "only on the demonstration company" in result.output
    assert _snapshot(neighbour) == before_neighbour
    assert _snapshot(demo_id) == before_demo
    assert _org(DEMO_SLUG) == demo_id and _retired_orgs() == []


def test_a_failure_part_way_through_the_retirement_changes_nothing(app, db_session, monkeypatch):
    from app.commands import reset_demo_company as module
    from app.commands.seed_demo_company import seed_demo_company

    seed_demo_company()
    demo_id = _org(DEMO_SLUG)
    before = _snapshot(demo_id)
    real_query = module._UNIQUE_TEXT_COLUMNS
    # a last column in a table that does not exist: every rename before it has run when it fails
    monkeypatch.setattr(module, "_UNIQUE_TEXT_COLUMNS",
                        "SELECT * FROM (%s) q UNION ALL SELECT 'no_such_table', 'code', 10" % real_query.replace("ORDER BY c.relname, a.attname", ""))
    with pytest.raises(Exception):
        module.reset_demo_company()
    assert _snapshot(demo_id) == before, "one transaction: no earlier rename survived the failure"
    assert _org(DEMO_SLUG) == demo_id and _retired_orgs() == []
    assert db_session.execute(db.text("SELECT is_active FROM organizations WHERE id = :o"), {"o": demo_id}).scalar() is not False


def test_a_table_guarded_against_update_stops_the_retirement_and_names_the_table(app, db_session, monkeypatch):
    import click

    from app.commands import reset_demo_company as module
    from app.commands.seed_demo_company import seed_demo_company

    seed_demo_company()
    demo_id = _org(DEMO_SLUG)
    before = _snapshot(demo_id)
    db_session.execute(db.text(
        "CREATE OR REPLACE FUNCTION test_refuse_update() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN RAISE EXCEPTION 'guarded against update' USING ERRCODE = '55000'; END; $$"))
    db_session.execute(db.text(
        "CREATE TRIGGER test_refuse_update BEFORE UPDATE ON users "
        "FOR EACH ROW EXECUTE FUNCTION test_refuse_update()"))
    db_session.commit()
    with pytest.raises(click.ClickException) as stopped:
        module.reset_demo_company()
    assert "users is guarded against UPDATE" in str(stopped.value) and "Nothing was changed" in str(stopped.value)
    db_session.execute(db.text("DROP TRIGGER test_refuse_update ON users"))
    assert _snapshot(demo_id) == before and _retired_orgs() == []


def test_the_command_exits_clean_and_prints_what_it_retired_and_created(app, db_session):
    from app.commands.reset_demo_company import reset_demo_company_command
    from app.commands.seed_demo_company import seed_demo_company

    seed_demo_company()
    result = app.test_cli_runner().invoke(reset_demo_company_command, [])
    assert result.exit_code == 0, result.output
    assert re.search(r"retired: the old demonstration company is now lantern-quay-retired-\S+ \(inactive; nothing deleted\)",
                     result.output), result.output
    assert re.search(r"  application_components\.application_code: 20 value\(s\) renamed", result.output), result.output
    assert "left exactly as sealed: " in result.output and "transformation_outbox_events" in result.output
    assert re.search(r"created:\n(  .+\n)+  total rows created: [1-9]\d*", result.output), result.output
    assert "elements created: " in result.output


def test_the_demo_vendors_page_says_how_many_systems_its_vendors_supply(app, db_session, client, login_as):
    """After a reset the vendors page does not say '14 vendors supply 0 of your 21 systems'."""
    from app.commands.reset_demo_company import reset_demo_company
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.user import User

    seed_demo_company()
    # the old demo: vendors named on the systems, no system linked to any (as QA found on the preview)
    db_session.execute(db.text(
        "UPDATE application_components SET vendor_organization_id = NULL WHERE organization_id = :o"), {"o": _org(DEMO_SLUG)})
    db_session.commit()
    reset_demo_company()
    demo_id = _org(DEMO_SLUG)
    user = User.query.filter_by(email=DEMO_EMAIL).one()
    assert user.organization_id == demo_id
    linked = db_session.execute(db.text(
        "SELECT count(*) FROM application_components WHERE organization_id = :o AND vendor_organization_id IS NOT NULL"),
        {"o": demo_id}).scalar()
    systems = _rows(demo_id, "application_components")
    vendors = db_session.execute(db.text(
        "SELECT count(DISTINCT vendor_organization_id) FROM application_components WHERE organization_id = :o"),
        {"o": demo_id}).scalar()
    assert linked == systems > 0

    login_as(client, user)
    page = client.get("/applications/vendors")
    assert page.status_code == 200
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page.get_data(as_text=True)))
    answer = re.search(r"(\d+) vendors? supply (\d+) of your (\d+) systems?", text)
    assert answer, text[:600]
    assert (int(answer.group(2)), int(answer.group(3))) == (linked, systems)
    assert int(answer.group(1)) >= vendors
    assert "have no vendor recorded" not in text
    # the register behind the page lists each vendor with the systems it supplies, and they add up to the links
    from app.services import vendor_register

    register = vendor_register.register(demo_id)
    assert sum(row["system_count"] for row in register) == linked
    assert all(row["system_count"] > 0 for row in register if row["name"] != "Lantern Quay (in-house)")
