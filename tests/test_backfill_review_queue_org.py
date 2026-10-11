"""backfill-review-queue-org derives each NULL-org review_queue_items row's
organization from its reviewed item first (item_type + item_id), then from
user FKs in a stated order of preference.

Scenario: items created before the TenantMixin migration, with
organization_id NULL, seeded for two different organisations via their
reviewed item ids or user foreign key references, plus unresolvable rows.
"""
import uuid

import pytest


def _user(db_session, org, label="u"):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label}-{suffix}@example.com",
        first_name="Test",
        last_name=label,
        organization_id=org.id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _app_component(db_session, org, label="app"):
    """Create an ApplicationComponent owned by *org*."""
    from app.models.application_portfolio import ApplicationComponent

    suffix = uuid.uuid4().hex[:8]
    app = ApplicationComponent(
        name=f"{label}-{suffix}",
        description="test application",
        organization_id=org.id,
    )
    db_session.add(app)
    db_session.flush()
    return app


def _archimate_element(db_session, org, label="ae"):
    """Create an ArchiMateElement owned by *org*."""
    from app.models.archimate_core import ArchiMateElement

    suffix = uuid.uuid4().hex[:8]
    ae = ArchiMateElement(
        name=f"{label}-{suffix}",
        type="ApplicationComponent",
        layer="application",
        # TenantMixin column
        organization_id=org.id,
    )
    db_session.add(ae)
    db_session.flush()
    return ae


