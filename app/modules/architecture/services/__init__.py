"""
Architecture services — consolidated from 88 legacy files (~2.4MB) into 6 modules.

Modules:
- archimate_service: Core ArchiMate service, template, validation, rules, metrics
- layer_service:     Business, application, technology, motivation, data layers
- document_service:  Document analysis, processing, chunking, comparison, upload
- modeling_service:  Model generation, patterns, viewpoints, relationships, exchange
- governance_service: Governance, monitoring, gaps, implementation, workflow
- ai_service:        AI/LLM analysis, semantic, entity resolution, knowledge graph

Usage::

    from app.modules.architecture.services import (
        ArchiMateService,
        BusinessLayerService,
        DocumentAnalysisService,
        ArchiMateModelGenerator,
        ArchitectureGovernanceService,
        CognitiveArchitectureService,
    )

For specialized classes, import directly from the sub-module::

    from app.modules.architecture.services.modeling_service import RelationshipValidator
"""

# Core ArchiMate
from app.modules.architecture.services.archimate_service import (  # noqa: F401
    ArchiMateElementCloner,
    ArchiMateMetamodelValidator,
    ArchiMateMetricsService,
    ArchiMateRulesEngine,
    ArchiMateService,
    ArchiMateTemplateService,
    ArchiMateValidationEngine,
    ArchiMateValidator,
    ArchiMateXMLExportService,
    ComprehensiveArchiMateService,
    ElementTypeNormalizer,
    UnifiedArchiMateServices,
)

# Layer services
from app.modules.architecture.services.layer_service import (  # noqa: F401
    ApplicationInferenceService,
    ApplicationLayerService,
    ArchiMateLayerGenerators,
    BusinessLayerService,
    ConstraintService,
    DataArchitectureService,
    DriverService,
    GoalService,
    MotivationLayerService,
    OutcomeService,
    PrincipleService,
    RiskService,
    StakeholderService,
    TechnologyLayerService,
    UnifiedArchiMateLayerServices,
)

# Document services
from app.modules.architecture.services.document_service import (  # noqa: F401
    DocumentAnalysisService,
    DocumentChunkingService,
    DocumentComparisonService,
    DocumentProcessor,
    DocumentUploadService,
    TabularDataExtractor,
)

# Modeling services
from app.modules.architecture.services.modeling_service import (  # noqa: F401
    ArchiMateExchangeService,
    ArchiMateModelGenerator,
    ArchiMatePatternLibrary,
    ArchiMatePatternService,
    ArchiMateRelationshipGenerator,
    ArchiMateRelationshipService,
    ArchiMateViewpointBuilder,
    ArchiMateViewpointGenerator,
    ArchiMateViewpointService,
    GraphRelationshipService,
    RelationshipCompletionService,
    RelationshipPatternService,
    RelationshipService,
    RelationshipValidator,
    RoadmapGenerator,
    ViewpointBuilder,
    VisualEditorService,
)

# Governance services
from app.modules.architecture.services.governance_service import (  # noqa: F401
    AgenticGapImplementationService,
    ArchitectureGovernanceService,
    ArchitectureImportExportService,
    ArchitectureMonitoringService,
    ArchitectureSearchService,
    ArchitectureValidator,
    ArchitectWorkflowService,
    GapArchiMateService,
    GapResolutionService,
    ImplementationContextEngine,
    ImplementationMigrationService,
    ImplementationWizardService,
    OptionsAnalysisService,
    SolutionArchitectOrchestrator,
)

# AI services
from app.modules.architecture.services.ai_service import (  # noqa: F401
    AIArchitectureAnalysisService,
    ArchiMateLLMService,
    CognitiveArchitectureService,
    ConfidenceScoringService,
    EnhancedArchiMateExtractor,
    EntityResolutionService,
    FeedbackLearningService,
    GeminiFileSearchService,
    KnowledgeGraphService,
    MissingFieldsAnalyzer,
    MultiModalLLMService,
    SemanticSimilarityService,
)

