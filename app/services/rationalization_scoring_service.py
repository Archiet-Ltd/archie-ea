"""
Rationalization Scoring Service

Implements TIME framework (Tolerate/Invest/Migrate/Eliminate) for portfolio rationalization.
Calculates comprehensive application health scores across multiple dimensions.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app import db
from app.models.application_layer import ApplicationComponent
from app.models.application_rationalization import (
    READINESS_DIMENSIONS,
    ApplicationDependency,
    ApplicationRationalizationScore,
    DispositionAction,
    RationalizationPolicy,
    ScoringConfiguration,
    TIME_TO_DISPOSITION,
)
from app.models.cost_intelligence import CapabilityCostAllocation

# Correct association table name (application ↔ vendor products)
from app.models.relationship_tables import ApplicationProcessSupport
from app.models.relationship_tables import (
    application_component_vendor_products as application_vendor_products,
)
from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
from app.services.decorators import transactional

logger = logging.getLogger(__name__)


class RationalizationScoringService:
    """
    Service for calculating application rationalization scores using TIME framework.

    TIME Framework Actions:
    - TOLERATE: Keep as-is (low cost, low value but necessary)
    - INVEST: Strategic investment needed (high value, needs improvement)
    - MIGRATE: Move to better platform (technical debt, obsolete)
    - ELIMINATE: Retire/consolidate (redundant, high cost, low value)

    Scoring Dimensions:
    - Technical Health (30%): Architecture, tech stack, technical debt
    - Business Value (35%): Strategic alignment, user satisfaction, process criticality
    - Cost Efficiency (25%): TCO, license optimization, maintenance burden
    - Vendor Risk (10%): Vendor health, contract status, concentration risk
    - Capability Coverage (adjustment): Apps supporting critical L1-L2 capabilities
      receive a score adjustment (+/-10 pts max).  Single-point-of-failure for
      L1-L2 capabilities blocks ELIMINATE recommendations.

    Disposition Taxonomy:
    - DispositionAction enum defines the canonical 7R actions (Retain/Rehost/Replatform/
      Refactor/Replace/Retire/Consolidate).  Populated on ApplicationRationalizationScore
      as disposition_action alongside the TIME rationalization_action for backwards
      compatibility.  Use get_disposition_label() for human-readable rendering.
    """

    # RAT-118: Model versioning and calibration
    # CAP-020: bumped to 2.1 — capability coverage now influences overall score
    MODEL_VERSION = "2.1"  # Current scoring model version
    MODEL_CALIBRATION_HISTORY = []  # Populated by calibration runs

    @classmethod
    def get_model_metadata(cls):
        """Return current scoring model metadata."""
        return {
            "version": cls.MODEL_VERSION,
            "weights": {
                "technical_health": 0.30,
                "business_value": 0.35,
                "cost_efficiency": 0.25,
                "vendor_risk": 0.10,
            },
            "capability_coverage": {
                "type": "adjustment",
                "max_adjustment": 10.0,
                "level_weights": {"L1": 3.0, "L2": 2.0, "L3+": 1.0},
                "spof_l1_l2_blocks_eliminate": True,
            },
            "thresholds": {
                "eliminate_overall": 40,
                "eliminate_bv_ce": 40,
                "migrate_tech": 40,
                "migrate_bv": 50,
                "invest_bv": 70,
                "invest_tech": 50,
            },
        }

    @classmethod
    def compute_calibration_adjustments(cls, app_ids=None):
        """RAT-118: Compute calibration adjustments based on realized outcomes.

        Compares projected vs actual benefits for completed rationalization actions
        and suggests weight/threshold adjustments. Does NOT mutate historical records.
        """
        from app.models.application_rationalization import (
            RationalizationBenefitsTracker,
        )

        query = RationalizationBenefitsTracker.query.filter(
            RationalizationBenefitsTracker.tracking_status.in_(["measured", "validated"])
        )
        if app_ids:
            query = query.filter(RationalizationBenefitsTracker.application_id.in_(app_ids))

        trackers = query.all()
        if not trackers:
            return {
                "calibration_data_points": 0,
                "adjustments": [],
                "recommendation": "Insufficient data for calibration",
            }

        # Analyse variance patterns
        overestimates = 0
        underestimates = 0
        accurate = 0
        total_variance = 0.0

        for t in trackers:
            variance = getattr(t, "savings_variance_pct", None)  # model-safety-ok
            if variance is None:
                continue
            total_variance += variance
            if variance < -10:
                overestimates += 1
            elif variance > 10:
                underestimates += 1
            else:
                accurate += 1

        n = overestimates + underestimates + accurate
        avg_variance = total_variance / n if n > 0 else 0

        adjustments = []
        if overestimates > n * 0.6 and n >= 3:
            adjustments.append(
                {
                    "type": "threshold",
                    "field": "cost_efficiency_weight",
                    "direction": "increase",
                    "reason": f"Savings consistently overestimated ({overestimates}/{n} cases)",
                }
            )
        if underestimates > n * 0.6 and n >= 3:
            adjustments.append(
                {
                    "type": "threshold",
                    "field": "cost_efficiency_weight",
                    "direction": "decrease",
                    "reason": f"Savings consistently underestimated ({underestimates}/{n} cases)",
                }
            )

        return {
            "calibration_data_points": n,
            "average_variance_pct": round(avg_variance, 2),
            "breakdown": {
                "overestimates": overestimates,
                "underestimates": underestimates,
                "accurate": accurate,
            },
            "adjustments": adjustments,
            "recommendation": (
                "Apply adjustments" if adjustments else "Model calibration within acceptable range"
            ),
            "model_version": cls.MODEL_VERSION,
        }

    @staticmethod
    def get_disposition_label(action: str | None) -> str:
        """
        Return a human-readable label for a disposition action or TIME label.

        Convenience wrapper around the module-level function in
        app.models.application_rationalization.  Accepts both DispositionAction
        value strings ('retain', 'rehost', …) and TIME labels ('TOLERATE', …).

        Args:
            action: Disposition value string or TIME label.

        Returns:
            Human-readable label string (e.g. 'Retain', 'TOLERATE → Retain').
        """
        from app.models.application_rationalization import get_disposition_label
        return get_disposition_label(action)

    @staticmethod
    def get_scoring_configuration(
        scope_type: str = "global",
        scope_entity_id: Optional[int] = None,
        scope_entity_type: Optional[str] = None,
    ) -> ScoringConfiguration:
        """
        Get the appropriate scoring configuration for the given scope.

        Falls back to default federal CIO weights (30/35/25/10) if no
        custom configuration exists for the specified scope.

        Args:
            scope_type: Configuration scope (global, division, department, business_unit)
            scope_entity_id: ID of the scoped entity
            scope_entity_type: Type of entity (BusinessUnit, Department, etc.)

        Returns:
            ScoringConfiguration object
        """
        try:
            # Try to find specific configuration
            query = ScoringConfiguration.query.filter_by(
                is_active=True
            )

            # For non-global scopes, try specific config first
            if scope_type != "global" and scope_entity_id is not None:
                specific_config = query.filter_by(
                    scope_type=scope_type,
                    scope_entity_id=scope_entity_id,
                    scope_entity_type=scope_entity_type,
                ).first()
                if specific_config:
                    return specific_config

            # Try default configuration
            default_config = query.filter_by(is_default=True).first()
            if default_config:
                return default_config

            # Return global configuration or create default
            global_config = query.filter_by(scope_type="global").first()
            if global_config:
                return global_config

            # Create default CIO.gov baseline configuration if none exists
            logger.warning("No scoring configuration found, creating default CIO.gov baseline")
            default = ScoringConfiguration(
                name="CIO.gov Federal Baseline",
                description="Default weights per CIO.gov Application Rationalization Playbook",
                scope_type="global",
                technical_health_weight=30,
                business_value_weight=35,
                cost_efficiency_weight=25,
                vendor_risk_weight=10,
                is_default=True,
            )
            db.session.add(default)
            db.session.flush()
            return default

        except Exception as e:
            logger.error(f"Error getting scoring configuration: {e}", exc_info=True)
            # Return a transient default configuration (not persisted)
            return ScoringConfiguration(
                name="Emergency Default",
                scope_type="global",
                technical_health_weight=30,
                business_value_weight=35,
                cost_efficiency_weight=25,
                vendor_risk_weight=10,
            )

    @staticmethod
    def resolve_policy(app: "ApplicationComponent") -> Optional["RationalizationPolicy"]:
        """
        Resolve the most appropriate RationalizationPolicy for an application.

        Resolution order:
        1. All non-default policies whose scope_filter matches the application's
           attributes (business_unit, portfolio, lifecycle).  The first match
           returned by the database (ordered by id) is used.
        2. The policy with ``is_default = True``.
        3. ``None`` — caller falls back to ScoringConfiguration alone.

        ``scope_filter`` matching: every key present in the policy's
        scope_filter dict must match the corresponding attribute on the
        application.  A key absent from scope_filter is treated as a wildcard.

        Args:
            app: ApplicationComponent instance to resolve a policy for.

        Returns:
            Matching RationalizationPolicy or None.
        """
        try:
            all_policies = (
                RationalizationPolicy.query.order_by(
                    RationalizationPolicy.is_default.asc(),  # non-defaults first
                    RationalizationPolicy.id.asc(),
                ).all()
            )

            app_business_unit = getattr(app, "business_unit", None)  # model-safety-ok
            app_portfolio = getattr(app, "portfolio", None)  # model-safety-ok
            app_lifecycle = getattr(app, "lifecycle_status", None)  # model-safety-ok

            for policy in all_policies:
                if policy.is_default:
                    # Skip default in first pass; handle as fallback below.
                    continue
                scope = policy.scope_filter
                if not scope:
                    # A non-default policy with no scope_filter is too broad — skip.
                    continue
                matched = True
                for attr_key, expected_val in scope.items():
                    if attr_key == "business_unit":
                        actual = app_business_unit
                    elif attr_key == "portfolio":
                        actual = app_portfolio
                    elif attr_key == "lifecycle":
                        actual = app_lifecycle
                    else:
                        # Unknown scope key — skip this criterion (permissive).
                        continue
                    if actual is None or str(actual).strip() != str(expected_val).strip():
                        matched = False
                        break
                if matched:
                    logger.debug(
                        "Policy '%s' (id=%s) matched app '%s' via scope_filter %s",
                        policy.name, policy.id, app.name, scope,
                    )
                    return policy

            # Fall back to default policy.
            for policy in all_policies:
                if policy.is_default:
                    logger.debug(
                        "Using default policy '%s' (id=%s) for app '%s'",
                        policy.name, policy.id, app.name,
                    )
                    return policy

            logger.debug("No RationalizationPolicy found for app '%s'", app.name)
            return None

        except Exception as exc:
            logger.error(
                "Error resolving policy for app %s: %s", getattr(app, "id", "?"), exc,  # model-safety-ok
                exc_info=True,
            )
            return None

    @staticmethod
    def evaluate_readiness(app: "ApplicationComponent") -> Dict:
        """
        Evaluate data readiness for a single ApplicationComponent.

        Checks each of the 8 canonical dimensions defined in READINESS_DIMENSIONS
        against actual field values on the application record.  A dimension is
        considered satisfied when at least one of its backing fields contains a
        non-null, non-empty, non-zero value.

        Returns a dict with four keys:
            dimensions      – per-dimension bool map, e.g. {"owner": True, "cost": False, …}
            readiness_score – fraction of satisfied dimensions (0.0 – 1.0)
            is_decision_ready – True only when ALL high-severity dimensions are satisfied
            missing_critical  – list of high-severity dimension names that are missing
        """
        dimensions: Dict[str, bool] = {}

        # --- owner ---
        dimensions["owner"] = bool(
            app.application_owner and app.application_owner.strip()
        )

        # --- lifecycle ---
        # RATA-001: Accept deployment_status as fallback for Abacus apps
        lifecycle_info = RationalizationScoringService._map_deployment_to_lifecycle(app)
        dimensions["lifecycle"] = not lifecycle_info["data_quality_flag"]

        # --- cost ---
        # At least one of the four cost fields must be non-zero and non-null.
        # TODO(Release 2 Cost Fact consolidation): Use application_cost_accessor.get_annual_cost()
        # and related accessors instead of direct model field access.
        cost_fields = [
            app.total_cost_of_ownership,
            app.license_cost,
            app.maintenance_cost,
            app.infrastructure_cost,
        ]
        dimensions["cost"] = any(
            v is not None and float(v) > 0 for v in cost_fields
        )

        # --- usage ---
        dimensions["usage"] = bool(
            app.user_count is not None and app.user_count > 0
        )

        # --- dependencies ---
        # Accept either the denormalised counter or actual ApplicationDependency rows.
        has_dep_count = bool(
            app.dependencies_count is not None and app.dependencies_count > 0
        )
        has_dep_records = False
        try:
            has_dep_records = bool(app.dependencies_out)
        except Exception:
            logger.debug("Could not access dependencies_out for app %s", app.id)
        dimensions["dependencies"] = has_dep_count or has_dep_records

        # --- capability ---
        has_capability = False
        try:
            has_capability = bool(app.capability_mappings)
        except Exception:
            logger.debug("Could not access capability_mappings for app %s", app.id)
        dimensions["capability"] = has_capability

        # --- vendor ---
        dimensions["vendor"] = bool(
            app.vendor_product_id is not None
            or (app.vendor_name and app.vendor_name.strip())
        )

        # --- risk ---
        dimensions["risk"] = bool(
            (app.technical_risk and app.technical_risk.strip())
            or (app.business_risk and app.business_risk.strip())
        )

        # Compute aggregate readiness_score
        total_dimensions = len(READINESS_DIMENSIONS)
        satisfied = sum(1 for v in dimensions.values() if v)
        readiness_score = satisfied / total_dimensions if total_dimensions else 0.0

        # Identify missing high-severity dimensions
        missing_critical = [
            dim
            for dim, meta in READINESS_DIMENSIONS.items()
            if meta["severity"] == "high" and not dimensions.get(dim, False)
        ]
        is_decision_ready = len(missing_critical) == 0

        return {
            "dimensions": dimensions,
            "readiness_score": round(readiness_score, 4),
            "is_decision_ready": is_decision_ready,
            "missing_critical": missing_critical,
        }

    # ── RATA-001: Abacus lifecycle_status → canonical lifecycle mapping ──
    # Actual Abacus values: "2.1 STRATEGIC" (283), "5. DECOMMISSIONED" (361),
    # "2.2 TACTICAL" (106), "1. UNDETERMINED" (29), "3. SUNSET" (28),
    # "4.1 DECOM DECIDED" (21), "4.2 DECOM PLANNED" (7), "4.3 READ-ONLY" (7),
    # "4.4 STOPPED" (2). All 844 apps have lifecycle_status set.
    ABACUS_LIFECYCLE_MAP = {
        "2.1 STRATEGIC": "operational",
        "2.2 TACTICAL": "operational",
        "1. UNDETERMINED": "operational",
        "3. SUNSET": "deprecated",
        "4.1 DECOM DECIDED": "deprecated",
        "4.2 DECOM PLANNED": "deprecated",
        "4.3 READ-ONLY": "deprecated",
        "4.4 STOPPED": "deprecated",
        "5. DECOMMISSIONED": "deprecated",
    }

    # All Abacus lifecycle values that should be included in scoring
    SCOREABLE_LIFECYCLE_VALUES = [
        # Canonical values (normalized by Abacus connector)
        "operational", "testing", "development", "deprecated", "retired", "planning",
        # Abacus raw values (before normalization)
        "2.1 STRATEGIC", "2.2 TACTICAL", "1. UNDETERMINED",
        "3. SUNSET", "4.1 DECOM DECIDED", "4.2 DECOM PLANNED",
        "4.3 READ-ONLY", "4.4 STOPPED", "5. DECOMMISSIONED",
    ]

    DEPLOYMENT_TO_LIFECYCLE = {
        "Production": "operational",
        "In Development": "development",
        "5. Decommissioned": "deprecated",
        "Pilot": "testing",
        "Testing": "testing",
    }

    @staticmethod
    def _map_deployment_to_lifecycle(app: "ApplicationComponent") -> Dict:
        """Map Abacus lifecycle/deployment status to canonical lifecycle.

        Returns dict with:
            lifecycle: str — the canonical lifecycle status
            data_quality_flag: bool — True if the mapping is uncertain
        """
        # Check lifecycle_status first (may be Abacus code or canonical value)
        ls = (app.lifecycle_status or "").strip()
        if ls:
            abacus_map = RationalizationScoringService.ABACUS_LIFECYCLE_MAP
            if ls in abacus_map:
                return {"lifecycle": abacus_map[ls], "data_quality_flag": False}
            # Already a canonical value
            if ls in ("operational", "testing", "development", "deprecated", "retired", "planning"):
                return {"lifecycle": ls, "data_quality_flag": False}
            # Unknown lifecycle value — treat as operational with flag
            return {"lifecycle": "operational", "data_quality_flag": True}

        # Fall back to deployment_status mapping
        ds = (app.deployment_status or "").strip()
        deploy_map = RationalizationScoringService.DEPLOYMENT_TO_LIFECYCLE
        if ds in deploy_map:
            return {"lifecycle": deploy_map[ds], "data_quality_flag": False}

        # Both unknown — default to operational with flag
        return {"lifecycle": "operational", "data_quality_flag": True}

    @staticmethod
    @transactional
    def calculate_app_score(
        application_id: int,
        app: Optional[ApplicationComponent] = None,
        scoring_config: Optional[ScoringConfiguration] = None,
    ) -> Optional[ApplicationRationalizationScore]:
        """
        Calculate comprehensive rationalization score for an application.

        Creates or updates ApplicationRationalizationScore record with TIME recommendation.
        Uses configurable weights if scoring_config provided, otherwise uses default.

        Args:
            application_id: Application component ID
            app: Optional pre-loaded ApplicationComponent (avoids redundant query)
            scoring_config: Optional custom ScoringConfiguration to use

        Returns:
            ApplicationRationalizationScore object or None on error.
            The returned object has an ``evidence_trail`` attribute (list of dicts)
            exposing per-factor score drivers so callers and API routes can surface
            them without re-running the scoring logic.
        """
        try:
            if app is None:
                app = ApplicationComponent.query.get(application_id)
            if not app:
                logger.error(f"Application not found: {application_id}")
                return None

            # Get scoring configuration
            if scoring_config is None:
                scoring_config = RationalizationScoringService.get_scoring_configuration(
                    scope_type="global"
                )

            # Resolve the policy overlay for this application.
            # The policy can override dimension weights and TIME thresholds.
            active_policy = RationalizationScoringService.resolve_policy(app)

            # Calculate individual dimension scores (0 - 100)
            technical_score, tech_evidence = RationalizationScoringService._calculate_technical_health(app)
            business_score, biz_evidence = RationalizationScoringService._calculate_business_value(app)
            cost_score, cost_evidence = RationalizationScoringService._calculate_cost_efficiency(app)
            vendor_result = RationalizationScoringService._calculate_vendor_risk(app)
            vendor_score = vendor_result["score"]
            vendor_evidence = vendor_result.get("evidence", [])

            # Capability coverage dimension (CAP-020: now contributes to overall score)
            capability_result = RationalizationScoringService._compute_capability_score(application_id)

            # Resolve effective weights — policy overrides base config if present.
            if active_policy is not None:
                weights = active_policy.get_effective_weights(scoring_config)
            else:
                weights = scoring_config.get_weights_dict()

            # Calculate weighted overall score using resolved weights.
            overall_score = (
                technical_score * weights["technical_health"]
                + business_score * weights["business_value"]
                + cost_score * weights["cost_efficiency"]
                + vendor_score * weights["vendor_risk"]
            )

            # CAP-020: Apply capability coverage adjustment (+/-10 pts max).
            # Apps supporting critical L1-L2 capabilities receive a bonus;
            # apps with no capability mappings receive a penalty.
            cap_adjustment = capability_result.get("capability_adjustment", 0.0)
            overall_score = max(0.0, min(100.0, overall_score + cap_adjustment))

            # Build structured evidence trail covering all four scoring dimensions.
            # Each entry documents the factor name, its weight within its dimension,
            # the raw value observed, the points contributed, the data source field,
            # and a plain-English rationale so architects can challenge the scoring.
            evidence_trail: List[Dict] = []

            # Dimension-level summary entries (one per dimension)
            tech_weight_pct = round(weights["technical_health"] * 100, 1)
            biz_weight_pct = round(weights["business_value"] * 100, 1)
            cost_weight_pct = round(weights["cost_efficiency"] * 100, 1)
            vendor_weight_pct = round(weights["vendor_risk"] * 100, 1)

            evidence_trail.append({
                "factor": "Technical Health",
                "dimension": "technical_health",
                "weight": tech_weight_pct,
                "raw_value": round(technical_score, 2),
                "contribution": round(technical_score * weights["technical_health"], 2),
                "source": "ApplicationComponent (deployment_model, architecture_style, technology_age_years, technical_risk, lifecycle_status)",
                "rationale": f"Technical health score of {technical_score:.1f} contributes {technical_score * weights['technical_health']:.1f} pts to overall score (weight {tech_weight_pct}%)",
                "sub_factors": tech_evidence,
            })
            evidence_trail.append({
                "factor": "Business Value",
                "dimension": "business_value",
                "weight": biz_weight_pct,
                "raw_value": round(business_score, 2),
                "contribution": round(business_score * weights["business_value"], 2),
                "source": "ApplicationComponent (business_criticality, strategic_importance, ApplicationProcessSupport)",
                "rationale": f"Business value score of {business_score:.1f} contributes {business_score * weights['business_value']:.1f} pts to overall score (weight {biz_weight_pct}%)",
                "sub_factors": biz_evidence,
            })
            evidence_trail.append({
                "factor": "Cost Efficiency",
                "dimension": "cost_efficiency",
                "weight": cost_weight_pct,
                "raw_value": round(cost_score, 2),
                "contribution": round(cost_score * weights["cost_efficiency"], 2),
                "source": "ApplicationComponent (total_cost_of_ownership, license_cost, maintenance_cost, infrastructure_cost, user_count) — TODO(Release 2): use cost accessor",
                "rationale": f"Cost efficiency score of {cost_score:.1f} contributes {cost_score * weights['cost_efficiency']:.1f} pts to overall score (weight {cost_weight_pct}%)",
                "sub_factors": cost_evidence,
            })
            evidence_trail.append({
                "factor": "Vendor Risk",
                "dimension": "vendor_risk",
                "weight": vendor_weight_pct,
                "raw_value": round(vendor_score, 2),
                "contribution": round(vendor_score * weights["vendor_risk"], 2),
                "source": "VendorOrganization (enterprise_readiness_score, financial_health_score, innovation_score, acquisition_risk), VendorProduct (product_maturity, end_of_life_date, compliance_standards)",
                "rationale": f"Vendor risk score of {vendor_score:.1f} contributes {vendor_score * weights['vendor_risk']:.1f} pts to overall score (weight {vendor_weight_pct}%)",
                "sub_factors": vendor_evidence,
            })
            # CAP-020: Capability coverage dimension — now contributes to overall
            # score via adjustment and influences TIME action via SPOF L1-L2 flag.
            cap_sub_factors = []
            if capability_result["score"] > 0:
                cap_sub_factors.append({
                    "factor": "Unified Weighted Coverage Score",
                    "weight": None,
                    "raw_value": capability_result["score"],
                    "contribution": capability_result["score"],
                    "source": "unified_application_capability_mapping + unified_capabilities",
                    "rationale": (
                        f"Unified capability coverage score of {capability_result['score']} "
                        f"based on level-weighted capability mappings"
                    ),
                })
            if capability_result.get("business_capability_count", 0) > 0:
                cap_sub_factors.append({
                    "factor": "Business Capability Coverage (CAP-020)",
                    "weight": None,
                    "raw_value": capability_result.get("capability_score", 0),
                    "contribution": cap_adjustment,
                    "source": "application_capability_mapping + business_capability",
                    "rationale": (
                        f"BusinessCapability mappings: {capability_result.get('business_capability_count', 0)} total "
                        f"(L1={capability_result.get('l1_count', 0)}, L2={capability_result.get('l2_count', 0)}). "
                        f"Normalized score: {capability_result.get('capability_score', 0):.1f}/100. "
                        f"Overall adjustment: {cap_adjustment:+.1f} pts."
                    ),
                })
            if capability_result["single_point_of_failure"]:
                spof_names = [c["name"] for c in capability_result["spof_capabilities"]]
                spof_l1_l2 = capability_result.get("spof_l1_l2", False)
                cap_sub_factors.append({
                    "factor": "Single Point of Failure",
                    "weight": None,
                    "raw_value": True,
                    "contribution": 5.0 if spof_l1_l2 else 0,
                    "source": "application_capability_mapping + unified_application_capability_mapping (GROUP BY HAVING COUNT=1)",
                    "rationale": (
                        f"App is sole supporter of {len(spof_names)} capability(ies): "
                        f"{', '.join(spof_names[:5])}"
                        f"{' (and more)' if len(spof_names) > 5 else ''}. "
                        f"L1-L2 SPOF: {spof_l1_l2}"
                        f"{' — ELIMINATE blocked, retention priority raised' if spof_l1_l2 else ''}"
                    ),
                })
            if capability_result["consolidation_candidates"]:
                cand_names = [c["name"] for c in capability_result["consolidation_candidates"]]
                cap_sub_factors.append({
                    "factor": "Consolidation Candidates",
                    "weight": None,
                    "raw_value": len(cand_names),
                    "contribution": 0,
                    "source": "unified_application_capability_mapping (exact capability-set match)",
                    "rationale": (
                        f"{len(cand_names)} app(s) cover the same capabilities: "
                        f"{', '.join(cand_names[:5])}"
                        f"{' (and more)' if len(cand_names) > 5 else ''}"
                    ),
                })
            evidence_trail.append({
                "factor": "Capability Coverage",
                "dimension": "capability_coverage",
                "weight": "adjustment",
                "raw_value": capability_result.get("capability_score", capability_result["score"]),
                "contribution": cap_adjustment,
                "source": "application_capability_mapping, business_capability, unified_application_capability_mapping, unified_capabilities",
                "rationale": (
                    f"Capability coverage score: {capability_result.get('capability_score', 0):.1f}/100. "
                    f"Adjustment: {cap_adjustment:+.1f} pts. "
                    f"SPOF: {capability_result['single_point_of_failure']} "
                    f"(L1-L2: {capability_result.get('spof_l1_l2', False)}). "
                    f"Consolidation candidates: {len(capability_result['consolidation_candidates'])}."
                ),
                "sub_factors": cap_sub_factors,
                "capability_details": capability_result,
            })

            # Determine TIME framework action.
            # When a policy is active, use its threshold overrides; otherwise
            # delegate entirely to the ScoringConfiguration method.
            if active_policy is not None:
                thresholds = active_policy.get_effective_thresholds(scoring_config)
                time_action = RationalizationScoringService._determine_time_action_with_thresholds(
                    overall_score=overall_score,
                    technical_score=technical_score,
                    business_score=business_score,
                    cost_score=cost_score,
                    app=app,
                    thresholds=thresholds,
                )
            else:
                time_action = scoring_config.determine_time_action(
                    overall_score=overall_score,
                    technical_score=technical_score,
                    business_score=business_score,
                    cost_score=cost_score,
                    app=app,
                )

            # CAP-020: SPOF L1-L2 override — if the app is the sole supporter of any
            # L1 or L2 capability, ELIMINATE is blocked because retiring it would leave
            # a critical capability gap.  Downgrade to TOLERATE (retain until a
            # replacement is mapped).
            if capability_result.get("spof_l1_l2", False) and time_action == "ELIMINATE":
                spof_l1_l2_names = [
                    c["name"] for c in capability_result["spof_capabilities"]
                    if c.get("level") in (1, 2)
                ]
                logger.info(
                    "App %s (%s) qualifies for ELIMINATE but is sole supporter of "
                    "%d L1/L2 capability(ies): %s. Overriding to TOLERATE.",
                    app.id, app.name, len(spof_l1_l2_names),
                    ", ".join(spof_l1_l2_names[:5]),
                )
                time_action = "TOLERATE"

            # Generate justification narrative
            config_label = (
                f"{scoring_config.name} + policy '{active_policy.name}'"
                if active_policy
                else scoring_config.name
            )
            justification = RationalizationScoringService._generate_justification(
                time_action=time_action,
                technical_score=technical_score,
                business_score=business_score,
                cost_score=cost_score,
                vendor_score=vendor_score,
                app=app,
                config_name=config_label,
            )

            # Check or create score record
            score = ApplicationRationalizationScore.query.filter_by(
                application_component_id=application_id
            ).first()

            if not score:
                score = ApplicationRationalizationScore(application_component_id=application_id)
                db.session.add(score)

            # Update all fields
            score.technical_health_score = round(technical_score, 2)
            score.business_value_score = round(business_score, 2)
            score.cost_efficiency_score = round(cost_score, 2)
            score.vendor_risk_score = round(vendor_score, 2)
            score.overall_health_score = round(overall_score, 2)
            score.rationalization_action = time_action
            score.action_rationale = justification
            score.assessment_date = datetime.utcnow().date()
            score.scoring_model_version = scoring_config.configuration_version

            # Record which policy was applied (nullable — None when no policy exists).
            score.policy_id = active_policy.id if active_policy else None
            score.policy_name = active_policy.name if active_policy else None

            # Derive canonical disposition action from TIME label.
            # ELIMINATE → CONSOLIDATE when sibling app overlap is the primary driver;
            # otherwise ELIMINATE → RETIRE (the default mapping).
            disposition, confidence, uncertainty_reasons = RationalizationScoringService._derive_disposition(
                time_action=time_action,
                overall_score=float(overall_score),
                technical_score=float(technical_score),
                business_score=float(business_score),
                cost_score=float(cost_score),
                app=app,
            )
            score.disposition_action = disposition.value
            score.disposition_confidence = confidence
            score.confidence_reasons = uncertainty_reasons if uncertainty_reasons else []

            # Populate vendor risk detail fields
            score.vendor_lock_in_level = vendor_result.get("vendor_lock_in_level")
            score.vendor_viability_score = vendor_result.get("vendor_viability_score")
            score.exit_complexity = vendor_result.get("exit_complexity")
            score.exit_cost_estimate = vendor_result.get("exit_cost_estimate")
            score.alternative_vendors_available = vendor_result.get("alternative_vendors_available")

            # Evaluate and store data readiness
            readiness = RationalizationScoringService.evaluate_readiness(app)
            score.readiness_dimensions = readiness["dimensions"]
            score.readiness_score = readiness["readiness_score"]
            score.is_decision_ready = readiness["is_decision_ready"]

            # Gate final recommendation on readiness.
            # When critical evidence is missing the disposition is overridden to
            # INSUFFICIENT_EVIDENCE so consumers can distinguish a real recommendation
            # from a tentative one driven by sparse data.  The TIME action and underlying
            # dimension scores are still stored for tracking and audit purposes.
            if not readiness["is_decision_ready"]:
                score.disposition_action = DispositionAction.INSUFFICIENT_EVIDENCE.value
                score.disposition_confidence = "none"
                # Confidence reasons are superseded by the readiness gate — missing
                # dimensions are already captured in the evidence trail entry below.
                score.confidence_reasons = []
                # Record the missing dimensions in the evidence trail so the UI can
                # surface exactly what data is needed to unlock a real recommendation.
                evidence_trail.append({
                    "factor": "Insufficient Evidence Gate",
                    "dimension": "readiness",
                    "weight": None,
                    "raw_value": readiness["readiness_score"],
                    "contribution": 0,
                    "source": "RationalizationScoringService.evaluate_readiness()",
                    "rationale": (
                        f"Recommendation overridden to INSUFFICIENT_EVIDENCE. "
                        f"Missing high-severity dimensions: {readiness['missing_critical']}. "
                        f"Readiness score: {readiness['readiness_score']:.0%}. "
                        f"Populate the missing fields to obtain a reliable TIME recommendation."
                    ),
                    "insufficient_evidence": True,
                    "missing_dimensions": readiness["missing_critical"],
                })
                logger.warning(
                    f"App '{app.name}' (id={application_id}) is NOT decision-ready. "
                    f"Missing high-severity dimensions: {readiness['missing_critical']}. "
                    f"Readiness score: {readiness['readiness_score']:.0%}. "
                    f"Disposition overridden to INSUFFICIENT_EVIDENCE (TIME action '{time_action}' is tentative)."
                )

            # Attach the structured evidence trail as a transient attribute.
            # This is intentionally NOT persisted to the DB (no schema migration needed)
            # — callers that need it call get_evidence_trail() which re-derives it.
            score.evidence_trail = evidence_trail  # type: ignore[attr-defined]

            db.session.flush()

            logger.info(
                f"Calculated rationalization score for {app.name} using '{scoring_config.name}': "
                f"Overall={overall_score:.1f}, Action={time_action}, "
                f"Readiness={readiness['readiness_score']:.0%}, "
                f"DecisionReady={readiness['is_decision_ready']}"
            )

            return score

        except Exception as e:
            logger.error(f"Error calculating score for app {application_id}: {e}", exc_info=True)
            db.session.rollback()
            return None

    @staticmethod
    def get_evidence_trail(
        application_id: int,
        app: Optional[ApplicationComponent] = None,
        scoring_config: Optional[ScoringConfiguration] = None,
    ) -> Dict:
        """
        Return the full structured evidence trail for a single application without
        persisting any score changes.

        This is the dedicated endpoint backing method for
        ``GET /rationalization/api/evidence-trail/<app_id>``.  It re-runs each
        scoring sub-method (read-only; no DB writes) and collates the per-factor
        evidence into a single response dict that includes:

        - ``app_id`` / ``app_name``
        - ``scores`` — the four dimension scores and their weighted contributions
        - ``weights`` — the active ScoringConfiguration weights
        - ``overall_score`` — the weighted total
        - ``time_action`` — the TIME recommendation derived from the scores
        - ``disposition_action`` / ``disposition_confidence`` — the 7R mapping
        - ``evidence_trail`` — list of dimension-level evidence dicts, each with
          ``sub_factors`` containing the granular per-field evidence entries
        - ``readiness`` — decision-readiness snapshot
        - ``scoring_config_name`` — which configuration was used

        Args:
            application_id: ApplicationComponent PK.
            app: Optional pre-loaded instance to avoid a DB round-trip.
            scoring_config: Optional override; defaults to the active global config.

        Returns:
            Dict with keys above, or ``{"error": str}`` on failure.
        """
        try:
            if app is None:
                app = ApplicationComponent.query.get(application_id)
            if not app:
                return {"error": f"Application {application_id} not found"}

            if scoring_config is None:
                scoring_config = RationalizationScoringService.get_scoring_configuration(
                    scope_type="global"
                )

            # Resolve policy overlay (read-only — no DB flush in this method).
            active_policy = RationalizationScoringService.resolve_policy(app)

            technical_score, tech_evidence = RationalizationScoringService._calculate_technical_health(app)
            business_score, biz_evidence = RationalizationScoringService._calculate_business_value(app)
            cost_score, cost_evidence = RationalizationScoringService._calculate_cost_efficiency(app)
            vendor_result = RationalizationScoringService._calculate_vendor_risk(app)
            vendor_score = vendor_result["score"]
            vendor_evidence = vendor_result.get("evidence", [])

            if active_policy is not None:
                weights = active_policy.get_effective_weights(scoring_config)
            else:
                weights = scoring_config.get_weights_dict()

            overall_score = (
                technical_score * weights["technical_health"]
                + business_score * weights["business_value"]
                + cost_score * weights["cost_efficiency"]
                + vendor_score * weights["vendor_risk"]
            )

            # CAP-020: Capability coverage (read-only, no DB writes)
            et_cap_result = RationalizationScoringService._compute_capability_score(application_id)
            et_cap_adjustment = et_cap_result.get("capability_adjustment", 0.0)
            overall_score = max(0.0, min(100.0, overall_score + et_cap_adjustment))

            tech_weight_pct = round(weights["technical_health"] * 100, 1)
            biz_weight_pct = round(weights["business_value"] * 100, 1)
            cost_weight_pct = round(weights["cost_efficiency"] * 100, 1)
            vendor_weight_pct = round(weights["vendor_risk"] * 100, 1)

            evidence_trail = [
                {
                    "factor": "Technical Health",
                    "dimension": "technical_health",
                    "weight": tech_weight_pct,
                    "raw_value": round(technical_score, 2),
                    "contribution": round(technical_score * weights["technical_health"], 2),
                    "source": "ApplicationComponent (deployment_model, architecture_style, technology_age_years, technical_risk, lifecycle_status)",
                    "rationale": f"Technical health score of {technical_score:.1f} contributes {technical_score * weights['technical_health']:.1f} pts to overall score (weight {tech_weight_pct}%)",
                    "sub_factors": tech_evidence,
                },
                {
                    "factor": "Business Value",
                    "dimension": "business_value",
                    "weight": biz_weight_pct,
                    "raw_value": round(business_score, 2),
                    "contribution": round(business_score * weights["business_value"], 2),
                    "source": "ApplicationComponent (business_criticality, strategic_importance, ApplicationProcessSupport)",
                    "rationale": f"Business value score of {business_score:.1f} contributes {business_score * weights['business_value']:.1f} pts to overall score (weight {biz_weight_pct}%)",
                    "sub_factors": biz_evidence,
                },
                {
                    "factor": "Cost Efficiency",
                    "dimension": "cost_efficiency",
                    "weight": cost_weight_pct,
                    "raw_value": round(cost_score, 2),
                    "contribution": round(cost_score * weights["cost_efficiency"], 2),
                    "source": "ApplicationComponent (total_cost_of_ownership, license_cost, maintenance_cost, infrastructure_cost, user_count) — TODO(Release 2): use cost accessor",
                    "rationale": f"Cost efficiency score of {cost_score:.1f} contributes {cost_score * weights['cost_efficiency']:.1f} pts to overall score (weight {cost_weight_pct}%)",
                    "sub_factors": cost_evidence,
                },
                {
                    "factor": "Vendor Risk",
                    "dimension": "vendor_risk",
                    "weight": vendor_weight_pct,
                    "raw_value": round(vendor_score, 2),
                    "contribution": round(vendor_score * weights["vendor_risk"], 2),
                    "source": "VendorOrganization (enterprise_readiness_score, financial_health_score, innovation_score, acquisition_risk), VendorProduct (product_maturity, end_of_life_date, compliance_standards)",
                    "rationale": f"Vendor risk score of {vendor_score:.1f} contributes {vendor_score * weights['vendor_risk']:.1f} pts to overall score (weight {vendor_weight_pct}%)",
                    "sub_factors": vendor_evidence,
                },
            ]

            # CAP-020: Capability coverage evidence trail
            et_cap_sub_factors = []
            if et_cap_result["score"] > 0:
                et_cap_sub_factors.append({
                    "factor": "Unified Weighted Coverage Score",
                    "weight": None,
                    "raw_value": et_cap_result["score"],
                    "contribution": et_cap_result["score"],
                    "source": "unified_application_capability_mapping + unified_capabilities",
                    "rationale": (
                        f"Unified capability coverage score of {et_cap_result['score']} "
                        f"based on level-weighted capability mappings"
                    ),
                })
            if et_cap_result.get("business_capability_count", 0) > 0:
                et_cap_sub_factors.append({
                    "factor": "Business Capability Coverage (CAP-020)",
                    "weight": None,
                    "raw_value": et_cap_result.get("capability_score", 0),
                    "contribution": et_cap_adjustment,
                    "source": "application_capability_mapping + business_capability",
                    "rationale": (
                        f"BusinessCapability mappings: {et_cap_result.get('business_capability_count', 0)} total "
                        f"(L1={et_cap_result.get('l1_count', 0)}, L2={et_cap_result.get('l2_count', 0)}). "
                        f"Normalized score: {et_cap_result.get('capability_score', 0):.1f}/100. "
                        f"Overall adjustment: {et_cap_adjustment:+.1f} pts."
                    ),
                })
            if et_cap_result["single_point_of_failure"]:
                et_spof_names = [c["name"] for c in et_cap_result["spof_capabilities"]]
                et_spof_l1_l2 = et_cap_result.get("spof_l1_l2", False)
                et_cap_sub_factors.append({
                    "factor": "Single Point of Failure",
                    "weight": None,
                    "raw_value": True,
                    "contribution": 5.0 if et_spof_l1_l2 else 0,
                    "source": "application_capability_mapping + unified_application_capability_mapping (GROUP BY HAVING COUNT=1)",
                    "rationale": (
                        f"App is sole supporter of {len(et_spof_names)} capability(ies): "
                        f"{', '.join(et_spof_names[:5])}"
                        f"{' (and more)' if len(et_spof_names) > 5 else ''}. "
                        f"L1-L2 SPOF: {et_spof_l1_l2}"
                        f"{' — ELIMINATE blocked, retention priority raised' if et_spof_l1_l2 else ''}"
                    ),
                })
            if et_cap_result["consolidation_candidates"]:
                et_cand_names = [c["name"] for c in et_cap_result["consolidation_candidates"]]
                et_cap_sub_factors.append({
                    "factor": "Consolidation Candidates",
                    "weight": None,
                    "raw_value": len(et_cand_names),
                    "contribution": 0,
                    "source": "unified_application_capability_mapping (exact capability-set match)",
                    "rationale": (
                        f"{len(et_cand_names)} app(s) cover the same capabilities: "
                        f"{', '.join(et_cand_names[:5])}"
                        f"{' (and more)' if len(et_cand_names) > 5 else ''}"
                    ),
                })
            evidence_trail.append({
                "factor": "Capability Coverage",
                "dimension": "capability_coverage",
                "weight": "adjustment",
                "raw_value": et_cap_result.get("capability_score", et_cap_result["score"]),
                "contribution": et_cap_adjustment,
                "source": "application_capability_mapping, business_capability, unified_application_capability_mapping, unified_capabilities",
                "rationale": (
                    f"Capability coverage score: {et_cap_result.get('capability_score', 0):.1f}/100. "
                    f"Adjustment: {et_cap_adjustment:+.1f} pts. "
                    f"SPOF: {et_cap_result['single_point_of_failure']} "
                    f"(L1-L2: {et_cap_result.get('spof_l1_l2', False)}). "
                    f"Consolidation candidates: {len(et_cap_result['consolidation_candidates'])}."
                ),
                "sub_factors": et_cap_sub_factors,
                "capability_details": et_cap_result,
            })

            # Derive TIME + disposition (read-only — no DB flush)
            if active_policy is not None:
                thresholds = active_policy.get_effective_thresholds(scoring_config)
                time_action = RationalizationScoringService._determine_time_action_with_thresholds(
                    overall_score=overall_score,
                    technical_score=technical_score,
                    business_score=business_score,
                    cost_score=cost_score,
                    app=app,
                    thresholds=thresholds,
                )
            else:
                time_action = scoring_config.determine_time_action(
                    overall_score=overall_score,
                    technical_score=technical_score,
                    business_score=business_score,
                    cost_score=cost_score,
                    app=app,
                )

            # CAP-020: SPOF L1-L2 override in evidence trail path
            if et_cap_result.get("spof_l1_l2", False) and time_action == "ELIMINATE":
                time_action = "TOLERATE"
            disposition, disp_confidence, disp_uncertainty_reasons = RationalizationScoringService._derive_disposition(
                time_action=time_action,
                overall_score=float(overall_score),
                technical_score=float(technical_score),
                business_score=float(business_score),
                cost_score=float(cost_score),
                app=app,
            )
            readiness = RationalizationScoringService.evaluate_readiness(app)

            # Apply the same readiness gate used in calculate_app_score so the
            # evidence trail always reflects the true stored disposition.
            if not readiness["is_decision_ready"]:
                final_disposition_value = DispositionAction.INSUFFICIENT_EVIDENCE.value
                final_confidence = "none"
                final_uncertainty_reasons: list[str] = []
                evidence_trail.append({
                    "factor": "Insufficient Evidence Gate",
                    "dimension": "readiness",
                    "weight": None,
                    "raw_value": readiness["readiness_score"],
                    "contribution": 0,
                    "source": "RationalizationScoringService.evaluate_readiness()",
                    "rationale": (
                        f"Recommendation overridden to INSUFFICIENT_EVIDENCE. "
                        f"Missing high-severity dimensions: {readiness['missing_critical']}. "
                        f"Readiness score: {readiness['readiness_score']:.0%}. "
                        f"Populate the missing fields to obtain a reliable TIME recommendation."
                    ),
                    "insufficient_evidence": True,
                    "missing_dimensions": readiness["missing_critical"],
                })
            else:
                final_disposition_value = disposition.value
                final_confidence = disp_confidence
                final_uncertainty_reasons = disp_uncertainty_reasons

            policy_applied = (
                {"id": active_policy.id, "name": active_policy.name}
                if active_policy
                else None
            )

            return {
                "app_id": application_id,
                "app_name": app.name,
                "scoring_config_name": scoring_config.name,
                "policy_applied": policy_applied,
                "scores": {
                    "technical_health": round(technical_score, 2),
                    "business_value": round(business_score, 2),
                    "cost_efficiency": round(cost_score, 2),
                    "vendor_risk": round(vendor_score, 2),
                    "capability_coverage": et_cap_result,
                    "capability_adjustment": et_cap_adjustment,
                    "overall": round(overall_score, 2),
                },
                "weights": {
                    "technical_health": tech_weight_pct,
                    "business_value": biz_weight_pct,
                    "cost_efficiency": cost_weight_pct,
                    "vendor_risk": vendor_weight_pct,
                    "capability_coverage": "adjustment (+/-10 pts)",
                },
                "overall_score": round(overall_score, 2),
                "time_action": time_action,
                "time_action_tentative": not readiness["is_decision_ready"],
                "disposition_action": final_disposition_value,
                "disposition_confidence": final_confidence,
                "confidence_reasons": final_uncertainty_reasons,
                "insufficient_evidence": not readiness["is_decision_ready"],
                "missing_dimensions": readiness["missing_critical"] if not readiness["is_decision_ready"] else [],
                "readiness": {
                    "readiness_score": readiness["readiness_score"],
                    "is_decision_ready": readiness["is_decision_ready"],
                    "missing_critical": readiness["missing_critical"],
                    "dimensions": readiness["dimensions"],
                },
                "evidence_trail": evidence_trail,
            }

        except Exception as exc:
            logger.error(
                f"Error building evidence trail for app {application_id}: {exc}", exc_info=True
            )
            return {"error": str(exc)}

    @staticmethod
    def _compute_capability_score(app_id: int) -> Dict:
        """
        Compute capability coverage dimension for rationalization scoring.

        Analyses both mapping tables to determine:
        - Weighted coverage score based on capability level and strategic importance
        - Single-point-of-failure (SPOF) detection: capabilities where this app
          is the ONLY supporter
        - Consolidation candidates: other apps covering the exact same set of capabilities
        - Normalized capability score (0-100) for overall score adjustment
        - L1-L2 SPOF retention flag (blocks ELIMINATE recommendations)

        Sources queried (merged, de-duplicated by capability name):
        1. unified_application_capability_mapping + unified_capabilities
        2. application_capability_mapping + business_capability (CAP-020)

        Scoring weights by capability level:
        - L1: 3.0 weight (strategic)
        - L2: 2.0 weight (core)
        - L3+: 1.0 weight (supporting/operational/detailed)

        Unified capability level weights (legacy, retained for backward compatibility):
        - L1 critical: +30, L1 other: +20
        - L2: +15, L3: +10, L4: +5, L5: +2

        Returns:
            Dict with keys:
            - score (int): Weighted capability coverage score (0+, legacy)
            - capability_score (float): Normalized 0-100 score for overall adjustment
            - capability_adjustment (float): Points to add/subtract from overall score
            - single_point_of_failure (bool): True if app is sole supporter of any capability
            - spof_l1_l2 (bool): True if SPOF specifically for L1-L2 capabilities
            - spof_capabilities (list): List of dicts with id/name/level of SPOF capabilities
            - consolidation_candidates (list): List of dicts with id/name of apps covering
              the same capability set
            - business_capability_count (int): Number of BusinessCapability mappings found
            - l1_count (int): Number of L1 capabilities mapped
            - l2_count (int): Number of L2 capabilities mapped
        """
        neutral_result = {
            "score": 0,
            "capability_score": 0.0,
            "capability_adjustment": 0.0,
            "single_point_of_failure": False,
            "spof_l1_l2": False,
            "spof_capabilities": [],
            "consolidation_candidates": [],
            "business_capability_count": 0,
            "l1_count": 0,
            "l2_count": 0,
        }

        try:
            from sqlalchemy import func

            from app.models.application_capability import ApplicationCapabilityMapping
            from app.models.business_capabilities import BusinessCapability
            from app.models.unified_application_capability_mapping import (
                UnifiedApplicationCapabilityMapping,
            )
            from app.models.unified_capability import UnifiedCapability

            # ── Source 1: Unified capability mappings ──
            unified_mappings = (
                db.session.query(
                    UnifiedApplicationCapabilityMapping.unified_capability_id,
                    UnifiedCapability.name,
                    UnifiedCapability.level,
                    UnifiedCapability.strategic_importance,
                )
                .join(
                    UnifiedCapability,
                    UnifiedApplicationCapabilityMapping.unified_capability_id == UnifiedCapability.id,
                )
                .filter(
                    UnifiedApplicationCapabilityMapping.application_component_id == app_id,
                    UnifiedApplicationCapabilityMapping.is_active.is_(True),
                )
                .all()
            )

            # ── Source 2: BusinessCapability mappings (CAP-020) ──
            biz_cap_mappings = (
                db.session.query(
                    ApplicationCapabilityMapping.business_capability_id,
                    BusinessCapability.name,
                    BusinessCapability.level,
                    BusinessCapability.strategic_importance,
                )
                .join(
                    BusinessCapability,
                    ApplicationCapabilityMapping.business_capability_id == BusinessCapability.id,
                )
                .filter(
                    ApplicationCapabilityMapping.application_component_id == app_id,
                    ApplicationCapabilityMapping.is_active.is_(True),
                )
                .all()
            )

            if not unified_mappings and not biz_cap_mappings:
                return neutral_result

            # ── Legacy weighted score (unified capabilities) ──
            level_weights = {1: 20, 2: 15, 3: 10, 4: 5, 5: 2}
            l1_critical_bonus = 10  # Extra points for L1 + critical -> total 30

            total_score = 0
            unified_capability_ids = set()

            for cap_id, _cap_name, level, strategic_importance in unified_mappings:
                unified_capability_ids.add(cap_id)
                base_weight = level_weights.get(level, 2)
                if level == 1 and strategic_importance and strategic_importance.lower() == "critical":
                    base_weight += l1_critical_bonus
                total_score += base_weight

            # ── CAP-020: BusinessCapability level-weighted score ──
            # L1 = 3.0, L2 = 2.0, L3+ = 1.0
            biz_cap_level_weights = {1: 3.0, 2: 2.0}
            biz_cap_weighted_total = 0.0
            biz_cap_ids = set()
            l1_count = 0
            l2_count = 0

            for cap_id, _cap_name, level, _strategic_importance in biz_cap_mappings:
                biz_cap_ids.add(cap_id)
                weight = biz_cap_level_weights.get(level, 1.0)
                biz_cap_weighted_total += weight
                if level == 1:
                    l1_count += 1
                elif level == 2:
                    l2_count += 1

            # ── Normalized capability score (0-100) ──
            # Scale: An app with 5+ L1 capabilities and 10+ L2 capabilities scores 100.
            # The formula rewards breadth and criticality of capability coverage.
            max_expected_weighted = 5 * 3.0 + 10 * 2.0 + 10 * 1.0  # = 45.0
            if biz_cap_weighted_total > 0:
                capability_score = min(100.0, (biz_cap_weighted_total / max_expected_weighted) * 100.0)
            elif unified_mappings:
                # Fall back to unified mapping score if no business capability data
                max_unified_expected = 3 * 20 + 5 * 15 + 10 * 10  # = 235
                capability_score = min(100.0, (total_score / max_unified_expected) * 100.0)
            else:
                capability_score = 0.0

            # ── Capability adjustment ──
            # Ranges from -10 to +10 points applied to overall score.
            # Below 30 → penalty (app supports few/no critical capabilities → easier to eliminate)
            # 30-70 → neutral zone
            # Above 70 → bonus (app supports many critical capabilities → retain/invest)
            if capability_score >= 70:
                capability_adjustment = min(20.0, (capability_score - 70.0) / 1.5)
            elif capability_score < 30:
                capability_adjustment = max(-20.0, (capability_score - 30.0) / 1.5)
            else:
                capability_adjustment = 0.0

            # ── SPOF detection (unified capabilities) ──
            spof_capabilities = []
            has_spof = False

            if unified_capability_ids:
                spof_subquery = (
                    db.session.query(
                        UnifiedApplicationCapabilityMapping.unified_capability_id,
                    )
                    .filter(
                        UnifiedApplicationCapabilityMapping.unified_capability_id.in_(unified_capability_ids),
                        UnifiedApplicationCapabilityMapping.is_active.is_(True),
                    )
                    .group_by(UnifiedApplicationCapabilityMapping.unified_capability_id)
                    .having(func.count(func.distinct(
                        UnifiedApplicationCapabilityMapping.application_component_id
                    )) == 1)
                    .subquery()
                )

                spof_rows = (
                    db.session.query(
                        UnifiedCapability.id,
                        UnifiedCapability.name,
                        UnifiedCapability.level,
                    )
                    .join(spof_subquery, UnifiedCapability.id == spof_subquery.c.unified_capability_id)
                    .all()
                )

                spof_capabilities = [
                    {"id": row.id, "name": row.name, "level": row.level}
                    for row in spof_rows
                ]
                has_spof = len(spof_capabilities) > 0

            # ── SPOF detection (BusinessCapability — CAP-020) ──
            spof_l1_l2 = False

            if biz_cap_ids:
                biz_spof_subquery = (
                    db.session.query(
                        ApplicationCapabilityMapping.business_capability_id,
                    )
                    .filter(
                        ApplicationCapabilityMapping.business_capability_id.in_(biz_cap_ids),
                        ApplicationCapabilityMapping.is_active.is_(True),
                    )
                    .group_by(ApplicationCapabilityMapping.business_capability_id)
                    .having(func.count(func.distinct(
                        ApplicationCapabilityMapping.application_component_id
                    )) == 1)
                    .subquery()
                )

                biz_spof_rows = (
                    db.session.query(
                        BusinessCapability.id,
                        BusinessCapability.name,
                        BusinessCapability.level,
                    )
                    .join(biz_spof_subquery, BusinessCapability.id == biz_spof_subquery.c.business_capability_id)
                    .all()
                )

                for row in biz_spof_rows:
                    entry = {"id": row.id, "name": row.name, "level": row.level, "source": "business_capability"}
                    spof_capabilities.append(entry)
                    has_spof = True
                    if row.level in (1, 2):
                        spof_l1_l2 = True

                # Also boost adjustment for SPOF L1-L2: these apps must NOT be eliminated
                if spof_l1_l2:
                    capability_adjustment = max(capability_adjustment, 5.0)

            # Also check unified SPOFs for L1-L2
            if not spof_l1_l2:
                for spof in spof_capabilities:
                    if spof.get("level") in (1, 2):
                        spof_l1_l2 = True
                        capability_adjustment = max(capability_adjustment, 5.0)
                        break

            # ── Consolidation candidate detection ──
            consolidation_candidates = []
            if unified_capability_ids:
                cap_count = len(unified_capability_ids)

                candidate_subquery = (
                    db.session.query(
                        UnifiedApplicationCapabilityMapping.application_component_id,
                    )
                    .filter(
                        UnifiedApplicationCapabilityMapping.unified_capability_id.in_(unified_capability_ids),
                        UnifiedApplicationCapabilityMapping.is_active.is_(True),
                        UnifiedApplicationCapabilityMapping.application_component_id != app_id,
                    )
                    .group_by(UnifiedApplicationCapabilityMapping.application_component_id)
                    .having(func.count(func.distinct(
                        UnifiedApplicationCapabilityMapping.unified_capability_id
                    )) == cap_count)
                    .subquery()
                )

                candidate_total_caps = (
                    db.session.query(
                        UnifiedApplicationCapabilityMapping.application_component_id,
                        func.count(func.distinct(
                            UnifiedApplicationCapabilityMapping.unified_capability_id
                        )).label("total_caps"),
                    )
                    .filter(
                        UnifiedApplicationCapabilityMapping.application_component_id.in_(
                            db.session.query(candidate_subquery.c.application_component_id)
                        ),
                        UnifiedApplicationCapabilityMapping.is_active.is_(True),
                    )
                    .group_by(UnifiedApplicationCapabilityMapping.application_component_id)
                    .having(func.count(func.distinct(
                        UnifiedApplicationCapabilityMapping.unified_capability_id
                    )) == cap_count)
                    .subquery()
                )

                candidate_apps = (
                    db.session.query(
                        ApplicationComponent.id,
                        ApplicationComponent.name,
                    )
                    .join(
                        candidate_total_caps,
                        ApplicationComponent.id == candidate_total_caps.c.application_component_id,
                    )
                    .all()
                )

                consolidation_candidates = [
                    {"id": row.id, "name": row.name} for row in candidate_apps
                ]

            return {
                "score": total_score,
                "capability_score": round(capability_score, 2),
                "capability_adjustment": round(capability_adjustment, 2),
                "single_point_of_failure": has_spof,
                "spof_l1_l2": spof_l1_l2,
                "spof_capabilities": spof_capabilities,
                "consolidation_candidates": consolidation_candidates,
                "business_capability_count": len(biz_cap_ids),
                "l1_count": l1_count,
                "l2_count": l2_count,
            }

        except Exception as exc:
            logger.warning(
                f"Capability score computation failed for app {app_id}: {exc}",
                exc_info=True,
            )
            return neutral_result

    @staticmethod
    def _calculate_technical_health(app: ApplicationComponent) -> tuple:
        """
        Calculate technical health score (0 - 100, higher = healthier).

        Uses structured ApplicationComponent fields when available, with
        keyword-based fallback for apps that only have description text.

        Factors:
        - Deployment model and architecture style (structured fields)
        - Technology age and technical risk (structured fields)
        - Integration complexity
        - Lifecycle status

        Returns:
            Tuple of (score: float, evidence: list[dict]) where each evidence entry
            documents the factor, raw value, points contributed, source field, and rationale.
        """
        score = 50.0  # Baseline
        evidence: List[Dict] = []

        # Lifecycle status impact
        # Model values: planning, development, testing, operational, deprecated, retired
        lifecycle_scores = {
            "operational": 20, "testing": 15, "development": 10,
            "planning": 5, "deprecated": -15, "retired": -30,
        }
        # Normalise Abacus codes → canonical lifecycle before scoring.
        # ABACUS_LIFECYCLE_MAP handles "2.1 STRATEGIC" → "operational",
        # "5. DECOMMISSIONED" → "deprecated", etc. Without this, all Abacus
        # apps get 0 lifecycle delta because the raw codes don't match the
        # lifecycle_scores keys.
        lifecycle_info = RationalizationScoringService._map_deployment_to_lifecycle(app)
        canonical_lifecycle = lifecycle_info["lifecycle"]
        lifecycle_delta = lifecycle_scores.get(canonical_lifecycle, 0)
        score += lifecycle_delta
        evidence.append({
            "factor": "Lifecycle Status",
            "weight": None,
            "raw_value": app.lifecycle_status,
            "contribution": lifecycle_delta,
            "source": "ApplicationComponent.lifecycle_status",
            "rationale": (
                f"Lifecycle '{app.lifecycle_status}' → canonical '{canonical_lifecycle}' "
                f"adds {lifecycle_delta:+d} pts"
            ),
        })

        # --- Structured field scoring (preferred) ---
        has_structured_data = any([
            app.deployment_model,
            app.architecture_style,
            app.technology_age_years,
        ])

        if has_structured_data:
            # Deployment model (replaces keyword scan for "cloud"/"mainframe")
            deployment_scores = {
                "saas": 15, "cloud": 15, "hybrid": 5,
                "on_premise": -5, "mainframe": -20,
            }
            if app.deployment_model:
                deploy_delta = deployment_scores.get(app.deployment_model.lower(), 0)
                score += deploy_delta
                evidence.append({
                    "factor": "Deployment Model",
                    "weight": None,
                    "raw_value": app.deployment_model,
                    "contribution": deploy_delta,
                    "source": "ApplicationComponent.deployment_model",
                    "rationale": f"Deployment '{app.deployment_model}' adds {deploy_delta:+d} pts",
                })

            # Architecture style (fixes wrong field name: was architecture_type)
            arch_scores = {
                "microservices": 15, "event_driven": 10,
                "service_oriented": 5, "monolithic": -5,
            }
            if app.architecture_style:
                arch_delta = arch_scores.get(app.architecture_style.lower(), 0)
                score += arch_delta
                evidence.append({
                    "factor": "Architecture Style",
                    "weight": None,
                    "raw_value": app.architecture_style,
                    "contribution": arch_delta,
                    "source": "ApplicationComponent.architecture_style",
                    "rationale": f"Architecture style '{app.architecture_style}' adds {arch_delta:+d} pts",
                })

            # Technology age
            age = app.technology_age_years
            if age is not None:
                if age <= 3:
                    age_delta = 15
                    age_label = "0–3 yrs (modern)"
                elif age <= 7:
                    age_delta = 5
                    age_label = "4–7 yrs (recent)"
                elif age <= 12:
                    age_delta = -10
                    age_label = "8–12 yrs (ageing)"
                else:
                    age_delta = -20
                    age_label = f"{age} yrs (legacy)"
                score += age_delta
                evidence.append({
                    "factor": "Technology Age",
                    "weight": None,
                    "raw_value": age,
                    "contribution": age_delta,
                    "source": "ApplicationComponent.technology_age_years",
                    "rationale": f"Technology age {age} yrs ({age_label}) adds {age_delta:+d} pts",
                })

            # Technical risk from structured field
            risk_scores = {"low": 10, "medium": 0, "high": -10, "critical": -20}
            if app.technical_risk:
                risk_delta = risk_scores.get(app.technical_risk.lower(), 0)
                score += risk_delta
                evidence.append({
                    "factor": "Technical Risk",
                    "weight": None,
                    "raw_value": app.technical_risk,
                    "contribution": risk_delta,
                    "source": "ApplicationComponent.technical_risk",
                    "rationale": f"Technical risk '{app.technical_risk}' adds {risk_delta:+d} pts",
                })

        else:
            # --- Fallback: keyword scan for apps with only description ---
            if app.description:
                desc_lower = app.description.lower()

                modern_keywords = [
                    "cloud", "microservice", "api", "saas",
                    "containerized", "kubernetes",
                ]
                modern_count = sum(1 for kw in modern_keywords if kw in desc_lower)
                modern_delta = min(modern_count * 5, 20)
                score += modern_delta
                if modern_count > 0:
                    evidence.append({
                        "factor": "Modern Technology Keywords (description scan)",
                        "weight": None,
                        "raw_value": modern_count,
                        "contribution": modern_delta,
                        "source": "ApplicationComponent.description (keyword fallback)",
                        "rationale": f"{modern_count} modern keyword(s) found in description adds {modern_delta:+d} pts",
                    })

                legacy_keywords = [
                    "mainframe", "cobol", "legacy", "deprecated",
                    "end-of-life", "unsupported",
                ]
                legacy_count = sum(1 for kw in legacy_keywords if kw in desc_lower)
                legacy_delta = -min(legacy_count * 10, 30)
                score += legacy_delta
                if legacy_count > 0:
                    evidence.append({
                        "factor": "Legacy Technology Keywords (description scan)",
                        "weight": None,
                        "raw_value": legacy_count,
                        "contribution": legacy_delta,
                        "source": "ApplicationComponent.description (keyword fallback)",
                        "rationale": f"{legacy_count} legacy keyword(s) found in description adds {legacy_delta:+d} pts",
                    })

        # Integration complexity (structured field, works for both paths)
        # Model stores lowercase: "low", "medium", "high"
        complexity_scores = {"low": 10, "medium": 5, "high": -5, "very_high": -10}
        if app.integration_complexity:
            complexity_key = app.integration_complexity.lower() if isinstance(app.integration_complexity, str) else app.integration_complexity
            complexity_delta = complexity_scores.get(complexity_key, 0)
            score += complexity_delta
            evidence.append({
                "factor": "Integration Complexity",
                "weight": None,
                "raw_value": app.integration_complexity,
                "contribution": complexity_delta,
                "source": "ApplicationComponent.integration_complexity",
                "rationale": f"Integration complexity '{app.integration_complexity}' adds {complexity_delta:+d} pts",
            })

        # Check for pending retirement
        if app.planned_retirement_date and app.planned_retirement_date < datetime.utcnow() + timedelta(days=365):
            score -= 20  # Retiring soon = technical debt
            evidence.append({
                "factor": "Planned Retirement",
                "weight": None,
                "raw_value": str(app.planned_retirement_date),
                "contribution": -20,
                "source": "ApplicationComponent.planned_retirement_date",
                "rationale": f"Retirement date {app.planned_retirement_date} is within 12 months: −20 pts",
            })

        # Technical capability coverage (ACM taxonomy — 273 capabilities, 7 domains)
        # Domain breadth: multi-domain apps are harder to replace (platform signal).
        # Coverage quality: "full" mappings indicate deeper integration vs "partial" (keyword-inferred).
        # Max contribution: +20 pts (breadth 10 + quality 10). Never penalises — absence is neutral.
        try:
            from sqlalchemy import text as _sql_text
            cap_rows = db.session.execute(
                _sql_text("""
                    SELECT tc.acm_domain, atcm.capability_coverage
                    FROM application_technical_capability_mapping atcm
                    JOIN technical_capabilities tc
                        ON tc.id = atcm.technical_capability_id
                        AND tc.level = 'L1'
                    WHERE atcm.application_id = :app_id
                """),
                {"app_id": app.id},
            ).fetchall()

            if cap_rows:
                domains_covered = {r[0] for r in cap_rows}
                full_count = sum(1 for r in cap_rows if (r[1] or "") == "full")

                # Breadth: each unique ACM domain this app covers
                breadth = len(domains_covered)
                if breadth >= 4:
                    breadth_delta = 10
                    breadth_label = f"{breadth} ACM domains (platform-level)"
                elif breadth >= 2:
                    breadth_delta = 5
                    breadth_label = f"{breadth} ACM domains (multi-capability)"
                else:
                    breadth_delta = 0
                    breadth_label = f"{breadth} ACM domain (single-purpose)"

                if breadth_delta:
                    score += breadth_delta
                    evidence.append({
                        "factor": "ACM Domain Breadth",
                        "weight": None,
                        "raw_value": sorted(domains_covered),
                        "contribution": breadth_delta,
                        "source": "application_technical_capability_mapping",
                        "rationale": f"{breadth_label}: +{breadth_delta} pts. Multi-domain apps are harder to replace.",
                    })

                # Coverage quality: proportion of "full" vs "partial" mappings
                if full_count > len(cap_rows) / 2:
                    quality_delta = 10
                    quality_label = f"{full_count}/{len(cap_rows)} full-coverage mappings"
                elif full_count > 0:
                    quality_delta = 5
                    quality_label = f"{full_count}/{len(cap_rows)} partial full-coverage mappings"
                else:
                    quality_delta = 0
                    quality_label = "all partial coverage (keyword-inferred)"

                if quality_delta:
                    score += quality_delta
                    evidence.append({
                        "factor": "ACM Coverage Quality",
                        "weight": None,
                        "raw_value": {"full": full_count, "total": len(cap_rows)},
                        "contribution": quality_delta,
                        "source": "application_technical_capability_mapping.capability_coverage",
                        "rationale": f"{quality_label}: +{quality_delta} pts",
                    })
        except Exception as exc:
            logger.debug("suppressed error in RationalizationScoringService._calculate_technical_health (app/services/rationalization_scoring_service.py): %s", exc)  # Never let capability scoring break the main score

        return max(0.0, min(score, 100.0)), evidence

    @staticmethod
    def _calculate_business_value(app: ApplicationComponent) -> tuple:
        """
        Calculate business value score (0 - 100, higher = more valuable).

        Factors:
        - Strategic alignment
        - Business criticality
        - Process support breadth
        - User base size

        Returns:
            Tuple of (score: float, evidence: list[dict]).
        """
        score = 50.0  # Baseline
        evidence: List[Dict] = []

        # Criticality level impact
        # Model stores Title case: "Critical", "High", "Medium", "Low"
        criticality_scores = {"critical": 30, "high": 20, "medium": 5, "low": -10}
        crit_val = (app.business_criticality or "").lower()
        crit_delta = criticality_scores.get(crit_val, 0)
        score += crit_delta
        evidence.append({
            "factor": "Business Criticality",
            "weight": None,
            "raw_value": app.business_criticality,
            "contribution": crit_delta,
            "source": "ApplicationComponent.business_criticality",
            "rationale": f"Business criticality '{app.business_criticality}' adds {crit_delta:+d} pts",
        })

        # Strategic importance
        # Model stores lowercase: "critical", "high", "medium", "low"
        if app.strategic_importance:
            strategic_scores = {
                "critical": 20,
                "high": 10,
                "medium": 0,
                "low": -10,
            }
            strat_key = app.strategic_importance.lower() if isinstance(app.strategic_importance, str) else app.strategic_importance
            strat_delta = strategic_scores.get(strat_key, 0)
            score += strat_delta
            evidence.append({
                "factor": "Strategic Importance",
                "weight": None,
                "raw_value": app.strategic_importance,
                "contribution": strat_delta,
                "source": "ApplicationComponent.strategic_importance",
                "rationale": f"Strategic importance '{app.strategic_importance}' adds {strat_delta:+d} pts",
            })

        # Capability coverage — real organizational data (primary differentiator)
        # Apps covering more business capabilities are more valuable to the organization.
        try:
            from app import db as _db
            from app.utils.tenant_sql import org_scope

            # Dual-use path: `flask rationalization score-all` walks every
            # organisation's applications in one session with no request
            # context, so g.current_org_id is None here and reaching for it
            # would leave the query global. Scope on the application being
            # scored instead — same predicate under a request and under the CLI.
            #
            # bc, not acm: application_capability_mapping has an organization_id
            # column but it is NULL on every row, so a predicate there would
            # score every application as covering zero capabilities.
            _org_clause, _org_params = org_scope(
                prefix="bc.", org_id=getattr(app, "organization_id", None)
            )
            cap_row = _db.session.execute(_db.text(
                "SELECT COUNT(*) as cap_count, "
                "SUM(CASE WHEN bc.level = 1 THEN 1 ELSE 0 END) as l1_count, "
                "SUM(CASE WHEN bc.level = 2 THEN 1 ELSE 0 END) as l2_count "
                "FROM application_capability_mapping acm "
                "JOIN business_capability bc ON bc.id = acm.business_capability_id "
                "WHERE acm.application_component_id = :app_id" + _org_clause
            ), {"app_id": app.id, **_org_params}).fetchone()
            cap_count = cap_row[0] if cap_row else 0
            l1_count = cap_row[1] if cap_row else 0
            l2_count = cap_row[2] if cap_row else 0
        except Exception:
            cap_count, l1_count, l2_count = 0, 0, 0

        if cap_count > 0:
            # More capabilities = more business value (up to 25 pts)
            cap_delta = min(cap_count * 5, 25)
            # L1 strategic capabilities are worth extra
            l1_bonus = min(l1_count * 8, 15)
            total_cap_delta = cap_delta + l1_bonus
            score += total_cap_delta
            evidence.append({
                "factor": "Capability Coverage",
                "weight": None,
                "raw_value": f"{cap_count} capabilities ({l1_count} L1, {l2_count} L2)",
                "contribution": total_cap_delta,
                "source": "application_capability_mapping",
                "rationale": f"Supports {cap_count} business capabilities ({l1_count} strategic L1, {l2_count} tactical L2) → +{total_cap_delta} pts",
            })
        else:
            score -= 10  # No capability mappings = unknown business value
            evidence.append({
                "factor": "Capability Coverage",
                "weight": None,
                "raw_value": "0 capabilities mapped",
                "contribution": -10,
                "source": "application_capability_mapping",
                "rationale": "No capability mappings found — business value unclear: −10 pts",
            })

        # Process support — load once to avoid 3 separate queries
        process_supports = ApplicationProcessSupport.query.filter_by(
            application_component_id=app.id
        ).all()

        process_count = len(process_supports)
        if process_count > 0:
            proc_delta = min(process_count * 3, 20)
            score += proc_delta
            evidence.append({
                "factor": "Process Support Breadth",
                "weight": None,
                "raw_value": process_count,
                "contribution": proc_delta,
                "source": "ApplicationProcessSupport (count)",
                "rationale": f"{process_count} process link(s); up to 20 pts cap → adds {proc_delta:+d} pts",
            })
        else:
            score -= 5  # Reduced penalty (capability coverage already accounts for this)
            evidence.append({
                "factor": "Process Support Breadth",
                "weight": None,
                "raw_value": 0,
                "contribution": -5,
                "source": "ApplicationProcessSupport (count)",
                "rationale": "No process support links found: −5 pts",
            })

        # Critical process support (derive from loaded data)
        critical_process_count = sum(
            1 for p in process_supports
            if p.criticality in ("high", "critical")
        )
        if critical_process_count > 0:
            crit_proc_delta = min(critical_process_count * 5, 15)
            score += crit_proc_delta
            evidence.append({
                "factor": "Critical Process Support",
                "weight": None,
                "raw_value": critical_process_count,
                "contribution": crit_proc_delta,
                "source": "ApplicationProcessSupport.criticality (high/critical)",
                "rationale": f"{critical_process_count} critical/high-criticality process link(s) adds {crit_proc_delta:+d} pts",
            })

        # Primary system bonus (derive from loaded data)
        is_primary = any(
            p.is_system_of_record for p in process_supports
        )
        if is_primary:
            score += 10
            evidence.append({
                "factor": "System of Record",
                "weight": None,
                "raw_value": True,
                "contribution": 10,
                "source": "ApplicationProcessSupport.is_system_of_record",
                "rationale": "Application is a system of record for at least one process: +10 pts",
            })

        return max(0.0, min(score, 100.0)), evidence

    @staticmethod
    def _calculate_cost_efficiency(app: ApplicationComponent) -> tuple:
        """
        Calculate cost efficiency score (0 - 100, higher = more efficient).

        Uses structured cost fields from ApplicationComponent when available,
        falls back to CapabilityCostAllocation and keyword scan.

        Factors:
        - Total cost of ownership (TCO) from structured fields
        - License + maintenance + infrastructure cost breakdown
        - Cost per user efficiency
        - Capability redundancy

        Returns:
            Tuple of (score: float, evidence: list[dict]).
        """
        score = 50.0  # Baseline
        evidence: List[Dict] = []

        # --- Prefer structured cost fields on ApplicationComponent ---
        # TODO(Release 2 Cost Fact consolidation): Use application_cost_accessor.get_annual_cost()
        # and related accessors instead of direct model field access.
        tco = app.total_cost_of_ownership
        license_cost = app.license_cost
        maint_cost = app.maintenance_cost
        infra_cost = app.infrastructure_cost

        annual_cost = None
        cost_source = None
        if tco is not None and tco > 0:
            annual_cost = float(tco)
            cost_source = "ApplicationComponent.total_cost_of_ownership"
        elif any(c is not None for c in [license_cost, maint_cost, infra_cost]):
            annual_cost = float(
                (license_cost or 0) + (maint_cost or 0) + (infra_cost or 0)
            )
            cost_source = "ApplicationComponent (license_cost + maintenance_cost + infrastructure_cost)"

        # Fallback: derive cost from capability cost allocations via capability mappings
        if annual_cost is None:
            cap_ids = [m.business_capability_id for m in (app.capability_mappings or [])]
            if cap_ids:
                cost_allocations = CapabilityCostAllocation.query.filter(
                    CapabilityCostAllocation.capability_id.in_(cap_ids)
                ).all()
                if cost_allocations:
                    total = sum(
                        float(ca.calculate_total_cost() or 0) for ca in cost_allocations
                    )
                    if total > 0:
                        annual_cost = total
                        cost_source = "CapabilityCostAllocation (derived from capability mappings)"

        if annual_cost is not None and annual_cost > 0:
            # Cost bracket impact
            if annual_cost < 50000:
                cost_delta = 20
                cost_bracket = "< £50k (low cost)"
            elif annual_cost < 100000:
                cost_delta = 10
                cost_bracket = "£50k–£100k (moderate)"
            elif annual_cost < 250000:
                cost_delta = -5
                cost_bracket = "£100k–£250k (high)"
            elif annual_cost < 500000:
                cost_delta = -15
                cost_bracket = "£250k–£500k (very high)"
            else:
                cost_delta = -25
                cost_bracket = f"£{annual_cost:,.0f} (extremely high)"
            score += cost_delta
            evidence.append({
                "factor": "Annual Cost (TCO)",
                "weight": None,
                "raw_value": round(annual_cost, 2),
                "contribution": cost_delta,
                "source": cost_source,
                "rationale": f"Annual cost {cost_bracket} adds {cost_delta:+d} pts",
            })

            # Cost per user efficiency
            user_count = app.user_count
            if user_count is not None and user_count > 0:
                cost_per_user = annual_cost / user_count
                if cost_per_user < 100:
                    cpu_delta = 10
                    cpu_label = f"£{cost_per_user:.0f}/user (efficient)"
                elif cost_per_user > 500:
                    cpu_delta = -10
                    cpu_label = f"£{cost_per_user:.0f}/user (expensive)"
                else:
                    cpu_delta = 0
                    cpu_label = f"£{cost_per_user:.0f}/user (average)"
                score += cpu_delta
                if cpu_delta != 0:
                    evidence.append({
                        "factor": "Cost per User",
                        "weight": None,
                        "raw_value": round(cost_per_user, 2),
                        "contribution": cpu_delta,
                        "source": f"{cost_source} / ApplicationComponent.user_count",
                        "rationale": f"Cost per user {cpu_label} adds {cpu_delta:+d} pts",
                    })
        else:
            evidence.append({
                "factor": "Annual Cost (TCO)",
                "weight": None,
                "raw_value": None,
                "contribution": 0,
                "source": "ApplicationComponent (no cost data available)",
                "rationale": "No cost data found; no cost adjustment applied",
            })

        # Check for redundancy via capability mappings
        # ApplicationComponent has no direct business_capability_id; uses capability_mappings relationship
        cap_mappings = app.capability_mappings
        if cap_mappings:
            cap_ids = [m.business_capability_id for m in cap_mappings]
            if cap_ids:
                from app.models.application_capability import ApplicationCapabilityMapping
                sibling_count = (
                    # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
                    ApplicationCapabilityMapping.query.filter(
                        ApplicationCapabilityMapping.business_capability_id.in_(cap_ids),
                        ApplicationCapabilityMapping.application_component_id != app.id,
                    )
                    .distinct(ApplicationCapabilityMapping.application_component_id)
                    .count()
                )
                if sibling_count > 2:
                    redundancy_delta = -min(sibling_count * 5, 20)
                    score += redundancy_delta
                    evidence.append({
                        "factor": "Capability Redundancy",
                        "weight": None,
                        "raw_value": sibling_count,
                        "contribution": redundancy_delta,
                        "source": "ApplicationCapabilityMapping (sibling apps sharing same capability)",
                        "rationale": f"{sibling_count} sibling apps share the same capability bucket: redundancy penalty {redundancy_delta:+d} pts",
                    })

        # Deployment model efficiency bonus (structured field)
        deploy = app.deployment_model
        if deploy:
            deploy_lower = deploy.lower()
            if deploy_lower in ("saas", "cloud"):
                score += 10  # SaaS/cloud typically lower maintenance
                evidence.append({
                    "factor": "Deployment Model (cost)",
                    "weight": None,
                    "raw_value": deploy,
                    "contribution": 10,
                    "source": "ApplicationComponent.deployment_model",
                    "rationale": f"SaaS/Cloud deployment '{deploy}' typically has lower maintenance cost: +10 pts",
                })
            elif deploy_lower == "mainframe":
                score -= 10  # High maintenance cost
                evidence.append({
                    "factor": "Deployment Model (cost)",
                    "weight": None,
                    "raw_value": deploy,
                    "contribution": -10,
                    "source": "ApplicationComponent.deployment_model",
                    "rationale": "Mainframe deployment carries high maintenance cost: −10 pts",
                })

        return max(0.0, min(score, 100.0)), evidence

    @staticmethod
    def _calculate_vendor_risk(app: ApplicationComponent) -> dict:
        """
        Calculate vendor risk score (0 - 100, higher = lower risk).

        Returns dict with keys: score, vendor_lock_in_level, vendor_viability_score,
        exit_complexity, exit_cost_estimate, alternative_vendors_available, evidence.
        The ``evidence`` key holds a list of per-factor evidence dicts compatible with
        the evidence_trail structure used by calculate_app_score / get_evidence_trail.
        """
        score = 50.0  # Baseline
        evidence: List[Dict] = []

        # Get vendor info via application ↔ vendor product association table
        vendor_link = (
            db.session.query(VendorProduct, VendorOrganization)
            .join(
                application_vendor_products,
                VendorProduct.id == application_vendor_products.c.vendor_product_id,
            )
            .join(VendorOrganization, VendorProduct.vendor_organization_id == VendorOrganization.id)
            .filter(application_vendor_products.c.application_component_id == app.id)
            .first()
        )

        if not vendor_link:
            # No vendor = potential in-house app = higher risk (no support)
            return {
                "score": 40.0,
                "vendor_lock_in_level": None,
                "vendor_viability_score": None,
                "exit_complexity": None,
                "exit_cost_estimate": None,
                "alternative_vendors_available": None,
                "evidence": [{
                    "factor": "Vendor Link",
                    "weight": None,
                    "raw_value": None,
                    "contribution": -10,
                    "source": "application_component_vendor_products (no link found)",
                    "rationale": "No vendor product linked; scored as 40 (in-house / unsupported app penalty)",
                }],
            }

        product, vendor = vendor_link

        # --- Prefer structured fields on VendorOrganization ---
        enterprise_score = vendor.enterprise_readiness_score
        financial_score = vendor.financial_health_score

        if enterprise_score is not None:
            # enterprise_readiness_score is 0-100 — map to scoring impact
            if enterprise_score >= 80:
                ent_delta = 20
                ent_label = f"score {enterprise_score} (excellent)"
            elif enterprise_score >= 60:
                ent_delta = 10
                ent_label = f"score {enterprise_score} (good)"
            elif enterprise_score >= 40:
                ent_delta = 0
                ent_label = f"score {enterprise_score} (adequate)"
            else:
                ent_delta = -15
                ent_label = f"score {enterprise_score} (poor)"
            score += ent_delta
            evidence.append({
                "factor": "Vendor Enterprise Readiness",
                "weight": None,
                "raw_value": enterprise_score,
                "contribution": ent_delta,
                "source": "VendorOrganization.enterprise_readiness_score",
                "rationale": f"Enterprise readiness {ent_label} adds {ent_delta:+d} pts",
            })

        if financial_score is not None:
            if financial_score >= 80:
                fin_delta = 10
                fin_label = f"score {financial_score} (healthy)"
            elif financial_score >= 50:
                fin_delta = 5
                fin_label = f"score {financial_score} (stable)"
            elif financial_score < 30:
                fin_delta = -20
                fin_label = f"score {financial_score} (at risk)"
            else:
                fin_delta = 0
                fin_label = f"score {financial_score} (moderate)"
            score += fin_delta
            evidence.append({
                "factor": "Vendor Financial Health",
                "weight": None,
                "raw_value": financial_score,
                "contribution": fin_delta,
                "source": "VendorOrganization.financial_health_score",
                "rationale": f"Financial health {fin_label} adds {fin_delta:+d} pts",
            })

        # Vendor status (structured field)
        vendor_status = vendor.status
        if vendor_status:
            vendor_status_scores = {"active": 5, "restricted": -10, "deprecated": -25}
            vs_delta = vendor_status_scores.get(vendor_status, 0)
            score += vs_delta
            if vs_delta != 0:
                evidence.append({
                    "factor": "Vendor Status",
                    "weight": None,
                    "raw_value": vendor_status,
                    "contribution": vs_delta,
                    "source": "VendorOrganization.status",
                    "rationale": f"Vendor status '{vendor_status}' adds {vs_delta:+d} pts",
                })

        # Fallback: keyword scan only if no structured scores available
        if enterprise_score is None and financial_score is None and vendor.description:
            desc_lower = vendor.description.lower()
            established_keywords = ["enterprise", "global", "leader", "fortune"]
            established_count = sum(1 for kw in established_keywords if kw in desc_lower)
            est_delta = min(established_count * 8, 20)
            score += est_delta
            if est_delta > 0:
                evidence.append({
                    "factor": "Vendor Stability Keywords (description scan)",
                    "weight": None,
                    "raw_value": established_count,
                    "contribution": est_delta,
                    "source": "VendorOrganization.description (keyword fallback)",
                    "rationale": f"{established_count} stability keyword(s) found → adds {est_delta:+d} pts",
                })

            risk_keywords = ["startup", "acquired", "bankruptcy", "financial difficulty"]
            risk_count = sum(1 for kw in risk_keywords if kw in desc_lower)
            risk_delta = -min(risk_count * 15, 30)
            score += risk_delta
            if risk_delta < 0:
                evidence.append({
                    "factor": "Vendor Risk Keywords (description scan)",
                    "weight": None,
                    "raw_value": risk_count,
                    "contribution": risk_delta,
                    "source": "VendorOrganization.description (keyword fallback)",
                    "rationale": f"{risk_count} risk keyword(s) found → adds {risk_delta:+d} pts",
                })

        # Check vendor concentration (portfolio percentage)
        from app.models.application_rationalization import VendorConcentrationAnalysis

        concentration = VendorConcentrationAnalysis.query.filter_by(
            vendor_organization_id=vendor.id
        ).first()

        if concentration:
            # High concentration = risk
            if concentration.percentage_of_it_budget and concentration.percentage_of_it_budget > 30:
                score -= 20
                evidence.append({
                    "factor": "Vendor IT Budget Concentration",
                    "weight": None,
                    "raw_value": concentration.percentage_of_it_budget,
                    "contribution": -20,
                    "source": "VendorConcentrationAnalysis.percentage_of_it_budget",
                    "rationale": f"Vendor accounts for {concentration.percentage_of_it_budget:.1f}% of IT budget (>30%): −20 pts",
                })
            elif (
                concentration.percentage_of_it_budget and concentration.percentage_of_it_budget > 20
            ):
                score -= 10
                evidence.append({
                    "factor": "Vendor IT Budget Concentration",
                    "weight": None,
                    "raw_value": concentration.percentage_of_it_budget,
                    "contribution": -10,
                    "source": "VendorConcentrationAnalysis.percentage_of_it_budget",
                    "rationale": f"Vendor accounts for {concentration.percentage_of_it_budget:.1f}% of IT budget (20–30%): −10 pts",
                })
            elif (
                concentration.percentage_of_it_budget and concentration.percentage_of_it_budget > 10
            ):
                score -= 5
                evidence.append({
                    "factor": "Vendor IT Budget Concentration",
                    "weight": None,
                    "raw_value": concentration.percentage_of_it_budget,
                    "contribution": -5,
                    "source": "VendorConcentrationAnalysis.percentage_of_it_budget",
                    "rationale": f"Vendor accounts for {concentration.percentage_of_it_budget:.1f}% of IT budget (10–20%): −5 pts",
                })

            # Alternative vendors available = lower risk
            if concentration.alternative_vendor_count and concentration.alternative_vendor_count > 3:
                score += 15
                evidence.append({
                    "factor": "Alternative Vendors Available",
                    "weight": None,
                    "raw_value": concentration.alternative_vendor_count,
                    "contribution": 15,
                    "source": "VendorConcentrationAnalysis.alternative_vendor_count",
                    "rationale": f"{concentration.alternative_vendor_count} alternatives available (>3): +15 pts",
                })
            elif concentration.alternative_vendor_count and concentration.alternative_vendor_count > 1:
                score += 10
                evidence.append({
                    "factor": "Alternative Vendors Available",
                    "weight": None,
                    "raw_value": concentration.alternative_vendor_count,
                    "contribution": 10,
                    "source": "VendorConcentrationAnalysis.alternative_vendor_count",
                    "rationale": f"{concentration.alternative_vendor_count} alternatives available (2–3): +10 pts",
                })

        # Product maturity (VendorProduct uses product_maturity, not lifecycle_status)
        product_maturity = product.product_maturity
        if product_maturity:
            maturity_scores = {
                "growth": 10, "mature": 5, "emerging": 0, "declining": -15,
            }
            mat_delta = maturity_scores.get(product_maturity, 0)
            score += mat_delta
            if mat_delta != 0:
                evidence.append({
                    "factor": "Product Maturity",
                    "weight": None,
                    "raw_value": product_maturity,
                    "contribution": mat_delta,
                    "source": "VendorProduct.product_maturity",
                    "rationale": f"Product maturity '{product_maturity}' adds {mat_delta:+d} pts",
                })

        # End-of-life check
        eol_date = product.end_of_life_date
        if eol_date and eol_date < datetime.utcnow() + timedelta(days=365):
            score -= 25  # Product approaching or past end-of-life
            evidence.append({
                "factor": "Product End-of-Life",
                "weight": None,
                "raw_value": str(eol_date),
                "contribution": -25,
                "source": "VendorProduct.end_of_life_date",
                "rationale": f"Product end-of-life {eol_date} is within 12 months: −25 pts",
            })

        # --- New scoring factors: wire remaining vendor model fields ---

        # Factor 9: Innovation score (VendorOrganization)
        innovation_score = getattr(vendor, "innovation_score", None)  # model-safety-ok
        if innovation_score is not None:
            if innovation_score >= 70:
                score += 5
                innov_delta = 5
            elif innovation_score <= 30:
                score -= 10
                innov_delta = -10
            else:
                innov_delta = 0
            if innov_delta != 0:
                evidence.append({
                    "factor": "Vendor Innovation Score",
                    "weight": None,
                    "raw_value": innovation_score,
                    "contribution": innov_delta,
                    "source": "VendorOrganization.innovation_score",
                    "rationale": f"Innovation score {innovation_score} adds {innov_delta:+d} pts",
                })

        # Factor 10: Acquisition risk (VendorOrganization)
        acquisition_risk = getattr(vendor, "acquisition_risk", None)  # model-safety-ok
        if acquisition_risk is not None:
            acq_lower = str(acquisition_risk).lower()
            if acq_lower == "high":
                score -= 15
                acq_delta = -15
            elif acq_lower == "medium":
                score -= 5
                acq_delta = -5
            elif acq_lower == "low":
                score += 5
                acq_delta = 5
            else:
                acq_delta = 0
            if acq_delta != 0:
                evidence.append({
                    "factor": "Vendor Acquisition Risk",
                    "weight": None,
                    "raw_value": acquisition_risk,
                    "contribution": acq_delta,
                    "source": "VendorOrganization.acquisition_risk",
                    "rationale": f"Acquisition risk '{acquisition_risk}' adds {acq_delta:+d} pts",
                })

        # Factor 11: Vendor lock-in risk (VendorOrganization, 1-10 scale)
        vendor_lock_in_risk = getattr(vendor, "vendor_lock_in_risk", None)  # model-safety-ok
        if vendor_lock_in_risk is not None:
            if vendor_lock_in_risk >= 8:
                score -= 10
                lockin_delta = -10
            elif vendor_lock_in_risk >= 5:
                score -= 5
                lockin_delta = -5
            elif vendor_lock_in_risk <= 2:
                score += 5
                lockin_delta = 5
            else:
                lockin_delta = 0
            if lockin_delta != 0:
                evidence.append({
                    "factor": "Vendor Lock-in Risk",
                    "weight": None,
                    "raw_value": vendor_lock_in_risk,
                    "contribution": lockin_delta,
                    "source": "VendorOrganization.vendor_lock_in_risk",
                    "rationale": f"Vendor lock-in risk score {vendor_lock_in_risk}/10 adds {lockin_delta:+d} pts",
                })

        # Factor 12: End-of-support date (VendorProduct, separate from end-of-life)
        eos_date = getattr(product, "end_of_support_date", None)  # model-safety-ok
        if eos_date is not None:
            days_to_eos = (eos_date - datetime.utcnow()).days
            if days_to_eos < 365:
                score -= 15
                eos_delta = -15
                eos_label = f"within 12 months ({days_to_eos}d)"
            elif days_to_eos < 730:
                score -= 5
                eos_delta = -5
                eos_label = f"within 24 months ({days_to_eos}d)"
            else:
                eos_delta = 0
                eos_label = None
            if eos_delta != 0:
                evidence.append({
                    "factor": "End-of-Support Date",
                    "weight": None,
                    "raw_value": str(eos_date),
                    "contribution": eos_delta,
                    "source": "VendorProduct.end_of_support_date",
                    "rationale": f"End-of-support date {eos_label}: {eos_delta:+d} pts",
                })

        # Factor 13: Compliance standards (VendorProduct, JSON text)
        compliance_raw = getattr(product, "compliance_standards", None)  # model-safety-ok
        if compliance_raw:
            recognized = {"SOC2", "ISO27001", "GDPR", "HIPAA", "FedRAMP"}
            try:
                standards = json.loads(compliance_raw) if isinstance(compliance_raw, str) else compliance_raw
                if isinstance(standards, list) and any(s in recognized for s in standards):
                    score += 5
                    comp_delta = 5
                    comp_label = "recognized standard(s) present"
                else:
                    score -= 5
                    comp_delta = -5
                    comp_label = "no recognized compliance standards"
            except (json.JSONDecodeError, TypeError):
                score -= 5
                comp_delta = -5
                comp_label = "compliance_standards field could not be parsed"
            evidence.append({
                "factor": "Compliance Standards",
                "weight": None,
                "raw_value": comp_label,
                "contribution": comp_delta,
                "source": "VendorProduct.compliance_standards",
                "rationale": f"Compliance check ({comp_label}) adds {comp_delta:+d} pts",
            })
        elif compliance_raw is not None:
            # Empty string
            score -= 5
            evidence.append({
                "factor": "Compliance Standards",
                "weight": None,
                "raw_value": "",
                "contribution": -5,
                "source": "VendorProduct.compliance_standards",
                "rationale": "Compliance standards field is empty: −5 pts",
            })

        # Factor 14: Single point of failure (VendorConcentrationAnalysis)
        if concentration:
            spof = getattr(concentration, "is_single_point_of_failure", None)  # model-safety-ok
            if spof is True:
                score -= 15
                evidence.append({
                    "factor": "Single Point of Failure",
                    "weight": None,
                    "raw_value": True,
                    "contribution": -15,
                    "source": "VendorConcentrationAnalysis.is_single_point_of_failure",
                    "rationale": "Vendor is a single point of failure in the portfolio: −15 pts",
                })

            # Factor 15: Contract exit complexity
            exit_cx = getattr(concentration, "contract_exit_complexity", None)  # model-safety-ok
            if exit_cx is not None:
                cx_lower = str(exit_cx).lower()
                if cx_lower == "infeasible":
                    score -= 10
                    cx_delta = -10
                elif cx_lower == "complex":
                    score -= 5
                    cx_delta = -5
                else:
                    cx_delta = 0
                if cx_delta != 0:
                    evidence.append({
                        "factor": "Contract Exit Complexity",
                        "weight": None,
                        "raw_value": exit_cx,
                        "contribution": cx_delta,
                        "source": "VendorConcentrationAnalysis.contract_exit_complexity",
                        "rationale": f"Contract exit complexity '{exit_cx}' adds {cx_delta:+d} pts",
                    })

            # Factor 16: Data portability score (0-100)
            dp_score = getattr(concentration, "data_portability_score", None)  # model-safety-ok
            if dp_score is not None:
                if dp_score < 30:
                    score -= 10
                    dp_delta = -10
                    dp_label = f"score {dp_score} (low portability)"
                elif dp_score >= 70:
                    score += 5
                    dp_delta = 5
                    dp_label = f"score {dp_score} (high portability)"
                else:
                    dp_delta = 0
                    dp_label = None
                if dp_delta != 0:
                    evidence.append({
                        "factor": "Data Portability",
                        "weight": None,
                        "raw_value": dp_score,
                        "contribution": dp_delta,
                        "source": "VendorConcentrationAnalysis.data_portability_score",
                        "rationale": f"Data portability {dp_label} adds {dp_delta:+d} pts",
                    })

            # Factor 17: Estimated switching cost (Decimal)
            switching_cost = getattr(concentration, "estimated_switching_cost", None)  # model-safety-ok
            if switching_cost is not None:
                try:
                    cost_val = float(switching_cost)
                    if cost_val > 1_000_000:
                        score -= 10
                        sc_delta = -10
                        sc_label = f"£{cost_val:,.0f} (>£1M)"
                    elif cost_val > 500_000:
                        score -= 5
                        sc_delta = -5
                        sc_label = f"£{cost_val:,.0f} (>£500k)"
                    else:
                        sc_delta = 0
                        sc_label = None
                    if sc_delta != 0:
                        evidence.append({
                            "factor": "Estimated Switching Cost",
                            "weight": None,
                            "raw_value": cost_val,
                            "contribution": sc_delta,
                            "source": "VendorConcentrationAnalysis.estimated_switching_cost",
                            "rationale": f"Switching cost {sc_label} adds {sc_delta:+d} pts",
                        })
                except (ValueError, TypeError):
                    pass

        # --- Build output fields ---
        viability_parts = [v for v in [enterprise_score, financial_score, innovation_score] if v is not None]
        vendor_viability = int(sum(viability_parts) / len(viability_parts)) if viability_parts else None

        return {
            "score": max(0, min(score, 100)),
            "vendor_lock_in_level": vendor_lock_in_risk,
            "vendor_viability_score": vendor_viability,
            "exit_complexity": getattr(concentration, "contract_exit_complexity", None) if concentration else None,
            "exit_cost_estimate": getattr(concentration, "estimated_switching_cost", None) if concentration else None,
            "alternative_vendors_available": getattr(concentration, "alternative_vendor_count", None) if concentration else None,
            "evidence": evidence,
        }

    @staticmethod
    def _derive_disposition(
        time_action: str,
        overall_score: float,
        technical_score: float,
        business_score: float,
        cost_score: float,
        app: ApplicationComponent,
    ) -> tuple[DispositionAction, str, list[str]]:
        """
        Derive the canonical 7R DispositionAction, confidence level, and uncertainty reasons.

        The TIME framework (TOLERATE/INVEST/MIGRATE/ELIMINATE) drives the scoring model.
        DispositionAction is the practitioner-facing vocabulary communicated to business
        stakeholders.  This method maps from TIME output to the most specific 7R action
        by examining secondary signals (sibling overlap, replatform indicators, etc.).

        ELIMINATE is refined into:
        - CONSOLIDATE  when capability-sibling overlap is detected (>1 sibling app in the
          same capability bucket) — retire by merging, not by decommissioning independently.
        - RETIRE        when no meaningful sibling overlap exists.

        MIGRATE is refined into:
        - REHOST       when the app is on-premise/mainframe and has low integration
          complexity — a lift-and-shift is the most cost-effective path.
        - REPLATFORM   when the app needs modest modification (e.g. config-driven cloud
          migration) but doesn't require re-architecture.
        - REPLACE      (default for MIGRATE) — major technical replacement needed.

        INVEST maps to REFACTOR (re-architect for strategic fit).
        TOLERATE maps to RETAIN.

        Confidence is:
        - "high"   — overall_score is clearly in the action's zone (>15 pts from threshold)
                     AND at least one secondary signal corroborates.
        - "medium" — primary signal clear but secondary signals are neutral or absent.
        - "low"    — overall_score is within 10 pts of a threshold, or input data is sparse.

        Uncertainty reasons explain why confidence is not "high" and are suitable for
        display to EA practitioners as actionable data-quality guidance.

        Args:
            time_action: TIME label string (TOLERATE, INVEST, MIGRATE, ELIMINATE).
            overall_score: Weighted overall health score (0-100).
            technical_score: Technical health dimension score (0-100).
            business_score: Business value dimension score (0-100).
            cost_score: Cost efficiency dimension score (0-100).
            app: ApplicationComponent instance (used for structured field inspection).

        Returns:
            Tuple of (DispositionAction, confidence_string, uncertainty_reasons).
            uncertainty_reasons is an empty list when confidence is "high".
        """
        # --- Confidence: measure distance from decision thresholds ---
        # Thresholds: ELIMINATE < 40, INVEST > 70 biz + > 50 tech, MIGRATE < 40 tech + > 50 biz
        threshold_distances = []
        if time_action == "ELIMINATE":
            threshold_distances.append(40.0 - overall_score)  # positive = clearly below
        elif time_action == "INVEST":
            threshold_distances.append(business_score - 70.0)
            threshold_distances.append(technical_score - 50.0)
        elif time_action == "MIGRATE":
            threshold_distances.append(40.0 - technical_score)
            threshold_distances.append(business_score - 50.0)
        elif time_action == "TOLERATE":
            # TOLERATE = everything else; distance to nearest boundary
            threshold_distances.append(overall_score - 40.0)

        min_distance = min(threshold_distances) if threshold_distances else 0.0

        # Sparse data penalty (fewer structured fields = less evidence)
        has_structured_data = any([
            getattr(app, "deployment_model", None),  # model-safety-ok
            getattr(app, "architecture_style", None),  # model-safety-ok
            getattr(app, "technology_age_years", None),  # model-safety-ok
            getattr(app, "strategic_importance", None),  # model-safety-ok
        ])

        if min_distance > 15 and has_structured_data:
            base_confidence = "high"
        elif min_distance > 5 or has_structured_data:
            base_confidence = "medium"
        else:
            base_confidence = "low"

        # --- Uncertainty reasons (only collected when confidence is not "high") ---
        # Each reason is a plain-English string that explains a specific data gap or
        # boundary condition so EA practitioners can take targeted remediation actions.
        uncertainty_reasons: list[str] = []

        if base_confidence != "high":
            # Threshold proximity
            if min_distance <= 5:
                uncertainty_reasons.append("Score near threshold boundary")

            # Cost dimension gap — TCO analysis requires at least one cost field
            # TODO(Release 2 Cost Fact consolidation): Use application_cost_accessor.get_annual_cost()
            # and related accessors instead of direct model field access.
            cost_fields = [
                app.total_cost_of_ownership,  # model-safety-ok (known field)
                app.license_cost,  # model-safety-ok
                app.maintenance_cost,  # model-safety-ok
                app.infrastructure_cost,  # model-safety-ok
            ]
            has_cost_data = any(v is not None and float(v) > 0 for v in cost_fields)
            if not has_cost_data:
                uncertainty_reasons.append("No cost data for TCO analysis")

            # Vendor data gap — accept structured vendor_product_id, legacy vendor_name, or junction table
            has_vendor_data = bool(
                getattr(app, "vendor_product_id", None)  # model-safety-ok
                or getattr(app, "vendor_name", None)     # model-safety-ok: Abacus-populated text field
                or getattr(app, "vendor_products", None) # model-safety-ok: M:M junction (eager-loaded in portfolio scoring)
            )
            if not has_vendor_data:
                uncertainty_reasons.append("Limited vendor data available")

            # Dependency data gap
            has_dep_count = bool(
                getattr(app, "dependencies_count", None)  # model-safety-ok
                and app.dependencies_count > 0
            )
            has_dep_records = False
            try:
                has_dep_records = bool(app.dependencies_out)
            except Exception as exc:
                logger.debug("Could not check dependencies_out for app %s: %s", app.id, exc)
            if not (has_dep_count or has_dep_records):
                uncertainty_reasons.append("No dependency data available")

            # Lifecycle gap
            lifecycle_val = getattr(app, "lifecycle_status", None)  # model-safety-ok
            if not lifecycle_val or not str(lifecycle_val).strip():
                uncertainty_reasons.append("Missing lifecycle status")

        # --- Disposition refinement ---

        if time_action == "TOLERATE":
            return DispositionAction.RETAIN, base_confidence, uncertainty_reasons

        if time_action == "INVEST":
            return DispositionAction.REFACTOR, base_confidence, uncertainty_reasons

        if time_action == "ELIMINATE":
            # Detect sibling overlap: other apps in the same capability bucket
            sibling_count = 0
            try:
                from app.models.application_capability import ApplicationCapabilityMapping
                cap_mappings = app.capability_mappings if app.capability_mappings else []
                if cap_mappings:
                    cap_ids = [m.business_capability_id for m in cap_mappings]
                    sibling_count = (
                        # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
                        ApplicationCapabilityMapping.query.filter(
                            ApplicationCapabilityMapping.business_capability_id.in_(cap_ids),
                            ApplicationCapabilityMapping.application_component_id != app.id,
                        )
                        .distinct(ApplicationCapabilityMapping.application_component_id)
                        .count()
                    )
            except Exception:
                sibling_count = 0

            if sibling_count > 0:
                # Retire by consolidating with a sibling, not by independent decommission
                return DispositionAction.CONSOLIDATE, base_confidence, uncertainty_reasons
            return DispositionAction.RETIRE, base_confidence, uncertainty_reasons

        if time_action == "MIGRATE":
            deploy = (getattr(app, "deployment_model", None) or "").lower()  # model-safety-ok
            arch = (getattr(app, "architecture_style", None) or "").lower()  # model-safety-ok
            integration = (getattr(app, "integration_complexity", None) or "").lower()  # model-safety-ok
            age = getattr(app, "technology_age_years", None)  # model-safety-ok

            # REHOST: on-prem/mainframe with low coupling → pure lift-and-shift
            if deploy in ("on_premise", "mainframe") and integration in ("low", ""):
                return DispositionAction.REHOST, base_confidence, uncertainty_reasons

            # REPLATFORM: already partially cloud-aware or service-oriented, needs modest
            # changes but not a full rebuild (e.g. monolith to managed PaaS)
            if arch in ("service_oriented", "monolithic") and deploy not in ("mainframe",):
                if age is not None and age <= 10:
                    return DispositionAction.REPLATFORM, base_confidence, uncertainty_reasons

            # Default MIGRATE → REPLACE
            return DispositionAction.REPLACE, base_confidence, uncertainty_reasons

        # Fallback: use TIME_TO_DISPOSITION mapping for any unrecognised TIME label
        mapped = TIME_TO_DISPOSITION.get(time_action.upper(), DispositionAction.RETAIN)
        return mapped, "low", ["Score derived from unrecognised TIME label — manual review required"]

    @staticmethod
    def _determine_time_action(
        overall_score: float,
        technical_score: float,
        business_score: float,
        cost_score: float,
        app: ApplicationComponent,
    ) -> str:
        """
        Determine TIME framework action based on scores.

        Decision matrix:
        - ELIMINATE: Low overall (<40), low business value, high cost
        - MIGRATE: Low technical (<40), but moderate-high business value
        - INVEST: High business value (>70), moderate+ technical health
        - TOLERATE: Default for acceptable but not strategic apps

        IMPORTANT: ELIMINATE is blocked or downgraded if:
        - App has critical dependencies (other apps depend on it)
        - App has `blocks_retirement=True` dependencies
        """
        # ELIMINATE: Low value, high cost, or already retiring
        if app.lifecycle_status in ["deprecated", "retired"]:
            return "ELIMINATE"

        # Check if app would qualify for ELIMINATE based on scores
        qualifies_for_eliminate = overall_score < 40 or (business_score < 40 and cost_score < 40)

        if qualifies_for_eliminate:
            # Check for blocking dependencies before recommending ELIMINATE
            blockers = RationalizationScoringService.get_retirement_blockers(app.id)

            if blockers.get("has_critical_blockers"):
                # Critical dependencies exist — app cannot be retired cleanly.
                # Return MIGRATE (TIME label) which maps to REPLACE in the canonical
                # disposition taxonomy: find a replacement while keeping this app alive.
                logger.info(
                    f"App {app.id} ({app.name}) qualifies for ELIMINATE but has "
                    f"{blockers.get('critical_count', 0)} critical dependencies. "
                    f"Downgrading to MIGRATE (canonical: REPLACE)."
                )
                return "MIGRATE"
            elif blockers.get("has_blockers"):
                # Non-critical blockers — ELIMINATE still appropriate; disposition
                # service will evaluate whether RETIRE or CONSOLIDATE is best.
                logger.info(
                    f"App {app.id} ({app.name}) recommended for ELIMINATE with "
                    f"{blockers.get('total_count', 0)} dependent applications."
                )
                return "ELIMINATE"
            else:
                return "ELIMINATE"

        # INVEST: High business value with decent technical foundation
        if business_score > 70 and technical_score > 50:
            return "INVEST"

        # MIGRATE: Technical debt but business value justifies replacement
        if technical_score < 40 and business_score > 50:
            return "MIGRATE"

        # INVEST: Strategic importance overrides other factors
        if app.strategic_importance in ("critical", "high"):
            return "INVEST"

        # TOLERATE: Everything else (good enough but not strategic)
        return "TOLERATE"

    @staticmethod
    def _generate_justification(
        time_action: str,
        technical_score: float,
        business_score: float,
        cost_score: float,
        vendor_score: float,
        app: ApplicationComponent,
        config_name: str = "Default",
    ) -> str:
        """Generate human-readable justification for TIME recommendation."""
        justifications = []

        # Configuration reference
        justifications.append(f"Scored using {config_name} configuration.")

        # Technical justification
        if technical_score > 70:
            justifications.append("Strong technical health and modern architecture.")
        elif technical_score < 40:
            justifications.append("Significant technical debt or legacy technology stack.")

        # Business value justification
        if business_score > 70:
            justifications.append("High strategic business value and critical process support.")
        elif business_score < 40:
            justifications.append("Limited business value or redundant functionality.")

        # Cost justification
        if cost_score > 70:
            justifications.append("Excellent cost efficiency and TCO optimization.")
        elif cost_score < 40:
            justifications.append("High cost burden relative to value delivered.")

        # Vendor risk justification
        if vendor_score < 40:
            justifications.append("Elevated vendor concentration or sustainability risk.")

        # Action-specific recommendations
        action_recommendations = {
            "TOLERATE": "Maintain current state with minimal investment. Monitor for changes.",
            "INVEST": "Strategic investment recommended to maximize business value and capabilities.",
            "MIGRATE": "Plan migration to modern platform to address technical debt while preserving business value.",
            "ELIMINATE": "Candidate for retirement or consolidation. Assess replacement or capability transfer options.",
        }

        justifications.append(action_recommendations.get(time_action, ""))

        return " ".join(justifications)

    @staticmethod
    def calculate_portfolio_scores(force_recalculate: bool = False) -> Dict:
        """
        Calculate rationalization scores for entire application portfolio.

        Args:
            force_recalculate: Recalculate even if recent scores exist

        Returns:
            Summary statistics and processing results
        """
        try:
            # Get all active applications — RATA-001: include Abacus lifecycle codes
            # Production data uses codes like "2.1 STRATEGIC", "5. DECOMMISSIONED"
            scoreable = RationalizationScoringService.SCOREABLE_LIFECYCLE_VALUES
            apps = ApplicationComponent.query.options(
                joinedload(ApplicationComponent.vendor_products)
            ).filter(
                db.or_(
                    ApplicationComponent.lifecycle_status.in_(scoreable),
                    db.and_(
                        ApplicationComponent.lifecycle_status.is_(None),
                        ApplicationComponent.deployment_status.isnot(None),
                    ),
                )
            ).all()

            results = {
                "total_apps": len(apps),
                "processed": 0,
                "errors": 0,
                "time_distribution": {"TOLERATE": 0, "INVEST": 0, "MIGRATE": 0, "ELIMINATE": 0},
                "average_scores": {
                    "overall": 0,
                    "technical": 0,
                    "business": 0,
                    "cost": 0,
                    "vendor": 0,
                },
            }

            scores_list = []

            # Batch prefetch recent scores for all apps to avoid N+1
            recent_scores_map = {}
            if not force_recalculate and apps:
                app_ids = [a.id for a in apps]
                recent_threshold = datetime.utcnow().date() - timedelta(days=30)
                recent_scores = ApplicationRationalizationScore.query.filter(
                    ApplicationRationalizationScore.application_component_id.in_(app_ids),
                    ApplicationRationalizationScore.assessment_date > recent_threshold,
                ).all()
                for s in recent_scores:
                    # Keep the most recent score per app (first one encountered)
                    if s.application_component_id not in recent_scores_map:
                        recent_scores_map[s.application_component_id] = s

            for app in apps:
                try:
                    # Check if recent score exists (within 30 days)
                    if not force_recalculate:
                        existing_score = recent_scores_map.get(app.id)

                        if existing_score:
                            scores_list.append(existing_score)
                            action = existing_score.rationalization_action or "TOLERATE"
                            if action in results["time_distribution"]:
                                results["time_distribution"][action] += 1
                            results["processed"] += 1
                            continue

                    # Calculate new score (pass pre-loaded app to avoid N+1 re-fetch)
                    score = RationalizationScoringService.calculate_app_score(app.id, app=app)

                    if score:
                        scores_list.append(score)
                        action = score.rationalization_action or "TOLERATE"
                        if action in results["time_distribution"]:
                            results["time_distribution"][action] += 1
                        results["processed"] += 1
                    else:
                        results["errors"] += 1

                except Exception as e:
                    logger.error(f"Error processing app {app.id}: {e}")
                    results["errors"] += 1

            # Calculate averages
            if scores_list:
                results["average_scores"]["overall"] = round(
                    sum(s.overall_health_score for s in scores_list) / len(scores_list), 2
                )
                results["average_scores"]["technical"] = round(
                    sum(s.technical_health_score for s in scores_list) / len(scores_list), 2
                )
                results["average_scores"]["business"] = round(
                    sum(s.business_value_score for s in scores_list) / len(scores_list), 2
                )
                results["average_scores"]["cost"] = round(
                    sum(s.cost_efficiency_score for s in scores_list) / len(scores_list), 2
                )
                results["average_scores"]["vendor"] = round(
                    sum(s.vendor_risk_score for s in scores_list) / len(scores_list), 2
                )

            db.session.commit()

            logger.info(
                f"Portfolio scoring complete: {results['processed']}/{results['total_apps']} processed, "
                f"{results['errors']} errors"
            )

            results["success"] = True
            return results

        except Exception as e:
            logger.error(f"Error calculating portfolio scores: {e}", exc_info=True)
            db.session.rollback()
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_elimination_candidates(min_score_threshold: float = 40, limit: int = 20) -> List[Dict]:
        """
        Get top candidates for elimination based on rationalization scores.

        Args:
            min_score_threshold: Maximum overall score for candidates (default 40)
            limit: Maximum number of results

        Returns:
            List of application summaries with rationalization data
        """
        try:
            candidates = (
                db.session.query(ApplicationRationalizationScore, ApplicationComponent)
                .join(
                    ApplicationComponent,
                    ApplicationRationalizationScore.application_component_id
                    == ApplicationComponent.id,
                )
                .filter(
                    or_(
                        ApplicationRationalizationScore.rationalization_action == "ELIMINATE",
                        ApplicationRationalizationScore.overall_health_score < min_score_threshold,
                    ),
                    # RATA-001: include Abacus lifecycle codes for active apps
                    ApplicationComponent.lifecycle_status.in_(
                        RationalizationScoringService.SCOREABLE_LIFECYCLE_VALUES
                    ),
                )
                .order_by(ApplicationRationalizationScore.overall_health_score.asc())
                .limit(limit)
                .all()
            )

            results = []
            for score, app in candidates:
                results.append(
                    {
                        "id": app.id,
                        "application_id": app.id,
                        "name": app.name,
                        "application_name": app.name,
                        "overall_score": score.overall_health_score,
                        "time_action": score.rationalization_action,
                        # Canonical 7R disposition — may be None for scores calculated
                        # before RAT-103 was deployed; callers should handle None gracefully.
                        "disposition_action": score.disposition_action,
                        "disposition_confidence": score.disposition_confidence,
                        "confidence_reasons": score.confidence_reasons or [],
                        "technical_score": score.technical_health_score,
                        "business_score": score.business_value_score,
                        "cost_score": score.cost_efficiency_score,
                        "vendor_score": score.vendor_risk_score,
                        "vendor_lock_in_level": score.vendor_lock_in_level,
                        "vendor_viability_score": score.vendor_viability_score,
                        "exit_complexity": score.exit_complexity,
                        "exit_cost_estimate": float(score.exit_cost_estimate) if score.exit_cost_estimate is not None else None,
                        "alternative_vendors_available": score.alternative_vendors_available,
                        "justification": score.action_rationale,
                        "criticality": app.business_criticality,
                        "lifecycle_status": app.lifecycle_status,
                    }
                )

            return results

        except Exception as e:
            logger.error(f"Error getting elimination candidates: {e}", exc_info=True)
            return []

    @staticmethod
    def get_retirement_blockers(app_id: int) -> Dict:
        """
        Get applications that depend on this app and would block retirement.

        Returns a dict with:
        - has_blockers: bool - True if any dependencies exist
        - has_critical_blockers: bool - True if critical/blocking dependencies exist
        - total_count: int - Total number of dependent applications
        - critical_count: int - Number with blocks_retirement=True or critical dependency_strength
        - blockers: List of blocker details with app info and dependency type

        Args:
            app_id: Application component ID to check

        Returns:
            Dictionary with blocker analysis
        """
        try:
            # Find all apps that depend ON this app (this app is the target)
            dependencies = (
                ApplicationDependency.query.filter(
                    ApplicationDependency.target_app_id == app_id,
                    ApplicationDependency.status == "active",
                )
                .options(joinedload(ApplicationDependency.source_app))
                .all()
            )

            if not dependencies:
                return {
                    "has_blockers": False,
                    "has_critical_blockers": False,
                    "total_count": 0,
                    "critical_count": 0,
                    "blockers": [],
                }

            blockers = []
            critical_count = 0

            for dep in dependencies:
                is_critical = (
                    dep.blocks_retirement
                    or dep.dependency_strength in ["critical", "high"]
                    or dep.business_criticality in ["mission_critical", "business_critical"]
                )

                if is_critical:
                    critical_count += 1

                source_app = dep.source_app
                blockers.append(
                    {
                        "dependency_id": dep.id,
                        "source_app_id": dep.source_app_id,
                        "source_app_name": source_app.name if source_app else "Unknown",
                        "source_app_lifecycle": source_app.lifecycle_status if source_app else None,
                        "dependency_type": dep.dependency_type,
                        "dependency_strength": dep.dependency_strength,
                        "blocks_retirement": dep.blocks_retirement,
                        "business_criticality": dep.business_criticality,
                        "is_critical": is_critical,
                        "decoupling_complexity": dep.decoupling_complexity,
                        "decoupling_cost_estimate": float(dep.decoupling_cost_estimate)
                        if dep.decoupling_cost_estimate
                        else None,
                    }
                )

            # Sort: critical blockers first
            blockers.sort(key=lambda x: (not x["is_critical"], x["source_app_name"]))

            return {
                "has_blockers": len(blockers) > 0,
                "has_critical_blockers": critical_count > 0,
                "total_count": len(blockers),
                "critical_count": critical_count,
                "blockers": blockers,
            }

        except Exception as e:
            logger.error(f"Error getting retirement blockers for app {app_id}: {e}", exc_info=True)
            return {
                "has_blockers": False,
                "has_critical_blockers": False,
                "total_count": 0,
                "critical_count": 0,
                "blockers": [],
                "error": str(e),
            }

    @staticmethod
    def get_blast_radius(app_id: int, depth: int = 3) -> Dict:
        """
        Get the full impact cascade of retiring an application.

        Performs recursive dependency traversal to find all applications
        that would be affected (directly or indirectly) by retiring this app.

        Args:
            app_id: Application component ID to analyze
            depth: Maximum depth of dependency traversal (default 3)

        Returns:
            Dictionary with:
            - target_app: Info about the app being analyzed
            - direct_dependents: Apps that directly depend on this app
            - indirect_dependents: Apps that depend on dependents (by level)
            - total_affected_count: Total unique apps affected
            - critical_path_count: Count of critical dependencies in cascade
            - estimated_total_decoupling_cost: Sum of decoupling costs
            - risk_level: low/medium/high/critical based on impact
        """
        try:
            app = ApplicationComponent.query.get(app_id)
            if not app:
                return {"error": f"Application {app_id} not found"}

            visited = set()
            visited.add(app_id)  # Don't include the target app itself

            all_dependents = []
            levels = {}  # app_id -> level
            critical_count = 0
            total_decoupling_cost = 0.0

            def traverse(current_app_id: int, current_depth: int):
                nonlocal critical_count, total_decoupling_cost

                if current_depth > depth:
                    return

                # Find apps that depend on current_app_id (eagerly load source_app to avoid N+1)
                dependencies = (
                    ApplicationDependency.query.filter(
                        ApplicationDependency.target_app_id == current_app_id,
                        ApplicationDependency.status == "active",
                    )
                    .options(joinedload(ApplicationDependency.source_app))
                    .all()
                )

                for dep in dependencies:
                    source_id = dep.source_app_id

                    if source_id in visited:
                        continue

                    visited.add(source_id)

                    source_app = dep.source_app
                    if not source_app:
                        continue

                    is_critical = dep.blocks_retirement or dep.dependency_strength in [
                        "critical",
                        "high",
                    ]

                    if is_critical:
                        critical_count += 1

                    if dep.decoupling_cost_estimate:
                        total_decoupling_cost += float(dep.decoupling_cost_estimate)

                    dependent_info = {
                        "app_id": source_id,
                        "app_name": source_app.name,
                        "lifecycle_status": source_app.lifecycle_status,
                        "business_criticality": source_app.business_criticality,
                        "dependency_type": dep.dependency_type,
                        "dependency_strength": dep.dependency_strength,
                        "is_critical": is_critical,
                        "level": current_depth,
                        "depends_on_app_id": current_app_id,
                    }

                    all_dependents.append(dependent_info)
                    levels[source_id] = current_depth

                    # Recurse to find indirect dependents
                    traverse(source_id, current_depth + 1)

            # Start traversal from the target app
            traverse(app_id, 1)

            # Organize by level
            direct_dependents = [d for d in all_dependents if d["level"] == 1]
            indirect_by_level = {}
            for d in all_dependents:
                if d["level"] > 1:
                    level_key = f"level_{d['level']}"
                    if level_key not in indirect_by_level:
                        indirect_by_level[level_key] = []
                    indirect_by_level[level_key].append(d)

            # Determine risk level
            total_affected = len(all_dependents)
            if total_affected == 0:
                risk_level = "low"
            elif critical_count >= 3 or total_affected >= 10:
                risk_level = "critical"
            elif critical_count >= 1 or total_affected >= 5:
                risk_level = "high"
            elif total_affected >= 2:
                risk_level = "medium"
            else:
                risk_level = "low"

            return {
                "success": True,
                "target_app": {
                    "id": app.id,
                    "name": app.name,
                    "lifecycle_status": app.lifecycle_status,
                    "business_criticality": app.business_criticality,
                },
                "direct_dependents": direct_dependents,
                "direct_dependent_count": len(direct_dependents),
                "indirect_dependents": indirect_by_level,
                "total_affected_count": total_affected,
                "critical_path_count": critical_count,
                "estimated_total_decoupling_cost": round(total_decoupling_cost, 2),
                "risk_level": risk_level,
                "max_depth_analyzed": depth,
            }

        except Exception as e:
            logger.error(f"Error calculating blast radius for app {app_id}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    def should_trigger_options_analysis(
        score: ApplicationRationalizationScore,
    ) -> bool:
        """
        Determine if options analysis should be triggered for this rationalization score.

        Options analysis is relevant for MIGRATE and INVEST actions where
        multiple vendor/platform options should be evaluated.

        Args:
            score: ApplicationRationalizationScore record

        Returns:
            True if options analysis should be invoked, False otherwise
        """
        return score.rationalization_action in ["MIGRATE", "INVEST"]

    @staticmethod
    def get_options_analysis_requirements(
        app: ApplicationComponent,
        score: ApplicationRationalizationScore,
    ) -> Dict[str, any]:
        """
        Build requirements dictionary for options analysis based on app and score.

        Extracts relevant application attributes and rationalization context
        to inform multi-criteria decision analysis.

        Args:
            app: ApplicationComponent to analyze
            score: ApplicationRationalizationScore with TIME framework recommendation

        Returns:
            Dictionary of requirements for OptionsAnalysisEngine
        """
        requirements = {
            "application_id": app.id,
            "application_name": app.name,
            "time_action": score.rationalization_action,
            "business_value_score": score.business_value_score,
            "technical_health_score": score.technical_health_score,
            "cost_efficiency_score": score.cost_efficiency_score,
            "vendor_risk_score": score.vendor_risk_score,
            "business_criticality": app.business_criticality,
            "lifecycle_status": app.lifecycle_status,
            "action_rationale": score.action_rationale,
        }

        # Add technical context for MIGRATE decisions
        if score.rationalization_action == "MIGRATE":
            requirements.update(
                {
                    "technical_debt_drivers": [
                        "Low technical health score",
                        "Technical debt accumulation",
                        "Platform obsolescence risk",
                    ],
                    "target_state": "Modern cloud-native platform",
                }
            )

        # Add investment context for INVEST decisions
        elif score.rationalization_action == "INVEST":
            requirements.update(
                {
                    "investment_objectives": [
                        "Maximize business value",
                        "Enhance capabilities",
                        "Strategic alignment",
                    ],
                    "target_state": "Enhanced platform with expanded capabilities",
                }
            )

        return requirements

    @staticmethod
    def compute_disposition_matrix(scope_app_ids: list) -> list:
        """
        Compute a disposition matrix for the given application IDs.

        For each app, derives:
        - business_value_score: capability coverage fraction (mapped_count / total_capabilities)
        - technical_debt_score: based on arch_pattern
        - replacement_cost_score: 0.3 if a VendorProduct is linked, 0.7 otherwise
        - disposition: Retire / Retain / Replace / Re-engineer / Review
        - net_value_score: business_value_score - (technical_debt_score * 0.5)

        Returns list sorted by net_value_score descending.
        """
        if not scope_app_ids:
            return []

        from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
        from sqlalchemy import func

        # technical_debt_score mapping by arch_pattern
        DEBT_MAP = {
            "legacy": 1.0,
            "monolith": 0.7,
            "modular_monolith": 0.7,
            "microservice": 0.3,
            "saas": 0.0,
            "api_gateway": 0.3,
            "unknown": 0.5,
        }

        # Total capabilities in the platform (denominator for coverage)
        total_capabilities = db.session.query(func.count()).select_from(
            db.session.query(UnifiedApplicationCapabilityMapping.unified_capability_id).distinct().subquery()
        ).scalar() or 1

        # Batch: count mappings per app in scope
        mapping_counts = dict(
            db.session.query(
                UnifiedApplicationCapabilityMapping.application_component_id,
                func.count(UnifiedApplicationCapabilityMapping.id),
            )
            .filter(UnifiedApplicationCapabilityMapping.application_component_id.in_(scope_app_ids))
            .group_by(UnifiedApplicationCapabilityMapping.application_component_id)
            .all()
        )

        # Batch: check vendor product linkage for apps in scope
        linked_app_ids = set(
            row[0]
            for row in db.session.query(application_vendor_products.c.application_component_id)
            .filter(application_vendor_products.c.application_component_id.in_(scope_app_ids))
            .distinct()
            .all()
        )

        # Load apps in scope
        apps = ApplicationComponent.query.filter(
            ApplicationComponent.id.in_(scope_app_ids)
        ).all()

        results = []
        for app in apps:
            # business_value_score: capability coverage fraction
            mapped_count = mapping_counts.get(app.id, 0)
            business_value_score = round(min(mapped_count / total_capabilities, 1.0), 4)

            # technical_debt_score: from arch_pattern
            pattern = (app.arch_pattern or "unknown").lower()
            technical_debt_score = DEBT_MAP.get(pattern, DEBT_MAP["unknown"])

            # replacement_cost_score: vendor product linked?
            replacement_cost_score = 0.3 if app.id in linked_app_ids else 0.7

            # disposition rule
            if business_value_score < 0.4 and technical_debt_score > 0.6:
                disposition = "Retire"
            elif business_value_score >= 0.6 and technical_debt_score < 0.4:
                disposition = "Retain"
            elif business_value_score < 0.4 and technical_debt_score < 0.4:
                disposition = "Replace"
            elif business_value_score >= 0.6 and technical_debt_score >= 0.6:
                disposition = "Re-engineer"
            else:
                disposition = "Review"

            net_value_score = round(business_value_score - (technical_debt_score * 0.5), 4)

            results.append({
                "app_id": app.id,
                "app_name": app.name,
                "business_value_score": business_value_score,
                "technical_debt_score": technical_debt_score,
                "replacement_cost_score": replacement_cost_score,
                "disposition": disposition,
                "net_value_score": net_value_score,
            })

        results.sort(key=lambda x: x["net_value_score"], reverse=True)
        return results

    @staticmethod
    def _determine_time_action_with_thresholds(
        overall_score: float,
        technical_score: float,
        business_score: float,
        cost_score: float,
        app: "ApplicationComponent",
        thresholds: dict,
    ) -> str:
        """
        Determine TIME framework action using caller-supplied threshold values.

        This method is used by ``calculate_app_score`` when a
        ``RationalizationPolicy`` is active and provides threshold overrides.
        It mirrors the logic of ``_determine_time_action`` but reads all
        threshold values from the supplied dict rather than hardcoded constants,
        so business-unit policies can shift classification boundaries without
        touching the core scoring logic.

        Args:
            overall_score:    Weighted overall health score (0–100).
            technical_score:  Technical health dimension score (0–100).
            business_score:   Business value dimension score (0–100).
            cost_score:       Cost efficiency dimension score (0–100).
            app:              ApplicationComponent instance (used for lifecycle
                              status and strategic importance checks).
            thresholds:       Dict produced by
                              ``RationalizationPolicy.get_effective_thresholds()``.
                              Expected keys: eliminate_below, invest_above,
                              invest_tech_min, migrate_tech_threshold,
                              migrate_business_threshold.

        Returns:
            TIME action string: ``"TOLERATE"``, ``"INVEST"``, ``"MIGRATE"``,
            or ``"ELIMINATE"``.
        """
        eliminate_below = thresholds.get("eliminate_below", 40)
        invest_above = thresholds.get("invest_above", 70)
        invest_tech_min = thresholds.get("invest_tech_min", 50)
        migrate_tech_threshold = thresholds.get("migrate_tech_threshold", 40)
        migrate_business_threshold = thresholds.get("migrate_business_threshold", 50)

        # ELIMINATE: deprecated/retired lifecycle always triggers elimination.
        if app.lifecycle_status in ["deprecated", "retired"]:
            return "ELIMINATE"

        # Determine whether elimination is score-driven.
        qualifies_for_eliminate = overall_score < eliminate_below or (
            business_score < eliminate_below and cost_score < eliminate_below
        )

        if qualifies_for_eliminate:
            blockers = RationalizationScoringService.get_retirement_blockers(app.id)
            if blockers.get("has_critical_blockers"):
                logger.info(
                    "App %s (%s) qualifies for ELIMINATE (policy thresholds) but has "
                    "%s critical dependencies. Downgrading to MIGRATE.",
                    app.id, app.name, blockers.get("critical_count", 0),
                )
                return "MIGRATE"
            return "ELIMINATE"

        # INVEST: high business value with sufficient technical foundation.
        if business_score > invest_above and technical_score > invest_tech_min:
            return "INVEST"

        # MIGRATE: technical debt but business value justifies replacement.
        if technical_score < migrate_tech_threshold and business_score > migrate_business_threshold:
            return "MIGRATE"

        # Strategic importance override (same as base logic).
        if app.strategic_importance in ("critical", "high"):
            return "INVEST"

        return "TOLERATE"

    @staticmethod
    def assess_retirement_blockers(app: "ApplicationComponent") -> Dict:
        """
        Authoritative blocker assessment across five categories before an application
        can be retired or replaced.

        Categories assessed:
        - Integrations: active upstream/downstream dependency edges
        - Users: active user population without a confirmed migration plan
        - Contracts: vendor contracts with >6 months remaining term
        - Compliance: system-of-record status or compliance-critical classification
        - Data Migration: large dataset footprint without a documented strategy

        Args:
            app: ApplicationComponent instance to assess.

        Returns:
            {
                "categories": [
                    {
                        "name": str,
                        "status": "blocked" | "warning" | "clear",
                        "count": int,
                        "details": str
                    },
                    ...
                ],
                "total_blockers": int,
                "is_retirement_safe": bool,
                "blocker_summary": str
            }
        """
        categories = []
        total_blockers = 0

        # ── Category 1: Integrations ──────────────────────────────────────────
        try:
            outbound = []
            inbound = []
            try:
                outbound = list(app.dependencies_out or [])
            except Exception:
                logger.debug("Could not access dependencies_out for app %s", app.id)  # model-safety-ok
            try:
                inbound = list(app.dependencies_in or [])
            except Exception:
                logger.debug("Could not access dependencies_in for app %s", app.id)  # model-safety-ok

            # Count only active dependencies
            active_out = [d for d in outbound if getattr(d, "status", "active") == "active"]  # model-safety-ok
            active_in = [d for d in inbound if getattr(d, "status", "active") == "active"]  # model-safety-ok

            critical_consumers = [
                d for d in active_in
                if getattr(d, "blocks_retirement", False)  # model-safety-ok
                or getattr(d, "dependency_strength", "") in ("critical", "high")  # model-safety-ok
            ]
            total_integrations = len(active_out) + len(active_in)
            critical_count = len(critical_consumers)

            if critical_count > 0:
                int_status = "blocked"
                total_blockers += 1
                int_details = (
                    f"{critical_count} critical downstream consumer(s) must be decoupled before retirement. "
                    f"Total active integrations: {total_integrations}."
                )
            elif total_integrations > 0:
                int_status = "warning"
                int_details = (
                    f"{total_integrations} active integration(s) exist. None are classified as critical, "
                    "but each must be reviewed and closed before retirement."
                )
            else:
                int_status = "clear"
                int_details = "No active integration dependencies recorded."

            categories.append({
                "name": "Integrations",
                "status": int_status,
                "count": total_integrations,
                "details": int_details,
            })
        except Exception as exc:
            logger.error("Integrations check failed for app %s: %s", app.id, exc, exc_info=True)  # model-safety-ok
            categories.append({
                "name": "Integrations",
                "status": "warning",
                "count": 0,
                "details": "Integration data could not be assessed — manual review required.",
            })

        # ── Category 2: Users ────────────────────────────────────────────────
        try:
            user_count = (
                getattr(app, "user_count", None)  # model-safety-ok
                or getattr(app, "user_base_size", None)  # model-safety-ok
                or getattr(app, "average_daily_users", None)  # model-safety-ok
                or 0
            )
            user_count = int(user_count or 0)

            # Check if a replacement/migration is planned
            planned_retirement = getattr(app, "planned_retirement_date", None)  # model-safety-ok
            lifecycle = getattr(app, "lifecycle_status", "") or ""  # model-safety-ok
            has_migration_signal = (
                planned_retirement is not None
                or lifecycle.lower() in ("deprecated", "migration")
            )

            if user_count > 100 and not has_migration_signal:
                usr_status = "blocked"
                total_blockers += 1
                usr_details = (
                    f"{user_count:,} active users recorded without a confirmed migration plan. "
                    "A user migration or transition plan must be in place before retirement."
                )
            elif user_count > 0:
                usr_status = "warning"
                usr_details = (
                    f"{user_count:,} user(s) recorded."
                    + (" A planned retirement date is set." if planned_retirement else " No migration plan detected — verify user impact.")
                )
            else:
                usr_status = "clear"
                usr_details = "No active users recorded against this application."

            categories.append({
                "name": "Users",
                "status": usr_status,
                "count": user_count,
                "details": usr_details,
            })
        except Exception as exc:
            logger.error("Users check failed for app %s: %s", app.id, exc, exc_info=True)  # model-safety-ok
            categories.append({
                "name": "Users",
                "status": "warning",
                "count": 0,
                "details": "User count data could not be assessed — manual review required.",
            })

        # ── Category 3: Contracts ─────────────────────────────────────────────
        try:
            from datetime import date as _date

            today = _date.today()
            six_months_ahead = today.replace(
                month=today.month + 6 if today.month <= 6 else today.month - 6,
                year=today.year if today.month <= 6 else today.year + 1,
            )

            active_contracts = []
            blocking_contracts = []

            # Check VendorContract rows linked to this application
            try:
                for vc in (app.vendor_contracts or []):
                    vc_status = getattr(vc, "status", "active") or "active"  # model-safety-ok
                    if vc_status not in ("active", "renewed"):
                        continue
                    end_date = getattr(vc, "end_date", None)  # model-safety-ok
                    active_contracts.append(vc)
                    if end_date is None or end_date > six_months_ahead:
                        blocking_contracts.append(vc)
            except Exception:
                logger.debug("Could not iterate vendor_contracts for app %s", app.id)  # model-safety-ok

            # Fallback: check legacy contract_expiry_date field on the app itself
            if not active_contracts:
                legacy_expiry = getattr(app, "contract_expiry_date", None)  # model-safety-ok
                contract_type_val = getattr(app, "contract_type", None)  # model-safety-ok
                if legacy_expiry and contract_type_val:
                    active_contracts.append(legacy_expiry)
                    if legacy_expiry > six_months_ahead:
                        blocking_contracts.append(legacy_expiry)

            if blocking_contracts:
                ctr_status = "blocked"
                total_blockers += 1
                ctr_details = (
                    f"{len(blocking_contracts)} active contract(s) with more than 6 months remaining. "
                    "Early termination fees or renegotiation required before retirement."
                )
            elif active_contracts:
                ctr_status = "warning"
                ctr_details = (
                    f"{len(active_contracts)} active contract(s) expiring within 6 months. "
                    "Verify no auto-renewal clauses will trigger."
                )
            else:
                ctr_status = "clear"
                ctr_details = "No active vendor contracts with blocking terms found."

            categories.append({
                "name": "Contracts",
                "status": ctr_status,
                "count": len(active_contracts),
                "details": ctr_details,
            })
        except Exception as exc:
            logger.error("Contracts check failed for app %s: %s", app.id, exc, exc_info=True)  # model-safety-ok
            categories.append({
                "name": "Contracts",
                "status": "warning",
                "count": 0,
                "details": "Contract data could not be assessed — manual review required.",
            })

        # ── Category 4: Compliance ────────────────────────────────────────────
        try:
            # Signals that indicate compliance-critical status
            data_classification = (getattr(app, "data_classification", "") or "").lower()  # model-safety-ok
            security_level = (getattr(app, "security_level", "") or "").lower()  # model-safety-ok
            criticality = (getattr(app, "criticality", "") or "").lower()  # model-safety-ok
            business_criticality = (getattr(app, "business_criticality", "") or "").lower()  # model-safety-ok
            pii_processed = getattr(app, "pii_data_processed", False) or False  # model-safety-ok
            getattr(app, "gdpr_compliant", False) or False

            # Compliance tags — stored as JSON text
            compliance_tags_raw = getattr(app, "compliance_tags", None)  # model-safety-ok
            compliance_tags: list = []
            if compliance_tags_raw:
                try:
                    import json as _json
                    compliance_tags = _json.loads(compliance_tags_raw)
                    if not isinstance(compliance_tags, list):
                        compliance_tags = []
                except Exception:
                    compliance_tags = []

            # Compliance requirements field
            compliance_reqs_raw = getattr(app, "compliance_requirements", None)  # model-safety-ok
            compliance_reqs: list = []
            if compliance_reqs_raw:
                try:
                    import json as _json
                    compliance_reqs = _json.loads(compliance_reqs_raw)
                    if not isinstance(compliance_reqs, list):
                        compliance_reqs = []
                except Exception:
                    compliance_reqs = []

            is_compliance_critical = (
                data_classification in ("restricted", "confidential")
                or security_level == "critical"
                or criticality == "mission_critical"
                or business_criticality in ("critical",)
                or pii_processed
                or bool(compliance_tags)
                or bool(compliance_reqs)
            )

            compliance_signals = []
            if data_classification in ("restricted", "confidential"):
                compliance_signals.append(f"data classification: {data_classification}")
            if security_level == "critical":
                compliance_signals.append("security level: critical")
            if criticality == "mission_critical":
                compliance_signals.append("criticality: mission-critical")
            if pii_processed:
                compliance_signals.append("processes PII data")
            if compliance_tags:
                compliance_signals.append(f"compliance tags: {', '.join(compliance_tags[:5])}")
            if compliance_reqs:
                compliance_signals.append(f"compliance requirements: {', '.join(str(r) for r in compliance_reqs[:3])}")

            if is_compliance_critical:
                cmp_status = "blocked"
                total_blockers += 1
                cmp_details = (
                    "Application is compliance-critical: "
                    + "; ".join(compliance_signals)
                    + ". A formal regulatory impact assessment and data custody transfer plan are required."
                )
            else:
                cmp_status = "clear"
                cmp_details = "No compliance-critical attributes detected on this application."

            categories.append({
                "name": "Compliance",
                "status": cmp_status,
                "count": len(compliance_signals),
                "details": cmp_details,
            })
        except Exception as exc:
            logger.error("Compliance check failed for app %s: %s", app.id, exc, exc_info=True)  # model-safety-ok
            categories.append({
                "name": "Compliance",
                "status": "warning",
                "count": 0,
                "details": "Compliance data could not be assessed — manual review required.",
            })

        # ── Category 5: Data Migration ────────────────────────────────────────
        try:
            database_size_gb = getattr(app, "database_size_gb", None) or 0.0  # model-safety-ok
            database_size_gb = float(database_size_gb or 0.0)

            # Additional data volume signals
            has_primary_db = bool(getattr(app, "primary_database", None) or getattr(app, "primary_data_store", None))  # model-safety-ok
            data_arch = (getattr(app, "data_architecture", "") or "").lower()  # model-safety-ok

            # Check for a migration strategy signal: planned retirement date, lifecycle, or notes
            planned_retirement = getattr(app, "planned_retirement_date", None)  # model-safety-ok
            assessment_notes = (getattr(app, "assessment_notes", "") or "").lower()  # model-safety-ok
            notes_field = (getattr(app, "notes", "") or "").lower()  # model-safety-ok

            migration_keywords = ("migration", "migrat", "data transfer", "etl", "cutover")
            has_migration_strategy = (
                planned_retirement is not None
                or any(kw in assessment_notes for kw in migration_keywords)
                or any(kw in notes_field for kw in migration_keywords)
            )

            # Large dataset threshold: >10 GB without a documented migration strategy is a blocker
            large_dataset = database_size_gb > 10.0

            if large_dataset and not has_migration_strategy:
                dm_status = "blocked"
                total_blockers += 1
                dm_details = (
                    f"Database footprint is {database_size_gb:,.1f} GB with no documented data migration strategy. "
                    "A data migration plan, ETL runbook, and cutover schedule must be defined before retirement."
                )
            elif database_size_gb > 0 or has_primary_db:
                dm_status = "warning"
                size_str = f"{database_size_gb:,.1f} GB" if database_size_gb > 0 else "unknown size"
                dm_details = (
                    f"Application has a data store ({size_str}"
                    + (f", {data_arch} architecture" if data_arch else "")
                    + ")."
                    + (" A migration strategy is indicated." if has_migration_strategy else " Verify data migration strategy before retirement.")
                )
            else:
                dm_status = "clear"
                dm_details = "No significant data footprint detected — no data migration constraints identified."

            categories.append({
                "name": "Data Migration",
                "status": dm_status,
                "count": int(database_size_gb),
                "details": dm_details,
            })
        except Exception as exc:
            logger.error("Data migration check failed for app %s: %s", app.id, exc, exc_info=True)  # model-safety-ok
            categories.append({
                "name": "Data Migration",
                "status": "warning",
                "count": 0,
                "details": "Data migration data could not be assessed — manual review required.",
            })

        # ── Summary ───────────────────────────────────────────────────────────
        is_retirement_safe = total_blockers == 0

        if total_blockers == 0:
            blocked_names = [c["name"] for c in categories if c["status"] == "warning"]
            if blocked_names:
                blocker_summary = (
                    f"No hard blockers found, but {len(blocked_names)} category(ies) need attention: "
                    + ", ".join(blocked_names) + "."
                )
            else:
                blocker_summary = "All five retirement blocker categories are clear. Retirement can proceed."
        else:
            blocked_names = [c["name"] for c in categories if c["status"] == "blocked"]
            blocker_summary = (
                f"{total_blockers} retirement blocker(s) across: "
                + ", ".join(blocked_names)
                + ". These must be resolved before retirement can proceed."
            )

        return {
            "categories": categories,
            "total_blockers": total_blockers,
            "is_retirement_safe": is_retirement_safe,
            "blocker_summary": blocker_summary,
        }

    @staticmethod
    def compute_retirement_sequence(app_ids: Optional[List[int]] = None) -> Dict:
        """
        Compute a dependency-aware retirement sequence for ELIMINATE/MIGRATE apps.

        Performs a topological sort over the dependency graph formed by
        ApplicationDependency edges among the target app set.  Apps with no
        downstream consumers within the set retire first (Wave 1); each
        subsequent wave contains apps whose dependents have all been placed in
        earlier waves.

        If ``app_ids`` is provided only those applications are considered.
        Otherwise, all ApplicationRationalizationScore records whose
        rationalization_action is 'ELIMINATE' or 'MIGRATE' are used.

        Args:
            app_ids: Optional explicit list of ApplicationComponent IDs to sequence.
                     If None, queries all ELIMINATE/MIGRATE scored apps.

        Returns:
            {
                "success": True,
                "total_apps": int,
                "total_waves": int,
                "waves": [
                    {
                        "wave_number": int,          # 1-based
                        "app_count": int,
                        "apps": [
                            {
                                "app_id": int,
                                "app_name": str,
                                "disposition": str,  # ELIMINATE | MIGRATE | unknown
                                "lifecycle_status": str | None,
                                "business_criticality": str | None,
                                "blocked_by": [int],  # app_ids that must retire first
                                "unblocks": [int],    # app_ids that can retire after this
                            },
                            ...
                        ]
                    },
                    ...
                ],
                "unsequenced": [int],  # app_ids excluded due to cycles (cannot sequence)
            }
        """
        try:
            # ── 1. Resolve the set of apps to sequence ────────────────────────
            if app_ids is not None:
                candidates = (
                    ApplicationComponent.query
                    .filter(ApplicationComponent.id.in_(app_ids))
                    .all()
                )
                disposition_map: Dict[int, str] = {}
                # Batch-load all scores for candidate app IDs to avoid N+1
                score_rows = ApplicationRationalizationScore.query.filter(
                    ApplicationRationalizationScore.application_component_id.in_(app_ids)
                ).all()
                score_by_app = {s.application_component_id: s for s in score_rows}
                for app in candidates:
                    score_row = score_by_app.get(app.id)
                    if score_row:
                        disposition_map[app.id] = getattr(score_row, "rationalization_action", "unknown") or "unknown"  # model-safety-ok
                    else:
                        disposition_map[app.id] = "unknown"
            else:
                score_rows = ApplicationRationalizationScore.query.filter(
                    ApplicationRationalizationScore.rationalization_action.in_(
                        ["ELIMINATE", "MIGRATE"]
                    )
                ).all()
                app_ids = [row.application_component_id for row in score_rows]
                disposition_map = {
                    row.application_component_id: row.rationalization_action
                    for row in score_rows
                }
                candidates = (
                    ApplicationComponent.query
                    .filter(ApplicationComponent.id.in_(app_ids))
                    .all()
                )

            if not candidates:
                return {
                    "success": True,
                    "total_apps": 0,
                    "total_waves": 0,
                    "waves": [],
                    "unsequenced": [],
                }

            candidate_ids: set = {app.id for app in candidates}
            app_name_map: Dict[int, str] = {app.id: app.name for app in candidates}
            lifecycle_map: Dict[int, Optional[str]] = {
                app.id: getattr(app, "lifecycle_status", None)  # model-safety-ok
                for app in candidates
            }
            criticality_map: Dict[int, Optional[str]] = {
                app.id: getattr(app, "business_criticality", None)  # model-safety-ok
                for app in candidates
            }

            # ── 2. Build adjacency within the candidate set ───────────────────
            # Edge semantics: source depends ON target.
            # For sequencing: target must retire BEFORE source.
            # deps_on[app_id]  = set of app_ids this app depends on (must retire first)
            # consumers[app_id] = set of app_ids that depend on this app (unblocked after)
            deps_on: Dict[int, set] = {app_id: set() for app_id in candidate_ids}
            consumers: Dict[int, set] = {app_id: set() for app_id in candidate_ids}

            active_deps = (
                ApplicationDependency.query.filter(
                    ApplicationDependency.source_app_id.in_(candidate_ids),
                    ApplicationDependency.target_app_id.in_(candidate_ids),
                    ApplicationDependency.status == "active",
                ).all()
            )

            for dep in active_deps:
                src = dep.source_app_id
                tgt = dep.target_app_id
                if src in candidate_ids and tgt in candidate_ids and src != tgt:
                    # src depends on tgt → tgt must retire first
                    deps_on[src].add(tgt)
                    consumers[tgt].add(src)

            # ── 3. Kahn's algorithm (topological sort into waves) ─────────────
            # in-degree = number of prerequisite retirements still pending
            in_degree: Dict[int, int] = {
                app_id: len(deps_on[app_id]) for app_id in candidate_ids
            }

            # Wave 0 seed: apps with no remaining prerequisites
            current_wave_apps = [
                app_id for app_id in candidate_ids if in_degree[app_id] == 0
            ]
            current_wave_apps.sort()  # deterministic order within wave

            waves_output: List[Dict] = []
            sequenced_ids: set = set()
            wave_number = 1

            while current_wave_apps:
                wave_entries = []
                for app_id in current_wave_apps:
                    wave_entries.append({
                        "app_id": app_id,
                        "app_name": app_name_map.get(app_id, f"App {app_id}"),
                        "disposition": disposition_map.get(app_id, "unknown"),
                        "lifecycle_status": lifecycle_map.get(app_id),
                        "business_criticality": criticality_map.get(app_id),
                        "blocked_by": sorted(deps_on[app_id]),
                        "unblocks": sorted(consumers[app_id]),
                    })
                    sequenced_ids.add(app_id)

                waves_output.append({
                    "wave_number": wave_number,
                    "app_count": len(wave_entries),
                    "apps": wave_entries,
                })
                wave_number += 1

                # Reduce in-degree for all consumers of apps retiring in this wave
                next_wave_apps = []
                for app_id in current_wave_apps:
                    for consumer_id in consumers[app_id]:
                        in_degree[consumer_id] -= 1
                        if in_degree[consumer_id] == 0:
                            next_wave_apps.append(consumer_id)

                next_wave_apps.sort()
                current_wave_apps = next_wave_apps

            # ── 4. Detect cycles (apps that were never sequenced) ─────────────
            unsequenced = sorted(candidate_ids - sequenced_ids)
            if unsequenced:
                logger.warning(
                    "compute_retirement_sequence: %d app(s) could not be sequenced "
                    "due to dependency cycles: %s",
                    len(unsequenced),
                    unsequenced,
                )

            return {
                "success": True,
                "total_apps": len(candidate_ids),
                "total_waves": len(waves_output),
                "waves": waves_output,
                "unsequenced": unsequenced,
            }

        except Exception as exc:
            logger.error(
                "compute_retirement_sequence failed: %s", exc, exc_info=True
            )
            return {
                "success": False,
                "error": str(exc),
                "total_apps": 0,
                "total_waves": 0,
                "waves": [],
                "unsequenced": [],
            }
