"""Configurable scoring engine - replaces hardcoded values across the platform.

Provides a single service to retrieve and manage scoring parameters for:
- ArchiMate pattern confidence (was hardcoded 0.7-0.75)
- Vendor pricing tier scores (was hardcoded brackets)
- Vendor quality base score (was hardcoded 0)
- Risk scoring weights (was hardcoded impact/probability split)

Usage::

    from app.services.scoring_engine_service import ScoringEngineService

    engine = ScoringEngineService()
    confidence = engine.get_score('archimate_confidence', 'pattern_match')
    # Returns DB-configured value, or default 0.7 if no DB row exists.

All methods are safe to call without any DB records - they fall back to
SCORING_DEFAULTS defined below.
"""

import logging

from app import db
from app.models.scoring_config import ScoringConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default values (used when no DB config exists)
# ---------------------------------------------------------------------------
SCORING_DEFAULTS = {
    # ArchiMate confidence scores
    ("archimate_confidence", "pattern_match"): {
        "weight": 0.7,
        "threshold": 0.5,
        "description": "Confidence score for ArchiMate pattern matching",
    },
    ("archimate_confidence", "layer_coverage"): {
        "weight": 0.75,
        "threshold": 0.6,
        "description": "Confidence score for ArchiMate layer coverage",
    },
    # Vendor pricing tier scores
    ("vendor_pricing", "under_10k"): {
        "weight": 90.0,
        "description": "Score for vendor pricing under 10k a year",
    },
    ("vendor_pricing", "under_50k"): {
        "weight": 75.0,
        "description": "Score for vendor pricing under 50k a year",
    },
    ("vendor_pricing", "under_100k"): {
        "weight": 60.0,
        "description": "Score for vendor pricing under 100k a year",
    },
    ("vendor_pricing", "over_100k"): {
        "weight": 40.0,
        "description": "Score for vendor pricing over 100k a year",
    },
    # Vendor quality
    ("vendor_quality", "base_score"): {
        "weight": 0.0,
        "description": "Base vendor quality score (currently returns 0)",
    },
    # Risk scoring weights
    ("risk_scoring", "impact_weight"): {
        "weight": 0.6,
        "description": "Weight applied to risk impact factor",
    },
    ("risk_scoring", "probability_weight"): {
        "weight": 0.4,
        "description": "Weight applied to risk probability factor",
    },
}


