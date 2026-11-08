"""Compliance Tracking: a tile that counts zero is not drawn in a warning colour, and says why it is zero."""

from __future__ import annotations

import re

import pytest

from tests.test_compliance_one_answer import _world

pytestmark = pytest.mark.usefixtures("db_session")


def _tile(html, title):
    """The markup of the tile headed ``title``, up to the next tile."""
    start = html.index(">%s<" % title)
    tail = html[start:]
    end = tail.find('data-slot="card"', 20)
    return tail[: end if end > 0 else 1500]


def test_a_zero_tile_is_neutral_and_explains_itself(db_session, make_org, client, login_as):
    from app.models.application_compliance import ApplicationComplianceControl

    org, user, _cap, _bare, _controls, app_a = _world(db_session, make_org, "zero")
    for link in ApplicationComplianceControl.query.filter_by(organization_id=org.id, application_id=app_a.id):
        link.implementation_status = "implemented"
    db_session.flush()
    login_as(client, user)
    html = client.get("/strategic/compliance-tracking").get_data(as_text=True)

    for title in ("Controls Not In Place", "Not Compliant"):
        tile = _tile(html, title)
        assert re.search(r">\s*0\s*<", tile), tile
        assert "text-destructive" not in tile and "text-warning" not in tile, "%s: a zero drawn in a warning colour" % title
    assert "Every control mapped to an application is in place" in _tile(html, "Controls Not In Place")
    assert "No capability is below" in _tile(html, "Not Compliant")


def test_a_non_zero_tile_keeps_its_warning_colour(db_session, make_org, client, login_as):
    org, user, *_ = _world(db_session, make_org, "some")
    login_as(client, user)
    html = client.get("/strategic/compliance-tracking").get_data(as_text=True)
    assert "text-destructive" in _tile(html, "Controls Not In Place")
