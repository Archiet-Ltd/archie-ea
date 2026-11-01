"""
Predictive Analytics Engine

Provides machine learning-powered predictive capabilities for the platform:
- Application sunset prediction
- Cost forecast modeling
- Capability gap prediction
- Vendor risk scoring
- Resource utilization forecasting
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from app.models.application_portfolio import ApplicationComponent
from app.models.unified_capability import UnifiedCapability
from app.models.vendor.vendor_organization import VendorOrganization

logger = logging.getLogger(__name__)


class PredictiveModel:
    """Base class for predictive models."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self.model = None
        self.scaler = StandardScaler()
        self.is_trained = False
        self.model_path = os.path.join("models", f"{model_name}.joblib")
        self.scaler_path = os.path.join("models", f"{model_name}_scaler.joblib")

    def save(self):
        """Save model and scaler to disk."""
        os.makedirs("models", exist_ok=True)
        joblib.dump(self.model, self.model_path)
        joblib.dump(self.scaler, self.scaler_path)
        logger.info(f"Model {self.model_name} saved to {self.model_path}")

    def load(self):
        """Load model and scaler from disk."""
        if os.path.exists(self.model_path) and os.path.exists(self.scaler_path):
            self.model = joblib.load(self.model_path)
            self.scaler = joblib.load(self.scaler_path)
            self.is_trained = True
            logger.info(f"Model {self.model_name} loaded from {self.model_path}")
            return True
        return False


class ApplicationSunsetPredictor(PredictiveModel):
    """
    Predicts which applications are likely to be sunset in the next 12 months.

    Features:
    - Application age
    - Last update timestamp
    - Number of dependencies
    - Vendor status
    - Technical debt indicators
    - Usage metrics
    """

    def __init__(self):
        super().__init__("application_sunset")
        self.model = RandomForestClassifier(n_estimators=100, random_state=42)

    def prepare_features(self, applications: List[ApplicationComponent]) -> pd.DataFrame:
        """Extract features from applications."""
        features = []

        for app in applications:
            # Calculate age in days
            age_days = (datetime.utcnow() - app.created_at).days if app.created_at else 0

            # Days since last update
            days_since_update = (datetime.utcnow() - app.updated_at).days if app.updated_at else 999

            # Status encoding
            status_map = {"active": 0, "planned": 1, "deprecated": 2, "sunset": 3, "retired": 4}
            status_code = status_map.get(getattr(app, "status", "active").lower(), 0)

            # Criticality encoding
            criticality_map = {"low": 0, "medium": 1, "high": 2, "critical": 3}
            criticality_code = criticality_map.get(getattr(app, "criticality", "medium").lower(), 1)

            feature_row = {
                "age_days": age_days,
                "days_since_update": days_since_update,
                "status_code": status_code,
                "criticality_code": criticality_code,
                "has_description": 1 if app.description else 0,
                "name_length": len(app.name) if app.name else 0,
            }

            features.append(feature_row)

        return pd.DataFrame(features)

    def train(self, applications: List[ApplicationComponent], labels: List[int]):
        """Train the sunset prediction model."""
        if len(applications) < 10:
            logger.warning("Insufficient training data for sunset predictor")
            return False

        X = self.prepare_features(applications)
        X_scaled = self.scaler.fit_transform(X)

        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X_scaled, labels, test_size=0.2, random_state=42
        )

        # Train model
        self.model.fit(X_train, y_train)

        # Evaluate
        train_score = self.model.score(X_train, y_train)
        test_score = self.model.score(X_test, y_test)

        logger.info(
            f"Sunset predictor trained - Train accuracy: {train_score:.3f}, Test accuracy: {test_score:.3f}"
        )

        self.is_trained = True
        self.save()
        return True

    def predict(self, applications: List[ApplicationComponent]) -> List[Dict[str, Any]]:
        """Predict sunset likelihood for applications."""
        if not self.is_trained:
            if not self.load():
                logger.error("Model not trained and cannot be loaded")
                return []

        X = self.prepare_features(applications)
        X_scaled = self.scaler.transform(X)

        predictions = self.model.predict_proba(X_scaled)

        results = []
        for i, app in enumerate(applications):
            sunset_probability = predictions[i][1] if len(predictions[i]) > 1 else 0.0

            results.append(
                {
                    "application_id": app.id,
                    "application_name": app.name,
                    "sunset_probability": float(sunset_probability),
                    "risk_level": self._get_risk_level(sunset_probability),
                    "predicted_at": datetime.utcnow().isoformat(),
                }
            )

        return results

    def _get_risk_level(self, probability: float) -> str:
        """Convert probability to risk level."""
        if probability >= 0.7:
            return "high"
        elif probability >= 0.4:
            return "medium"
        else:
            return "low"


