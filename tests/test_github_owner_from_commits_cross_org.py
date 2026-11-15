"""A committer who is a member only of another organisation is never offered or made owner."""

from __future__ import annotations

import pytest

from tests.test_github_owner_from_commits import REPOSITORY, commits, make_application, make_user, primaries

pytestmark = pytest.mark.usefixtures("db_session")


def test_a_member_of_the_other_organisation_is_not_a_candidate(db_session, make_org):
    from app.connectors import github
    from app.connectors.github_gaps import _commit_hint

    org_a, org_b = make_org("xo-a"), make_org("xo-b")
    ada = make_user(db_session, org_a, "Ada", "Obi")
    bea = make_user(db_session, org_b, "Bea", "Roe")
    element, component = make_application(db_session, org_a)

    summary = github.summarise_commits(org_a.id, REPOSITORY, commits((bea.email, 20), (ada.email, 4)))

    assert [c["user_id"] for c in summary["candidates"]] == [ada.id]
    assert github.apply_commit_history(org_a.id, element, component, summary) is True
    assert [r.user_id for r in primaries(db_session, component)] == [ada.id]
    # a stored candidate from the other organisation is dropped when the page is built
    summary["candidates"].append({"user_id": bea.id, "commits": 2})
    element.acm_properties = None
    github._write_facts(element, {"github_commit_owners": summary})
    assert bea.id not in [c["user_id"] for c in _commit_hint(element, org_a.id).get("candidates", [])]


def test_make_owner_with_a_user_of_the_other_organisation_answers_404_and_writes_nothing(
    db_session, make_org, client, login_as
):
    from app.connectors import github

    org_a, org_b = make_org("xo-c"), make_org("xo-d")
    ada = make_user(db_session, org_a, "Ada", "Obi")
    sam = make_user(db_session, org_a, "Sam", "Lee")
    bea = make_user(db_session, org_b, "Bea", "Roe")
    element, component = make_application(db_session, org_a)
    summary = github.summarise_commits(org_a.id, REPOSITORY, commits((ada.email, 6), (sam.email, 6)))
    github.apply_commit_history(org_a.id, element, component, summary)
    db_session.commit()
    login_as(client, ada)

    url = f"/applications/{component.id}/owners/from-commits"
    refused = client.post(url, data={"user_id": bea.id})
    assert refused.status_code in (403, 404)
    assert primaries(db_session, component) == []

    allowed = client.post(url, data={"user_id": sam.id})
    assert allowed.status_code == 302
    assert [r.user_id for r in primaries(db_session, component)] == [sam.id]
