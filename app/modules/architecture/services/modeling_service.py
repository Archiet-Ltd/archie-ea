"""
Architecture modeling service — imports from inlined canonical sources.

Consolidates model generation, patterns, viewpoints, and relationships:
- app.services.archimate_model_generator (ArchiMateModelGenerator — 26KB)
- app.services.archimate_pattern_library (ArchiMatePatternLibrary — 26KB)
- app.services.archimate.archimate_pattern_service (ArchiMatePatternService — 24KB)
- app.services.archimate.archimate_viewpoint_service (ArchiMateViewpointService — 33KB)
- app.services.archimate_viewpoint_generator (ArchiMateViewpointGenerator — 28KB)
- app.services.archimate_viewpoint_builder (ArchiMateViewpointBuilder — 27KB)
- app.services.archimate.viewpoint_builder (ViewpointBuilder — 24KB)
- app.services.archimate_relationship_generator (ArchiMateRelationshipGenerator — 15KB)
- app.services.archimate_relationship_service (ArchiMateRelationshipService — 8KB)
- app.services.archimate.relationship_service (RelationshipService — 33KB)
- app.services.archimate.relationship_validator (RelationshipValidator — 20KB)
- app.services.archimate.relationship_pattern_service (RelationshipPatternService — 9KB)
- app.services.archimate.relationship_completion_service (RelationshipCompletionService — 4KB)
- app.services.archimate.graph_relationship_service (GraphRelationshipService — 15KB)
- app.services.archimate.roadmap_generator (RoadmapGenerator — 16KB)
- app.services.archimate.visual_editor_service (VisualEditorService — 17KB)
- app.services.archimate_exchange_service (ArchiMateExchangeService — 50KB)
- app.services.kg.archimate_exchange_service (KGArchiMateExchangeService — 16KB)
"""

from app.modules.architecture.services.archimate_model_generator import (  # noqa: F401
    ArchiMateModelGenerator,
)

from app.modules.architecture.services.archimate_pattern_library import (  # noqa: F401
    ArchiMatePatternLibrary,
)

from app.modules.architecture.services.archimate_pattern_service import (  # noqa: F401
    ArchiMatePatternService,
)

from app.modules.architecture.services.archimate_viewpoint_service import (  # noqa: F401
    ArchiMateViewpointService,
)

from app.modules.architecture.services.archimate_viewpoint_generator import (  # noqa: F401
    ArchiMateViewpointGenerator,
)

from app.modules.architecture.services.archimate_viewpoint_builder import (  # noqa: F401
    ArchiMateViewpointBuilder,
)

from app.modules.architecture.services.viewpoint_builder import (  # noqa: F401
    ViewpointBuilder,
)

from app.modules.architecture.services.archimate_relationship_generator import (  # noqa: F401
    ArchiMateRelationshipGenerator,
)

from app.modules.architecture.services.archimate_relationship_service import (  # noqa: F401
    ArchiMateRelationshipService,
)

from app.modules.architecture.services.relationship_service import (  # noqa: F401
    RelationshipService,
)

from app.modules.architecture.services.relationship_validator import (  # noqa: F401
    RelationshipValidator,
)

from app.modules.architecture.services.relationship_pattern_service import (  # noqa: F401
    RelationshipPatternService,
)

from app.modules.architecture.services.relationship_completion_service import (  # noqa: F401
    RelationshipCompletionService,
)

from app.modules.architecture.services.graph_relationship_service import (  # noqa: F401
    GraphRelationshipService,
)

from app.modules.architecture.services.roadmap_generator import (  # noqa: F401
    RoadmapGenerator,
)

from app.modules.architecture.services.visual_editor_service import (  # noqa: F401
    VisualEditorService,
)

from app.modules.architecture.services.archimate_exchange_service import (  # noqa: F401
    ArchiMateExchangeService,
)

try:
    from app.modules.architecture.services.kg_archimate_exchange_service import (  # noqa: F401
        ArchiMateExchangeService as KGArchiMateExchangeService,
    )
except ImportError:
    KGArchiMateExchangeService = None  # type: ignore[assignment,misc]
