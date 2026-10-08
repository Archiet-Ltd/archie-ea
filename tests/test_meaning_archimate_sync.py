"""create_meaning_archimate (app/models/motivation.py) must persist
archimate_element_id to the actual meanings row, not just the in-memory
object.

Bug found 6 Oct 2026: the listener ran on "after_insert" and only set
target.archimate_element_id as a plain Python attribute -- after_insert
fires once the row has already been INSERTed, so that assignment was
never written back. Every Meaning created since this listener shipped
(PR #370) had archimate_element_id = NULL in the database despite the
in-memory object showing a value, until something re-fetched the row
fresh.
"""
from __future__ import annotations

import uuid

from app.models.archimate_core import ArchiMateElement
from app.models.motivation import Meaning


def test_a_new_meanings_archimate_element_id_survives_a_fresh_reload(
    app, db_session, make_org
):
    org = make_org("meaning-archimate-sync")
    meaning = Meaning(name=f"Term {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(meaning)
    db_session.flush()

    # The bug only showed on a fresh read from the database, not on the
    # same in-memory object that created the row.
    db_session.expire_all()
    reloaded = Meaning.query.filter_by(id=meaning.id).one()

    assert reloaded.archimate_element_id is not None
    element = ArchiMateElement.query.get(reloaded.archimate_element_id)
    assert element is not None
    assert element.type == "Meaning"
    assert element.layer == "Motivation"
    assert element.organization_id == org.id
