"""
AI Workbench Kernel — AIC-312 through AIC-318

Canonical workbench session and workspace contract for the AI chat architecture
workbench. Provides:

- Workspace creation, resumption, and identity management (AIC-312)
- Artifact lifecycle state tracking (draft → confirmed → persisted → approved)
- Greenfield blueprint workflow (AIC-313)
- Brownfield modernization workflow (AIC-314)
- ArchiMate 3.2 authoring from chat (AIC-315)
- SAD/governance pack generation (AIC-316)
- Delivery planning and roadmap generation (AIC-317)
- Evidence gate enforcement (AIC-318)

All workspace state is persisted via SolutionAnalysisSession.custom_metadata
and Flask session, so it survives page refreshes and session resumes.
"""

from app.services.archimate_backbone import sync_archimate_element
import enum
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from app import db

logger = logging.getLogger(__name__)


def _pascal_to_snake(name: str) -> str:
    """Convert PascalCase to snake_case for ArchiMate type lookup.

    Example: 'ApplicationComponent' -> 'application_component'
    """
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


# ============================================================================
# AIC-312: ARTIFACT LIFECYCLE STATES
# ============================================================================

class ArtifactState(enum.Enum):
    """Lifecycle state for workbench artifacts."""
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    PERSISTED = "persisted"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


# Valid state transitions — only adjacent transitions allowed
VALID_TRANSITIONS = {
    None: {ArtifactState.DRAFT},
    ArtifactState.DRAFT: {ArtifactState.CONFIRMED, ArtifactState.SUPERSEDED},
    ArtifactState.CONFIRMED: {ArtifactState.PERSISTED, ArtifactState.DRAFT, ArtifactState.SUPERSEDED},
    ArtifactState.PERSISTED: {ArtifactState.APPROVED, ArtifactState.CONFIRMED, ArtifactState.SUPERSEDED},
    ArtifactState.APPROVED: {ArtifactState.SUPERSEDED},
    ArtifactState.SUPERSEDED: set(),
}


WORKSPACE_TYPE_GREENFIELD = "greenfield"
WORKSPACE_TYPE_BROWNFIELD = "brownfield"

# Greenfield workflow steps (AIC-313)
GREENFIELD_STEPS = [
    "BRIEF",           # Capture business brief / objective
    "SCOPE",           # Stakeholders, drivers, goals, constraints, NFRs
    "TARGET_STATE",    # Propose target architecture options
    "OPTIONS",         # Evaluate options with data
    "RECOMMENDATION",  # Recommend best option + TCO / risk
    "ROADMAP",         # Work packages, plateaus, transition plan
    "SUMMARY",         # Final summary with links
]

# Brownfield workflow steps (AIC-314)
BROWNFIELD_STEPS = [
    "CONTEXT_LOAD",     # Load existing portfolio data for target domain
    "CURRENT_STATE",    # Present current-state assessment
    "GAP_ANALYSIS",     # Identify gaps between current and target
    "TRANSITION_PLAN",  # Design transition architecture
    "MIGRATION_PLAN",   # Generate migration work packages
    "SUMMARY",          # Final summary with links
]

# ArchiMate authoring steps (AIC-315)
ARCHIMATE_STEPS = [
    "IDENTIFY",    # Identify elements from conversation context
    "CREATE",      # Create and validate elements/relationships
    "VIEWPOINT",   # Generate viewpoint/diagram
    "PERSIST",     # Persist to canonical model
]

# Valid ArchiMate 3.2 element types (AIC-315)
VALID_ARCHIMATE_TYPES = {
    "ApplicationComponent", "ApplicationService", "ApplicationInterface",
    "ApplicationFunction", "ApplicationProcess", "ApplicationInteraction",
    "ApplicationEvent", "ApplicationCollaboration", "DataObject",
    "BusinessProcess", "BusinessService", "BusinessActor", "BusinessRole",
    "BusinessObject", "BusinessFunction", "BusinessInterface",
    "BusinessInteraction", "BusinessEvent", "BusinessCollaboration",
    "Contract", "Representation", "Product",
    "Node", "Device", "SystemSoftware", "CommunicationNetwork",
    "Artifact", "Path", "TechnologyService", "TechnologyFunction",
    "TechnologyProcess", "TechnologyInteraction", "TechnologyEvent",
    "TechnologyInterface", "TechnologyCollaboration",
    "Driver", "Goal", "Constraint", "Requirement", "Assessment",
    "Principle", "Stakeholder", "Outcome", "Meaning", "Value",
    "Capability", "CourseOfAction", "ValueStream", "Resource",
    "WorkPackage", "Plateau", "Deliverable", "Gap", "ImplementationEvent",
    "Equipment", "Facility", "DistributionNetwork", "Material",
}

# Valid ArchiMate 3.2 layers (AIC-315)
VALID_ARCHIMATE_LAYERS = {
    "Motivation", "Strategy", "Business", "Application",
    "Technology", "Implementation", "Physical",
}


# ============================================================================
# AIC-312: WORKBENCH KERNEL SERVICE
# ============================================================================

