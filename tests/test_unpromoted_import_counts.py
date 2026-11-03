"""An import nobody owns is a repository: one rule, and every count and label uses it."""

import pytest

from app import db

from app.connectors.github import (
    is_unpromoted_import,
    unpromoted_import_element_ids,
    unpromoted_import_expression,
    unpromoted_import_ids,
)
from app.models.application_owner import ApplicationOwner
from app.models.application_portfolio import ApplicationComponent
from app.models.archimate_core import ArchiMateElement
from app.models.user import User
from app.modules.dashboard.services.overview_figures import _active_applications, left_out_applications
from app.models.enterprise_intelligence import OrganizationUnit
from app.modules.applications.routes.coverage_routes import coverage_figures
from app.modules.intelligence.services.history_service import ChangeEntry, _plain, change_row


def _import(db_session, org, name, source_kind="connector"):
    element = ArchiMateElement(name=name, type="Application", layer="application",
                               organization_id=org.id, source_kind=source_kind)
    db_session.add(element)
    db_session.flush()
    row = ApplicationComponent(name=name, organization_id=org.id, archimate_element_id=element.id)
    db_session.add(row)
    db_session.flush()
    return row, element


def _person(db_session, org, tag):
    user = User(email="%s@example.test" % tag, organization_id=org.id, first_name="A", last_name="B")
    db_session.add(user)
    db_session.flush()
    return user


def test_expression_batch_and_predicate_agree_and_other_organisation_is_ignored(db_session, make_org):
    mine, theirs = make_org("mine"), make_org("theirs")
    repo, repo_element = _import(db_session, mine, "Repo One")
    typed, _ = _import(db_session, mine, "Typed In", source_kind="manual")
    owned, _ = _import(db_session, mine, "Owned Import")
    rival, _ = _import(db_session, theirs, "Rival Import")
    person = _person(db_session, mine, "owner-%s" % mine.id)
    db_session.add(ApplicationOwner(application_id=owned.id, user_id=person.id,
                                    organization_id=mine.id, ownership_type="primary"))
    db_session.flush()

    ids = [repo.id, typed.id, owned.id, rival.id]
    assert [is_unpromoted_import(r) for r in (repo, typed, owned, rival)] == [True, False, False, True]
    assert unpromoted_import_ids(mine.id, ids) == {repo.id}
    assert unpromoted_import_element_ids(mine.id, [repo_element.id, rival.archimate_element_id]) == {repo_element.id}
    flagged = db_session.execute(
        ApplicationComponent.__table__.select().with_only_columns(ApplicationComponent.id)
        .where(ApplicationComponent.id.in_(ids), unpromoted_import_expression(ApplicationComponent))
    ).scalars().all()
    assert set(flagged) == {repo.id, rival.id}


def test_unpromoted_import_is_in_no_count_until_an_owner_is_named(db_session, make_org):
    org = make_org("counts")
    repo, _ = _import(db_session, org, "Counted Later")

    def counted():
        stmt = _active_applications().where(ApplicationComponent.organization_id == org.id)
        return {r.id for r in db_session.execute(stmt).scalars()}

    assert repo.id not in counted()
    assert left_out_applications(org.id)["repository"] == 1

    person = _person(db_session, org, "later-%s" % org.id)
    db_session.add(ApplicationOwner(application_id=repo.id, user_id=person.id,
                                    organization_id=org.id, ownership_type="primary"))
    db_session.flush()
    assert repo.id in counted()
    assert left_out_applications(org.id)["repository"] == 0


def test_left_out_names_each_reason_once(db_session, make_org):
    org = make_org("reasons")
    _import(db_session, org, "Only A Repo")
    db_session.add_all([
        ApplicationComponent(name="(Duplicate) Copy", organization_id=org.id),
        ApplicationComponent(name="Old System", organization_id=org.id, lifecycle_status="5. DECOMMISSIONED"),
        ApplicationComponent(name="(Duplicate) Old Copy", organization_id=org.id, lifecycle_status="5. DECOMMISSIONED"),
    ])
    db_session.flush()
    assert left_out_applications(org.id) == {"duplicate": 2, "decommissioned": 1, "repository": 1}


def test_booleans_read_yes_or_no():
    assert _plain(True) == "yes"
    assert _plain(False) == "no"


@pytest.mark.parametrize("blank", ["\t", "\n", " ", " \t \n "])
def test_owner_text_of_only_whitespace_names_nobody_in_python_and_sql(db_session, make_org, blank):
    org = make_org("blank")
    repo, _ = _import(db_session, org, "Blank Owner")
    repo.legacy_application_owner = blank
    db_session.flush()
    assert is_unpromoted_import(repo) is True
    assert unpromoted_import_ids(org.id, [repo.id]) == {repo.id}
    repo.legacy_application_owner = " \tReal Person "
    db_session.flush()
    assert is_unpromoted_import(repo) is False
    assert unpromoted_import_ids(org.id, [repo.id]) == set()


