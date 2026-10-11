"""
ArchiMate 3.2 Intelligence Services - Plan C + Plan B Hybrid

This package provides AI-powered ArchiMate architecture services including:
- Relationship validation (metamodel compliance)
- Viewpoint generation and filtering
- Pattern recognition and recommendation
- Architecture health metrics
- Impact analysis
- Quality assessment
- LLM-powered architecture generation
- Multi-modal document processing (PDF, images, Office docs)
- Tabular data extraction (Excel/CSV application portfolios, server inventories)
- Implementation & Migration layer generation
- One-click code generation integration

Plan C + Plan B Hybrid Features:
- **Visual Architecture Editor**: Interactive React + ReactFlow canvas
- **Real-time AI Assistance**: Smart element suggestions during editing
- **Auto-complete Layers**: AI generates missing elements for complete architecture
- **Implementation Wizard**: 7 - step guided implementation planning
- **Context-Aware Generation**: Leverages existing tech stacks, capabilities, workflows
- **Impact Analysis**: Understand effects of architecture changes
- **One-Click Execution**: Direct workflow pipeline integration
"""

import os

_FAST_INIT = os.getenv("APP_FAST_INIT", "0") == "1"


if _FAST_INIT:
    # In fast-init / E2E contexts, importing the full ArchiMate services graph
    # triggers large transitive imports (models + external deps). Keep package
    # import side-effect free.
    __all__ = []
else:
    from .application_layer_service import ApplicationLayerService
    from .archimate_llm_service import ArchiMateLLMService
    from .archimate_validator import ArchiMateValidator
    from .archimate_viewpoint_service import ArchiMateViewpointService
    from .document_processor import DocumentProcessor
    from .document_upload_service import DocumentUploadService
    from .implementation_migration_service import ImplementationMigrationService
    from .multi_modal_llm_service import MultiModalLLMService
    from .relationship_service import RelationshipService
    from .tabular_data_extractor import TabularDataExtractor
    from .viewpoint_builder import (
        Viewpoint,
        ViewpointBuilder,
        ViewpointElement,
        ViewpointRelationship,
        ViewpointValidationResult,
        get_viewpoint_builder,
    )

    __all__ = [
        "ArchiMateValidator",
        "ArchiMateViewpointService",
        "ArchiMateLLMService",
        "DocumentUploadService",
        "MultiModalLLMService",
        "DocumentProcessor",
        "ApplicationLayerService",
        "ImplementationMigrationService",
        "RelationshipService",
        "TabularDataExtractor",
        "ViewpointBuilder",
        "ViewpointElement",
        "ViewpointRelationship",
        "Viewpoint",
        "ViewpointValidationResult",
        "get_viewpoint_builder",
    ]
