"""``GET /api/v1/intelligence/yield``, the p95 bucket-edge read, and the
Shape-B trigger.

Fixtures (app, db_session, make_org, tenant_ctx, client, login_as) are
discovered via app/modules/conftest.py's import of
tests.conftest (same pattern as test_impact_route.py).
"""

from __future__ import annotations

import uuid

import pytest

from app.services.prometheus_metrics import INTELLIGENCE_QUERY_DURATION

PINNED_LABELS = {"query": "cross_layer_impact", "depth": "4", "include_derived": "true"}


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"t005-{uuid.uuid4().hex[:10]}@example.com",
        first_name="T005",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org_id, name, layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type=type_, organization_id=org_id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _observe_pinned(n: int, *, seconds: float = 0.01, labels: dict | None = None) -> None:
    lbl = dict(PINNED_LABELS)
    if labels:
        lbl.update(labels)
    for _ in range(n):
        INTELLIGENCE_QUERY_DURATION.labels(**lbl).observe(seconds)


# --- Acceptance criterion 1: fields present (FR-7) --------------------------


def test_computed_branch_carries_every_required_field(app, db_session, make_org, client, login_as):
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    org = make_org("yield-fields")
    user = _user(db_session, org.id)
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    db_session.commit()
    org_id = org.id

    with app.app_context():
        DerivationRunner().run_and_persist(org_id, trigger="on_demand")

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/yield")
    assert resp.status_code == 200
    data = resp.get_json()["data"]

    for key in (
        "explicit_count", "derived_count", "ratio", "computed_at", "engine_version",
        "stale_count", "last_recompute_duration_ms", "p95",
    ):
        assert key in data, f"missing field {key!r}"
    assert "latency_seconds" in data["p95"]
    assert "sample_count" in data["p95"]


# --- Acceptance criterion 2: not-computed vs measured-zero distinguishability


def test_not_computed_branch_carries_state_and_reason_with_null_counts(
    app, db_session, make_org, client, login_as
):
    org = make_org("yield-not-computed")
    user = _user(db_session, org.id)
    db_session.commit()

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/yield")
    assert resp.status_code == 200
    data = resp.get_json()["data"]

    assert data["state"] == "not_computed"
    assert data["reason"] == "derivation_not_computed"
    for key in ("explicit_count", "derived_count", "ratio", "computed_at", "engine_version", "stale_count", "last_recompute_duration_ms"):
        assert data[key] is None


def test_measured_zero_differs_from_not_computed_end_to_end(app, db_session, make_org, client, login_as):
    """The D6 case, driven through the endpoint: a tenant that ran and
    derived zero must read differently from a tenant that never ran."""
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    org_ran_zero = make_org("yield-ran-zero")
    org_never_ran = make_org("yield-never-ran")
    user_ran_zero = _user(db_session, org_ran_zero.id)
    user_never_ran = _user(db_session, org_never_ran.id)
    db_session.commit()
    org_ran_zero_id = org_ran_zero.id

    with app.app_context():
        DerivationRunner().run_and_persist(org_ran_zero_id, trigger="on_demand")

    login_as(client, user_ran_zero)
    resp_ran_zero = client.get("/api/v1/intelligence/yield")
    data_ran_zero = resp_ran_zero.get_json()["data"]

    login_as(client, user_never_ran)
    resp_never_ran = client.get("/api/v1/intelligence/yield")
    data_never_ran = resp_never_ran.get_json()["data"]

    assert data_ran_zero["state"] == "computed"
    assert data_ran_zero["derived_count"] == 0
    assert data_never_ran["state"] == "not_computed"
    assert data_never_ran["derived_count"] is None
    assert data_ran_zero != data_never_ran


def test_mutation_proof_not_computed_zero_seam(app, db_session, make_org, client, login_as, monkeypatch):
    """Acceptance item 12: force the not-computed branch to emit 0 instead of
    null via its isolated seam, and confirm the distinguishability test above
    would go red -- proving that test is not vacuously green.

    Test id for the build report: this test IS the mutation proof for
    ``test_measured_zero_differs_from_not_computed_end_to_end`` /
    ``test_not_computed_branch_carries_state_and_reason_with_null_counts``.
    """
    import app.modules.intelligence.services.query_service as qs

    org = make_org("yield-mutation-proof")
    user = _user(db_session, org.id)
    db_session.commit()

    def _zeroed_counts():
        return {
            "explicit_count": 0,
            "derived_count": 0,
            "ratio": 0,
            "computed_at": None,
            "engine_version": None,
            "stale_count": 0,
            "last_recompute_duration_ms": None,
        }

    monkeypatch.setattr(qs, "_not_computed_counts", _zeroed_counts)

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/yield")
    data = resp.get_json()["data"]

    # With the seam mutated, the not-computed branch now reports 0s -- the
    # exact fabrication the real seam prevents. This confirms the guard is
    # load-bearing: removing it changes the observable payload.
    assert data["derived_count"] == 0
    assert data["state"] == "not_computed"


