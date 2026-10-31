"""Journey: a business architect opens the value streams list and reaches a stream's capability grid.

The list used to link the grid through an endpoint that is not registered, so
rendering the page raised BuildError and the whole list answered 500. The
journey signs in, opens the list, follows the grid link the row offers and
asserts the landing page shows that stream, its stages and the grid card the
link points at. An organisation with no value streams gets the page too.
"""

import re
import uuid

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey


def _seed_stream(app, org_id, name):
    from flask import g

    from app import db
    from app.models.unified_capability import ValueStreamStage
    from app.modules.capabilities.services import value_stream_service as vs_service

    with app.test_request_context():
        g.current_org_id = org_id
        vs = vs_service.create_value_stream({"name": name, "code": "VS-%s" % uuid.uuid4().hex[:6]})
        db.session.add_all([
            ValueStreamStage(name="Identify need", value_stream_id=vs.id, stage_order=1),
            ValueStreamStage(name="Fulfil order", value_stream_id=vs.id, stage_order=2),
        ])
        db.session.commit()
        return vs.id


def test_value_streams_list_links_each_stream_to_its_capability_grid(app, client):
    from app import db

    with app.app_context():
        org_id = make_org(db, "Value Streams")
        user_id = make_user(db, org_id, "ba", enterprise_role="business_architect",
                            role_name="Administrator")
        name = "Order to cash %s" % uuid.uuid4().hex[:6]
        vs_id = _seed_stream(app, org_id, name)

    login(client, user_id)

    listing = client.get("/value-streams/")
    assert listing.status_code == 200
    html = listing.get_data(as_text=True)
    assert name in html

    grid_links = re.findall(r'href="(/value-streams/%d#capability-grid)"' % vs_id, html)
    assert grid_links, "the row offers no link to the capability grid"

    detail = client.get(grid_links[0].split("#")[0])
    assert detail.status_code == 200
    page = detail.get_data(as_text=True)
    assert name in page
    assert "Identify need" in page and "Fulfil order" in page
    assert 'id="capability-grid"' in page


def test_value_streams_list_for_an_organisation_with_none_still_renders(app, client):
    from app import db

    with app.app_context():
        org_id = make_org(db, "No Streams")
        user_id = make_user(db, org_id, "ba", enterprise_role="business_architect",
                            role_name="Administrator")

    login(client, user_id)
    response = client.get("/value-streams/")
    assert response.status_code == 200
    assert "Value stream" in response.get_data(as_text=True)