class ScoringEngineService:
    """Centralised scoring parameter retrieval and management.

    All public methods fall back to SCORING_DEFAULTS when no DB row
    exists, so the service works out-of-the-box without seeding.
    """

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get_score(self, category, metric):
        """Return the weight for a (category, metric) pair.

        Lookup order:
        1. Active DB row matching category + metric
        2. SCORING_DEFAULTS entry
        3. Fallback: 1.0
        """
        try:
            config = ScoringConfig.query.filter_by(
                category=category, metric=metric, is_active=True
            ).first()
            if config:
                return config.weight
        except Exception:
            logger.debug(
                "DB lookup failed for %s/%s, using defaults", category, metric
            )

        default = SCORING_DEFAULTS.get((category, metric))
        if default:
            return default["weight"]
        return 1.0

    def get_config(self, category, metric):
        """Return full config dict for a (category, metric) pair.

        Returns a dict with keys: weight, threshold, min_value, max_value,
        formula, description.  DB values override defaults.
        """
        try:
            config = ScoringConfig.query.filter_by(
                category=category, metric=metric, is_active=True
            ).first()
            if config:
                return config.to_dict()
        except Exception:
            logger.debug(
                "DB lookup failed for %s/%s, using defaults", category, metric
            )

        default = SCORING_DEFAULTS.get((category, metric))
        if default:
            return {
                "id": None,
                "category": category,
                "metric": metric,
                "weight": default.get("weight", 1.0),
                "threshold": default.get("threshold"),
                "min_value": default.get("min_value"),
                "max_value": default.get("max_value"),
                "formula": default.get("formula"),
                "is_active": True,
                "description": default.get("description"),
                "created_at": None,
                "updated_at": None,
            }
        return None

    def get_category_configs(self, category):
        """Return all configs for a category (DB rows merged with defaults).

        DB rows take precedence.  Defaults that have no DB row are
        included with ``id=None``.
        """
        result = {}

        # Start with defaults for this category
        for (cat, met), vals in SCORING_DEFAULTS.items():
            if cat == category:
                result[met] = {
                    "id": None,
                    "category": cat,
                    "metric": met,
                    "weight": vals.get("weight", 1.0),
                    "threshold": vals.get("threshold"),
                    "min_value": vals.get("min_value"),
                    "max_value": vals.get("max_value"),
                    "formula": vals.get("formula"),
                    "is_active": True,
                    "description": vals.get("description"),
                    "created_at": None,
                    "updated_at": None,
                }

        # Override with DB rows
        try:
            db_configs = ScoringConfig.query.filter_by(
                category=category, is_active=True
            ).all()
            for cfg in db_configs:
                result[cfg.metric] = cfg.to_dict()
        except Exception:
            logger.debug("DB lookup failed for category %s", category)

        return list(result.values())

    def list_all_configs(self):
        """Return every config (DB rows merged with defaults)."""
        result = {}

        # Defaults first
        for (cat, met), vals in SCORING_DEFAULTS.items():
            key = f"{cat}/{met}"
            result[key] = {
                "id": None,
                "category": cat,
                "metric": met,
                "weight": vals.get("weight", 1.0),
                "threshold": vals.get("threshold"),
                "min_value": vals.get("min_value"),
                "max_value": vals.get("max_value"),
                "formula": vals.get("formula"),
                "is_active": True,
                "description": vals.get("description"),
                "created_at": None,
                "updated_at": None,
            }

        # DB rows override
        try:
            for cfg in ScoringConfig.query.filter_by(is_active=True).all():  # model-safety-ok: single query
                key = f"{cfg.category}/{cfg.metric}"
                result[key] = cfg.to_dict()
        except Exception:
            logger.debug("DB lookup failed for list_all_configs")

        return list(result.values())

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def update_config(self, category, metric, **kwargs):
        """Update or create a scoring config row.

        Accepted kwargs: weight, threshold, min_value, max_value,
        formula, is_active, description.

        Returns the updated/created ScoringConfig instance.
        """
        config = ScoringConfig.query.filter_by(
            category=category, metric=metric
        ).first()

        if config is None:
            default = SCORING_DEFAULTS.get((category, metric), {})
            config = ScoringConfig(
                category=category,
                metric=metric,
                weight=default.get("weight", 1.0),
                threshold=default.get("threshold"),
                description=default.get("description"),
            )
            db.session.add(config)

        allowed = {
            "weight", "threshold", "min_value", "max_value",
            "formula", "is_active", "description",
        }
        for key, value in kwargs.items():
            if key in allowed:
                setattr(config, key, value)

        db.session.commit()
        logger.info("Updated scoring config %s/%s", category, metric)
        return config

    def seed_defaults(self):
        """Seed all SCORING_DEFAULTS into the DB (skip existing rows)."""
        created = 0
        for (category, metric), vals in SCORING_DEFAULTS.items():
            existing = ScoringConfig.query.filter_by(  # model-safety-ok: bounded seed loop
                category=category, metric=metric
            ).first()
            if existing:
                continue
            config = ScoringConfig(
                category=category,
                metric=metric,
                weight=vals.get("weight", 1.0),
                threshold=vals.get("threshold"),
                min_value=vals.get("min_value"),
                max_value=vals.get("max_value"),
                formula=vals.get("formula"),
                description=vals.get("description"),
                is_active=True,
            )
            db.session.add(config)
            created += 1

        if created:
            db.session.commit()
            logger.info("Seeded %d scoring config defaults", created)
        return created

    # ------------------------------------------------------------------
    # Convenience accessors for common use-cases
    # ------------------------------------------------------------------

    def get_archimate_confidence(self, metric="pattern_match"):
        """Shortcut for ArchiMate confidence scores."""
        return self.get_score("archimate_confidence", metric)

    def get_vendor_pricing_score(self, tier):
        """Shortcut for vendor pricing tier scores.

        ``tier`` should be one of: under_10k, under_50k, under_100k, over_100k.
        """
        return self.get_score("vendor_pricing", tier)

    def get_risk_weights(self):
        """Return (impact_weight, probability_weight) tuple."""
        impact = self.get_score("risk_scoring", "impact_weight")
        probability = self.get_score("risk_scoring", "probability_weight")
        return impact, probability