def test_two_units_differing_only_in_case_or_spacing_never_count_one_application_twice(
    db_session, make_org, tenant_ctx
):
    mine, theirs = make_org("units"), make_org("rival")
    for org in (mine, theirs):
        for name in ("Operations", "OPERATIONS", "Retail  Banking"):
            db_session.add(OrganizationUnit(name=name, code="%s-%s" % (name[:3], org.id), organization_id=org.id))
        row = ApplicationComponent(name="Ops App %s" % org.id, organization_id=org.id,
                                   business_domain=" operations ", legacy_application_owner="Someone")
        spaced = ApplicationComponent(name="Retail App %s" % org.id, organization_id=org.id,
                                      business_domain="retail banking", legacy_application_owner="Someone")
        db_session.add_all([row, spaced])
    db_session.flush()
    with tenant_ctx(mine.id):
        figures = coverage_figures(mine.id)
    totals = {r["unit_name"]: r["total_apps"] for r in figures["rows"]}
    assert sum(totals.values()) == 2, totals
    assert totals["OPERATIONS"] == 1 and totals["Operations"] == 0, totals
    assert totals["Retail  Banking"] == 1, totals
    assert figures["merged_units"] == [("Operations", "OPERATIONS")] or figures["merged_units"] == [
        ("OPERATIONS", "Operations")
    ], figures["merged_units"]


def test_application_rows_of_an_unpromoted_import_read_repository_in_the_evidence(db_session, make_org):
    org = make_org("evidence")
    repo, _ = _import(db_session, org, "Evidence Repo")
    change = ChangeEntry(
        table_name="application_component", record_id=repo.id, action="insert", valid_from=None, valid_to=None,
        recorded_at=None, changed_by_id=None, changed_by_name=None, change_reason=None, snapshot={},
        label=repo.name, kind="Application",
    )
    assert change_row(change, None, frozenset(), frozenset({repo.id}))["kind"] == "Repository"
    assert change_row(change, None, frozenset(), frozenset())["kind"] == "Application"


def _unicode_spaces():
    return [chr(i) for i in range(0x3100) if chr(i).isspace()]


@pytest.mark.parametrize("space", _unicode_spaces(), ids=lambda c: "U+%04X" % ord(c))
def test_owner_text_of_only_unicode_spaces_names_nobody_in_both_rules(db_session, make_org, space):
    from app.services.element_ownership_service import application_is_owned, application_is_owned_expression

    org = make_org("uni")
    repo, _ = _import(db_session, org, "Unicode Blank")
    repo.legacy_application_owner = space * 2
    db_session.flush()
    in_sql = db_session.execute(
        db.select(ApplicationComponent.id).where(ApplicationComponent.id == repo.id, application_is_owned_expression())
    ).first() is not None
    assert application_is_owned(repo) is False
    assert in_sql is False


@pytest.mark.parametrize("space", _unicode_spaces(), ids=lambda c: "U+%04X" % ord(c))
def test_unit_name_rules_in_sql_and_python_agree_on_edge_spaces(db_session, make_org, tenant_ctx, space):
    from app.modules.applications.routes.coverage_routes import _domain_matches_unit, _normalised

    org = make_org("edge")
    row = ApplicationComponent(name="Edge App", organization_id=org.id, business_domain=space + "Ops" + space + "Desk" + space)
    db_session.add(row)
    db_session.flush()
    unit = "ops desk"
    in_sql = db_session.execute(
        db.select(ApplicationComponent.id).where(ApplicationComponent.id == row.id, _domain_matches_unit(unit))
    ).first() is not None
    assert _normalised(row.business_domain) == unit
    assert in_sql is True


def test_a_change_to_an_owner_names_the_application_never_a_number(db_session, make_org):
    from datetime import datetime
    from app.models.audit_log import AuditLog
    from app.modules.intelligence.services.history_service import HistoryService

    mine, theirs = make_org("evmine"), make_org("evtheirs")
    mine_app, _ = _import(db_session, mine, "Named Application")
    their_app, _ = _import(db_session, theirs, "Rival Named Application")
    when = datetime(2026, 10, 20, 12, 0, 0)
    for org, app_row, record in ((mine, mine_app, 9001), (theirs, their_app, 9002), (mine, their_app, 9003)):
        db_session.add(AuditLog(
            organization_id=org.id, user_id=None, table_name="application_owners", record_id=record,
            action="create", created_at=when, old_value=None,
            new_value={"application_id": app_row.id, "ownership_type": "primary"},
            extra_json={"source": "screen", "correlation_id": "c-%d" % record},
        ))
    db_session.flush()
    changes = {c.record_id: c for c in HistoryService(db_session, mine.id).difference(
        datetime(2026, 10, 1), datetime(2026, 11, 1)).changes}
    rows = {rid: change_row(c) for rid, c in changes.items()}
    assert rows[9001]["what"] == "Named Application"
    assert "#" not in rows[9003]["what"] and "not recorded" in rows[9003]["what"]
    assert "Rival" not in rows[9003]["what"]
    assert 9002 not in rows


def test_a_record_that_cannot_be_named_says_so_in_words():
    change = ChangeEntry(
        table_name="solution", record_id=7, action="update", valid_from=None, valid_to=None, recorded_at=None,
        changed_by_id=None, changed_by_name=None, change_reason=None, snapshot={}, label=None, kind="Solution",
    )
    row = change_row(change)
    assert "#" not in row["what"] and "not recorded" in row["what"]
