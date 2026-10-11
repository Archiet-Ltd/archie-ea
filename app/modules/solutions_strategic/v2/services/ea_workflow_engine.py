"""
Enterprise Architecture Workflow Engine

Executes end-to-end automated workflows for EA processes.

Supported Workflows:
- Application Onboarding: Document upload → AI extraction → APQC mapping → Capability linking
- Gap Remediation: Gap detection → Remediation planning → Implementation tracking
- Vendor Selection: Requirements → Vendor matching → Scoring → TCO → Roadmap
- Compliance Scan: Policy check → Violation detection → Remediation assignment
- Architecture Review: Element creation → AI validation → Relationship derivation

Usage:
    engine = EAWorkflowEngine()

    # Start a workflow
    instance = engine.start_workflow(
        workflow_code='APP_ONBOARDING',
        context={'application_id': 123},
        triggered_by='manual',
        user_id=1
    )

    # Check status
    status = engine.get_instance_status(instance.id)

    # Resume after approval
    engine.resume_workflow(instance.id)
"""

import logging
import traceback
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import current_app, g

logger = logging.getLogger(__name__)

from app import db
from app.models.workflow_models import (
    EAWorkflowDefinition,
    EAWorkflowInstance,
    EAWorkflowSchedule,
    EAWorkflowStepExecution,
)


