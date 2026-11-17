"""An application read from GitHub gets an owner from its recent commits when one member leads them."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

REPOSITORY = "acme/archie-ea"


def make_user(db_session, org, first, last, role="application_manager"):
    from app.models.user import User

    user = User(
        email=f"{first.lower()}-{uuid.uuid4().hex[:8]}@example.com", first_name=first, last_name=last,
        organization_id=org.id, confirmed=True, enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def make_application(db_session, org):
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_backbone import create_backbone_element
    from app.connectors.github import stamp_origin

    name = f"Archie Ea {uuid.uuid4().hex[:6]}"
    element = create_backbone_element(
        element_type="Application", layer="Application", name=name, organization_id=org.id)
    stamp_origin(element, repository=REPOSITORY, commit="abc1234def", file="Dockerfile")
    component = ApplicationComponent(name=name, organization_id=org.id, archimate_element_id=element.id)
    db_session.add(component)
    db_session.flush()
    return element, component


def commits(*pairs):
    """A page of GitHub commit records: ``("ada@x", n)`` makes n commits by that e-mail, newest first."""
    out = []
    for email, count in pairs:
        for _ in range(count):
            number = len(out)
            out.append({"sha": f"{number:07x}aaaa", "commit": {"author": {"email": email, "date": f"2026-10-{28 - number % 27:02d}T10:00:00Z"}}})
    return out


def primaries(db_session, component):
    from app.models.application_owner import ApplicationOwner

    return ApplicationOwner.query.filter_by(
        application_id=component.id, organization_id=component.organization_id, ownership_type="primary").all()


def test_one_member_who_leads_the_commits_becomes_the_owner_with_the_source(db_session, make_org):
    from app.connectors import github
    from app.connectors.github_gaps import owner_source_note

    org = make_org("own-lead")
    ada, sam = make_user(db_session, org, "Ada", "Obi"), make_user(db_session, org, "Sam", "Lee")
    element, component = make_application(db_session, org)
    summary = github.summarise_commits(org.id, REPOSITORY, commits((ada.email, 9), (sam.email, 3), ("stranger@example.org", 40)))
    assert summary["member_commits"] == 12

    assert github.apply_commit_history(org.id, element, component, summary) is True

    rows = primaries(db_session, component)
    assert [(r.user_id, r.source_table) for r in rows] == [(ada.id, "github_connector")]
    note = owner_source_note(component, [{"user_id": ada.id, "ownership_type": "primary"}])
    assert note.startswith("From commit history: 9 of 12 commits in the last 90 days")
    assert REPOSITORY in note and " to " in note


def test_two_members_who_share_the_commits_are_both_offered_and_nobody_is_made_owner(db_session, make_org):
    from app.connectors import github
    from app.connectors.github_gaps import _commit_hint

    org = make_org("own-share")
    ada, sam = make_user(db_session, org, "Ada", "Obi"), make_user(db_session, org, "Sam", "Lee")
    element, component = make_application(db_session, org)
    summary = github.summarise_commits(org.id, REPOSITORY, commits((ada.email, 10), (sam.email, 10)))

    assert github.apply_commit_history(org.id, element, component, summary) is False

    assert primaries(db_session, component) == []
    hint = _commit_hint(element, org.id)
    assert hint["note"] == "Recent commits are shared: Ada Obi (10), Sam Lee (10)."
    assert [c["user_id"] for c in hint["candidates"]] == [ada.id, sam.id]


def test_no_committer_who_is_a_member_says_why(db_session, make_org):
    from app.connectors import github
    from app.connectors.github_gaps import _commit_hint

    org = make_org("own-none")
    element, component = make_application(db_session, org)
    summary = github.summarise_commits(org.id, REPOSITORY, commits(("stranger@example.org", 5)))

    assert github.apply_commit_history(org.id, element, component, summary) is False

    assert primaries(db_session, component) == []
    hint = _commit_hint(element, org.id)
    assert hint == {"note": "GitHub names no single code owner of archie-ea, and no one author leads its commits in the last 90 days."}


def test_an_owner_a_person_recorded_is_never_overwritten(db_session, make_org):
    from app.connectors import github
    from app.services.element_ownership_service import set_application_owner

    org = make_org("own-kept")
    ada, sam = make_user(db_session, org, "Ada", "Obi"), make_user(db_session, org, "Sam", "Lee")
    element, component = make_application(db_session, org)
    set_application_owner(component, sam.id, "primary", organization_id=org.id)
    db_session.flush()
    summary = github.summarise_commits(org.id, REPOSITORY, commits((ada.email, 9)))

    assert github.apply_commit_history(org.id, element, component, summary) is False

    assert [(r.user_id, r.source_table) for r in primaries(db_session, component)] == [(sam.id, None)]

