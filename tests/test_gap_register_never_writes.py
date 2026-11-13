"""Reading the Gaps never writes a record, a Gap element gets its record from its writer, and one Gap element
never has two records. Two organisations: each one's elements stay its own."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.usefixtures("db_session")

PAGE = "/enterprise/implementation/gap-analysis"


def _user_in(db_session, org, role_name, enterprise_role):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="nw.%s@example.com" % uuid.uuid4().hex[:8], first_name="N", last_name="W",
        organization_id=org.id, enterprise_role=enterprise_role, confirmed=True,
    )
    user.role = Role.query.filter_by(name=role_name).one()
    user.password = "Passw0rd!x1"
    db_session.add(user)
    db_session.flush()
    return user


def _bare_gap_element(db_session, org, name):
    """A Gap element stored without the model writer (a plain insert), so it has no record."""
    from app.models.archimate_core import ArchiMateElement

    table = ArchiMateElement.__table__
    row = db_session.execute(table.insert().values(
        name=name, type="Gap", layer="Implementation", organization_id=org.id, scope="enterprise",
    ).returning(table.c.id)).scalar_one()
    return row


def _gap_rows(db_session, *orgs):
    """Gap rows of these organisations, counted in SQL so no request's organisation filter applies."""
    from sqlalchemy import text

    return db_session.execute(
        text("SELECT count(*) FROM gaps WHERE organization_id = ANY(:ids)"), {"ids": [o.id for o in orgs]}
    ).scalar_one()


def test_no_way_of_reading_the_register_writes_a_record(client, login_as, db_session, make_org):
    from app.services.gap_register_service import count_gaps, list_gaps

    mine, theirs = make_org("nw-mine"), make_org("nw-theirs")
    tag = uuid.uuid4().hex[:6]
    _bare_gap_element(db_session, mine, "Imported gap %s" % tag)
    _bare_gap_element(db_session, theirs, "Their gap %s" % tag)
    architect = _user_in(db_session, mine, "Architect", "enterprise_architect")
    viewer = _user_in(db_session, mine, "Viewer", "viewer")
    db_session.commit()
    before = _gap_rows(db_session, mine, theirs)
    assert before == 0

    for person in (architect, viewer):
        login_as(client, person)
        assert client.get(PAGE).status_code == 200
        assert client.get(PAGE + "?severity=high").status_code == 200
        assert client.get("/api/roadmap/gaps").status_code == 200
        assert client.get("/implementation/reports/generate").status_code == 200
        assert _gap_rows(db_session, mine, theirs) == before, "a read by %s wrote a record" % person.enterprise_role
    count_gaps(mine.id)
    count_gaps(theirs.id, severity="critical")
    list_gaps(mine.id)
    from app.services import gap_register_service

    gap_register_service.gap_register_query(mine.id).all()
    assert _gap_rows(db_session, mine, theirs) == before


def test_a_gap_element_gets_its_record_in_its_own_organisation_when_it_is_written(db_session, make_org):
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import Gap

    mine, theirs = make_org("nw-w-mine"), make_org("nw-w-theirs")
    elements = {}
    for org in (mine, theirs):
        element = ArchiMateElement(
            name="Gap of %s" % org.slug, type="Gap", layer="Implementation", organization_id=org.id,
            source_kind="import")
        db_session.add(element)
        elements[org.id] = element
    db_session.commit()

    for org in (mine, theirs):
        record = Gap.query.filter_by(archimate_element_id=elements[org.id].id).one()
        assert record.organization_id == org.id
        assert record.name == "Gap of %s" % org.slug
        assert (record.priority, record.severity, record.impact) == (None, None, None)
        assert record.resolution_status == "identified"
    assert Gap.query.filter_by(organization_id=mine.id).count() == 1
    assert Gap.query.filter_by(organization_id=theirs.id).count() == 1


def test_a_second_record_for_one_gap_element_is_refused(db_session, make_org):
    from app.models.implementation_migration import Gap

    org, other = make_org("nw-dup"), make_org("nw-dup-other")
    element_id = _bare_gap_element(db_session, org, "Once")
    db_session.add(Gap(organization_id=org.id, name="Once", archimate_element_id=element_id))
    db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(Gap(organization_id=org.id, name="Twice", archimate_element_id=element_id))
            db_session.flush()
    # gaps with no element are not limited, and the other organisation's own gap is unaffected
    db_session.add_all([Gap(organization_id=org.id, name="Free one"), Gap(organization_id=org.id, name="Free two"),
                        Gap(organization_id=other.id, name="Theirs")])
    db_session.flush()
    assert Gap.query.filter_by(archimate_element_id=element_id).count() == 1
