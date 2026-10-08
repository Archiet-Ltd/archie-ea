"""Live cross-org leak: api_health_scorecard's inference_rels count was unscoped.

`ArchitectureInferenceRelationship` carries no `organization_id` column, and
`api_health_scorecard` (app/modules/architecture/routes/archimate_crud/routes.py)
counted it with a bare `db.session.query(func.count(InfRel.id)).scalar()` --
no filter at all -- while every other count in the same function went through
the route's own `_scope()` helper. Any signed-in user got a platform-wide
count folded into `tests.relationship_density.detail.total_relationships`.

Fix: scope the count by joining to the source `ArchiMateElement`, which does
carry `organization_id`, through the existing `_scope()` helper.

This test seeds inference-relationship rows for two different organisations
via their real elements, and asserts the scorecard for org A's own user
reflects only org A's rows.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.archimate_core import ArchiMateElement
from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship
from app.models.user import User

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, make_org, label):
    org = make_org(label)
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label}-{suffix}@example.com",
        first_name="Tenant",
        last_name="Fence",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    user.password = "Sup3rSecret!23"
    db_session.add(user)
    db_session.flush()
    return org, user


def test_inference_rels_count_is_scoped_to_caller_organisation(
    app, db_session, make_org, client, login_as
):
    org_a, user_a = _make_user(db_session, make_org, "tenant-fence-a")
    org_b, _user_b = _make_user(db_session, make_org, "tenant-fence-b")

    # Org A: one inference relationship between two real org-A elements.
    src_a = ArchiMateElement(name="Org A Source", type="Node", layer="technology",
                              organization_id=org_a.id)
    tgt_a = ArchiMateElement(name="Org A Target", type="Node", layer="technology",
                              organization_id=org_a.id)
    db_session.add_all([src_a, tgt_a])
    db_session.flush()

    rel_a = ArchitectureInferenceRelationship(
        architecture_id=1,
        source_type="ArchiMateElement", source_id=src_a.id,
        target_type="ArchiMateElement", target_id=tgt_a.id,
        rel_type="serving",
    )
    db_session.add(rel_a)

    # Org B: three inference relationships between org-B elements -- the
    # platform-wide count the unpatched code leaked into org A's scorecard.
    for i in range(3):
        src_b = ArchiMateElement(name=f"Org B Source {i}", type="Node", layer="technology",
                                  organization_id=org_b.id)
        tgt_b = ArchiMateElement(name=f"Org B Target {i}", type="Node", layer="technology",
                                  organization_id=org_b.id)
        db_session.add_all([src_b, tgt_b])
        db_session.flush()
        db_session.add(ArchitectureInferenceRelationship(
            architecture_id=1,
            source_type="ArchiMateElement", source_id=src_b.id,
            target_type="ArchiMateElement", target_id=tgt_b.id,
            rel_type="serving",
        ))
    db_session.flush()

    login_as(client, user_a)
    resp = client.get("/architecture/api/health-scorecard")
    assert resp.status_code == 200
    data = resp.get_json()

    total_relationships = data["tests"]["relationship_density"]["detail"]["total_relationships"]

    assert total_relationships == 1, (
        "CROSS-TENANT LEAK: api_health_scorecard's inference_rels count reported "
        f"{total_relationships} relationships to an org-A user -- org A owns exactly 1 "
        "inference relationship; the other 3 belong to org B and must not appear here."
    )