def _item(db_session, org_id=None, item_name=None, item_type="capability_mapping",
          item_id=None, assigned_to_id=None,
          reviewed_by_id=None, escalated_to_id=None):
    """Create a review queue item, optionally with NULL organization_id.

    item_id defaults to None (no reviewed-item reference) so a caller that
    only sets up user FKs exercises the user-fallback resolution path, not
    whatever row happens to occupy item_id=1 in a shared test database.
    Tests exercising reviewed-item resolution must pass an item_id that
    points at an application or ArchiMate element they created themselves.
    """
    from app.models.confidence_review import ReviewQueueItem, ReviewStatus

    if item_name is None:
        item_name = f"item-{uuid.uuid4().hex[:8]}"
    row = ReviewQueueItem(
        organization_id=org_id,
        item_type=item_type,
        item_id=item_id,
        item_name=item_name,
        item_data='{"key":"value"}',
        confidence_score=0.75,
        confidence_factors='{"factor":0.8}',
        ai_model_used="test-model",
        status=ReviewStatus.PENDING,
        review_priority=5,
        assigned_to_id=assigned_to_id,
        reviewed_by_id=reviewed_by_id,
        escalated_to_id=escalated_to_id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _run(dry_run=False):
    """Invoke the backfill CLI command and return its output."""
    from app import create_app

    app = create_app("testing")
    runner = app.test_cli_runner()
    args = ["backfill-review-queue-org"]
    if dry_run:
        args.append("--dry-run")
    result = runner.invoke(args=args)
    return result


@pytest.fixture
def two_orgs(db_session, make_org):
    org_a = make_org("rq-backfill-a")
    org_b = make_org("rq-backfill-b")
    return org_a, org_b


def test_items_get_own_organisation(db_session, two_orgs):
    """Two NULL-org items, each with an assigned_to user in a different org,
    must each receive their user's organisation."""
    org_a, org_b = two_orgs
    user_a = _user(db_session, org_a, "reviewer-a")
    user_b = _user(db_session, org_b, "reviewer-b")

    item_a = _item(db_session, org_id=None, item_name="A-item", assigned_to_id=user_a.id)
    item_b = _item(db_session, org_id=None, item_name="B-item", assigned_to_id=user_b.id)
    db_session.commit()

    assert item_a.organization_id is None
    assert item_b.organization_id is None

    result = _run()
    assert result.exit_code == 0, f"backfill failed: {result.output}"

    db_session.refresh(item_a)
    db_session.refresh(item_b)

    assert item_a.organization_id == org_a.id, (
        f"item A should get org {org_a.id}, got {item_a.organization_id}"
    )
    assert item_b.organization_id == org_b.id, (
        f"item B should get org {org_b.id}, got {item_b.organization_id}"
    )


def test_dry_run_changes_nothing(db_session, two_orgs):
    """A dry run must report counts but leave organization_id NULL."""
    org_a, _org_b = two_orgs
    user_a = _user(db_session, org_a, "reviewer-a")

    item = _item(db_session, org_id=None, item_name="dry-run-item", assigned_to_id=user_a.id)
    db_session.commit()

    assert item.organization_id is None

    result = _run(dry_run=True)
    assert result.exit_code == 0
    assert "dry run" in result.output.lower()

    db_session.refresh(item)
    assert item.organization_id is None, "dry-run must not write"


def test_unresolvable_item_stays_null(db_session, two_orgs):
    """An item with no user FKs at all must stay NULL and be reported."""
    org_a, _org_b = two_orgs

    item = _item(db_session, org_id=None, item_name="orphan-item",
                 assigned_to_id=None, reviewed_by_id=None, escalated_to_id=None)
    db_session.commit()

    assert item.organization_id is None

    result = _run()
    # The command does not exit non-zero for unresolvable rows — it reports
    # them and leaves them NULL, same as backfill_ai_chat_feedback_org.
    assert result.exit_code == 0

    db_session.refresh(item)
    assert item.organization_id is None, "unresolvable item must stay NULL"


def test_backfill_is_idempotent(db_session, two_orgs):
    """Running twice must not change an already-backfilled row."""
    org_a, _org_b = two_orgs
    user_a = _user(db_session, org_a, "reviewer-a")

    item = _item(db_session, org_id=None, item_name="idempotent-item", assigned_to_id=user_a.id)
    db_session.commit()

    _run()
    db_session.refresh(item)
    first_org = item.organization_id
    assert first_org == org_a.id

    result2 = _run()
    assert result2.exit_code == 0

    db_session.refresh(item)
    assert item.organization_id == first_org, "idempotent re-run must not change org"


def test_reviewed_by_fallback(db_session, two_orgs):
    """When assigned_to_id is NULL, fall back to reviewed_by_id."""
    org_a, _org_b = two_orgs
    user_a = _user(db_session, org_a, "reviewer-a")

    item = _item(db_session, org_id=None, item_name="reviewed-by-item",
                 assigned_to_id=None, reviewed_by_id=user_a.id)
    db_session.commit()

    _run()
    db_session.refresh(item)

    assert item.organization_id == org_a.id


def test_escalated_to_fallback(db_session, two_orgs):
    """When assigned_to_id and reviewed_by_id are both NULL, fall back to escalated_to_id."""
    org_a, _org_b = two_orgs
    user_a = _user(db_session, org_a, "escalation-target")

    item = _item(db_session, org_id=None, item_name="escalated-item",
                 assigned_to_id=None, reviewed_by_id=None, escalated_to_id=user_a.id)
    db_session.commit()

    _run()
    db_session.refresh(item)

    assert item.organization_id == org_a.id


def test_preference_order_assigned_over_reviewed(db_session, two_orgs):
    """When both assigned_to_id and reviewed_by_id are set (to different orgs),
    assigned_to_id wins."""
    org_a, org_b = two_orgs
    user_a = _user(db_session, org_a, "assignee-a")
    user_b = _user(db_session, org_b, "reviewer-b")

    item = _item(db_session, org_id=None, item_name="pref-order-item",
                 assigned_to_id=user_a.id, reviewed_by_id=user_b.id)
    db_session.commit()

    _run()
    db_session.refresh(item)

    assert item.organization_id == org_a.id, (
        "assigned_to_id must take precedence over reviewed_by_id"
    )


# ── reviewed-item resolution (pending, unassigned items) ────────────────────


def test_pending_item_resolved_from_application(db_session, two_orgs):
    """A pending item with no user FKs must be resolved from the application
    referenced by item_type / item_id."""
    org_a, org_b = two_orgs
    app_a = _app_component(db_session, org_a, "app-a")
    app_b = _app_component(db_session, org_b, "app-b")

    item_a = _item(db_session, org_id=None, item_name="pending-a",
                   item_type="capability_mapping", item_id=app_a.id,
                   assigned_to_id=None, reviewed_by_id=None, escalated_to_id=None)
    item_b = _item(db_session, org_id=None, item_name="pending-b",
                   item_type="capability_mapping", item_id=app_b.id,
                   assigned_to_id=None, reviewed_by_id=None, escalated_to_id=None)
    db_session.commit()

    assert item_a.organization_id is None
    assert item_b.organization_id is None

    result = _run()
    assert result.exit_code == 0, f"backfill failed: {result.output}"

    db_session.refresh(item_a)
    db_session.refresh(item_b)

    assert item_a.organization_id == org_a.id, (
        f"pending A should get app org {org_a.id}, got {item_a.organization_id}"
    )
    assert item_b.organization_id == org_b.id, (
        f"pending B should get app org {org_b.id}, got {item_b.organization_id}"
    )


def test_reviewed_item_wins_over_user_fks(db_session, two_orgs):
    """The reviewed item's organisation takes precedence over user FKs."""
    org_a, org_b = two_orgs
    app_a = _app_component(db_session, org_a, "app-a")
    user_b = _user(db_session, org_b, "reviewer-b")

    # Item belongs to org_a's app, but assigned_to is org_b's user.
    item = _item(db_session, org_id=None, item_name="item-wins",
                 item_type="capability_mapping", item_id=app_a.id,
                 assigned_to_id=user_b.id,
                 reviewed_by_id=None, escalated_to_id=None)
    db_session.commit()

    _run()
    db_session.refresh(item)

    assert item.organization_id == org_a.id, (
        "reviewed item (org A) must take precedence over assigned_to (org B)"
    )


def test_pending_item_process_classification(db_session, two_orgs):
    """process_classification items resolve from the application."""
    org_a, org_b = two_orgs
    app_a = _app_component(db_session, org_a, "proc-app-a")
    app_b = _app_component(db_session, org_b, "proc-app-b")

    item_a = _item(db_session, org_id=None, item_name="proc-a",
                   item_type="process_classification", item_id=app_a.id,
                   assigned_to_id=None, reviewed_by_id=None, escalated_to_id=None)
    item_b = _item(db_session, org_id=None, item_name="proc-b",
                   item_type="process_classification", item_id=app_b.id,
                   assigned_to_id=None, reviewed_by_id=None, escalated_to_id=None)
    db_session.commit()

    _run()
    db_session.refresh(item_a)
    db_session.refresh(item_b)

    assert item_a.organization_id == org_a.id
    assert item_b.organization_id == org_b.id


def test_pending_item_vendor_analysis(db_session, two_orgs):
    """vendor_analysis items resolve from the application."""
    org_a, _org_b = two_orgs
    app_a = _app_component(db_session, org_a, "vendor-app-a")

    item = _item(db_session, org_id=None, item_name="vendor-item",
                 item_type="vendor_analysis", item_id=app_a.id,
                 assigned_to_id=None, reviewed_by_id=None, escalated_to_id=None)
    db_session.commit()

    _run()
    db_session.refresh(item)

    assert item.organization_id == org_a.id


def test_pending_item_archimate_element(db_session, two_orgs):
    """archimate_element items resolve from the ArchiMateElement."""
    org_a, org_b = two_orgs
    ae_a = _archimate_element(db_session, org_a, "ae-a")
    ae_b = _archimate_element(db_session, org_b, "ae-b")

    item_a = _item(db_session, org_id=None, item_name="archi-a",
                   item_type="archimate_element", item_id=ae_a.id,
                   assigned_to_id=None, reviewed_by_id=None, escalated_to_id=None)
    item_b = _item(db_session, org_id=None, item_name="archi-b",
                   item_type="archimate_element", item_id=ae_b.id,
                   assigned_to_id=None, reviewed_by_id=None, escalated_to_id=None)
    db_session.commit()

    _run()
    db_session.refresh(item_a)
    db_session.refresh(item_b)

    assert item_a.organization_id == org_a.id
    assert item_b.organization_id == org_b.id


def test_unknown_item_type_reported_stays_null(db_session, two_orgs):
    """An unrecognised item_type must be reported by name and count,
    and its rows stay NULL."""
    org_a, _org_b = two_orgs
    app_a = _app_component(db_session, org_a, "unknown-app")

    item = _item(db_session, org_id=None, item_name="unknown-type",
                 item_type="future_feature_type", item_id=app_a.id,
                 assigned_to_id=None, reviewed_by_id=None, escalated_to_id=None)
    db_session.commit()

    result = _run()
    assert result.exit_code == 0
    # Unknown types are reported by name in the output.
    assert "future_feature_type" in result.output, (
        f"unknown type must be reported: {result.output}"
    )

    db_session.refresh(item)
    assert item.organization_id is None, "unknown-type item must stay NULL"