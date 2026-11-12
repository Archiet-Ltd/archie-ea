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
        # The proposal is checked, reviewed and settled by the job worker as soon as the caller saves it;
        # nobody approves it.
        from app.modules.ai_chat.services.change_review import queue_settling

        queue_settling(proposal.organization_id)
        db.session.flush()

    return proposal


def execute_blueprint_proposal(approval) -> dict:
    """Apply one settled blueprint proposal of an organisation: a new element for the solution, or, when the
    workspace already has that element (the same name and type, or the one the review chose), a link to it.

    Called only by ``AIChatApprovalService.approve_and_execute``. Nothing is added twice: a proposal whose
    element exists joins the solution to it. Returns ``linked`` and the element's id and name."""
    import json

    from sqlalchemy import func

    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_models import SolutionArchiMateElement

    org_id = approval.organization_id
    try:
        payload = json.loads(approval.operation_payload or "{}")
    except (TypeError, ValueError):
        return {"success": False, "error": "Invalid blueprint proposal payload"}
    proposal = SolutionBlueprintProposal.query.filter_by(
        id=payload.get("solution_blueprint_proposal_id"), organization_id=org_id
    ).first()
    if proposal is None:
        return {"success": False, "error": "The blueprint proposal is no longer in this workspace."}

    chosen = None
    if payload.get("link_to_element_id") is not None:
        chosen = ArchiMateElement.query.filter_by(id=payload["link_to_element_id"], organization_id=org_id).first()
        if chosen is None:
            return {"success": False, "error": "The element it was to be linked to is not in this workspace."}

    if chosen is None:
        from app.modules.architecture_assistant.document_ingestion import DocumentIngestionService

        newest = db.session.query(func.max(ArchiMateElement.id)).filter(
            ArchiMateElement.organization_id == org_id).scalar() or 0
        result = DocumentIngestionService().accept_proposal(proposal.id, proposal.solution_id)
        if result.get("error"):
            return {"success": False, "error": str(result["error"])}
        element_id = result.get("element_id")
        element = ArchiMateElement.query.filter_by(id=element_id, organization_id=org_id).first() if element_id else None
        linked = element is not None and element.id <= newest
        created = []
        if element is not None and not linked:
            # What the journey's own inference chain derived for it is joined to the same solution and is
            # new too; undoing the item removes all of it.
            derived = SolutionArchiMateElement.query.filter(
                SolutionArchiMateElement.solution_id == proposal.solution_id,
                SolutionArchiMateElement.element_id > newest,
            ).all()
            created = sorted({element.id} | {row.element_id for row in derived})
        return {"success": True, "linked": linked, "element_id": element_id, "created_element_ids": created,
                "element_name": element.name if element else proposal.name,
                "message": ("Linked to the existing %s." % element.name) if linked and element
                else "Added %s to the model." % proposal.name}

    if not SolutionArchiMateElement.query.filter_by(solution_id=proposal.solution_id, element_id=chosen.id).first():
        # reuse-ok: the junction row that joins a solution to an element, not an element
        db.session.add(SolutionArchiMateElement(
            solution_id=proposal.solution_id, element_id=chosen.id, layer_type=chosen.type,
            element_table="archimate_elements", element_name=chosen.name, element_role="document_ingestion",
            is_new_element=False,
        ))
    proposal.status = "accepted"
    proposal.existing_element_id = chosen.id
    proposal.promoted_element_id = chosen.id
    db.session.flush()
    return {"success": True, "linked": True, "element_id": chosen.id, "element_name": chosen.name,
            "message": "Linked to the existing %s." % chosen.name}
