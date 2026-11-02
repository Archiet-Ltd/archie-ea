"""An import nobody owns is a repository: one rule, and every count and label uses it."""

import pytest

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
