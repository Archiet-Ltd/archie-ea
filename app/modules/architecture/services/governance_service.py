"""
Architecture governance service — imports from inlined canonical sources.

Consolidates governance, monitoring, gaps, and implementation:
- app.services.architecture_governance_service (ArchitectureGovernanceService — 26KB)
- app.modules.architecture.services.architecture_monitoring_service (ArchitectureMonitoringService)
- app.services.architecture_validation_service (ArchitectureValidationService — 7KB)
- app.services.architecture_import_export_service (ArchitectureImportExportService — 7KB)
- app.services.architecture_search_service (ArchitectureSearchService — 2KB)
- app.services.gap_archimate_service (GapArchiMateService — 20KB)
- app.services.archimate.gap_resolution_service (GapResolutionService — 12KB)
- app.services.archimate.agentic_gap_implementation_service (AgenticGapImplementationService — 79KB)
- app.services.archimate.implementation_migration_service (ImplementationMigrationService — 30KB)
- app.services.archimate.implementation_wizard_service (ImplementationWizardService — 17KB)
- app.services.archimate.implementation_context_engine (ImplementationContextEngine — 19KB)
- app.services.archimate.options_analysis_service (OptionsAnalysisService — 13KB)
- app.services.architect_workflow_service (ArchitectWorkflowService — 13KB)
- app.services.solution_architect_orchestrator (SolutionArchitectOrchestrator — 20KB)
"""

from app.modules.architecture.services.architecture_governance_service import (  # noqa: F401
    ArchitectureGovernanceService,
)

from app.modules.architecture.services.architecture_monitoring_service import (  # noqa: F401
    ArchitectureMonitoringService,
)

from app.modules.architecture.services.architecture_validation_service import (  # noqa: F401
    ArchitectureValidator,
)

from app.modules.architecture.services.architecture_import_export_service import (  # noqa: F401
    ArchitectureImportExportService,
)

from app.modules.architecture.services.architecture_search_service import (  # noqa: F401
    ArchitectureSearchService,
)

from app.modules.architecture.services.gap_archimate_service import (  # noqa: F401
    GapArchiMateService,
)

from app.modules.architecture.services.gap_resolution_service import (  # noqa: F401
    GapResolutionService,
)

from app.modules.architecture.services.agentic_gap_implementation_service import (  # noqa: F401
    AgenticGapImplementationService,
)

from app.modules.architecture.services.implementation_migration_service import (  # noqa: F401
    ImplementationMigrationService,
)

from app.modules.architecture.services.implementation_wizard_service import (  # noqa: F401
    ImplementationWizardService,
)

from app.modules.architecture.services.implementation_context_engine import (  # noqa: F401
    ImplementationContextEngine,
)

from app.modules.architecture.services.options_analysis_service import (  # noqa: F401
    OptionsAnalysisService,
)

from app.modules.architecture.services.architect_workflow_service import (  # noqa: F401
    ArchitectWorkflowService,
)

from app.modules.architecture.services.solution_architect_orchestrator import (  # noqa: F401
    SolutionArchitectOrchestrator,
)
