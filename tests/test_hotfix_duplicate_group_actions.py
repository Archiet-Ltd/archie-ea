"""Hotfix: duplicate group status actions are limited to the group's own
organisation (or a platform admin).

The group tables carry no organisation column; a group belongs to the
organisation of its member applications. Covers the v1 routes (registered in
the app) and the v2 routes (swapped in for the same endpoints, since both
blueprints share one name).
"""

import uuid

import pytest

from tests.test_hotfix_risky_actions import _make_org, _make_user


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(params=["v1", "v2"])
def impl(request, app, monkeypatch):
    if request.param == "v2":
        from app.modules.duplicate_detection.v2.routes import unified_duplicate_routes as v2

        for name in (
            "api_ignore_group",
            "api_add_group_to_consolidation",
            "approve_consolidation_recommendation",
            "reject_consolidation_recommendation",
        ):
            monkeypatch.setitem(app.view_functions, f"unified_duplicate.{name}", getattr(v2, name))
    return request.param


def _make_app(db_session, org):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=f"HF app {uuid.uuid4().hex[:8]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _unified_group(db_session, apps):
    from app.models.unified_duplicate_detection import UnifiedDuplicateGroup, unified_group_members

    group = UnifiedDuplicateGroup(name=f"HF group {uuid.uuid4().hex[:6]}", similarity_score=0.9)
    db_session.add(group)
    db_session.flush()
    for a in apps:
        db_session.execute(unified_group_members.insert().values(group_id=group.id, application_id=a.id))
    db_session.flush()
    return group


def _legacy_group(db_session, apps):
    from app.models.application_duplicate_detection import (
        DuplicateDetectionRun,
        DuplicateGroup,
        DuplicateType,
        duplicate_group_members,
    )

    run = DuplicateDetectionRun(run_name=f"HF run {uuid.uuid4().hex[:6]}")
    db_session.add(run)
    db_session.flush()
    group = DuplicateGroup(
        group_name="HF legacy",
        duplicate_type=DuplicateType.FUNCTIONAL,
        detection_run_id=run.id,
        overall_similarity_score=0.9,
    )
    db_session.add(group)
    db_session.flush()
    for a in apps:
        db_session.execute(
            duplicate_group_members.insert().values(duplicate_group_id=group.id, application_id=a.id)
        )
    db_session.flush()
    return group


@pytest.fixture
def scene(db_session):
    org_a = _make_org(db_session, "a")
    org_b = _make_org(db_session, "b")
    user_a = _make_user(db_session, org_a)
    user_b = _make_user(db_session, org_b)
    platform_admin = _make_user(db_session, org_b, is_org_admin=True, is_platform_admin=True)
    apps_a = [_make_app(db_session, org_a), _make_app(db_session, org_a)]
    db_session.commit()
    return org_a, user_a, user_b, platform_admin, apps_a


def _fresh_groups(db_session, apps):
    unified = _unified_group(db_session, apps)
    legacy = _legacy_group(db_session, apps)
    db_session.commit()
    return unified, legacy


def _actions(unified, legacy):
    base = "/duplicate-detection"
    return [
        ("unified", f"{base}/api/groups/{unified.id}/ignore", {"reason": "hf"}),
        ("unified", f"{base}/api/groups/{unified.id}/add-to-consolidation", {}),
        ("legacy", f"{base}/api/groups/{legacy.id}/ignore", {"reason": "hf"}),
        ("legacy", f"{base}/api/groups/{legacy.id}/add-to-consolidation", {}),
        ("legacy", f"{base}/api/consolidation-recommendation/{legacy.id}/approve", {}),
        ("legacy", f"{base}/api/consolidation-recommendation/{legacy.id}/reject", {"reason": "hf"}),
    ]


class TestDuplicateGroupActionsByOrg:
    def test_other_org_gets_404_and_status_unchanged(self, app, login_as, client, db_session, scene, impl):
        from app.models.application_duplicate_detection import DuplicateGroup
        from app.models.consolidation_list import ConsolidationListEntry
        from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

        _, _, user_b, _, apps_a = scene
        unified, legacy = _fresh_groups(db_session, apps_a)
        uid, lid = unified.id, legacy.id
        with app.app_context():
            login_as(client, user_b)
            for _, url, body in _actions(unified, legacy):
                assert client.post(url, json=body).status_code == 404, url
            db_session.expire_all()
            assert UnifiedDuplicateGroup.query.get(uid).status == "pending"
            assert DuplicateGroup.query.get(lid).status not in ("ignored", "approved", "rejected")
            assert ConsolidationListEntry.query.filter_by(source_group_id=uid).count() == 0

    def test_own_org_still_succeeds(self, app, login_as, client, db_session, scene, impl):
        from app.models.application_duplicate_detection import DuplicateGroup
        from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

        _, user_a, _, _, apps_a = scene
        unified, legacy = _fresh_groups(db_session, apps_a)
        uid, lid = unified.id, legacy.id
        base = "/duplicate-detection"
        with app.app_context():
            login_as(client, user_a)
            assert client.post(f"{base}/api/groups/{uid}/add-to-consolidation", json={}).status_code == 200
            assert client.post(f"{base}/api/groups/{uid}/ignore", json={"reason": "hf"}).status_code == 200
            assert client.post(f"{base}/api/groups/{lid}/ignore", json={"reason": "hf"}).status_code == 200
            assert client.post(f"{base}/api/consolidation-recommendation/{lid}/approve", json={}).status_code == 200
            assert client.post(f"{base}/api/consolidation-recommendation/{lid}/reject", json={"reason": "x"}).status_code == 200
            db_session.expire_all()
            assert UnifiedDuplicateGroup.query.get(uid).status == "ignored"
            assert DuplicateGroup.query.get(lid).status == "rejected"

    def test_platform_admin_not_refused(self, app, login_as, client, db_session, scene, impl):
        _, _, _, platform_admin, apps_a = scene
        unified, legacy = _fresh_groups(db_session, apps_a)
        with app.app_context():
            login_as(client, platform_admin)
            for kind, url, body in _actions(unified, legacy):
                # Not refused. (The add path may still answer 400/500 for another
                # organisation's members, which the tenant filter hides from it.)
                assert client.post(url, json=body).status_code not in (403, 404), url
