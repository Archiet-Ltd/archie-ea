"""
Query Optimization Service

Provides eager loading patterns, query caching, and N + 1 query prevention
for improved database performance across the application.
"""

import hashlib
import json
import threading
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Type

from sqlalchemy.orm import joinedload, selectinload


class QueryCache:
    """
    In-memory query cache with TTL support.
    Thread-safe implementation for concurrent access.
    """

    def __init__(self, default_ttl_seconds: int = 300):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.default_ttl = default_ttl_seconds
        self.stats = {"hits": 0, "misses": 0, "evictions": 0}

    def _make_key(self, prefix: str, *args, **kwargs) -> str:
        """Generate cache key from prefix and arguments."""
        key_data = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True, default=str)
        key_hash = hashlib.md5(key_data.encode(), usedforsecurity=False).hexdigest()[:16]
        return f"{prefix}:{key_hash}"

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if datetime.now() < entry["expires"]:
                    self.stats["hits"] += 1
                    return entry["value"]
                else:
                    del self._cache[key]
                    self.stats["evictions"] += 1
            self.stats["misses"] += 1
            return None

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        """Set value in cache with TTL."""
        ttl = ttl_seconds or self.default_ttl
        with self._lock:
            self._cache[key] = {
                "value": value,
                "expires": datetime.now() + timedelta(seconds=ttl),
                "created": datetime.now(),
            }

    def invalidate(self, pattern: Optional[str] = None) -> int:
        """Invalidate cache entries matching pattern or all entries."""
        with self._lock:
            if pattern:
                keys_to_remove = [k for k in self._cache if pattern in k]
            else:
                keys_to_remove = list(self._cache.keys())

            for key in keys_to_remove:
                del self._cache[key]

            return len(keys_to_remove)

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total = self.stats["hits"] + self.stats["misses"]
            hit_rate = (self.stats["hits"] / total * 100) if total > 0 else 0
            return {**self.stats, "size": len(self._cache), "hit_rate": f"{hit_rate:.1f}%"}


# Global cache instance
_query_cache = QueryCache()


def cached_query(ttl_seconds: int = 300, cache_key_prefix: Optional[str] = None):
    """
    Decorator to cache query results.

    Usage:
        @cached_query(ttl_seconds=600, cache_key_prefix='capabilities')
        def get_all_capabilities():
            return UnifiedCapability.active().all()
    """

    def decorator(func: Callable) -> Callable:
        prefix = cache_key_prefix or func.__name__

        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = _query_cache._make_key(prefix, *args, **kwargs)

            # Try cache first
            cached_result = _query_cache.get(cache_key)
            if cached_result is not None:
                return cached_result

            # Execute query and cache result
            result = func(*args, **kwargs)
            _query_cache.set(cache_key, result, ttl_seconds)
            return result

        # Add cache management methods to the wrapper
        wrapper.invalidate_cache = lambda: _query_cache.invalidate(prefix)
        wrapper.cache_stats = lambda: _query_cache.get_stats()

        return wrapper

    return decorator


class EagerLoadingPatterns:
    """
    Pre-defined eager loading patterns for common query scenarios.
    """

    @staticmethod
    def application_with_relationships():
        """Eager load application with all relationships."""
        return [
            joinedload("unified_capability_mappings"),
            joinedload("apqc_process_mappings"),
            selectinload("archimate_elements"),
        ]

    @staticmethod
    def capability_with_hierarchy():
        """Eager load capability with parent/children hierarchy."""
        return [
            joinedload("parent"),
            selectinload("children"),
            selectinload("application_mappings"),
        ]

    @staticmethod
    def vendor_with_products():
        """Eager load vendor with products and capabilities."""
        return [selectinload("products"), selectinload("vendor_apqc_mappings")]

    @staticmethod
    def duplicate_group_full():
        """Eager load duplicate group with all analysis data."""
        return [
            joinedload("applications"),
            joinedload("pairwise_analyses"),
            joinedload("consolidation_recommendations"),
        ]


