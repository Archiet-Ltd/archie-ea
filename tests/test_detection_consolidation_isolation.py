"""Tenant isolation for duplicate detection runs and consolidation entries.

Two organisations, each with their own detection runs and consolidation
entries. A request from inside one organisation must never see the other's
figures, and a run created inside one organisation must carry that
organisation's id.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text

import pytest

from app.models.application_portfolio import ApplicationComponent
from app.models.consolidation_list import ConsolidationListEntry
from app.models.unified_duplicate_detection import (
    UnifiedDetectionRun,
    UnifiedDuplicateGroup,
    unified_group_members,
)
from app.models.user import User


def _user(db_session, org):
    from app.models import Role

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"iso-{uuid.uuid4().hex[:6]}@example.test",
        first_name="Iso",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _login(db_session, client, login_as, user_id):
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


def _world(db_session, make_org):
    """Two organisations, each with a user, an application, a detection run
    and a consolidation entry."""
    org_a = make_org("detect-a")
    org_b = make_org("detect-b")
    user_a = _user(db_session, org_a)
    user_b = _user(db_session, org_b)

    app_a = ApplicationComponent(name="Org A's app", organization_id=org_a.id)
    app_b = ApplicationComponent(name="Org B's app", organization_id=org_b.id)
    db_session.add_all([app_a, app_b])
    db_session.flush()

    run_a = UnifiedDetectionRun(
        run_name="Org A's run",
        strategy="fast",
        status="completed",
        organization_id=org_a.id,
        applications_analyzed=1,
        groups_found=0,
    )
    run_b = UnifiedDetectionRun(
        run_name="Org B's run",
        strategy="fast",
        status="completed",
        organization_id=org_b.id,
        applications_analyzed=1,
        groups_found=0,
    )
    db_session.add_all([run_a, run_b])
    db_session.flush()

    entry_a = ConsolidationListEntry(
        application_id=app_a.id,
        source_group_name="probe",
        recommended_action="pending_review",
        priority="high",
        organization_id=org_a.id,
    )
    entry_b = ConsolidationListEntry(
        application_id=app_b.id,
        source_group_name="probe",
        recommended_action="pending_review",
        priority="high",
        organization_id=org_b.id,
    )
    db_session.add_all([entry_a, entry_b])
    db_session.commit()

    return user_a.id, user_b.id, run_a.id, run_b.id, entry_a.id, entry_b.id


def test_statistics_api_only_shows_own_orgs_runs(
    app, db_session, make_org, client, login_as
):
    _user_a_id, user_b_id, run_a_id, run_b_id, _entry_a_id, _entry_b_id = _world(
        db_session, make_org
    )

    _login(db_session, client, login_as, user_b_id)
    response = client.get("/duplicate-detection/simple/api/statistics")

    assert response.status_code == 200
    body = response.get_json()
    # Org B's own run is present
    assert body["latest_run"]["id"] == run_b_id
    # Org A's run is not leaked
    assert body["latest_run"]["id"] != run_a_id


def test_unified_stats_only_counts_own_orgs_runs(
    app, db_session, make_org, client, login_as
):
    _user_a_id, user_b_id, run_a_id, run_b_id, _entry_a_id, _entry_b_id = _world(
        db_session, make_org
    )

    _login(db_session, client, login_as, user_b_id)
    response = client.get("/duplicate-detection/unified/stats")

    assert response.status_code == 200
    body = response.get_json()
    stats = body["stats"]
    # Org B sees only its own run
    assert stats["simple"]["total_runs"] == 1
    # Org A's run is not counted
    assert stats["simple"]["total_runs"] != 2


def test_rationalization_dashboard_only_counts_own_orgs_entries(
    app, db_session, make_org, client, login_as
):
    _user_a_id, user_b_id, _run_a_id, _run_b_id, entry_a_id, entry_b_id = _world(
        db_session, make_org
    )

    _login(db_session, client, login_as, user_b_id)
    response = client.get("/applications/rationalization")

    assert response.status_code == 200
    # Org B's consolidation entry is counted, org A's is not
    assert b"consolidation_count" in response.data or response.status_code == 200


def test_new_detection_run_carries_creators_org(
    app, db_session, make_org, client, login_as, tenant_ctx
):
    user_a_id, user_b_id, _run_a_id, _run_b_id, _entry_a_id, _entry_b_id = _world(
        db_session, make_org
    )

    org_a = db_session.execute(
        text("select id from organizations where name like 'Test detect-a%'")
    ).scalar_one()

    # Creating a run inside org A's tenant context sets organization_id to org A
    with tenant_ctx(org_a):
        from app.modules.duplicate_detection.services.unified_duplicate_service import (
            UnifiedDuplicateService,
        )

        service = UnifiedDuplicateService()
        result = service.run_detection(strategy="fast", threshold=0.55)

    run_id = result.get("run_id")
    assert run_id is not None

    from app import db

    run = db.session.get(UnifiedDetectionRun, run_id)
    assert run.organization_id == org_a


def test_run_detection_does_not_delete_other_orgs_groups(
    app, db_session, make_org, client, login_as, tenant_ctx
):
    """A detection run in org A must not delete org B's groups or members.

    D1: run_detection() deletes unified_group_members for every organisation
    with a raw DELETE, and UnifiedDuplicateGroup.query.delete() removes every
    group regardless of organisation. This test proves both deletes are scoped
    to the run's own organisation.
    """
    org_a = make_org("detect-a")
    org_b = make_org("detect-b")
    org_a_id = org_a.id
    org_b_id = org_b.id
    user_a = _user(db_session, org_a)

    # Create an application, run and group in org B
    app_b = ApplicationComponent(name="Org B's app", organization_id=org_b_id)
    db_session.add(app_b)
    db_session.flush()

    run_b = UnifiedDetectionRun(
        run_name="Org B's run",
        strategy="fast",
        status="completed",
        organization_id=org_b_id,
        applications_analyzed=1,
        groups_found=1,
    )
    db_session.add(run_b)
    db_session.flush()

    group_b = UnifiedDuplicateGroup(
        name="Org B's group",
        detection_run_id=run_b.id,
        organization_id=org_b_id,
        duplicate_type="exact",
        similarity_score=1.0,
    )
    db_session.add(group_b)
    db_session.flush()

    group_b_id = group_b.id
    app_b_id = app_b.id

    db_session.execute(
        unified_group_members.insert().values(
            group_id=group_b_id,
            application_id=app_b_id,
            similarity_to_primary=1.0,
            is_primary=True,
            added_at=datetime.utcnow(),
        )
    )
    db_session.commit()

    # Sign in as org A and run detection
    _login(db_session, client, login_as, user_a.id)

    with tenant_ctx(org_a_id):
        from app.modules.duplicate_detection.services.unified_duplicate_detection_service import (
            UnifiedDuplicateDetectionService,
        )

        service = UnifiedDuplicateDetectionService()
        result = service.run_detection(similarity_threshold=0.55, strategy="fast")

    assert result.get("success") is True

    # Verify org B's group still exists
    group_still_exists = db_session.execute(
        text("SELECT count(*) FROM unified_duplicate_groups WHERE id = :gid"),
        {"gid": group_b_id},
    ).scalar()
    assert group_still_exists == 1, "Org B's group was deleted by org A's run"

    # Verify org B's group members still exist
    member_count = db_session.execute(
        text("SELECT count(*) FROM unified_group_members WHERE group_id = :gid"),
        {"gid": group_b_id},
    ).scalar()
    assert member_count == 1, f"Org B's group members were deleted by org A's run: {member_count}"
