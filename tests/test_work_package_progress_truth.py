"""A work package shows the progress a person recorded, or work-derived progress, or "Not recorded";
never the share of its dates that has passed. Its owner is one fact, and saving returns to the record."""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta

import pytest

from app.models.implementation_migration import Deliverable, Plateau
from app.models.user import User
from app.services import work_package_service as service

pytestmark = pytest.mark.usefixtures("db_session")

TODAY = date(2026, 11, 14)


def _user(db_session, org):
    user = User(email=f"wp-{uuid.uuid4().hex[:10]}@example.test", organization_id=org.id,
                confirmed=True, enterprise_role="enterprise_architect", first_name="Ada", last_name="Owner")
    db_session.add(user)
    db_session.flush()
    return user


def _under_way(org, name="Rollout", **fields):
    return service.create_work_package(
        organization_id=org.id, name=name, status="in_progress",
        start_date=datetime(2026, 10, 20), end_date=datetime(2026, 11, 19), **fields)


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _testid(html, testid):
    m = re.search(r'data-testid="%s"[^>]*>([^<]*)<' % testid, html)
    return m.group(1).strip() if m else None


def test_dated_package_under_way_with_nothing_recorded_reads_no_figure(db_session, make_org, tenant_ctx, client, login_as):
    org = make_org("noprog")
    user = _user(db_session, org)
    with tenant_ctx(org.id):
        wp = _under_way(org)
        db_session.flush()
        assert service.progress_percent(wp, today=TODAY) is None
        assert service.days_gone_label(wp, today=date(2026, 11, 14)) == "25 of 30 days gone"
        wp_id = wp.id
    login_as(client, user)
    html = client.get(f"/implementation/work-packages/{wp_id}").get_data(as_text=True)
    assert "Not recorded" in html
    assert "83%" not in html
    assert "Progress: not recorded" in _text(html)