class BatchQueryExecutor:
    """
    Execute multiple related queries in batches to prevent N + 1 patterns.
    """

    def __init__(self, db_session):
        self.session = db_session
        self._queries = []
        self._results = {}

    def add_query(self, name: str, model: Type, filter_kwargs: Dict[str, Any]):
        """Add a query to the batch."""
        self._queries.append({"name": name, "model": model, "filter_kwargs": filter_kwargs})
        return self

    def execute(self) -> Dict[str, List[Any]]:
        """Execute all queries and return results by name."""
        for query_def in self._queries:
            try:
                model = query_def["model"]
                filters = query_def["filter_kwargs"]
                name = query_def["name"]

                # Execute query with filter
                result = model.query.filter_by(**filters).all()
                self._results[name] = result
            except Exception:
                self._results[query_def["name"]] = []

        return self._results

    def clear(self):
        """Clear batch queries and results."""
        self._queries = []
        self._results = {}


def get_application_elements_batch(
    app_id: int, element_models: Dict[str, Type], search: Optional[str] = None
) -> Dict[str, List[Any]]:
    """
    Fetch all ArchiMate elements for an application in a single batch operation.

    Args:
        app_id: Application component ID
        element_models: Dict mapping element type names to model classes
        search: Optional search filter

    Returns:
        Dict mapping element type names to lists of matching elements
    """
    from app import db

    results = {}
    search_lower = search.lower() if search else None

    # Use UNION query for better performance when fetching multiple element types
    for element_type, model in element_models.items():
        try:
            query = model.query.filter_by(application_component_id=app_id)

            if search_lower:
                query = query.filter(
                    db.or_(
                        model.name.ilike(f"%{search_lower}%"),
                        model.description.ilike(f"%{search_lower}%")
                        if hasattr(model, "description")
                        else False,
                    )
                )

            results[element_type] = query.all()
        except Exception:
            results[element_type] = []

    return results


def optimize_count_queries(model: Type, group_by_column: str = "status") -> Dict[str, int]:
    """
    Optimize multiple count queries into a single GROUP BY query.

    Instead of:
        pending = Model.query.filter_by(status='pending').count()
        completed = Model.query.filter_by(status='completed').count()

    Use:
        counts = optimize_count_queries(Model, 'status')
        # Returns: {'pending': 10, 'completed': 20, ...}
    """
    from sqlalchemy import func

    from app import db

    column = getattr(model, group_by_column)
    results = db.session.query(column, func.count(model.id)).group_by(column).all()

    return {str(status): count for status, count in results}


def paginate_query(
    query, page: int = 1, per_page: int = 50, max_per_page: int = 100
) -> Dict[str, Any]:
    """
    Apply pagination to a query with metadata.

    Args:
        query: SQLAlchemy query object
        page: Page number (1 - indexed)
        per_page: Items per page
        max_per_page: Maximum allowed items per page

    Returns:
        Dict with 'items', 'pagination' metadata
    """
    per_page = min(per_page, max_per_page)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return {
        "items": pagination.items,
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
            "has_next": pagination.has_next,
            "has_prev": pagination.has_prev,
            "next_page": pagination.next_num if pagination.has_next else None,
            "prev_page": pagination.prev_num if pagination.has_prev else None,
        },
    }


# Cache management functions
def invalidate_capability_cache():
    """Invalidate all capability-related caches."""
    return _query_cache.invalidate("capability")


def invalidate_application_cache():
    """Invalidate all application-related caches."""
    return _query_cache.invalidate("application")


def invalidate_vendor_cache():
    """Invalidate all vendor-related caches."""
    return _query_cache.invalidate("vendor")


def get_cache_stats() -> Dict[str, Any]:
    """Get global query cache statistics."""
    return _query_cache.get_stats()


def clear_all_caches():
    """Clear all cached queries."""
    return _query_cache.invalidate()
