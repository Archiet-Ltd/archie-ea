"""Feature flag system for dynamic feature control.

Allows admin to toggle features on/off dynamically without code changes.
Supports multiple granularity levels and feature states.
"""

from enum import Enum
import time
from typing import Dict

from markupsafe import Markup, escape

from app.extensions import db
from app.models.mixins import TimestampMixin

__all__ = ["FeatureFlag", "FeatureState", "FeatureType"]


# In-memory cache for feature flags (TTL: 60 seconds)
_feature_cache: Dict[str, tuple] = {}  # key -> (is_active, timestamp)  # cache-tenancy-ok: the feature_flags table has no organisation column and its key is unique, so there is one answer per key for every organisation
_CACHE_TTL = 60  # seconds

# Cache metrics tracking
_cache_metrics = {
    "hits": 0,
    "misses": 0,
    "evictions": 0,
    "invalidations": 0,
}


class FeatureState(str, Enum):
    """Feature lifecycle states."""

    ALPHA = "alpha"  # Early development, may be unstable
    LABS = "labs"  # Experimental features, disabled by default, shown with Labs badge
    BETA = "beta"  # Testing phase, stable but not production-ready
    STABLE = "stable"  # Production-ready, fully supported
    DEPRECATED = "deprecated"  # Still works but will be removed
    MAINTENANCE_MODE = "maintenance_mode"  # Temporarily disabled for maintenance


class FeatureType(str, Enum):
    """Feature granularity levels."""

    SIDEBAR_SECTION = "sidebar_section"  # Entire sidebar section
    ROUTE = "route"  # Individual route/endpoint
    BLUEPRINT = "blueprint"  # Entire Flask blueprint
    FUNCTIONALITY = "functionality"  # Specific functionality within a page


