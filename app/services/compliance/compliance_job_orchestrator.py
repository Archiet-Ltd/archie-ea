"""Compliance requirement generation orchestrator.

Coordinates capability gap analysis with compliance requirement generation
background tasks so the highest-risk capabilities receive coverage first.
"""

import logging
from datetime import UTC, datetime
from typing import Dict, List, Optional, Set, Tuple

from app import db
from app.models import BusinessCapability
from app.models.compliance_models import ComplianceControl
from app.services.capability_gap_service import CapabilityGapAnalysisService
from app.services.job_queue_service import QUEUE_AGENT, get_job_queue_service
from app.tasks.compliance_tasks import generate_requirements_for_control

logger = logging.getLogger(__name__)


class ComplianceJobOrchestrator:
    """High-level orchestrator for compliance requirement generation jobs."""

    def __init__(self):
        self.capability_service = CapabilityGapAnalysisService()

    def enqueue_priority_requirement_generation(
        self, user_id: int, architecture_id: Optional[int] = None, max_controls: int = 10
    ) -> Dict:
        """Queue requirement generation jobs for highest-priority capabilities."""
        priority_capabilities = self.capability_service.get_requirement_generation_priority(
            architecture_id=architecture_id, limit=max_controls * 2
        )

        control_candidates, capability_map = self._collect_control_candidates(
            priority_capabilities, max_controls
        )
        if not control_candidates:
            return {
                "queued_jobs": [],
                "controls_considered": 0,
                "message": "No eligible compliance controls found for the prioritized capabilities.",
            }

        job_queue = get_job_queue_service()
        queued_jobs: List[Dict] = []

        for control, capability_id in control_candidates:
            job = job_queue.enqueue(
                QUEUE_AGENT,
                generate_requirements_for_control,
                control_id=control.id,
                user_id=user_id,
                job_timeout="10m",
                description=f"Auto requirement generation for {control.control_id}",
            )

            queued_jobs.append(
                {
                    "job_id": job.id,
                    "control_id": control.id,
                    "control_code": control.control_id,
                    "framework": control.framework.code if control.framework else None,
                    "capability_id": capability_id,
                    "queued_at": datetime.now(UTC).isoformat(),
                }
            )

            logger.info(
                "Queued compliance requirement generation for control %s (capability_id=%s, job_id=%s)",
                control.control_id,
                capability_id,
                job.id,
            )

        db.session.commit()

        return {
            "queued_jobs": queued_jobs,
            "controls_considered": len(control_candidates),
            "capabilities_scored": len(priority_capabilities),
        }

    def _collect_control_candidates(
        self, priority_capabilities: List[Dict], max_controls: int
    ) -> Tuple[List[Tuple[ComplianceControl, int]], Dict[int, List[int]]]:
        """Return list of controls tied to prioritized capabilities."""
        selected: List[Tuple[ComplianceControl, int]] = []
        seen_control_ids: Set[int] = set()
        capability_to_controls: Dict[int, List[int]] = {}

        for ranked in priority_capabilities:
            if len(selected) >= max_controls:
                break

            capability = db.session.get(BusinessCapability, ranked["capability_id"])
            if not capability:
                continue

            controls = self._controls_for_capability(capability)
            if not controls:
                continue

            capability_to_controls[capability.id] = [control.id for control in controls]

            for control in controls:
                if control.id in seen_control_ids:
                    continue

                if control.requirements.count() > 0:
                    continue

                selected.append((control, capability.id))
                seen_control_ids.add(control.id)

                if len(selected) >= max_controls:
                    break

        return selected, capability_to_controls

    def _controls_for_capability(self, capability: BusinessCapability) -> List[ComplianceControl]:
        """Resolve controls mapped to a capability via compliance requirements."""
        control_ids: Set[int] = set()

        for requirement in capability.compliance_requirements:
            if requirement.control_id:
                control_ids.add(requirement.control_id)

        if not control_ids:
            return []

        return list(ComplianceControl.query.filter(ComplianceControl.id.in_(control_ids)))
