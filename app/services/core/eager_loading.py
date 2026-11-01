"""
Eager Loading Utilities for N + 1 Query Prevention

Provides factory functions to create query options for common relationship patterns.
Use these in routes and services to avoid N + 1 query problems.

Example:
    from app.services.core.eager_loading import get_application_options

    # Instead of: apps = ApplicationComponent.query.all()
    apps = ApplicationComponent.query.options(
        *get_application_options('with_capabilities')
    ).all()
"""

from sqlalchemy.orm import joinedload, selectinload


def get_application_options(preset="basic"):
    """
    Get eager loading options for ApplicationComponent queries.

    Presets:
        - 'basic': Just technology_stack
        - 'with_capabilities': Include capability mappings
        - 'with_processes': Include process mappings
        - 'full_analysis': All relationships for duplicate detection
        - 'dashboard': For portfolio dashboards
    """
    from app.models.application_portfolio import ApplicationComponent

    presets = {
        "basic": [
            # technology_stack is a column, not a relationship - no eager loading needed
        ],
        "with_capabilities": [
            selectinload(ApplicationComponent.capability_mappings),
        ],
        "with_processes": [
            selectinload(ApplicationComponent.process_mappings),
        ],
        "full_analysis": [
            selectinload(ApplicationComponent.capability_mappings),
            selectinload(ApplicationComponent.process_mappings),
        ],
        "dashboard": [
            selectinload(ApplicationComponent.capability_mappings),
        ],
    }

    return presets.get(preset, presets["basic"])


def get_capability_options(preset="basic"):
    """
    Get eager loading options for UnifiedCapability queries.

    Presets:
        - 'basic': No extra loading
        - 'with_children': Include child capabilities
        - 'with_parent': Include parent capability
        - 'full_hierarchy': Both children and parent
        - 'with_applications': Include application mappings
        - 'map_view': For capability map visualization
    """
    from app.models.unified_capability import UnifiedCapability

    presets = {
        "basic": [],
        "with_children": [
            selectinload(UnifiedCapability.children),
        ],
        "with_parent": [
            joinedload(UnifiedCapability.parent_capability),
        ],
        "full_hierarchy": [
            selectinload(UnifiedCapability.children),
            joinedload(UnifiedCapability.parent_capability),
        ],
        "with_applications": [
            selectinload(UnifiedCapability.application_capability_mappings),
        ],
        "map_view": [
            selectinload(UnifiedCapability.children),
            selectinload(UnifiedCapability.application_capability_mappings),
        ],
    }

    return presets.get(preset, presets["basic"])


# Placeholder classes for backwards compatibility and documentation
class ApplicationQueryOptions:
    """DEPRECATED: Use get_application_options() instead"""

    pass


class CapabilityQueryOptions:
    """DEPRECATED: Use get_capability_options() instead"""

    pass


class ArchiMateQueryOptions:
    """Placeholder for ArchiMate eager loading options"""

    pass


class VendorQueryOptions:
    """Placeholder for vendor eager loading options"""

    pass


class DuplicateDetectionOptions:
    """
    Options for duplicate detection queries.

    Use DuplicateDetectionOptions.get_comparison_data() to get options.
    """

    @staticmethod
    def get_comparison_data():
        """Get options for loading applications with all comparison data"""
        from app.models.application_portfolio import ApplicationComponent

        return [
            selectinload(ApplicationComponent.capability_mappings),
            selectinload(ApplicationComponent.process_mappings),
        ]

    # Class attribute for backwards compatibility
    COMPARISON_DATA = None  # Will be populated on first access


class GapAnalysisOptions:
    """Placeholder for gap analysis eager loading options"""

    pass


# =============================================================================
# Helper Functions
# =============================================================================


def apply_eager_loading(query, *option_lists):
    """
    Apply multiple option lists to a query.

    Example:
        query = apply_eager_loading(
            ApplicationComponent.query,
            ApplicationQueryOptions.WITH_CAPABILITIES,
            ApplicationQueryOptions.WITH_PROCESSES
        )
    """
    for options in option_lists:
        for opt in options:
            query = query.options(opt)
    return query


def get_applications_for_analysis(app_ids=None):
    """
    Load applications with all data needed for similarity/duplicate analysis.

    Returns dict: {app_id: ApplicationComponent}
    """
    from app.models.application_portfolio import ApplicationComponent

    query = ApplicationComponent.query.options(*DuplicateDetectionOptions.COMPARISON_DATA)

    if app_ids:
        query = query.filter(ApplicationComponent.id.in_(app_ids))

    applications = query.all()
    return {app.id: app for app in applications}


def get_capabilities_for_map(domain_id=None):
    """
    Load capabilities with hierarchy for map visualization.

    Returns list of UnifiedCapability objects with children pre-loaded.
    """
    from app.models.unified_capability import UnifiedCapability

    query = UnifiedCapability.active().options(*CapabilityQueryOptions.MAP_VIEW)

    if domain_id:
        query = query.filter(UnifiedCapability.domain_id == domain_id)

    return query.order_by(UnifiedCapability.level, UnifiedCapability.name).all()


def get_archimate_elements_for_diagram(architecture_id):
    """
    Load ArchiMate elements with relationships for diagram rendering.

    Returns list of ArchiMateElement objects with relationships pre-loaded.
    """
    from app.models.models import ArchiMateElement

    return (
        ArchiMateElement.query.options(*ArchiMateQueryOptions.FULL)
        .filter(ArchiMateElement.architecture_id == architecture_id)
        .all()
    )


# =============================================================================
# Batch Loading Utilities
# =============================================================================


def batch_load_applications(batch_size=500):
    """
    Generator that loads applications in batches to avoid memory issues.

    Usage:
        for batch in batch_load_applications(batch_size=100):
            for app in batch:
                process(app)
    """
    from app.models.application_portfolio import ApplicationComponent

    offset = 0
    while True:
        batch = (
            ApplicationComponent.query.options(*ApplicationQueryOptions.BASIC)
            .order_by(ApplicationComponent.id)
            .offset(offset)
            .limit(batch_size)
            .all()
        )

        if not batch:
            break

        yield batch
        offset += batch_size


def batch_load_capabilities(batch_size=500):
    """Generator that loads capabilities in batches"""
    from app.models.unified_capability import UnifiedCapability

    offset = 0
    while True:
        batch = (
            UnifiedCapability.active().options(*CapabilityQueryOptions.WITH_CHILDREN)
            .order_by(UnifiedCapability.id)
            .offset(offset)
            .limit(batch_size)
            .all()
        )

        if not batch:
            break

        yield batch
        offset += batch_size