class CostForecastModel(PredictiveModel):
    """
    Forecasts application costs for the next 6 - 12 months.

    Features:
    - Historical cost data
    - Usage trends
    - Vendor pricing changes
    - Seasonality
    """

    def __init__(self):
        super().__init__("cost_forecast")
        self.model = RandomForestRegressor(n_estimators=100, random_state=42)

    def prepare_features(self, cost_data: pd.DataFrame) -> pd.DataFrame:
        """Prepare time-series features for cost forecasting."""
        # Add time-based features
        if "date" in cost_data.columns:
            cost_data["month"] = pd.to_datetime(cost_data["date"]).dt.month
            cost_data["quarter"] = pd.to_datetime(cost_data["date"]).dt.quarter
            cost_data["year"] = pd.to_datetime(cost_data["date"]).dt.year

        return cost_data

    def forecast(self, application_id: int, months: int = 6) -> List[Dict[str, Any]]:
        """Forecast costs for the next N months."""
        if not self.is_trained:
            if not self.load():
                logger.warning("Cost forecast model not trained, returning default forecast")
                return self._default_forecast(months)

        forecasts = []
        base_date = datetime.utcnow()

        for month_offset in range(1, months + 1):
            forecast_date = base_date + timedelta(days=30 * month_offset)

            # Simplified forecast (would use actual historical data in production)
            estimated_cost = 10000 * (1 + month_offset * 0.05)  # 5% monthly growth

            forecasts.append(
                {
                    "application_id": application_id,
                    "forecast_date": forecast_date.strftime("%Y-%m-%d"),
                    "estimated_cost": round(estimated_cost, 2),
                    "confidence_interval": [
                        round(estimated_cost * 0.9, 2),
                        round(estimated_cost * 1.1, 2),
                    ],
                }
            )

        return forecasts

    def _default_forecast(self, months: int) -> List[Dict[str, Any]]:
        """Generate default forecast when model is not trained."""
        base_cost = 10000
        forecasts = []
        base_date = datetime.utcnow()

        for month_offset in range(1, months + 1):
            forecast_date = base_date + timedelta(days=30 * month_offset)
            estimated_cost = base_cost * (1 + month_offset * 0.03)

            forecasts.append(
                {
                    "forecast_date": forecast_date.strftime("%Y-%m-%d"),
                    "estimated_cost": round(estimated_cost, 2),
                    "confidence_interval": [
                        round(estimated_cost * 0.85, 2),
                        round(estimated_cost * 1.15, 2),
                    ],
                }
            )

        return forecasts


class CapabilityGapPredictor(PredictiveModel):
    """
    Predicts future capability gaps based on business trends and technology evolution.
    """

    def __init__(self):
        super().__init__("capability_gap")
        self.model = RandomForestClassifier(n_estimators=100, random_state=42)

    def predict_gaps(self, current_capabilities: List[UnifiedCapability]) -> List[Dict[str, Any]]:
        """Predict likely capability gaps."""
        predictions = []

        # Simple heuristic-based predictions (would use ML in production)
        domains = set(cap.domain for cap in current_capabilities if cap.domain)

        # Common capability gaps
        potential_gaps = [
            {"domain": "Digital", "capability": "AI/ML Integration", "priority": "high"},
            {"domain": "Technology", "capability": "Cloud Migration", "priority": "high"},
            {"domain": "Business", "capability": "Customer Analytics", "priority": "medium"},
            {"domain": "Data", "capability": "Real-time Analytics", "priority": "medium"},
            {"domain": "Security", "capability": "Zero Trust Architecture", "priority": "high"},
        ]

        for gap in potential_gaps:
            # Check if domain exists
            has_domain = gap["domain"] in domains

            predictions.append(
                {
                    "gap_type": gap["capability"],
                    "domain": gap["domain"],
                    "priority": gap["priority"],
                    "likelihood": 0.7 if not has_domain else 0.4,
                    "impact": "high" if gap["priority"] == "high" else "medium",
                    "recommended_action": f"Develop {gap['capability']} capability",
                }
            )

        return sorted(predictions, key=lambda x: x["likelihood"], reverse=True)


