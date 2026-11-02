"""An import nobody owns is a repository: one rule, and every count and label uses it."""

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
from app.modules.intelligence.services.history_service import _plain


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
