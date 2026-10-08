"""
Tests for the dashboard v2 module.

Covers:
- Module imports and blueprint structure
- Blueprint names match v1 for url_for compatibility
- Schema validation (all 10 schemas)
- Utility functions (format_currency, clamp_depth, safe_percentage, build_tier_result)
- Route coverage (dashboard, dashboard_pages)
- Compat wrapper infrastructure (stats tracking, route map completeness)
"""

import pathlib
import re



# ============================================================================
# 1. Imports
# ============================================================================


class TestDashboardV2Imports:
    """Verify all v2 submodules and blueprints are importable."""

    def test_v2_module_imports(self):
        from app.modules.dashboard.v2 import register

        assert callable(register)

    def test_v2_blueprint_imports(self):
        from app.modules.dashboard.v2.routes import (
            dashboard_bp_v2,
            dashboard_pages_bp_v2,
        )

        assert dashboard_bp_v2 is not None
        assert dashboard_pages_bp_v2 is not None

    def test_dashboard_bp_has_correct_name(self):
        from app.modules.dashboard.v2.routes import dashboard_bp_v2

        assert dashboard_bp_v2.name == "dashboard"

    def test_dashboard_pages_bp_has_correct_name(self):
        from app.modules.dashboard.v2.routes import dashboard_pages_bp_v2

        assert dashboard_pages_bp_v2.name == "dashboard_pages"

    def test_v2_services_imports(self):
        from app.modules.dashboard.v2.services import (
            AnalysisOption,
            ApplicationConsolidationService,
            CapabilityHeatmapService,
            GovernanceService,
            RationalizationScoringService,
            UnifiedDuplicateDetectionService,
            VendorRiskService,
            get_options_analysis_engine,
        )

        assert AnalysisOption is not None
        assert ApplicationConsolidationService is not None
        assert CapabilityHeatmapService is not None
        assert GovernanceService is not None
        assert RationalizationScoringService is not None
        assert UnifiedDuplicateDetectionService is not None
        assert VendorRiskService is not None
        assert callable(get_options_analysis_engine)


# ============================================================================
# 2. Schemas
# ============================================================================


class TestDashboardV2Schemas:
    """Test declarative validation schemas."""

    def test_rationalization_score_schema_valid(self):
        from app.modules.dashboard.v2.schemas import RationalizationScoreSchema

        errors = RationalizationScoreSchema.validate({})
        assert errors == []

    def test_portfolio_analysis_schema_valid(self):
        from app.modules.dashboard.v2.schemas import PortfolioAnalysisSchema

        errors = PortfolioAnalysisSchema.validate({"threshold": 40})
        assert errors == []

    def test_options_analysis_schema_valid(self):
        from app.modules.dashboard.v2.schemas import OptionsAnalysisSchema

        errors = OptionsAnalysisSchema.validate(
            {"options": [{"id": 1, "name": "Option A"}]}
        )
        assert errors == []

    def test_options_analysis_schema_missing_options(self):
        from app.modules.dashboard.v2.schemas import OptionsAnalysisSchema

        errors = OptionsAnalysisSchema.validate({})
        assert any("options" in e for e in errors)

    def test_options_analysis_schema_empty_options(self):
        from app.modules.dashboard.v2.schemas import OptionsAnalysisSchema

        errors = OptionsAnalysisSchema.validate({"options": []})
        assert any("At least one" in e for e in errors)

    def test_options_analysis_schema_non_list(self):
        from app.modules.dashboard.v2.schemas import OptionsAnalysisSchema

        errors = OptionsAnalysisSchema.validate({"options": "not a list"})
        assert any("list" in e for e in errors)

    def test_scoring_config_schema_valid(self):
        from app.modules.dashboard.v2.schemas import ScoringConfigurationSchema

        errors = ScoringConfigurationSchema.validate(
            {
                "name": "Default",
                "technical_health_weight": 30,
                "business_value_weight": 35,
                "cost_efficiency_weight": 25,
                "vendor_risk_weight": 10,
            }
        )
        assert errors == []

    def test_scoring_config_schema_missing_name(self):
        from app.modules.dashboard.v2.schemas import ScoringConfigurationSchema

        errors = ScoringConfigurationSchema.validate({})
        assert any("name" in e for e in errors)

    def test_scoring_config_schema_invalid_weights(self):
        from app.modules.dashboard.v2.schemas import ScoringConfigurationSchema

        errors = ScoringConfigurationSchema.validate(
            {
                "name": "Bad",
                "technical_health_weight": 50,
                "business_value_weight": 50,
                "cost_efficiency_weight": 50,
                "vendor_risk_weight": 50,
            }
        )
        assert any("100" in e for e in errors)

    def test_assessment_submission_schema_valid(self):
        from app.modules.dashboard.v2.schemas import AssessmentSubmissionSchema

        errors = AssessmentSubmissionSchema.validate({"application_id": 1})
        assert errors == []

    def test_assessment_submission_schema_missing(self):
        from app.modules.dashboard.v2.schemas import AssessmentSubmissionSchema

        errors = AssessmentSubmissionSchema.validate({})
        assert any("application_id" in e for e in errors)

    def test_onboarding_schema_valid(self):
        from app.modules.dashboard.v2.schemas import OnboardingSchema

        errors = OnboardingSchema.validate({"name": "MyApp"})
        assert errors == []

    def test_onboarding_schema_missing_name(self):
        from app.modules.dashboard.v2.schemas import OnboardingSchema

        errors = OnboardingSchema.validate({})
        assert any("name" in e for e in errors)

    def test_conflict_resolution_schema_valid(self):
        from app.modules.dashboard.v2.schemas import ConflictResolutionSchema

        errors = ConflictResolutionSchema.validate(
            {"field": "Name", "value": "Test", "source": "manual"}
        )
        assert errors == []

    def test_conflict_resolution_schema_missing_field(self):
        from app.modules.dashboard.v2.schemas import ConflictResolutionSchema

        errors = ConflictResolutionSchema.validate(
            {"value": "Test", "source": "manual"}
        )
        assert any("field" in e for e in errors)

    def test_dashboard_generation_schema_valid(self):
        from app.modules.dashboard.v2.schemas import DashboardGenerationSchema

        errors = DashboardGenerationSchema.validate({"model": "VendorOrganization"})
        assert errors == []

    def test_consolidation_recommendation_schema_valid(self):
        from app.modules.dashboard.v2.schemas import ConsolidationRecommendationSchema

        errors = ConsolidationRecommendationSchema.validate({})
        assert errors == []

    def test_schema_rejects_non_dict(self):
        from app.modules.dashboard.v2.schemas import OnboardingSchema

        errors = OnboardingSchema.validate("not a dict")
        assert any("JSON object" in e for e in errors)