# --- Acceptance criterion 3: insufficient-samples branch --------------------


def test_p95_null_with_insufficient_samples_below_100(app, db_session, make_org, client, login_as):
    org = make_org("yield-insufficient")
    user = _user(db_session, org.id)
    db_session.commit()

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/yield")
    data = resp.get_json()["data"]
    # sample_count == 0 on a never-observed series.
    assert data["p95"]["sample_count"] >= 0
    if data["p95"]["sample_count"] < 100:
        assert data["p95"]["latency_seconds"] is None
        assert data["p95"]["reason"] == "insufficient_samples_for_p95"


def test_p95_sample_count_boundary_99_vs_100(app, db_session, make_org, client, login_as):
    from app.modules.intelligence.services.latency_probe import read_p95_bucket_edge

    with app.app_context():
        distinct_query = f"boundary-test-{uuid.uuid4().hex[:8]}"
        for _ in range(99):
            INTELLIGENCE_QUERY_DURATION.labels(
                query=distinct_query, depth="4", include_derived="true"
            ).observe(0.02)
        result_99 = read_p95_bucket_edge(query=distinct_query, depth="4", include_derived="true")
        assert result_99["sample_count"] == 99
        assert result_99["latency_seconds"] is None
        assert result_99["reason"] == "insufficient_samples_for_p95"

        INTELLIGENCE_QUERY_DURATION.labels(
            query=distinct_query, depth="4", include_derived="true"
        ).observe(0.02)
        result_100 = read_p95_bucket_edge(query=distinct_query, depth="4", include_derived="true")
        assert result_100["sample_count"] == 100
        assert result_100["latency_seconds"] is not None
        assert result_100["reason"] is None


# --- Acceptance criterion 4: no fabricated target ---------------------------


def test_no_fabricated_target_field_anywhere_in_payload(app, db_session, make_org, client, login_as):
    import json

    org = make_org("yield-no-target")
    user = _user(db_session, org.id)
    db_session.commit()

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/yield")
    body_text = json.dumps(resp.get_json())

    for forbidden in ("\"target\"", "\"goal\"", "\"slo_target\"", "threshold_target"):
        assert forbidden not in body_text, f"found forbidden fabricated-target key {forbidden!r}"


# --- Acceptance criterion 5: p95 source is the histogram, pinned selector --


def test_p95_reads_declared_bucket_boundaries_and_moves_across_them(app):
    from app.modules.intelligence.services.latency_probe import read_p95_bucket_edge

    with app.app_context():
        distinct_query = f"bucket-edge-{uuid.uuid4().hex[:8]}"
        for _ in range(100):
            INTELLIGENCE_QUERY_DURATION.labels(
                query=distinct_query, depth="4", include_derived="true"
            ).observe(0.02)  # falls in the 0.025 declared bucket
        result = read_p95_bucket_edge(query=distinct_query, depth="4", include_derived="true")
        # 0.02s falls in the declared 0.025 bucket -- the reported value is
        # always one of the histogram's own declared boundaries.
        assert result["latency_seconds"] == 0.025


def test_p95_unaffected_by_different_include_derived_or_depth_label(app):
    from app.modules.intelligence.services.latency_probe import read_p95_bucket_edge

    with app.app_context():
        distinct_query = f"label-isolation-{uuid.uuid4().hex[:8]}"
        for _ in range(100):
            INTELLIGENCE_QUERY_DURATION.labels(
                query=distinct_query, depth="4", include_derived="true"
            ).observe(0.01)
        baseline = read_p95_bucket_edge(query=distinct_query, depth="4", include_derived="true")

        # Observations on a DIFFERENT include_derived label must not move it.
        for _ in range(500):
            INTELLIGENCE_QUERY_DURATION.labels(
                query=distinct_query, depth="4", include_derived="false"
            ).observe(4.5)
        after_different_include_derived = read_p95_bucket_edge(
            query=distinct_query, depth="4", include_derived="true"
        )
        assert after_different_include_derived == baseline

        # Observations on a DIFFERENT depth label must not move it either.
        for _ in range(500):
            INTELLIGENCE_QUERY_DURATION.labels(
                query=distinct_query, depth="3", include_derived="true"
            ).observe(4.5)
        after_different_depth = read_p95_bucket_edge(
            query=distinct_query, depth="4", include_derived="true"
        )
        assert after_different_depth == baseline


