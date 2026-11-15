"""On a product's page, Add joins an element the organisation already holds, once, and never one from another organisation.

Two real organisations: the second holds an element named like the first's, and the first must neither see it
in search nor be able to join it by id.
"""

from __future__ import annotations

import uuid

import pytest

from app.models import ArchiMateElement, ArchiMateRelationship
from app.models.archimate_missing_elements import Product
from app.models.user import User

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org):
    user = User(email=f"join-{uuid.uuid4().hex[:10]}@example.com", first_name="Jo", last_name="Join",
                organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect")
    db_session.add(user)
    db_session.flush()
    return user


def _el(db_session, org, name, type_, layer):
    row = ArchiMateElement(organization_id=org.id, name=name, type=type_, layer=layer)
    db_session.add(row)
    db_session.flush()
    return row


def _estate(db_session, org, name):
    product = _el(db_session, org, name, "Product", "business")
    db_session.add(Product(organization_id=org.id, name=name, archimate_element_id=product.id))
    existing = _el(db_session, org, name, "ApplicationComponent", "Application")
    db_session.flush()
    return product, existing


def _post(client, product, **data):
    data.setdefault("section", "application")
    return client.post(f"/architecture/products/{product.id}/elements", data=data, follow_redirects=True)


def _joins(org, product, target):
    return ArchiMateRelationship.query.filter_by(
        organization_id=org.id, source_id=product.id, target_id=target.id).count()


def test_choosing_an_existing_element_joins_it_and_creates_nothing(app, db_session, make_org, client, login_as):
    org = make_org("join-a")
    product, existing = _estate(db_session, org, "Ifiok")
    login_as(client, _user(db_session, org))
    before = ArchiMateElement.query.filter_by(organization_id=org.id).count()
    response = _post(client, product, existing_id=existing.id)
    assert "joined to" in response.get_data(as_text=True)
    assert _joins(org, product, existing) == 1
    assert ArchiMateElement.query.filter_by(organization_id=org.id).count() == before


def test_joining_the_same_element_twice_says_so_and_adds_no_second_relationship(
        app, db_session, make_org, client, login_as):
    org = make_org("join-twice")
    product, existing = _estate(db_session, org, "Ifiok")
    user = _user(db_session, org)
    login_as(client, user)
    _post(client, product, existing_id=existing.id)
    login_as(client, user)
    again = _post(client, product, existing_id=existing.id)
    assert "already joined" in again.get_data(as_text=True)
    assert _joins(org, product, existing) == 1


def test_search_offers_only_this_organisations_elements_of_the_layer_types(
        app, db_session, make_org, client, login_as):
    mine, theirs = make_org("join-mine"), make_org("join-theirs")
    _estate(db_session, mine, "Ifiok")
    _el(db_session, theirs, "Ifiok Their Secret", "ApplicationComponent", "Application")
    _el(db_session, mine, "Ifiok Goal", "Goal", "Motivation")
    login_as(client, _user(db_session, mine))
    found = client.get("/archimate/api/elements/search?q=Ifiok&types=ApplicationComponent,DataObject&similar=1").get_json()
    names = [row["name"] for row in found["data"]]
    assert "Ifiok" in names
    assert not any("Their Secret" in n or n == "Ifiok Goal" for n in names)
    near = client.get("/archimate/api/elements/search?q=Ifiokk&types=ApplicationComponent&similar=1").get_json()
    assert [row["name"] for row in near["data"]] == []
    assert [row["name"] for row in near["similar"]] == ["Ifiok"]


def test_cross_org_joining_another_organisations_element_by_id_is_refused(
        app, db_session, make_org, client, login_as):
    mine, theirs = make_org("join-x-a"), make_org("join-x-b")
    product, _ = _estate(db_session, mine, "Ifiok")
    _, their_element = _estate(db_session, theirs, "Ifiok")
    login_as(client, _user(db_session, mine))
    refused = _post(client, product, existing_id=their_element.id)
    assert "already holds" in refused.get_data(as_text=True)
    assert ArchiMateRelationship.query.filter_by(target_id=their_element.id).count() == 0
    assert ArchiMateRelationship.query.filter_by(source_id=product.id).count() == 0
