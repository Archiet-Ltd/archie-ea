"""
Application Management Blueprint

Dashboard and CRUD operations for various models using the dashboard generator.
"""

from flask import Blueprint

application_mgmt = Blueprint("application_mgmt", __name__, url_prefix="/dashboard")

# Import routes after blueprint creation to avoid circular imports
from . import (
    analytics_routes,  # noqa: F401
    api_data_routes,  # noqa: F401
    apqc_process_api_endpoints,
    application_layer_routes,  # noqa: F401
    archimate_routes,  # noqa: F401
    architecture_api_routes,  # noqa: F401
    business_layer_routes,  # noqa: F401
    compliance_routes,
    framework_catalogue_routes,
    consolidation_routes,  # noqa: F401
    crud_routes,  # noqa: F401
    custom_field_routes,
    dashboard_routes,  # noqa: F401
    detail_layer_routes,  # noqa: F401
    detail_overview_routes,  # noqa: F401
    documents_routes,  # noqa: F401
    impact_routes,
    implementation_layer_routes,  # noqa: F401
    export_routes,  # noqa: F401
    import_routes,  # noqa: F401
    motivation_layer_routes,  # noqa: F401
    physical_layer_routes,  # noqa: F401
    relationship_add_routes,  # noqa: F401
    routes,
    technology_layer_routes,  # noqa: F401
    element_api_routes,  # noqa: F401
    template_api_routes,  # noqa: F401
    vendor_analysis_routes,
    vendor_routes,
)
