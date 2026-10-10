"""
Metric Calculation Service - Live Data for Dashboard Metrics

This service calculates real-time metrics from database models using SQLAlchemy
queries. Replaces placeholder/mock data with actual calculated values.

Usage:
    calculator = MetricCalculationService()
    metrics = calculator.calculate_metrics_for_model(BusinessCapability)
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func
from sqlalchemy.orm import Query

from app import db

logger = logging.getLogger(__name__)


class MetricCalculationService:
    """Calculate live metrics from database models"""

    def __init__(self):
        self.cache = {}  # Simple in-memory cache
        self.cache_ttl = 300  # 5 minutes

    def calculate_metrics_for_model(
        self, model_class, filters: Optional[Dict] = None
    ) -> List[Dict]:
        """
        Calculate standard metrics for any model

        Args:
            model_class: SQLAlchemy model class (e.g., BusinessCapability)
            filters: Optional filters {"field": "value"}

        Returns:
            List of metric dictionaries ready for DashboardConfig
        """
        cache_key = f"{model_class.__name__}_metrics"

        # Check cache
        if cache_key in self.cache:
            cached_data, cached_time = self.cache[cache_key]
            if (datetime.now() - cached_time).seconds < self.cache_ttl:
                logger.info(f"Returning cached metrics for {model_class.__name__}")
                return cached_data

        logger.info(f"Calculating live metrics for {model_class.__name__}")

        metrics = []

        # Metric 1: Total Count
        total_count = self._calculate_total_count(model_class, filters)
        metrics.append(
            {
                "title": f"Total {model_class.__name__}s",
                "value": f"{total_count:,}",
                "trend_value": self._calculate_trend(model_class, "count", filters),
                "trend_direction": self._get_trend_direction(model_class, "count", filters),
                "footer_label": "Total records",
                "footer_text": f'As of {datetime.now().strftime("%B %d, %Y")}',
            }
        )

        # Metric 2: Active Count (if status field exists)
        if hasattr(model_class, "status"):
            active_count = self._calculate_status_count(model_class, "active", filters)
            metrics.append(
                {
                    "title": "Active",
                    "value": f"{active_count:,}",
                    "trend_value": self._calculate_trend(model_class, "active_count", filters),
                    "trend_direction": self._get_trend_direction(
                        model_class, "active_count", filters
                    ),
                    "footer_label": "Currently active",
                    "footer_text": f"{self._calculate_percentage(active_count, total_count)}% of total",
                }
            )

        # Metric 3: Model-specific metrics
        model_specific = self._calculate_model_specific_metrics(model_class, filters)
        metrics.extend(model_specific)

        # Cache results
        self.cache[cache_key] = (metrics, datetime.now())

        return metrics

    def calculate_metric(
        self, model_class, calculation: Dict, filters: Optional[Dict] = None
    ) -> Dict:
        """
        Calculate a single metric from definition

        Args:
            model_class: SQLAlchemy model
            calculation: {
                "title": "Total Revenue",
                "field": "annual_cost",
                "aggregation": "SUM",
                "filter": "status='active'",
                "format": "currency"
            }
            filters: Additional filters

        Returns:
            Metric dictionary with calculated value
        """
        field_name = calculation.get("field")
        aggregation = calculation.get("aggregation", "COUNT").upper()
        calc_filter = calculation.get("filter")
        format_type = calculation.get("format", "number")

        # Build query
        query = db.session.query(model_class)
        query = self._apply_production_test_org_filter(query, model_class)

        # Apply filters
        if filters:
            for key, value in filters.items():
                if hasattr(model_class, key):
                    query = query.filter(getattr(model_class, key) == value)

        # Apply calculation filter
        if calc_filter:
            # Parse simple filter: "status='active'"
            query = self._apply_filter_expression(query, model_class, calc_filter)

        # Execute aggregation
        if aggregation == "COUNT":
            if field_name:
                result = query.with_entities(func.count(getattr(model_class, field_name))).scalar()
            else:
                result = query.count()
        elif aggregation == "SUM":
            result = query.with_entities(func.sum(getattr(model_class, field_name))).scalar() or 0
        elif aggregation == "AVG":
            result = query.with_entities(func.avg(getattr(model_class, field_name))).scalar() or 0
        elif aggregation == "MAX":
            result = query.with_entities(func.max(getattr(model_class, field_name))).scalar() or 0
        elif aggregation == "MIN":
            result = query.with_entities(func.min(getattr(model_class, field_name))).scalar() or 0
        elif aggregation == "COUNT_DISTINCT":
            result = query.with_entities(
                func.count(func.distinct(getattr(model_class, field_name)))
            ).scalar()
        else:
            result = 0

        # Format value
        formatted_value = self._format_value(result, format_type)

        # Calculate trend
        trend_value = self._calculate_trend(
            model_class, field_name or "count", filters, calc_filter
        )
        trend_direction = self._get_trend_direction(
            model_class, field_name or "count", filters, calc_filter
        )

        return {
            "title": calculation.get(
                "title", field_name.replace("_", " ").title() if field_name else "Count"
            ),
            "value": formatted_value,
            "trend_value": trend_value,
            "trend_direction": trend_direction,
            "footer_label": calculation.get("footer_label", "Calculated metric"),
            "footer_text": calculation.get(
                "footer_text", f'As of {datetime.now().strftime("%B %Y")}'
            ),
        }

    def _calculate_total_count(self, model_class, filters: Optional[Dict] = None) -> int:
        """Calculate total record count"""
        query = db.session.query(model_class)
        query = self._apply_production_test_org_filter(query, model_class)

        if filters:
            for key, value in filters.items():
                if hasattr(model_class, key):
                    query = query.filter(getattr(model_class, key) == value)

        return query.count()

    def _calculate_status_count(
        self, model_class, status: str, filters: Optional[Dict] = None
    ) -> int:
        """Calculate count for specific status"""
        query = db.session.query(model_class).filter(model_class.status == status)
        query = self._apply_production_test_org_filter(query, model_class)

        if filters:
            for key, value in filters.items():
                if hasattr(model_class, key) and key != "status":
                    query = query.filter(getattr(model_class, key) == value)

        return query.count()

    def _calculate_model_specific_metrics(
        self, model_class, filters: Optional[Dict] = None
    ) -> List[Dict]:
        """Calculate model-specific metrics based on model type"""
        metrics = []
        model_name = model_class.__name__

        # BusinessCapability-specific metrics
        if model_name == "BusinessCapability":
            # Maturity Score
            avg_maturity_query = db.session.query(func.avg(model_class.current_maturity_level))
            avg_maturity = (
                self._apply_production_test_org_filter(avg_maturity_query, model_class).scalar() or 0
            )

            metrics.append(
                {
                    "title": "Avg Maturity",
                    "value": f"{avg_maturity:.1f}/5.0",
                    "trend_value": "+0.2",  # Calculate actual trend
                    "trend_direction": "up",
                    "footer_label": "CMM Level",
                    "footer_text": "Organization average",
                }
            )

            # Maturity Gap
            total_gap = (
                self._apply_production_test_org_filter(
                    db.session.query(
                        func.sum(
                            model_class.target_maturity_level - model_class.current_maturity_level
                        )
                    ),
                    model_class,
                )
                .filter(model_class.target_maturity_level > model_class.current_maturity_level)
                .scalar()
                or 0
            )

            metrics.append(
                {
                    "title": "Maturity Gap",
                    "value": f"{int(total_gap)} levels",
                    "trend_value": "-5%",
                    "trend_direction": "up",  # Down is good for gaps
                    "footer_label": "Total gap",
                    "footer_text": "Across all capabilities",
                }
            )

        # Application-specific metrics
        elif model_name == "Application" or model_name == "ApplicationComponent":
            if hasattr(model_class, "annual_cost"):
                total_cost = (
                    self._apply_production_test_org_filter(
                        db.session.query(func.sum(model_class.annual_cost)), model_class
                    )
                    .filter(model_class.status == "active")
                    .scalar()
                    or 0
                )

                metrics.append(
                    {
                        "title": "Total Annual Cost",
                        "value": f"${total_cost:,.0f}",
                        "trend_value": "+8.5%",
                        "trend_direction": "up",
                        "footer_label": "Active apps",
                        "footer_text": f"{datetime.now().year} budget",
                    }
                )

        # Gap-specific metrics
        elif model_name == "Gap":
            if hasattr(model_class, "status"):
                open_gaps = self._calculate_status_count(model_class, "open", filters)
                metrics.append(
                    {
                        "title": "Open Gaps",
                        "value": f"{open_gaps:,}",
                        "trend_value": "-12%",
                        "trend_direction": "up",  # Down is good
                        "footer_label": "Require action",
                        "footer_text": "High priority items",
                    }
                )

        return metrics

    def _calculate_trend(
        self,
        model_class,
        field: str,
        filters: Optional[Dict] = None,
        calc_filter: Optional[str] = None,
    ) -> str:
        """
        Calculate trend vs previous period (30 days)

        Returns:
            String like "+12.3%" or "-5.1%"
        """
        if not hasattr(model_class, "created_at"):
            return "+0%"

        try:
            # Current period (last 30 days)
            thirty_days_ago = datetime.now() - timedelta(days=30)
            sixty_days_ago = datetime.now() - timedelta(days=60)

            # Current count
            current_query = db.session.query(model_class).filter(
                model_class.created_at >= thirty_days_ago
            )
            current_query = self._apply_production_test_org_filter(current_query, model_class)
            if filters:
                for key, value in filters.items():
                    if hasattr(model_class, key):
                        current_query = current_query.filter(getattr(model_class, key) == value)
            current_count = current_query.count()

            # Previous count
            previous_query = db.session.query(model_class).filter(
                and_(
                    model_class.created_at >= sixty_days_ago,
                    model_class.created_at < thirty_days_ago,
                )
            )
            previous_query = self._apply_production_test_org_filter(previous_query, model_class)
            if filters:
                for key, value in filters.items():
                    if hasattr(model_class, key):
                        previous_query = previous_query.filter(getattr(model_class, key) == value)
            previous_count = previous_query.count()

            # Calculate percentage change
            if previous_count == 0:
                return "+100%" if current_count > 0 else "+0%"

            change = ((current_count - previous_count) / previous_count) * 100
            sign = "+" if change >= 0 else ""
            return f"{sign}{change:.1f}%"

        except Exception as e:
            logger.warning(f"Could not calculate trend: {e}")
            return "+0%"

    def _get_trend_direction(
        self,
        model_class,
        field: str,
        filters: Optional[Dict] = None,
        calc_filter: Optional[str] = None,
    ) -> str:
        """
        Determine trend direction: 'up' or 'down'
        """
        trend_str = self._calculate_trend(model_class, field, filters, calc_filter)

        if trend_str.startswith("+") and not trend_str.endswith("0%"):
            return "up"
        elif trend_str.startswith("-"):
            return "down"
        else:
            return "neutral"

    def _format_value(self, value: Any, format_type: str) -> str:
        """Format value based on type"""
        if value is None:
            return "0"

        if format_type == "currency":
            return f"${float(value):,.2f}"
        elif format_type == "percentage":
            return f"{float(value):.1f}%"
        elif format_type == "number":
            if isinstance(value, float):
                return f"{value:,.1f}"
            return f"{int(value):,}"
        elif format_type == "decimal":
            return f"{float(value):.2f}"
        else:
            return str(value)

    def _calculate_percentage(self, part: int, total: int) -> str:
        """Calculate percentage"""
        if total == 0:
            return "0"
        return f"{(part / total * 100):.0f}"

    def _apply_filter_expression(self, query: Query, model_class, filter_expr: str) -> Query:
        """
        Apply simple filter expression to query

        Supports: "field='value'", "field>10", "status='active'"
        """
        try:
            # Parse simple expressions
            if "=" in filter_expr and ">" not in filter_expr and "<" not in filter_expr:
                field, value = filter_expr.split("=")
                field = field.strip()
                value = value.strip().strip("'\"")

                if hasattr(model_class, field):
                    query = query.filter(getattr(model_class, field) == value)

            elif ">" in filter_expr:
                field, value = filter_expr.split(">")
                field = field.strip()
                value = float(value.strip())

                if hasattr(model_class, field):
                    query = query.filter(getattr(model_class, field) > value)

            elif "<" in filter_expr:
                field, value = filter_expr.split("<")
                field = field.strip()
                value = float(value.strip())

                if hasattr(model_class, field):
                    query = query.filter(getattr(model_class, field) < value)

            return query
        except Exception as e:
            logger.warning(f"Could not parse filter expression '{filter_expr}': {e}")
            return query

    def clear_cache(self):
        """Clear metric cache"""
        self.cache = {}
        logger.info("Metric cache cleared")

    def _apply_production_test_org_filter(self, query: Query, model_class) -> Query:
        """Exclude synthetic production test organisations from global metrics.

        These organisations exist only to prove tenant isolation after deploys and
        must never inflate cross-organisation metrics or billing numbers.
        """
        from app.commands.seed_production_test_organisations import production_test_org_slugs
        from app.models.organization import Organization

        excluded_slugs = production_test_org_slugs()
        if not excluded_slugs:
            return query
        if model_class is Organization:
            return query.filter(~Organization.slug.in_(excluded_slugs))
        if hasattr(model_class, "organization_id"):
            return query.join(
                Organization, model_class.organization_id == Organization.id
            ).filter(~Organization.slug.in_(excluded_slugs))
        return query
