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
            stated = int(re.search(r"itself connected to others: (\d+) other elements?", html).group(1))
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


def test_the_card_the_answer_and_the_overview_give_one_relationship_figure_for_every_linked_application(
    db_session, org_a, org_b, approved, people, login_as, client, tenant_ctx
):
    """A card counts relationships; an answer counts the elements they reach. The answer states
    the card's figure, from the overview's own reader, and says what it is made of."""
    from app.modules.dashboard.services.overview_figures import relationship_ends

    db_session.commit()
    with tenant_ctx(org_a.id):
        ends = relationship_ends(org_a.id)
        named = {a.id: a.name for a in _applications(org_a.id) if ends.get(a.id)}
    login_as(client, people.admin)
    offered = client.get("/ai-chat/first-questions").get_json()["questions"]
    assert offered and all(q["name"] and q["question"] == "What breaks if %s fails?" % q["name"] for q in offered)
    by_name = {name: ends[element_id] for element_id, name in named.items()}
    assert "Payments Worker" in by_name
    for name, count in by_name.items():
        login_as(client, people.admin)
        html = _ask(client, name)
        stated = re.search(r"has (\d+) relationships? recorded:", html)
        assert stated, name
        assert int(stated.group(1)) == count, (name, stated.group(1), count)
    for question in offered:
        assert question["detail"] == "%d relationship%s recorded" % (
            by_name[question["name"]],
            "" if by_name[question["name"]] == 1 else "s",
        )


def test_a_reload_keeps_the_answer_that_named_what_the_element_is_connected_to(
    db_session, org_a, org_b, approved, people, login_as, client, tenant_ctx
):
    from app.models.conversation import ConversationMessageRecord as ConversationMessage

    db_session.commit()
    login_as(client, people.admin)
    body = client.post("/ai-chat/estate-answer", json={"message": "What breaks if Payments Worker fails?"}).get_json()
    assert "data-depends-on" in body["html"]
    kept = [
        m.content
        for m in ConversationMessage.query.filter_by(thread_id=body["thread_id"]).all()
        if "Answer from your records" in (m.content or "")
    ]
    assert kept, "the answer was not kept"
    assert "0 connections" not in kept[-1], kept[-1]
    assert re.search(r"connected to [1-9]\d* other element", kept[-1]), kept[-1]
    assert "[Open this answer](/intelligence/answers/" in kept[-1]


def test_more_than_the_shown_limit_of_connections_says_how_many_are_cut_off_when_nothing_depends_on_it(
    db_session, org_a, approved, people, login_as, client, tenant_ctx
):
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.modules.ai_chat.routes.estate_answer_routes import SHOWN_LIMIT

    db_session.commit()
    with tenant_ctx(org_a.id):
        hub = ArchiMateElement(name="Hub Of Many", type="ApplicationComponent", layer="Application", organization_id=org_a.id)
        db_session.add(hub)
        db_session.flush()
        for i in range(SHOWN_LIMIT + 3):
            part = ArchiMateElement(name="Hub Part %02d" % i, type="ApplicationComponent", layer="Application", organization_id=org_a.id)
            db_session.add(part)
            db_session.flush()
            db_session.add(ArchiMateRelationship(type="Composition", source_id=part.id, target_id=hub.id, organization_id=org_a.id))
        db_session.commit()
    login_as(client, people.admin)
    html = _ask(client, "Hub Of Many")
    assert "data-depends-on" in html
    assert "Showing %d of %d" % (SHOWN_LIMIT, SHOWN_LIMIT + 3) in html


def _sentence(html):
    """The figures sentence of an answer, as text, or None."""
    found = re.search(r"<span data-recorded-links>(.*?)</span>", html, re.S)
    return " ".join(found.group(1).split()) if found else None


def _split_by_hand(org_id, element_id):
    """From the stored relationships, with no help from the product: (total, from it, to it, other elements)."""
    from app.models import ArchiMateRelationship as R

    rows = R.query.filter(R.organization_id == org_id, (R.source_id == element_id) | (R.target_id == element_id)).all()
    from_it = [r for r in rows if r.source_id == element_id]
    to_it = [r for r in rows if r.source_id != element_id]
    others = {r.target_id if r.source_id == element_id else r.source_id for r in rows} - {element_id}
    return len(rows), len(from_it), len(to_it), len(others)


def _plural(n, one, many):
    return "%d %s" % (n, one if n == 1 else many)