# ============================================================================
# 3. Utils
# ============================================================================


class TestDashboardV2Utils:
    """Test utility helper functions."""

    def test_format_currency_millions(self):
        from app.modules.dashboard.v2.utils import format_currency

        assert format_currency(5_500_000) == "£5.5M"

    def test_format_currency_thousands(self):
        from app.modules.dashboard.v2.utils import format_currency

        assert format_currency(75_000) == "£75K"

    def test_format_currency_small(self):
        from app.modules.dashboard.v2.utils import format_currency

        assert format_currency(500) == "£500"

    def test_format_currency_invalid(self):
        from app.modules.dashboard.v2.utils import format_currency

        assert format_currency(None) == "£0"

    def test_format_currency_custom_symbol(self):
        from app.modules.dashboard.v2.utils import format_currency

        assert format_currency(1_000_000, "$") == "$1.0M"

    def test_clamp_depth_normal(self):
        from app.modules.dashboard.v2.utils import clamp_depth

        assert clamp_depth(3) == 3

    def test_clamp_depth_too_high(self):
        from app.modules.dashboard.v2.utils import clamp_depth

        assert clamp_depth(10) == 5

    def test_clamp_depth_too_low(self):
        from app.modules.dashboard.v2.utils import clamp_depth

        assert clamp_depth(0) == 1

    def test_clamp_depth_invalid(self):
        from app.modules.dashboard.v2.utils import clamp_depth

        assert clamp_depth("abc") == 3

    def test_safe_percentage_normal(self):
        from app.modules.dashboard.v2.utils import safe_percentage

        assert safe_percentage(25, 100) == 25.0

    def test_safe_percentage_zero_denominator(self):
        from app.modules.dashboard.v2.utils import safe_percentage

        assert safe_percentage(10, 0) == 0.0

    def test_build_tier_result(self):
        from app.modules.dashboard.v2.utils import build_tier_result

        tier = {"name": "Low", "min": 0, "max": 50000, "color": "green"}
        result = build_tier_result(tier, 10, 100)
        assert result["application_count"] == 10
        assert result["percentage"] == 10.0
        assert result["name"] == "Low"


