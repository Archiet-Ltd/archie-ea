"""Lifecycle status normalization for rationalization scoring.

``_map_deployment_to_lifecycle`` is the lifecycle dimension of
``evaluate_readiness`` (read by the data-readiness tiles) and feeds the
technical-health lifecycle adjustment in ``calculate_app_score``. It must
resolve an Abacus lifecycle code the same way regardless of case (production
data carries both "2.1 STRATEGIC" and "2.1 strategic" for the same code), and
agree with the one canonical mapping in ``app.config.abacus_field_mapping``
(``normalize_lifecycle_status`` / ``LIFECYCLE_STATUS_MAP``) rather than
resolving a code to one lifecycle there and a different one here.

Before this, a second, independent mapping (``ABACUS_LIFECYCLE_MAP``) only
recognized upper-case codes and disagreed with the canonical mapping on
"5. DECOMMISSIONED" (deprecated vs retired) and silently fell through
"4.4 STOPPED" to a default of "operational" because the canonical mapping had
never been given an entry for it. ``SCOREABLE_LIFECYCLE_VALUES`` -- the list
the real product-facing portfolio scorer, elimination-candidates query and
bulk "unscored" scope filter on -- was exact-case and upper-case only, so a
lower-case-coded application was silently excluded from scoring entirely,
regardless of how complete its data was.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config.abacus_field_mapping import LIFECYCLE_STATUS_MAP, normalize_lifecycle_status
from app.services.rationalization_scoring_service import RationalizationScoringService


def _app(lifecycle_status=None, deployment_status=None):
    """A minimal stand-in for ApplicationComponent: _map_deployment_to_lifecycle
    only ever reads these two attributes."""
    return SimpleNamespace(
        lifecycle_status=lifecycle_status, deployment_status=deployment_status
    )


class TestNormalizeLifecycleStatus:
    """The one canonical mapping every lifecycle consumer reads."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2.1 STRATEGIC", "operational"),
            ("2.1 strategic", "operational"),
            ("2.2 Tactical", "operational"),
            ("1. undetermined", "planning"),
            ("3. SUNSET", "deprecated"),
            ("4.1 Decom Decided", "deprecated"),
            ("4.2 decom planned", "deprecated"),
            ("4.3 read-only", "deprecated"),
            # Absent from the mapping table silently falls through to the
            # "operational" default -- wrong for a code that, like its
            # 4.1-4.3 siblings, means an application already in the
            # decommissioning process.
            ("4.4 STOPPED", "deprecated"),
            ("4.4 stopped", "deprecated"),
            # Decommissioned means retired, not merely deprecated -- matches
            # this same module's "decommissioned": "retired" standard-name
            # entry and LIFECYCLE_TO_DEPLOYMENT's "5. decommissioned": "retired".
            ("5. DECOMMISSIONED", "retired"),
            ("5. decommissioned", "retired"),
            ("  5. decommissioned  ".strip(), "retired"),
            ("operational", "operational"),
            ("production", "operational"),
        ],
    )
    def test_known_codes_normalize_regardless_of_case(self, raw, expected):
        assert normalize_lifecycle_status(raw) == expected

    def test_unknown_value_defaults_to_operational(self):
        assert normalize_lifecycle_status("not a real status") == "operational"

    def test_empty_value_defaults_to_operational(self):
        assert normalize_lifecycle_status("") == "operational"
        assert normalize_lifecycle_status(None) == "operational"

    def test_every_scoreable_lifecycle_value_is_a_canonical_mapping_key(self):
        """RationalizationScoringService.SCOREABLE_LIFECYCLE_VALUES must stay
        derived from this one canonical table, not a second hand-maintained
        list that can silently drift out of agreement with it."""
        assert RationalizationScoringService.SCOREABLE_LIFECYCLE_VALUES == frozenset(
            LIFECYCLE_STATUS_MAP.keys()
        )


