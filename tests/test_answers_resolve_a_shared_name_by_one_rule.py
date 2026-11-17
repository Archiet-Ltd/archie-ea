"""Every answer path names the same element for a shared name, and a deleted element is never counted.

An organisation records two elements called Billing (one link and five links) and two called
orders-api (none and three). The card, the typed fallback, the catalogue, the assistant's tool
resolver and both slash commands must pick the one with the most links, however the name is
cased, spaced or hyphenated. A relationship to a deleted element counts nowhere, and an id
outside the database's range is ignored like any invalid id. A second organisation holding the
same names never changes the first's answers.
"""

from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _build(db_session, org, ends):
    """Elements by key, with a link from each key in *ends* to as many fresh peers as given."""
    from app.models import ArchiMateElement, ArchiMateRelationship

    ids = {}
    for key, name, links in ends:
        element = ArchiMateElement(name=name, type="ApplicationComponent", layer="Application", organization_id=org.id)
        db_session.add(element)
        db_session.flush()
        ids[key] = element.id
        for index in range(links):
            peer = ArchiMateElement(
                name="%s peer %d" % (key, index), type="ApplicationService", layer="Application", organization_id=org.id
            )
            db_session.add(peer)
            db_session.flush()
            ids["%s_peer_%d" % (key, index)] = peer.id
            db_session.add(
                ArchiMateRelationship(type="Serving", source_id=element.id, target_id=peer.id, organization_id=org.id)
            )
    db_session.flush()
    return ids


@pytest.fixture
def world(db_session, make_org):
    one, two = make_org("one"), make_org("two")
    ids = _build(
        db_session,
        one,
        [("billing_few", "Billing", 1), ("billing_many", "Billing", 5), ("orders_old", "orders-api", 0), ("orders_new", "orders-api", 3)],
    )
    # The other organisation: the same names, the opposite way round.
    theirs = _build(db_session, two, [("billing_few", "Billing", 5), ("billing_many", "Billing", 1)])
    db_session.commit()
    return {"one": one, "two": two, "ids": ids, "theirs": theirs}


NAMES = ("Billing", "billing", "BILLING", "orders-api", "Orders-API", "orders api", "orders_api")


def _expected(world, name):
    return world["ids"]["billing_many" if name.lower() == "billing" else "orders_new"]


def test_the_one_rule_names_the_element_with_the_most_links_however_the_name_is_written(world, tenant_ctx):
    from app.modules.dashboard.services.overview_figures import element_named

    with tenant_ctx(world["one"].id):
        for name in NAMES:
            assert element_named(world["one"].id, name) == _expected(world, name), name
        assert element_named(world["one"].id, "Nothing like it") is None
    with tenant_ctx(world["two"].id):
        assert element_named(world["two"].id, "billing") == world["theirs"]["billing_few"]
        assert element_named(world["two"].id, "orders-api") is None


def test_the_catalogue_the_tool_resolver_and_both_slash_commands_pick_the_cards_element(world, tenant_ctx):
    from app.modules.ai_chat.services.command_parser_service import _element_meant_by
    from app.modules.ai_chat.tools.resolver import EntityResolver
    from app.models import ArchiMateElement
    from app.modules.dashboard.services.overview_figures import relationship_ends
    from app.modules.intelligence.services.query_catalogue import _resolve_service_element, _what_breaks_if_fails

    with tenant_ctx(world["one"].id):
        ends = relationship_ends(world["one"].id)
        for name in NAMES:
            want = _expected(world, name)
            card = ends[want]
            assert _resolve_service_element(world["one"].id, name).id == want, name
            assert EntityResolver.resolve_archimate_element(name)["id"] == want, name
            assert _element_meant_by(name, ArchiMateElement).id == want, name
            assert _what_breaks_if_fails(world["one"].id, service=name)["total"] == card, (name, card)


def test_a_relationship_to_a_deleted_element_is_counted_and_listed_nowhere(world, tenant_ctx, db_session):
    from app.models import ArchiMateElement
    from app.modules.dashboard.services.overview_figures import _relationships, relationship_ends, relationship_split
    from app.modules.intelligence.services.query_catalogue import _what_breaks_if_fails

    org_id = world["one"].id
    with tenant_ctx(org_id):
        before_total = _relationships(org_id, datetime.now(timezone.utc))["total"]
        gone = db_session.get(ArchiMateElement, world["ids"]["orders_new_peer_0"])
        gone.deleted_at = datetime.now(timezone.utc)
        db_session.flush()
        want = world["ids"]["orders_new"]
        assert relationship_ends(org_id)[want] == 2
        assert relationship_split(org_id, want)["total"] == 2
        assert _relationships(org_id, datetime.now(timezone.utc))["total"] == before_total - 1
        answer = _what_breaks_if_fails(org_id, service="orders-api")
        assert answer["total"] == 2
        assert all(row.get("name") and row["name"] != "—" for row in answer["rows"]), answer["rows"]
        assert world["ids"]["orders_new_peer_0"] not in [row.get("element_id") for row in answer["rows"]]


def test_an_id_outside_the_database_range_is_ignored_like_any_invalid_id(world, tenant_ctx):
    from app.modules.ai_chat.services.estate_answer import owned_element_id

    org_id = world["one"].id
    with tenant_ctx(org_id):
        assert owned_element_id(world["ids"]["billing_many"], org_id) == world["ids"]["billing_many"]
        for bad in (2**31, 2**31 - 1 + 1, 2**70, -1, 0):
            assert owned_element_id(bad, org_id) is None, bad
