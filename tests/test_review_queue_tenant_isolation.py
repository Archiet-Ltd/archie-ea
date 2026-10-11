"""Review queue items must not be readable or mutable across organisations.

``ReviewQueueItem`` previously had no ``TenantMixin`` and no ``organization_id``,
so the list, detail, statistics, approve, reject, assign and bulk-approve
endpoints all operated across every organisation's items. A user of organisation
A could see and act on organisation B's review items and counts.

Written against the shared fixtures in ``tests/conftest.py``.
"""

import uuid

from app.models.confidence_review import ReviewQueueItem, ReviewStatus
from app.models.user import User


def _make_item(db_session, org_id, item_type="capability_mapping", item_name=None,
               status=ReviewStatus.PENDING, confidence_score=0.75):
    """Create a review queue item belonging to *org_id*."""
    if item_name is None:
        item_name = f"item-{uuid.uuid4().hex[:8]}"
    row = ReviewQueueItem(
        organization_id=org_id,
        item_type=item_type,
        item_id=1,
        item_name=item_name,
        item_data='{"key":"value"}',
        confidence_score=confidence_score,
        confidence_factors='{"factor":0.8}',
        ai_model_used="test-model",
        status=status,
        review_priority=5,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_user(db_session, org_id, email_prefix="user"):
    """Create a confirmed user in *org_id*."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{email_prefix}-{suffix}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org_id,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


# ---------------------------------------------------------------------------
# ORM-level: the tenant filter must hide another org's rows
# ---------------------------------------------------------------------------

def test_orm_list_is_scoped_to_current_org(db_session, make_org, tenant_ctx):
    """Org A must not see org B's review queue items in an ORM query."""
    org_a, org_b = make_org("a"), make_org("b")
    _make_item(db_session, org_a.id, item_name="A-item")
    b_item = _make_item(db_session, org_b.id, item_name="B-item")

    with tenant_ctx(org_a.id):
        visible = ReviewQueueItem.query.all()
        visible_ids = {row.id for row in visible}

    assert b_item.id not in visible_ids, (
        "TENANT LEAK: org A's ORM query returned org B's review queue item."
    )
    assert all(row.organization_id == org_a.id for row in visible), (
        "TENANT LEAK: query returned items belonging to another organisation."
    )


def test_orm_detail_cannot_reach_foreign_item(db_session, make_org, tenant_ctx):
    """Filtering by another org's item id must return nothing."""
    org_a, org_b = make_org("a"), make_org("b")
    b_item = _make_item(db_session, org_b.id, item_name="B-item")

    db_session.expunge_all()

    with tenant_ctx(org_a.id):
        found = ReviewQueueItem.query.filter_by(id=b_item.id).first()

    assert found is None, (
        f"TENANT LEAK: org A retrieved org B's review item (id={b_item.id}) "
        "by filtering on its id."
    )


def test_orm_statistics_are_scoped(db_session, make_org, tenant_ctx):
    """Org A's statistics must not include org B's items."""
    org_a, org_b = make_org("a"), make_org("b")
    _make_item(db_session, org_a.id, item_name="A-item", status=ReviewStatus.PENDING)
    _make_item(db_session, org_b.id, item_name="B-item", status=ReviewStatus.PENDING)
    _make_item(db_session, org_b.id, item_name="B-item-2", status=ReviewStatus.APPROVED)

    with tenant_ctx(org_a.id):
        items = ReviewQueueItem.query.all()
        pending = [i for i in items if i.status == ReviewStatus.PENDING]
        approved = [i for i in items if i.status == ReviewStatus.APPROVED]

    assert len(items) == 1, f"Org A should see 1 item, saw {len(items)}"
    assert len(pending) == 1
    assert len(approved) == 0, "Org A must not see org B's approved item"


# ---------------------------------------------------------------------------
# API-level: the endpoints must enforce tenant isolation
# ---------------------------------------------------------------------------

class TestReviewQueueAPI:
    """End-to-end tests proving org A never sees or changes org B's items."""

    def test_list_endpoint_hides_foreign_items(self, app, db_session, make_org, client, login_as):
        """GET /api/review-queue must not return another org's items."""
        org_a, org_b = make_org("a"), make_org("b")
        user_a = _make_user(db_session, org_a.id, "a-user")
        user_b = _make_user(db_session, org_b.id, "b-user")

        a_item = _make_item(db_session, org_a.id, item_name="A-visible")
        b_item = _make_item(db_session, org_b.id, item_name="B-hidden")
        # Assign items so they appear in each user's pending list
        a_item.assigned_to_id = user_a.id
        b_item.assigned_to_id = user_b.id
        db_session.commit()

        with app.app_context():
            login_as(client, user_a)
            resp = client.get("/api/review-queue?status=pending")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        item_ids = {item["id"] for item in data["items"]}
        assert a_item.id in item_ids, "Org A's own item must be visible"
        assert b_item.id not in item_ids, (
            f"TENANT LEAK: list endpoint returned org B's item (id={b_item.id})"
        )

    def test_detail_endpoint_hides_foreign_item(self, app, db_session, make_org, client, login_as):
        """GET /api/confidence/queue/<id> must 404 for another org's item."""
        org_a, org_b = make_org("a"), make_org("b")
        user_a = _make_user(db_session, org_a.id, "a-user")
        _make_user(db_session, org_b.id, "b-user")

        _make_item(db_session, org_a.id, item_name="A-item")
        b_item = _make_item(db_session, org_b.id, item_name="B-item")
        db_session.commit()

        with app.app_context():
            login_as(client, user_a)
            resp = client.get(f"/api/confidence/queue/{b_item.id}")

        assert resp.status_code == 404, (
            f"TENANT LEAK: org A retrieved org B's review item detail (id={b_item.id}) "
            f"with status {resp.status_code}"
        )

    def test_statistics_endpoint_excludes_foreign_counts(self, app, db_session, make_org, client, login_as):
        """GET /api/review-queue/statistics must not include org B's counts."""
        org_a, org_b = make_org("a"), make_org("b")
        user_a = _make_user(db_session, org_a.id, "a-user")
        _make_user(db_session, org_b.id, "b-user")

        _make_item(db_session, org_a.id, item_name="A-pending", status=ReviewStatus.PENDING)
        _make_item(db_session, org_b.id, item_name="B-pending", status=ReviewStatus.PENDING)
        _make_item(db_session, org_b.id, item_name="B-approved", status=ReviewStatus.APPROVED)
        db_session.commit()

        with app.app_context():
            login_as(client, user_a)
            resp = client.get("/api/review-queue/statistics")

        assert resp.status_code == 200
        data = resp.get_json()
        stats = data["statistics"]
        assert stats["total_items"] == 1, (
            f"Org A statistics should show 1 item, got {stats['total_items']}"
        )
        assert stats["pending_items"] == 1
        assert stats["approved_items"] == 0, (
            "TENANT LEAK: org A's statistics include org B's approved count"
        )

    def test_approve_endpoint_refuses_foreign_item(self, app, db_session, make_org, client, login_as):
        """POST /api/review-queue/<id>/approve must refuse another org's item."""
        org_a, org_b = make_org("a"), make_org("b")
        user_a = _make_user(db_session, org_a.id, "a-user")
        _make_user(db_session, org_b.id, "b-user")

        _make_item(db_session, org_a.id, item_name="A-item")
        b_item = _make_item(db_session, org_b.id, item_name="B-item")
        db_session.commit()

        with app.app_context():
            login_as(client, user_a)
            resp = client.post(
                f"/api/review-queue/{b_item.id}/approve",
                json={"decision_reason": "should not work"},
            )

        # Must not succeed — the item belongs to another org
        assert resp.status_code in (400, 404), (
            f"TENANT LEAK: org A approved org B's item (id={b_item.id}) "
            f"with status {resp.status_code}: {resp.get_json()}"
        )
        # Verify the item was not mutated
        db_session.expire_all()
        refreshed = db_session.get(ReviewQueueItem, b_item.id)
        assert refreshed is not None, "Item should still exist"
        assert refreshed.status == ReviewStatus.PENDING, (
            "TENANT LEAK: org B's item status was changed by org A's approve attempt"
        )

    def test_reject_endpoint_refuses_foreign_item(self, app, db_session, make_org, client, login_as):
        """POST /api/review-queue/<id>/reject must refuse another org's item."""
        org_a, org_b = make_org("a"), make_org("b")
        user_a = _make_user(db_session, org_a.id, "a-user")
        _make_user(db_session, org_b.id, "b-user")

        _make_item(db_session, org_a.id, item_name="A-item")
        b_item = _make_item(db_session, org_b.id, item_name="B-item")
        db_session.commit()

        with app.app_context():
            login_as(client, user_a)
            resp = client.post(
                f"/api/review-queue/{b_item.id}/reject",
                json={"decision_reason": "should not work"},
            )

        assert resp.status_code in (400, 404), (
            f"TENANT LEAK: org A rejected org B's item (id={b_item.id}) "
            f"with status {resp.status_code}: {resp.get_json()}"
        )
        db_session.expire_all()
        refreshed = db_session.get(ReviewQueueItem, b_item.id)
        assert refreshed.status == ReviewStatus.PENDING, (
            "TENANT LEAK: org B's item status was changed by org A's reject attempt"
        )

    def test_assign_endpoint_refuses_foreign_item(self, app, db_session, make_org, client, login_as):
        """POST /api/confidence/queue/<id>/assign must refuse another org's item."""
        org_a, org_b = make_org("a"), make_org("b")
        user_a = _make_user(db_session, org_a.id, "a-user")
        _make_user(db_session, org_b.id, "b-user")

        _make_item(db_session, org_a.id, item_name="A-item")
        b_item = _make_item(db_session, org_b.id, item_name="B-item")
        db_session.commit()

        with app.app_context():
            login_as(client, user_a)
            resp = client.post(
                f"/api/confidence/queue/{b_item.id}/assign",
                json={"reviewer_id": user_a.id},
            )

        data = resp.get_json()
        assert data["success"] is False, (
            f"TENANT LEAK: org A assigned org B's item (id={b_item.id}): {data}"
        )
        # Verify the item was not assigned
        db_session.expire_all()
        refreshed = db_session.get(ReviewQueueItem, b_item.id)
        assert refreshed.assigned_to_id is None, (
            "TENANT LEAK: org B's item was assigned to org A's user"
        )

    def test_bulk_approve_endpoint_refuses_foreign_items(self, app, db_session, make_org, client, login_as):
        """POST /api/review-queue/bulk-approve must refuse another org's items."""
        org_a, org_b = make_org("a"), make_org("b")
        user_a = _make_user(db_session, org_a.id, "a-user")
        _make_user(db_session, org_b.id, "b-user")

        a_item = _make_item(db_session, org_a.id, item_name="A-item")
        b_item = _make_item(db_session, org_b.id, item_name="B-item")
        db_session.commit()

        with app.app_context():
            login_as(client, user_a)
            resp = client.post(
                "/api/review-queue/bulk-approve",
                json={"item_ids": [a_item.id, b_item.id]},
            )

        data = resp.get_json()
        results = data.get("results", [])
        # Own item should succeed
        successful_ids = {r["review_item_id"] for r in results if r.get("success")}
        failed = [r for r in results if not r.get("success")]
        assert a_item.id in successful_ids, f"Own item should be approved, results: {results}"
        assert any("not found" in r.get("error", "").lower() for r in failed), (
            f"Foreign item must be refused, results: {results}"
        )

        # Verify org B's item was not mutated
        db_session.expire_all()
        refreshed = db_session.get(ReviewQueueItem, b_item.id)
        assert refreshed.status == ReviewStatus.PENDING, (
            "TENANT LEAK: org B's item status was changed by org A's bulk approve"
        )

    def test_own_items_are_visible_and_actionable(self, app, db_session, make_org, client, login_as):
        """Sanity check: org A can see and act on its own items."""
        org_a = make_org("a")
        user_a = _make_user(db_session, org_a.id, "a-user")

        a_item = _make_item(db_session, org_a.id, item_name="A-item")
        a_item.assigned_to_id = user_a.id
        db_session.commit()

        with app.app_context():
            login_as(client, user_a)

            # List
            resp = client.get("/api/review-queue?status=pending")
            assert resp.status_code == 200
            data = resp.get_json()
            item_ids = {item["id"] for item in data["items"]}
            assert a_item.id in item_ids

            # Detail
            resp = client.get(f"/api/confidence/queue/{a_item.id}")
            assert resp.status_code == 200
            detail = resp.get_json()
            assert detail["review_item"]["id"] == a_item.id

            # Statistics
            resp = client.get("/api/review-queue/statistics")
            assert resp.status_code == 200
            stats = resp.get_json()["statistics"]
            assert stats["total_items"] >= 1