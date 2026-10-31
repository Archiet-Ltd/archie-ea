"""The capability record lists only mappings from the capability's own organisation.

Another organisation's mapping can carry the same capability id (the mapping row stores a plain id). The
record page must never list that application.
"""

import uuid

import pytest


def _component(db_session, org_id, name):
    from app.models.application_layer import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _mapping(db_session, org_id, component_id, capability_id):
    from app.models.application_capability import ApplicationCapabilityMapping

    row = ApplicationCapabilityMapping(
        organization_id=org_id, application_component_id=component_id, business_capability_id=capability_id)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.mark.usefixtures("db_session")
def test_another_organisations_mapping_on_the_same_capability_is_not_listed(app, db_session, make_org):
    from app import db
    from app.models.capability_models import BusinessCapability
    from app.services.capability_fact_sheet import _realizing_apps

    suffix = uuid.uuid4().hex[:6]
    own, other = make_org("fs-own"), make_org("fs-other")
    cap = BusinessCapability(name="Scope %s" % suffix, organization_id=own.id, category="Operations", level=1)
    db_session.add(cap)
    db_session.flush()
    mine = _component(db_session, own.id, "Own App %s" % suffix)
    theirs = _component(db_session, other.id, "Foreign App %s" % suffix)
    _mapping(db_session, own.id, mine.id, cap.id)
    # The ORM refuses a mapping whose application and capability sit in different organisations, so the
    # foreign row is written as a stored row would exist after a bad import: by SQL.
    db_session.execute(
        db.text("INSERT INTO application_capability_mapping (organization_id, application_component_id, "
                "business_capability_id) VALUES (:o, :a, :c)"),
        {"o": other.id, "a": theirs.id, "c": cap.id})
    db_session.flush()

    names = [a["name"] for a in _realizing_apps(cap.id, own.id)]
    assert names == ["Own App %s" % suffix]


@pytest.mark.usefixtures("db_session")
def test_a_capability_or_mapping_with_no_organisation_cannot_be_stored(app, db_session, make_org):
    """The no-organisation case the record page's filter could meet never reaches it: the store refuses it.

    business_capability.organization_id and application_capability_mapping.organization_id are NOT NULL, so
    the page is never asked to list mappings for a capability with no organisation. This test pins that, so
    a later change that relaxes either column has to decide the page's behaviour on purpose.
    """
    from sqlalchemy.exc import IntegrityError

    from app.models.capability_models import BusinessCapability

    cap = BusinessCapability(name="No org %s" % uuid.uuid4().hex[:6], organization_id=None,
                             category="Operations", level=1)
    db_session.add(cap)
    with pytest.raises(IntegrityError):
        db_session.flush()