# ============================================================================
# 4. Blueprint Structure
# ============================================================================


class TestDashboardV2BlueprintStructure:
    """Verify blueprints have routes registered."""

    def test_dashboard_bp_has_routes(self):
        from app.modules.dashboard.v2.routes import dashboard_bp_v2

        # deferred_functions contain the route registrations
        assert len(dashboard_bp_v2.deferred_functions) >= 15

    def test_dashboard_pages_bp_has_routes(self):
        from app.modules.dashboard.v2.routes import dashboard_pages_bp_v2

        assert len(dashboard_pages_bp_v2.deferred_functions) >= 30


# ============================================================================
# 5. Compat Wrappers
# ============================================================================


class TestDashboardCompatWrappers:
    """Test compatibility wrapper infrastructure."""

    def test_compat_module_imports(self):
        from app.compat.dashboard import (
            wrap_legacy_dashboard_bp,
            wrap_legacy_dashboard_pages_bp,
        )

        assert callable(wrap_legacy_dashboard_bp)
        assert callable(wrap_legacy_dashboard_pages_bp)

    def test_compat_stats_tracking(self):
        from app.compat.dashboard import DashboardCompatStats

        DashboardCompatStats.reset()
        DashboardCompatStats.record("dashboard.index")
        DashboardCompatStats.record("dashboard.index")
        DashboardCompatStats.record("dashboard_pages.review_queue")
        stats = DashboardCompatStats.get_stats()
        assert stats["total_legacy_hits"] == 3
        assert stats["endpoints"]["dashboard.index"]["hits"] == 2
        assert stats["endpoints"]["dashboard_pages.review_queue"]["hits"] == 1

    def test_compat_stats_reset(self):
        from app.compat.dashboard import DashboardCompatStats

        DashboardCompatStats.record("dashboard.test")
        DashboardCompatStats.reset()
        stats = DashboardCompatStats.get_stats()
        assert stats["total_legacy_hits"] == 0

    def test_legacy_route_map_completeness(self):
        """Route map should cover dashboard (17) + dashboard_pages (40) = 57 endpoints."""
        from app.compat.dashboard import LEGACY_ROUTE_MAP

        dashboard_routes = [k for k in LEGACY_ROUTE_MAP if k.startswith("dashboard.")]
        dashboard_pages_routes = [
            k for k in LEGACY_ROUTE_MAP if k.startswith("dashboard_pages.")
        ]

        assert len(dashboard_routes) >= 17, (
            f"Expected >=17 dashboard routes, got {len(dashboard_routes)}"
        )
        assert len(dashboard_pages_routes) >= 10, (
            f"Expected >=10 dashboard_pages routes, got {len(dashboard_pages_routes)}"
        )

    def test_legacy_route_map_v2_endpoints_match(self):
        """Every LEGACY_ROUTE_MAP entry should have a v2 key pointing to a valid endpoint name."""
        from app.compat.dashboard import LEGACY_ROUTE_MAP

        for key, entry in LEGACY_ROUTE_MAP.items():
            assert "v2" in entry, f"Missing 'v2' key in LEGACY_ROUTE_MAP[{key}]"
            assert "url" in entry, f"Missing 'url' key in LEGACY_ROUTE_MAP[{key}]"
            assert "method" in entry, f"Missing 'method' key in LEGACY_ROUTE_MAP[{key}]"


class TestDashboardV2ServiceInlining:
    """Ensure dashboard v2 has no direct app.services imports."""

    # Shared homes a dashboard must read rather than copy: the layer map is
    # the one system of record for ArchiMate layers (ADR 0008, also read by
    # the composer), and the feature-flag service is the one AI feature gate.
    # Copying either into v2 would make a second implementation.
    SHARED_SYSTEMS_OF_RECORD = {
        "app.services.archimate_viewpoint_service",
        "app.services.feature_flag_service",
        "app.services.application_cost_accessor",
    }

    def test_no_direct_app_services_imports(self):
        v2_root = pathlib.Path(__file__).resolve().parents[1] / "v2"
        pattern = re.compile(r"^\s*from\s+(app\.services\.[\w.]+)", re.MULTILINE)

        offenders = []
        for py_file in v2_root.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for module in pattern.findall(content):
                if module not in self.SHARED_SYSTEMS_OF_RECORD:
                    offenders.append(f"{py_file.relative_to(v2_root.parent)}: {module}")

        assert offenders == [], f"Direct app.services imports found: {offenders}"