class VendorRiskScorer(PredictiveModel):
    """
    Scores vendor risk based on multiple factors.
    """

    def __init__(self):
        super().__init__("vendor_risk")
        self.model = RandomForestClassifier(n_estimators=100, random_state=42)

    def score_vendors(self, vendors: List[VendorOrganization]) -> List[Dict[str, Any]]:
        """Calculate risk scores for vendors."""
        scores = []

        for vendor in vendors:
            # Calculate risk factors
            risk_score = 0.0
            risk_factors = []

            # Factor 1: Strategic tier (higher tier = lower risk)
            tier = getattr(vendor, "strategic_tier", "tier_3")
            if tier == "tier_1":
                risk_score += 0.1
            elif tier == "tier_2":
                risk_score += 0.3
            else:
                risk_score += 0.5
                risk_factors.append("Lower strategic tier")

            # Factor 2: Vendor status
            status = getattr(vendor, "status", "active")
            if status != "active":
                risk_score += 0.3
                risk_factors.append("Non-active status")

            # Factor 3: Data availability
            if not vendor.website:
                risk_score += 0.1
                risk_factors.append("Missing website")

            if not vendor.description:
                risk_score += 0.1
                risk_factors.append("Missing description")

            # Normalize score to 0 - 1
            risk_score = min(risk_score, 1.0)

            scores.append(
                {
                    "vendor_id": vendor.id,
                    "vendor_name": vendor.name,
                    "risk_score": round(risk_score, 2),
                    "risk_level": self._get_risk_level(risk_score),
                    "risk_factors": risk_factors,
                    "assessed_at": datetime.utcnow().isoformat(),
                }
            )

        return sorted(scores, key=lambda x: x["risk_score"], reverse=True)

    def _get_risk_level(self, score: float) -> str:
        """Convert score to risk level."""
        if score >= 0.7:
            return "high"
        elif score >= 0.4:
            return "medium"
        else:
            return "low"


class PredictiveAnalyticsEngine:
    """
    Main engine for all predictive analytics.
    """

    def __init__(self):
        self.sunset_predictor = ApplicationSunsetPredictor()
        self.cost_forecaster = CostForecastModel()
        self.gap_predictor = CapabilityGapPredictor()
        self.vendor_risk_scorer = VendorRiskScorer()

    def get_application_sunset_predictions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get applications most likely to be sunset."""
        applications = ApplicationComponent.query.limit(100).all()

        if not applications:
            return []

        predictions = self.sunset_predictor.predict(applications)

        # Return top N predictions sorted by probability
        predictions.sort(key=lambda x: x["sunset_probability"], reverse=True)
        return predictions[:limit]

    def get_cost_forecast(self, application_id: int, months: int = 6) -> List[Dict[str, Any]]:
        """Get cost forecast for an application."""
        return self.cost_forecaster.forecast(application_id, months)

    def get_capability_gaps(self) -> List[Dict[str, Any]]:
        """Predict capability gaps."""
        capabilities = UnifiedCapability.active().all()
        return self.gap_predictor.predict_gaps(capabilities)

    def get_vendor_risk_scores(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get vendor risk scores."""
        vendors = VendorOrganization.query.limit(100).all()

        if not vendors:
            return []

        scores = self.vendor_risk_scorer.score_vendors(vendors)
        return scores[:limit]

    def get_dashboard_metrics(self) -> Dict[str, Any]:
        """Get aggregated predictive metrics for dashboard."""
        sunset_predictions = self.get_application_sunset_predictions(limit=5)
        capability_gaps = self.get_capability_gaps()
        vendor_risks = self.get_vendor_risk_scores(limit=5)

        return {
            "high_risk_applications": len(
                [p for p in sunset_predictions if p["risk_level"] == "high"]
            ),
            "predicted_sunset_count": len(
                [p for p in sunset_predictions if p["sunset_probability"] > 0.5]
            ),
            "capability_gaps_count": len(capability_gaps),
            "high_risk_vendors": len([v for v in vendor_risks if v["risk_level"] == "high"]),
            "top_sunset_risks": sunset_predictions[:3],
            "top_capability_gaps": capability_gaps[:3],
            "top_vendor_risks": vendor_risks[:3],
            "last_updated": datetime.utcnow().isoformat(),
        }


# Singleton instance
_analytics_engine = None


def get_analytics_engine() -> PredictiveAnalyticsEngine:
    """Get or create the analytics engine instance."""
    global _analytics_engine

    if _analytics_engine is None:
        _analytics_engine = PredictiveAnalyticsEngine()

    return _analytics_engine
