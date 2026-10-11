"""
AI analysis service — imports from inlined canonical sources.

Consolidates AI analysis, detection, and recommendation services:
- ai_gap_detection_service (AIGapDetectionService)
- ai_impact_analysis_service (AIImpactAnalysisService)
- ai_semantic_discovery_service (AISemanticDiscoveryService)
- ai_confidence_calculator (AIConfidenceCalculator)
- ai_hallucination_detector (AIHallucinationDetector)
- ai_data_interaction_service (AIDataInteractionService)
- ai_import_service (AIImportService)
"""

from app.modules.ai_chat.services.ai_gap_detection_service import (  # noqa: F401
    AIGapDetectionService,
)

from app.modules.ai_chat.services.ai_impact_analysis_service import (  # noqa: F401
    AIImpactAnalysisService,
)

from app.modules.ai_chat.services.ai_semantic_discovery_service import (  # noqa: F401
    AISemanticDiscoveryService,
    LLMRecommendation,
    SemanticSearchResult,
)

from app.modules.ai_chat.services.ai_confidence_calculator import (  # noqa: F401
    AIConfidenceCalculator,
)

from app.modules.ai_chat.services.ai_hallucination_detector import (  # noqa: F401
    AIHallucinationDetector,
)

from app.modules.ai_chat.services.ai_data_interaction_service import (  # noqa: F401
    AIDataInteractionService,
)

from app.modules.ai_chat.services.ai_import_service import (  # noqa: F401
    AIImportResult,
    AIImportService,
)
