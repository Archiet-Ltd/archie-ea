"""

Application capability catalog for the Digital Application Platform.

Provides a curated hierarchy (Level 0 - 3) aligned with the augmented IIRM model.
Used by the alternative verification pipeline to seed canonical capabilities and
compare generated outputs.
"""
import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from app import db
from app.models import ArchiMateElement
from app.models.business_capabilities import BusinessFunction
from app.models.unified_capability import BusinessDomain, UnifiedCapability

# from app.services.decorators import transactional  # Temporarily disabled

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FunctionSpec:
    """Definition of a canonical Level 3 function for a capability."""

    name: str
    description: str
    function_type: str = "service"
    is_automated: bool = False
    automation_level: int = 0


@dataclass(frozen=True)
class CapabilitySpec:
    """Definition of a capability node in the catalog."""

    name: str
    description: str
    level: int
    domain: str
    category: str
    capability_type: str
    children: List["CapabilitySpec"]
    functions: List[FunctionSpec]


def _build_catalog() -> CapabilitySpec:
    """Return the static capability catalog definition."""
    return CapabilitySpec(
        name="Digital Application Platform",
        description="Unified platform capability delivering, evolving, and governing applications.",
        level=0,
        domain="Enterprise",
        category="Core",
        capability_type="core",
        children=[
            CapabilitySpec(
                name="Information Consumer Capabilities",
                description="Deliver user-facing experiences across channels with personalization and workflow support.",
                level=1,
                domain="Experience",
                category="Core",
                capability_type="core",
                children=[
                    CapabilitySpec(
                        name="Experience Orchestration",
                        description="Coordinate end-to-end user journeys and adaptive experiences.",
                        level=2,
                        domain="Experience",
                        category="Core",
                        capability_type="core",
                        functions=[
                            FunctionSpec(
                                name="Journey Mapping Service",
                                description="Maintain customer journey maps and trigger contextual workflows.",
                            ),
                            FunctionSpec(
                                name="Contextual Content Assembly",
                                description="Assemble content variants based on persona, segment, and channel.",
                            ),
                            FunctionSpec(
                                name="Adaptive UI Layout Engine",
                                description="Render responsive layouts with component-level targeting.",
                                function_type="presentation",
                                automation_level=60,
                                is_automated=True,
                            ),
                            FunctionSpec(
                                name="Accessibility Compliance Toolkit",
                                description="Apply accessibility rules and testing to all experience variants.",
                                function_type="process",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Multi-Channel Delivery",
                        description="Provide consistent experiences across web, mobile, conversational, and edge channels.",
                        level=2,
                        domain="Experience",
                        category="Core",
                        capability_type="core",
                        functions=[
                            FunctionSpec(
                                name="Web Experience Delivery",
                                description="Serve responsive web applications with real-time personalization.",
                                function_type="service",
                            ),
                            FunctionSpec(
                                name="Native Mobile Delivery",
                                description="Deliver native mobile apps with OTA updates and feature flag support.",
                                function_type="service",
                                automation_level=40,
                            ),
                            FunctionSpec(
                                name="Conversational Interface Delivery",
                                description="Manage chat, voice, and virtual assistant experiences with NLP adapters.",
                                function_type="service",
                            ),
                            FunctionSpec(
                                name="Embedded Client Delivery",
                                description="Deploy kiosk and embedded client experiences with offline safeguards.",
                                function_type="service",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Personalization & Insight Surfaces",
                        description="Expose data-driven insights and experimentation surfaces to end users.",
                        level=2,
                        domain="Analytics",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Segmentation Engine",
                                description="Manage audience segments and eligibility rules across channels.",
                                function_type="analysis",
                            ),
                            FunctionSpec(
                                name="Recommendation Service",
                                description="Generate real-time content and product recommendations.",
                                function_type="analysis",
                                is_automated=True,
                                automation_level=70,
                            ),
                            FunctionSpec(
                                name="Behavioral Analytics Dashboard",
                                description="Present behavioral metrics and cohort performance tracking.",
                            ),
                            FunctionSpec(
                                name="Experimentation Service",
                                description="Run A/B and multivariate experiments with statistical guardrails.",
                                function_type="process",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Workflow Interaction Surfaces",
                        description="Enable guided workflows, decision support, and collaborative task execution.",
                        level=2,
                        domain="Operations",
                        category="Core",
                        capability_type="core",
                        functions=[
                            FunctionSpec(
                                name="Task Automation Console",
                                description="Provide operators with task queues, SLAs, and automation triggers.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Approval & Exception Handling UI",
                                description="Manage approvals, escalations, and exception routing for workflows.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Decision Support Panel",
                                description="Surface context-aware recommendations for human decisions.",
                                function_type="analysis",
                            ),
                            FunctionSpec(
                                name="Collaborative Workspace Integration",
                                description="Integrate collaboration tools for shared workflow execution.",
                                function_type="service",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Edge & Offline Client Support",
                        description="Deliver resilient edge experiences with synchronization and telemetry batching.",
                        level=2,
                        domain="Mobility",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Sync & Conflict Resolution",
                                description="Synchronize offline transactions with conflict resolution policies.",
                                function_type="process",
                                is_automated=True,
                                automation_level=50,
                            ),
                            FunctionSpec(
                                name="Edge Cache Management",
                                description="Manage local caches, eviction strategies, and encryption.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Device Provisioning & Hardening",
                                description="Provision and secure edge devices with policy-driven controls.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Offline Telemetry Batching",
                                description="Collect and forward telemetry when connectivity resumes.",
                                function_type="process",
                            ),
                        ],
                        children=[],
                    ),
                ],
                functions=[],
            ),
            CapabilitySpec(
                name="Development Capabilities",
                description="Provide platform engineering tooling, automation, and developer experience.",
                level=1,
                domain="Engineering",
                category="Core",
                capability_type="core",
                children=[
                    CapabilitySpec(
                        name="Platform Engineering Toolchain",
                        description="Deliver CI/CD, environment provisioning, and artifact management services.",
                        level=2,
                        domain="Engineering",
                        category="Core",
                        capability_type="core",
                        functions=[
                            FunctionSpec(
                                name="CI Pipeline Orchestration",
                                description="Coordinate build, test, and deploy pipelines across stacks.",
                                function_type="process",
                                is_automated=True,
                                automation_level=80,
                            ),
                            FunctionSpec(
                                name="Artifact & Version Management",
                                description="Manage package repositories, versioning, and provenance metadata.",
                                function_type="service",
                            ),
                            FunctionSpec(
                                name="Environment Provisioning Service",
                                description="Provision environments via infrastructure-as-code blueprints.",
                                function_type="service",
                            ),
                            FunctionSpec(
                                name="Infrastructure Blueprint Library",
                                description="Store and govern reusable IaC modules and policies.",
                                function_type="service",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Blueprint & Scaffolding Services",
                        description="Deliver domain templates, code generators, and compliance-ready stacks.",
                        level=2,
                        domain="Engineering",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Domain Template Catalog",
                                description="Maintain reference implementations and golden paths per domain.",
                                function_type="service",
                            ),
                            FunctionSpec(
                                name="Boilerplate Code Generation",
                                description="Generate starter code and configuration based on templates.",
                                function_type="transformation",
                                is_automated=True,
                                automation_level=75,
                            ),
                            FunctionSpec(
                                name="Compliance Stack Configurations",
                                description="Provide compliant infrastructure stacks with guardrails.",
                                function_type="service",
                            ),
                            FunctionSpec(
                                name="Reference Implementation Gallery",
                                description="Curate and publish exemplar projects with observability baked in.",
                                function_type="service",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Automation & Testing Services",
                        description="Automate quality gates, testing, and performance validation.",
                        level=2,
                        domain="Quality",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Continuous Testing Suite",
                                description="Run automated tests across unit, integration, and contract layers.",
                                function_type="process",
                                is_automated=True,
                                automation_level=85,
                            ),
                            FunctionSpec(
                                name="Quality Gate Automation",
                                description="Enforce code quality, security, and policy checks in pipelines.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Performance & Resiliency Harness",
                                description="Execute load, chaos, and resiliency tests via automation.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Security Testing Pipeline",
                                description="Run SAST, DAST, and dependency scans automatically.",
                                function_type="process",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Developer Experience Portal",
                        description="Provide self-service workflows, documentation, and community feedback loops.",
                        level=2,
                        domain="Engineering",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Self-Service Catalog",
                                description="Expose services, environments, and templates through a unified portal.",
                                function_type="service",
                            ),
                            FunctionSpec(
                                name="Workflow Automation",
                                description="Guide developers through golden paths with integrated approvals.",
                                function_type="process",
                                is_automated=True,
                                automation_level=60,
                            ),
                            FunctionSpec(
                                name="Knowledge Base & Runbooks",
                                description="Publish playbooks, runbooks, and best practices.",
                            ),
                            FunctionSpec(
                                name="Feedback & Community Hub",
                                description="Collect feedback and support community interactions for platform teams.",
                                function_type="service",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="AI-Assisted Development",
                        description="Augment engineering workflows with AI guidance and governance.",
                        level=2,
                        domain="Engineering",
                        category="Differentiating",
                        capability_type="differentiating",
                        functions=[
                            FunctionSpec(
                                name="Code Co-Pilot Service",
                                description="Provide AI-assisted coding with policy-aware prompts.",
                                function_type="service",
                                is_automated=True,
                                automation_level=70,
                            ),
                            FunctionSpec(
                                name="Requirements-to-Test Generation",
                                description="Transform requirements into executable tests using AI.",
                                function_type="transformation",
                                is_automated=True,
                                automation_level=65,
                            ),
                            FunctionSpec(
                                name="Documentation Synthesizer",
                                description="Generate and update technical documentation automatically.",
                                function_type="transformation",
                                is_automated=True,
                                automation_level=60,
                            ),
                            FunctionSpec(
                                name="AI Tool Governance",
                                description="Manage policy, auditing, and governance for AI-assisted development.",
                                function_type="management",
                            ),
                        ],
                        children=[],
                    ),
                ],
                functions=[],
            ),
            CapabilitySpec(
                name="Brokering Capabilities",
                description="Enable mediation, integration, and traffic control across services.",
                level=1,
                domain="Integration",
                category="Core",
                capability_type="core",
                children=[
                    CapabilitySpec(
                        name="API Mediation Layer",
                        description="Provide API gateways, lifecycle management, and policy enforcement.",
                        level=2,
                        domain="Integration",
                        category="Core",
                        capability_type="core",
                        functions=[
                            FunctionSpec(
                                name="API Gateway & Rate Shaping",
                                description="Manage API traffic, quotas, and throttling policies.",
                                function_type="routing",
                            ),
                            FunctionSpec(
                                name="Protocol Transformation",
                                description="Convert between REST, GraphQL, SOAP, and custom protocols.",
                                function_type="transformation",
                            ),
                            FunctionSpec(
                                name="API Lifecycle & Monetization",
                                description="Manage API versions, subscriptions, and monetization.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Developer Onboarding & Keys",
                                description="Provision developer access, keys, and analytics dashboards.",
                                function_type="service",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Event Streaming & Choreography",
                        description="Manage event-driven architectures and orchestration patterns.",
                        level=2,
                        domain="Integration",
                        category="Core",
                        capability_type="core",
                        functions=[
                            FunctionSpec(
                                name="Event Bus Management",
                                description="Operate event brokers with partition, retention, and schema policies.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Schema & Contract Registry",
                                description="Govern event schemas and compatibility guarantees.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Stream Processing",
                                description="Process event streams with stateful and stateless operations.",
                                function_type="process",
                                is_automated=True,
                                automation_level=65,
                            ),
                            FunctionSpec(
                                name="Saga & Orchestration Framework",
                                description="Coordinate long-running transactions and compensating actions.",
                                function_type="process",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Integration Adapter Marketplace",
                        description="Provide reusable connectors, credentials, and connectivity monitoring.",
                        level=2,
                        domain="Integration",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Connector Library Curation",
                                description="Curate vendor and custom connectors with lifecycle management.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Adapter Development Sandbox",
                                description="Offer sandboxes and SDKs for building new adapters.",
                                function_type="service",
                            ),
                            FunctionSpec(
                                name="Credential Vaulting",
                                description="Securely manage integration credentials and rotation policies.",
                                function_type="security",
                            ),
                            FunctionSpec(
                                name="Connectivity Monitoring",
                                description="Monitor adapter health, throughput, and error rates.",
                                function_type="monitoring",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Data Virtualization & Semantic Mapping",
                        description="Expose unified data views and semantic layers with policy awareness.",
                        level=2,
                        domain="Data",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Logical Data Fabric",
                                description="Expose virtualized datasets with policy-driven access.",
                                function_type="service",
                            ),
                            FunctionSpec(
                                name="Semantic Model Catalog",
                                description="Manage semantic metadata and ontologies for shared understanding.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Query Federation",
                                description="Federate queries across heterogeneous data stores.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Policy-Aware Data Delivery",
                                description="Enforce privacy and usage policies during data delivery.",
                                function_type="security",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Service Mesh & Traffic Control",
                        description="Provide service-to-service communication, security, and observability.",
                        level=2,
                        domain="Integration",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Sidecar & Mesh Management",
                                description="Manage mesh configuration, lifecycle, and upgrades.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Zero-Trust Network Policy",
                                description="Enforce zero-trust policies and mTLS for service communication.",
                                function_type="security",
                            ),
                            FunctionSpec(
                                name="Circuit Breaking & QoS Routing",
                                description="Apply resilience patterns and quality-of-service routing.",
                                function_type="routing",
                            ),
                            FunctionSpec(
                                name="Runtime Topology Visualization",
                                description="Visualize live dependency graphs and latency heatmaps.",
                                function_type="monitoring",
                            ),
                        ],
                        children=[],
                    ),
                ],
                functions=[],
            ),
            CapabilitySpec(
                name="Management Utility Capabilities",
                description="Operate, govern, and sustain the application platform.",
                level=1,
                domain="Operations",
                category="Supporting",
                capability_type="supporting",
                children=[
                    CapabilitySpec(
                        name="Observability & Telemetry",
                        description="Collect metrics, traces, and logs with business KPI instrumentation.",
                        level=2,
                        domain="Operations",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Metrics Collection & Storage",
                                description="Ingest metrics with labels, retention, and downsampling policies.",
                                function_type="monitoring",
                            ),
                            FunctionSpec(
                                name="Distributed Tracing",
                                description="Trace end-to-end requests with sampling and baggage support.",
                                function_type="monitoring",
                            ),
                            FunctionSpec(
                                name="Log Aggregation & Analytics",
                                description="Aggregate structured/unstructured logs with query capabilities.",
                                function_type="analysis",
                            ),
                            FunctionSpec(
                                name="Business KPI Instrumentation",
                                description="Instrument and visualize product and business KPIs.",
                                function_type="analysis",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Configuration & Feature Management",
                        description="Manage configurations, feature flags, and compliance drift detection.",
                        level=2,
                        domain="Operations",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Centralized Config Service",
                                description="Provide configuration storage with versioning and rollout control.",
                                function_type="service",
                            ),
                            FunctionSpec(
                                name="Feature Flag Targeting",
                                description="Roll out features gradually with audience targeting.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Release Segmentation",
                                description="Segment releases by cohort, geography, or environment.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Compliance Drift Detection",
                                description="Detect configuration drift against baselines and policies.",
                                function_type="monitoring",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Policy & Compliance Automation",
                        description="Automate policy-as-code, compliance scanning, and remediation workflows.",
                        level=2,
                        domain="Governance",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Policy-as-Code Authoring",
                                description="Provide rule authoring, testing, and publishing workflows.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Automated Compliance Scanning",
                                description="Continuously scan infrastructure and applications for compliance drift.",
                                function_type="process",
                                is_automated=True,
                                automation_level=70,
                            ),
                            FunctionSpec(
                                name="Audit Evidence Vault",
                                description="Collect and store compliance evidence with lineage.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Remediation Workflow",
                                description="Trigger automated or guided remediation steps for violations.",
                                function_type="process",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="FinOps & Sustainability Management",
                        description="Optimize cost, capacity, and sustainability metrics for the platform.",
                        level=2,
                        domain="Finance",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Cost Allocation & Showback",
                                description="Allocate platform costs and provide showback/chargeback reporting.",
                                function_type="analysis",
                            ),
                            FunctionSpec(
                                name="Capacity & Right-Sizing Analytics",
                                description="Analyze utilization and recommend right-sizing actions.",
                                function_type="analysis",
                            ),
                            FunctionSpec(
                                name="Carbon Impact Tracking",
                                description="Estimate carbon footprint of workloads and suggest optimizations.",
                                function_type="analysis",
                            ),
                            FunctionSpec(
                                name="Optimization Recommendation Engine",
                                description="Provide actionable cost and sustainability recommendations.",
                                function_type="analysis",
                                is_automated=True,
                                automation_level=55,
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Incident & Operations Center",
                        description="Coordinate incident response, automation, and post-incident reviews.",
                        level=2,
                        domain="Operations",
                        category="Core",
                        capability_type="core",
                        functions=[
                            FunctionSpec(
                                name="Event Correlation & Alerting",
                                description="Correlate events, suppress noise, and route actionable alerts.",
                                function_type="monitoring",
                            ),
                            FunctionSpec(
                                name="Runbook Automation",
                                description="Automate runbooks and self-healing actions for common incidents.",
                                function_type="process",
                                is_automated=True,
                                automation_level=60,
                            ),
                            FunctionSpec(
                                name="Major Incident Command Center",
                                description="Coordinate stakeholders, communications, and resolution tasks.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Post-Incident Review Analytics",
                                description="Capture learnings and trend analysis from incidents.",
                                function_type="analysis",
                            ),
                        ],
                        children=[],
                    ),
                ],
                functions=[],
            ),
            CapabilitySpec(
                name="Information Provider Capabilities",
                description="Ingest, process, and serve data, content, and models across the platform.",
                level=1,
                domain="Data",
                category="Core",
                capability_type="core",
                children=[
                    CapabilitySpec(
                        name="Data Ingestion Gateways",
                        description="Collect batch and streaming data with governance and quality gates.",
                        level=2,
                        domain="Data",
                        category="Core",
                        capability_type="core",
                        functions=[
                            FunctionSpec(
                                name="Batch & Streaming Ingestion",
                                description="Ingest batch files, CDC streams, and sensor telemetry.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Source Connector Management",
                                description="Operate source connectors with schema evolution controls.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Data Quality Gate",
                                description="Validate incoming data with quality and lineage checks.",
                                function_type="process",
                                is_automated=True,
                                automation_level=65,
                            ),
                            FunctionSpec(
                                name="Landing Zone Governance",
                                description="Manage landing zones, retention, and access policies.",
                                function_type="management",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Data Processing & Enrichment",
                        description="Transform, enrich, and validate data for downstream consumption.",
                        level=2,
                        domain="Data",
                        category="Core",
                        capability_type="core",
                        functions=[
                            FunctionSpec(
                                name="ETL/ELT Orchestration",
                                description="Manage data pipelines with scheduling and dependency tracking.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Real-Time Enrichment Pipelines",
                                description="Enrich streaming data with reference datasets in real time.",
                                function_type="process",
                                is_automated=True,
                                automation_level=70,
                            ),
                            FunctionSpec(
                                name="Metadata & Lineage Capture",
                                description="Capture metadata, lineage, and dataset provenance.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Validation & Anomaly Detection",
                                description="Detect anomalies and data drift during processing.",
                                function_type="analysis",
                                is_automated=True,
                                automation_level=65,
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Knowledge Graph & Master Data Services",
                        description="Provide master data, knowledge graphs, and semantic query APIs.",
                        level=2,
                        domain="Data",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Entity Resolution",
                                description="Resolve entities and relationships across data domains.",
                                function_type="analysis",
                            ),
                            FunctionSpec(
                                name="Hierarchy & Relationship Management",
                                description="Manage hierarchies, relationships, and version histories.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Golden Record Stewardship",
                                description="Maintain master records with stewardship workflows.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Semantic Query APIs",
                                description="Expose knowledge graph queries through APIs.",
                                function_type="service",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Content & Object Storage Federation",
                        description="Manage content storage, indexing, and distribution with lifecycle policies.",
                        level=2,
                        domain="Data",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Unified Content Indexing",
                                description="Index and classify content across multiple repositories.",
                                function_type="analysis",
                            ),
                            FunctionSpec(
                                name="Storage Lifecycle Policies",
                                description="Apply retention, tiering, and archival policies.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Secure Content Distribution",
                                description="Distribute content with watermarking and DRM.",
                                function_type="security",
                            ),
                            FunctionSpec(
                                name="Legal Hold Management",
                                description="Enforce legal holds and eDiscovery requirements.",
                                function_type="management",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Analytic & Model Serving Endpoints",
                        description="Serve analytics, models, and features to applications and workflows.",
                        level=2,
                        domain="Data",
                        category="Differentiating",
                        capability_type="differentiating",
                        functions=[
                            FunctionSpec(
                                name="Feature Store Management",
                                description="Manage feature definitions, lineage, and freshness.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Online/Offline Inference",
                                description="Serve ML models for batch and real-time inference.",
                                function_type="service",
                                is_automated=True,
                                automation_level=70,
                            ),
                            FunctionSpec(
                                name="Model Deployment & Promotion",
                                description="Deploy and promote models with testing and approval workflows.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Model Monitoring & Drift Detection",
                                description="Monitor model performance, fairness, and drift.",
                                function_type="analysis",
                            ),
                        ],
                        children=[],
                    ),
                ],
                functions=[],
            ),
            CapabilitySpec(
                name="Cross-Cutting Qualities",
                description="Cross-capability qualities governing performance, policy, security, mobility, and privacy.",
                level=1,
                domain="Governance",
                category="Supporting",
                capability_type="supporting",
                children=[
                    CapabilitySpec(
                        name="Performance & SLO Governance",
                        description="Define, monitor, and enforce service-level objectives across the platform.",
                        level=2,
                        domain="Operations",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="SLO Definition Registry",
                                description="Maintain SLOs, error budgets, and contracts.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Synthetic Monitoring",
                                description="Run synthetic checks aligned to SLO targets.",
                                function_type="monitoring",
                            ),
                            FunctionSpec(
                                name="Auto-Scaling Policies",
                                description="Apply scaling policies based on performance guardrails.",
                                function_type="process",
                                is_automated=True,
                                automation_level=60,
                            ),
                            FunctionSpec(
                                name="Continuous Performance Review",
                                description="Conduct recurring reviews of performance metrics and actions.",
                                function_type="process",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Management Policy Controls",
                        description="Manage policy catalogs, change impact, and delegated approvals.",
                        level=2,
                        domain="Governance",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Policy Catalog & Versioning",
                                description="Catalogue policies with version control and lifecycle states.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Control Mapping Automation",
                                description="Map policies to controls and automated checks.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Change Impact Analysis",
                                description="Assess policy change impact across capabilities.",
                                function_type="analysis",
                            ),
                            FunctionSpec(
                                name="Delegated Approval Workflow",
                                description="Route policy and control changes through delegated approvals.",
                                function_type="process",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Zero-Trust Security Fabric",
                        description="Provide identity orchestration, secrets, posture assessment, and threat detection.",
                        level=2,
                        domain="Security",
                        category="Core",
                        capability_type="core",
                        functions=[
                            FunctionSpec(
                                name="Identity & Access Orchestration",
                                description="Orchestrate identities, federation, and access policies.",
                                function_type="security",
                            ),
                            FunctionSpec(
                                name="Secrets & Key Management",
                                description="Manage secrets, keys, and rotation policies centrally.",
                                function_type="security",
                            ),
                            FunctionSpec(
                                name="Continuous Posture Assessment",
                                description="Continuously assess workloads and configurations for risk.",
                                function_type="security",
                                is_automated=True,
                                automation_level=65,
                            ),
                            FunctionSpec(
                                name="Threat Detection & Response",
                                description="Detect threats and orchestrate automated responses.",
                                function_type="security",
                                is_automated=True,
                                automation_level=60,
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Mobility & Resiliency Enablement",
                        description="Provide session continuity, edge routing, chaos testing, and global failover.",
                        level=2,
                        domain="Operations",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Session Continuity Service",
                                description="Maintain seamless sessions across networks and devices.",
                                function_type="service",
                            ),
                            FunctionSpec(
                                name="Edge-Aware Routing",
                                description="Route traffic based on device, location, and network quality.",
                                function_type="routing",
                            ),
                            FunctionSpec(
                                name="Resiliency Testing (Chaos)",
                                description="Conduct regular chaos and failover tests.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Global Failover Orchestration",
                                description="Orchestrate cross-region failover with minimal disruption.",
                                function_type="process",
                                automation_level=55,
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Compliance & Audit Automation",
                        description="Automate evidence collection, reporting, and residency enforcement.",
                        level=2,
                        domain="Governance",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Evidence Collection Automation",
                                description="Automate evidence gathering for audits and attestations.",
                                function_type="process",
                                is_automated=True,
                                automation_level=65,
                            ),
                            FunctionSpec(
                                name="Regulator-Ready Reporting",
                                description="Produce reports aligned to regulatory templates.",
                                function_type="process",
                            ),
                            FunctionSpec(
                                name="Data Residency Enforcement",
                                description="Ensure workloads comply with residency requirements.",
                                function_type="security",
                            ),
                            FunctionSpec(
                                name="Exception Management",
                                description="Handle compliance exceptions with remediation tracking.",
                                function_type="process",
                            ),
                        ],
                        children=[],
                    ),
                    CapabilitySpec(
                        name="Privacy-by-Design Management",
                        description="Embed privacy practices, consent handling, and privacy impact assessments.",
                        level=2,
                        domain="Governance",
                        category="Supporting",
                        capability_type="supporting",
                        functions=[
                            FunctionSpec(
                                name="Data Minimization Catalog",
                                description="Catalogue data minimization requirements and practices.",
                                function_type="management",
                            ),
                            FunctionSpec(
                                name="Consent Preference Services",
                                description="Manage user consent, preferences, and revocation.",
                                function_type="service",
                            ),
                            FunctionSpec(
                                name="Differential Privacy Toolkit",
                                description="Apply differential privacy techniques to datasets.",
                                function_type="security",
                            ),
                            FunctionSpec(
                                name="Privacy Impact Assessment Pipeline",
                                description="Automate privacy impact assessments for changes.",
                                function_type="process",
                            ),
                        ],
                        children=[],
                    ),
                ],
                functions=[],
            ),
        ],
        functions=[],
    )


CATALOG_ROOT = _build_catalog()


def ensure_capabilities_seeded() -> None:
    """Ensure catalog capabilities and functions exist in the database."""
    created = 0

    def get_or_create_capability(
        spec: CapabilitySpec, parent: Optional[UnifiedCapability]
    ) -> UnifiedCapability:
        nonlocal created
        existing = UnifiedCapability.query.filter_by(name=spec.name).first()
        if existing:
            # Update core attributes if missing to keep catalog authoritative.
            updated = False
            if existing.level != spec.level:
                existing.level = spec.level
                updated = True
            # Map domain string to BusinessDomain
            if spec.domain:
                domain = BusinessDomain.query.filter_by(code=spec.domain.upper()).first()
                if domain and existing.domain_id != domain.id:
                    existing.domain_id = domain.id
                    updated = True
            if spec.category and existing.category != spec.category:
                existing.category = spec.category
                updated = True
            if spec.capability_type and existing.capability_type != spec.capability_type:
                existing.capability_type = spec.capability_type
                updated = True
            if spec.description and existing.description != spec.description:
                existing.description = spec.description
                updated = True
            if parent and existing.parent_capability_id != parent.id:
                existing.parent_capability_id = parent.id
                updated = True
            if updated:
                db.session.add(existing)
            return existing

        # Map domain string to BusinessDomain
        domain = None
        if spec.domain:
            domain = BusinessDomain.query.filter_by(code=spec.domain.upper()).first()
            if not domain:
                # Create domain if it doesn't exist
                domain = BusinessDomain(
                    code=spec.domain.upper(),
                    name=spec.domain.title(),
                    description=f"{spec.domain.title()} domain",
                    domain_type="primary"
                    if spec.capability_type in {"core", "differentiating"}
                    else "supporting",
                )
                db.session.add(domain)
                db.session.flush()

        from app.modules.capabilities.services.capability_service import (
            ensure_capability_record,
        )

        capability, was_created = ensure_capability_record(
            name=spec.name,
            level=spec.level,
            description=spec.description,
            domain_id=domain.id if domain else None,
            category=spec.category,
            capability_type=spec.capability_type,
            parent_capability_id=parent.id if parent else None,
            strategic_importance="critical"
            if spec.capability_type in {"core", "differentiating"}
            else "high",
            business_criticality="mission_critical"
            if spec.capability_type == "core"
            else "important",
            current_maturity_level=2,
            target_maturity_level=4,
            discovered_by_ai=False,
            status="defined",
        )
        if was_created:
            created += 1
        return capability

    def ensure_function(capability: UnifiedCapability, spec: FunctionSpec) -> None:
        nonlocal created
        existing = BusinessFunction.query.filter_by(
            capability_id=capability.id, name=spec.name
        ).first()
        if existing:
            updated = False
            if spec.description and existing.description != spec.description:
                existing.description = spec.description
                updated = True
            if spec.function_type and existing.function_type != spec.function_type:
                existing.function_type = spec.function_type
                updated = True
            if existing.is_automated != spec.is_automated:
                existing.is_automated = spec.is_automated
                updated = True
            if existing.automation_level != spec.automation_level:
                existing.automation_level = spec.automation_level
                updated = True
            if updated:
                db.session.add(existing)
            return

        archimate_element = ArchiMateElement(
            name=spec.name,
            type="ApplicationFunction",
            layer="application",
            description=spec.description,
        )
        db.session.add(archimate_element)
        db.session.flush()

        function = BusinessFunction(
            capability_id=capability.id,
            name=spec.name,
            description=spec.description,
            function_type=spec.function_type,
            is_automated=spec.is_automated,
            automation_level=spec.automation_level,
            automation_potential=max(spec.automation_level, 50) if spec.automation_level else 50,
            inputs=json.dumps([]),
            outputs=json.dumps([]),
            archimate_element_id=archimate_element.id,
            discovered_by_ai=False,
        )
        db.session.add(function)
        created += 1

    def walk(spec: CapabilitySpec, parent: Optional[UnifiedCapability]) -> None:
        capability = get_or_create_capability(spec, parent)
        for function_spec in spec.functions:
            ensure_function(capability, function_spec)
        for child in spec.children:
            walk(child, capability)

    walk(CATALOG_ROOT, None)
    if created:
        db.session.commit()
    else:
        db.session.flush()


def _capability_to_dict(capability: CapabilitySpec, parent: Optional[UnifiedCapability]) -> Dict:
    """Convert catalog spec to dictionary with database identifiers."""
    db_capability = UnifiedCapability.query.filter_by(name=capability.name).first()
    capability_id = db_capability.id if db_capability else None
    result = {
        "name": capability.name,
        "description": capability.description,
        "level": capability.level,
        "domain": capability.domain,
        "category": capability.category,
        "capability_type": capability.capability_type,
        "id": capability_id,
        "functions": [],
        "children": [],
    }

    if db_capability:
        functions = (
            BusinessFunction.query.filter_by(capability_id=db_capability.id)
            .order_by(BusinessFunction.name)
            .all()
        )
        for function in functions:
            result["functions"].append(
                {
                    "id": function.id,
                    "name": function.name,
                    "description": function.description,
                    "function_type": function.function_type,
                    "automation_level": function.automation_level,
                }
            )

    for child_spec in capability.children:
        result["children"].append(_capability_to_dict(child_spec, db_capability))

    return result


def get_catalog_with_ids() -> Dict:
    """Return the catalog structure enriched with database identifiers."""
    ensure_capabilities_seeded()
    return _capability_to_dict(CATALOG_ROOT, None)


def flatten_level_two_capabilities() -> List[Dict]:
    """Return a flat list of level 2 capabilities with IDs and parent grouping."""
    catalog = get_catalog_with_ids()
    level_two: List[Dict] = []

    def collect(node: Dict, parent: Optional[Dict]) -> None:
        if node["level"] == 2:
            level_two.append(
                {
                    "id": node["id"],
                    "name": node["name"],
                    "description": node["description"],
                    "parent_name": parent["name"] if parent else None,
                    "parent_id": parent["id"] if parent else None,
                    "functions": node.get("functions", []),
                    "domain": node.get("domain"),
                    "category": node.get("category"),
                    "capability_type": node.get("capability_type"),
                    "level": node.get("level"),
                }
            )
        for child in node.get("children", []):
            collect(child, node)

    for child in catalog.get("children", []):
        collect(child, catalog)

    return level_two


def build_hierarchical_catalog() -> List[Dict]:
    """Return a hierarchical view suitable for UI rendering."""
    ensure_capabilities_seeded()
    root = get_catalog_with_ids()
    return root.get("children", [])


@dataclass
class SeedingResult:
    """Result of capability seeding operation."""

    capabilities_created: int
    functions_created: int
    errors: List[str]


class ApplicationCapabilityCatalogService:
    """Service for managing the application capability catalog."""

    @staticmethod
    # @transactional  # Temporarily disabled
    def seed_capabilities() -> SeedingResult:
        """
        Ensure the capability catalog is seeded with canonical capabilities.

        Returns:
            SeedingResult with counts and any errors
        """
        result = SeedingResult(capabilities_created=0, functions_created=0, errors=[])

        try:
            # Call the existing function
            ensure_capabilities_seeded()
            logger.info("Capability catalog seeding completed successfully")
        except Exception as e:
            logger.error(f"Failed to seed capability catalog: {e}")
            result.errors.append(str(e))

        return result

    @staticmethod
    def get_catalog_hierarchy() -> Dict:
        """Get the complete capability catalog hierarchy."""
        catalog = _build_catalog()

        def spec_to_dict(spec: CapabilitySpec) -> Dict:
            return {
                "name": spec.name,
                "description": spec.description,
                "level": spec.level,
                "domain": spec.domain,
                "category": spec.category,
                "capability_type": spec.capability_type,
                "functions": [
                    {
                        "name": f.name,
                        "description": f.description,
                        "function_type": f.function_type,
                        "is_automated": f.is_automated,
                        "automation_level": f.automation_level,
                    }
                    for f in spec.functions
                ],
                "children": [spec_to_dict(child) for child in spec.children],
            }

        return spec_to_dict(catalog)

    @staticmethod
    def validate_capability_structure() -> Dict:
        """Validate the capability catalog structure."""
        catalog = _build_catalog()
        validation_result = {
            "is_valid": True,
            "errors": [],
            "warnings": [],
            "statistics": {
                "total_capabilities": 0,
                "total_functions": 0,
                "max_depth": 0,
                "domains": set(),
                "categories": set(),
                "capability_types": set(),
            },
        }

        def validate_spec(spec: CapabilitySpec, depth: int = 0):
            validation_result["statistics"]["total_capabilities"] += 1
            validation_result["statistics"]["total_functions"] += len(spec.functions)
            validation_result["statistics"]["max_depth"] = max(
                validation_result["statistics"]["max_depth"], depth
            )
            validation_result["statistics"]["domains"].add(spec.domain)
            validation_result["statistics"]["categories"].add(spec.category)
            validation_result["statistics"]["capability_types"].add(spec.capability_type)

            # Validate required fields
            if not spec.name or not spec.name.strip():
                validation_result["errors"].append(
                    f"Capability at level {spec.level} has empty name"
                )
                validation_result["is_valid"] = False

            if not spec.description or not spec.description.strip():
                validation_result["warnings"].append(
                    f"Capability '{spec.name}' has empty description"
                )

            # Validate functions
            for func in spec.functions:
                if not func.name or not func.name.strip():
                    validation_result["errors"].append(
                        f"Function in capability '{spec.name}' has empty name"
                    )
                    validation_result["is_valid"] = False

                if func.automation_level < 0 or func.automation_level > 100:
                    validation_result["warnings"].append(
                        f"Function '{func.name}' has invalid automation level: {func.automation_level}"
                    )

            # Recursively validate children
            for child in spec.children:
                validate_spec(child, depth + 1)

        validate_spec(catalog)

        # Convert sets to lists for JSON serialization
        validation_result["statistics"]["domains"] = list(
            validation_result["statistics"]["domains"]
        )
        validation_result["statistics"]["categories"] = list(
            validation_result["statistics"]["categories"]
        )
        validation_result["statistics"]["capability_types"] = list(
            validation_result["statistics"]["capability_types"]
        )

        return validation_result

    def get_portfolio(self, domain=None, criticality=None, status=None):
        """Application portfolio with optional domain/criticality/status filters."""
        from app.models.application_portfolio import ApplicationComponent
        q = ApplicationComponent.query
        if domain and hasattr(ApplicationComponent, "business_domain"):
            q = q.filter(ApplicationComponent.business_domain == domain)
        if criticality and hasattr(ApplicationComponent, "criticality"):
            q = q.filter(ApplicationComponent.criticality == criticality)
        if status and hasattr(ApplicationComponent, "lifecycle_status"):
            q = q.filter(ApplicationComponent.lifecycle_status == status)
        return [
            {
                "id": a.id,
                "name": a.name,
                "business_owner": getattr(a, "business_owner", None),
                "criticality": getattr(a, "criticality", None) or getattr(a, "business_criticality", None),
                "lifecycle_status": getattr(a, "lifecycle_status", None),
                "business_domain": getattr(a, "business_domain", None),
            }
            for a in q.limit(1000).all()
        ]

    def get_application_capabilities(self, app_id):
        """Capabilities mapped to a specific application."""
        from app.models.application_capability import ApplicationCapabilityMapping
        from app.models.business_capabilities import BusinessCapability
        # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
        maps = ApplicationCapabilityMapping.query.filter_by(
            application_component_id=app_id
        ).all()
        out = []
        for mp in maps:
            cap = BusinessCapability.query.get(mp.business_capability_id) if getattr(mp, "business_capability_id", None) else None
            if cap:
                out.append({"id": cap.id, "name": cap.name, "code": getattr(cap, "code", None), "mapping_id": mp.id})
        return out
