"""The default AI duplicate run and the business-aware run make groups.

Both runs score every pair through a business-similarity step. That step did
not exist, so each run failed and made nothing. These tests seed near-duplicate
applications in one organisation (and an unrelated pair in a second one) and
assert each strategy finishes, records no error, and makes a group naming the
seeded pair.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

STRATEGIES = ("ai_enhanced", "business_aware")


def _seed_app(db_session, org, name, **extra):
    from app.models.application_portfolio import ApplicationComponent

    app = ApplicationComponent(
        name=name,
        description="Customer billing and invoicing platform",
        component_type="application",
        technology_stack="java, postgres",
        lifecycle_status="active",
        business_domain="Finance",
        organization_id=org.id,
        **extra,
    )
    db_session.add(app)
    db_session.flush()
    return app


@pytest.fixture
def seeded(app, db_session, make_org, login_as):
    from app.models.user import User

    org = make_org("dupai")
    other = make_org("dupai-other")
    user = User(
        email=f"dupai-{org.id}@example.com",
        first_name="Dup",
        last_name="Ai",
        organization_id=org.id,
        confirmed=True,
    )
    if hasattr(user, "enterprise_role"):
        user.enterprise_role = "enterprise_architect"
    db_session.add(user)
    first = _seed_app(db_session, org, "Billing Portal")
    second = _seed_app(db_session, org, "Billing Portal Service")
    _seed_app(db_session, other, "Billing Portal")
    _seed_app(db_session, other, "Billing Portal Service")
    db_session.flush()

    app.config["WTF_CSRF_ENABLED"] = False
    raw = app.test_client()
    login_as(raw, user)
    return raw, org, {first.id, second.id}


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_run_makes_a_group_for_the_seeded_pair(seeded, db_session, strategy):
    from app.models.unified_duplicate_detection import (
        UnifiedDetectionRun,
        UnifiedDuplicateGroup,
    )

    client, org, seeded_ids = seeded
    response = client.post(
        "/duplicate-detection/ai/api/detect",
        json={"strategy": strategy, "threshold": 0.65},
    )
    body = response.get_json() or {}
    assert response.status_code == 200 and body.get("success") is True, body
    assert body.get("error") is None

    run = db_session.get(UnifiedDetectionRun, body["run_id"])
    assert run.status == "completed"
    assert not run.error_message
    assert run.groups_found >= 1

    groups = UnifiedDuplicateGroup.query.filter_by(detection_run_id=run.id).all()
    assert groups, f"{strategy} made no duplicate group"
    member_ids = {m.id for g in groups for m in g.applications}
    assert seeded_ids <= member_ids, "the seeded pair is not in a group"
    # Nothing from the other organisation is grouped with this one's pair.
    assert all(m.organization_id == org.id for g in groups for m in g.applications)
