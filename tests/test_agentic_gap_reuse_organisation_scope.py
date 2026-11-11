"""The reuse endpoints look a capability up by the identifier in the URL or body; an identifier
that belongs to another organisation is not found, even when that row is already loaded."""

from __future__ import annotations

import inspect
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _foreign_capability(db_session, make_org):
    from app.models.unified_capability import UnifiedCapability

    org_a, org_b = make_org("a"), make_org("b")
    capability = UnifiedCapability(
        name=f"Capability of B {uuid.uuid4().hex[:8]}", organization_id=org_b.id, scope="tenant", level=1
    )
    db_session.add(capability)
    db_session.flush()
    # The row is in the session's identity map, as it is after any earlier read in the same session.
    assert db_session.get(UnifiedCapability, capability.id) is capability
    return org_a, org_b, capability


def test_reuse_candidates_does_not_find_another_organisations_capability(db_session, make_org, tenant_ctx):
    """Asserts the lookup refuses an identifier owned by another organisation."""
    from app.main.routes_agentic_gaps import get_reuse_candidates

    org_a, _org_b, capability = _foreign_capability(db_session, make_org)
    with tenant_ctx(org_a.id):
        response = inspect.unwrap(get_reuse_candidates)(capability.id)
    body, status = response
    assert status == 404
    assert capability.name not in body.get_data(as_text=True)


def test_reuse_recommendation_does_not_find_another_organisations_capability(
    db_session, make_org, app, tenant_ctx
):
    """Asserts the lookup refuses an identifier owned by another organisation."""
    from app.main.routes_agentic_gaps import get_reuse_recommendation

    org_a, _org_b, capability = _foreign_capability(db_session, make_org)
    with app.test_request_context("/", method="POST", json={"gap_id": capability.id}):
        from flask import g

        g.current_org_id = org_a.id
        body, status = inspect.unwrap(get_reuse_recommendation)()
    assert status == 404
    assert capability.name not in body.get_data(as_text=True)


def test_reuse_candidates_finds_the_organisations_own_capability(db_session, make_org, tenant_ctx):
    """An organisation's own capability is still found (the request goes on past the lookup)."""
    from unittest import mock

    from app.main.routes_agentic_gaps import get_reuse_candidates
    from app.models.unified_capability import UnifiedCapability

    org_a = make_org("a")
    own = UnifiedCapability(name=f"Own {uuid.uuid4().hex[:8]}", organization_id=org_a.id, scope="tenant", level=1)
    db_session.add(own)
    db_session.flush()
    with tenant_ctx(org_a.id), mock.patch(
        "app.services.application_similarity_service.ApplicationSimilarityService.find_reuse_candidates_for_gap",
        return_value=[],
    ):
        response = inspect.unwrap(get_reuse_candidates)(own.id)
    assert response.status_code == 200
