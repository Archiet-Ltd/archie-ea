"""Roadmap plateau cards: one reader for a plateau's gaps and realising work packages.

A plateau's gaps are the gaps recorded against it in the gap register UNION the gaps of
the work packages that realise it, de-duplicated, and another organisation's rows never
appear. The "N gap(s) assigned" figure and the gap names come from this one reader.
"""

from __future__ import annotations

import pytest

from tests.test_work_package_round3 import _gap, _org_with_user, _plateau

pytestmark = pytest.mark.usefixtures("db_session")


def _wp(org, name, **links):
    from app.services import work_package_service

    return work_package_service.create_work_package(organization_id=org.id, name=name, **links)


def test_plateau_gaps_are_register_links_union_work_package_gaps(db_session, make_org):
    from app.services import gap_register_service

    org, _user = _org_with_user(db_session, make_org, "pgone")
    plateau, empty = _plateau(db_session, org), _plateau(db_session, org, "Bare plateau")
    registered, via_wp, both = _gap(db_session, org), _gap(db_session, org), _gap(db_session, org)
    gap_register_service.link_gap_to_plateau(registered.id, plateau.id, org.id)
    gap_register_service.link_gap_to_plateau(both.id, plateau.id, org.id)
    _wp(org, "Realises it", plateau_id=plateau.id, gap_id=via_wp.id)
    _wp(org, "Realises it too", plateau_id=plateau.id, gap_id=both.id)
    db_session.commit()

    out = gap_register_service.plateau_gaps_and_work_packages([plateau.id, empty.id], org.id)

    assert {g["id"] for g in out[plateau.id]["gaps"]} == {registered.id, via_wp.id, both.id}
    assert len(out[plateau.id]["gaps"]) == 3  # "both" is counted once
    assert sorted(w["name"] for w in out[plateau.id]["work_packages"]) == ["Realises it", "Realises it too"]
    assert out[empty.id] == {"gaps": [], "work_packages": []}


def test_other_organisation_never_appears(db_session, make_org):
    from app.services import gap_register_service

    org_a, _ua = _org_with_user(db_session, make_org, "pgtwoa")
    org_b, _ub = _org_with_user(db_session, make_org, "pgtwob")
    plateau_a, gap_a = _plateau(db_session, org_a), _gap(db_session, org_a)
    gap_b = _gap(db_session, org_b)
    gap_register_service.link_gap_to_plateau(gap_a.id, plateau_a.id, org_a.id)
    _wp(org_a, "Mine", plateau_id=plateau_a.id, gap_id=gap_a.id)
    _wp(org_b, "Theirs", gap_id=gap_b.id)
    db_session.commit()

    seen_by_b = gap_register_service.plateau_gaps_and_work_packages([plateau_a.id], org_b.id)
    assert seen_by_b[plateau_a.id] == {"gaps": [], "work_packages": []}
    seen_by_a = gap_register_service.plateau_gaps_and_work_packages([plateau_a.id], org_a.id)
    assert [g["id"] for g in seen_by_a[plateau_a.id]["gaps"]] == [gap_a.id]


def test_plateaus_api_carries_gaps_and_gap_names_on_work_packages(db_session, make_org, client, login_as):
    from app.services import gap_register_service

    org, user = _org_with_user(db_session, make_org, "pgapi")
    other, _other_user = _org_with_user(db_session, make_org, "pgapix")
    plateau, gap = _plateau(db_session, org), _gap(db_session, org)
    _plateau(db_session, other, "Foreign plateau")
    gap_register_service.link_gap_to_plateau(gap.id, plateau.id, org.id)
    wp = _wp(org, "Timeline row", plateau_id=plateau.id, gap_id=gap.id)
    db_session.commit()

    login_as(client, user)
    plateaus = client.get("/capability-map/api/roadmap/plateaus").get_json()["plateaus"]
    mine = [p for p in plateaus if p["id"] == plateau.id]
    assert len(mine) == 1 and len(plateaus) == 1
    assert mine[0]["gap_count"] == 1
    assert mine[0]["gaps"] == [{"id": gap.id, "name": gap.name}]
    assert mine[0]["work_packages"] == [{"id": wp.id, "name": "Timeline row"}]

    login_as(client, user)
    rows = client.get("/capability-map/api/roadmap/work-packages").get_json()["work_packages"]
    assert rows[0]["gaps"] == [{"id": gap.id, "name": gap.name}]