def test_the_sentence_under_the_figures_is_true_for_one_connection_and_four_relationships(
    db_session, org_a, org_b, approved, people, login_as, client, tenant_ctx
):
    """Customer Portal has four relationships and one thing that depends on it. The sentence says how
    the four divide, in numbers read from the stored links, whatever the answer lists above it."""
    from app.models import ArchiMateElement as E
    from app.models import ArchiMateRelationship as R

    db_session.commit()

    def make(org_id, name):
        element = E(name=name, type="ApplicationComponent", layer="Application", organization_id=org_id)
        db_session.add(element)
        db_session.flush()
        return element

    with tenant_ctx(org_a.id):
        portal = make(org_a.id, "Customer Portal Fanin")
        feeders = [make(org_a.id, "Feeder %d" % i) for i in range(3)]
        billing = make(org_a.id, "Billing Fanout")
        for feeder in feeders:
            db_session.add(R(type="Serving", source_id=feeder.id, target_id=portal.id, organization_id=org_a.id))
        db_session.add(R(type="Serving", source_id=portal.id, target_id=billing.id, organization_id=org_a.id))
        portal_id = portal.id
        db_session.commit()
    with tenant_ctx(org_b.id):
        # the same name in another organisation, with a different shape, must not change the sentence
        theirs = make(org_b.id, "Customer Portal Fanin")
        for i in range(2):
            db_session.add(R(type="Serving", source_id=theirs.id, target_id=make(org_b.id, "Theirs %d" % i).id, organization_id=org_b.id))
        db_session.commit()
    login_as(client, people.admin)
    html = _ask(client, "Customer Portal Fanin")
    assert "1 connection in your records" in html
    assert _sentence(html) == (
        "Customer Portal Fanin has 4 relationships recorded: 1 leads from it and 3 lead to it, reaching 4 other elements."
    ), _sentence(html)
    assert "your overview gives" not in html and "also count the systems" not in html
    assert _split_by_hand(org_a.id, portal_id) == (4, 1, 3, 4)


def test_the_sentence_agrees_with_the_stored_links_for_every_linked_application(
    db_session, org_a, org_b, approved, people, login_as, client, tenant_ctx
):
    from app.modules.dashboard.services.overview_figures import relationship_ends

    db_session.commit()
    with tenant_ctx(org_a.id):
        ends = relationship_ends(org_a.id)
        linked = [(a.id, a.name) for a in _applications(org_a.id) if ends.get(a.id)]
        expected = {name: _split_by_hand(org_a.id, element_id) for element_id, name in linked}
    assert linked
    for name, (total, from_it, to_it, others) in expected.items():
        login_as(client, people.admin)
        said = _sentence(_ask(client, name))
        assert said == "%s has %s recorded: %d %s from it and %d %s to it, reaching %s." % (
            name,
            _plural(total, "relationship", "relationships"),
            from_it,
            "leads" if from_it == 1 else "lead",
            to_it,
            "leads" if to_it == 1 else "lead",
            _plural(others, "other element", "other elements"),
        ), (name, said)


def _app(db_session, org_id, name, *, own=False):
    from app.models import ArchiMateElement as E
    from app.models.application_portfolio import ApplicationComponent

    element = E(name=name, type="ApplicationComponent", layer="Application", organization_id=org_id)
    db_session.add(element)
    db_session.flush()
    if own:
        db_session.add(ApplicationComponent(name=name, organization_id=org_id, archimate_element_id=element.id))
    return element


def _link(db_session, org_id, source, target):
    from app.models import ArchiMateRelationship as R

    db_session.add(R(type="Serving", source_id=source.id, target_id=target.id, organization_id=org_id))


