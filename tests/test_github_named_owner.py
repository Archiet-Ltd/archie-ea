"""GitHub names the owner of an application even when that person is not a member of the organisation."""

from __future__ import annotations

import json

import pytest

from tests.test_github_owner_from_commits import REPOSITORY, make_application, make_user, primaries

pytestmark = pytest.mark.usefixtures("db_session")


def commits_by(*triples):
    """Commit records: ``("login", "email", n)`` makes n commits by that GitHub account."""
    out = []
    for login, email, count in triples:
        for _ in range(count):
            number = len(out)
            out.append({
                "sha": f"{number:07x}bbbb", "author": {"login": login},
                "commit": {"author": {"email": email, "date": f"2026-10-{28 - number % 27:02d}T10:00:00Z"}},
            })
    return out


def with_code_owners(element, owners):
    from app.connectors import github

    github._write_facts(element, {"github_code_owners": owners})


def named(db_session, component):
    return [(r.person_name, r.user_id, r.source_table) for r in primaries(db_session, component)]


def source(element):
    return (element.acm_properties or {})["github_owner_source"]["value"]


def test_one_code_owner_who_is_not_a_member_becomes_a_named_owner_with_the_source(db_session, make_org):
    from app.connectors import github

    org = make_org("gh-named-co")
    element, component = make_application(db_session, org)
    with_code_owners(element, ["@Anioko"])
    summary = github.summarise_commits(org.id, REPOSITORY, commits_by(("Anioko", "a@example.org", 2), ("zed", "z@example.org", 2)))

    assert github.apply_commit_history(org.id, element, component, summary) is True

    assert named(db_session, component) == [("@Anioko", None, "github_connector")]
    assert source(element)["text"] == f"Named by GitHub: code owner in {REPOSITORY} CODEOWNERS at abc1234"
    assert "a@example.org" not in json.dumps(source(element))


def test_one_author_who_leads_the_commits_becomes_a_named_owner_with_the_count(db_session, make_org):
    from app.connectors import github

    org = make_org("gh-named-lead")
    element, component = make_application(db_session, org)
    summary = github.summarise_commits(org.id, REPOSITORY, commits_by(("Bea-Roe", "b@example.org", 7), ("zed", "z@example.org", 3)))

    assert github.apply_commit_history(org.id, element, component, summary) is True

    assert named(db_session, component) == [("@Bea-Roe", None, "github_connector")]
    assert source(element)["text"] == f"Named by GitHub: leads the recent commits of {REPOSITORY} (7 of 10)"


def test_a_member_match_wins_over_the_named_person(db_session, make_org):
    from app.connectors import github

    org = make_org("gh-named-member")
    ada = make_user(db_session, org, "Ada", "Obi")
    element, component = make_application(db_session, org)
    with_code_owners(element, ["@ada-obi"])
    summary = github.summarise_commits(org.id, REPOSITORY, commits_by(("ada-obi", ada.email, 5)))

    assert github.apply_commit_history(org.id, element, component, summary) is True

    assert named(db_session, component) == [(None, ada.id, "github_connector")]


def test_one_code_owner_wins_over_a_commit_leader(db_session, make_org):
    from app.connectors import github

    org = make_org("gh-named-order")
    element, component = make_application(db_session, org)
    with_code_owners(element, ["@owner-one"])
    summary = github.summarise_commits(org.id, REPOSITORY, commits_by(("leader", "l@example.org", 9), ("owner-one", "o@example.org", 1)))

    github.apply_commit_history(org.id, element, component, summary)

    assert named(db_session, component) == [("@owner-one", None, "github_connector")]


def test_several_code_owners_and_no_leader_leave_the_reason(db_session, make_org):
    from app.connectors import github

    org = make_org("gh-named-many")
    element, component = make_application(db_session, org)
    with_code_owners(element, ["@one", "@two"])
    summary = github.summarise_commits(org.id, REPOSITORY, commits_by(("one", "1@example.org", 3), ("two", "2@example.org", 3)))

    assert github.apply_commit_history(org.id, element, component, summary) is False

    assert named(db_session, component) == []
    assert source(element)["user_id"] is None
    assert source(element)["text"].startswith("No owner was set: GitHub names @one, @two as code owner")


def test_several_code_owners_with_a_leader_name_the_leader(db_session, make_org):
    from app.connectors import github

    org = make_org("gh-named-many-lead")
    element, component = make_application(db_session, org)
    with_code_owners(element, ["@one", "@two"])
    summary = github.summarise_commits(org.id, REPOSITORY, commits_by(("one", "1@example.org", 8), ("two", "2@example.org", 2)))

    github.apply_commit_history(org.id, element, component, summary)

    assert named(db_session, component) == [("@one", None, "github_connector")]


def test_no_code_owner_and_no_leader_names_nobody(db_session, make_org):
    from app.connectors import github

    org = make_org("gh-named-none")
    element, component = make_application(db_session, org)
    summary = github.summarise_commits(org.id, REPOSITORY, commits_by(("one", "1@example.org", 3), ("two", "2@example.org", 3)))

    assert github.apply_commit_history(org.id, element, component, summary) is False
    assert named(db_session, component) == []


