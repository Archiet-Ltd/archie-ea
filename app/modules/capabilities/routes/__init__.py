"""
Capability Map routes — decomposed from capability_map_routes.py (6,562 lines).

The ``capability_map`` Blueprint is defined here and shared across all
sub-modules. Each sub-module imports it and registers its routes.

Sub-modules:
- map_views:            Template-rendering views (index, hierarchy, network, etc.)
- mapping_routes:       Core mapping CRUD, suggestions, bulk ops, statistics
- export_routes:        CSV/JSON/image export
- process_routes:       Process-capability integration & traceability
- domain_routes:        Unified/manufacturing/process domain management
- acm_map_routes:       ACM technical capability operations within capability map
- roadmap_routes:       Roadmap gap analysis & work packages
- archimate_cap_routes: ArchiMate elements, APQC requirements & mappings
- vendor_matrix_routes: Vendor-capability matrix & risk API
"""

from flask import Blueprint

capability_map = Blueprint("capability_map", __name__)

# Import sub-modules to register their routes on the shared blueprint.
# Order matches the original monolith's route order for traceability.
from . import (  # noqa: F401, E402
    map_views,
    mapping_routes,
    export_routes,
    process_routes,
    domain_routes,
    acm_map_routes,
    roadmap_routes,
    archimate_cap_routes,
    vendor_matrix_routes,
    tree_routes,
    ownership_routes,
)