class WorkbenchKernel:
    """
    Canonical workspace kernel for the AI architecture workbench.

    Manages workspace identity, artifact lifecycle, and workflow state.
    Reuses SolutionAnalysisSession as the persistent workspace record.
    """

    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id

    # ------------------------------------------------------------------
    # Workspace CRUD
    # ------------------------------------------------------------------

    def create_workspace(
        self,
        name: str,
        workspace_type: str = WORKSPACE_TYPE_GREENFIELD,
        description: str = "",
        solution_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a new workbench workspace anchored to a SolutionAnalysisSession."""
        try:
            from app.models.solution_architect_models import (
                SolutionAnalysisSession,
                SolutionSessionStatus,
            )

            session = SolutionAnalysisSession(
                name=name,
                description=description,
                status=SolutionSessionStatus.IN_PROGRESS,
                created_by_id=self.user_id or 1,
                tags=[workspace_type, "workbench"],
                custom_metadata={
                    "workspace_type": workspace_type,
                    "solution_id": solution_id,
                    "artifacts": {},
                    "pending_approvals": [],
                    "workflow_phase": None,
                    "evidence": [],
                    "created_via": "ai_chat_workbench",
                },
            )
            db.session.add(session)
            db.session.commit()

            return {
                "success": True,
                "workspace_id": session.id,
                "workspace_type": workspace_type,
                "name": name,
                "solution_id": solution_id,
            }
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to create workspace: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def load_workspace(self, workspace_id: int) -> Optional[Dict[str, Any]]:
        """Load an existing workspace by ID."""
        try:
            from app.models.solution_architect_models import SolutionAnalysisSession

            session = SolutionAnalysisSession.query.get(workspace_id)
            if not session:
                return None

            meta = session.custom_metadata or {}
            return {
                "workspace_id": session.id,
                "name": session.name,
                "description": session.description,
                "status": session.status.value if session.status else "draft",
                "workspace_type": meta.get("workspace_type", "greenfield"),
                "solution_id": meta.get("solution_id"),
                "artifacts": meta.get("artifacts", {}),
                "pending_approvals": meta.get("pending_approvals", []),
                "workflow_phase": meta.get("workflow_phase"),
                "evidence": meta.get("evidence", []),
                "current_version": session.current_version,
                "created_at": session.created_at.isoformat() if session.created_at else None,
                "updated_at": session.updated_at.isoformat() if session.updated_at else None,
            }
        except Exception as e:
            logger.error("Failed to load workspace %s: %s", workspace_id, e)
            return None

    def update_workspace_metadata(
        self, workspace_id: int, updates: Dict[str, Any]
    ) -> bool:
        """Update workspace custom_metadata fields."""
        try:
            from app.models.solution_architect_models import SolutionAnalysisSession

            session = SolutionAnalysisSession.query.get(workspace_id)
            if not session:
                return False

            meta = dict(session.custom_metadata or {})
            meta.update(updates)
            session.custom_metadata = meta
            session.updated_at = datetime.utcnow()
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to update workspace %s: %s", workspace_id, e)
            return False

    # ------------------------------------------------------------------
    # AIC-312: Artifact lifecycle
    # ------------------------------------------------------------------

    def set_artifact_state(
        self,
        workspace_id: int,
        artifact_key: str,
        state: str,
        data: Optional[Dict] = None,
    ) -> bool:
        """Set the lifecycle state of a workspace artifact. Validates transition."""
        try:
            from app.models.solution_architect_models import SolutionAnalysisSession

            # Validate state is a valid ArtifactState value
            try:
                new_state = ArtifactState(state)
            except ValueError:
                logger.warning("Invalid artifact state: %s", state)
                return False

            session = SolutionAnalysisSession.query.get(workspace_id)
            if not session:
                return False

            meta = dict(session.custom_metadata or {})
            artifacts = dict(meta.get("artifacts", {}))

            # Get current state for transition validation
            current_entry = artifacts.get(artifact_key)
            if current_entry:
                try:
                    current_state = ArtifactState(current_entry["state"])
                except (ValueError, KeyError):
                    current_state = None
            else:
                current_state = None

            # Validate transition
            allowed = VALID_TRANSITIONS.get(current_state, set())
            if new_state not in allowed:
                logger.warning(
                    "Invalid artifact transition: %s -> %s for %s (allowed: %s)",
                    current_state, new_state, artifact_key, allowed,
                )
                return False

            artifacts[artifact_key] = {
                "state": state,
                "updated_at": datetime.utcnow().isoformat(),
                "data": data or (current_entry.get("data", {}) if current_entry else {}),
            }
            meta["artifacts"] = artifacts
            session.custom_metadata = meta
            session.updated_at = datetime.utcnow()
            if (
                new_state in {ArtifactState.PERSISTED, ArtifactState.APPROVED}
                and meta.get("solution_id")
                and session.organization_id
            ):
                from app.models.arb_submission_evidence import WorkbenchArtifactEvidence

                solution_id = meta.get("solution_id")
                WorkbenchArtifactEvidence.capture(
                    organization_id=session.organization_id,
                    workspace_id=session.id,
                    solution_id=solution_id,
                    name=artifact_key,
                    state=new_state.value,
                    payload=artifacts[artifact_key]["data"],
                    actor_id=self.user_id or session.created_by_id,
                )
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error("Artifact state update failed: %s", e)
            return False

    def get_artifact_state(
        self, workspace_id: int, artifact_key: str
    ) -> Optional[Dict]:
        """Get the current state of a workspace artifact."""
        ws = self.load_workspace(workspace_id)
        if not ws:
            return None
        return ws.get("artifacts", {}).get(artifact_key)

    # ------------------------------------------------------------------
    # AIC-312: Pending approvals
    # ------------------------------------------------------------------

    def add_pending_approval(
        self,
        workspace_id: int,
        approval_type: str,
        description: str,
        payload: Dict,
    ) -> bool:
        """Add a pending approval to the workspace."""
        try:
            from app.models.solution_architect_models import SolutionAnalysisSession

            session = SolutionAnalysisSession.query.get(workspace_id)
            if not session:
                return False

            meta = dict(session.custom_metadata or {})
            pending = list(meta.get("pending_approvals", []))
            pending.append({
                "type": approval_type,
                "description": description,
                "payload": payload,
                "created_at": datetime.utcnow().isoformat(),
                "status": "pending",
            })
            meta["pending_approvals"] = pending
            session.custom_metadata = meta
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to add pending approval: %s", e)
            return False

    # ------------------------------------------------------------------
    # AIC-312: Evidence tracking
    # ------------------------------------------------------------------

    def add_evidence(
        self,
        workspace_id: int,
        evidence_type: str,
        description: str,
        reference: str = "",
    ) -> bool:
        """Add an evidence entry to the workspace."""
        try:
            from app.models.solution_architect_models import SolutionAnalysisSession

            session = SolutionAnalysisSession.query.get(workspace_id)
            if not session:
                return False

            meta = dict(session.custom_metadata or {})
            evidence = list(meta.get("evidence", []))
            evidence.append({
                "type": evidence_type,
                "description": description,
                "reference": reference,
                "recorded_at": datetime.utcnow().isoformat(),
            })
            meta["evidence"] = evidence
            session.custom_metadata = meta
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to add evidence: %s", e)
            return False

    # ------------------------------------------------------------------
    # AIC-312: Version snapshots
    # ------------------------------------------------------------------

    def save_checkpoint(
        self, workspace_id: int, step_name: str = "", description: str = ""
    ) -> Dict[str, Any]:
        """Save a version snapshot of the current workspace state."""
        try:
            from app.models.solution_architect_models import (
                SolutionAnalysisSession,
                SolutionSessionVersion,
            )

            session = SolutionAnalysisSession.query.get(workspace_id)
            if not session:
                return {"success": False, "error": "Workspace not found"}

            latest = (
                SolutionSessionVersion.query
                .filter_by(session_id=workspace_id)
                .order_by(SolutionSessionVersion.version_number.desc())
                .first()
            )
            next_version = (latest.version_number + 1) if latest else 1

            meta = session.custom_metadata or {}
            snapshot = {
                "artifacts": meta.get("artifacts", {}),
                "pending_approvals": meta.get("pending_approvals", []),
                "workflow_phase": meta.get("workflow_phase"),
                "evidence": meta.get("evidence", []),
                "workspace_type": meta.get("workspace_type", "greenfield"),
                "solution_id": meta.get("solution_id"),
                "current_step": step_name,
            }

            version = SolutionSessionVersion(
                session_id=workspace_id,
                version_number=next_version,
                version_name=step_name or f"v{next_version}",
                description=description or f"Checkpoint at step: {step_name}",
                snapshot=snapshot,
                created_by_id=self.user_id or session.created_by_id,
            )
            db.session.add(version)

            session.current_version = next_version
            if step_name:
                meta = dict(meta)
                meta["workflow_phase"] = step_name
                session.custom_metadata = meta
            session.updated_at = datetime.utcnow()

            db.session.commit()

            return {
                "success": True,
                "version_number": next_version,
                "workspace_id": workspace_id,
            }
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to save checkpoint for workspace %s: %s", workspace_id, e)
            return {"success": False, "error": str(e)}

    def resume(self, workspace_id: int) -> Optional[Dict[str, Any]]:
        """Resume a workspace from its latest version snapshot."""
        try:
            from app.models.solution_architect_models import (
                SolutionAnalysisSession,
                SolutionSessionVersion,
            )

            session = SolutionAnalysisSession.query.get(workspace_id)
            if not session:
                return None

            latest = (
                SolutionSessionVersion.query
                .filter_by(session_id=workspace_id)
                .order_by(SolutionSessionVersion.version_number.desc())
                .first()
            )

            meta = session.custom_metadata or {}
            result = {
                "workspace_id": session.id,
                "name": session.name,
                "description": session.description,
                "status": session.status.value if session.status else "draft",
                "workspace_type": meta.get("workspace_type", "greenfield"),
                "solution_id": meta.get("solution_id"),
                "artifacts": meta.get("artifacts", {}),
                "pending_approvals": meta.get("pending_approvals", []),
                "evidence": meta.get("evidence", []),
                "current_step": meta.get("workflow_phase"),
                "current_version": session.current_version,
                "created_at": session.created_at.isoformat() if session.created_at else None,
                "updated_at": session.updated_at.isoformat() if session.updated_at else None,
            }

            if latest and latest.snapshot:
                snap = latest.snapshot
                result["artifacts"] = snap.get("artifacts", result["artifacts"])
                result["pending_approvals"] = snap.get("pending_approvals", result["pending_approvals"])
                result["evidence"] = snap.get("evidence", result["evidence"])
                result["current_step"] = snap.get("current_step", result["current_step"])

            return result
        except Exception as e:
            logger.error("Failed to resume workspace %s: %s", workspace_id, e)
            return None

    def list_versions(self, workspace_id: int) -> List[Dict[str, Any]]:
        """List version history for a workspace."""
        try:
            from app.models.solution_architect_models import SolutionSessionVersion

            versions = (
                SolutionSessionVersion.query
                .filter_by(session_id=workspace_id)
                .order_by(SolutionSessionVersion.version_number.desc())
                .all()
            )
            return [
                {
                    "version_id": v.id,
                    "version_number": v.version_number,
                    "version_name": v.version_name,
                    "description": v.description,
                    "created_at": v.created_at.isoformat() if v.created_at else None,
                }
                for v in versions
            ]
        except Exception as e:
            logger.error("Failed to list versions for workspace %s: %s", workspace_id, e)
            return []

    # ------------------------------------------------------------------
    # AIC-315: ArchiMate authoring convenience methods
    # ------------------------------------------------------------------

    def create_archimate_element_from_chat(
        self,
        workspace_id: int,
        element_name: str,
        element_type: str,
        layer: str,
        description: str = "",
        solution_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create an ArchiMate element from chat and link to workspace/solution."""
        if element_type not in VALID_ARCHIMATE_TYPES:
            return {
                "success": False,
                "error": (
                    f"Invalid ArchiMate element type: '{element_type}'. "
                    f"Must be one of the ArchiMate 3.2 catalogue types."
                ),
            }
        if layer not in VALID_ARCHIMATE_LAYERS:
            return {
                "success": False,
                "error": (
                    f"Invalid ArchiMate layer: '{layer}'. "
                    f"Must be one of: {', '.join(sorted(VALID_ARCHIMATE_LAYERS))}."
                ),
            }
        try:
            from app.models.models import ArchiMateElement
            element = ArchiMateElement(
                name=element_name[:100],
                type=element_type,
                layer=layer,
                description=description or f"{element_type}: {element_name}",
            )
            db.session.add(element)
            db.session.flush()
            if solution_id:
                try:
                    from app.modules.solutions_strategic.v2.routes.solution_phase_routes import (
                        _sync_archimate_element,
                    )
                    _sync_archimate_element(
                        solution_id,
                        ae_type=element_type,
                        ae_layer=layer,
                        name=element_name[:100],
                        description=description,
                    )
                except Exception as link_err:
                    logger.debug("_sync_archimate_element skipped: %s", link_err)
                    try:
                        from app.models.solution_archimate_element import SolutionArchiMateElement
                        db.session.add(SolutionArchiMateElement(
                            solution_id=solution_id, element_id=element.id,
                            element_role="ai_derived",
                        ))
                    except Exception as fb_err:
                        logger.debug("Fallback solution link skipped: %s", fb_err)
            db.session.commit()
            if workspace_id:
                a_key = f"element_{element.id}"
                a_data = {"element_id": element.id, "name": element_name,
                          "type": element_type, "layer": layer}
                self.set_artifact_state(workspace_id, a_key, ArtifactState.DRAFT.value, a_data)
                self.set_artifact_state(workspace_id, a_key, ArtifactState.CONFIRMED.value)
                self.set_artifact_state(workspace_id, a_key, ArtifactState.PERSISTED.value)
            return {
                "success": True, "element_id": element.id,
                "name": element_name, "type": element_type, "layer": layer,
            }
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to create ArchiMate element from chat: %s", e)
            return {"success": False, "error": str(e)}

    def create_archimate_relationship_from_chat(
        self,
        workspace_id: int,
        source_id: int,
        target_id: int,
        relationship_type: str,
    ) -> Dict[str, Any]:
        """Create a validated ArchiMate relationship from chat."""
        try:
            from app.models.archimate_core import validate_relationship
            from app.models.models import ArchiMateElement, ArchiMateRelationship
            source = ArchiMateElement.query.get(source_id)
            target = ArchiMateElement.query.get(target_id)
            if not source:
                return {"success": False, "error": f"Source element {source_id} not found"}
            if not target:
                return {"success": False, "error": f"Target element {target_id} not found"}
            src_key = _pascal_to_snake(source.type or "")
            tgt_key = _pascal_to_snake(target.type or "")
            is_valid, message = validate_relationship(relationship_type, src_key, tgt_key)
            if not is_valid:
                return {
                    "success": False, "error": message,
                    "source": source.name, "target": target.name,
                    "attempted_type": relationship_type,
                }
            rel = ArchiMateRelationship(
                type=relationship_type, source_id=source_id, target_id=target_id,
            )
            db.session.add(rel)
            db.session.commit()
            if workspace_id:
                r_key = f"relationship_{rel.id}"
                r_data = {"relationship_id": rel.id, "source": source.name,
                          "target": target.name, "type": relationship_type}
                self.set_artifact_state(workspace_id, r_key, ArtifactState.DRAFT.value, r_data)
                self.set_artifact_state(workspace_id, r_key, ArtifactState.CONFIRMED.value)
                self.set_artifact_state(workspace_id, r_key, ArtifactState.PERSISTED.value)
            return {
                "success": True, "relationship_id": rel.id,
                "source": source.name, "target": target.name,
                "type": relationship_type,
                "message": f"Created: {source.name} --{relationship_type}--> {target.name}",
            }
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to create ArchiMate relationship from chat: %s", e)
            return {"success": False, "error": str(e)}

    def get_workspace_archimate_summary(self, workspace_id: int) -> Dict[str, Any]:
        """Return ArchiMate summary for a workspace."""
        try:
            ws = self.load_workspace(workspace_id)
            if not ws:
                return {"success": False, "error": "Workspace not found"}
            artifacts = ws.get("artifacts", {})
            element_ids = []
            for key, val in artifacts.items():
                if key.startswith("element_") and isinstance(val, dict):
                    eid = val.get("data", {}).get("element_id")
                    if eid:
                        element_ids.append(eid)
            relationship_ids = []
            for key, val in artifacts.items():
                if key.startswith("relationship_") and isinstance(val, dict):
                    rid = val.get("data", {}).get("relationship_id")
                    if rid:
                        relationship_ids.append(rid)
            elements_by_layer: Dict[str, int] = {}
            element_names: List[str] = []
            if element_ids:
                from app.models.models import ArchiMateElement
                elements = ArchiMateElement.query.filter(
                    ArchiMateElement.id.in_(element_ids)
                ).all()
                for elem in elements:
                    lyr = elem.layer or "Unknown"
                    elements_by_layer[lyr] = elements_by_layer.get(lyr, 0) + 1
                    element_names.append(f"{elem.name} ({elem.type})")
            return {
                "success": True, "workspace_id": workspace_id,
                "element_count": len(element_ids),
                "elements_by_layer": elements_by_layer,
                "relationship_count": len(relationship_ids),
                "element_names": element_names,
            }
        except Exception as e:
            logger.error("Failed to get workspace ArchiMate summary: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # AIC-316: SAD / Governance convenience methods on kernel
    # ------------------------------------------------------------------

    def generate_sad_from_workspace(self, workspace_id: int) -> Dict[str, Any]:
        """Generate SAD sections from workspace artifacts and persisted model state.

        Loads the workspace, attempts to call StructuredDeliverableService for
        the linked solution, and stores the result as a DRAFT artifact.
        """
        ws = self.load_workspace(workspace_id)
        if not ws:
            return {"success": False, "error": "Workspace not found"}

        solution_id = ws.get("solution_id")
        artifacts = ws.get("artifacts", {})
        sections = []

        # Try StructuredDeliverableService if solution exists
        if solution_id:
            try:
                from app.modules.ai_chat.services.structured_deliverable_service import (
                    StructuredDeliverableService,
                )
                sds = StructuredDeliverableService(user_id=self.user_id)
                sad_result = sds.generate_sad_sections(solution_id)
                if sad_result.get("success") and sad_result.get("sections"):
                    sections = sad_result["sections"]
                    logger.debug("AIC-316: SAD sections from service for solution %s", solution_id)
            except Exception as sds_err:
                logger.debug("AIC-316: StructuredDeliverableService skipped: %s", sds_err)

        # Fallback: derive sections from workspace artifacts
        if not sections:
            if artifacts.get("scope") or artifacts.get("brief"):
                sections.append({"section": "1. Architecture Vision", "state": ArtifactState.DRAFT.value,
                                 "content": "Scope, stakeholders, drivers, goals, and constraints."})
            if artifacts.get("target_state"):
                sections.append({"section": "2. Target Architecture", "state": ArtifactState.DRAFT.value,
                                 "content": "Target-state business, application, and technology architecture."})
            if artifacts.get("options"):
                sections.append({"section": "3. Options Analysis", "state": ArtifactState.DRAFT.value,
                                 "content": "Solution options with pros/cons."})
            if artifacts.get("recommendation"):
                sections.append({"section": "4. Recommendation", "state": ArtifactState.DRAFT.value,
                                 "content": "Recommended option with rationale."})
            if artifacts.get("roadmap"):
                sections.append({"section": "5. Implementation Roadmap", "state": ArtifactState.DRAFT.value,
                                 "content": "Work packages, plateaus, and delivery timeline."})

        # Store as artifact
        self.set_artifact_state(
            workspace_id, "sad_sections",
            ArtifactState.DRAFT.value,
            {"section_count": len(sections)},
        )
        self.save_checkpoint(workspace_id, step_name="SAD_GENERATION")

        return {
            "success": True,
            "sections": sections,
            "solution_id": solution_id,
        }

    def generate_governance_pack(self, workspace_id: int) -> Dict[str, Any]:
        """Assemble a governance pack from workspace artifacts and service data.

        Combines SAD sections, decision records, risk data, and evidence into
        a single governance pack dictionary.
        """
        ws = self.load_workspace(workspace_id)
        if not ws:
            return {"success": False, "error": "Workspace not found"}

        solution_id = ws.get("solution_id")
        artifacts = ws.get("artifacts", {})
        evidence = ws.get("evidence", [])

        pack: Dict[str, Any] = {
            "sad_sections": [],
            "decisions": [],
            "risks": [],
            "evidence": evidence,
        }

        # Gather SAD sections
        sad_artifact = artifacts.get("sad_sections") or artifacts.get("sad_document")
        if sad_artifact:
            pack["sad_sections"].append(sad_artifact.get("data", {}))

        # Gather decision records
        for key, val in artifacts.items():
            if key.startswith("decision_") and isinstance(val, dict):
                pack["decisions"].append(val.get("data", {}))

        # Try risk register via StructuredDeliverableService
        if solution_id:
            try:
                from app.modules.ai_chat.services.structured_deliverable_service import (
                    StructuredDeliverableService,
                )
                sds = StructuredDeliverableService(user_id=self.user_id)
                risk_result = sds.generate_risk_register(solution_id=solution_id)
                if risk_result.get("success"):
                    pack["risks"] = risk_result
                    logger.debug("AIC-316: Risk register generated for solution %s", solution_id)
            except Exception as risk_err:
                logger.debug("AIC-316: Risk register generation skipped: %s", risk_err)

        # Store as artifact
        self.set_artifact_state(
            workspace_id, "governance_pack",
            ArtifactState.DRAFT.value,
            {"decision_count": len(pack["decisions"]), "evidence_count": len(evidence)},
        )
        self.save_checkpoint(workspace_id, step_name="GOVERNANCE_PACK")

        return {
            "success": True,
            "pack": pack,
        }

    def record_architecture_decision(
        self,
        workspace_id: int,
        title: str,
        chosen_option: str,
        rationale: str,
        alternatives: str = "",
        assumptions: str = "",
    ) -> Dict[str, Any]:
        """Create an Architecture Decision Record and link it to the workspace.

        Creates the ADR in the database, links it via SolutionADRLink, and
        tracks it as a workspace artifact.
        """
        try:
            from app.models.architecture_decision import ArchitectureDecision
            from app.models.solution_architect_models import SolutionAnalysisSession

            # Not .query.get(): an identity-map hit on a warm session returns
            # the cached row with no SQL, bypassing the tenant filter.
            session = SolutionAnalysisSession.query.filter_by(id=workspace_id).first()
            org_id = session.organization_id if session else None

            adr = ArchitectureDecision(
                decision_id=ArchitectureDecision.next_decision_id(),
                title=title[:200],
                status="proposed",
                context=f"Decision context for: {title}",
                decision=chosen_option,
                rationale=rationale,
                consequences=f"Alternatives: {alternatives}" if alternatives else "No alternatives documented",
                alternatives=alternatives or None,
                assumptions=assumptions or None,
                organization_id=org_id,
            )
            db.session.add(adr)
            db.session.flush()

            # Link to workspace session
            try:
                from app.models.solution_architect_models import SolutionADRLink
                link = SolutionADRLink(
                    session_id=workspace_id,
                    adr_id=adr.id,
                    relationship_type="informs",
                    linked_by_id=self.user_id,
                )
                db.session.add(link)
            except Exception as link_err:
                logger.debug("SolutionADRLink creation skipped: %s", link_err)

            db.session.commit()

            # Track as artifact
            art_key = f"decision_{adr.id}"
            self.set_artifact_state(
                workspace_id, art_key,
                ArtifactState.DRAFT.value,
                {"adr_id": adr.id, "title": title, "chosen_option": chosen_option},
            )
            self.set_artifact_state(workspace_id, art_key, ArtifactState.CONFIRMED.value)
            self.set_artifact_state(workspace_id, art_key, ArtifactState.PERSISTED.value)

            return {"success": True, "decision_id": adr.id}
        except Exception as e:
            db.session.rollback()
            logger.error("AIC-316: record_architecture_decision failed: %s", e)
            return {"success": False, "error": str(e)}

    def submit_to_arb(self, workspace_id: int) -> Dict[str, Any]:
        """Submit through the canonical ARB service using trusted workspace state."""
        if workspace_id is None:
            return {
                "success": False,
                "reason_codes": ["trusted_workspace_required"],
                "missing_evidence": [
                    {"code": "trusted_workspace_required", "action": "Open a solution workbench"}
                ],
            }
        ws = self.load_workspace(workspace_id)
        if not ws:
            return {
                "success": False,
                "reason_codes": ["workspace_not_found"],
                "missing_evidence": [{"code": "workspace_not_found"}],
            }
        solution_id = ws.get("solution_id")
        if not solution_id:
            return {
                "success": False,
                "reason_codes": ["workspace_solution_missing"],
                "missing_evidence": [{"code": "workspace_solution_missing"}],
            }
        from app.modules.transformation_room.arb_submission_adapter import (
            TypedARBSubmissionAdapter,
        )

        submission = TypedARBSubmissionAdapter.submit_solution_for_actor(
            actor_id=self.user_id,
            solution_id=solution_id,
            trusted_workspace_id=workspace_id,
            trusted_human_reviewed=False,
        )
        if not submission.success:
            blocked = {
                "success": False,
                "reason_codes": submission.reason_codes,
                "missing_evidence": submission.missing_evidence,
            }
            if "cost_source_required" in submission.reason_codes:
                from app.modules.solutions_strategic.v2.services.arb_submission_service import (
                    architect_cost_provenance_recovery,
                )

                blocked["recovery"] = architect_cost_provenance_recovery(solution_id)
            return blocked

        artifact_data = {
            "solution_id": solution_id,
            "review_item_id": submission.review_item_id,
            "review_number": submission.review_number,
            "snapshot_id": submission.snapshot_id,
            "idempotent": submission.idempotent,
            "review_cycle_id": submission.review_cycle_id,
            "canonical_url": submission.canonical_url,
        }
        for state in (
            ArtifactState.DRAFT.value,
            ArtifactState.CONFIRMED.value,
            ArtifactState.PERSISTED.value,
        ):
            if not self.set_artifact_state(workspace_id, "arb_submission", state, artifact_data):
                return {
                    "success": True,
                    **artifact_data,
                    "artifact_recorded": False,
                    "warnings": ["submission_artifact_persistence_failed"],
                }
        return {"success": True, **artifact_data, "artifact_recorded": True, "warnings": []}

    # ------------------------------------------------------------------
    # AIC-318: Evidence gate enforcement
    # ------------------------------------------------------------------

    # Artifact state ordering for comparison
    _STATE_ORDER = {
        "draft": 0,
        "confirmed": 1,
        "persisted": 2,
        "approved": 3,
        "superseded": -1,
    }

    # Required artifacts per workflow type
    _GREENFIELD_REQUIREMENTS = {
        "brief": None,            # any state
        "scope": "draft",         # state >= draft
    }
    _BROWNFIELD_REQUIREMENTS = {
        "portfolio_context": None, # any state
        "current_state": "draft",  # state >= draft
    }

    @classmethod
    def _state_gte(cls, actual_state: str, minimum_state: str) -> bool:
        """Return True if actual_state >= minimum_state in the lifecycle ordering."""
        actual_ord = cls._STATE_ORDER.get(actual_state, -1)
        min_ord = cls._STATE_ORDER.get(minimum_state, 0)
        return actual_ord >= min_ord

    def check_evidence_gate(
        self, workspace_id: int, workflow_type: str = "greenfield"
    ) -> Dict[str, Any]:
        """AIC-318: Check if workspace has sufficient evidence for ARB submission.

        Returns a dict with pass/fail, missing artifacts, artifact summary,
        and suggested actions. Fails closed on any error.
        """
        fail_result = {
            "pass": False,
            "workspace_id": workspace_id,
            "workflow_type": workflow_type,
            "missing": [],
            "artifact_summary": {},
            "suggested_actions": [],
        }

        if workspace_id is None:
            fail_result["missing"].append("workspace_id is None")
            fail_result["suggested_actions"].append("Provide a valid workspace ID")
            return fail_result

        try:
            ws = self.load_workspace(workspace_id)
            if ws is None:
                fail_result["missing"].append("workspace not found")
                fail_result["suggested_actions"].append(
                    "Create a workspace before checking evidence"
                )
                return fail_result

            artifacts = ws.get("artifacts", {})

            # Build artifact summary: {name: state}
            artifact_summary = {}
            for key, entry in artifacts.items():
                if isinstance(entry, dict):
                    artifact_summary[key] = entry.get("state", "unknown")
                else:
                    artifact_summary[key] = "unknown"

            missing = []
            suggested_actions = []

            # Select requirements based on workflow type
            if workflow_type == "brownfield":
                required = self._BROWNFIELD_REQUIREMENTS
            else:
                required = self._GREENFIELD_REQUIREMENTS

            # Check required named artifacts
            for artifact_name, min_state in required.items():
                if artifact_name not in artifacts:
                    missing.append(artifact_name)
                    suggested_actions.append(f"Generate {artifact_name} artifact")
                elif min_state is not None:
                    entry = artifacts[artifact_name]
                    actual_state = entry.get("state", "unknown") if isinstance(entry, dict) else "unknown"
                    if not self._state_gte(actual_state, min_state):
                        missing.append(
                            f"{artifact_name} (state={actual_state}, need>={min_state})"
                        )
                        suggested_actions.append(
                            f"Advance {artifact_name} to at least {min_state}"
                        )

            # Check: at least 2 artifacts with state >= persisted
            persisted_count = sum(
                1 for entry in artifacts.values()
                if isinstance(entry, dict)
                and self._state_gte(entry.get("state", "unknown"), "persisted")
            )
            if persisted_count < 2:
                missing.append(
                    f"persisted artifacts ({persisted_count}/2 minimum)"
                )
                suggested_actions.append(
                    "Persist at least 2 artifacts (confirm then persist)"
                )

            # Check: solution_id present in workspace metadata
            solution_id = ws.get("solution_id")
            if not solution_id:
                missing.append("solution_id in workspace metadata")
                suggested_actions.append(
                    "Link workspace to a solution before ARB submission"
                )

            gate_pass = len(missing) == 0

            return {
                "pass": gate_pass,
                "workspace_id": workspace_id,
                "workflow_type": workflow_type,
                "missing": missing,
                "artifact_summary": artifact_summary,
                "suggested_actions": suggested_actions,
            }

        except Exception as e:
            logger.error(
                "AIC-318: Evidence gate check failed for workspace %s: %s",
                workspace_id, e, exc_info=True,
            )
            fail_result["missing"].append(f"exception: {e}")
            fail_result["suggested_actions"].append(
                "Investigate error and retry evidence gate check"
            )
            return fail_result


# ============================================================================
# AIC-313: GREENFIELD BLUEPRINT WORKFLOW
# ============================================================================

class GreenfieldWorkflow:
    """
    Chat-first greenfield solution blueprint workflow.

    Guides the architect from a blank business brief through scope capture,
    target state design, options analysis, recommendation, and roadmap
    generation — all within the chat surface.
    """

    def __init__(self, kernel: WorkbenchKernel, user_id: Optional[int] = None):
        self.kernel = kernel
        self.user_id = user_id

    def start(self, brief: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Start a greenfield workflow from a business brief."""
        # Create workspace
        ws_result = self.kernel.create_workspace(
            name=f"Greenfield: {brief[:80]}",
            workspace_type=WORKSPACE_TYPE_GREENFIELD,
            description=brief,
            solution_id=(context or {}).get("solution_id"),
        )
        if not ws_result.get("success"):
            return {"success": False, "error": "Failed to create workspace"}

        workspace_id = ws_result["workspace_id"]

        # Store workflow state
        workflow_state = {
            "step": "BRIEF",
            "workspace_id": workspace_id,
            "brief": brief,
            "accumulated": {"brief": brief},
            "workspace_type": WORKSPACE_TYPE_GREENFIELD,
        }

        # Record the brief as a draft artifact
        self.kernel.set_artifact_state(
            workspace_id, "brief",
            ArtifactState.DRAFT.value,
            {"text": brief},
        )

        return {
            "success": True,
            "workflow_state": workflow_state,
            "workspace_id": workspace_id,
            "response": self._build_brief_response(brief, workspace_id),
        }

    def advance(
        self, message: str, wf: Dict, requested_model: str = None
    ) -> Optional[Dict[str, Any]]:
        """Advance the greenfield workflow based on user input."""
        step = wf.get("step", "BRIEF")
        workspace_id = wf.get("workspace_id")
        accumulated = wf.get("accumulated", {})
        msg_lower = message.strip().lower()

        is_advance = msg_lower in ("next", "proceed", "continue", "yes", "go", "ok")
        is_skip = msg_lower in ("skip",)

        # Allow side questions — if message is a question or doesn't look like
        # workflow input, return None so the normal chat handler processes it
        if not is_advance and not is_skip:
            is_question = "?" in message
            is_command = message.startswith("/")
            is_short_feedback = len(message.strip()) < 200 and not any(
                kw in msg_lower for kw in ("add", "change", "update", "include", "remove", "also")
            )
            if is_question or is_command or (is_short_feedback and len(msg_lower.split()) <= 8):
                # Let the normal chat handler process this as a side question
                return None

            # Longer messages are treated as refinement input for the current step
            accumulated.setdefault("user_feedback", {})[step] = message[:2000]
            wf["accumulated"] = accumulated
            return {
                "success": True,
                "workflow_state": wf,
                "response": (
                    f"Got it — I've captured your input for **{step.replace('_', ' ').title()}**.\n\n"
                    f"Type **'next'** to proceed or continue adding detail."
                ),
            }

        # Determine next step
        try:
            current_idx = GREENFIELD_STEPS.index(step)
        except ValueError:
            current_idx = 0
        next_idx = current_idx + 1

        if next_idx >= len(GREENFIELD_STEPS):
            return self._complete_workflow(wf)

        next_step = GREENFIELD_STEPS[next_idx]
        wf["step"] = next_step

        result = self._execute_step(next_step, wf, requested_model)

        # Save checkpoint after each step (AIC-312)
        if workspace_id:
            self.kernel.save_checkpoint(workspace_id, step_name=next_step)

        return result

    def _execute_step(
        self, step: str, wf: Dict, requested_model: str = None
    ) -> Dict[str, Any]:
        """Execute a specific greenfield workflow step."""
        workspace_id = wf.get("workspace_id")
        accumulated = wf.get("accumulated", {})
        accumulated.get("brief", "")

        step_handlers = {
            "SCOPE": self._execute_scope_step,
            "TARGET_STATE": self._execute_target_state_step,
            "OPTIONS": self._execute_options_step,
            "RECOMMENDATION": self._execute_recommendation_step,
            "ROADMAP": self._execute_roadmap_step,
            "SUMMARY": self._execute_summary_step,
        }

        handler = step_handlers.get(step)
        if not handler:
            return {"success": True, "workflow_state": wf, "response": "Unknown step."}

        try:
            result = handler(wf, requested_model)
            result["workflow_state"] = wf

            # Track artifact
            if workspace_id:
                self.kernel.set_artifact_state(
                    workspace_id, step.lower(),
                    ArtifactState.DRAFT.value,
                    {"step": step, "generated_at": datetime.utcnow().isoformat()},
                )

            return result
        except Exception as e:
            logger.error("Greenfield step %s failed: %s", step, e, exc_info=True)
            return {
                "success": True,
                "workflow_state": wf,
                "response": f"Step {step} encountered an issue. Type **'next'** to retry or **'cancel'** to exit.",
            }

    def _build_brief_response(self, brief: str, workspace_id: int) -> str:
        return (
            f"## Greenfield Architecture Workbench\n"
            f"**Workspace ID:** {workspace_id}\n\n"
            f"**Business Brief:** {brief}\n\n"
            f"I've created a workspace for this greenfield design.\n\n"
            f"**Step 1 of 7: Business Brief** — Captured.\n\n"
            f"Type **'next'** to proceed to **Step 2: Scope & Vision** where I'll help "
            f"identify stakeholders, drivers, goals, and constraints.\n\n"
            f"---\n"
            f"*Type **'cancel'** to exit at any time.*"
        )

    def _execute_scope_step(self, wf: Dict, requested_model: str = None) -> Dict:
        """Step 2: Create Solution + ProblemDefinition + persist drivers/goals/constraints."""
        brief = wf["accumulated"].get("brief", "")
        target = brief[:80]
        workspace_id = wf.get("workspace_id")
        user_feedback = wf.get("accumulated", {}).get("user_feedback", {}).get("BRIEF", "")

        # Load real entity data
        try:
            from app.modules.ai_chat.services.multi_domain_chat_service import MultiDomainChatService
            svc = MultiDomainChatService(user_id=self.user_id)
            resolved = svc._resolve_entities_from_message(brief)
            apps = resolved.get("applications", [])
            caps = resolved.get("capabilities", [])
            wf["accumulated"]["resolved_apps"] = [{"id": a["id"], "name": a["name"]} for a in apps[:10]]
            wf["accumulated"]["resolved_caps"] = [{"id": c["id"], "name": c["name"]} for c in caps[:10]]
        except Exception as _re:
            logger.debug("Entity resolution skipped: %s", _re)

        # Create Solution record
        solution_id = wf.get("accumulated", {}).get("solution_id")
        if not solution_id:
            try:
                from app.models.solution_models import Solution
                solution = Solution(
                    name=target, description=brief,
                    created_by_id=self.user_id or 1,
                    status="planned", governance_status="draft", adm_phase="A",
                )
                db.session.add(solution)
                db.session.flush()
                solution_id = solution.id
                wf["accumulated"]["solution_id"] = solution_id
                if workspace_id:
                    self.kernel.update_workspace_metadata(workspace_id, {"solution_id": solution_id})
                db.session.commit()
                logger.info("AIC-313: Created solution %s for workspace %s", solution_id, workspace_id)
            except Exception as _se:
                db.session.rollback()
                logger.error("AIC-313: Solution creation failed: %s", _se)

        # Create ProblemDefinition (FK target for drivers/goals/constraints)
        problem_id = wf.get("accumulated", {}).get("problem_id")
        if not problem_id and workspace_id:
            try:
                from app.models.solution_architect_models import SolutionAnalysisSession, SolutionProblemDefinition
                session_obj = SolutionAnalysisSession.query.get(workspace_id)
                if session_obj:
                    problem = SolutionProblemDefinition(
                        session_id=session_obj.id, problem_description=brief,
                        business_context=user_feedback or brief,
                    )
                    db.session.add(problem)
                    db.session.flush()
                    problem_id = problem.id
                    wf["accumulated"]["problem_id"] = problem_id
                    db.session.commit()
            except Exception as _pe:
                db.session.rollback()
                logger.error("AIC-313: ProblemDefinition creation failed: %s", _pe)

        # Ask LLM for structured scope items as JSON
        json_prompt = (
            f"You are an Enterprise Architecture AI. Business brief: {brief}\n"
            f"User refinement: {user_feedback}\n\n"
            f"Return ONLY valid JSON (no markdown fences):\n"
            f'{{"drivers": [{{"name": "...", "description": "...", "type": "technology"}}], '
            f'"goals": [{{"name": "...", "description": "...", "measurement": "..."}}], '
            f'"constraints": [{{"name": "...", "description": "...", "type": "budget"}}], '
            f'"requirements": [{{"name": "...", "description": "...", "type": "functional"}}]}}\n'
            f"Generate 3 drivers, 3 goals, 2 constraints, 2 requirements. Types must be valid enums."
        )
        llm_json = self._call_llm(json_prompt, requested_model)

        # Parse and persist scope artifacts
        persisted = {"drivers": 0, "goals": 0, "constraints": 0, "requirements": 0}
        if problem_id and solution_id:
            try:
                import json as _json
                json_str = llm_json
                if "```" in json_str:
                    json_str = json_str.split("```")[1]
                    if json_str.startswith("json"):
                        json_str = json_str[4:]
                    json_str = json_str.strip()
                scope = _json.loads(json_str)

                from app.models.solution_architect_models import (
                    SolutionDriver, SolutionGoal, SolutionConstraint,
                    SolutionRequirement, DriverType, ConstraintType, RequirementType,
                )

                for d in scope.get("drivers", [])[:5]:
                    try:
                        dt = DriverType(d.get("type", "internal"))
                    except ValueError:
                        dt = DriverType.INTERNAL
                    db.session.add(SolutionDriver(
                        problem_id=problem_id, name=d["name"][:200],
                        description=d.get("description", ""), driver_type=dt,
                        ai_generated=True, ai_confidence=0.8,
                    ))
                    persisted["drivers"] += 1

                for g in scope.get("goals", [])[:5]:
                    db.session.add(SolutionGoal(
                        problem_id=problem_id, name=g["name"][:200],
                        description=g.get("description", ""),
                        measurement_criteria=g.get("measurement", ""),
                        ai_generated=True, ai_confidence=0.8,
                    ))
                    persisted["goals"] += 1

                for c in scope.get("constraints", [])[:5]:
                    try:
                        ct = ConstraintType(c.get("type", "technical"))
                    except ValueError:
                        ct = ConstraintType.TECHNICAL
                    db.session.add(SolutionConstraint(
                        problem_id=problem_id, name=c["name"][:200],
                        description=c.get("description", ""), constraint_type=ct,
                        ai_generated=True,
                    ))
                    persisted["constraints"] += 1

                for r in scope.get("requirements", [])[:5]:
                    try:
                        rt = RequirementType(r.get("type", "functional"))
                    except ValueError:
                        rt = RequirementType.FUNCTIONAL
                    db.session.add(SolutionRequirement(
                        problem_id=problem_id, solution_id=solution_id,
                        name=r["name"][:200], description=r.get("description", ""),
                        requirement_type=rt, ai_generated=True, ai_confidence=0.8,
                    ))
                    persisted["requirements"] += 1

                db.session.commit()

                # ARCH-LINK-1: sync to ArchiMate catalog
                try:
                    from app.modules.solutions_strategic.v2.routes.solution_phase_routes import _sync_archimate_element
                    for d in scope.get("drivers", [])[:5]:
                        _sync_archimate_element(
                            solution_id,
                            ae_type="Driver",
                            ae_layer="Motivation",
                            name=d["name"][:200],
                        )
                    for g in scope.get("goals", [])[:5]:
                        _sync_archimate_element(
                            solution_id,
                            ae_type="Goal",
                            ae_layer="Motivation",
                            name=g["name"][:200],
                        )
                except Exception as _ae:
                    logger.debug("AIC-313: ArchiMate sync skipped: %s", _ae)

                logger.info("AIC-313: Scope persisted for solution %s: %s", solution_id, persisted)
            except Exception as _je:
                db.session.rollback()
                logger.warning("AIC-313: Scope JSON parse failed: %s", _je)

        # Generate human-readable response
        readable = self._call_llm(
            f"Present TOGAF Phase A scope for: {brief}\n"
            f"List: Stakeholders, Drivers, Goals, Constraints, Requirements as bullet points. "
            f"Mark as AI-Assisted.", requested_model,
        )

        total = sum(persisted.values())
        db_note = (
            f"\n\n> **Persisted:** {persisted['drivers']} drivers, {persisted['goals']} goals, "
            f"{persisted['constraints']} constraints, {persisted['requirements']} requirements "
            f"| **Solution #{solution_id}**"
            if total > 0 else ""
        )

        header = f"## Greenfield: {target}\n**Step 2 of 7: Scope & Vision** (TOGAF Phase A)\n\n"
        footer = db_note + "\n\n---\n*Review and refine, then type **'next'** to proceed. Type **'cancel'** to exit.*"

        return {"success": True, "response": header + readable + footer}

    def _execute_target_state_step(self, wf: Dict, requested_model: str = None) -> Dict:
        """Step 3: Design target state architecture."""
        brief = wf["accumulated"].get("brief", "")
        feedback = wf["accumulated"].get("user_feedback", {})

        prompt = (
            f"You are guiding an Enterprise Architect through a greenfield solution design.\n\n"
            f"**Brief:** {brief}\n"
            f"**Scope feedback:** {feedback.get('SCOPE', 'No changes')}\n\n"
            f"This is STEP 3 of 7: TARGET STATE DESIGN (TOGAF Phase B-D)\n\n"
            f"Propose a target-state architecture that addresses the brief:\n"
            f"1. **Business Architecture** — key processes, services, actors affected\n"
            f"2. **Application Architecture** — application components needed\n"
            f"3. **Technology Architecture** — infrastructure, platforms, middleware\n"
            f"4. **Integration Points** — how components connect\n\n"
            f"Present as a structured list with ArchiMate element types in parentheses.\n"
            f"Tell the user to type **next** to proceed to Step 4: Options Analysis."
        )

        response_text = self._call_llm(prompt, requested_model)
        header = f"## Greenfield: {brief[:60]}\n**Step 3 of 7: Target State Design** (TOGAF Phase B-D)\n\n"
        footer = "\n\n---\n*Type **'next'** to proceed to Options Analysis. Type **'cancel'** to exit.*"

        return {"success": True, "response": header + response_text + footer}

    def _execute_options_step(self, wf: Dict, requested_model: str = None) -> Dict:
        """Step 4: Options analysis with real portfolio data."""
        brief = wf["accumulated"].get("brief", "")
        apps = wf["accumulated"].get("resolved_apps", [])
        wf["accumulated"].get("resolved_caps", [])

        # Load real vendor data
        vendor_ctx = ""
        try:
            from app.models.vendor_models import VendorOrganization
            vendors = VendorOrganization.query.limit(10).all()
            if vendors:
                vendor_ctx = "Available vendors: " + ", ".join(v.name for v in vendors if v.name)
        except Exception as _ve:  # fabricated-ok: guarded skip on error; emits no fabricated value
            logger.debug("Vendor context load failed: %s", _ve)
            vendor_ctx = ""

        app_ctx = ""
        if apps:
            app_ctx = f"Existing applications in scope: {', '.join(a['name'] for a in apps[:5])}"

        prompt = (
            f"You are guiding an Enterprise Architect through a greenfield solution design.\n\n"
            f"**Brief:** {brief}\n"
            f"{app_ctx}\n{vendor_ctx}\n\n"
            f"This is STEP 4 of 7: OPTIONS ANALYSIS (TOGAF Phase E)\n\n"
            f"Present 2-3 solution options:\n"
            f"For each option:\n"
            f"- **Option Name** and approach (Build / Buy / Reuse / Partner / Hybrid)\n"
            f"- **Pros** (3-4 bullet points)\n"
            f"- **Cons** (2-3 bullet points)\n"
            f"- **Estimated complexity** (Low / Medium / High)\n"
            f"- **Risk level** (Low / Medium / High)\n\n"
            f"Reference real vendor products and existing applications where relevant.\n"
            f"Tell the user to type **next** to proceed to Step 5: Recommendation."
        )

        response_text = self._call_llm(prompt, requested_model)
        header = f"## Greenfield: {brief[:60]}\n**Step 4 of 7: Options Analysis** (TOGAF Phase E)\n\n"
        footer = "\n\n---\n*Type **'next'** to proceed to Recommendation. Type **'cancel'** to exit.*"

        return {"success": True, "response": header + response_text + footer}

    def _execute_recommendation_step(self, wf: Dict, requested_model: str = None) -> Dict:
        """Step 5: Generate recommendations via StructuredDeliverableService + persist."""
        brief = wf["accumulated"].get("brief", "")
        feedback = wf["accumulated"].get("user_feedback", {})
        workspace_id = wf.get("workspace_id")
        wf.get("accumulated", {}).get("solution_id")

        # Try StructuredDeliverableService first (AC-313-3)
        rec_count = 0
        try:
            from app.modules.ai_chat.services.structured_deliverable_service import StructuredDeliverableService
            sds = StructuredDeliverableService(user_id=self.user_id)
            analysis = sds.generate_solution_analysis(problem_description=brief)
            if analysis.get("success") and analysis.get("recommendations"):
                rec_count = len(analysis["recommendations"])
                logger.info("AIC-313: StructuredDeliverableService produced %d recommendations", rec_count)
        except Exception as _sds_err:
            logger.debug("AIC-313: StructuredDeliverableService skipped: %s", _sds_err)

        # LLM-generated readable output
        prompt = (
            f"You are guiding an Enterprise Architect through a greenfield solution design.\n\n"
            f"**Brief:** {brief}\n"
            f"**Options feedback:** {feedback.get('OPTIONS', 'No changes')}\n\n"
            f"This is STEP 5 of 7: RECOMMENDATION (TOGAF Phase E)\n\n"
            f"Provide:\n"
            f"1. **Recommended Option** — which and why\n"
            f"2. **Risk Assessment** — top 3 risks with mitigations\n"
            f"3. **Decision Rationale** — why this over alternatives\n"
            f"4. **Assumptions** — key assumptions\n\n"
            f"Mark all as **AI-Assisted**."
        )
        response_text = self._call_llm(prompt, requested_model)

        if workspace_id:
            self.kernel.set_artifact_state(
                workspace_id, "recommendation",
                ArtifactState.DRAFT.value,
                {"text": response_text[:2000], "recommendation_count": rec_count},
            )

        db_note = f"\n\n> **{rec_count} recommendations persisted** to SolutionRecommendation" if rec_count > 0 else ""
        header = f"## Greenfield: {brief[:60]}\n**Step 5 of 7: Recommendation**\n\n"
        footer = db_note + "\n\n---\n*Type **'next'** to proceed to Roadmap. Type **'cancel'** to exit.*"

        return {"success": True, "response": header + response_text + footer}

    def _execute_roadmap_step(self, wf: Dict, requested_model: str = None) -> Dict:
        """Step 6: Generate work packages + SolutionPlateau records."""
        brief = wf["accumulated"].get("brief", "")
        workspace_id = wf.get("workspace_id")
        solution_id = wf.get("accumulated", {}).get("solution_id")

        # Create work packages in DB
        work_packages = self._create_work_packages(wf)

        # Create SolutionPlateau records (AC-313-5)
        plateau_count = 0
        if solution_id:
            try:
                from app.models.solution_lifecycle_models import SolutionPlateau
                plateaus = [
                    SolutionPlateau(
                        solution_id=solution_id, name="P1: Foundation",
                        description=f"Infrastructure and base setup for {brief[:60]}",
                        order=1,
                    ),
                    SolutionPlateau(
                        solution_id=solution_id, name="P2: Core Implementation",
                        description=f"Core feature delivery for {brief[:60]}",
                        order=2,
                    ),
                    SolutionPlateau(
                        solution_id=solution_id, name="P3: Integration & Go-Live",
                        description=f"Integration testing and production cutover for {brief[:60]}",
                        order=3,
                    ),
                ]
                for p in plateaus:
                    db.session.add(p)
                db.session.commit()
                plateau_count = len(plateaus)
                logger.info("AIC-313: Created %d plateaus for solution %s", plateau_count, solution_id)
            except Exception as _pe:
                db.session.rollback()
                logger.error("AIC-313: Plateau creation failed: %s", _pe)

        prompt = (
            f"You are guiding an Enterprise Architect through a greenfield solution design.\n\n"
            f"**Brief:** {brief}\n\n"
            f"This is STEP 6 of 7: DELIVERY ROADMAP (TOGAF Phase F)\n\n"
            f"Generate a delivery roadmap with:\n"
            f"1. **Work Packages** — 3-5 units with dependencies\n"
            f"2. **Transition Plateaus** — 2-3 milestone architectures\n"
            f"3. **Timeline** — sequencing across quarters\n"
            f"4. **Critical Path** — which packages are critical\n"
            f"5. **Blockers & Assumptions**\n\n"
            f"Mark as AI-Assisted."
        )
        response_text = self._call_llm(prompt, requested_model)

        if workspace_id:
            self.kernel.set_artifact_state(
                workspace_id, "roadmap",
                ArtifactState.DRAFT.value,
                {"work_package_count": len(work_packages), "plateau_count": plateau_count},
            )

        db_note = (
            f"\n\n> **Persisted:** {len(work_packages)} work packages, {plateau_count} transition plateaus"
            if work_packages or plateau_count else ""
        )
        header = f"## Greenfield: {brief[:60]}\n**Step 6 of 7: Delivery Roadmap** (TOGAF Phase F)\n\n"
        footer = db_note + "\n\n---\n*Type **'next'** for the final summary. Type **'cancel'** to exit.*"

        return {"success": True, "response": header + response_text + footer}

    def _execute_summary_step(self, wf: Dict, requested_model: str = None) -> Dict:
        """Step 7: Final summary with links to persisted artifacts."""
        workspace_id = wf.get("workspace_id")
        brief = wf["accumulated"].get("brief", "")
        solution_id = wf.get("workspace_state", {}).get("solution_id") or wf.get("accumulated", {}).get("solution_id")

        artifacts = {}
        if workspace_id:
            ws = self.kernel.load_workspace(workspace_id)
            artifacts = (ws or {}).get("artifacts", {})

        artifact_summary = "\n".join(
            f"- **{key.replace('_', ' ').title()}**: {val.get('state', 'unknown')}"
            for key, val in artifacts.items()
        ) or "No artifacts recorded yet."

        sol_link = f"\n\n[View Solution Detail](/solutions/{solution_id})" if solution_id else ""
        codegen_link = f"\n- [Generate code from this blueprint](/solutions/{solution_id}/codegen)" if solution_id else ""

        response = (
            f"## Greenfield Design Complete\n\n"
            f"**Brief:** {brief}\n"
            f"**Workspace ID:** {workspace_id}\n\n"
            f"### Artifact Summary\n{artifact_summary}\n\n"
            f"### Next Actions\n"
            f"- Review and confirm each artifact (say 'confirm [artifact]')\n"
            f"- Submit to ARB for review (say 'submit to ARB')\n"
            f"- Export as Architecture Brief (say 'export brief')\n"
            f"- Generate ArchiMate viewpoint (say 'generate viewpoint'){codegen_link}{sol_link}\n\n"
            f"*All artifacts are in **draft** state until explicitly confirmed.*"
        )

        return {"success": True, "response": response}

    def _complete_workflow(self, wf: Dict) -> Dict:
        """Complete the greenfield workflow."""
        return self._execute_summary_step(wf)

    def _create_work_packages(self, wf: Dict) -> list:
        """Create work packages from the workflow accumulated context."""
        workspace_id = wf.get("workspace_id")
        brief = wf.get("accumulated", {}).get("brief", "")
        if not workspace_id:
            return []

        try:
            from app.models.implementation_migration import WorkPackage

            packages = []
            wp_names = [
                f"Foundation & Infrastructure for {brief[:40]}",
                f"Core Implementation for {brief[:40]}",
                f"Integration & Testing for {brief[:40]}",
            ]
            for i, name in enumerate(wp_names):
                wp = WorkPackage(
                    name=name[:255],
                    summary=f"Auto-generated from greenfield workbench workspace {workspace_id}",
                    status="planned",
                    priority="medium",
                    sequence_order=i + 1,
                    owner_id=self.user_id,
                )
                db.session.add(wp)
                sync_archimate_element(wp)
                packages.append(wp)

            db.session.commit()
            return packages
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to create work packages: %s", e)
            return []

    def _call_llm(self, prompt: str, requested_model: str = None) -> str:
        """Call LLM and return response text."""
        try:
            from app.services.llm_service import LLMService

            provider_name, model = LLMService._get_configured_provider()
            if requested_model:
                from app.modules.ai_chat.services.multi_domain_chat_service import MultiDomainChatService
                svc = MultiDomainChatService(user_id=self.user_id)
                resolved = svc._resolve_requested_model(requested_model)
                if resolved:
                    provider_name, model = resolved

            response_text, _ = LLMService._call_llm(
                prompt=prompt, model=model, provider=provider_name,
                user_id=self.user_id, max_tokens=2000,
            )
            return response_text or "Analysis generated. Review the results above."
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            return f"*AI analysis unavailable ({e}). Provide manual input or type 'next' to skip.*"


# ============================================================================
# AIC-314: BROWNFIELD MODERNIZATION WORKFLOW
# ============================================================================

class BrownfieldWorkflow:
    """
    Chat-first brownfield modernization workflow.

    Loads existing portfolio data, produces current-state assessment,
    gap analysis, transition plan, and migration outputs from real
    repository data. Distinguishes facts from recommendations.
    """

    def __init__(self, kernel: WorkbenchKernel, user_id: Optional[int] = None):
        self.kernel = kernel
        self.user_id = user_id

    def start(self, target_domain: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Start a brownfield workflow for a target domain."""
        ws_result = self.kernel.create_workspace(
            name=f"Brownfield: {target_domain[:80]}",
            workspace_type=WORKSPACE_TYPE_BROWNFIELD,
            description=f"Modernization assessment for {target_domain}",
            solution_id=(context or {}).get("solution_id"),
        )
        if not ws_result.get("success"):
            return {"success": False, "error": "Failed to create workspace"}

        workspace_id = ws_result["workspace_id"]

        # Load current-state data from portfolio
        portfolio_data = self._load_portfolio_context(target_domain)

        workflow_state = {
            "step": "CONTEXT_LOAD",
            "workspace_id": workspace_id,
            "target_domain": target_domain,
            "accumulated": {
                "target_domain": target_domain,
                "portfolio": portfolio_data,
            },
            "workspace_type": WORKSPACE_TYPE_BROWNFIELD,
        }

        self.kernel.set_artifact_state(
            workspace_id, "portfolio_context",
            ArtifactState.PERSISTED.value,
            {"app_count": portfolio_data.get("app_count", 0)},
        )

        return {
            "success": True,
            "workflow_state": workflow_state,
            "workspace_id": workspace_id,
            "response": self._build_context_response(target_domain, portfolio_data, workspace_id),
        }

    def advance(
        self, message: str, wf: Dict, requested_model: str = None
    ) -> Optional[Dict[str, Any]]:
        """Advance the brownfield workflow."""
        step = wf.get("step", "CONTEXT_LOAD")
        workspace_id = wf.get("workspace_id")
        accumulated = wf.get("accumulated", {})
        msg_lower = message.strip().lower()

        is_advance = msg_lower in ("next", "proceed", "continue", "yes", "go", "ok")
        is_skip = msg_lower in ("skip",)

        # Allow side questions — if message is a question or doesn't look like
        # workflow input, return None so the normal chat handler processes it
        if not is_advance and not is_skip:
            is_question = "?" in message
            is_command = message.startswith("/")
            is_short_feedback = len(message.strip()) < 200 and not any(
                kw in msg_lower for kw in ("add", "change", "update", "include", "remove", "also")
            )
            if is_question or is_command or (is_short_feedback and len(msg_lower.split()) <= 8):
                # Let the normal chat handler process this as a side question
                return None

            # Longer messages are treated as refinement input for the current step
            accumulated.setdefault("user_feedback", {})[step] = message[:2000]
            wf["accumulated"] = accumulated
            return {
                "success": True,
                "workflow_state": wf,
                "response": (
                    f"Got it — I've captured your input for **{step.replace('_', ' ').title()}**.\n\n"
                    f"Type **'next'** to proceed or continue adding detail."
                ),
            }

        try:
            current_idx = BROWNFIELD_STEPS.index(step)
        except ValueError:
            current_idx = 0
        next_idx = current_idx + 1

        if next_idx >= len(BROWNFIELD_STEPS):
            return self._complete_workflow(wf)

        next_step = BROWNFIELD_STEPS[next_idx]
        wf["step"] = next_step

        result = self._execute_step(next_step, wf, requested_model)

        # Save checkpoint after each step (AIC-314)
        if workspace_id:
            self.kernel.save_checkpoint(workspace_id, step_name=next_step)

        return result

    def _execute_step(self, step: str, wf: Dict, requested_model: str = None) -> Dict:
        """Execute a brownfield workflow step."""
        workspace_id = wf.get("workspace_id")

        step_handlers = {
            "CURRENT_STATE": self._execute_current_state,
            "GAP_ANALYSIS": self._execute_gap_analysis,
            "TRANSITION_PLAN": self._execute_transition_plan,
            "MIGRATION_PLAN": self._execute_migration_plan,
            "SUMMARY": self._execute_summary,
        }

        handler = step_handlers.get(step)
        if not handler:
            return {"success": True, "workflow_state": wf, "response": "Unknown step."}

        try:
            result = handler(wf, requested_model)
            result["workflow_state"] = wf

            if workspace_id:
                self.kernel.set_artifact_state(
                    workspace_id, step.lower(),
                    ArtifactState.DRAFT.value,
                    {"step": step},
                )

            return result
        except Exception as e:
            logger.error("Brownfield step %s failed: %s", step, e, exc_info=True)
            return {
                "success": True,
                "workflow_state": wf,
                "response": f"Step {step} encountered an issue. Type **'next'** to continue.",
            }

    def _load_portfolio_context(self, target_domain: str) -> Dict:
        """Load real portfolio data for the target domain."""
        result = {"target_domain": target_domain, "app_count": 0, "apps": [], "capabilities": [], "vendors": []}
        try:
            from app.models.models import ApplicationComponent
            apps = ApplicationComponent.query.filter(
                ApplicationComponent.name.ilike(f"%{target_domain}%")
            ).limit(20).all()
            result["app_count"] = len(apps)
            result["apps"] = [
                {"id": a.id, "name": a.name, "status": getattr(a, "lifecycle_status", None)}
                for a in apps
            ]
        except Exception as _ae:
            logger.debug("Portfolio app load failed: %s", _ae)

        try:
            from app.models.models import ArchiMateElement
            elements = ArchiMateElement.query.filter(
                ArchiMateElement.name.ilike(f"%{target_domain}%")
            ).limit(20).all()
            result["archimate_elements"] = [
                {"id": e.id, "name": e.name, "type": e.type, "layer": e.layer}
                for e in elements
            ]
        except Exception as _ee:
            logger.debug("Portfolio element load failed: %s", _ee)

        try:
            from app.models.vendor_models import VendorOrganization
            vendors = VendorOrganization.query.limit(10).all()
            result["vendors"] = [{"id": v.id, "name": v.name} for v in vendors if v.name]
        except Exception as _ve:
            logger.debug("Portfolio vendor load failed: %s", _ve)

        return result

    def _build_context_response(self, target_domain: str, portfolio: Dict, workspace_id: int) -> str:
        apps = portfolio.get("apps", [])
        elements = portfolio.get("archimate_elements", [])

        app_list = "\n".join(f"  - {a['name']} (status: {a.get('status', 'unknown')})" for a in apps[:10])
        elem_list = "\n".join(f"  - {e['name']} ({e.get('type', '')}, {e.get('layer', '')})" for e in elements[:10])

        return (
            f"## Brownfield Modernization Workbench\n"
            f"**Target Domain:** {target_domain}\n"
            f"**Workspace ID:** {workspace_id}\n\n"
            f"### Portfolio Context Loaded (Repository Facts)\n"
            f"**Applications found:** {portfolio.get('app_count', 0)}\n"
            f"{app_list or '  No matching applications found.'}\n\n"
            f"**ArchiMate Elements:**\n{elem_list or '  No matching elements found.'}\n\n"
            f"Type **'next'** to proceed to **Current State Assessment**.\n\n"
            f"---\n"
            f"*All counts above are from the repository — not AI-generated.*\n"
            f"*Type **'cancel'** to exit at any time.*"
        )

    def _execute_current_state(self, wf: Dict, requested_model: str = None) -> Dict:
        """Step 2: Current state assessment + Solution record + blast radius analysis."""
        portfolio = wf["accumulated"].get("portfolio", {})
        target = wf["accumulated"].get("target_domain", "")
        workspace_id = wf.get("workspace_id")
        apps = portfolio.get("apps", [])

        app_summary = ", ".join(a["name"] for a in apps[:8]) if apps else "none found"

        # Create Solution record (AIC-314 — same pattern as greenfield SCOPE step)
        solution_id = wf.get("accumulated", {}).get("solution_id")
        if not solution_id:
            try:
                from app.models.solution_models import Solution
                solution = Solution(
                    name=f"Modernization: {target[:70]}", description=f"Brownfield modernization for {target}",
                    created_by_id=self.user_id or 1,
                    status="planned", governance_status="draft", adm_phase="B",
                )
                db.session.add(solution)
                db.session.flush()
                solution_id = solution.id
                wf["accumulated"]["solution_id"] = solution_id
                if workspace_id:
                    self.kernel.update_workspace_metadata(workspace_id, {"solution_id": solution_id})
                db.session.commit()
                logger.info("AIC-314: Created solution %s for workspace %s", solution_id, workspace_id)
            except Exception as _se:
                db.session.rollback()
                logger.error("AIC-314: Solution creation failed: %s", _se)

        # Blast radius analysis via AIImpactAnalysisService (AIC-314)
        impact_count = 0
        try:
            from app.modules.ai_chat.services.ai_impact_analysis_service import AIImpactAnalysisService
            for app_data in portfolio.get("apps", [])[:5]:
                impact = AIImpactAnalysisService.analyze_application_impact(app_data["id"], "modification")
                if impact and impact.get("success"):
                    impact_count += 1
            if impact_count > 0 and workspace_id:
                self.kernel.update_workspace_metadata(workspace_id, {"impact_analyses": impact_count})
        except Exception as _impact_err:
            logger.debug("AIC-314: Impact analysis skipped: %s", _impact_err)

        prompt = (
            f"You are assessing the current state of the '{target}' domain.\n\n"
            f"**Repository facts (DO NOT fabricate — these are real):**\n"
            f"- Applications: {app_summary}\n"
            f"- Total app count: {portfolio.get('app_count', 0)}\n\n"
            f"Produce a **Current State Assessment**:\n"
            f"1. **Application Landscape** — summarize what exists\n"
            f"2. **Technology Stack** — infer from the applications\n"
            f"3. **Integration Points** — likely connections\n"
            f"4. **Pain Points** — common issues with this kind of estate\n\n"
            f"Clearly label repository facts vs. AI inferences.\n"
            f"Tell the user to type **next** for Gap Analysis."
        )

        response_text = self._call_llm(prompt, requested_model)

        db_note = ""
        notes = []
        if solution_id:
            notes.append(f"Solution #{solution_id}")
        if impact_count > 0:
            notes.append(f"{impact_count} impact analyses")
        if notes:
            db_note = f"\n\n> **Persisted:** {' | '.join(notes)}"

        header = f"## Brownfield: {target}\n**Step 2 of 6: Current State Assessment**\n\n"
        footer = db_note + "\n\n---\n*Type **'next'** to proceed to Gap Analysis.*"

        return {"success": True, "response": header + response_text + footer}

    def _execute_gap_analysis(self, wf: Dict, requested_model: str = None) -> Dict:
        """Step 3: Gap analysis + AI gap detection service integration."""
        target = wf["accumulated"].get("target_domain", "")
        wf["accumulated"].get("portfolio", {})
        workspace_id = wf.get("workspace_id")

        # Call AIGapDetectionService for real gap data (AIC-314)
        gap_context = ""
        gap_count = 0
        try:
            from app.modules.ai_chat.services.ai_gap_detection_service import AIGapDetectionService
            gap_svc = AIGapDetectionService()
            gap_summary = gap_svc.get_comprehensive_gap_summary()
            if gap_summary:
                low_cov = gap_summary.get("low_coverage_capabilities", [])
                rat_opps = gap_summary.get("rationalization_opportunities", [])
                critical = gap_summary.get("critical_gaps", [])
                gap_count = len(low_cov) + len(rat_opps) + len(critical)
                parts = []
                if low_cov:
                    parts.append(f"- Low coverage capabilities: {len(low_cov)}")
                if rat_opps:
                    parts.append(f"- Rationalization opportunities: {len(rat_opps)}")
                if critical:
                    parts.append(f"- Critical gaps: {len(critical)}")
                if parts:
                    gap_context = "\n**Repository Gap Data (real):**\n" + "\n".join(parts) + "\n"
                if workspace_id:
                    self.kernel.update_workspace_metadata(workspace_id, {"gap_detection_count": gap_count})
        except Exception as _ge:
            logger.debug("AIC-314: Gap detection service skipped: %s", _ge)

        prompt = (
            f"For the '{target}' domain, perform a gap analysis:\n\n"
            f"{gap_context}\n"
            f"1. **Capability Gaps** — what capabilities are missing or underserved\n"
            f"2. **Technology Gaps** — outdated or missing technology components\n"
            f"3. **Integration Gaps** — missing connections between systems\n"
            f"4. **Reuse Opportunities** — existing assets that could be leveraged\n\n"
            f"Mark AI-inferred items as '(AI-Assisted)' vs. repository-backed facts.\n"
            f"Tell the user to type **next** for Transition Plan."
        )

        response_text = self._call_llm(prompt, requested_model)

        db_note = f"\n\n> **Gap detection:** {gap_count} repository-backed findings" if gap_count > 0 else ""
        header = f"## Brownfield: {target}\n**Step 3 of 6: Gap Analysis**\n\n"
        footer = db_note + "\n\n---\n*Type **'next'** to proceed to Transition Plan.*"

        return {"success": True, "response": header + response_text + footer}

    def _execute_transition_plan(self, wf: Dict, requested_model: str = None) -> Dict:
        target = wf["accumulated"].get("target_domain", "")

        prompt = (
            f"Design a transition architecture for modernizing the '{target}' domain:\n\n"
            f"1. **Target State** — what the modernized landscape looks like\n"
            f"2. **Transition Plateaus** — 2-3 intermediate states\n"
            f"3. **Migration Strategy** — approach per application (rehost/replatform/refactor/retire/retain)\n"
            f"4. **Dependencies** — what must happen before what\n"
            f"5. **Risks** — top 3 migration risks with mitigation\n\n"
            f"Tell the user to type **next** for Migration Plan."
        )

        response_text = self._call_llm(prompt, requested_model)
        header = f"## Brownfield: {target}\n**Step 4 of 6: Transition Plan**\n\n"
        footer = "\n\n---\n*Type **'next'** to proceed to Migration Plan.*"

        return {"success": True, "response": header + response_text + footer}

    def _execute_migration_plan(self, wf: Dict, requested_model: str = None) -> Dict:
        """Step 5: Migration plan + SolutionPlateau records (same as greenfield ROADMAP)."""
        target = wf["accumulated"].get("target_domain", "")
        workspace_id = wf.get("workspace_id")
        solution_id = wf.get("accumulated", {}).get("solution_id")

        # Create SolutionPlateau records (AIC-314 — same pattern as greenfield ROADMAP)
        plateau_count = 0
        if solution_id:
            try:
                from app.models.solution_lifecycle_models import SolutionPlateau
                plateaus = [
                    SolutionPlateau(
                        solution_id=solution_id, name="P1: Assessment & Quick Wins",
                        description=f"Current state validation and low-risk modernization for {target[:60]}",
                        order=1,
                    ),
                    SolutionPlateau(
                        solution_id=solution_id, name="P2: Core Migration",
                        description=f"Primary application migration and refactoring for {target[:60]}",
                        order=2,
                    ),
                    SolutionPlateau(
                        solution_id=solution_id, name="P3: Decommission & Cutover",
                        description=f"Legacy decommission and production cutover for {target[:60]}",
                        order=3,
                    ),
                ]
                for p in plateaus:
                    db.session.add(p)
                db.session.commit()
                plateau_count = len(plateaus)
                logger.info("AIC-314: Created %d plateaus for solution %s", plateau_count, solution_id)
            except Exception as _pe:
                db.session.rollback()
                logger.error("AIC-314: Plateau creation failed: %s", _pe)

        # Create work packages
        work_packages = self._create_work_packages(wf)

        prompt = (
            f"Generate a migration execution plan for the '{target}' domain:\n\n"
            f"1. **Work Packages** — 3-5 implementation units with owners and dependencies\n"
            f"2. **Timeline** — sequencing across quarters\n"
            f"3. **Critical Path** — which items are blocking\n"
            f"4. **Assumptions & Blockers** — what could derail the plan\n\n"
            f"Tell the user to type **next** for the final summary."
        )

        response_text = self._call_llm(prompt, requested_model)

        if workspace_id:
            self.kernel.set_artifact_state(
                workspace_id, "migration_plan",
                ArtifactState.DRAFT.value,
                {"work_package_count": len(work_packages), "plateau_count": plateau_count},
            )

        db_note = (
            f"\n\n> **Persisted:** {len(work_packages)} work packages, {plateau_count} transition plateaus"
            if work_packages or plateau_count else ""
        )
        header = f"## Brownfield: {target}\n**Step 5 of 6: Migration Plan** (TOGAF Phase F)\n\n"
        footer = db_note + "\n\n---\n*Type **'next'** for the final summary.*"

        return {"success": True, "response": header + response_text + footer}

    def _execute_summary(self, wf: Dict, requested_model: str = None) -> Dict:
        """Step 6: Final summary with links to persisted artifacts."""
        workspace_id = wf.get("workspace_id")
        target = wf["accumulated"].get("target_domain", "")
        solution_id = wf.get("accumulated", {}).get("solution_id")

        artifacts = {}
        if workspace_id:
            ws = self.kernel.load_workspace(workspace_id)
            artifacts = (ws or {}).get("artifacts", {})

        artifact_summary = "\n".join(
            f"- **{key.replace('_', ' ').title()}**: {val.get('state', 'unknown')}"
            for key, val in artifacts.items()
        ) or "No artifacts recorded."

        sol_link = f"\n\n[View Solution Detail](/solutions/{solution_id})" if solution_id else ""
        codegen_link = f"\n- [Generate code from this blueprint](/solutions/{solution_id}/codegen)" if solution_id else ""

        return {
            "success": True,
            "response": (
                f"## Brownfield Modernization Complete: {target}\n\n"
                f"**Workspace ID:** {workspace_id}\n\n"
                f"### Artifact Summary\n{artifact_summary}\n\n"
                f"### Next Actions\n"
                f"- Confirm artifacts (say 'confirm [artifact]')\n"
                f"- Generate ArchiMate viewpoint (say 'generate viewpoint')\n"
                f"- Create governance pack (say 'generate governance pack')\n"
                f"- Submit to ARB (say 'submit to ARB'){codegen_link}{sol_link}\n\n"
                f"*All artifacts are in **draft** state until explicitly confirmed.*"
            ),
        }

    def _create_work_packages(self, wf: Dict) -> list:
        """Create work packages from the brownfield workflow context."""
        workspace_id = wf.get("workspace_id")
        target = wf.get("accumulated", {}).get("target_domain", "")
        if not workspace_id:
            return []

        try:
            from app.models.implementation_migration import WorkPackage

            packages = []
            wp_names = [
                f"Assessment & Discovery for {target[:40]}",
                f"Core Migration for {target[:40]}",
                f"Decommission & Cutover for {target[:40]}",
            ]
            for i, name in enumerate(wp_names):
                wp = WorkPackage(
                    name=name[:255],
                    summary=f"Auto-generated from brownfield workbench workspace {workspace_id}",
                    status="planned",
                    priority="medium",
                    sequence_order=i + 1,
                    owner_id=self.user_id,
                )
                db.session.add(wp)
                sync_archimate_element(wp)
                packages.append(wp)

            db.session.commit()
            return packages
        except Exception as e:
            db.session.rollback()
            logger.error("AIC-314: Failed to create work packages: %s", e)
            return []

    def _complete_workflow(self, wf: Dict) -> Dict:
        return self._execute_summary(wf)

    def _call_llm(self, prompt: str, requested_model: str = None) -> str:
        try:
            from app.services.llm_service import LLMService
            provider_name, model = LLMService._get_configured_provider()
            response_text, _ = LLMService._call_llm(
                prompt=prompt, model=model, provider=provider_name,
                user_id=self.user_id, max_tokens=2000,
            )
            return response_text or "Analysis generated."
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            return "*AI analysis unavailable. Type 'next' to continue.*"


# ============================================================================
# AIC-315: ARCHIMATE 3.2 AUTHORING FROM CHAT
# ============================================================================

class ArchiMateChatAuthoring:
    """
    Chat-driven ArchiMate 3.2 element, relationship, and viewpoint authoring.

    Routes all creation through canonical model services and validates
    relationship legality before persistence.
    """

    # Valid ArchiMate 3.2 relationship types (subset of commonly used)
    VALID_RELATIONSHIPS = {
        ("ApplicationComponent", "ApplicationService"): ["serving", "composition"],
        ("ApplicationComponent", "ApplicationComponent"): ["composition", "aggregation", "flow", "serving"],
        ("BusinessProcess", "ApplicationService"): ["serving"],
        ("BusinessProcess", "BusinessProcess"): ["composition", "triggering", "flow"],
        ("Node", "ApplicationComponent"): ["assignment"],
        ("Driver", "Goal"): ["influence"],
        ("Goal", "Requirement"): ["realization"],
        ("Requirement", "ApplicationComponent"): ["realization"],
        ("Stakeholder", "Driver"): ["association"],
    }

    def __init__(self, kernel: WorkbenchKernel, user_id: Optional[int] = None):
        self.kernel = kernel
        self.user_id = user_id

    def create_element(
        self,
        name: str,
        element_type: str,
        layer: str,
        description: str = "",
        workspace_id: Optional[int] = None,
        solution_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create an ArchiMate element via chat and persist it."""
        # Validate element type against ArchiMate 3.2 catalogue
        if element_type not in VALID_ARCHIMATE_TYPES:
            return {
                "success": False,
                "error": (
                    f"Invalid ArchiMate element type: '{element_type}'. "
                    f"Must be one of the ArchiMate 3.2 catalogue types."
                ),
            }
        # Validate layer
        if layer not in VALID_ARCHIMATE_LAYERS:
            return {
                "success": False,
                "error": (
                    f"Invalid ArchiMate layer: '{layer}'. "
                    f"Must be one of: {', '.join(sorted(VALID_ARCHIMATE_LAYERS))}."
                ),
            }
        try:
            from app.models.models import ArchiMateElement

            element = ArchiMateElement(
                name=name[:100],
                type=element_type,
                layer=layer,
                description=description,
            )
            db.session.add(element)
            db.session.flush()

            # Link to solution if provided
            if solution_id:
                try:
                    from app.models.solution_archimate_element import SolutionArchiMateElement
                    link = SolutionArchiMateElement(
                        solution_id=solution_id,
                        element_id=element.id,
                        element_role="ai_derived",
                    )
                    db.session.add(link)
                except Exception as _le:
                    logger.debug("Solution-element link skipped: %s", _le)

            db.session.commit()

            # Track artifact — step through valid lifecycle transitions
            if workspace_id:
                art_key = f"element_{element.id}"
                art_data = {"element_id": element.id, "name": name, "type": element_type}
                self.kernel.set_artifact_state(workspace_id, art_key, ArtifactState.DRAFT.value, art_data)
                self.kernel.set_artifact_state(workspace_id, art_key, ArtifactState.CONFIRMED.value)
                self.kernel.set_artifact_state(workspace_id, art_key, ArtifactState.PERSISTED.value)

            return {
                "success": True,
                "element_id": element.id,
                "name": name,
                "type": element_type,
                "layer": layer,
                "message": f"Created ArchiMate element: **{name}** ({element_type}, {layer} layer)",
            }
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to create ArchiMate element: %s", e)
            return {"success": False, "error": str(e)}

    def create_relationship(
        self,
        source_id: int,
        target_id: int,
        relationship_type: str,
        workspace_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a validated ArchiMate relationship."""
        try:
            from app.models.archimate_core import validate_relationship
            from app.models.models import ArchiMateElement, ArchiMateRelationship

            source = ArchiMateElement.query.get(source_id)
            target = ArchiMateElement.query.get(target_id)
            if not source or not target:
                return {"success": False, "error": "Source or target element not found"}

            # Validate via canonical ArchiMate 3.2 relationship matrix
            src_key = _pascal_to_snake(source.type or "")
            tgt_key = _pascal_to_snake(target.type or "")
            is_valid, message = validate_relationship(
                relationship_type, src_key, tgt_key,
            )
            if not is_valid:
                return {
                    "success": False,
                    "error": message,
                }

            # Also check local pair whitelist for stricter validation
            pair = (source.type, target.type)
            valid_types = self.VALID_RELATIONSHIPS.get(pair, [])
            if valid_types and relationship_type not in valid_types:
                return {
                    "success": False,
                    "error": f"Invalid relationship: {source.type} --{relationship_type}--> {target.type}. "
                             f"Valid types: {', '.join(valid_types)}",
                }

            rel = ArchiMateRelationship(
                type=relationship_type,
                source_id=source_id,
                target_id=target_id,
            )
            db.session.add(rel)
            db.session.commit()

            if workspace_id:
                rel_key = f"relationship_{rel.id}"
                rel_data = {"source": source.name, "target": target.name, "type": relationship_type}
                self.kernel.set_artifact_state(workspace_id, rel_key, ArtifactState.DRAFT.value, rel_data)
                self.kernel.set_artifact_state(workspace_id, rel_key, ArtifactState.CONFIRMED.value)
                self.kernel.set_artifact_state(workspace_id, rel_key, ArtifactState.PERSISTED.value)

            return {
                "success": True,
                "relationship_id": rel.id,
                "message": f"Created relationship: {source.name} --{relationship_type}--> {target.name}",
            }
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to create relationship: %s", e)
            return {"success": False, "error": str(e)}

    def generate_viewpoint(
        self,
        workspace_id: int,
        viewpoint_type: str = "application_cooperation",
        solution_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate an ArchiMate viewpoint from workspace elements."""
        try:
            ws = self.kernel.load_workspace(workspace_id)
            if not ws:
                return {"success": False, "error": "Workspace not found"}

            # Gather elements from workspace artifacts
            artifacts = ws.get("artifacts", {})
            element_ids = [
                v["data"].get("element_id")
                for k, v in artifacts.items()
                if k.startswith("element_") and v.get("data", {}).get("element_id")
            ]

            from app.models.models import ArchiMateElement
            elements = ArchiMateElement.query.filter(
                ArchiMateElement.id.in_(element_ids)
            ).all() if element_ids else []

            if not elements and solution_id:
                from app.models.solution_archimate_element import SolutionArchiMateElement
                links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
                element_ids = [link.element_id for link in links]
                elements = ArchiMateElement.query.filter(
                    ArchiMateElement.id.in_(element_ids)
                ).all() if element_ids else []

            element_list = "\n".join(
                f"- {e.name} ({e.type}, {e.layer})" for e in elements
            ) or "No elements in workspace."

            vp_key = f"viewpoint_{viewpoint_type}"
            vp_data = {"element_count": len(elements), "viewpoint_type": viewpoint_type}
            self.kernel.set_artifact_state(workspace_id, vp_key, ArtifactState.DRAFT.value, vp_data)
            self.kernel.set_artifact_state(workspace_id, vp_key, ArtifactState.CONFIRMED.value)
            self.kernel.set_artifact_state(workspace_id, vp_key, ArtifactState.PERSISTED.value)

            return {
                "success": True,
                "viewpoint_type": viewpoint_type,
                "element_count": len(elements),
                "elements": element_list,
                "message": (
                    f"## ArchiMate {viewpoint_type.replace('_', ' ').title()} Viewpoint\n\n"
                    f"**Elements ({len(elements)}):**\n{element_list}\n\n"
                    f"*Viewpoint persisted. Open Composer for precise layout editing.*"
                ),
            }
        except Exception as e:
            logger.error("Failed to generate viewpoint: %s", e)
            return {"success": False, "error": str(e)}


# ============================================================================
# AIC-316: SAD / GOVERNANCE PACK GENERATION
# ============================================================================

class SADGovernanceGenerator:
    """
    Generate structured SAD sections and governance packs from workspace state.
    """

    def __init__(self, kernel: WorkbenchKernel, user_id: Optional[int] = None):
        self.kernel = kernel
        self.user_id = user_id

    def generate_sad_sections(
        self, workspace_id: int, solution_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Generate SAD sections from the canonical workspace/model state."""
        ws = self.kernel.load_workspace(workspace_id)
        if not ws:
            return {"success": False, "error": "Workspace not found"}

        artifacts = ws.get("artifacts", {})
        sections = []

        # Generate sections from available artifacts
        if artifacts.get("scope") or artifacts.get("brief"):
            sections.append({
                "section": "1. Architecture Vision",
                "state": ArtifactState.DRAFT.value,
                "content": "Scope, stakeholders, drivers, goals, and constraints captured during workbench session.",
            })

        if artifacts.get("target_state"):
            sections.append({
                "section": "2. Target Architecture",
                "state": ArtifactState.DRAFT.value,
                "content": "Target-state business, application, and technology architecture.",
            })

        if artifacts.get("options"):
            sections.append({
                "section": "3. Options Analysis",
                "state": ArtifactState.DRAFT.value,
                "content": "Solution options with pros/cons and MCDA scoring.",
            })

        if artifacts.get("recommendation"):
            sections.append({
                "section": "4. Recommendation",
                "state": ArtifactState.DRAFT.value,
                "content": "Recommended option with rationale, risks, and assumptions.",
            })

        if artifacts.get("roadmap"):
            sections.append({
                "section": "5. Implementation Roadmap",
                "state": ArtifactState.DRAFT.value,
                "content": "Work packages, plateaus, and delivery timeline.",
            })

        self.kernel.set_artifact_state(
            workspace_id, "sad_document",
            ArtifactState.DRAFT.value,
            {"section_count": len(sections)},
        )

        section_summary = "\n".join(
            f"- **{s['section']}** — {s['state']}: {s['content']}"
            for s in sections
        ) or "No SAD sections generated. Complete more workflow steps first."

        return {
            "success": True,
            "sections": sections,
            "message": (
                f"## Solution Architecture Document (Draft)\n\n"
                f"**Workspace:** {ws.get('name', 'Unknown')}\n\n"
                f"### Generated Sections\n{section_summary}\n\n"
                f"*All sections are in **draft** state. Say 'confirm SAD' to advance to confirmed.*"
            ),
        }

    def generate_governance_pack(
        self, workspace_id: int, solution_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Generate an ARB-ready governance pack from workspace state."""
        ws = self.kernel.load_workspace(workspace_id)
        if not ws:
            return {"success": False, "error": "Workspace not found"}

        artifacts = ws.get("artifacts", {})
        evidence = ws.get("evidence", [])

        pack_items = []

        # Decision records
        if artifacts.get("recommendation"):
            pack_items.append("Architecture Decision Record (ADR) — from recommendation")

        # Risk assessment
        pack_items.append("Risk Assessment — from identified risks")

        # Compliance check
        pack_items.append("Compliance Checklist — architecture principles adherence")

        # Evidence summary
        evidence_summary = "\n".join(
            f"  - {e.get('type', 'unknown')}: {e.get('description', '')}"
            for e in evidence[:10]
        ) or "  No evidence recorded yet."

        self.kernel.set_artifact_state(
            workspace_id, "governance_pack",
            ArtifactState.DRAFT.value,
            {"item_count": len(pack_items)},
        )

        items_list = "\n".join(f"- {item}" for item in pack_items)

        return {
            "success": True,
            "items": pack_items,
            "message": (
                f"## ARB Governance Pack (Draft)\n\n"
                f"**Workspace:** {ws.get('name', 'Unknown')}\n\n"
                f"### Pack Contents\n{items_list}\n\n"
                f"### Evidence Trail\n{evidence_summary}\n\n"
                f"*Pack is in **draft** state. Say 'confirm governance pack' to advance. "
                f"Say 'submit to ARB' when ready for review.*"
            ),
        }

    def generate_decision_record(
        self,
        workspace_id: int,
        title: str,
        chosen_option: str,
        rationale: str,
        alternatives: List[str] = None,
    ) -> Dict[str, Any]:
        """Create and persist an architecture decision record."""
        try:
            from app.models.architecture_decision import ArchitectureDecision
            from app.models.solution_architect_models import SolutionAnalysisSession

            # Not .query.get(): an identity-map hit on a warm session returns
            # the cached row with no SQL, bypassing the tenant filter.
            session = SolutionAnalysisSession.query.filter_by(id=workspace_id).first()
            org_id = session.organization_id if session else None

            alternatives_text = ", ".join(alternatives or [])

            adr = ArchitectureDecision(
                decision_id=ArchitectureDecision.next_decision_id(),
                title=title[:200],
                status="proposed",
                context=f"Decision context for: {title}",
                decision=chosen_option,
                rationale=rationale,
                consequences=f"Alternatives considered: {alternatives_text}" if alternatives_text else "No alternatives documented",
                alternatives=alternatives_text or None,
                organization_id=org_id,
            )
            db.session.add(adr)
            db.session.flush()

            # Link to workspace session via SolutionADRLink
            try:
                from app.models.solution_architect_models import SolutionADRLink
                link = SolutionADRLink(
                    session_id=workspace_id,
                    adr_id=adr.id,
                    relationship_type="informs",
                    linked_by_id=self.user_id,
                )
                db.session.add(link)
            except Exception as link_err:
                logger.debug("SolutionADRLink creation skipped: %s", link_err)

            db.session.commit()

            # Track as artifact with proper state transitions
            art_key = f"decision_{adr.id}"
            self.kernel.set_artifact_state(
                workspace_id, art_key,
                ArtifactState.DRAFT.value,
                {"adr_id": adr.id, "title": title},
            )
            self.kernel.set_artifact_state(workspace_id, art_key, ArtifactState.CONFIRMED.value)
            self.kernel.set_artifact_state(workspace_id, art_key, ArtifactState.PERSISTED.value)

            self.kernel.add_evidence(
                workspace_id, "decision_record",
                f"ADR created: {title}", f"adr_id={adr.id}",
            )

            return {
                "success": True,
                "adr_id": adr.id,
                "message": f"Architecture Decision Record created: **{title}** (ID: {adr.id})",
            }
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to create ADR: %s", e)
            return {"success": False, "error": str(e)}


# ============================================================================
# AIC-317: DELIVERY PLANNING & ROADMAP GENERATION
# ============================================================================

class DeliveryPlanningService:
    """
    Generate delivery planning artifacts from workspace state using
    canonical roadmap/workflow services.
    """

    def __init__(self, kernel: WorkbenchKernel, user_id: Optional[int] = None):
        self.kernel = kernel
        self.user_id = user_id

    def generate_delivery_roadmap(self, workspace_id: int) -> Dict[str, Any]:
        """Generate a full delivery roadmap including work packages and plateaus.

        Combines generate_work_packages and generate_plateaus into a single
        operation. Tries StructuredDeliverableService first, falls back to
        artifact-based generation.

        Returns:
            Dict with success, work_packages count, plateaus count, and
            critical_path list.
        """
        ws = self.kernel.load_workspace(workspace_id)
        if not ws:
            return {"success": False, "error": "Workspace not found"}

        solution_id = ws.get("solution_id")

        # Try StructuredDeliverableService for AI-generated roadmap
        try:
            from app.modules.ai_chat.services.structured_deliverable_service import (
                StructuredDeliverableService,
            )
            svc = StructuredDeliverableService(user_id=self.user_id)
            roadmap_result = svc.generate_roadmap()
            if roadmap_result.get("success"):
                logger.info("AIC-317: StructuredDeliverableService roadmap generated")
            else:
                db.session.rollback()
                logger.debug("AIC-317: StructuredDeliverableService returned error: %s", roadmap_result.get("error"))
        except Exception as sds_err:
            db.session.rollback()
            logger.debug("AIC-317: StructuredDeliverableService unavailable: %s", sds_err)

        # Generate work packages
        wp_result = self.generate_work_packages(workspace_id, solution_id=solution_id)
        if not wp_result.get("success"):
            return wp_result

        # Generate plateaus
        plat_result = self.generate_plateaus(workspace_id, solution_id=solution_id)
        plateau_count = plat_result.get("plateau_count", 0)

        # For brownfield workspaces, query dependency constraints
        workspace_type = ws.get("workspace_type", "greenfield")
        dependency_constraints = []
        if workspace_type == "brownfield" and solution_id:
            try:
                from app.models.application_rationalization import ApplicationDependency
                deps = ApplicationDependency.query.filter(
                    ApplicationDependency.dependency_strength.in_(["critical", "high"])
                ).limit(50).all()
                for dep in deps:
                    dependency_constraints.append(dep.source_app_id)
                logger.info(
                    "AIC-317: Loaded %d dependency constraints for brownfield workspace",
                    len(dependency_constraints),
                )
            except Exception as dep_err:
                db.session.rollback()
                logger.debug("AIC-317: Dependency query skipped: %s", dep_err)

        # Track as delivery_roadmap artifact
        self.kernel.set_artifact_state(
            workspace_id, "delivery_roadmap",
            ArtifactState.DRAFT.value,
            {
                "work_packages": len(wp_result.get("work_packages", [])),
                "plateaus": plateau_count,
            },
        )
        self.kernel.save_checkpoint(workspace_id, "delivery_roadmap", "Full delivery roadmap generated")

        critical_path = [wp["name"] for wp in wp_result.get("work_packages", [])]
        return {
            "success": True,
            "work_packages": len(wp_result.get("work_packages", [])),
            "plateaus": plateau_count,
            "critical_path": critical_path,
        }

    def generate_work_packages(
        self, workspace_id: int, solution_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Generate work packages from workspace artifacts."""
        ws = self.kernel.load_workspace(workspace_id)
        if not ws:
            return {"success": False, "error": "Workspace not found"}

        artifacts = ws.get("artifacts", {})
        created_packages = []

        # Try StructuredDeliverableService for AI-enriched work packages
        try:
            from app.modules.ai_chat.services.structured_deliverable_service import (
                StructuredDeliverableService,
            )
            svc = StructuredDeliverableService(user_id=self.user_id)
            roadmap_result = svc.generate_roadmap()
            if roadmap_result.get("success"):
                logger.info("AIC-317: StructuredDeliverableService consulted for work packages")
            else:
                # Service returned error without raising — ensure transaction is clean
                db.session.rollback()
                logger.debug("AIC-317: StructuredDeliverableService returned error: %s", roadmap_result.get("error"))
        except Exception as sds_err:
            db.session.rollback()
            logger.debug("AIC-317: StructuredDeliverableService unavailable: %s", sds_err)

        # For brownfield workspaces, query dependency constraints
        workspace_type = ws.get("workspace_type", "greenfield")
        if workspace_type == "brownfield":
            try:
                from app.models.application_rationalization import ApplicationDependency
                deps = ApplicationDependency.query.filter(
                    ApplicationDependency.dependency_strength.in_(["critical", "high"])
                ).limit(50).all()
                if deps:
                    logger.info(
                        "AIC-317: %d high-criticality dependencies found for brownfield planning",
                        len(deps),
                    )
            except Exception as dep_err:
                db.session.rollback()
                logger.debug("AIC-317: Dependency query skipped: %s", dep_err)

        try:
            from app.models.implementation_migration import WorkPackage

            # Generate work packages based on workflow artifacts
            wp_definitions = []

            if artifacts.get("scope"):
                wp_definitions.append(("Architecture Definition", "Define target architecture based on scope analysis"))
            if artifacts.get("target_state"):
                wp_definitions.append(("Foundation Setup", "Establish infrastructure and platform foundation"))
            if artifacts.get("options") or artifacts.get("recommendation"):
                wp_definitions.append(("Solution Implementation", "Implement recommended solution option"))
            if artifacts.get("gap_analysis"):
                wp_definitions.append(("Gap Remediation", "Address identified gaps in capability and technology"))

            wp_definitions.append(("Integration & Testing", "End-to-end integration testing and validation"))
            wp_definitions.append(("Go-Live & Transition", "Production cutover and knowledge transfer"))

            for i, (name, summary) in enumerate(wp_definitions):
                wp = WorkPackage(
                    name=name[:255],
                    summary=summary[:512],
                    status="planned",
                    priority="medium",
                    sequence_order=i + 1,
                    owner_id=self.user_id,
                )
                db.session.add(wp)
                sync_archimate_element(wp)
                db.session.flush()
                created_packages.append({"id": wp.id, "name": name, "summary": summary})

            db.session.commit()

            self.kernel.set_artifact_state(
                workspace_id, "work_packages",
                ArtifactState.DRAFT.value,
                {"count": len(created_packages)},
            )

            self.kernel.save_checkpoint(
                workspace_id, "delivery_roadmap",
                "Work packages generated",
            )

            wp_list = "\n".join(
                f"- **WP-{p['id']}**: {p['name']} — {p['summary']}"
                for p in created_packages
            )

            return {
                "success": True,
                "work_packages": created_packages,
                "message": (
                    f"## Work Packages Generated\n\n"
                    f"{wp_list}\n\n"
                    f"*{len(created_packages)} work packages created and linked to workspace {workspace_id}.*"
                ),
            }
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to generate work packages: %s", e)
            return {"success": False, "error": str(e)}

    def generate_plateaus(
        self, workspace_id: int, solution_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Generate transition plateaus from workspace state."""
        if not solution_id:
            ws = self.kernel.load_workspace(workspace_id)
            solution_id = (ws or {}).get("solution_id") if ws else None

        if not solution_id:
            return {
                "success": True,
                "message": "No solution linked to workspace. Link a solution first to create plateaus.",
            }

        try:
            from app.models.solution_lifecycle_models import SolutionPlateau

            plateaus = [
                SolutionPlateau(
                    solution_id=solution_id,
                    name="Plateau 1: Foundation",
                    description="Infrastructure and platform foundation established",
                    order=1,
                ),
                SolutionPlateau(
                    solution_id=solution_id,
                    name="Plateau 2: Core Delivery",
                    description="Core solution components implemented and integrated",
                    order=2,
                ),
                SolutionPlateau(
                    solution_id=solution_id,
                    name="Plateau 3: Full Operation",
                    description="Complete solution operational with all integrations",
                    order=3,
                ),
            ]

            for p in plateaus:
                db.session.add(p)
            db.session.commit()

            self.kernel.set_artifact_state(
                workspace_id, "plateaus",
                ArtifactState.DRAFT.value,
                {"count": len(plateaus), "solution_id": solution_id},
            )

            self.kernel.save_checkpoint(
                workspace_id, "plateaus",
                "Transition plateaus generated",
            )

            return {
                "success": True,
                "plateau_count": len(plateaus),
                "message": (
                    f"## Transition Plateaus Created\n\n"
                    f"- **Plateau 1: Foundation** — infrastructure baseline\n"
                    f"- **Plateau 2: Core Delivery** — solution implementation\n"
                    f"- **Plateau 3: Full Operation** — production cutover\n\n"
                    f"*3 plateaus linked to solution {solution_id}.*"
                ),
            }
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to generate plateaus: %s", e)
            return {"success": False, "error": str(e)}

    def generate_planner_summary(
        self, workspace_id: int, requested_model: str = None
    ) -> Dict[str, Any]:
        """Generate a planner-facing summary with milestones, blockers, assumptions, and critical path."""
        ws = self.kernel.load_workspace(workspace_id)
        if not ws:
            return {"success": False, "error": "Workspace not found"}

        artifacts = ws.get("artifacts", {})

        wp_count = artifacts.get("work_packages", {}).get("data", {}).get("count", 0)
        plateau_count = artifacts.get("plateaus", {}).get("data", {}).get("count", 0)

        self.kernel.set_artifact_state(
            workspace_id, "planner_summary",
            ArtifactState.DRAFT.value,
            {"wp_count": wp_count, "plateau_count": plateau_count},
        )

        self.kernel.save_checkpoint(
            workspace_id, "planner_summary",
            "Planner summary generated",
        )

        return {
            "success": True,
            "message": (
                f"## Delivery Planning Summary\n\n"
                f"**Workspace:** {ws.get('name', 'Unknown')}\n\n"
                f"### Milestones\n"
                f"- {wp_count} work packages defined\n"
                f"- {plateau_count} transition plateaus planned\n\n"
                f"### Assumptions\n"
                f"- Resource availability as planned\n"
                f"- No major scope changes during implementation\n"
                f"- Vendor commitments honoured\n\n"
                f"### Blockers\n"
                f"- No blockers identified (pending detailed planning)\n\n"
                f"### Critical Path\n"
                f"- Foundation -> Core Implementation -> Integration -> Go-Live\n\n"
                f"*This is an AI-assisted summary. Validate assumptions before committing to delivery dates.*"
            ),
        }


# ============================================================================
# AIC-318: EVIDENCE GATE
# ============================================================================

class EvidenceGate:
    """
    Evidence gate for chat-primary architecture workbench readiness.

    Validates that all golden journey evidence packs are present before
    certifying chat as the primary architecture surface.
    """

    REQUIRED_JOURNEYS = [
        "greenfield",
        "brownfield",
        "archimate",
        "governance",
        "planning",
    ]

    @staticmethod
    def check_readiness(workspace_id: int, kernel: WorkbenchKernel) -> Dict[str, Any]:
        """Check if all golden journeys have evidence packs."""
        ws = kernel.load_workspace(workspace_id)
        if not ws:
            return {"pass": False, "error": "Workspace not found"}

        artifacts = ws.get("artifacts", {})
        evidence = ws.get("evidence", [])

        results = {}
        all_pass = True

        # Check each required journey
        journey_artifact_map = {
            "greenfield": ["brief", "scope", "recommendation", "roadmap"],
            "brownfield": ["portfolio_context", "current_state", "gap_analysis", "migration_plan"],
            "archimate": [k for k in artifacts if k.startswith("element_") or k.startswith("viewpoint_")],
            "governance": ["governance_pack", "sad_document"],
            "planning": ["work_packages", "plateaus", "planner_summary"],
        }

        for journey in EvidenceGate.REQUIRED_JOURNEYS:
            required_artifacts = journey_artifact_map.get(journey, [])
            present = [a for a in required_artifacts if a in artifacts]
            missing = [a for a in required_artifacts if a not in artifacts]

            journey_pass = len(missing) == 0 and len(present) > 0
            results[journey] = {
                "pass": journey_pass,
                "present": present,
                "missing": missing,
            }
            if not journey_pass:
                all_pass = False

        return {
            "pass": all_pass,
            "journeys": results,
            "evidence_count": len(evidence),
            "artifact_count": len(artifacts),
        }

    @staticmethod
    def format_readiness_report(readiness: Dict) -> str:
        """Format a human-readable readiness report."""
        if readiness.get("error"):
            return f"Evidence gate error: {readiness['error']}"

        lines = ["## Chat-Primary Workbench Readiness Report\n"]

        for journey, result in readiness.get("journeys", {}).items():
            status = "PASS" if result["pass"] else "FAIL"
            icon = "+" if result["pass"] else "x"
            lines.append(f"- [{icon}] **{journey.title()}**: {status}")
            if result.get("missing"):
                lines.append(f"  Missing: {', '.join(result['missing'])}")

        overall = "PASS" if readiness["pass"] else "FAIL"
        lines.append(f"\n**Overall: {overall}**")
        lines.append(f"Artifacts: {readiness.get('artifact_count', 0)} | Evidence entries: {readiness.get('evidence_count', 0)}")

        if not readiness["pass"]:
            lines.append(
                "\n*Readiness certification blocked. Complete missing journeys before claiming chat-first readiness.*"
            )

        return "\n".join(lines)
