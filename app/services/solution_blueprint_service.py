"""Writer for SolutionBlueprintProposal (consolidation).

Lives in the services layer, not app/models/, so this module's dependency on
app/modules/ai_chat/services/ai_chat_approval_service.py keeps the usual
model -> service direction instead of inverting it.
"""

from app import db
from app.models.solution_blueprint_proposal import SolutionBlueprintProposal


def create_solution_blueprint_proposal(**kwargs) -> "SolutionBlueprintProposal":
    """Consolidation: the one place every pending SolutionBlueprintProposal is created.

    Same keyword arguments as the model's constructor. Adds the proposal
    (unchanged — it stays the system of record for its own ACM-specific
    fields and every existing reader) and pairs it with an
    ai_chat_crud_approvals row so it also surfaces in the one organisation-
    wide approval inbox. Flushes (not commit — callers control their own
    transaction boundary, as before this change); the caller must still
    call db.session.commit() itself.

    Not for a proposal created already accepted (`status="promoted"` or
    `is_baseline=True`, e.g. journey_orchestrator.py's baseline-template and
    regenerated-element paths): those construct SolutionBlueprintProposal
    directly, since there is nothing pending to route through an approval.
    """
    proposal = SolutionBlueprintProposal(**kwargs)
    db.session.add(proposal)
    db.session.flush()

    if proposal.organization_id is not None:
        from app.modules.ai_chat.services.ai_chat_approval_service import (
            create_approval_record,
        )

        approval = create_approval_record(
            organization_id=proposal.organization_id,
            operation_type="propose",
            entity_type="solution_blueprint_element",
            entity_id=proposal.id,
            summary=f"Blueprint proposal: {proposal.name} ({proposal.archimate_type})",
            operation_payload={
                "solution_blueprint_proposal_id": proposal.id,
                "solution_id": proposal.solution_id,
                "archimate_type": proposal.archimate_type,
                "name": proposal.name,
                "acm_domain": proposal.acm_domain,
                "source": proposal.source,
            },
            source_table="solution_blueprint_proposals",
            source_id=proposal.id,
        )
        # Mark superseded immediately (consolidation pattern step 5), not only
        # by the backfill command: without this, the backfill's idempotency
        # check (`WHERE retired_into_id IS NULL`) would re-copy this row and
        # create a second approval for it the next time it runs.
        proposal.retired_into_id = approval.id
        db.session.flush()

    return proposal
