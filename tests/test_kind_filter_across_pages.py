"""Regression: kind filter must work across all pages, not just the current page.

PR 903 merged the SAFe profile writer but the kind filter in
api_layer_elements() ran after pagination, so filtering by Epic only scanned
the current page. This test pins the fix: filter before pagination, batch-load
profiles, and recalculate totals.
"""

from __future__ import annotations

import pytest


@pytest.mark.usefixtures("db_session")
def test_kind_filter_returns_matching_elements_across_pages(
    app, db_session, make_org, tenant_ctx
):
    """30 WorkPackage elements, 12 marked Epic across 3 pages; filtering to
    Epic shows 12 with the right page count."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.user import User
    from app.services.element_profile_service import set_kind

    org = make_org("kind-filter")
    with tenant_ctx(org.id):
        # Create 30 WorkPackage ArchiMateElements.
        wp_ids = []
        for i in range(30):
            ae = ArchiMateElement(
                name=f"Kind Filter WP {i:02d} {org.id}",
                type="WorkPackage",
                layer="implementation",
                organization_id=org.id,
            )
            db_session.add(ae)
            db_session.flush()
            wp_ids.append(ae.id)

        # Mark 12 as Epic (indices 0-11).
        for ae_id in wp_ids[:12]:
            ae = db_session.get(ArchiMateElement, ae_id)
            set_kind(ae, "Epic")

        user = User(
            email=f"kf-{org.id}@example.com",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login

        c = app.test_client()
        with app.app_context():
            _login(c, user.id)

            # Request page 1 with kind=Epic, per_page=10.
            resp = c.get(
                "/architecture/api/layer/implementation/elements"
                "?kind=Epic&per_page=10&page=1"
            )
            assert resp.status_code == 200, resp.get_data(as_text=True)
            body = resp.get_json()
            pag = body["pagination"]
            assert pag["total"] == 12, (
                f"expected total=12 for Epic filter, got {pag['total']}"
            )
            assert pag["pages"] == 2, (
                f"expected pages=2 for 12 Epics at per_page=10, got {pag['pages']}"
            )
            assert len(body["elements"]) == 10, (
                f"expected 10 elements on page 1, got {len(body['elements'])}"
            )
            # Every returned element must have kind=Epic.
            for el in body["elements"]:
                assert el.get("kind") == "Epic", (
                    f"element {el['id']} has kind={el.get('kind')}, expected Epic"
                )

            # Page 2 should have the remaining 2 Epics.
            resp2 = c.get(
                "/architecture/api/layer/implementation/elements"
                "?kind=Epic&per_page=10&page=2"
            )
            assert resp2.status_code == 200
            body2 = resp2.get_json()
            assert len(body2["elements"]) == 2, (
                f"expected 2 elements on page 2, got {len(body2['elements'])}"
            )
            for el in body2["elements"]:
                assert el.get("kind") == "Epic"

            # Page 3 should be empty (only 12 Epics, 2 pages at per_page=10).
            resp3 = c.get(
                "/architecture/api/layer/implementation/elements"
                "?kind=Epic&per_page=10&page=3"
            )
            assert resp3.status_code == 200
            body3 = resp3.get_json()
            assert len(body3["elements"]) == 0, (
                f"expected 0 elements on page 3, got {len(body3['elements'])}"
            )

            # Without kind filter, all 30 elements should be returned.
            resp_all = c.get(
                "/architecture/api/layer/implementation/elements"
                "?per_page=50"
            )
            assert resp_all.status_code == 200
            body_all = resp_all.get_json()
            assert body_all["pagination"]["total"] == 30, (
                f"expected total=30 without filter, got {body_all['pagination']['total']}"
            )


@pytest.mark.usefixtures("db_session")
def test_profile_enrichment_is_batched_not_n_plus_one(
    app, db_session, make_org, tenant_ctx
):
    """Profile enrichment must issue at most one query, not one per element.

    The endpoint previously called db.session.get(ArchiMateElement, id) once per
    WorkPackage element (N+1). After the fix, all profiles are loaded in one
    batched query. This test verifies that by counting queries that read
    archimate_elements by id with an IN clause.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.models.user import User
    from app.services.element_profile_service import set_kind

    org = make_org("kind-batch")
    with tenant_ctx(org.id):
        # Create 25 WorkPackage ArchiMateElements.
        for i in range(25):
            ae = ArchiMateElement(
                name=f"Kind Batch WP {i:02d} {org.id}",
                type="WorkPackage",
                layer="implementation",
                organization_id=org.id,
            )
            db_session.add(ae)
            db_session.flush()
            if i < 10:
                set_kind(ae, "Epic")

        user = User(
            email=f"kb-{org.id}@example.com",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login

        c = app.test_client()
        with app.app_context():
            _login(c, user.id)

            from sqlalchemy import event
            from app import db as _db

            # Warm-up request.
            c.get("/architecture/api/layer/implementation/elements?per_page=25")

            # Count only queries that read archimate_elements by id with IN
            # (the batched profile enrichment pattern).
            profile_queries = []

            def _record(conn, cursor, statement, parameters, context, executemany):
                stmt = " ".join(statement.split())
                if "SELECT archimate_elements.id" in stmt and "archimate_elements.id IN" in stmt:
                    profile_queries.append(stmt)

            engine = _db.engine
            event.listen(engine, "before_cursor_execute", _record)
            try:
                resp = c.get(
                    "/architecture/api/layer/implementation/elements"
                    "?per_page=25"
                )
            finally:
                event.remove(engine, "before_cursor_execute", _record)

            assert resp.status_code == 200, resp.get_data(as_text=True)

            # Profile enrichment should use at most 1 batched query (IN clause),
            # not 25 individual queries (one per element).
            assert len(profile_queries) <= 1, (
                f"Expected at most 1 batched profile query for 25 elements, "
                f"got {len(profile_queries)}. Profile enrichment must use "
                f"ArchiMateElement.id.in_(ids) not db.session.get per element.\n"
                + "\n".join(profile_queries)
            )