"""A Gap element made in the Composer, by an import or on the create form has its one gap record as soon as it
is saved, before anyone reads the register, and the Gaps page then lists it once. Two organisations: one
organisation's Gap never becomes the other's."""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db_session")

PAGE = "/enterprise/implementation/gap-analysis"


def _architect(db_session, org):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="gw.%s@example.com" % uuid.uuid4().hex[:8], first_name="G", last_name="W",
        organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "Passw0rd!x1"
    db_session.add(user)
    db_session.commit()
    return user


def _records(db_session, name):
    """(organisation, element, rating) of every gap record with this name, read in SQL so no
    request's organisation filter applies and nothing reads the register."""
    return db_session.execute(text(
        "SELECT g.organization_id, g.archimate_element_id, g.priority FROM gaps g WHERE g.name = :n"
    ), {"n": name}).all()


def _oef(name):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-m">'
        "<name>Import</name><elements>"
        '<element identifier="g1" xsi:type="Gap"><name>%s</name></element>'
        "</elements><relationships/></model>" % name
    )


def _write(client, how, name):
    if how == "composer":
        reply = client.post("/api/architecture-assistant/create-element",
                            json={"name": name, "type": "Gap", "layer": "Implementation"})
        assert reply.get_json()["status"] == "created", reply.get_data(as_text=True)
    elif how == "composer canvas":
        reply = client.post("/archimate/api/elements/materialize", json={"elements": [
            {"element_id": "__builtin__g1", "name": name, "el_type": "Gap", "layer": "implementation"}]})
        assert reply.status_code == 200, reply.get_data(as_text=True)
    elif how == "import":
        reply = client.post("/architecture/import/oef", json={"xml_content": _oef(name)})
        assert reply.get_json()["created"] == 1, reply.get_data(as_text=True)
    else:
        reply = client.post("/architecture/implementation/Gap/new",
                            json={"name": name, "description": "d", "priority": "high", "resolution_status": ""})
        assert reply.status_code == 200, reply.get_data(as_text=True)


@pytest.mark.parametrize("how", ["composer", "composer canvas", "import", "create form"])
def test_a_gap_saved_anywhere_has_one_record_at_once_and_is_listed(client, login_as, db_session, make_org, how):
    mine, theirs = make_org("gw-mine"), make_org("gw-theirs")
    tag = uuid.uuid4().hex[:6]
    name, their_name = "Saved by %s %s" % (how, tag), "Theirs by %s %s" % (how, tag)

    login_as(client, _architect(db_session, theirs))
    _write(client, how, their_name)
    login_as(client, _architect(db_session, mine))
    _write(client, how, name)

    # The record is there before anything reads the register: one, in its own organisation, on its element.
    rows = _records(db_session, name)
    assert len(rows) == 1, rows
    org_id, element_id, rating = rows[0]
    assert org_id == mine.id and element_id is not None
    # The create form rates its gap; a Gap saved anywhere else is stored unrated.
    assert rating == ("high" if how == "create form" else None)
    owner = db_session.execute(text("SELECT organization_id FROM archimate_elements WHERE id = :i"),
                               {"i": element_id}).scalar_one()
    assert owner == mine.id
    assert [r[0] for r in _records(db_session, their_name)] == [theirs.id]

    page = client.get(PAGE)
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page.get_data(as_text=True)))
    assert page.status_code == 200
    assert name in plain and their_name not in plain
    assert len(_records(db_session, name)) == 1
