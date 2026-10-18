"""The regulatory compliance dashboard (a sidebar destination) loads for a signed-in person.

The navigation check fails when a route that is in a persona's sidebar is never
requested by any test; this is that request, with the page content checked and
the signed-out case refused.
"""

from __future__ import annotations

import pytest

from tests.test_work_package_round3 import _org_with_user

pytestmark = pytest.mark.usefixtures("db_session")


def test_dashboard_loads_for_a_signed_in_person(db_session, make_org, client, login_as):
    _, user = _org_with_user(db_session, make_org, "cfd-a")
    login_as(client, user)

    resp = client.get("/dashboard/compliance")

    assert resp.status_code == 200
    assert "Regulatory Compliance" in resp.get_data(as_text=True)


def test_dashboard_needs_a_signed_in_person(client):
    resp = client.get("/dashboard/compliance")

    assert resp.status_code in (301, 302, 401, 403)