def test_saving_only_a_status_change_keeps_the_recorded_progress(db_session, make_org, tenant_ctx, client, login_as):
    org = make_org("keep")
    user = _user(db_session, org)
    with tenant_ctx(org.id):
        wp = _under_way(org, progress_percentage=40)
        db_session.flush()
        wp_id = wp.id
    login_as(client, user)
    resp = client.post(f"/implementation/work-packages/{wp_id}/edit", data={"status": "on_hold"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/implementation/work-packages/{wp_id}")
    db_session.expire_all()
    with tenant_ctx(org.id):
        assert service.get_work_package(wp_id, org.id).progress_percentage == 40
    client.post(f"/implementation/work-packages/{wp_id}/edit", data={"status": "in_progress"})
    page = client.get(f"/implementation/work-packages/{wp_id}").get_data(as_text=True)
    assert "40%" in page
    assert _testid(page, "wp-progress-source") == "Entered by the team"


def test_saving_the_edit_form_lands_on_the_record_with_a_saved_notice(db_session, make_org, tenant_ctx, client, login_as):
    org = make_org("saved")
    user = _user(db_session, org)
    with tenant_ctx(org.id):
        wp_id = _under_way(org).id
        db_session.flush()
    login_as(client, user)
    resp = client.post(f"/implementation/work-packages/{wp_id}/edit",
                       data={"progress_percentage": "65"}, follow_redirects=True)
    text = _text(resp.get_data(as_text=True))
    assert "Saved." in text and "65%" in text and "Entered by the team" in text
    bad = client.post(f"/implementation/work-packages/{wp_id}/edit", data={"progress_percentage": "140"})
    assert bad.status_code == 400
    assert "between 0 and 100" in bad.get_data(as_text=True)


def test_the_page_owner_and_the_evidence_owner_are_the_same_text(db_session, make_org, tenant_ctx, client, login_as):
    org = make_org("owner")
    user = _user(db_session, org)
    with tenant_ctx(org.id):
        typed = _under_way(org, "Typed owner", assigned_to="Grace Hopper")
        member = _under_way(org, "Member owner")
        member.owner_id = user.id
        nobody = _under_way(org, "No owner")
        db_session.flush()
        ids = (typed.id, member.id, nobody.id)
    login_as(client, user)
    for wp_id, expected in zip(ids, ("Grace Hopper", "Ada Owner", None)):
        html = client.get(f"/implementation/work-packages/{wp_id}").get_data(as_text=True)
        text = _text(html)
        if expected:
            assert "Owner: %s" % expected in text
            assert re.search(r'data-testid="wp-owner"[^>]*>\s*%s\s*<' % expected, html)
            assert "Owner: none recorded" not in text
        else:
            assert "Owner: none recorded" in text and "No owner yet" in text


def test_computed_progress_is_labelled_with_how_it_was_computed(db_session, make_org, tenant_ctx):
    org = make_org("computed")
    with tenant_ctx(org.id):
        wp = _under_way(org)
        db_session.flush()
        db_session.add_all([
            Deliverable(name="A", organization_id=org.id, unified_work_package_id=wp.id, delivery_status="completed"),
            Deliverable(name="B", organization_id=org.id, unified_work_package_id=wp.id, delivery_status="planned"),
            Deliverable(name="C", organization_id=org.id, unified_work_package_id=wp.id, delivery_status="planned"),
        ])
        db_session.flush()
        basis = service.progress_basis(wp)
    assert basis == {"percent": 33, "source": "computed", "label": "1 of 3 deliverables completed"}


def test_every_reader_shows_the_same_figure_for_one_package(db_session, make_org, tenant_ctx, client, login_as):
    from app.modules.genome.services.roadmap_slice import build_roadmap_slice

    org, other = make_org("agree"), make_org("agree-other")
    user = _user(db_session, org)
    with tenant_ctx(org.id):
        recorded = _under_way(org, "Recorded", progress_percentage=40)
        blank = _under_way(org, "Blank")
        db_session.flush()
        ids = (recorded.id, blank.id)
    with tenant_ctx(other.id):
        theirs = _under_way(other, "Theirs")
        db_session.flush()
        theirs_id = theirs.id
    login_as(client, user)
    with tenant_ctx(org.id):
        slice_ = {w["id"]: w for w in build_roadmap_slice(org.id)["work_packages"]}
        shown = {i: service.to_roadmap_dict(service.get_work_package(i, org.id), org.id) for i in ids}
    assert [slice_[i]["percent_complete"] for i in ids] == [40, None]
    assert [shown[i]["percent_complete"] for i in ids] == [40, None]
    pages = [client.get(f"/implementation/work-packages/{i}").get_data(as_text=True) for i in ids]
    assert "40%" in pages[0] and "Not recorded" in pages[1]
    # the other organisation's package is not reachable and its figure is untouched
    assert client.get(f"/implementation/work-packages/{theirs_id}").status_code in (302, 403, 404)
    db_session.expire_all()
    with tenant_ctx(other.id):
        assert service.get_work_package(theirs_id, other.id).progress_percentage in (0, None)


def test_an_empty_plateau_is_not_completed_and_reads_nothing_planned_yet(db_session, make_org, tenant_ctx, client, login_as):
    org = make_org("empty-plateau")
    user = _user(db_session, org)
    db_session.add(Plateau(name="Quiet state", organization_id=org.id, sequence_order=1, target_date=date(2026, 9, 30)))
    db_session.flush()
    login_as(client, user)
    html = client.get("/genome/roadmap/").get_data(as_text=True)
    text = _text(html)
    assert "Quiet state" in text and "Nothing planned yet" in text
    assert "This state was reached" not in text
    completed = re.search(r'id="roadmap-col-completed".*?(?=id="roadmap-col-|\Z)', html, re.S)
    assert completed is None or "Quiet state" not in completed.group(0)


def test_a_plateau_averages_only_the_figures_that_exist(db_session, make_org, tenant_ctx, client, login_as):
    org = make_org("avg")
    user = _user(db_session, org)
    plateau = Plateau(name="Half known", organization_id=org.id, sequence_order=1, target_date=date(2027, 3, 31))
    unknown = Plateau(name="None known", organization_id=org.id, sequence_order=2, target_date=date(2027, 6, 30))
    db_session.add_all([plateau, unknown])
    db_session.flush()
    with tenant_ctx(org.id):
        for name, pct in (("Known", 50), ("Unknown", None)):
            wp = _under_way(org, name, **({"progress_percentage": pct} if pct else {}))
            db_session.flush()
            service.link_plateau(wp.id, plateau.id, organization_id=org.id)
        lone = _under_way(org, "Lone")
        db_session.flush()
        service.link_plateau(lone.id, unknown.id, organization_id=org.id)
    login_as(client, user)
    text = _text(client.get("/genome/roadmap/").get_data(as_text=True))
    assert "50% complete, based on 1 of 2 packages" in text
    assert "25%" not in text
    assert "0 of 1 done, progress: Not recorded" in text
