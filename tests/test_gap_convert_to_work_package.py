"""Convert to Work Package on the Gap Analysis screen creates a work package and the screen shows it."""

from __future__ import annotations

import pytest

from tests.test_gap_work_package_link import _gap, _user, _wp, world  # noqa: F401

pytestmark = pytest.mark.usefixtures("db_session")


def test_convert_creates_a_linked_work_package_the_screen_then_lists(db_session, world, client, login_as):  # noqa: F811
    from app.services import work_package_service

    gap = _gap(db_session, world["a"], "Convert me")
    login_as(client, world["editor_a"])
    resp = client.post(f"/capability-map/api/roadmap/gaps/{gap.id}/work-packages", json={"template": "auto"})
    body = resp.get_json()
    assert resp.status_code in (200, 201), body
    assert body.get("success") is not False, body

    linked = work_package_service.work_packages_for_gaps([gap.id], world["a"].id).get(gap.id, [])
    assert linked, "no work package is linked to the gap after converting it"

    login_as(client, world["editor_a"])
    html = client.get("/enterprise/implementation/gap-analysis").get_data(as_text=True)
    assert f'data-testid="gap-work-packages-{gap.id}"' in html
    assert linked[0].name in html


def test_convert_in_one_organisation_never_reaches_another(db_session, world, client, login_as):  # noqa: F811
    from app.services import work_package_service

    gap_a = _gap(db_session, world["a"], "Org A gap")
    login_as(client, world["editor_b"])
    resp = client.post(f"/capability-map/api/roadmap/gaps/{gap_a.id}/work-packages", json={"template": "auto"})
    assert resp.status_code == 404, resp.get_json()
    assert work_package_service.work_packages_for_gaps([gap_a.id], world["a"].id).get(gap_a.id, []) == []


def test_a_viewer_cannot_convert_a_gap_and_no_work_package_is_written(db_session, world, client, login_as):  # noqa: F811
    from app.services import work_package_service

    gap = _gap(db_session, world["a"], "Viewer must not convert")
    db_session.commit()
    before = work_package_service.query_for(world["a"].id).count()
    login_as(client, world["viewer_a"])
    resp = client.post(f"/capability-map/api/roadmap/gaps/{gap.id}/work-packages", json={"template": "auto"})
    assert resp.status_code == 403, resp.get_json()
    assert work_package_service.query_for(world["a"].id).count() == before
    assert work_package_service.work_packages_for_gaps([gap.id], world["a"].id).get(gap.id, []) == []
