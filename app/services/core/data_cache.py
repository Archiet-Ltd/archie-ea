"""
Data Caching Layer for frequently-accessed, rarely-changed data

Provides cached access to:
- Business capabilities and hierarchies
- Application portfolio data
- Vendor catalogs
- Filter/dropdown options

Uses the cache_service infrastructure with appropriate TTLs.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import g
from sqlalchemy.orm import selectinload

from app import db
from app.services.core.cache_service import cache_service

logger = logging.getLogger(__name__)


# =============================================================================
# TTL Constants (in seconds)
# =============================================================================

TTL_CAPABILITIES = 86400  # 24 hours - enterprise configuration, rarely changes
TTL_APPLICATIONS = 21600  # 6 hours - moderate change frequency
TTL_VENDORS = 172800  # 48 hours - very stable external data
TTL_FILTERS = 43200  # 12 hours - static lookup values
TTL_METRICS = 3600  # 1 hour - needs some freshness
TTL_TEMPLATES = 86400  # 24 hours - rarely updated
TTL_REQUEST = 300  # 5 minutes - request-scoped fallback


# =============================================================================
# In-Memory Fallback Cache (when Redis is unavailable)
# =============================================================================


class InMemoryCache:
    """Simple in-memory cache fallback when Redis is not available"""

    def __init__(self, max_size: int = 100):
        self._cache: Dict[str, tuple] = {}  # key -> (value, expiry_timestamp)
        self._max_size = max_size

    def get(self, key: str) -> Optional[Any]:
        if key in self._cache:
            value, expiry = self._cache[key]
            if datetime.utcnow().timestamp() < expiry:
                return value
            else:
                del self._cache[key]
        return None

    def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        # Evict oldest if at capacity
        if len(self._cache) >= self._max_size and key not in self._cache:
            oldest_key = min(self._cache, key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]

        expiry = datetime.utcnow().timestamp() + ttl
        self._cache[key] = (value, expiry)
        return True

    def delete(self, key: str) -> bool:
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def clear(self) -> None:
        self._cache.clear()


# Global fallback cache
_memory_cache = InMemoryCache(max_size=200)


def _get_cached(key: str, ttl: int, fetch_func, use_memory_fallback: bool = True):
    """
    Get value from cache with fallback to memory cache and fresh fetch.

    Args:
        key: Cache key
        ttl: Time-to-live in seconds
        fetch_func: Function to call if cache miss
        use_memory_fallback: Use in-memory cache if Redis unavailable

    Returns:
        Cached or freshly fetched value
    """
    # Try Redis first
    cached = cache_service.get(key)
    if cached is not None:
        return cached

    # Try memory cache fallback
    if use_memory_fallback:
        cached = _memory_cache.get(key)
        if cached is not None:
            logger.debug(f"Memory cache HIT: {key}")
            return cached

    # Cache miss - fetch fresh data
    logger.info(f"Cache MISS, fetching: {key}")
    value = fetch_func()

    # Store in both caches
    cache_service.set(key, value, ttl)
    if use_memory_fallback:
        _memory_cache.set(key, value, ttl)

    return value


# =============================================================================
# Capability Caching
# =============================================================================


def get_all_capabilities(include_inactive: bool = False) -> List[Dict]:
    """
    Get all unified capabilities with hierarchy pre-loaded.

    Cached for 24 hours. Returns serialized dicts for JSON compatibility.
    """
    cache_key = f"capabilities:all:include_inactive={include_inactive}"

    def fetch():
        from app.models.unified_capability import UnifiedCapability

        query = UnifiedCapability.query.options(
            selectinload(UnifiedCapability.children),
            selectinload(UnifiedCapability.application_capability_mappings),
        )

        if not include_inactive:
            query = query.filter(UnifiedCapability.status != "retired")

        capabilities = query.order_by(
            UnifiedCapability.domain_id, UnifiedCapability.level, UnifiedCapability.name
        ).all()

        return [
            cap.to_dict()
            if hasattr(cap, "to_dict")
            else {
                "id": cap.id,
                "name": cap.name,
                "code": getattr(cap, "code", None),
                "description": cap.description,
                "level": cap.level,
                "parent_capability_id": cap.parent_capability_id,
                "domain_id": cap.domain_id,
                "status": getattr(cap, "status", "active"),
                "strategic_importance": getattr(cap, "strategic_importance", None),
                "current_maturity_level": getattr(cap, "current_maturity_level", None),
            }
            for cap in capabilities
        ]

    return _get_cached(cache_key, TTL_CAPABILITIES, fetch)


def get_capability_hierarchy() -> Dict[int, List[Dict]]:
    """
    Get capabilities organized by parent_id for tree building.

    Returns dict: {parent_id: [child_capabilities]}
    """
    cache_key = "capabilities:hierarchy"

    def fetch():
        capabilities = get_all_capabilities()
        hierarchy = {}
        for cap in capabilities:
            parent_id = cap.get("parent_capability_id")
            if parent_id not in hierarchy:
                hierarchy[parent_id] = []
            hierarchy[parent_id].append(cap)
        return hierarchy

    return _get_cached(cache_key, TTL_CAPABILITIES, fetch)


def get_business_capabilities() -> List[Dict]:
    """Get all business capabilities (legacy model support)"""
    cache_key = "business_capabilities:all"

    def fetch():
        from app.models.business_capabilities import BusinessCapability

        capabilities = (
            BusinessCapability.query.options(selectinload(BusinessCapability.children))
            .order_by(BusinessCapability.name)
            .all()
        )

        return [
            cap.to_dict()
            if hasattr(cap, "to_dict")
            else {
                "id": cap.id,
                "name": cap.name,
                "description": getattr(cap, "description", None),
                "parent_id": getattr(cap, "parent_id", None),
                "level": getattr(cap, "level", 0),
            }
            for cap in capabilities
        ]

    return _get_cached(cache_key, TTL_CAPABILITIES, fetch)


# =============================================================================
# Application Portfolio Caching
# =============================================================================


def get_all_applications(include_retired: bool = False) -> List[Dict]:
    """
    Get all application components with key relationships pre-loaded.

    Cached for 6 hours. Returns serialized dicts.
    """
    cache_key = f"applications:all:include_retired={include_retired}"

    def fetch():
        from app.models.application_portfolio import ApplicationComponent

        query = ApplicationComponent.query.options(
            selectinload(ApplicationComponent.capability_mappings),
            # technology_stack is a column, not a relationship
        )

        if not include_retired:
            query = query.filter(ApplicationComponent.lifecycle_status != "retired")

        applications = query.order_by(ApplicationComponent.name).all()

        return [
            app.to_dict()
            if hasattr(app, "to_dict")
            else {
                "id": app.id,
                "name": app.name,
                "description": getattr(app, "description", None),
                "component_type": getattr(app, "component_type", None),
                "deployment_status": getattr(app, "deployment_status", None),
                "lifecycle_status": getattr(app, "lifecycle_status", None),
                "business_criticality": getattr(app, "business_criticality", None),
                "vendor_name": getattr(app, "vendor_name", None),
                "total_cost_of_ownership": getattr(app, "total_cost_of_ownership", None),
            }
            for app in applications
        ]

    return _get_cached(cache_key, TTL_APPLICATIONS, fetch)


def get_application_count() -> int:
    """Get total count of active applications"""
    cache_key = "applications:count"

    def fetch():
        from app.models.application_portfolio import ApplicationComponent

        return ApplicationComponent.query.filter(
            ApplicationComponent.lifecycle_status != "retired"
        ).count()

    return _get_cached(cache_key, TTL_APPLICATIONS, fetch)


def get_applications_by_ids(app_ids: List[int]) -> Dict[int, Dict]:
    """
    Get multiple applications by ID with eager loading.

    Returns dict: {app_id: app_dict}
    """
    if not app_ids:
        return {}

    # For small sets, query directly (caching individual apps is complex)
    from app.models.application_portfolio import ApplicationComponent

    applications = (
        ApplicationComponent.query.options(
            selectinload(ApplicationComponent.capability_mappings).selectinload(
                "business_capability"
            ),
            selectinload(ApplicationComponent.process_mappings).selectinload("business_process"),
        )
        .filter(ApplicationComponent.id.in_(app_ids))
        .all()
    )

    return {
        app.id: app.to_dict() if hasattr(app, "to_dict") else {"id": app.id, "name": app.name}
        for app in applications
    }


# =============================================================================
# APQC Process Caching
# =============================================================================


def get_all_apqc_processes() -> List[Dict]:
    """
    Get all APQC processes with hierarchy pre-loaded.

    Cached for 24 hours. Returns serialized dicts.
    """
    cache_key = "apqc:processes:all"

    def fetch():
        from app.models.apqc_process import APQCProcess, sort_processes_naturally

        processes = sort_processes_naturally(APQCProcess.query.order_by(APQCProcess.id).all())

        return [
            p.to_dict()
            if hasattr(p, "to_dict")
            else {
                "id": p.id,
                "process_code": p.process_code,
                "process_name": p.process_name,
                "description": getattr(p, "description", None),
                "level": getattr(p, "level", None),
                "parent_id": getattr(p, "parent_id", None),
            }
            for p in processes
        ]

    return _get_cached(cache_key, TTL_CAPABILITIES, fetch)


def get_apqc_by_code() -> Dict[str, Dict]:
    """Get APQC processes indexed by process_code for quick lookups"""
    cache_key = "apqc:by_code"

    def fetch():
        processes = get_all_apqc_processes()
        return {p["process_code"]: p for p in processes if p.get("process_code")}

    return _get_cached(cache_key, TTL_CAPABILITIES, fetch)


def get_apqc_by_name() -> Dict[str, Dict]:
    """Get APQC processes indexed by lowercase name for quick lookups"""
    cache_key = "apqc:by_name"

    def fetch():
        processes = get_all_apqc_processes()
        return {p["process_name"].lower(): p for p in processes if p.get("process_name")}

    return _get_cached(cache_key, TTL_CAPABILITIES, fetch)


def invalidate_apqc_cache():
    """Invalidate APQC-related caches"""
    cache_service.delete_pattern("apqc:*")
    _memory_cache.clear()
    logger.info("APQC cache invalidated")


# =============================================================================
# Vendor Caching
# =============================================================================


def get_all_vendors() -> List[Dict]:
    """Get all vendor organizations"""
    cache_key = "vendors:all"

    def fetch():
        from app.models.vendor.vendor_organization import VendorOrganization

        vendors = VendorOrganization.query.order_by(VendorOrganization.name).all()

        return [
            v.to_dict()
            if hasattr(v, "to_dict")
            else {
                "id": v.id,
                "name": v.name,
                "vendor_type": getattr(v, "vendor_type", None),
                "status": getattr(v, "status", None),
            }
            for v in vendors
        ]

    return _get_cached(cache_key, TTL_VENDORS, fetch)


# =============================================================================
# Filter/Dropdown Options Caching
# =============================================================================


def get_application_filter_options() -> Dict[str, List[str]]:
    """
    Get distinct values for application filter dropdowns.

    Returns dict with keys: component_types, deployment_statuses,
    lifecycle_statuses, criticalities, vendors
    """
    cache_key = "filters:application_options"

    def fetch():
        from app.models.application_portfolio import ApplicationComponent

        return {
            "component_types": [
                r[0]
                for r in db.session.query(ApplicationComponent.component_type).distinct().all()
                if r[0]
            ],
            "deployment_statuses": [
                r[0]
                for r in db.session.query(ApplicationComponent.deployment_status).distinct().all()
                if r[0]
            ],
            "lifecycle_statuses": [
                r[0]
                for r in db.session.query(ApplicationComponent.lifecycle_status).distinct().all()
                if r[0]
            ],
            "criticalities": [
                r[0]
                for r in db.session.query(ApplicationComponent.business_criticality)
                .distinct()
                .all()
                if r[0]
            ],
            "vendors": [
                r[0]
                for r in db.session.query(ApplicationComponent.vendor_name)
                .distinct()
                .order_by(ApplicationComponent.vendor_name)
                .all()
                if r[0]
            ],
        }

    return _get_cached(cache_key, TTL_FILTERS, fetch)


def get_capability_filter_options() -> Dict[str, List]:
    """Get distinct values for capability filter dropdowns"""
    cache_key = "filters:capability_options"

    def fetch():
        from app.models.unified_capability import UnifiedCapability

        return {
            "maturity_levels": [
                r[0]
                for r in db.session.query(UnifiedCapability.current_maturity_level).distinct().all()
                if r[0]
            ],
            "statuses": [
                r[0] for r in db.session.query(UnifiedCapability.status).distinct().all() if r[0]
            ],
            "domains": [
                r[0] for r in db.session.query(UnifiedCapability.domain_id).distinct().all() if r[0]
            ],
        }

    return _get_cached(cache_key, TTL_FILTERS, fetch)


# =============================================================================
# Prompt Template Caching
# =============================================================================


def get_prompt_templates() -> List[Dict]:
    """Get all AI prompt templates"""
    cache_key = "templates:prompts"

    def fetch():
        try:
            from app.models.ai_chat_models import AIPromptTemplate

            templates = (
                AIPromptTemplate.query.filter_by(is_active=True)
                .order_by(AIPromptTemplate.name)
                .all()
            )

            return [
                t.to_dict()
                if hasattr(t, "to_dict")
                else {
                    "id": t.id,
                    "name": t.name,
                    "template": getattr(t, "template", None),
                    "domain": getattr(t, "domain", None),
                }
                for t in templates
            ]
        except Exception as e:
            logger.warning(f"Could not load prompt templates: {e}")
            return []

    return _get_cached(cache_key, TTL_TEMPLATES, fetch)


# =============================================================================
# Cache Invalidation
# =============================================================================


def invalidate_capability_cache():
    """Invalidate all capability-related caches"""
    cache_service.delete_pattern("capabilities:*")
    cache_service.delete_pattern("business_capabilities:*")
    cache_service.delete_pattern("filters:capability_options")
    _memory_cache.clear()
    logger.info("Capability cache invalidated")


def invalidate_application_cache():
    """Invalidate all application-related caches"""
    cache_service.delete_pattern("applications:*")
    cache_service.delete_pattern("filters:application_options")
    _memory_cache.clear()
    logger.info("Application cache invalidated")


def invalidate_vendor_cache():
    """Invalidate vendor-related caches"""
    cache_service.delete_pattern("vendors:*")
    _memory_cache.clear()
    logger.info("Vendor cache invalidated")


def invalidate_all_caches():
    """Nuclear option - invalidate everything"""
    cache_service.delete_pattern("*")
    _memory_cache.clear()
    logger.info("All caches invalidated")


# =============================================================================
# Request-Scoped Cache (using Flask's g object)
# =============================================================================


def request_cached(func):
    """
    Decorator for request-scoped caching.

    Caches function results for the duration of the current request.
    Useful for data that's accessed multiple times within a single request.
    """
    from functools import wraps

    @wraps(func)
    def wrapper(*args, **kwargs):
        # Initialize request cache if needed
        if not hasattr(g, "_request_cache"):
            g._request_cache = {}

        # Generate cache key
        cache_key = f"{func.__module__}.{func.__name__}:{str(args)}:{str(sorted(kwargs.items()))}"

        if cache_key in g._request_cache:
            return g._request_cache[cache_key]

        result = func(*args, **kwargs)
        g._request_cache[cache_key] = result
        return result

    return wrapper


# =============================================================================
# Statistics and Monitoring
# =============================================================================


def get_cache_stats() -> Dict[str, Any]:
    """Get comprehensive cache statistics"""
    redis_stats = cache_service.get_stats()

    return {
        "redis": redis_stats,
        "memory_cache": {"size": len(_memory_cache._cache), "max_size": _memory_cache._max_size},
        "ttl_settings": {
            "capabilities": TTL_CAPABILITIES,
            "applications": TTL_APPLICATIONS,
            "vendors": TTL_VENDORS,
            "filters": TTL_FILTERS,
            "metrics": TTL_METRICS,
        },
    }
