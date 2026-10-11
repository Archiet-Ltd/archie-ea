"""flask backfill-meaning-tenancy (R1-B81): derive a Meaning's
organisation from its linked ArchiMateElement; an unlinked row, or one
whose element has no organisation, stays NULL."""
from __future__ import annotations

import uuid

import pytest

from app.commands.backfill_meaning_tenancy import _backfill


def test_backfill_derives_organisation_from_linked_element(app, db_session, make_org):
    from app.models import ArchiMateElement
    from app.models.motivation import Meaning

    org = make_org("backfill-meaning")
    element = ArchiMateElement(
        name=f"El {uuid.uuid4().hex[:6]}", type="BusinessObject", layer="business",
        organization_id=org.id,
    )
    db_session.add(element)
    db_session.flush()

    meaning = Meaning(name=f"Term {uuid.uuid4().hex[:6]}", archimate_element_id=element.id)
    meaning.organization_id = None
    db_session.add(meaning)
    db_session.commit()

    with app.app_context():
        derived, orphans = _backfill(dry_run=False)

    db_session.expire_all()
    reloaded = Meaning.query.get(meaning.id)
    assert reloaded.organization_id == org.id
    assert derived >= 1


def test_a_meaning_with_no_element_link_stays_null(app, db_session, make_org):
    """A legacy row from before organization_id existed on this table --
    simulated with a raw INSERT, since the ORM's own after_insert sync
    (R1-B81) now requires an organisation to create the mirrored
    ArchiMateElement row and so cannot construct this state itself."""
    from sqlalchemy import text

    from app.models.motivation import Meaning

    name = f"Orphan {uuid.uuid4().hex[:6]}"
    db_session.execute(
        text("INSERT INTO meanings (name, organization_id) VALUES (:name, NULL)"),
        {"name": name},
    )
    db_session.commit()

    with app.app_context():
        _backfill(dry_run=False)

    db_session.expire_all()
    reloaded = Meaning.query.filter_by(name=name).one()
    assert reloaded.organization_id is None


def test_dry_run_changes_nothing(app, db_session, make_org):
    from app.models import ArchiMateElement
    from app.models.motivation import Meaning

    org = make_org("backfill-meaning-dry")
    element = ArchiMateElement(
        name=f"El {uuid.uuid4().hex[:6]}", type="BusinessObject", layer="business",
        organization_id=org.id,
    )
    db_session.add(element)
    db_session.flush()
    meaning = Meaning(name=f"Term {uuid.uuid4().hex[:6]}", archimate_element_id=element.id)
    meaning.organization_id = None
    db_session.add(meaning)
    db_session.commit()

    with app.app_context():
        _backfill(dry_run=True)

    db_session.expire_all()
    reloaded = Meaning.query.get(meaning.id)
    assert reloaded.organization_id is None
