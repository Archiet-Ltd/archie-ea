"""Work package detail page and the Gap column on the Work Packages list.

A work package opens on its own page showing its recorded progress, the gaps it
addresses, the plateaus it realises and its owner area; the list names the gap
beside each package and shows an em dash, never 0, for progress nobody recorded.
Another organisation's package is a 404 and its gaps never appear.
"""

from __future__ import annotations

import pytest

from tests.test_work_package_round3 import _gap, _org_with_user, _plateau

pytestmark = pytest.mark.usefixtures("db_session")


def _package(org, name, **fields):
    from app.services import work_package_service as svc

    return svc.create_work_package(organization_id=org.id, name=name, **fields)


def test_detail_shows_gap_plateau_progress_and_owner_panel(db_session, make_org, client, login_as):
    org, user = _org_with_user(db_session, make_org, "wpd-a")
    gap, plateau = _gap(db_session, org, "Billing gap"), _plateau(db_session, org, "Target state")
    wp = _package(org, "Billing rework", gap_id=gap.id, plateau_id=plateau.id,
                  progress_percentage=40.0, assigned_to="Ivo Reed")

    login_as(client, user)
    resp = client.get("/enterprise/implementation/work-packages/%d" % wp.id)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Billing rework" in html
    assert gap.name in html
    assert plateau.name in html
    assert "40%" in html
    assert 'data-testid="owner-panel"' in html
    assert 'data-testid="work-package-assignee">Ivo Reed<' in html


def test_detail_shows_dash_when_progress_not_recorded(db_session, make_org, client, login_as):
    org, user = _org_with_user(db_session, make_org, "wpd-b")
    wp = _package(org, "Unmeasured package")
    wp.progress_percentage = None
    db_session.flush()

    login_as(client, user)
    html = client.get("/enterprise/implementation/work-packages/%d" % wp.id).get_data(as_text=True)

    assert 'data-testid="work-package-progress">—<' in html
    assert 'data-testid="work-package-progress"' in html
    assert ">0%<" not in html


def test_detail_is_404_for_another_organisation(db_session, make_org, client, login_as):
    org, user = _org_with_user(db_session, make_org, "wpd-c")
    other = make_org("wpd-other")
    theirs = _package(other, "Their secret package", gap_id=_gap(db_session, other, "Their gap").id)

    login_as(client, user)

    assert client.get("/enterprise/implementation/work-packages/%d" % theirs.id).status_code == 404


def test_list_api_names_gaps_and_returns_null_for_unrecorded_progress(
        db_session, make_org, client, login_as):
    org, user = _org_with_user(db_session, make_org, "wpd-d")
    other = make_org("wpd-e")
    gap = _gap(db_session, org, "Listed gap")
    linked = _package(org, "Linked package", gap_id=gap.id, progress_percentage=65.0)
    bare = _package(org, "Bare package")
    bare.progress_percentage = None
    foreign = _package(other, "Foreign package", gap_id=_gap(db_session, other, "Foreign gap").id)
    db_session.flush()

    login_as(client, user)
    body = client.get("/enterprise/api/work-packages?per_page=100").get_json()
    rows = {row["id"]: row for row in body["work_packages"]}

    assert rows[linked.id]["gaps"] == [gap.name]
    assert rows[linked.id]["percent_complete"] == 65.0
    assert rows[bare.id]["gaps"] == []
    assert rows[bare.id]["percent_complete"] is None
    assert foreign.id not in rows
    assert "Foreign gap" not in str(body)