def test_repeated_yield_calls_do_not_move_the_pinned_series(app, db_session, make_org, client, login_as):
    """D2 self-pollution guard: calling the yield endpoint 200 times must not
    change the pinned cross_layer_impact series' sample_count -- AND must
    move derivation_yield's own series by the same number of calls (D-3,
    refuter round 2). Checking only the negative half (pinned series
    unaffected) would leave a mislabelling regression -- one that reused the
    pinned selector's exact labels for the yield endpoint's own probe --
    green, since nothing would have moved either series and the test would
    never notice. Asserting both halves pins that the two label sets are
    genuinely disjoint, not just that nothing happened.
    """
    from app.modules.intelligence.services.latency_probe import read_p95_bucket_edge

    # derivation_yield's own probe (query_service.py's
    # `record_query_latency("derivation_yield")` call) never sets
    # scope.depth/scope.include_derived, so it always resolves to
    # depth="unknown", include_derived="false" -- matching latency_probe.py's
    # own fallback labelling, not the pinned NFR5 selector.
    own_labels = {"query": "derivation_yield", "depth": "unknown", "include_derived": "false"}

    org = make_org("yield-self-pollution")
    user = _user(db_session, org.id)
    db_session.commit()

    login_as(client, user)
    first = client.get("/api/v1/intelligence/yield").get_json()["data"]
    before_count = first["p95"]["sample_count"]
    before_own = read_p95_bucket_edge(min_samples=0, **own_labels)["sample_count"]

    # before_own is captured AFTER the "first" call above, so that call's
    # observation is already baked into the baseline -- only count
    # observations made from here on.
    calls_made = 0
    for _ in range(200):
        client.get("/api/v1/intelligence/yield")
        calls_made += 1

    last = client.get("/api/v1/intelligence/yield").get_json()["data"]
    calls_made += 1
    after_count = last["p95"]["sample_count"]
    after_own = read_p95_bucket_edge(min_samples=0, **own_labels)["sample_count"]

    assert after_count == before_count, (
        "calling the yield endpoint must not move the pinned "
        "cross_layer_impact series -- derivation_yield's own latency must "
        "not pollute the series it reports on"
    )
    assert after_own - before_own == calls_made, (
        "derivation_yield's OWN latency series must move by exactly the "
        "number of calls made -- if this does not move, the endpoint's own "
        "probe is not observing at all (or is mislabelled onto a series "
        "this test isn't watching), which the pinned-series-unmoved "
        "assertion alone cannot detect"
    )


# --- Acceptance criterion 6: above-top-bucket honesty ------------------------


def test_above_highest_bucket_reports_null_with_reason_and_still_fires_shape_b(app):
    from app.modules.intelligence.services.latency_probe import read_p95_bucket_edge

    with app.app_context():
        distinct_query = f"above-bucket-{uuid.uuid4().hex[:8]}"
        for _ in range(100):
            INTELLIGENCE_QUERY_DURATION.labels(
                query=distinct_query, depth="4", include_derived="true"
            ).observe(9.0)  # above the highest declared bucket (5.0)
        result = read_p95_bucket_edge(query=distinct_query, depth="4", include_derived="true")

    assert result["latency_seconds"] is None
    assert result["reason"] == "p95_above_highest_bucket"
    assert result["p95_exceeds_seconds"] == 5.0


def test_shape_b_trigger_fires_on_pinned_series_breach(app, db_session, make_org, client, login_as, monkeypatch):
    """Drives the trigger through the real pinned series
    (query=cross_layer_impact, depth=4, include_derived=true)."""
    org = make_org("yield-shape-b")
    user = _user(db_session, org.id)
    db_session.commit()

    _observe_pinned(100, seconds=4.9)  # above 2.0s threshold, within top bucket (5.0)

    caught = []

    def _capturing_warning(msg, *args, **kwargs):
        caught.append(msg % args if args else msg)

    import logging

    logger = logging.getLogger("archie.intelligence.oa2")
    monkeypatch.setattr(logger, "warning", _capturing_warning)

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/yield")
    data = resp.get_json()["data"]

    assert data["shape_b_trigger"] is not None
    assert data["shape_b_trigger"]["threshold_seconds"] == 2.0
    assert data["shape_b_trigger"]["sample_count"] >= 100
    assert data["shape_b_trigger"]["recorded_at"]
    assert any("shape_b_trigger" in m for m in caught)


def test_shape_b_trigger_creates_no_work_item_or_queue_entry(app, db_session, make_org, client, login_as):
    """No build/work item, task, queue entry or state change -- it opens a
    decision, it does not start work (task 02 acceptance item 8)."""
    org = make_org("yield-shape-b-no-workitem")
    user = _user(db_session, org.id)
    db_session.commit()

    _observe_pinned(100, seconds=4.9)

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/yield")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["shape_b_trigger"] is not None
    # No table row anywhere claims to be a "trigger" record; the response
    # body itself IS the record (D11 -- a dataclass, not a workflow).
    from app.modules.intelligence.services.observability import ShapeBTriggerRecord

    assert set(data["shape_b_trigger"].keys()) == set(ShapeBTriggerRecord.__dataclass_fields__.keys())


