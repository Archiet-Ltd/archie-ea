"""The layered diagram with nothing searched for draws one connected piece of the model.

The Architecture Elements page opens its Diagram View with no search term. It used to draw the first thirty
elements in alphabetical order, which in a real model are almost never related to each other, so the picture
was thirty boxes and one line. It now starts from the most connected element and grows along real
relationships, so the elements drawn are the ones that are related.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.user import User

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label):
    user = User(email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", first_name="Diagram", last_name=label,
                organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect")
    user.password = "Sup3rSecret!23"
    db_session.add(user)
    db_session.flush()
    return user


def _chain(db_session, org_id, prefix):
    """Four related elements across four layers, named to sort after the unrelated ones."""
    from flask import g

    g.current_org_id = org_id
    layers = [("Capability", "strategy"), ("BusinessService", "business"),
              ("ApplicationComponent", "application"), ("Node", "technology")]
    rows = [ArchiMateElement(name=f"{prefix} {kind}", type=kind, layer=layer) for kind, layer in layers]
    db_session.add_all(rows)
    db_session.flush()
    for upper, lower in zip(rows, rows[1:]):
        db_session.add(ArchiMateRelationship(source_id=lower.id, target_id=upper.id, type="Serving"))
    db_session.flush()
    return [r.id for r in rows]


def test_diagram_without_a_search_draws_the_related_elements_of_this_organisation_only(
        app, db_session, make_org, client, login_as):
    mine, theirs = make_org("diagramconnected-a"), make_org("diagramconnected-b")
    user = _user(db_session, mine, "A")

    with app.test_request_context():
        from flask import g

        g.current_org_id = mine.id
        db_session.add_all([ArchiMateElement(name=f"Alpha loose {i:02d}", type="Goal", layer="motivation")
                            for i in range(12)])
        db_session.flush()
        chain = _chain(db_session, mine.id, "Zeta")
        other = _chain(db_session, theirs.id, "Zeta other")

    login_as(client, user)
    data = client.get("/api/archimate/viewpoints/layered/diagram?limit=4").get_json()

    assert {e["id"] for e in data["elements"]} == set(chain)
    assert data["relationship_count"] == 3
    assert {e["layer"] for e in data["elements"]} == {"strategy", "business", "application", "technology"}
    assert not {e["id"] for e in data["elements"]} & set(other)


def test_diagram_without_relationships_still_lists_elements_by_name(app, db_session, make_org, client, login_as):
    org = make_org("diagramconnected-c")
    user = _user(db_session, org, "C")
    with app.test_request_context():
        from flask import g

        g.current_org_id = org.id
        db_session.add_all([ArchiMateElement(name=n, type="Goal", layer="motivation") for n in ("B goal", "A goal")])
        db_session.flush()

    login_as(client, user)
    data = client.get("/api/archimate/viewpoints/layered/diagram?limit=30").get_json()

    assert [e["name"] for e in data["elements"]] == ["A goal", "B goal"]
    assert data["relationship_count"] == 0


def test_diagram_names_its_layer_order_and_the_page_draws_its_bands_in_it(app, db_session, make_org, client, login_as):
    from pathlib import Path

    from app.services.archimate_viewpoint_service import STANDARD_VIEWPOINTS

    org = make_org("diagramconnected-d")
    user = _user(db_session, org, "D")
    with app.test_request_context():
        from flask import g

        g.current_org_id = org.id
        _chain(db_session, org.id, "Order")
    login_as(client, user)
    data = client.get("/api/archimate/viewpoints/layered/diagram?limit=4").get_json()

    # strategy before motivation: the viewpoint's own order, sent with the diagram
    assert data["layer_order"] == STANDARD_VIEWPOINTS["layered"]["layer_order"]
    assert data["layer_order"].index("strategy") < data["layer_order"].index("motivation")
    page = (Path(__file__).resolve().parents[1] / "app/templates/architecture/elements.html").read_text()
    assert "data.layer_order" in page and "'motivation', 'strategy'" not in page
