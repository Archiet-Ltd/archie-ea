"""The Gaps page lists every Gap element of the organisation, however it was made, and its summary line
speaks of the ratings the list shows. Two organisations: each sees only its own gaps."""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

PAGE = "/enterprise/implementation/gap-analysis"


@pytest.fixture
def _architect_in(db_session):
    from app.models.user import Role, User

    def _make(org):
        Role.insert_roles()
        user = User(
            email="gp.%s@example.com" % uuid.uuid4().hex[:8], first_name="G", last_name="P",
            organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True,
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = "Passw0rd!x1"
        db_session.add(user)
        db_session.flush()
        return user

    return _make


def _element(db_session, org, name, kind="Gap", source_kind="import"):
    """A Gap element made outside the gap pages: an ArchiMate element and nothing else."""
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(
        name=name, type=kind, layer="Implementation", organization_id=org.id, source_kind=source_kind)
    db_session.add(element)
    db_session.flush()
    return element


def _record(db_session, org, name, **fields):
    from app.models.implementation_migration import Gap

    gap = Gap(organization_id=org.id, name=name, **fields)
    db_session.add(gap)
    db_session.flush()
    return gap


def _text(response):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", response.get_data(as_text=True)))


def test_a_gap_element_made_outside_the_page_is_listed_with_its_own_page(client, login_as, db_session, make_org, _architect_in):
    mine, theirs = make_org("gaps-mine"), make_org("gaps-theirs")
    tag = uuid.uuid4().hex[:6]
    imported = "Billing not live %s" % tag
    foreign = "Their private gap %s" % tag
    element = _element(db_session, mine, imported)
    _element(db_session, theirs, foreign)
    login_as(client, _architect_in(mine))

    page = client.get(PAGE)

    assert page.status_code == 200
    text = _text(page)
    assert imported in text and "No gaps recorded yet" not in text
    assert foreign not in text
    from app.models.implementation_migration import Gap

    record = Gap.query.filter_by(archimate_element_id=element.id).one()
    assert record.organization_id == mine.id
    # Nobody rated it, so it is stored unrated and the page says so.
    assert (record.priority, record.severity, record.impact) == (None, None, None)
    assert "no gap has a severity yet" in text.lower()
    assert 'href="/architecture/implementation/Gap/%d"' % record.id in page.get_data(as_text=True)
    assert client.get("/architecture/implementation/Gap/%d" % record.id).status_code == 200
    # Reading again adopts nothing twice, and the other organisation gets none of mine.
    client.get(PAGE)
    assert Gap.query.filter_by(archimate_element_id=element.id).count() == 1
    assert Gap.query.filter_by(organization_id=theirs.id).count() == 0


def test_a_gap_made_on_the_page_and_an_element_are_each_listed_once(client, login_as, db_session, make_org, _architect_in):
    mine = make_org("gaps-once")
    tag = uuid.uuid4().hex[:6]
    element = _element(db_session, mine, "Made on the map %s" % tag)
    # the element's writer wrote its record with it; the person then rates that record on the page
    page_made = _element(db_session, mine, "Made on the page %s" % tag)
    from app.models.implementation_migration import Gap as _Gap

    rated = _Gap.query.filter_by(archimate_element_id=page_made.id).one()
    rated.priority = rated.severity = "high"
    db_session.flush()
    login_as(client, _architect_in(mine))

    text = _text(client.get(PAGE))

    assert text.count("Made on the map %s" % tag) >= 1
    assert text.count("Made on the page %s" % tag) >= 1
    from app.models.implementation_migration import Gap

    assert Gap.query.filter_by(organization_id=mine.id).count() == 2
    assert Gap.query.filter_by(archimate_element_id=element.id).count() == 1


def test_a_high_gap_is_in_the_summary_and_impact_is_not_claimed_for_it(client, login_as, db_session, make_org, _architect_in):
    mine = make_org("gaps-summary")
    tag = uuid.uuid4().hex[:6]
    _record(db_session, mine, "High one %s" % tag, priority="high", severity="high", impact="medium")
    _record(db_session, mine, "Medium one %s" % tag, priority="medium", severity="medium", impact="medium")
    login_as(client, _architect_in(mine))

    text = _text(client.get(PAGE)).lower()

    assert "1 rated high" in text and "1 rated medium" in text
    assert "every gap has medium impact" not in text


def test_every_gap_rated_high_says_so(client, login_as, db_session, make_org, _architect_in):
    mine = make_org("gaps-allhigh")
    _record(db_session, mine, "Only one %s" % uuid.uuid4().hex[:6], priority="high", severity="high", impact="medium")
    login_as(client, _architect_in(mine))

    text = _text(client.get(PAGE)).lower()

    assert "1 rated high" in text
    assert "every gap has medium impact" not in text
