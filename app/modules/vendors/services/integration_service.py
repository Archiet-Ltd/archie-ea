"""
Vendor integration service — imports from inlined canonical sources.

Consolidates:
- vendor_process_mapping_service (VendorProcessMappingService)
- unified_vendor_process_service (UnifiedVendorProcessService)
- vendor_capability_link_service (VendorCapabilityLinkService)
- vendor_product_archimate_generator (VendorProductArchiMateGenerator)
- apqc_vendor_archimate_service (APQCVendorArchiMateService)
- vendor_deployment_service (VendorDeploymentService)
- application_vendor_mapping_service (ApplicationVendorMappingService)
- solution_vendor_integration_service (SolutionVendorIntegrationService)
"""

from app.modules.vendors.services.vendor_process_mapping_service import (  # noqa: F401
    VendorProcessMappingService,
)

# Unified process service — has broken import (app.models.capability_process_mapping), lazy-load
try:
    from app.modules.vendors.services.unified_vendor_process_service import (  # noqa: F401
        UnifiedVendorProcessService,
    )
except ImportError:
    UnifiedVendorProcessService = None  # type: ignore[assignment,misc]

from app.modules.vendors.services.vendor_capability_link_service import (  # noqa: F401
    LinkResult,
    VendorCapabilityLinkService,
)

from app.modules.vendors.services.vendor_product_archimate_generator import (  # noqa: F401
    VendorProductArchiMateGenerator,
    generate_vendor_archimate_portfolio,
)

from app.modules.vendors.services.apqc_vendor_archimate_service import (  # noqa: F401
    APQCVendorArchiMateService,
)

from app.modules.vendors.services.vendor_deployment_service import (  # noqa: F401
    VendorDeploymentService,
    deploy_vendor_product,
)

from app.modules.vendors.services.application_vendor_mapping_service import (  # noqa: F401
    ApplicationVendorMappingService,
    get_application_vendor_mapping_service,
)

from app.modules.vendors.services.solution_vendor_integration_service import (  # noqa: F401
    SolutionVendorIntegrationService,
)