class TestMapDeploymentToLifecycle:
    """RationalizationScoringService._map_deployment_to_lifecycle: the
    lifecycle dimension of evaluate_readiness, and the lifecycle input to the
    technical-health score adjustment."""

    @pytest.mark.parametrize(
        "raw",
        ["2.1 STRATEGIC", "2.1 strategic", "2.1 Strategic", "  2.1 strategic  "],
    )
    def test_abacus_code_resolves_the_same_regardless_of_case_or_padding(self, raw):
        result = RationalizationScoringService._map_deployment_to_lifecycle(
            _app(lifecycle_status=raw)
        )
        assert result == {"lifecycle": "operational", "data_quality_flag": False}

    def test_already_canonical_value_is_recognized(self):
        result = RationalizationScoringService._map_deployment_to_lifecycle(
            _app(lifecycle_status="deprecated")
        )
        assert result == {"lifecycle": "deprecated", "data_quality_flag": False}

    def test_decommissioned_resolves_to_retired_not_deprecated(self):
        """A fully decommissioned application must score as "retired" (-30),
        not merely "deprecated" (-15); a second, independent mapping used to
        disagree with the canonical mapping for the exact same code."""
        result = RationalizationScoringService._map_deployment_to_lifecycle(
            _app(lifecycle_status="5. DECOMMISSIONED")
        )
        assert result == {"lifecycle": "retired", "data_quality_flag": False}

    def test_stopped_resolves_to_deprecated_like_its_sibling_codes(self):
        """4.1/4.2/4.3 (decommissioning in progress) all resolve to
        "deprecated"; 4.4 "stopped" sits in the same sequence and must not
        silently default to "operational"."""
        result = RationalizationScoringService._map_deployment_to_lifecycle(
            _app(lifecycle_status="4.4 STOPPED")
        )
        assert result == {"lifecycle": "deprecated", "data_quality_flag": False}

    def test_unrecognized_lifecycle_value_is_flagged_as_a_data_quality_issue(self):
        result = RationalizationScoringService._map_deployment_to_lifecycle(
            _app(lifecycle_status="not a real status")
        )
        assert result == {"lifecycle": "operational", "data_quality_flag": True}

    def test_falls_back_to_deployment_status_when_lifecycle_status_is_empty(self):
        result = RationalizationScoringService._map_deployment_to_lifecycle(
            _app(lifecycle_status=None, deployment_status="Production")
        )
        assert result == {"lifecycle": "operational", "data_quality_flag": False}

    def test_both_fields_empty_defaults_to_operational_and_flagged(self):
        result = RationalizationScoringService._map_deployment_to_lifecycle(
            _app(lifecycle_status=None, deployment_status=None)
        )
        assert result == {"lifecycle": "operational", "data_quality_flag": True}


class TestScoreableLifecycleFilterIsCaseInsensitive:
    """The real product-facing paths (calculate_portfolio_scores,
    get_elimination_candidates, and the bulk-score "unscored" scope) filter
    ApplicationComponent.lifecycle_status with SCOREABLE_LIFECYCLE_VALUES. If
    that list were upper-case/canonical-only and the SQL filter exact-case, a
    lower-case-coded application (as occurs in real Abacus data) would never
    match and would be silently skipped by every one of those paths
    regardless of how complete its data was."""

    def test_lower_case_abacus_coded_app_is_included_in_portfolio_scoring(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.models.application_portfolio import ApplicationComponent

        org = make_org("lifecycle-case-fence")
        with tenant_ctx(org.id):
            comp = ApplicationComponent(
                name="Lowercase Coded App",
                organization_id=org.id,
                lifecycle_status="2.1 strategic",
            )
            db_session.add(comp)
            db_session.flush()
            comp_id = comp.id

        results = RationalizationScoringService.calculate_portfolio_scores(
            force_recalculate=True
        )
        assert results.get("success") is not False

        from app.models.application_rationalization import ApplicationRationalizationScore

        score = ApplicationRationalizationScore.query.filter_by(
            application_component_id=comp_id
        ).first()
        assert score is not None, (
            "a lower-case Abacus lifecycle code must still be scored by "
            "calculate_portfolio_scores, not silently skipped"
        )
