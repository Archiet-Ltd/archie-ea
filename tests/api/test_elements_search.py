"""GET /api/v1/elements/search with a read-scoped token: only the calling
organisation's elements, filtered by name, type and layer, refusal without
an organisation.
"""

from __future__ import annotations

import pytest

from app.models.archimate_core import ArchiMateElement


def _make_element(db_session, org_id, name, type_="ApplicationComponent", layer="application"):
    element = ArchiMateElement(
        name=name, type=type_, layer=layer, organization_id=org_id,
    )
    db_session.add(element)
    db_session.flush()
    return element


def _names(payload):
    return [item["name"] for item in payload["data"]["elements"]]


def test_search_returns_only_the_calling_organisation_s_elements(
    app, db_session, client, org_pair, reader_tokens
):
    org_a, org_b = org_pair
    token_a, token_b = reader_tokens[org_a.id], reader_tokens[org_b.id]

    _make_element(db_session, org_a.id, "Alpha One")
    _make_element(db_session, org_b.id, "Beta Two")

    from tests.api.conftest import clear_identity

    clear_identity(app)
    response = client.get(
        "/api/v1/elements/search?q=Alpha",
        headers={"Authorization": "Bearer %s" % token_a},
    )
    assert response.status_code == 200
    assert _names(response.get_json()) == ["Alpha One"]

    clear_identity(app)
    response = client.get(
        "/api/v1/elements/search?q=Alpha",
        headers={"Authorization": "Bearer %s" % token_b},
    )
    assert response.status_code == 200
    assert _names(response.get_json()) == []

    clear_identity(app)
    response = client.get(
        "/api/v1/elements/search?q=Beta",
        headers={"Authorization": "Bearer %s" % token_b},
    )
    assert response.status_code == 200
    assert _names(response.get_json()) == ["Beta Two"]


def test_search_filters_by_type_and_layer(
    app, db_session, client, org_pair, reader_tokens
):
    org_a, _org_b = org_pair
    token_a = reader_tokens[org_a.id]

    _make_element(db_session, org_a.id, "Alpha One", type_="ApplicationComponent", layer="application")
    _make_element(db_session, org_a.id, "Alpha Two", type_="Group", layer="motivation")

    from tests.api.conftest import clear_identity

    clear_identity(app)
    response = client.get(
        "/api/v1/elements/search?q=Alpha&type=ApplicationComponent",
        headers={"Authorization": "Bearer %s" % token_a},
    )
    assert response.status_code == 200
    assert _names(response.get_json()) == ["Alpha One"]

    clear_identity(app)
    response = client.get(
        "/api/v1/elements/search?q=Alpha&layer=motivation",
        headers={"Authorization": "Bearer %s" % token_a},
    )
    assert response.status_code == 200
    assert _names(response.get_json()) == ["Alpha Two"]


def test_search_refuses_without_an_organisation(app, client, org_pair, reader_tokens):
    _org_a, _org_b = org_pair
    _token_a = reader_tokens[org_pair[0].id]

    response = client.get("/api/v1/elements/search?q=Alpha")
    assert response.status_code == 401
    assert response.get_json()["success"] is False