# --- Acceptance criterion 7: scope honesty (D4) ------------------------------


def test_p95_scope_is_process_estate_wide_and_nested(app, db_session, make_org, client, login_as):
    org = make_org("yield-scope")
    user = _user(db_session, org.id)
    db_session.commit()

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/yield")
    data = resp.get_json()["data"]

    assert isinstance(data["p95"], dict)
    assert data["p95"]["scope"] == "process_estate_wide"
    # p95 must never be flattened alongside the per-tenant counts.
    assert "latency_seconds" not in data
    assert "p95_sample_count" not in data


# --- Acceptance criterion 9: authorisation -----------------------------------


def test_yield_route_rejects_anonymous(app, client):
    resp = client.get("/api/v1/intelligence/yield")
    assert resp.status_code in (302, 401)


# --- Acceptance criterion 10: tenancy ----------------------------------------


def test_yield_response_is_tenant_scoped(app, db_session, make_org, client, login_as):
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    org_a = make_org("yield-tenant-a")
    org_b = make_org("yield-tenant-b")
    user_a = _user(db_session, org_a.id)
    user_b = _user(db_session, org_b.id)
    a1 = _element(db_session, org_a.id, "a1")
    a2 = _element(db_session, org_a.id, "a2")
    _relationship(db_session, org_a.id, a1, a2)
    db_session.commit()
    org_a_id = org_a.id

    with app.app_context():
        DerivationRunner().run_and_persist(org_a_id, trigger="on_demand")

    login_as(client, user_a)
    data_a = client.get("/api/v1/intelligence/yield").get_json()["data"]

    login_as(client, user_b)
    data_b = client.get("/api/v1/intelligence/yield").get_json()["data"]

    assert data_a["state"] == "computed"
    assert data_b["state"] == "not_computed", (
        "org_a having run must not make org_b report computed"
    )
    assert data_a["organization_id"] == org_a_id
    assert data_b["organization_id"] == org_b.id


# --- Acceptance criterion 11: store agreement (D8) ---------------------------


def test_recompute_and_yield_agree_on_explicit_derived_ratio(app, db_session, make_org, client, login_as):
    org = make_org("yield-store-agreement")
    user = _user(db_session, org.id)
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    c = _element(db_session, org.id, "C")
    _relationship(db_session, org.id, a, b, "Composition")
    _relationship(db_session, org.id, b, c, "Serving")
    db_session.commit()

    login_as(client, user)
    recompute_resp = client.post(
        "/api/v1/intelligence/derivation/recompute", json={"scope": "tenant"}
    )
    assert recompute_resp.status_code == 200
    recompute_data = recompute_resp.get_json()["data"]

    yield_resp = client.get("/api/v1/intelligence/yield")
    assert yield_resp.status_code == 200
    yield_data = yield_resp.get_json()["data"]

    assert yield_data["explicit_count"] == recompute_data["explicit_count"]
    assert yield_data["derived_count"] == recompute_data["derived_count"]
    if recompute_data["ratio"] is None:
        assert yield_data["ratio"] is None
    else:
        assert yield_data["ratio"] == pytest.approx(recompute_data["ratio"])


# --- Acceptance criterion 13: NFR-8 / Release 1 completeness ----------------


def test_exactly_thirteen_intelligence_routes_registered(app):
    rules = [
        rule for rule in app.url_map.iter_rules()
        if str(rule).startswith("/api/v1/intelligence")
    ]
    assert len(rules) == 13, (
        f"expected exactly 13 /api/v1/intelligence/* rules (recompute POST, "
        f"derived GET, impact GET, risk GET, portfolio GET, programme GET, "
        f"strategy GET, accountability GET, data GET, compliance GET, "
        f"traceability GET, value-streams-at-risk GET, yield GET) -- "
        f"found {len(rules)}: {[str(r) for r in rules]}"
    )


def test_release1_l0_engine_version_matches_runner_constant_on_stored_rows(
    app, db_session, make_org, client, login_as
):
    from app.modules.intelligence.services.derivation_runner import ENGINE_VERSION, DerivationRunner

    org = make_org("yield-l0-engine-version")
    user = _user(db_session, org.id)
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    c = _element(db_session, org.id, "C")
    _relationship(db_session, org.id, a, b, "Composition")
    _relationship(db_session, org.id, b, c, "Serving")
    db_session.commit()
    org_id = org.id

    with app.app_context():
        DerivationRunner().run_and_persist(org_id, trigger="on_demand")

    login_as(client, user)
    data = client.get("/api/v1/intelligence/yield").get_json()["data"]

    assert data["engine_version"] == [ENGINE_VERSION]
