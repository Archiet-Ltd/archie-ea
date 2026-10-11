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

Round 2 (T-hotfix-441-round2) extends this file with three more unscoped
counts the lead's review found in the same route:

- ``has_status`` (P441-1): a raw-SQL lifecycle count with no org predicate at
  all (``test_lifecycle_has_status_count_is_scoped_to_caller_organisation``).
- cross-layer pairs (P441-2): the raw-SQL ``_cross_sql`` union missing an org
  predicate on both halves
  (``test_cross_layer_pairs_are_scoped_to_caller_organisation``).
- ``semantic_rels`` (P441-3, plus the already-safe ``rel_type_rows`` half):
  ``InfRel.rel_type`` counts (``inf_type_rows``) unscoped, same leak class as
  ``inference_rels`` above
  (``test_semantic_relationship_ratio_is_scoped_to_caller_organisation``).

P441-4 (``legacy_ids`` / ``connected_count``) is fixed in the route too, but
is not asserted here: a live reproduction (see the round-2 build report)
showed the *intermediate* ``legacy_ids`` set did pick up another
organisation's element ids pre-fix, but the *final* ``connected_count`` query
is itself an auto-scoped ORM-attribute query against ``ArchiMateElement``
(see the corrected comment in the route), so the JSON response's
``orphan_rate`` was never actually wrong either before or after this fix --
there is no observable before/after difference for a route-level test to
assert here.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
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


def test_lifecycle_has_status_count_is_scoped_to_caller_organisation(
    app, db_session, make_org, client, login_as
):
    """P441-1: the raw-SQL ``has_status`` lifecycle count had no org predicate."""
    org_a, user_a = _make_user(db_session, make_org, "p441-1-a")
    org_b, _user_b = _make_user(db_session, make_org, "p441-1-b")

    # Org A: 2 elements with plateau set, 1 without -> has_status == 2.
    db_session.add_all([
        ArchiMateElement(name="A Plateau 1", type="Node", layer="technology",
                          organization_id=org_a.id, togaf_plateau="Current"),
        ArchiMateElement(name="A Plateau 2", type="Node", layer="technology",
                          organization_id=org_a.id, togaf_plateau="Future"),
        ArchiMateElement(name="A No Plateau", type="Node", layer="technology",
                          organization_id=org_a.id),
    ])

    # Org B: more plateau-tagged elements than org A -- if the leak were still
    # present this inflates org A's reported has_status past 2.
    db_session.add_all([
        ArchiMateElement(name=f"B Plateau {i}", type="Node", layer="technology",
                          organization_id=org_b.id, togaf_plateau="Current")
        for i in range(5)
    ])
    db_session.flush()

    login_as(client, user_a)
    resp = client.get("/architecture/api/health-scorecard")
    assert resp.status_code == 200
    data = resp.get_json()

    lifecycle_detail = data["tests"]["lifecycle_status"]["detail"]
    assert lifecycle_detail["has_status"] == 2, (
        "CROSS-TENANT LEAK: api_health_scorecard's has_status (lifecycle) count "
        f"reported {lifecycle_detail['has_status']} to an org-A user -- org A owns "
        "exactly 2 plateau-tagged elements; org B's 5 must not appear here."
    )
    assert lifecycle_detail["total"] == 3, (
        "org A's total_elements should be its own 3 elements, not the 8 across both orgs"
    )


def test_cross_layer_pairs_are_scoped_to_caller_organisation(
    app, db_session, make_org, client, login_as
):
    """P441-2: the raw-SQL ``_cross_sql`` union had no org predicate on either half."""
    org_a, user_a = _make_user(db_session, make_org, "p441-2-a")
    org_b, _user_b = _make_user(db_session, make_org, "p441-2-b")

    # Org A: one business->application legacy relationship (non-structural type).
    biz_a = ArchiMateElement(name="A Biz", type="BusinessProcess", layer="business",
                              organization_id=org_a.id)
    app_a = ArchiMateElement(name="A App", type="ApplicationComponent", layer="application",
                              organization_id=org_a.id)
    db_session.add_all([biz_a, app_a])
    db_session.flush()
    db_session.add(ArchiMateRelationship(
        source_id=biz_a.id, target_id=app_a.id, type="serving", organization_id=org_a.id,
    ))

    # Org B: several strategy->technology relationships -- a layer pair org A owns
    # none of. If the union leaks, this pair (or an inflated count on org A's own
    # pair) shows up in org A's response.
    for i in range(4):
        strat_b = ArchiMateElement(name=f"B Strategy {i}", type="Resource", layer="strategy",
                                    organization_id=org_b.id)
        tech_b = ArchiMateElement(name=f"B Tech {i}", type="Node", layer="technology",
                                   organization_id=org_b.id)
        db_session.add_all([strat_b, tech_b])
        db_session.flush()
        db_session.add(ArchiMateRelationship(
            source_id=strat_b.id, target_id=tech_b.id, type="serving", organization_id=org_b.id,
        ))
    db_session.flush()

    login_as(client, user_a)
    resp = client.get("/architecture/api/health-scorecard")
    assert resp.status_code == 200
    data = resp.get_json()

    pairs = data["tests"]["cross_layer_trace"]["detail"]["pairs"]
    # SUM(cnt) comes back as NUMERIC from the raw-SQL union, which Flask's JSON
    # encoder renders as a string to avoid precision loss -- cast for comparison.
    pairs_by_label = {(p["from"], p["to"]): int(p["count"]) for p in pairs}

    assert ("strategy", "technology") not in pairs_by_label, (
        "CROSS-TENANT LEAK: api_health_scorecard's cross-layer pairs included "
        f"org B's strategy->technology pair in org A's response: {pairs}"
    )
    assert pairs_by_label.get(("business", "application")) == 1, (
        "org A owns exactly 1 business->application pair; got "
        f"{pairs_by_label.get(('business', 'application'))} in {pairs}"
    )