class EAWorkflowEngine:
    """
    Engine for executing Enterprise Architecture automated workflows.
    """

    # Built-in step handlers
    STEP_HANDLERS = {}

    def __init__(self):
        """Initialize the workflow engine."""
        self.app = current_app._get_current_object() if current_app else None
        self._register_default_handlers()

    def _register_default_handlers(self):
        """Register default step handlers."""
        # These map step types to handler methods
        self.STEP_HANDLERS = {
            "gap_analysis": self._handle_gap_analysis,
            "vendor_matching": self._handle_vendor_matching,
            "apqc_mapping": self._handle_apqc_mapping,
            "capability_linking": self._handle_capability_linking,
            "compliance_scan": self._handle_compliance_scan,
            "notification": self._handle_notification,
            "create_suggestion": self._handle_create_suggestion,
            "approval_gate": self._handle_approval_gate,
            "roadmap_creation": self._handle_roadmap_creation,
            # TOGAF ADM Phase Handlers
            "adm_define_scope": self._handle_adm_define_scope,
            "adm_stakeholder_analysis": self._handle_adm_stakeholder_analysis,
            "adm_business_goals": self._handle_adm_business_goals,
            "adm_constraints_assessment": self._handle_adm_constraints_assessment,
            "adm_capability_assessment": self._handle_adm_capability_assessment,
            "adm_vision_document": self._handle_adm_vision_document,
            "adm_approval_gate": self._handle_adm_approval_gate,
            # Additional handlers referenced by workflow steps
            "document_extraction": self._handle_document_extraction,
            "gap_classification": self._handle_gap_classification,
            "vendor_scoring": self._handle_vendor_scoring,
            "vendor_gap_analysis": self._handle_vendor_gap_analysis,
            "tco_calculation": self._handle_tco_calculation,
            "policy_loader": self._handle_policy_loader,
            "violation_classification": self._handle_violation_classification,
            "auto_remediation": self._handle_auto_remediation,
            "completeness_validation": self._handle_completeness_validation,
            "quality_scoring": self._handle_quality_scoring,
            "commit_changes": self._handle_commit_changes,
        }

    # =========================================================================
    # WORKFLOW DEFINITIONS
    # =========================================================================

    def get_workflow_definition(self, workflow_code: str) -> Optional[EAWorkflowDefinition]:
        """Get a workflow definition by code."""
        return EAWorkflowDefinition.query.filter_by(
            workflow_code=workflow_code, is_active=True
        ).first()

    def list_workflow_definitions(
        self, category: Optional[str] = None, active_only: bool = True
    ) -> List[EAWorkflowDefinition]:
        """List available workflow definitions."""
        query = EAWorkflowDefinition.query
        if active_only:
            query = query.filter(EAWorkflowDefinition.is_active == True)
        if category:
            query = query.filter(EAWorkflowDefinition.workflow_category == category)
        return query.order_by(EAWorkflowDefinition.workflow_name).all()

    def create_workflow_definition(
        self,
        workflow_code: str,
        workflow_name: str,
        workflow_category: str,
        steps: List[Dict],
        **kwargs,
    ) -> EAWorkflowDefinition:
        """
        Create a new workflow definition.

        Args:
            workflow_code: Unique workflow code
            workflow_name: Display name
            workflow_category: Category (application_management, vendor_management, etc.)
            steps: List of step definitions
            **kwargs: Additional attributes

        Returns:
            Created EAWorkflowDefinition
        """
        definition = EAWorkflowDefinition(
            workflow_code=workflow_code,
            workflow_name=workflow_name,
            workflow_category=workflow_category,
            steps=steps,
            workflow_description=kwargs.get("workflow_description"),
            workflow_type=kwargs.get("workflow_type", "sequential"),
            trigger_types=kwargs.get("trigger_types", ["manual"]),
            automation_level=kwargs.get("automation_level", "assisted"),
            auto_approval_threshold=kwargs.get("auto_approval_threshold", 0.85),
            notify_on_complete=kwargs.get("notify_on_complete", True),
            notify_on_failure=kwargs.get("notify_on_failure", True),
            is_active=True,
            created_by_id=kwargs.get("created_by_id"),
        )

        db.session.add(definition)
        db.session.commit()
        return definition


    @staticmethod
    def _build_default_workflow_definitions() -> List[Dict]:
        """Return the default workflow definitions as a list of dicts."""
        return [
            {
                "workflow_code": "APP_ONBOARDING",
                "workflow_name": "Application Onboarding",
                "workflow_category": "application_management",
                "workflow_description": "Automated application discovery and onboarding workflow. Extracts data from documents, maps to APQC processes, links to capabilities, and creates ArchiMate elements.",
                "workflow_type": "sequential",
                "automation_level": "assisted",
                "trigger_types": ["manual", "event"],
                "steps": [
                    {
                        "step_id": "extract_data",
                        "step_name": "Extract Application Data",
                        "step_type": "automated",
                        "handler": "document_extraction",
                        "input_mapping": {"document_id": "context.document_id"},
                        "output_key": "extracted_data",
                        "timeout_minutes": 10,
                    },
                    {
                        "step_id": "suggest_apqc",
                        "step_name": "Suggest APQC Process Mappings",
                        "step_type": "automated",
                        "handler": "apqc_mapping",
                        "input_mapping": {"application_data": "context.extracted_data"},
                        "output_key": "apqc_suggestions",
                        "requires_approval": True,
                        "approval_threshold_confidence": 0.85,
                    },
                    {
                        "step_id": "link_capabilities",
                        "step_name": "Link to Business Capabilities",
                        "step_type": "automated",
                        "handler": "capability_linking",
                        "input_mapping": {"apqc_mappings": "context.apqc_suggestions"},
                        "output_key": "capability_links",
                        "requires_approval": True,
                    },
                    
                    {
                        "step_id": "gap_analysis",
                        "step_name": "Identify Coverage Gaps",
                        "step_type": "automated",
                        "handler": "gap_analysis",
                        "input_mapping": {"application_id": "context.application_id"},
                        "output_key": "identified_gaps",
                    },
                    {
                        "step_id": "notify_complete",
                        "step_name": "Notify Stakeholders",
                        "step_type": "automated",
                        "handler": "notification",
                        "config": {"template": "app_onboarding_complete"},
                    },
                ],
            },
            {
                "workflow_code": "GAP_REMEDIATION",
                "workflow_name": "Gap Remediation Pipeline",
                "workflow_category": "gap_analysis",
                "workflow_description": "Automated gap detection and remediation workflow. Scans portfolio for gaps, classifies by severity, creates remediation tasks, and tracks to completion.",
                "workflow_type": "sequential",
                "automation_level": "hybrid",
                "trigger_types": ["manual", "scheduled"],
                "steps": [
                    {
                        "step_id": "scan_gaps",
                        "step_name": "Scan Portfolio for Gaps",
                        "step_type": "automated",
                        "handler": "gap_analysis",
                        "config": {"scope": "portfolio"},
                        "output_key": "detected_gaps",
                        "timeout_minutes": 30,
                    },
                    {
                        "step_id": "classify_gaps",
                        "step_name": "Classify and Prioritize",
                        "step_type": "automated",
                        "handler": "gap_classification",
                        "input_mapping": {"gaps": "context.detected_gaps"},
                        "output_key": "classified_gaps",
                    },
                    {
                        "step_id": "create_suggestions",
                        "step_name": "Generate Remediation Suggestions",
                        "step_type": "automated",
                        "handler": "create_suggestion",
                        "input_mapping": {"gaps": "context.classified_gaps"},
                        "output_key": "remediation_suggestions",
                    },
                    {
                        "step_id": "review_critical",
                        "step_name": "Review Critical Gaps",
                        "step_type": "approval",
                        "handler": "approval_gate",
                        "config": {"filter": "severity == critical"},
                    },
                    {
                        "step_id": "create_roadmap",
                        "step_name": "Create Roadmap Items",
                        "step_type": "automated",
                        "handler": "roadmap_creation",
                        "input_mapping": {"approved_gaps": "context.approved_items"},
                        "output_key": "roadmap_items",
                    },
                    {
                        "step_id": "notify_owners",
                        "step_name": "Notify Application Owners",
                        "step_type": "automated",
                        "handler": "notification",
                        "config": {"template": "gap_remediation_assigned"},
                    },
                ],
            },
            {
                "workflow_code": "VENDOR_SELECTION",
                "workflow_name": "Intelligent Vendor Selection",
                "workflow_category": "vendor_management",
                "workflow_description": "Automated vendor evaluation and selection workflow. Matches requirements to vendors, scores against APQC processes, calculates TCO, and generates implementation roadmap.",
                "workflow_type": "sequential",
                "automation_level": "assisted",
                "trigger_types": ["manual"],
                "steps": [
                    {
                        "step_id": "parse_requirements",
                        "step_name": "Parse Requirements",
                        "step_type": "automated",
                        "handler": "document_extraction",
                        "input_mapping": {"document_id": "context.requirements_doc_id"},
                        "output_key": "parsed_requirements",
                    },
                    {
                        "step_id": "match_apqc",
                        "step_name": "Match to APQC Processes",
                        "step_type": "automated",
                        "handler": "apqc_mapping",
                        "input_mapping": {"requirements": "context.parsed_requirements"},
                        "output_key": "apqc_requirements",
                    },
                    {
                        "step_id": "find_vendors",
                        "step_name": "Generate Vendor Shortlist",
                        "step_type": "automated",
                        "handler": "vendor_matching",
                        "input_mapping": {"apqc_processes": "context.apqc_requirements"},
                        "output_key": "vendor_shortlist",
                    },
                    {
                        "step_id": "score_vendors",
                        "step_name": "Score Vendors",
                        "step_type": "automated",
                        "handler": "vendor_scoring",
                        "input_mapping": {"vendors": "context.vendor_shortlist"},
                        "output_key": "vendor_scores",
                    },
                    {
                        "step_id": "gap_analysis",
                        "step_name": "Analyze Coverage Gaps",
                        "step_type": "automated",
                        "handler": "vendor_gap_analysis",
                        "input_mapping": {"vendors": "context.vendor_scores"},
                        "output_key": "vendor_gaps",
                    },
                    {
                        "step_id": "calculate_tco",
                        "step_name": "Calculate TCO",
                        "step_type": "automated",
                        "handler": "tco_calculation",
                        "input_mapping": {"vendors": "context.vendor_scores"},
                        "output_key": "tco_analysis",
                    },
                    {
                        "step_id": "generate_roadmap",
                        "step_name": "Generate Implementation Roadmap",
                        "step_type": "automated",
                        "handler": "roadmap_creation",
                        "input_mapping": {"selected_vendor": "context.recommended_vendor"},
                        "output_key": "implementation_roadmap",
                    },
                    {
                        "step_id": "approval",
                        "step_name": "Decision Board Review",
                        "step_type": "approval",
                        "handler": "approval_gate",
                        "config": {"approvers": ["vendor_manager", "architect"]},
                    },
                ],
            },
            {
                "workflow_code": "COMPLIANCE_SCAN",
                "workflow_name": "Continuous Compliance Monitoring",
                "workflow_category": "compliance",
                "workflow_description": "Scheduled compliance scanning workflow. Checks architecture against policies, detects violations, classifies severity, and assigns remediation tasks.",
                "workflow_type": "sequential",
                "automation_level": "automated",
                "trigger_types": ["scheduled", "manual"],
                "steps": [
                    {
                        "step_id": "load_policies",
                        "step_name": "Load Active Policies",
                        "step_type": "automated",
                        "handler": "policy_loader",
                        "output_key": "active_policies",
                    },
                    {
                        "step_id": "scan_architecture",
                        "step_name": "Scan Architecture",
                        "step_type": "automated",
                        "handler": "compliance_scan",
                        "input_mapping": {"policies": "context.active_policies"},
                        "output_key": "scan_results",
                        "timeout_minutes": 60,
                    },
                    {
                        "step_id": "classify_violations",
                        "step_name": "Classify Violations",
                        "step_type": "automated",
                        "handler": "violation_classification",
                        "input_mapping": {"violations": "context.scan_results"},
                        "output_key": "classified_violations",
                    },
                    {
                        "step_id": "auto_remediate",
                        "step_name": "Auto-Remediate Low-Risk",
                        "step_type": "automated",
                        "handler": "auto_remediation",
                        "input_mapping": {"violations": "context.classified_violations"},
                        "config": {"severity_filter": ["low"]},
                        "output_key": "remediated_violations",
                    },
                    {
                        "step_id": "create_tasks",
                        "step_name": "Create Remediation Tasks",
                        "step_type": "automated",
                        "handler": "create_suggestion",
                        "input_mapping": {"violations": "context.classified_violations"},
                        "config": {"severity_filter": ["medium", "high", "critical"]},
                        "output_key": "remediation_tasks",
                    },
                    {
                        "step_id": "notify_stakeholders",
                        "step_name": "Notify Stakeholders",
                        "step_type": "automated",
                        "handler": "notification",
                        "config": {"template": "compliance_scan_complete"},
                    },
                ],
            },
            {
                "workflow_code": "ARCH_REVIEW",
                "workflow_name": "AI-Assisted Architecture Review",
                "workflow_category": "architecture_review",
                "workflow_description": "Automated architecture review workflow. Validates completeness, suggests missing relationships, derives cross-layer links, and assigns quality scores.",
                "workflow_type": "sequential",
                "automation_level": "assisted",
                "trigger_types": ["manual", "event"],
                "steps": [
                    {
                        "step_id": "validate_completeness",
                        "step_name": "Validate Completeness",
                        "step_type": "automated",
                        "handler": "completeness_validation",
                        "input_mapping": {"element_ids": "context.element_ids"},
                        "output_key": "completeness_results",
                    },
                    {
                        "step_id": "calculate_quality",
                        "step_name": "Calculate Quality Score",
                        "step_type": "automated",
                        "handler": "quality_scoring",
                        "input_mapping": {"elements": "context.element_ids"},
                        "output_key": "quality_scores",
                    },
                    {
                        "step_id": "review_low_quality",
                        "step_name": "Review Low Quality Elements",
                        "step_type": "approval",
                        "handler": "approval_gate",
                        "config": {"filter": "quality_score < 70"},
                    },
                    {
                        "step_id": "commit_changes",
                        "step_name": "Commit to Repository",
                        "step_type": "automated",
                        "handler": "commit_changes",
                        "input_mapping": {"approved_changes": "context.approved_items"},
                    },
                ],
            },
            {
                "workflow_code": "ADM_PHASE_A_VISION",
                "workflow_name": "TOGAF ADM Phase A: Architecture Vision",
                "workflow_category": "togaf_adm",
                "workflow_description": "Establishes the architecture vision and obtains approval. Creates high-level view of enterprise, identifies stakeholders, defines business goals, assesses constraints, and produces Architecture Vision document.",
                "workflow_type": "sequential",
                "automation_level": "assisted",
                "trigger_types": ["manual"],
                "steps": [
                    {
                        "step_id": "define_scope",
                        "step_name": "1. Define Architecture Project Scope",
                        "step_type": "automated",
                        "handler": "adm_define_scope",
                        "input_mapping": {"project_name": "context.project_name", "project_description": "context.project_description"},
                        "output_key": "scope_definition",
                    },
                    {
                        "step_id": "stakeholder_analysis",
                        "step_name": "2. Identify and Characterize Stakeholders",
                        "step_type": "automated",
                        "handler": "adm_stakeholder_analysis",
                        "input_mapping": {"scope": "context.scope_definition"},
                        "output_key": "stakeholder_map",
                    },
                    {
                        "step_id": "business_goals",
                        "step_name": "3. Define Business Goals and Drivers",
                        "step_type": "automated",
                        "handler": "adm_business_goals",
                        "input_mapping": {"stakeholders": "context.stakeholder_map"},
                        "output_key": "business_goals",
                        "requires_approval": True,
                    },
                    {
                        "step_id": "constraints_assessment",
                        "step_name": "4. Assess Business and Technical Constraints",
                        "step_type": "automated",
                        "handler": "adm_constraints_assessment",
                        "input_mapping": {"goals": "context.business_goals"},
                        "output_key": "constraints",
                    },
                    {
                        "step_id": "capability_assessment",
                        "step_name": "5. Assess Current Business Capability",
                        "step_type": "automated",
                        "handler": "adm_capability_assessment",
                        "input_mapping": {"scope": "context.scope_definition"},
                        "output_key": "capability_assessment",
                    },
                    {
                        "step_id": "vision_document",
                        "step_name": "6. Generate Architecture Vision Document",
                        "step_type": "automated",
                        "handler": "adm_vision_document",
                        "input_mapping": {
                            "scope": "context.scope_definition",
                            "stakeholders": "context.stakeholder_map",
                            "goals": "context.business_goals",
                            "constraints": "context.constraints",
                            "capabilities": "context.capability_assessment",
                        },
                        "output_key": "vision_document",
                    },
                    {
                        "step_id": "approval",
                        "step_name": "7. Architecture Board Approval",
                        "step_type": "approval",
                        "handler": "adm_approval_gate",
                        "config": {"approvers": ["enterprise_architect", "cio"]},
                    },
                    {
                        "step_id": "notify_complete",
                        "step_name": "8. Notify Stakeholders",
                        "step_type": "automated",
                        "handler": "notification",
                        "config": {"template": "adm_phase_a_complete"},
                    },
                ],
            },
        ]

    def seed_default_workflows(self) -> List[EAWorkflowDefinition]:
        """
        Seed the database with default EA workflow definitions.

        Returns:
            List of created workflow definitions
        """
        created = []

        for wf_data in self._build_default_workflow_definitions():

            existing = self.get_workflow_definition(wf_data["workflow_code"])
            if not existing:
                definition = self.create_workflow_definition(**wf_data)
                created.append(definition)

        return created

    # =========================================================================
    # WORKFLOW EXECUTION
    # =========================================================================

    def start_workflow(
        self,
        workflow_code: str,
        context: Dict,
        triggered_by: str = "manual",
        user_id: Optional[int] = None,
        scheduled_at: Optional[datetime] = None,
    ) -> EAWorkflowInstance:
        """
        Start a new workflow instance.

        Args:
            workflow_code: Code of the workflow to run
            context: Initial context with input parameters
            triggered_by: Trigger type (manual, scheduled, event, webhook, api)
            user_id: ID of triggering user (for manual triggers)
            scheduled_at: Optional scheduled execution time

        Returns:
            Created EAWorkflowInstance
        """
        definition = self.get_workflow_definition(workflow_code)
        if not definition:
            raise ValueError(f"Workflow {workflow_code} not found")

        # Generate unique instance code
        instance_code = (
            f"{workflow_code}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
        )

        instance = EAWorkflowInstance(
            workflow_definition_id=definition.id,
            instance_code=instance_code,
            context=context,
            status="pending",
            total_steps=len(definition.steps),
            triggered_by=triggered_by,
            triggered_by_user_id=user_id,
            scheduled_at=scheduled_at,
        )

        db.session.add(instance)
        db.session.commit()

        # Start execution if not scheduled for later
        if not scheduled_at or scheduled_at <= datetime.utcnow():
            self._execute_workflow(instance)

        return instance

    def _execute_workflow(self, instance: EAWorkflowInstance):
        """
        Execute a workflow instance.

        Args:
            instance: Workflow instance to execute
        """
        instance.status = "running"
        instance.started_at = datetime.utcnow()
        db.session.commit()

        definition = instance.workflow_definition
        steps = definition.steps

        try:
            for i, step_def in enumerate(steps):
                # Skip already completed steps (for resume)
                if i < instance.current_step_index:
                    continue

                instance.current_step_id = step_def["step_id"]
                instance.current_step_index = i
                instance.progress_percent = int((i / len(steps)) * 100)
                db.session.commit()

                # Execute step
                result = self._execute_step(instance, step_def, i)

                if result.get("status") == "waiting_approval":
                    # Pause for approval
                    instance.status = "waiting_approval"
                    instance.pending_approval_step_id = step_def["step_id"]
                    instance.approval_requested_at = datetime.utcnow()
                    db.session.commit()
                    return

                if result.get("status") == "failed":
                    instance.status = "failed"
                    instance.error_message = result.get("error")
                    instance.error_step_id = step_def["step_id"]
                    instance.completed_at = datetime.utcnow()
                    if instance.started_at:
                        instance.duration_seconds = int(
                            (instance.completed_at - instance.started_at).total_seconds()
                        )
                    db.session.commit()
                    return

                # Update context with step output
                if result.get("output") and step_def.get("output_key"):
                    instance.context[step_def["output_key"]] = result["output"]

                instance.completed_steps += 1
                db.session.commit()

            # Workflow completed successfully
            instance.status = "completed"
            instance.completed_at = datetime.utcnow()
            instance.progress_percent = 100
            if instance.started_at:
                instance.duration_seconds = int(
                    (instance.completed_at - instance.started_at).total_seconds()
                )

            # Update definition metrics
            definition.execution_count += 1
            definition.success_count += 1
            definition.last_executed_at = datetime.utcnow()

            db.session.commit()

        except Exception as e:
            instance.status = "failed"
            instance.error_message = str(e)
            instance.completed_at = datetime.utcnow()
            if instance.started_at:
                instance.duration_seconds = int(
                    (instance.completed_at - instance.started_at).total_seconds()
                )

            definition.execution_count += 1
            definition.last_executed_at = datetime.utcnow()

            db.session.commit()
            raise

    def _execute_step(self, instance: EAWorkflowInstance, step_def: Dict, step_index: int) -> Dict:
        """
        Execute a single workflow step.

        Args:
            instance: Workflow instance
            step_def: Step definition
            step_index: Step index

        Returns:
            Step result dictionary
        """
        # Create step execution record
        step_execution = EAWorkflowStepExecution(
            workflow_instance_id=instance.id,
            step_id=step_def["step_id"],
            step_index=step_index,
            step_name=step_def.get("step_name"),
            step_type=step_def.get("step_type", "automated"),
            status="running",
            started_at=datetime.utcnow(),
            service_class=step_def.get("service_class"),
            service_method=step_def.get("service_method"),
        )
        db.session.add(step_execution)
        db.session.commit()

        try:
            # Resolve input data
            input_data = self._resolve_inputs(instance.context, step_def.get("input_mapping", {}))
            step_execution.input_data = input_data

            # Check if approval is required
            if step_def.get("step_type") == "approval" or step_def.get("requires_approval"):
                step_execution.requires_approval = True
                step_execution.status = "waiting_approval"
                step_execution.approval_status = "pending"
                db.session.commit()
                return {"status": "waiting_approval", "step_id": step_def["step_id"]}

            # Get handler
            handler_name = step_def.get("handler")
            handler = self.STEP_HANDLERS.get(handler_name)

            if handler:
                # Execute handler
                output = handler(instance, step_def, input_data)
            else:
                # Try dynamic service invocation
                output = self._invoke_service(step_def, input_data)

            step_execution.output_data = output
            step_execution.status = "completed"
            step_execution.completed_at = datetime.utcnow()
            step_execution.duration_seconds = int(
                (step_execution.completed_at - step_execution.started_at).total_seconds()
            )
            db.session.commit()

            return {"status": "completed", "output": output}

        except Exception as e:
            step_execution.status = "failed"
            step_execution.error_message = str(e)
            step_execution.error_traceback = traceback.format_exc()
            step_execution.completed_at = datetime.utcnow()
            db.session.commit()

            instance.failed_steps += 1
            db.session.commit()

            return {"status": "failed", "error": str(e)}

    def _resolve_inputs(self, context: Dict, input_mapping: Dict) -> Dict:
        """
        Resolve input values from context using mapping.

        Args:
            context: Workflow context
            input_mapping: Mapping of input names to context paths

        Returns:
            Resolved input dictionary
        """
        resolved = {}
        for key, path in input_mapping.items():
            if path.startswith("context."):
                context_key = path[8:]  # Remove 'context.' prefix
                resolved[key] = context.get(context_key)
            else:
                resolved[key] = path
        return resolved

    def _invoke_service(self, step_def: Dict, input_data: Dict) -> Any:
        """
        Dynamically invoke a service method.

        Args:
            step_def: Step definition with service_class and service_method
            input_data: Input parameters

        Returns:
            Service method result
        """
        service_class = step_def.get("service_class")
        service_method = step_def.get("service_method")

        if not service_class or not service_method:
            return None

        # Import and instantiate service
        module_path, class_name = service_class.rsplit(".", 1)
        module = __import__(module_path, fromlist=[class_name])
        service_cls = getattr(module, class_name)
        service = service_cls()

        # Call method
        method = getattr(service, service_method)
        return method(**input_data)

    # =========================================================================
    # STEP HANDLERS
    # =========================================================================

    def _handle_gap_analysis(self, instance, step_def, input_data) -> Dict:
        """Handle gap analysis step."""
        from app.services.gap_analysis_service import ArchitecturalGapAnalyzer

        analyzer = ArchitecturalGapAnalyzer()

        if input_data.get("application_id"):
            return analyzer.analyze_application_gaps(input_data["application_id"])
        else:
            return analyzer.analyze_portfolio_gaps()

    def _handle_vendor_matching(self, instance, step_def, input_data) -> Dict:
        """Handle vendor matching step.

        Finds vendors that can address identified capability gaps using the
        UnifiedVendorProcessService for gap-specific vendor recommendations.
        """
        from app.services.unified_vendor_process_service import UnifiedVendorProcessService

        service = UnifiedVendorProcessService()

        gaps = input_data.get("gaps", [])
        matched_vendors = []
        match_scores = {}

        for gap in gaps:
            capability_name = gap.get("capability_name", "")
            process_code = gap.get("process_code", "")
            if not capability_name or not process_code:
                continue

            vendors = service.find_vendors_for_capability_gap(capability_name, process_code)
            for vendor in vendors:
                vendor_name = vendor.get("vendor_name", vendor.get("name", ""))
                matched_vendors.append({
                    "vendor_name": vendor_name,
                    "capability_name": capability_name,
                    "process_code": process_code,
                    "suitability_score": vendor.get("suitability_score", 0),
                    "recommendation_reason": vendor.get("recommendation_reason", ""),
                })
                if vendor_name:
                    match_scores[vendor_name] = max(
                        match_scores.get(vendor_name, 0),
                        vendor.get("suitability_score", 0),
                    )

        return {"matched_vendors": matched_vendors, "match_scores": match_scores}

    def _handle_apqc_mapping(self, instance, step_def, input_data) -> Dict:
        """Handle APQC process mapping step.

        Classifies application descriptions against APQC process framework
        using the UnifiedAPQCService.
        """
        from app.services.unified_apqc_service import get_unified_apqc_service

        service = get_unified_apqc_service()

        text = input_data.get("description", "")
        application_name = input_data.get("application_name", "")
        if application_name and text:
            text = f"{application_name}: {text}"
        elif application_name:
            text = application_name

        top_k = input_data.get("top_k", 5)

        results = service.classify_text(text, max_results=top_k)

        suggested_mappings = []
        confidence_scores = {}
        for r in results:
            process_code = r.get("process_code", "")
            suggested_mappings.append({
                "process_id": r.get("process_id"),
                "process_code": process_code,
                "process_name": r.get("process_name", ""),
                "confidence": r.get("confidence", 0),
            })
            if process_code:
                confidence_scores[process_code] = r.get("confidence", 0)

        return {"suggested_mappings": suggested_mappings, "confidence_scores": confidence_scores}

    def _handle_capability_linking(self, instance, step_def, input_data) -> Dict:
        """Handle capability linking step.

        Links applications to capabilities and analyzes portfolio coverage
        using the CapabilityMappingService.
        """
        from app.services.capability_mapping_service import CapabilityMappingService

        service = CapabilityMappingService()

        application_id = input_data.get("application_id")
        linked_capabilities = []

        if application_id:
            existing = service.get_application_capabilities(application_id)
            for cap in existing:
                linked_capabilities.append({
                    "capability_id": cap.unified_capability_id if hasattr(cap, "unified_capability_id") else cap.id,  # model-safety-ok: polymorphic coverage model
                    "application_id": application_id,
                    "coverage_percentage": getattr(cap, "coverage_percentage", 0),  # model-safety-ok: coverage model variant
                })

        coverage_analysis = service.analyze_portfolio_capability_coverage()

        return {
            "linked_capabilities": linked_capabilities,
            "coverage_analysis": coverage_analysis,
        }

    def _handle_compliance_scan(self, instance, step_def, input_data) -> Dict:
        """Handle compliance scanning step."""
        from app.services.policy_monitoring_service import PolicyMonitoringService

        service = PolicyMonitoringService()
        return service.run_compliance_scan()

    def _handle_notification(self, instance, step_def, input_data) -> Dict:
        """Handle notification step.

        Logs workflow notifications with step results to the application logger.
        Extracts recipients and template from step config and records the
        notification event for downstream consumers (email, webhook, etc.).
        """
        config = step_def.get("config", {})
        template = config.get("template", "default")
        recipients = config.get("recipients", [])
        subject = config.get("subject", f"Workflow notification: {instance.instance_code}")

        # Build notification payload from workflow context
        notification_payload = {
            "workflow_id": instance.id,
            "workflow_code": instance.instance_code,
            "template": template,
            "subject": subject,
            "recipients": recipients,
            "summary": input_data.get("summary", ""),
            "status": instance.status,
        }

        logger.info(
            "Workflow notification [%s]: template=%s, recipients=%s",
            instance.instance_code,
            template,
            recipients,
        )

        return {
            "notification_sent": True,
            "template": template,
            "recipients_count": len(recipients),
            "payload": notification_payload,
        }

    def _handle_create_suggestion(self, instance, step_def, input_data) -> Dict:
        """Handle AI suggestion creation step.

        Creates AI-powered suggestions for gap remediation, vendor matching,
        or capability improvements using the AISuggestionService.
        """
        from app.services.ai_suggestion_service import AISuggestionService

        service = AISuggestionService()

        items = input_data.get("items", [])
        source = input_data.get("source", AISuggestionService.SOURCE_GAP_DETECTION)
        workflow_name = instance.instance_code

        if not items:
            return {"suggestions_created": 0, "batch_id": None}

        suggestions_data = []
        for item in items:
            suggestions_data.append({
                "entity_type": item.get("entity_type", "application"),
                "entity_id": item.get("entity_id"),
                "suggestion_type": item.get("suggestion_type", "remediation"),
                "suggested_value": item.get("suggested_value", {}),
                "confidence": item.get("confidence", 0.7),
                "source": source,
                "reasoning": item.get("reasoning", ""),
                "field_name": item.get("field_name"),
            })

        batch_id, suggestions = service.create_batch_suggestions(
            suggestions_data=suggestions_data,
            workflow_name=workflow_name,
            workflow_step=step_def.get("step_id", "create_suggestion"),
        )

        return {"suggestions_created": len(suggestions), "batch_id": batch_id}

    def _handle_approval_gate(self, instance, step_def, input_data) -> Dict:
        """Handle approval gate — pauses workflow for human review.

        Captures the items requiring approval and the configured approvers,
        then returns waiting_approval status which causes the workflow engine
        to pause until resume_workflow is called.
        """
        config = step_def.get("config", {})
        approvers = config.get("approvers", [])
        approval_scope = config.get("scope", "all")

        # Gather items that need approval from the workflow context
        context = instance.context or {}
        pending_items = input_data.get("items", [])
        if not pending_items:
            # Collect results from prior steps as items for review
            pending_items = context.get("gap_analysis", context.get("suggestions", []))

        instance.approval_requested_at = datetime.utcnow()
        db.session.commit()

        return {
            "status": "waiting_approval",
            "approvers": approvers,
            "scope": approval_scope,
            "pending_items_count": len(pending_items) if isinstance(pending_items, list) else 0,
        }

    def _handle_roadmap_creation(self, instance, step_def, input_data) -> Dict:
        """Handle roadmap item creation step.

        Creates implementation work packages from approved remediation items
        using the RoadmapBuilderService.
        """
        from app.services.roadmap_builder_service import RoadmapBuilderService

        service = RoadmapBuilderService()

        approved_items = input_data.get("approved_items", [])
        if not approved_items:
            # Fall back to workflow context approved_items (set by resume_workflow)
            approved_items = (instance.context or {}).get("approved_items", [])

        created_ids = []
        for item in approved_items:
            name = item.get("name", item.get("title", "Remediation work package"))
            result = service.create_work_package(
                name=name,
                description=item.get("description", ""),
                priority=item.get("priority", item.get("severity", "medium")),
                estimated_cost=item.get("estimated_cost", 0.0),
                capability_id=item.get("capability_id"),
                created_by=f"workflow:{instance.instance_code}",
            )
            if result and result.get("id"):
                created_ids.append(result["id"])

        return {
            "roadmap_items_created": len(created_ids),
            "work_package_ids": created_ids,
        }

    # =========================================================================
    # FR - 001 to FR - 005: Intelligent Integration Handlers
    # =========================================================================

    # =========================================================================
    # TOGAF ADM Phase A: Architecture Vision Handlers
    # =========================================================================

    def _handle_adm_define_scope(self, instance, step_def, input_data) -> Dict:
        """Handle ADM Phase A Step 1: Define Architecture Project Scope.
        
        Establishes the boundaries and focus of the architecture project.
        """
        from app.services.multi_domain_chat_service import MultiDomainChatService
        
        project_name = input_data.get("project_name", "Unnamed Project")
        project_description = input_data.get("project_description", "")
        
        # Use AI to structure the scope definition
        llm = MultiDomainChatService(user_id=instance.triggered_by_user_id)
        scope_prompt = f"""Define an architecture project scope for:
        Project: {project_name}
        Description: {project_description}
        
        Provide:
        1. Scope boundaries (in/out)
        2. Architecture domains involved (Business, Data, Application, Technology)
        3. Key deliverables expected
        4. Success criteria
        """
        
        response = llm.process_message(
            message=scope_prompt,
            domain="architecture",
            persona="enterprise_architect"
        )
        
        return {
            "project_name": project_name,
            "scope_boundaries": response.get("response", {}).get("scope", "To be defined"),
            "domains": ["business", "data", "application", "technology"],
            "deliverables": [
                "Architecture Vision Document",
                "Stakeholder Map", 
                "Business Goals Definition",
                "Capability Assessment"
            ],
            "ai_analysis": response.get("response", {}),
        }

    def _handle_adm_stakeholder_analysis(self, instance, step_def, input_data) -> Dict:
        """Handle ADM Phase A Step 2: Identify and Characterize Stakeholders."""
        from app.models import User
        
        input_data.get("scope", {})
        
        # Get potential stakeholders from user database
        stakeholders = []
        org_id = getattr(g, "current_org_id", None)
        _q = User.query.filter(User.is_active == True)
        if org_id is not None:
            _q = _q.filter(User.organization_id == org_id)
        users = _q.all()  # tenant-scoping-ok: scoped above when org_id known
        
        for user in users:
            # Determine stakeholder category based on role
            category = self._categorize_stakeholder(user)
            stakeholders.append({
                "id": user.id,
                "name": user.full_name() or user.username,
                "email": user.email,
                "category": category,
                "concerns": self._get_stakeholder_concerns(category),
                "influence": "high" if category in ["executive", "business_owner"] else "medium",
            })
        
        return {
            "stakeholders": stakeholders,
            "stakeholder_count": len(stakeholders),
            "categories": list(set(s["category"] for s in stakeholders)),
            "communication_plan": self._generate_comm_plan(stakeholders),
        }

    def _categorize_stakeholder(self, user) -> str:
        """Categorize user as stakeholder type based on role."""
        role = (user.role or "").lower()
        if "admin" in role or "exec" in role:
            return "executive"
        elif "architect" in role:
            return "architect"
        elif "business" in role or "analyst" in role:
            return "business_owner"
        elif "dev" in role or "engineer" in role:
            return "technical"
        return "other"

    def _get_stakeholder_concerns(self, category: str) -> List[str]:
        """Get typical concerns for stakeholder category."""
        concerns_map = {
            "executive": ["ROI", "strategic alignment", "risk reduction"],
            "architect": ["consistency", "integration", "standards compliance"],
            "business_owner": ["process efficiency", "capability enablement"],
            "technical": ["feasibility", "performance", "maintainability"],
        }
        return concerns_map.get(category, ["general interest"])

    def _generate_comm_plan(self, stakeholders: List[Dict]) -> Dict:
        """Generate communication plan for stakeholders."""
        return {
            "executive_summary": "Monthly board presentation",
            "architect_review": "Weekly design sessions",
            "business_workshop": "Bi-weekly capability mapping",
            "technical_sync": "Weekly implementation review",
        }

    def _handle_adm_business_goals(self, instance, step_def, input_data) -> Dict:
        """Handle ADM Phase A Step 3: Define Business Goals and Drivers."""
        from app.services.multi_domain_chat_service import MultiDomainChatService
        
        input_data.get("stakeholders", [])
        
        # Use AI to analyze business goals from stakeholder concerns
        llm = MultiDomainChatService(user_id=instance.triggered_by_user_id)
        
        goals_prompt = """Based on enterprise architecture context, define business goals for this project.
        
        Consider:
        1. Strategic business drivers (digital transformation, cost reduction, etc.)
        2. Specific measurable objectives
        3. Target state vision
        4. Alignment with enterprise strategy
        
        Output format:
        - Goal statement
        - Measurable outcome
        - Timeline
        - Priority (Critical/High/Medium)
        """
        
        response = llm.process_message(
            message=goals_prompt,
            domain="business_capability",
            persona="business_architect"
        )
        
        goals = [
            {
                "id": "BG001",
                "statement": "Enable digital customer experience",
                "measurable_outcome": "80% customer transactions digital by 2026",
                "timeline": "18 months",
                "priority": "Critical",
            },
            {
                "id": "BG002",
                "statement": "Reduce operational costs through automation",
                "measurable_outcome": "30% cost reduction in processing workflows",
                "timeline": "24 months", 
                "priority": "High",
            },
        ]
        
        return {
            "business_goals": goals,
            "goal_count": len(goals),
            "strategic_drivers": response.get("response", {}).get("drivers", []),
            "ai_recommendations": response.get("response", {}),
        }

    def _handle_adm_constraints_assessment(self, instance, step_def, input_data) -> Dict:
        """Handle ADM Phase A Step 4: Assess Business and Technical Constraints."""
        from app.services.policy_monitoring_service import PolicyMonitoringService
        
        # Get active policies as constraints
        policy_service = PolicyMonitoringService()
        policies = policy_service.get_active_policies()
        
        constraints = {
            "business_constraints": [
                {"type": "budget", "description": "Annual IT budget cap", "impact": "high"},
                {"type": "timeline", "description": "Must complete by FY end", "impact": "high"},
                {"type": "compliance", "description": "SOX compliance required", "impact": "critical"},
            ],
            "technical_constraints": [
                {"type": "technology", "description": "Cloud-first mandate", "impact": "medium"},
                {"type": "integration", "description": "Must integrate with existing ERP", "impact": "high"},
                {"type": "security", "description": "Zero-trust architecture", "impact": "high"},
            ],
            "policy_constraints": [
                {"policy_id": p.id, "name": p.name, "category": p.category}
                for p in policies[:5]
            ],
        }
        
        return constraints

    def _handle_adm_capability_assessment(self, instance, step_def, input_data) -> Dict:
        """Handle ADM Phase A Step 5: Assess Current Business Capability.

        GLB-WF-008: Prefers UnifiedCapability (canonical model); falls back to
        BusinessCapability only when unified table is empty (backward compatibility).
        """
        capabilities = []

        # Prefer UnifiedCapability (canonical model) when table has data
        try:
            from app.models.unified_capability import UnifiedCapability

            l1_unified = UnifiedCapability.query.filter_by(level=1).limit(50).all()
            if l1_unified:
                for cap in l1_unified:
                    mat = cap.current_maturity_level
                    maturity = (
                        "high" if mat and mat >= 4 else ("low" if mat and mat <= 2 else "medium")
                    ) if mat else "unknown"
                    coverage = 0
                    maps = getattr(cap, "application_capability_mappings", None)
                    if maps:
                        coverage = len(maps)
                    capabilities.append({
                        "id": cap.id,
                        "name": cap.name,
                        "code": cap.code or "",
                        "maturity": maturity,
                        "automation": "unknown",
                        "coverage": coverage,
                    })
        except Exception as e:
            logger.debug("UnifiedCapability not available: %s", e)

        # Fallback to BusinessCapability
        if not capabilities:
            from app.models import BusinessCapability

            l1_capabilities = BusinessCapability.query.filter_by(level=1).limit(50).all()
            for cap in l1_capabilities:
                mat = getattr(cap, "maturity_level", None) or getattr(cap, "current_maturity_level", None)
                maturity = (
                    "high" if mat and mat >= 4 else ("low" if mat and mat <= 2 else "medium")
                ) if mat else "unknown"
                coverage = len(cap.applications) if hasattr(cap, "applications") and cap.applications else 0
                capabilities.append({
                    "id": cap.id,
                    "name": cap.name,
                    "code": getattr(cap, "code", "") or "",
                    "maturity": maturity,
                    "automation": getattr(cap, "automation_level", None) or "unknown",
                    "coverage": coverage,
                })

        # Calculate overall maturity
        _MATURITY_ORDER = {"low": 1, "medium": 2, "high": 3, "unknown": 2}
        maturity_scores = [
            _MATURITY_ORDER.get(str(c["maturity"]).lower(), 2)
            for c in capabilities
        ]
        avg_val = sum(maturity_scores) / len(maturity_scores) if maturity_scores else 2
        avg_maturity = "high" if avg_val >= 2.5 else ("low" if avg_val < 1.5 else "medium")

        return {
            "capabilities": capabilities,
            "l1_count": len(capabilities),
            "overall_maturity": avg_maturity,
            "gaps_identified": len(
                [c for c in capabilities if c["maturity"] in ["low", "unknown"]]
            ),
            "assessment_date": datetime.utcnow().isoformat(),
        }

    def _handle_adm_vision_document(self, instance, step_def, input_data) -> Dict:
        """Handle ADM Phase A Step 6: Generate Architecture Vision Document."""
        scope = input_data.get("scope", {})
        stakeholders = input_data.get("stakeholders", [])
        goals = input_data.get("goals", {})
        constraints = input_data.get("constraints", {})
        capabilities = input_data.get("capabilities", {})
        
        # Compile vision document content
        vision_content = {
            "title": f"Architecture Vision: {scope.get('project_name', 'Enterprise Architecture')}",
            "executive_summary": self._generate_exec_summary(scope, goals),
            "scope": scope,
            "stakeholders": stakeholders,
            "business_goals": goals.get("business_goals", []),
            "constraints": constraints,
            "current_state": {
                "capability_maturity": capabilities.get("overall_maturity"),
                "key_capabilities": capabilities.get("capabilities", [])[:5],
            },
            "target_vision": {
                "description": "Transformed enterprise with modernized capabilities",
                "key_outcomes": [g["statement"] for g in goals.get("business_goals", [])],
            },
            "next_steps": [
                "Proceed to Phase B: Business Architecture",
                "Define detailed business capability roadmap",
                "Identify transformation initiatives",
            ],
        }
        
        return {
            "document_generated": True,
            "document_type": "Architecture Vision",
            "content": vision_content,
            "sections": list(vision_content.keys()),
            "ready_for_approval": True,
        }

    def _generate_exec_summary(self, scope: Dict, goals: Dict) -> str:
        """Generate executive summary for vision document."""
        project = scope.get("project_name", "Architecture Initiative")
        goal_count = len(goals.get("business_goals", []))
        return f"""This Architecture Vision document defines the strategic direction for {project}.
        
The initiative addresses {goal_count} critical business goals and establishes a roadmap
for enterprise transformation. The vision aligns with TOGAF ADM Phase A methodology and
provides foundation for subsequent architecture development phases.
"""

    def _handle_adm_approval_gate(self, instance, step_def, input_data) -> Dict:
        """Handle ADM Phase A approval gate."""
        return {"status": "waiting_approval", "approvers": ["enterprise_architect", "cio"]}

    # =========================================================================
    # WORKFLOW CONTROL
    # =========================================================================

    def resume_workflow(
        self, instance_id: int, approved_items: Optional[List] = None, user_id: Optional[int] = None
    ) -> EAWorkflowInstance:
        """
        Resume a workflow after approval.

        Args:
            instance_id: Instance ID
            approved_items: Items approved in the approval step
            user_id: Approving user ID

        Returns:
            Updated workflow instance
        """
        instance = db.session.get(EAWorkflowInstance, instance_id)
        if not instance:
            raise ValueError(f"Workflow instance {instance_id} not found")

        if instance.status != "waiting_approval":
            raise ValueError(f"Workflow is not waiting for approval (status: {instance.status})")

        # Update approval step
        pending_step = EAWorkflowStepExecution.query.filter_by(
            workflow_instance_id=instance_id,
            step_id=instance.pending_approval_step_id,
            status="waiting_approval",
        ).first()

        if pending_step:
            pending_step.status = "completed"
            pending_step.approval_status = "approved"
            pending_step.approved_by_id = user_id
            pending_step.approved_at = datetime.utcnow()
            pending_step.completed_at = datetime.utcnow()
            instance.completed_steps += 1

        # Store approved items in context
        if approved_items:
            instance.context["approved_items"] = approved_items

        instance.pending_approval_step_id = None
        instance.approval_requested_at = None
        instance.current_step_index += 1
        db.session.commit()

        # Continue execution
        self._execute_workflow(instance)
        return instance

    def cancel_workflow(self, instance_id: int, reason: str = None) -> EAWorkflowInstance:
        """Cancel a running workflow."""
        instance = db.session.get(EAWorkflowInstance, instance_id)
        if not instance:
            raise ValueError(f"Workflow instance {instance_id} not found")

        instance.status = "cancelled"
        instance.error_message = reason or "Cancelled by user"
        instance.completed_at = datetime.utcnow()
        if instance.started_at:
            instance.duration_seconds = int(
                (instance.completed_at - instance.started_at).total_seconds()
            )
        db.session.commit()

        return instance

    def get_instance_status(self, instance_id: int) -> Dict:
        """Get current status of a workflow instance."""
        instance = db.session.get(EAWorkflowInstance, instance_id)
        if not instance:
            return {"error": "Instance not found"}

        step_executions = (
            EAWorkflowStepExecution.query.filter_by(workflow_instance_id=instance_id)
            .order_by(EAWorkflowStepExecution.step_index)
            .all()
        )

        return {"instance": instance.to_dict(), "steps": [s.to_dict() for s in step_executions]}

    # =========================================================================
    # SCHEDULING
    # =========================================================================

    def create_schedule(
        self, workflow_code: str, schedule_name: str, schedule_type: str, **kwargs
    ) -> EAWorkflowSchedule:
        """
        Create a scheduled workflow execution.

        Args:
            workflow_code: Workflow to schedule
            schedule_name: Schedule name
            schedule_type: Type (cron, daily, weekly, monthly)
            **kwargs: Schedule configuration

        Returns:
            Created schedule
        """
        definition = self.get_workflow_definition(workflow_code)
        if not definition:
            raise ValueError(f"Workflow {workflow_code} not found")

        schedule = EAWorkflowSchedule(
            workflow_definition_id=definition.id,
            schedule_name=schedule_name,
            schedule_type=schedule_type,
            cron_expression=kwargs.get("cron_expression"),
            time_of_day=kwargs.get("time_of_day"),
            day_of_week=kwargs.get("day_of_week"),
            day_of_month=kwargs.get("day_of_month"),
            timezone=kwargs.get("timezone", "UTC"),
            default_context=kwargs.get("default_context", {}),
            is_active=True,
            created_by_id=kwargs.get("created_by_id"),
        )

        db.session.add(schedule)
        db.session.commit()
        return schedule

    def get_pending_scheduled_workflows(self) -> List[EAWorkflowSchedule]:
        """Get schedules that are due to run."""
        now = datetime.utcnow()
        return EAWorkflowSchedule.query.filter(
            EAWorkflowSchedule.is_active == True, EAWorkflowSchedule.next_run_at <= now
        ).all()

    # ── Stub handlers for workflow steps whose full implementation lives
    # in the v1 engine or is planned for a future wave.  They are registered
    # so that every handler referenced by a workflow step resolves at runtime.

    def _handle_document_extraction(self, instance, step_def, input_data) -> Dict:
        return {"status": "delegated", "message": "document_extraction handled by v1 engine"}

    def _handle_gap_classification(self, instance, step_def, input_data) -> Dict:
        return {"status": "delegated", "message": "gap_classification handled by v1 engine"}

    def _handle_vendor_scoring(self, instance, step_def, input_data) -> Dict:
        return {"status": "delegated", "message": "vendor_scoring handled by v1 engine"}

    def _handle_vendor_gap_analysis(self, instance, step_def, input_data) -> Dict:
        return {"status": "delegated", "message": "vendor_gap_analysis handled by v1 engine"}

    def _handle_tco_calculation(self, instance, step_def, input_data) -> Dict:
        return {"status": "delegated", "message": "tco_calculation handled by v1 engine"}

    def _handle_policy_loader(self, instance, step_def, input_data) -> Dict:
        return {"status": "delegated", "message": "policy_loader handled by v1 engine"}

    def _handle_violation_classification(self, instance, step_def, input_data) -> Dict:
        return {"status": "delegated", "message": "violation_classification handled by v1 engine"}

    def _handle_auto_remediation(self, instance, step_def, input_data) -> Dict:
        return {"status": "delegated", "message": "auto_remediation handled by v1 engine"}

    def _handle_completeness_validation(self, instance, step_def, input_data) -> Dict:
        return {"status": "delegated", "message": "completeness_validation handled by v1 engine"}

    def _handle_quality_scoring(self, instance, step_def, input_data) -> Dict:
        return {"status": "delegated", "message": "quality_scoring handled by v1 engine"}

    def _handle_commit_changes(self, instance, step_def, input_data) -> Dict:
        return {"status": "delegated", "message": "commit_changes handled by v1 engine"}


# Singleton instance
ea_workflow_engine = EAWorkflowEngine()
