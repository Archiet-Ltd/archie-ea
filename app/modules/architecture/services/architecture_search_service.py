"""
-> app.modules.architecture.services.governance_service

rchitecture search and filtering service."""

import logging
from typing import List, Tuple
from sqlalchemy import or_

from app.extensions import db
from app.models.archimate_core import ArchiMateElement as ArchitectureElement

logger = logging.getLogger(__name__)


class ArchitectureSearchService:
    """Search and filter architecture elements."""
    
    @staticmethod
    def search(
        query: str = "",
        element_type: str = None,
        layer: str = None,
        page: int = 1,
        per_page: int = 20,
    ) -> Tuple[List, int]:
        """Search elements with filters.
        
        Returns: (results, total_count)
        """
        q = ArchitectureElement.query
        
        # Full-text search
        if query:
            search_term = f"%{query}%"
            q = q.filter(
                or_(
                    ArchitectureElement.name.ilike(search_term),
                    # `type`, not `element_type`: the mapped column on
                    # ArchiMateElement is `type`, and this attribute raised
                    # AttributeError on EVERY non-empty query -- so
                    # /architecture/search?q=anything was a 500 while the
                    # bare page rendered 200 and every gate stayed green.
                    ArchitectureElement.type.ilike(search_term),
                    ArchitectureElement.description.ilike(search_term),
                )
            )
        
        # Filter by type
        if element_type:
            q = q.filter_by(type=element_type)
        
        # Filter by layer
        if layer:
            q = q.filter_by(layer=layer)
        
        # Paginate
        total = q.count()
        results = q.offset((page - 1) * per_page).limit(per_page).all()
        
        return results, total
    
    @staticmethod
    def filter_by_layer(layer: str) -> List:
        """Get all elements in a layer."""
        return ArchitectureElement.query.filter_by(layer=layer).all()
    
    @staticmethod
    def filter_by_type(element_type: str) -> List:
        """Get all elements of a type."""
        return ArchitectureElement.query.filter_by(type=element_type).all()
    
    @staticmethod
    def get_statistics() -> dict:
        """Get architecture statistics."""
        total_elements = ArchitectureElement.query.count()
        
        # Count by layer
        by_layer = {}
        for layer in ["business", "application", "technology"]:
            count = ArchitectureElement.query.filter_by(layer=layer).count()
            by_layer[layer] = count
        
        # Count by type (top 5)
        type_counts = db.session.query(
            ArchitectureElement.type,
            db.func.count(ArchitectureElement.id).label("count")
        ).group_by(ArchitectureElement.type).order_by(
            db.func.count(ArchitectureElement.id).desc()
        ).limit(5).all()
        
        by_type = {t[0]: t[1] for t in type_counts}
        
        return {
            "total_elements": total_elements,
            "by_layer": by_layer,
            "by_type": by_type,
        }


def apply_element_search_filters(query, name="", elem_type="", layer=""):
    """Apply the shared element search filter chain: name ilike, type exact,
    layer ilike. Returns the query with filters applied.

    Both the /api/v1/elements/search route and the session-backed
    /api/elements/search route use this one chain so the two routes can never
    diverge on what "search" means. The session route's solution_id filter is
    applied by the caller after this function returns.
    """
    if name:
        query = query.filter(ArchitectureElement.name.ilike(f"%{name}%"))
    if elem_type:
        query = query.filter(ArchitectureElement.type == elem_type)
    if layer:
        query = query.filter(ArchitectureElement.layer.ilike(layer))
    return query
