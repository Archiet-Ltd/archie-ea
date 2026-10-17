"""Gap Analysis: what each gap shows, which work packages address it, and the
link / unlink write that keeps the list of unaddressed gaps in agreement.

Two organisations in every test: the second one's gaps and work packages never
appear for, nor can be written by, the first.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label):
    from app.models.user import Role, User

    Role.insert_roles()
    role_obj = Role.query.filter_by(name="User").first()
    user = User(
        email=f"gl-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Test",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
        role_id=role_obj.id if role_obj else None,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _gap(db_session, org, name, **extra):
    from app.services.gap_register_service import create_gap

    gap, _created = create_gap(org.id, name, **extra)
    db_session.flush()
    return gap


def _wp(org, name):
    from app.services import work_package_service

    return work_package_service.create_work_package(organization_id=org.id, name=name)


@pytest.fixture
def two_orgs(db_session, make_org):
    a, b = make_org("gap-a"), make_org("gap-b")
    return {"a": a, "b": b, "user_a": _user(db_session, a, "a")}


def test_link_gap_keeps_the_work_packages_other_gap_links(two_orgs, db_session):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    g1, g2 = _gap(db_session, a, "Gap one"), _gap(db_session, a, "Gap two")
    wp = _wp(a, "Fix both")
    svc.link_gap(wp.id, g1.id, a.id)
    svc.link_gap(wp.id, g2.id, a.id)
    svc.link_gap(wp.id, g2.id, a.id)  # already linked: a no-op, not an error
    assert svc.plateau_and_gap_links([wp], a.id)[wp.id]["gap_ids"] == [g1.id, g2.id]
    assert svc.unlink_gap(wp.id, g1.id, a.id) == 1
    assert svc.unlink_gap(wp.id, g1.id, a.id) == 0
    assert svc.plateau_and_gap_links([wp], a.id)[wp.id]["gap_ids"] == [g2.id]


def test_link_gap_refuses_another_organisations_gap_or_work_package(two_orgs, db_session):
    from app.services import work_package_service as svc

    a, b = two_orgs["a"], two_orgs["b"]
    gap_a, gap_b = _gap(db_session, a, "A gap"), _gap(db_session, b, "B gap")
    wp_a, wp_b = _wp(a, "A work"), _wp(b, "B work")
    with pytest.raises(svc.WorkPackageNotFound):
        svc.link_gap(wp_a.id, gap_b.id, a.id)
    with pytest.raises(svc.WorkPackageNotFound):
        svc.link_gap(wp_b.id, gap_a.id, a.id)
    assert svc.work_package_ids_for_gap(gap_b.id, b.id) == []
    assert svc.work_package_ids_for_gap(gap_a.id, a.id) == []


def test_not_addressed_list_and_counts_follow_the_links(two_orgs, db_session):
    from app.services import work_package_service as svc
    from app.services.gap_register_service import count_gaps, get_gaps_not_addressed

    a, b = two_orgs["a"], two_orgs["b"]
    gap = _gap(db_session, a, "Linked later")
    _gap(db_session, b, "Other organisation")
    assert gap.id in {g.id for g in get_gaps_not_addressed(a.id)}
    svc.link_gap(_wp(a, "Closes it").id, gap.id, a.id)
    assert gap.id not in {g.id for g in get_gaps_not_addressed(a.id)}
    assert count_gaps(a.id, has_work_package=True) == 1
    assert count_gaps(a.id, has_work_package=False) == 0
    assert count_gaps(b.id, has_work_package=True) == 0


def test_work_packages_for_gaps_reads_names_for_the_whole_page(two_orgs, db_session):
    from app.services import work_package_service as svc

    a, b = two_orgs["a"], two_orgs["b"]
    g1, g2 = _gap(db_session, a, "G1"), _gap(db_session, a, "G2")
    wp = _wp(a, "Shared work")
    svc.link_gap(wp.id, g1.id, a.id)
    out = svc.work_packages_for_gaps([g1.id, g2.id], a.id)
    assert out == {g1.id: [(wp.id, "Shared work")], g2.id: []}
    assert svc.work_packages_for_gaps([g1.id], b.id) == {g1.id: []}


def test_link_routes_write_and_fence_by_organisation(two_orgs, db_session, client, login_as):
    from app.services import work_package_service as svc

    a, b = two_orgs["a"], two_orgs["b"]
    gap_a, gap_b = _gap(db_session, a, "Route gap"), _gap(db_session, b, "Foreign gap")
    wp_a, wp_b = _wp(a, "Route work"), _wp(b, "Foreign work")
    db_session.flush()
    url = "/enterprise/api/gaps/%d/work-packages"
    login_as(client, two_orgs["user_a"])
    assert client.post(url % gap_a.id, json={"work_package_id": wp_a.id}).status_code == 200
    assert client.post(url % gap_a.id, json={"work_package_id": wp_a.id}).status_code == 200
    login_as(client, two_orgs["user_a"])
    assert svc.work_package_ids_for_gap(gap_a.id, a.id) == [wp_a.id]
    assert client.post(url % gap_b.id, json={"work_package_id": wp_a.id}).status_code == 404
    assert client.post(url % gap_a.id, json={"work_package_id": wp_b.id}).status_code == 404
    assert client.delete(url % gap_b.id + "?work_package_id=%d" % wp_a.id).status_code == 404
    assert svc.work_package_ids_for_gap(gap_b.id, b.id) == []
    assert client.post(url % gap_a.id, json={}).status_code == 400
    assert client.delete(url % gap_a.id + "?work_package_id=%d" % wp_a.id).status_code == 200
    assert svc.work_package_ids_for_gap(gap_a.id, a.id) == []


def test_screen_shows_recorded_severity_kind_cards_and_linked_work_packages(
    two_orgs, db_session, client, login_as
):
    from app.services import work_package_service as svc

    a, b = two_orgs["a"], two_orgs["b"]
    crit = _gap(db_session, a, "Burning gap", severity="critical")
    _gap(db_session, a, "Done gap", severity="low", resolution_status="resolved")
    _gap(db_session, b, "Foreign burning gap", severity="critical")
    svc.link_gap(_wp(a, "Extinguisher programme").id, crit.id, a.id)
    _wp(b, "Foreign programme")
    db_session.flush()
    login_as(client, two_orgs["user_a"])
    body = client.get("/enterprise/implementation/gap-analysis").get_data(as_text=True)
    assert "Burning gap" in body and "Foreign burning gap" not in body
    assert "Extinguisher programme" in body and "Foreign programme" not in body
    assert "Capability shortfall" in body and "Critical" in body
    assert "None" not in body.split("Identified Gaps", 1)[1].split("Gaps Not Addressed", 1)[0]
    from app.services.gap_register_service import count_gaps

    # The summary cards count what is recorded, for this organisation only.
    assert count_gaps(a.id, severity="critical") == 1
    assert count_gaps(a.id, resolution_status="resolved") == 1
    assert "Done gap" in body