def test_semantic_relationship_ratio_is_scoped_to_caller_organisation(
    app, db_session, make_org, client, login_as
):
    """P441-3 (``inf_type_rows``) and a sanity check of ``rel_type_rows`` (already safe):
    the ``semantic_rels`` ratio is built from both tables' type-grouped counts, so
    either one leaking inflates org A's reported ratio with org B's rows.
    """
    org_a, user_a = _make_user(db_session, make_org, "p441-3-a")
    org_b, _user_b = _make_user(db_session, make_org, "p441-3-b")

    # Org A: 1 semantic + 1 structural legacy relationship, 1 semantic inference
    # relationship -> total_rels = 3, semantic_rels = 2 (serving + triggering),
    # semantic_pct = round(2/3*100) = 67.
    sa1 = ArchiMateElement(name="A S1", type="Node", layer="technology", organization_id=org_a.id)
    ta1 = ArchiMateElement(name="A T1", type="Node", layer="technology", organization_id=org_a.id)
    sa2 = ArchiMateElement(name="A S2", type="Node", layer="technology", organization_id=org_a.id)
    ta2 = ArchiMateElement(name="A T2", type="Node", layer="technology", organization_id=org_a.id)
    sa3 = ArchiMateElement(name="A S3", type="Node", layer="technology", organization_id=org_a.id)
    ta3 = ArchiMateElement(name="A T3", type="Node", layer="technology", organization_id=org_a.id)
    db_session.add_all([sa1, ta1, sa2, ta2, sa3, ta3])
    db_session.flush()
    db_session.add(ArchiMateRelationship(
        source_id=sa1.id, target_id=ta1.id, type="serving", organization_id=org_a.id,
    ))
    db_session.add(ArchiMateRelationship(
        source_id=sa2.id, target_id=ta2.id, type="composition", organization_id=org_a.id,
    ))
    db_session.add(ArchitectureInferenceRelationship(
        architecture_id=1, source_type="ArchiMateElement", source_id=sa3.id,
        target_type="ArchiMateElement", target_id=ta3.id, rel_type="triggering",
    ))

    # Org B: noise heavily weighted toward semantic types in both tables -- if
    # either rel_type_rows or inf_type_rows leaks, org A's ratio moves well past 67.
    for i in range(10):
        sb = ArchiMateElement(name=f"B Legacy S{i}", type="Node", layer="technology",
                               organization_id=org_b.id)
        tb = ArchiMateElement(name=f"B Legacy T{i}", type="Node", layer="technology",
                               organization_id=org_b.id)
        db_session.add_all([sb, tb])
        db_session.flush()
        db_session.add(ArchiMateRelationship(
            source_id=sb.id, target_id=tb.id, type="serving", organization_id=org_b.id,
        ))
    for i in range(10):
        sb2 = ArchiMateElement(name=f"B Inf S{i}", type="Node", layer="technology",
                                organization_id=org_b.id)
        tb2 = ArchiMateElement(name=f"B Inf T{i}", type="Node", layer="technology",
                                organization_id=org_b.id)
        db_session.add_all([sb2, tb2])
        db_session.flush()
        db_session.add(ArchitectureInferenceRelationship(
            architecture_id=1, source_type="ArchiMateElement", source_id=sb2.id,
            target_type="ArchiMateElement", target_id=tb2.id, rel_type="triggering",
        ))
    db_session.flush()

    login_as(client, user_a)
    resp = client.get("/architecture/api/health-scorecard")
    assert resp.status_code == 200
    data = resp.get_json()

    density_detail = data["tests"]["relationship_density"]["detail"]
    semantic_pct = density_detail["sub_tests"]["semantic_ratio"]["value"]

    assert semantic_pct == 67, (
        "CROSS-TENANT LEAK: api_health_scorecard's semantic relationship ratio "
        f"reported {semantic_pct}% to an org-A user -- org A's own data computes to "
        f"67% (2 semantic of 3 total); org B's rows must not appear here. Full detail: "
        f"{density_detail}"
    )
    assert density_detail["total_relationships"] == 3, (
        f"org A owns exactly 3 relationships (2 legacy + 1 inference); got "
        f"{density_detail['total_relationships']}"
    )
