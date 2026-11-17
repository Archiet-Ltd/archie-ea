"""The two slash commands that take an element's name answer a recorded name with the card's figure.

Two organisations each record Billing, with a deleted neighbour: the first has three live links and
one deleted, the second one live and one deleted. ``/dependencies Billing`` lists the live neighbours,
``/portfolio-check Billing`` states the same relationship count as the overview and the question
cards, and a partial or unknown name is answered with the closest recorded names, each one a command
to run. The overview's "added in the last 30 days" never exceeds its total.
"""

import re
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _org(db_session, org, live, deleted, extra_names=()):
    from app.models import ArchiMateElement, ArchiMateRelationship

    def add(name, kind="ApplicationService"):
        element = ArchiMateElement(name=name, type=kind, layer="Application", organization_id=org.id)
        db_session.add(element)
        db_session.flush()
        return element

    billing = add("Billing", "ApplicationComponent")
    for name in extra_names:
        add(name, "ApplicationComponent")
    gone = []
    for index in range(live):
        peer = add("%s live %d" % (org.name, index))
        db_session.add(ArchiMateRelationship(type="Serving", source_id=billing.id, target_id=peer.id, organization_id=org.id))
    for index in range(deleted):
        peer = add("%s gone %d" % (org.name, index))
        peer.deleted_at = datetime.now(timezone.utc)
        gone.append(peer.id)
        db_session.add(ArchiMateRelationship(type="Serving", source_id=billing.id, target_id=peer.id, organization_id=org.id))
    db_session.flush()
    return billing.id


@pytest.fixture
def world(db_session, make_org):
    one, two = make_org("slash-one"), make_org("slash-two")
    first = _org(db_session, one, 3, 1, extra_names=("Billing Gateway", "Ledger"))
    second = _org(db_session, two, 1, 1)
    db_session.commit()
    return {"one": one, "two": two, "first": first, "second": second}


def _run(command, text, org_id, tenant_ctx):
    from app.modules.ai_chat.services.command_parser_service import CommandParserService

    service = CommandParserService()
    with tenant_ctx(org_id):
        parsed = service.parse("%s %s" % (command, text))
        assert parsed["valid"], parsed
        return service.execute(parsed["command"], parsed["args"], None, "architecture")["response"]


def test_dependencies_answers_a_recorded_name_in_any_case(world, tenant_ctx):
    for org_key, live in (("one", 3), ("two", 1)):
        org = world[org_key]
        for typed in ("Billing", "billing", "BILLING"):
            text = _run("/dependencies", typed, org.id, tenant_ctx)
            assert "Element not found" not in text and "Nothing recorded" not in text, (org_key, typed, text)
            assert text.count("%s live" % org.name) == live, (org_key, typed, text)
            assert "gone" not in text, (org_key, typed, text)


def test_portfolio_check_gives_the_figure_the_card_and_the_answer_give(world, tenant_ctx):
    from app.modules.dashboard.services.overview_figures import relationship_ends
    from app.modules.intelligence.services.query_catalogue import _what_breaks_if_fails

    for org_key, key, live in (("one", "first", 3), ("two", "second", 1)):
        org = world[org_key]
        with tenant_ctx(org.id):
            card = relationship_ends(org.id)[world[key]]
            answer = _what_breaks_if_fails(org.id, service="Billing")["total"]
        text = _run("/portfolio-check", "Billing", org.id, tenant_ctx)
        assert card == answer == live
        assert "**Relationships:** %d" % card in text, text
        assert "**Outgoing Relationships:** %d" % card in text, text
        assert "gone" not in text and "**Incoming Relationships:** 0" in text, text
        assert "**1st Degree Impacted:** %d" % card in text, text


@pytest.mark.parametrize("command", ["/dependencies", "/portfolio-check"])
def test_a_partial_name_is_answered_with_the_closest_recorded_names_as_commands(world, tenant_ctx, command):
    org = world["one"]
    text = _run(command, "Billin", org.id, tenant_ctx)
    assert "Nothing recorded is called `Billin`." in text, text
    offered = re.findall(r"- `(/[a-z-]+ [^`]+)`", text)
    assert "%s Billing" % command in offered and "%s Billing Gateway" % command in offered, text
    assert "%s Ledger" % command not in offered, text
    assert "Type `%s` and one of these names, or click one." % command in text, text
    # the other organisation records no Billing Gateway, so it is never offered there
    other = _run(command, "Billin", world["two"].id, tenant_ctx)
    assert "Billing Gateway" not in other, other


@pytest.mark.parametrize("command", ["/dependencies", "/portfolio-check"])
def test_a_name_nothing_resembles_says_what_to_type(world, tenant_ctx, command):
    text = _run(command, "Zephyr Quillborne", world["one"].id, tenant_ctx)
    assert "Nothing similar is recorded. Type `%s` and the name of a recorded element." % command in text, text
    assert "- `/" not in text, text


def test_the_thirty_day_figure_counts_current_relationships_and_never_exceeds_the_total(world, db_session, tenant_ctx):
    from app.models import ArchiMateRelationship
    from app.models.audit_log import AuditLog
    from app.modules.dashboard.services.overview_figures import _relationships

    org = world["one"]
    with tenant_ctx(org.id):
        now = datetime.now(timezone.utc)
        # audit creates for relationships since removed or to deleted elements must not be counted
        for _ in range(5):
            db_session.add(
                AuditLog(organization_id=org.id, table_name="archimate_relationships", action="create", created_at=now - timedelta(days=2))
            )
        old = ArchiMateRelationship.query.filter_by(organization_id=org.id).first()
        old.created_at = (now - timedelta(days=90)).replace(tzinfo=None)
        db_session.flush()
        figures = _relationships(org.id, now)
        assert figures["total"] == 3
        assert figures["added_30d"] <= figures["total"]
        assert figures["added_30d"] == 2
