"""The questions the chat offers first are answered from the same links the overview counts.

An organisation that has just connected GitHub is offered "What breaks if <application> fails?"
for its applications. An application that runs on, or is part of, something else has nothing
depending on it, yet the overview counts its links. Its answer names those links, each with the
repository and file it was read from, instead of saying nothing is connected.
"""

import re

import pytest

from tests.test_github_builds_the_model import (  # noqa: F401
    acme,
    approved,
    connect,
    first_sync,
    github,
    org_a,
    org_b,
    people,
    sync,
)

pytestmark = pytest.mark.usefixtures("db_session")


def _applications(org_id):
    from app.models import ArchiMateElement

    return ArchiMateElement.query.filter(
        ArchiMateElement.organization_id == org_id, ArchiMateElement.type.in_(("ApplicationComponent", "Application"))
    ).all()


def _links_touching(org_id, element_id):
    """The distinct things the element is linked to, at either end of a relationship (two
    relationships between the same pair are one connection in an answer)."""
    from app.models import ArchiMateRelationship as R

    rows = R.query.filter(
        R.organization_id == org_id, (R.source_id == element_id) | (R.target_id == element_id)
    ).all()
    return len({r.target_id if r.source_id == element_id else r.source_id for r in rows})


def _ask(client, name):
    reply = client.post("/ai-chat/estate-answer", json={"message": "What breaks if %s fails?" % name, "remember": False})
    assert reply.status_code == 200, reply.data[:300]
    body = reply.get_json()
    assert body["found"], name
    return body["html"]


def _rows(html):
    return re.findall(r'<dd class="text-foreground" data-citation>\s*(.*?)\s*(?:<a|</dd>)', html, re.S)


def test_every_offered_question_cites_a_source_and_never_says_nothing_is_connected(
    db_session, org_a, org_b, approved, people, login_as, client, tenant_ctx
):
    from app.models import ArchiMateElement, ArchiMateRelationship

    # A second organisation holds a same-named application and a link of its own.
    db_session.commit()
    with tenant_ctx(org_b.id):
        mine = ArchiMateElement(name="Payments Worker", type="ApplicationComponent", layer="Application", organization_id=org_b.id)
        other = ArchiMateElement(name="Other Org Secret Queue", type="ApplicationComponent", layer="Application", organization_id=org_b.id)
        db_session.add_all([mine, other])
        db_session.flush()
        db_session.add(ArchiMateRelationship(type="Serving", source_id=other.id, target_id=mine.id, organization_id=org_b.id))
        db_session.commit()

    with tenant_ctx(org_a.id):
        apps = [(a.id, a.name, _links_touching(org_a.id, a.id)) for a in _applications(org_a.id)]
    apps = [app_row for app_row in apps if app_row[2]]  # the overview counts a link for each of these
    assert len(apps) >= 3
    login_as(client, people.admin)
    upstream_only = 0
    for _id, name, links in apps:
        login_as(client, people.admin)
        html = _ask(client, name)
        assert "Nothing is recorded as connected to this yet" not in html, name
        assert "Other Org Secret Queue" not in html
        citations = _rows(html)
        assert citations, "%s: no source cited" % name
        assert all(c and c != "Source not recorded" for c in citations), citations
        if "It is itself connected to others" in html:
            upstream_only += 1
            stated = int(re.search(r"itself connected to others: (\d+) connections? in your records", html).group(1))
            assert stated == links == len(citations), (name, stated, links, citations)
            assert "data-depends-on" in html and "is connected to" in html
    assert upstream_only >= 1, "the seeded organisation has an application with nothing depending on it"


def test_another_organisation_asking_the_same_name_sees_only_its_own_links(
    db_session, org_a, org_b, approved, people, login_as, client, tenant_ctx
):
    from app.models import ArchiMateElement, ArchiMateRelationship
    from tests.test_connector_approval_flow import _make_admin

    db_session.commit()
    with tenant_ctx(org_b.id):
        worker = ArchiMateElement(name="Payments Worker", type="ApplicationComponent", layer="Application", organization_id=org_b.id)
        platform = ArchiMateElement(name="Org B Platform", type="ApplicationComponent", layer="Application", organization_id=org_b.id)
        db_session.add_all([worker, platform])
        db_session.flush()
        db_session.add(ArchiMateRelationship(type="Composition", source_id=platform.id, target_id=worker.id, organization_id=org_b.id))
        admin_b = _make_admin(db_session, org_b.id, "FounderB")
        db_session.commit()
    login_as(client, admin_b)
    html = _ask(client, "Payments Worker")
    assert "Org B Platform" in html
    assert "Payments Service" not in html and "acme/" not in html


def test_the_offered_questions_are_about_linked_applications_most_connected_first_and_only_the_organisations_own(
    db_session, org_a, org_b, approved, people, login_as, client, tenant_ctx
):
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.modules.dashboard.services.overview_figures import _relationships
    from datetime import datetime, timezone
    from tests.test_connector_approval_flow import _make_admin

    db_session.commit()
    with tenant_ctx(org_b.id):
        lone = ArchiMateElement(name="Org B Only App", type="ApplicationComponent", layer="Application", organization_id=org_b.id)
        peer = ArchiMateElement(name="Org B Peer App", type="ApplicationComponent", layer="Application", organization_id=org_b.id)
        db_session.add_all([lone, peer])
        db_session.flush()
        db_session.add(ArchiMateRelationship(type="Serving", source_id=lone.id, target_id=peer.id, organization_id=org_b.id))
        admin_b = _make_admin(db_session, org_b.id, "FounderB")
        db_session.commit()
    with tenant_ctx(org_a.id):
        overview_top = _relationships(org_a.id, datetime.now(timezone.utc))["top"]
        linked = {a.name for a in _applications(org_a.id) if _links_touching(org_a.id, a.id)}
        every = {a.name for a in _applications(org_a.id)}
        assert linked < every, "the seeded organisation has an application with no recorded link"

    login_as(client, people.admin)
    offered = client.get("/ai-chat/first-questions").get_json()["questions"]
    names = [re.match(r"What breaks if (.*) fails\?", q["question"]).group(1) for q in offered]
    assert 1 <= len(names) <= 4
    assert all(n in linked for n in names), "a question about an application with no recorded link"
    # the overview's most connected element leads, with the overview's own count
    if overview_top["name"] in every:
        assert names[0] == overview_top["name"]
        assert offered[0]["detail"] == "%d relationships recorded" % overview_top["count"]
    assert not any("Org B" in n for n in names)

    login_as(client, admin_b)
    theirs = client.get("/ai-chat/first-questions").get_json()["questions"]
    assert [q["question"] for q in theirs] and all("Org B" in q["question"] for q in theirs)
    assert not any("Payments" in q["question"] for q in theirs)
