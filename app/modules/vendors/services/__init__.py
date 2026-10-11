"""
Vendor services — consolidated from 35 legacy files (~750KB) into 5 focused modules.

Modules:
- vendor_service:      Core CRUD, unified vendor ops, product service, onboarding
- analysis_service:    Analysis, scoring, research, comparison, risk assessment
- discovery_service:   Discovery engine, semantic search, MDM, data population
- integration_service: ArchiMate sync, capability links, process mapping, deployment
- seeder_service:      Seeders, validators, importers, data population

Usage::

    from app.modules.vendors.services import (
        UnifiedVendorServices,
        VendorProductService,
        VendorComparisonService,
        VendorDiscoveryEngine,
        VendorProcessMappingService,
    )
"""

from app.modules.vendors.services.analysis_service import (  # noqa: F401
    VendorAnalysisService,
    VendorComparisonService,
    VendorRiskService,
)
from app.modules.vendors.services.discovery_service import (  # noqa: F401
    VendorDiscoveryEngine,
    VendorMDMService,
)
from app.modules.vendors.services.integration_service import (  # noqa: F401
    VendorProcessMappingService,
)
from app.modules.vendors.services.vendor_service import (  # noqa: F401
    UnifiedVendorServices,
    VendorProductService,
)
from app.modules.vendors.services.vendor_merge_service import VendorMergeService  # noqa: F401

__all__ = [
    "UnifiedVendorServices",
    "VendorProductService",
    "VendorAnalysisService",
    "VendorComparisonService",
    "VendorRiskService",
    "VendorDiscoveryEngine",
    "VendorMDMService",
    "VendorProcessMappingService",
    "VendorMergeService",
]