def test_a_card_opens_the_answer_about_the_element_it_names_when_two_share_the_name(
    db_session, org_a, org_b, approved, people, login_as, client, tenant_ctx
):
    """Two elements named Stock, the older with no link. The card names the linked one and the answer
    is about that one. Another organisation's Stock, with links of its own, is never reached."""
    from tests.test_connector_approval_flow import _make_admin

    db_session.commit()
    with tenant_ctx(org_b.id):
        older = _app(db_session, org_b.id, "Stock Twin")
        newer = _app(db_session, org_b.id, "Stock Twin")
        carts = _app(db_session, org_b.id, "Carts Peer")
        _link(db_session, org_b.id, newer, carts)
        older_id, newer_id = older.id, newer.id
        admin_b = _make_admin(db_session, org_b.id, "FounderB")
        db_session.commit()
    with tenant_ctx(org_a.id):
        theirs = _app(db_session, org_a.id, "Stock Twin")
        _link(db_session, org_a.id, theirs, _app(db_session, org_a.id, "Org A Peer Of Stock"))
        theirs_id = theirs.id
        db_session.commit()
    assert older_id < newer_id
    login_as(client, admin_b)
    cards = [q for q in client.get("/ai-chat/first-questions").get_json()["questions"] if q["name"] == "Stock Twin"]
    assert [c["element_id"] for c in cards] == [newer_id]
    assert cards[0]["detail"] == "1 relationship recorded"
    reply = client.post(
        "/ai-chat/estate-answer", json={"message": cards[0]["question"], "element_id": cards[0]["element_id"], "remember": False}
    ).get_json()
    assert "Carts Peer" in reply["html"]
    assert "Nothing is recorded as connected" not in reply["html"]
    assert "Org A Peer Of Stock" not in reply["html"]
    assert _sentence(reply["html"]).startswith("Stock Twin has 1 relationship recorded: 1 leads from it and 0 lead to it")
    # another organisation's element id is not honoured: the answer stays within this organisation
    foreign = client.post(
        "/ai-chat/estate-answer", json={"message": cards[0]["question"], "element_id": theirs_id, "remember": False}
    ).get_json()
    assert "Org A Peer Of Stock" not in foreign["html"]
    # the question typed by hand, with no card, is still answered by name
    typed = client.post("/ai-chat/estate-answer", json={"message": cards[0]["question"], "remember": False}).get_json()
    assert typed["found"]


def test_one_application_recorded_twice_is_offered_once_as_the_element_with_more_relationships_and_on_a_tie_its_own(
    db_session, org_a, org_b, approved, people, login_as, client, tenant_ctx
):
    from tests.test_connector_approval_flow import _make_admin

    db_session.commit()
    with tenant_ctx(org_b.id):
        # The copy has two relationships and the application's own element one: the copy is the one the name means.
        copy = _app(db_session, org_b.id, "Orders Twin")
        own = _app(db_session, org_b.id, "Orders Twin", own=True)
        hub = _app(db_session, org_b.id, "Orders Hub")
        extra = _app(db_session, org_b.id, "Orders Extra")
        _link(db_session, org_b.id, copy, hub)
        _link(db_session, org_b.id, copy, extra)
        _link(db_session, org_b.id, own, hub)
        copy_id = copy.id
        # Level on relationships, the application's own element is the one the name means.
        tied_copy = _app(db_session, org_b.id, "Billing Twin")
        tied_own = _app(db_session, org_b.id, "Billing Twin", own=True)
        _link(db_session, org_b.id, tied_copy, hub)
        _link(db_session, org_b.id, tied_own, extra)
        tied_own_id = tied_own.id
        admin_b = _make_admin(db_session, org_b.id, "FounderB")
        db_session.commit()
    with tenant_ctx(org_a.id):
        _link(db_session, org_a.id, _app(db_session, org_a.id, "Orders Twin"), _app(db_session, org_a.id, "Orders Other Peer"))
        db_session.commit()
    login_as(client, admin_b)
    offered = client.get("/ai-chat/first-questions").get_json()["questions"]
    cards = [q for q in offered if q["name"] == "Orders Twin"]
    assert len(cards) == 1, cards
    assert cards[0]["element_id"] == copy_id
    assert cards[0]["detail"] == "2 relationships recorded"
    tied = [q for q in offered if q["name"] == "Billing Twin"]
    assert len(tied) == 1 and tied[0]["element_id"] == tied_own_id, tied
    assert tied[0]["detail"] == "1 relationship recorded"


def test_an_organisation_with_applications_and_no_links_is_told_how_many_applications_it_has(
    db_session, org_a, org_b, approved, people, login_as, client, tenant_ctx
):
    from tests.test_connector_approval_flow import _make_admin

    db_session.commit()
    with tenant_ctx(org_b.id):
        # org_b has applications and no link between any of them
        for name in ("Lone One", "Lone Two", "Lone Three"):
            _app(db_session, org_b.id, name)
        admin_b = _make_admin(db_session, org_b.id, "FounderB")
        db_session.commit()
    login_as(client, admin_b)
    assert client.get("/ai-chat/first-questions").get_json() == {"questions": [], "applications": 3}
    login_as(client, people.admin)
    assert client.get("/ai-chat/first-questions").get_json()["applications"] >= 3