def test_an_owner_a_person_recorded_is_never_overwritten_by_a_named_one(db_session, make_org):
    from app.connectors import github
    from app.services.element_ownership_service import set_named_person_owner

    org = make_org("gh-named-kept")
    element, component = make_application(db_session, org)
    set_named_person_owner(component, "Sam Lee", "primary", organization_id=org.id)
    db_session.flush()
    with_code_owners(element, ["@Anioko"])
    summary = github.summarise_commits(org.id, REPOSITORY, commits_by(("Anioko", "a@example.org", 5)))

    assert github.apply_commit_history(org.id, element, component, summary) is False
    assert github.apply_commit_history(org.id, element, component, summary) is False

    assert named(db_session, component) == [("Sam Lee", None, None)]


def test_a_second_read_changes_nothing(db_session, make_org):
    from app.connectors import github
    from app.models.application_owner import ApplicationOwner

    org = make_org("gh-named-twice")
    element, component = make_application(db_session, org)
    with_code_owners(element, ["@Anioko"])
    summary = github.summarise_commits(org.id, REPOSITORY, commits_by(("Anioko", "a@example.org", 5)))

    github.apply_commit_history(org.id, element, component, summary)
    before = ApplicationOwner.query.filter_by(application_id=component.id).count()
    github.apply_commit_history(org.id, element, component, summary)

    assert ApplicationOwner.query.filter_by(application_id=component.id).count() == before == 1


def test_the_same_login_in_two_organisations_gives_each_its_own_owner(db_session, make_org):
    from app.connectors import github
    from app.models.application_owner import ApplicationOwner

    org_a, org_b = make_org("gh-named-a"), make_org("gh-named-b")
    element_a, component_a = make_application(db_session, org_a)
    element_b, component_b = make_application(db_session, org_b)
    for org, element, component in ((org_a, element_a, component_a), (org_b, element_b, component_b)):
        with_code_owners(element, ["@Anioko"])
        summary = github.summarise_commits(org.id, REPOSITORY, commits_by(("Anioko", "a@example.org", 5)))
        assert github.apply_commit_history(org.id, element, component, summary) is True

    rows_a, rows_b = primaries(db_session, component_a), primaries(db_session, component_b)
    assert [(r.person_name, r.organization_id) for r in rows_a] == [("@Anioko", org_a.id)]
    assert [(r.person_name, r.organization_id) for r in rows_b] == [("@Anioko", org_b.id)]
    assert rows_a[0].id != rows_b[0].id
    assert ApplicationOwner.query.filter_by(organization_id=org_a.id, application_id=component_b.id).count() == 0


def test_a_bot_that_made_most_commits_is_never_the_named_owner(db_session, make_org):
    from app.connectors import github

    org = make_org("gh-named-bot")
    element, component = make_application(db_session, org)
    summary = github.summarise_commits(org.id, REPOSITORY, commits_by(
        ("dependabot[bot]", "d@example.org", 8), ("alice", "a@example.org", 2)))

    assert github.apply_commit_history(org.id, element, component, summary) is True

    assert named(db_session, component) == [("@alice", None, "github_connector")]
    assert source(element)["text"].endswith("(2 of 2)")


def test_a_person_under_half_of_the_human_commits_is_not_named_beside_a_bot(db_session, make_org):
    from app.connectors import github

    org = make_org("gh-named-bot-split")
    element, component = make_application(db_session, org)
    summary = github.summarise_commits(org.id, REPOSITORY, commits_by(
        ("github-actions[bot]", "g@example.org", 6), ("alice", "a@example.org", 2), ("bob", "b@example.org", 2)))

    assert github.apply_commit_history(org.id, element, component, summary) is False
    assert named(db_session, component) == []


def test_a_repository_only_bots_committed_to_names_no_owner(db_session, make_org):
    from app.connectors import github

    org = make_org("gh-named-only-bots")
    element, component = make_application(db_session, org)
    summary = github.summarise_commits(org.id, REPOSITORY, commits_by(
        ("dependabot[bot]", "d@example.org", 5), ("renovate[bot]", "r@example.org", 3)))

    assert github.apply_commit_history(org.id, element, component, summary) is False
    assert named(db_session, component) == []


def test_a_team_beside_a_person_in_code_owners_names_no_single_owner(db_session, make_org):
    from app.connectors import github

    org = make_org("gh-named-team-beside")
    element, component = make_application(db_session, org)
    with_code_owners(element, ["@acme/platform", "@alice"])
    summary = github.summarise_commits(org.id, REPOSITORY, commits_by(("zed", "z@example.org", 4), ("alice", "a@example.org", 1)))

    assert github.apply_commit_history(org.id, element, component, summary) is True
    assert named(db_session, component) == [("@zed", None, "github_connector")]