class FeatureFlag(db.Model, TimestampMixin):
    """Feature flag for controlling application features.

    Attributes:
        key: Unique identifier (e.g., 'solutions_management', '/solutions/')
        name: Human-readable name
        description: What this feature does
        feature_type: Granularity level (section/route/blueprint/functionality)
        state: Current lifecycle state (alpha/beta/stable/deprecated/maintenance_mode)
        enabled: Hard on/off switch (overrides all permissions)
        sidebar_label: Label shown in sidebar (if sidebar_section type)
        sidebar_icon: Lucide icon name for sidebar
        routes: JSON list of affected routes
        parent_key: Key of parent feature (for hierarchical features)
        sort_order: Display order in admin UI
    """

    __tablename__ = "feature_flags"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)

    # Feature configuration
    feature_type = db.Column(
        db.Enum(FeatureType, native_enum=False, length=50),
        nullable=False,
        default=FeatureType.FUNCTIONALITY,
    )
    state = db.Column(
        db.Enum(FeatureState, native_enum=False, length=50),
        nullable=False,
        default=FeatureState.BETA,
    )
    enabled = db.Column(db.Boolean, default=True, nullable=False)

    # Sidebar integration
    sidebar_label = db.Column(db.String(100))
    sidebar_icon = db.Column(db.String(50))  # Lucide icon name
    routes = db.Column(
        db.JSON
    )  # List of route patterns: ["/solutions/*", "/solution-architect/*"]

    # Hierarchy
    parent_id = db.Column(db.Integer, db.ForeignKey("feature_flags.id"))
    parent = db.relationship("FeatureFlag", remote_side=[id], backref="children")

    # Admin UI
    sort_order = db.Column(db.Integer, default=0)

    # Audit (no foreign key - user table may not exist)
    last_modified_by = db.Column(db.Integer)

    def __repr__(self):
        return f"<FeatureFlag {self.key} ({self.state.value}, {'enabled' if self.enabled else 'disabled'})>"

    @property
    def is_active(self) -> bool:
        """Check if feature is currently active."""
        if self.state == FeatureState.LABS:
            return self.enabled  # LABS features require explicit enable
        return self.enabled and self.state not in [
            FeatureState.DEPRECATED,
            FeatureState.MAINTENANCE_MODE,
        ]

    @property
    def sidebar_css_classes(self) -> str:
        """CSS classes for sidebar display based on state."""
        if not self.enabled or self.state == FeatureState.MAINTENANCE_MODE:
            return "opacity-50 cursor-not-allowed"
        elif self.state == FeatureState.LABS:
            return "opacity-80"
        elif self.state == FeatureState.ALPHA:
            return "opacity-75"
        elif self.state == FeatureState.DEPRECATED:
            return "opacity-60 line-through"
        return ""

    @property
    def state_badge_html(self) -> Markup:
        """HTML badge for state display. Returns Markup-safe string."""
        badge_colors = {
            FeatureState.ALPHA: "bg-yellow-500/10 text-yellow-500",
            FeatureState.LABS: "bg-violet-500/10 text-violet-500",
            FeatureState.BETA: "bg-blue-500/10 text-blue-500",
            FeatureState.STABLE: "bg-green-500/10 text-green-500",
            FeatureState.DEPRECATED: "bg-red-500/10 text-red-500",
            FeatureState.MAINTENANCE_MODE: "bg-orange-500/10 text-orange-500",
        }
        color = badge_colors.get(self.state, "bg-gray-500/10 text-gray-500")
        label = escape(self.state.value)
        return Markup(f'<span class="inline-flex items-center rounded-full px-2 py-1 text-xs font-medium {color}">{label}</span>')  # raw-html-ok: color is a fixed dict value above; label is escape()'d one line above

    def matches_route(self, route_path: str) -> bool:
        """Check if this feature flag applies to a route path.

        Args:
            route_path: Route path to check (e.g., '/solutions/123')

        Returns:
            True if feature flag applies to this route
        """
        if not self.routes:
            return False

        for pattern in self.routes:
            # Wildcard matching
            if pattern.endswith("*"):
                if route_path.startswith(pattern[:-1]):
                    return True
            # Exact match
            elif pattern == route_path:
                return True

        return False

    @classmethod
    def clear_cache(cls, key: str = None):
        """Clear feature flag cache.

        Args:
            key: Specific feature key to clear, or None to clear all
        """
        global _cache_metrics
        if key:
            if key in _feature_cache:
                _feature_cache.pop(key)
                _cache_metrics["invalidations"] += 1
        else:
            count = len(_feature_cache)
            _feature_cache.clear()
            _cache_metrics["invalidations"] += count

    @classmethod
    def is_feature_enabled(cls, key: str) -> bool:
        """Check if a feature is enabled by key.

        Args:
            key: Feature key to check

        Returns:
            True if feature exists and is enabled, False otherwise
        """
        global _cache_metrics

        # Check cache first
        current_time = time.time()
        if key in _feature_cache:
            is_active, timestamp = _feature_cache[key]
            if current_time - timestamp < _CACHE_TTL:
                _cache_metrics["hits"] += 1
                return is_active
            else:
                # Cache expired
                _cache_metrics["evictions"] += 1

        # Cache miss or expired - query database
        _cache_metrics["misses"] += 1
        feature = cls.query.filter_by(key=key).first()
        if not feature:
            # Feature not in database = enabled by default (backward compatibility)
            result = True
        else:
            result = feature.is_active

        # Update cache
        _feature_cache[key] = (result, current_time)
        return result

    @classmethod
    def get_cache_metrics(cls) -> dict:
        """Get cache performance metrics.

        Returns:
            Dictionary with cache statistics including:
            - hits: Number of cache hits
            - misses: Number of cache misses
            - evictions: Number of expired entries
            - invalidations: Number of manual cache clears
            - size: Current cache size
            - hit_rate: Cache hit ratio (0-1)
        """
        total_requests = _cache_metrics["hits"] + _cache_metrics["misses"]
        hit_rate = (
            _cache_metrics["hits"] / total_requests if total_requests > 0 else 0.0
        )

        return {
            "hits": _cache_metrics["hits"],
            "misses": _cache_metrics["misses"],
            "evictions": _cache_metrics["evictions"],
            "invalidations": _cache_metrics["invalidations"],
            "size": len(_feature_cache),
            "hit_rate": round(hit_rate, 4),
            "total_requests": total_requests,
        }

    @classmethod
    def get_route_feature(cls, route_path: str):
        """Get the feature flag that controls a specific route.

        Args:
            route_path: Route path to check

        Returns:
            FeatureFlag instance or None
        """
        features = cls.query.filter(cls.routes.isnot(None)).all()
        for feature in features:
            if feature.matches_route(route_path):
                return feature
        return None

    @classmethod
    def get_sidebar_features(cls):
        """Get all sidebar section features ordered by sort_order.

        Returns:
            Query of FeatureFlag instances for sidebar sections
        """
        return (
            cls.query.filter_by(feature_type=FeatureType.SIDEBAR_SECTION)
            .order_by(cls.sort_order, cls.name)
            .all()
        )

    def to_dict(self):
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "feature_type": self.feature_type.value,
            "state": self.state.value,
            "enabled": self.enabled,
            "is_active": self.is_active,
            "sidebar_label": self.sidebar_label,
            "sidebar_icon": self.sidebar_icon,
            "routes": self.routes,
            "parent_id": self.parent_id,
            "sort_order": self.sort_order,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
