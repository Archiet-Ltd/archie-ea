"""REQ 2763 / WP 2895: no confirmed stakeholder is orphaned.

A confirmed stakeholder must link to a Driver, a Goal, or the capability it
serves. ``find_orphaned_stakeholders`` (app/services/motivation_integrity_service.py)
flags one that has no such relationship.
"""

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _element(db_session, org, name, layer="Motivation", kind="Stakeholder", **cols):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org.id, **cols)
    db_session.add(el)
    db_session.flush()
    return el


def _link(db_session, org, source, target, kind="association"):
    from app.models.archimate_core import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=kind, organization_id=org.id
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _stakeholder(db_session, org, name, element):
    from app.models.motivation import Stakeholder

    stakeholder = Stakeholder(
        name=name, organization_id=org.id, archimate_element_id=element.id
    )
    db_session.add(stakeholder)
    db_session.flush()
    return stakeholder


def test_confirmed_stakeholder_with_no_motivation_link_is_orphaned(db_session, make_org):
    from app.services.motivation_integrity_service import find_orphaned_stakeholders

    org = make_org("orphan-stakeholder")
    element = _element(db_session, org, "Chief Financial Officer")
    stakeholder = _stakeholder(db_session, org, "Chief Financial Officer", element)

    result = find_orphaned_stakeholders(org.id)

    assert result["count"] >= 1
    assert any(row["id"] == stakeholder.id for row in result["orphaned"])


def test_linking_to_a_driver_clears_the_orphan_flag(db_session, make_org):
    from app.services.motivation_integrity_service import find_orphaned_stakeholders

    org = make_org("linked-stakeholder")
    stakeholder_element = _element(db_session, org, "Regulatory Body")
    stakeholder = _stakeholder(db_session, org, "Regulatory Body", stakeholder_element)

    # Still orphaned before the link exists.
    before = find_orphaned_stakeholders(org.id)
    assert any(row["id"] == stakeholder.id for row in before["orphaned"])

    driver_element = _element(
        db_session, org, "Regulatory compliance mandate", kind="Driver"
    )
    _link(db_session, org, stakeholder_element, driver_element)

    after = find_orphaned_stakeholders(org.id)

    assert after["count"] == 0
    assert not any(row["id"] == stakeholder.id for row in after["orphaned"])


def test_draft_stakeholder_concept_has_no_status_column_yet():
    """Documents the model gap this check works around (see TODO in
    app/services/motivation_integrity_service.py): the enterprise Stakeholder
    model has no confirmed/draft status column, so the check treats every
    row in `stakeholders` as confirmed rather than inventing one."""
    from app.models.motivation import Stakeholder

    assert not hasattr(Stakeholder, "status")
    assert not hasattr(Stakeholder, "confirmed")
