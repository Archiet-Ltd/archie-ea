"""Model package exports.

Historically this package imported *all* models on import so callers could do
`from app.models import SomeModel`.

In lightweight contexts (notably E2E / APP_FAST_INIT=1) importing the entire
ORM graph can trigger heavy mapper configuration and, on Windows, intermittent
access-violation crashes.

So under APP_FAST_INIT=1 we intentionally export only a small, safe subset.
"""

from __future__ import annotations

import os

# Always available: constants, validators, mixins (no DB dependencies)
from .constants import (  # noqa
    ArchiMateLayer,
    ArchiMateRelationshipType,
    CascadePolicy,
    Criticality,
    FieldLength,
    GapStatus,
    LifecycleStatus,
    MaturityLevel,
    Priority,
    validate_percentage,
    validate_positive,
    validate_status,
)
from .mixins import AuditMixin, HierarchyMixin, SoftDeleteMixin, StatusMixin, TenantMixin, TimestampMixin  # noqa
from .organization import Organization  # noqa
from .permission import Permission, RolePermission, UserRole  # noqa
from .system_setting import SystemSetting  # noqa
from .validators import (  # noqa
    validate_code,
    validate_date_range,
    validate_email,
    validate_enum,
    validate_future_date,
    validate_max_length,
    validate_monetary,
    validate_not_empty,
    validate_positive_int,
    validate_rating,
    validate_slug,
    validate_url,
)

_FAST_INIT = os.getenv("APP_FAST_INIT", "0") == "1"

if _FAST_INIT:
    # Minimal exports used by fast-init routes/templates.
    from .archimate_core import *  # noqa

    # Import fast-init implementation models instead of main ones
    from .miscellaneous import *  # noqa
    from .technology_stack import *  # noqa - TechnologyStack for fast init
    from .user import *  # noqa
    # Session registry is read on every authenticated request via
    # app/_bootstrap/session_policy.py -- must exist even under fast init.
    from .user_session import UserSession  # noqa: F401
