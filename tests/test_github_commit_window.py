"""The commit read follows every page of the last 90 days, drops older commits, and names an owner only for a clear lead."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.test_github_owner_from_commits import REPOSITORY, make_application, make_user, primaries

pytestmark = pytest.mark.usefixtures("db_session")

STAMP = "%Y-%m-%dT%H:%M:%SZ"


def record(number, email, when):
    return {"sha": f"{number:07x}bbbb", "commit": {"author": {"email": email, "date": when.strftime(STAMP)}}}


class FakeCommitsApi:
    """Answers the commits listing one page at a time, as GitHub does, and remembers each request."""

    def __init__(self, items, honour_since=True):
        self.items, self.honour_since, self.requests = items, honour_since, []

    def get(self, path, params=None, use_cache=True):
        self.requests.append((path, dict(params or {})))
        items = self.items
        if self.honour_since and params.get("since"):
            items = [i for i in items if i["commit"]["author"]["date"] >= params["since"]]
        page, size = params.get("page", 1), params["per_page"]
        return SimpleNamespace(success=True, status_code=200, data=items[(page - 1) * size:page * size])


def read(org, api):
    from app.connectors.github import _RepositoryEvidence

    evidence = _RepositoryEvidence(MagicMock(), api, org.id, "acme", {})
    return evidence._commit_history(REPOSITORY)


def test_a_history_of_three_pages_is_counted_in_full(db_session, make_org):
    org = make_org("win-pages")
    ada, sam = make_user(db_session, org, "Ada", "Obi"), make_user(db_session, org, "Sam", "Lee")
    now = datetime.utcnow()
    items = [record(n, ada.email if n < 130 else sam.email, now - timedelta(hours=n)) for n in range(230)]
    api = FakeCommitsApi(items)

    summary = read(org, api)

    assert [p["page"] for _, p in api.requests] == [1, 2, 3]
    assert summary["member_commits"] == 230
    assert [(c["user_id"], c["commits"]) for c in summary["candidates"]] == [(ada.id, 130), (sam.id, 100)]


def test_commits_older_than_ninety_days_are_left_out_and_since_is_sent(db_session, make_org):
    org = make_org("win-old")
    ada = make_user(db_session, org, "Ada", "Obi")
    now = datetime.utcnow()
    items = [record(n, ada.email, now - timedelta(days=1 + n)) for n in range(3)]
    items += [record(10 + n, ada.email, now - timedelta(days=120 + n)) for n in range(5)]
    api = FakeCommitsApi(items, honour_since=False)

    summary = read(org, api)

    assert summary["member_commits"] == 3
    sent = datetime.strptime(api.requests[0][1]["since"], STAMP)
    assert abs((now - timedelta(days=90)) - sent) < timedelta(minutes=1)


def test_the_edge_of_the_window_is_a_minute_either_side_of_ninety_days(db_session, make_org):
    org = make_org("win-edge")
    ada = make_user(db_session, org, "Ada", "Obi")
    now = datetime.utcnow()
    inside = record(1, ada.email, now - timedelta(days=90) + timedelta(minutes=1))
    outside = record(2, ada.email, now - timedelta(days=90) - timedelta(minutes=1))

    summary = read(org, FakeCommitsApi([inside, outside], honour_since=False))

    assert summary["member_commits"] == 1
    assert summary["oldest"] == inside["sha"][:7]


@pytest.mark.parametrize("lead, other, owned", [(5, 5, False), (6, 5, True)])
def test_a_person_must_have_more_than_half_of_the_members_commits(db_session, make_org, lead, other, owned):
    from app.connectors import github
    from app.connectors.github_gaps import _commit_hint

    org = make_org(f"win-half-{lead}")
    ada, sam = make_user(db_session, org, "Ada", "Obi"), make_user(db_session, org, "Sam", "Lee")
    element, component = make_application(db_session, org)
    now = datetime.utcnow()
    items = [record(n, ada.email if n < lead else sam.email, now - timedelta(hours=n)) for n in range(lead + other)]

    summary = read(org, FakeCommitsApi(items))

    assert github.apply_commit_history(org.id, element, component, summary) is owned
    if owned:
        assert [r.user_id for r in primaries(db_session, component)] == [ada.id]
    else:
        assert primaries(db_session, component) == []
        assert _commit_hint(element, org.id)["note"] == "Recent commits are shared: Ada Obi (5), Sam Lee (5)."


def test_choosing_from_stale_commits_leaves_an_owned_application_with_its_one_owner(
    db_session, make_org, client, login_as
):
    from app.connectors import github
    from app.services.element_ownership_service import set_application_owner
    from tests.test_github_owner_from_commits import commits

    org, other = make_org("win-owned"), make_org("win-owned-b")
    ada, sam = make_user(db_session, org, "Ada", "Obi"), make_user(db_session, org, "Sam", "Lee")
    make_user(db_session, other, "Zed", "Ng")
    element, component = make_application(db_session, org)
    github.apply_commit_history(
        org.id, element, component, github.summarise_commits(org.id, REPOSITORY, commits((ada.email, 6), (sam.email, 6))))
    set_application_owner(component, sam.id, "primary", organization_id=org.id)
    db_session.commit()
    login_as(client, ada)

    response = client.post(
        f"/applications/{component.id}/owners/from-commits", data={"user_id": ada.id}, follow_redirects=True)

    assert response.status_code == 200
    assert "already has a primary owner, Sam Lee" in response.get_data(as_text=True)
    assert [r.user_id for r in primaries(db_session, component)] == [sam.id]
