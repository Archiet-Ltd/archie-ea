"""The one helper that gives an element a connector has made its inventory row."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _element(org):
    from app.services.archimate_backbone import create_backbone_element

    return create_backbone_element(
        element_type="ApplicationComponent",
        layer="Application",
        name=f"Billing {uuid.uuid4().hex[:6]}",
        organization_id=org.id,
    )


def _elements_named(org, name):
    from app.models.archimate_core import ArchiMateElement

    return ArchiMateElement.query.filter_by(organization_id=org.id, name=name).count()


def test_called_twice_gives_one_row_and_no_second_element(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent
    from app.services.connector_framework import ensure_application_row

    org = make_org("rows")
    element = _element(org)

    first = ensure_application_row(org.id, element, element.name, "Takes payments")
    second = ensure_application_row(org.id, element, element.name, "Takes payments")
    db_session.flush()

    assert first.id == second.id
    assert first.archimate_element_id == element.id
    assert first.organization_id == org.id
    assert ApplicationComponent.query.filter_by(organization_id=org.id, archimate_element_id=element.id).count() == 1
    assert _elements_named(org, element.name) == 1


def test_another_organisations_element_is_refused(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent
    from app.services.connector_framework import ConnectorWriteRefused, ensure_application_row

    org_a = make_org("rows-a")
    org_b = make_org("rows-b")
    element_b = _element(org_b)

    with pytest.raises(ConnectorWriteRefused):
        ensure_application_row(org_a.id, element_b, element_b.name, None)

    assert ApplicationComponent.query.filter_by(archimate_element_id=element_b.id).count() == 0