else:
    from .adr import *  # noqa - ArchitectureDecisionRecord (Solution Architecture governance)
    from .ai_audit_log import *  # noqa - AIAuditLog (ai_audit_logs table; needed by create_all)
    # SolutionCodeBundle lives in a feature module; import it so create_all() builds its
    # table (the route imports it lazily, which is too late for a fresh schema build).
    from app.modules.solutions_product.models import *  # noqa
    # Feature models that were defined but never imported here, so create_all() skipped
    # their tables -> routes querying them returned UndefinedTable 500s on a fresh install.
    from .import_history import *  # noqa
    # BA-B1: artefact_share_links. Imported here so create_all() builds the
    # table on a fresh install and reconcile-schema sees the mapped model on an
    # existing one — every column below the primary key is nullable or has a
    # default, so an ADD-only reconcile can apply it.
    from .artefact_share import ArtefactShareLink  # noqa: F401
    from .sso_config import *  # noqa
    # Session registry (server-side revocation on logout / password change).
    from .user_session import UserSession  # noqa: F401
    from .error_event import ErrorEvent  # noqa: F401 - server + client error telemetry
    from .gdpr_request import *  # noqa
    from .subscription import *  # noqa
    from .ai_chat_document import *  # noqa
    # conversation_threads / conversation_messages existed only in an Alembic
    # revision, and deploys do not run `flask db upgrade` — so a fresh database
    # had no chat history tables at all and /ai-chat/threads 500'd.
    from .conversation import ConversationMessageRecord, ConversationThreadRecord  # noqa
    from .consulting_partner import *  # noqa
    from .capability_archimate_mapping import *  # noqa
    from .copilot_insight import *  # noqa
    from .frontend_configuration import *  # noqa
    from .scoring_config import *  # noqa
    from .usage_event import *  # noqa
    from .simple_duplicate_detection import *  # noqa
    from .optimization import *  # noqa
    from .mapping_metrics import *  # noqa
    from .unified_work_package import *  # noqa - unified_work_packages (capability roadmap)
    from app.modules.codegen.services.scenario_tracker import ScenarioResult  # noqa - codegen_scenario_results
    from .architecture_decision import (  # noqa: F401
        ArchitectureDecision, DecisionCapabilityLink,
        ArchitectureChangeRequest, ChangeImpactAssessment, ArchitectureChangeNotice,
        VALID_LINK_TYPES, VALID_TRIGGER_TYPES, VALID_DISPOSITIONS
    )  # ARB-002, ARB-004
    from .application_portfolio import *  # noqa - ApplicationComponent, ApplicationTechnologyInstance, VendorContract
    from .application_rationalization import *  # noqa - ApplicationReplacement, ApplicationDependency, ApplicationRationalizationScore, VendorConcentrationAnalysis
    from .archimate_motivation import *  # noqa - MotivationStakeholder, MotivationAssessment, MotivationOutcome, MotivationConstraint, MotivationValue, MotivationMeaning (ArchiMate 3.2 Motivation Layer)
    from .business_capabilities import (  # noqa
        ApplicationCapabilityCoverage,
        BusinessCapability,
        BusinessFunction,
        Capability,
        FunctionalRequirement,
        NonFunctionalRequirement,
    )
    from .capabilities import *  # noqa
    from .capability_governance import *  # noqa - CapabilityGovernanceDecision
    from .compliance_models import *  # noqa
    from .cost_intelligence import *  # noqa - CapabilityCostAllocation, VendorContract, SLA (Cost intelligence)
    from .decision_ledger import *  # noqa - DecisionLedger (append-only governance ledger)

    # Integration & Solution Architecture Models
    from .integration_metadata import *  # noqa - ApplicationInterfaceMetadata, SystemDependency (Integration Architecture)
    from .miscellaneous import *  # noqa
    from .models import *  # noqa

    # EA Intelligence Enhancement Models
    from .motivation import *  # noqa - Driver, Goal models (Strategy-to-Implementation traceability)

    # Restore platform_models to fix PlatformType missing error
    from .platform_models import *  # noqa
    from .process_data import *  # noqa - BusinessProcess, DataDomain, DataEntity (Process-Capability-Data Trinity)

    # Reference Model Framework (ISA - 95, APQC, Industry 4.0)
    from .reference_models import *  # noqa - ReferenceModel, ReferenceModelCapability, ReferenceModelImport
    from .user import *  # noqa

    # Restore vendor models for vendor activation functionality
    from .vendor.vendor_organization import *  # noqa - Contains junction tables needed by other models
    from .vendor_analysis import *  # noqa
    from .vendor_stack_hierarchy import *  # noqa

    # North Star Persona MVP Models (ADR-0009, ADR-0010, ADR-0011)
    from .application_owner import ApplicationOwner  # noqa - Application Manager persona filtering
    # VendorContract already defined in application_portfolio.py - reuse existing
    from .license_entitlement import LicenseEntitlement  # noqa - Procurement license tracking
    from .contract_application import ContractApplication  # noqa - Contract-to-application allocation

    # Restore vendor stack template to fix missing VendorStackTemplate
    from .vendor_stack_template import *  # noqa

    # Restore workflow and vendor analysis models
    from .workflow_models import *  # noqa
    from .workflow_artifacts import *  # noqa - ArchitectureVisionDocument, ArchitectureReviewFinding, VendorSelectionReport, ComplianceScanReport, WorkflowCompletionSummary

    # Legacy alias: some routes expect `Application`; map to ApplicationComponent for backward compatibility
    Application = ApplicationComponent

    # ArchiMate 3.2 Domain Models - Business & Application Layers
    from .agentic_gaps import *  # noqa - AgentExecutionHistory, AgentConfiguration, AgentSchedule

    # AI Service Architecture - Intelligent Modeling & Audit
    from .ai_service import *  # noqa - AIServiceConfig, AIPromptTemplate, AIInteractionLog

    # Missing Architecture Models - Data, Solutions, Software Architecture
    from .all_missing_models import *  # noqa - ConceptualDataModel, LogicalDataModel, PhysicalDataModel, DataLineage, DataTransformation

    # NEW COMPREHENSIVE CAPABILITY MODELS - Task 1 Complete
    from .application_capability import *  # noqa - ApplicationCapabilityMapping (comprehensive)

    # Application Consolidation Intelligence - AI-powered duplication detection and cost savings
    from .application_consolidation import *  # noqa - ApplicationSimilarityAnalysis, ApplicationConsolidationRecommendation, ApplicationDuplicationReport
    from .application_import_history import *  # noqa - ApplicationImportHistory
    from .application_layer import *  # noqa - ApplicationInterface, ApplicationEvent, ApplicationCollaboration, ApplicationFunction, ApplicationProcess, ApplicationInteraction, DataObject
    from .application_lifecycle import *  # noqa - ApplicationVersioning, DeploymentPipeline, ApplicationPerformanceMetrics

    # APQC Process Models - Process framework integration
    from .apqc_process import *  # noqa - APQCProcess, CapabilityProcessMapping, ProcessApplicationMapping
    from .archimate import *  # noqa - ArchiMateView (ArchiMate view/diagram models)
    from .archimate_business import *  # noqa - BusinessCollaboration, BusinessInterface, BusinessInteraction, Contract, Representation (ArchiMate 3.2 Business Layer)
    from .archimate_core import *  # noqa - ArchitectureModel, ArchiMateElement, ArchiMateRelationship, CompositeStructure, OtherRelationship
    from .architecture_generation_run import ArchitectureGenerationRun  # noqa - run provenance tracking
    from .archimate_metamodel import *  # noqa - ArchiMateRelationshipRule, MetamodelViolation (ArchiMate 3.2 validation engine)
    from .archimate_technology import *  # noqa - TechnologyCollaborationFull, TechnologyFunction, TechnologyProcess, TechnologyInteraction, TechnologyEvent, Resource (ArchiMate 3.2 Technology Layer behavioral elements)
    from .archimate_viewpoint import *  # noqa - ArchiMateViewpoint, ViewpointStakeholderMapping, ViewpointView (ArchiMate 3.2 Viewpoint Catalog)
    from .architecture_session import *  # noqa - ArchitectureSession (undo/rollback capability for bulk operations)
    from .architecture_journey import *  # noqa - purpose-led architecture journey aggregate
    from .architecture_journey_link import *  # noqa - journey edges: links and members

    # from app.wizards.models import ApplicationRationalizationWizard, RequirementsToCodeWizard, ComplianceAccelerationWizard  # Commented out: wizards module doesn't exist
    from .autogen import *  # noqa

    # Batch Import Models - Batch processing with approval workflow
    from .batch_import import *  # noqa - BatchImportJob, BatchImportBatch, BatchImportApplication, BatchImportElement, BatchImportCheckpoint
    from .business_layer import *  # noqa - BusinessActor, BusinessRole, BusinessService, BusinessObject
    from .capability_models import *  # noqa - CapabilityDependency, CapabilityMaturityAssessment, TechnologyCapabilityMapping (renamed from TechnologyCapability), CapabilityRoadmap

    # Capability to Vendor/Application Mapping Models - Cross-specialization type relationships
    from .capability_to_vendor_mapping import *  # noqa - TechnicalCapabilityVendorMapping, UnifiedCapabilityApplicationMapping, UnifiedCapabilityVendorOrganizationMapping, ApplicationVendorProductMapping

    # Register the complete vendor catalogue graph before a worker can serve a
    # request. Without this eager import, a concurrent first request can
    # configure VendorProductDetail while its string-related VendorProductAlias
    # class is still being imported, leaving the worker's mapper registry
    # permanently invalid. Keep this after the canonical capability mapping;
    # vendor_product re-exports that mapping for legacy callers.
    from .vendor.vendor_product import (  # noqa: F401
        VendorProductAlias,
        VendorProductDetail,
        VendorProductFamily,
    )

    # Consolidation Module - Application consolidation and savings tracking
    from .consolidation import *  # noqa - ConsolidationCandidate, ConsolidationOpportunity, SavingsRealization

    # Custom Fields System - Dynamic field management
    from .custom_fields import *  # noqa - CustomFieldDefinition, ApplicationCustomFieldValue

    # Dashboard edits store
    from .dashboard_edit import *  # noqa
    from .data_governance import *  # noqa - DataCatalog, DataQualityMetrics, DataGovernanceWorkflow, DataAccessControl, DataRetentionPolicy

    # Derivation Audit Models - APQC to ArchiMate derivation tracking (Phase 6.1)

    # Vendor Catalogue Models - NEW vendor management (Phase 1)
    # NOTE: Temporarily disabled - conflicts with existing vendor_organization.py
    # Disabled: conflicts with canonical vendor_organization.py in app/models/vendor/
    # from .vendor import *  # noqa - VendorOrganization, VendorProduct
    # Document Analysis Models - Architecture document analysis and history
    from .document_analysis import *  # noqa - DocumentAnalysis, DocumentAnalysisEdit

    # Framework-Based Element Templates - Reusable ArchiMate elements from PCF, ITIL, COBIT, etc.
    from .element_templates import *  # noqa - ElementTemplate, ElementTemplateUsage, ElementTemplateRecommendation

    # Enterprise Intelligence Models - Portfolio management and financial tracking
    from .enterprise_intelligence import *  # noqa - PortfolioInitiative, OrganizationUnit, ApplicationCost, ApplicationROI

    # Connector Configuration - per-org encrypted credentials for external connectors
    from .connector_config import OrgConnectorConfig  # noqa

    # Feature Flags System - Dynamic feature control
    from .feature_flags import *  # noqa - FeatureFlag, FeatureState, FeatureType
    from .framework import *  # noqa - EnterpriseArchitectureFramework, QualityFramework, IndustryFramework

    # Framework Configuration Models - Configuration-driven framework system
    from .framework_configuration import *  # noqa - CapabilityFrameworkConfiguration, FrameworkExtension, etc.
    from .implementation_migration import *  # noqa - ImplementationEvent, Plateau, Gap (migration models)

    # Implementation Planning Models - Roadmap and plateau management
    from .implementation_planning import *  # noqa - ImplementationPlateau, RoadmapDeliverable, RoadmapGap, Resource, RoadmapScenario, RoadmapAudit

    # Import Session Models - Transactional staging with checkpointing
    from .import_session import *  # noqa - ImportSession, StagingElement, ImportCheckpoint
    from .jira_sync_tracking import *  # noqa - JiraSyncTracking, PushStatus (Jira push integration)
    from .job import *  # noqa - Job model for DB-backed job queue

    # Manufacturing Models - Manufacturing-specific capabilities and value streams
    from .manufacturing_capability import *  # noqa - ManufacturingCapability, ManufacturingValueStream, etc.

    # ArchiMate 3.2 Domain Models - Manufacturing/Technology Layer
    from .manufacturing_domain import *  # noqa - ManufacturingPlant, ProductionLine, Equipment, ProductionOrder
    from .missing_capability_models import *  # noqa - ApplicationCapability, TechnologyCapability
    from .physical_layer import *  # noqa - PhysicalEquipment, PhysicalFacility, PhysicalDistributionNetwork, PhysicalMaterial

    # Policy Monitoring Module - Architecture policy management and compliance tracking
    from .policy_monitoring import *  # noqa - ArchitecturePolicy, PolicyViolation, ComplianceStatus, PolicyExemption

    # Project Management Models
    from .project_models import *  # noqa - Project, Task, Milestone, ProjectNote, ProjectResource
    from .relationship_tables import *  # noqa - Junction tables for RACI, CRUD, dependencies, application mappings
    from .software_architecture import *  # noqa - SoftwareModule, DesignPattern, SoftwareDependency
    from .software_quality import *  # noqa - TechnicalDebt, CodeQualityMetrics, RefactoringTracking
    from .solution_deployment import *  # noqa - SolutionTechnologyMapping, SolutionDeploymentArchitecture

    # Strategic Module - Strategic initiatives, milestones, and roadmap management
    from .strategic import *  # noqa - StrategicInitiative, StrategicMilestone, RoadmapItem
    from .transformation_programme import *  # noqa - canonical programme aggregate children
    from .transformation_execution import *  # noqa - fenced commands and immutable results
    from .transformation_evidence import *  # noqa - candidates, signals, and evidence requests
    from .transformation_decision import *  # noqa - immutable options and decision briefs
    from .arb_submission_event import *  # noqa - immutable typed ARB submission receipt
    from .arb_decision_event import *  # noqa - immutable typed ARB decisions and conditions
    from .arb_condition_evidence import *  # noqa - immutable condition-scoped evidence
    from .arb_condition_event import *  # noqa - immutable ARB condition lifecycle
    from . import transformation_db_guards  # noqa: F401 - registers PostgreSQL guards
    from .strategy_layer import *  # noqa - StrategyResource, CourseOfAction, ValueStream (Strategy Layer completion)
    from .structural_elements import *  # noqa - Grouping, Junction, Location (Structural/Composite elements)

    # Agentic Gap Implementation Models
    from .system_architecture import *  # noqa - SystemBoundary, SystemHierarchy, SystemInterface, SystemDeployment, SystemLifecycle

    # Technical Capability Models (ACM) - Application Capability Model with 7 domains
    from .technical_capability import *  # noqa - TechnicalCapability, ACMDomain, mapping tables
    from .technology_layer import *  # noqa - Node, Device, SystemSoftware, TechnologyInterface, Path, CommunicationNetwork, TechnologyService
    from .truly_missing_models import *  # noqa - Solution, SolutionPattern, SolutionContract
    from .adm_kanban import ADMPhase, KanbanBoard, KanbanCard, KanbanCardComment, KanbanCardAttachment, ADMPhaseStep  # noqa: F401
    from .solution_architect_models import *  # noqa - SolutionAnalysisSession, SolutionProblemDefinition, etc.
    from app.models.solution_architect_models import RequirementChangeLog  # noqa: F401
    from .solution_lifecycle_models import *  # noqa - SolutionRisk, SolutionTCOItem, SolutionMetric, SolutionPlateau

    # Unified Business Capability Models - BUSINESS specialization type
    from .unified_capability import *  # noqa - UnifiedCapability, UnifiedCapabilityProcessMapping

    # Usage Analytics for PARTIAL Features
    from .usage_analytics import *  # noqa - UsageAnalytics (Phase 0 usage tracking)

    # Industry APQC Models - Industry-specific process classification (must be before vector_embeddings)
    from .industry_apqc import *  # noqa - IndustryAPQCFramework, IndustryAPQCProcess

    # Vector Embeddings - pgvector integration for semantic search
    from .vector_embeddings import *  # noqa - ProcessEmbedding, ChatMessageEmbedding

    # ArchiMate Relationship Auto-Sync - event listeners for junction table -> ArchiMateRelationship
    from . import archimate_relationship_sync  # noqa: F401 - registers event listeners

    # SA-001: Solution ↔ ArchiMate junction tables
    from .solution_archimate_element import SolutionArchiMateElement  # noqa: F401
    from .solution_element import SolutionElement  # noqa: F401

    from .ai_chat_feedback import AIChatFeedback  # noqa: F401

    # Sprint model for TPM sprint planning
    from .sprint import Sprint, SprintStatus  # noqa: F401

    # Kanban card history for TPM-009 flow analytics
    from .kanban_card_history import KanbanCardHistory  # noqa: F401

    # Risk model for TPM-013 risk heat map
    from .risk import Risk, RiskStatus  # noqa: F401

    # H1: Risk <-> Application/Solution/Programme links
    from .risk_entity_link import RiskEntityLink  # noqa: F401

    # RAID: Assumption/Issue/Dependency (Risk above already covers the "R")
    from .raid_item import RaidItem, RaidKind, RaidStatus  # noqa: F401

    # Approved-technology register behind the governance dashboard's Standards tab
    from .technology_standard import TechnologyStandard  # noqa: F401

    # Measurable initiative outcomes (replaces the expected_benefits JSON blob)
    from .benefit import Benefit  # noqa: F401

    # Demand intake (the front door) and Assumption (completes RAID)
    from .demand import Assumption, Demand  # noqa: F401

    # Hourly rates, so logged effort can be costed into initiative spend
    from .rate_card import RateCard  # noqa: F401

    # SA-009: TOGAF ADM deliverable checklists per phase
    from .adm_deliverable import ADMDeliverable, ADMDeliverableCheck  # noqa: F401

    # RRT-001: Requirement templates with 16 seeded architecture-layer templates
    from .requirement_template import RequirementTemplate  # noqa: F401

    # TPM-012: Stakeholder communication log
    from .stakeholder_communication import CommunicationType, StakeholderCommunication  # noqa: F401

    # TPM-010: Definition of Done templates and checks
    from .dod_template import DoDTemplate, DoDCheck  # noqa: F401

    # PRQ-001: Requirement dependency join table
    from app.models.solution_architect_models import RequirementDependency  # noqa: F401

    # Inference Engine: provenance-tagged cross-model relationships
    from .architecture_inference_relationship import ArchitectureInferenceRelationship  # noqa: F401

    # ACM Domain-Driven Architecture
    from .acm_domain_template import AcmDomainTemplate  # noqa: F401
    from .acm_cross_domain_rule import AcmCrossDomainRule  # noqa: F401
    from .solution_domain_spec import SolutionDomainSpec  # noqa: F401
    from .acm_property_template import AcmPropertyTemplate  # noqa: F401

    # T-003: derived-fact store (DE-2) — rule-derived ArchiMate relationships
    from app.modules.intelligence.models.derived_relationship import (  # noqa: F401
        DerivedRelationship,
    )

    # T-005 (D7): derivation run-record store (DE-11) — the only producer of
    # "did derivation run for this tenant, when, and how long did it take".
    from app.modules.intelligence.models.derivation_run import (  # noqa: F401
        DerivationRun,
    )

    # Solution Workflow & Governance — FK dependency: governance references workflow_tasks
    from .solution_workflow import *  # noqa: F401
    # solution_reasoning defines solution_ai_reasoning_states, which solution_governance
    # references via FK; it must be in metadata for create_all() to resolve that FK.
    from .solution_reasoning import *  # noqa: F401
    from .solution_governance import *  # noqa: F401
    from .arb_submission_evidence import (  # noqa: F401
        ARBSubmissionEvidenceSnapshot,
        WorkbenchArtifactEvidence,
    )

    # GOV-02: Architecture Decision Records (uses original architecture_decision.py, imported at line 61)
    # Duplicate architecture_decisions.py removed — original has richer schema

    # GOV-03: Governance Gates — hard enforcement of completeness thresholds
    from .governance_gates import GovernanceGate  # noqa: F401

    # CODEGEN-05: Published API spec registry
    from .published_api_spec import PublishedAPISpec  # noqa: F401

    # CODEGEN-06: Runtime compliance monitoring — spec drift detection
    from .compliance_check import RuntimeComplianceCheck  # noqa: F401

    # RUNTIME-08: Spec webhooks for drift remediation
    from .spec_webhook import SpecWebhook  # noqa: F401

    # RUNTIME-02: Integration contract registry — real endpoints for codegen
    from .integration_contract import IntegrationContract  # noqa: F401

    # AC-8: Versioned LLM prompt registry with A/B testing and metrics
    from .llm_prompt_version import LLMPromptVersion  # noqa: F401

    # Solution Blueprint, Cost, Outcomes, Scoring — tables created via db.create_all()
    from .solution_blueprint_proposal import SolutionBlueprintProposal  # noqa: F401
    from .solution_cost_model import (  # noqa: F401
        SolutionCostModel, SolutionCostLineItem,
        SolutionCostYearlyProjection, SolutionCostComparison,
    )
    from .solution_outcomes import (  # noqa: F401
        SolutionOutcome, SolutionOutcomeMeasurement,
    )
    from .solution_scoring_config import SolutionScoringConfig  # noqa: F401

    # INTARCH-001: Integration Pattern library — SAP↔Microsoft governance
    from .integration_pattern import IntegrationPattern  # noqa: F401

    # BMC-001: Business Model Canvas + Operating Model (Business-Architect artifact)
    from .business_model import BusinessModelCanvas  # noqa: F401

    # ORG-001: enterprise RACI assignments (Business-Architect org modeling)
    from .organization_model import EnterpriseRaciAssignment  # noqa: F401

    # BC-001: consolidated Business Case artifact (Business-Architect)
    from .business_case import BusinessCase  # noqa: F401

    # ARCH-124: Tech Radar — adopt/trial/assess/hold classification over the
    # existing Technology-layer ArchiMateElement catalogue.
    from .tech_radar import TechRadarEntry  # noqa: F401

    # ARCH-123 (Data Lineage) builds entirely on the existing
    # ArchiMateRelationship model (type="DataFlow" between DataObject
    # elements) — no new table required; see
    # app/modules/data_lineage/services.py.
    from .waitlist_signup import WaitlistSignup  # noqa: F401