# Inference Engine services
from app.modules.architecture.services.inference_engine_service import (  # noqa: F401
    ArchiMateInferenceEngine,
    ExecutionContext,
    InferenceResult,
    InferenceError,
    ProviderError,
)
from app.modules.architecture.services.inference_rules_registry import (  # noqa: F401
    InferenceRulesRegistry,
    CANONICAL_CHAIN,
    ELEMENT_INFERENCE_RULES,
)
from app.modules.architecture.services.architecture_graph_facade import (  # noqa: F401
    ArchitectureGraphFacade,
    GraphNode,
    GraphRelationship,
)
from app.modules.architecture.services.inference_providers import (  # noqa: F401
    PROVIDER_REGISTRY,
    GeneratedElement,
)

__all__ = [
    # Core ArchiMate
    "ArchiMateService",
    "UnifiedArchiMateServices",
    "ComprehensiveArchiMateService",
    "ArchiMateTemplateService",
    "ArchiMateValidator",
    "ArchiMateValidationEngine",
    "ArchiMateRulesEngine",
    "ArchiMateMetricsService",
    "ElementTypeNormalizer",
    "ArchiMateXMLExportService",
    "ArchiMateMetamodelValidator",
    "ArchiMateElementCloner",
    # Layer services
    "BusinessLayerService",
    "ApplicationLayerService",
    "ApplicationInferenceService",
    "TechnologyLayerService",
    "MotivationLayerService",
    "DataArchitectureService",
    "GoalService",
    "DriverService",
    "PrincipleService",
    "StakeholderService",
    "OutcomeService",
    "ConstraintService",
    "RiskService",
    "UnifiedArchiMateLayerServices",
    "ArchiMateLayerGenerators",
    # Document services
    "DocumentAnalysisService",
    "DocumentProcessor",
    "DocumentChunkingService",
    "DocumentComparisonService",
    "DocumentUploadService",
    "TabularDataExtractor",
    # Modeling services
    "ArchiMateModelGenerator",
    "ArchiMatePatternLibrary",
    "ArchiMatePatternService",
    "ArchiMateViewpointService",
    "ArchiMateViewpointGenerator",
    "ArchiMateViewpointBuilder",
    "ViewpointBuilder",
    "ArchiMateRelationshipGenerator",
    "ArchiMateRelationshipService",
    "RelationshipService",
    "RelationshipValidator",
    "RelationshipPatternService",
    "RelationshipCompletionService",
    "GraphRelationshipService",
    "RoadmapGenerator",
    "VisualEditorService",
    "ArchiMateExchangeService",
    # Governance services
    "ArchitectureGovernanceService",
    "ArchitectureMonitoringService",
    "ArchitectureValidator",
    "ArchitectureImportExportService",
    "ArchitectureSearchService",
    "GapArchiMateService",
    "GapResolutionService",
    "AgenticGapImplementationService",
    "ImplementationMigrationService",
    "ImplementationWizardService",
    "ImplementationContextEngine",
    "OptionsAnalysisService",
    "ArchitectWorkflowService",
    "SolutionArchitectOrchestrator",
    # AI services
    "CognitiveArchitectureService",
    "AIArchitectureAnalysisService",
    "ArchiMateLLMService",
    "MultiModalLLMService",
    "EnhancedArchiMateExtractor",
    "EntityResolutionService",
    "SemanticSimilarityService",
    "ConfidenceScoringService",
    "FeedbackLearningService",
    "KnowledgeGraphService",
    "MissingFieldsAnalyzer",
    "GeminiFileSearchService",
    # Inference Engine services
    "ArchiMateInferenceEngine",
    "ExecutionContext",
    "InferenceResult",
    "InferenceError",
    "ProviderError",
    "InferenceRulesRegistry",
    "CANONICAL_CHAIN",
    "ELEMENT_INFERENCE_RULES",
    "ArchitectureGraphFacade",
    "GraphNode",
    "GraphRelationship",
    "PROVIDER_REGISTRY",
    "GeneratedElement",
]
