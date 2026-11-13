"""Settle, once, every proposal that was already waiting for a person.

Before proposals settled on the server, a solution blueprint, a read of GitHub or a connection could leave
proposals pending, and some of them expired. This command runs each one through the same loop everything else
now uses (``app.modules.ai_chat.services.change_review``): the deterministic checks, a reviewer other than the
drafter, then applied, linked to the element the workspace already has, declined with reasons, or, when past its
time, retired with "expired on <date>; not applied".

    flask --app manage settle-waiting-proposals --dry-run
    flask --app manage settle-waiting-proposals
    flask --app manage settle-waiting-proposals --organization-id 7

``--dry-run`` writes nothing and calls no model: it runs only the deterministic checks and says how many items
would be linked, retired and declined, and how many pass the checks and go to the reviewer. It is a command
for the deploy to run by hand, after a backup; it is not a migration and it is not part of the schema deploy.
"""

from __future__ import annotations

import click
from flask.cli import with_appcontext


def _waiting_organisations(only: int | None):
    from app import db
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.modules.ai_chat.services.change_review import PROPOSAL_SOURCES

    query = db.session.query(AIChatCRUDApproval.organization_id).filter(
        AIChatCRUDApproval.status == ApprovalStatus.PENDING,
        AIChatCRUDApproval.source_table.in_(PROPOSAL_SOURCES),
        AIChatCRUDApproval.organization_id.isnot(None),
    )
    if only is not None:
        query = query.filter(AIChatCRUDApproval.organization_id == only)
    return sorted({row[0] for row in query.distinct().all()})


def plan_organisation(organization_id: int) -> dict:
    """What settling would do with each waiting proposal of one organisation, from the deterministic checks
    alone: nothing is written and no model is called. Runs with the organisation already active."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.modules.ai_chat.services import change_review
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    plan = {"link": 0, "apply": 0, "decline": 0, "retire": 0, "to_reviewer": 0, "total": 0, "reasons": {}}
    actor = change_review.system_actor(organization_id)
    rows = AIChatCRUDApproval.query.filter(
        AIChatCRUDApproval.organization_id == organization_id,
        AIChatCRUDApproval.status == ApprovalStatus.PENDING,
        AIChatCRUDApproval.source_table.in_(change_review.PROPOSAL_SOURCES),
    ).order_by(AIChatCRUDApproval.id).all()
    seen_names: set = set()
    for row in rows:
        plan["total"] += 1
        if actor is None:
            plan["decline"] += 1
            plan["reasons"][change_review.NO_SYSTEM_ACTOR_REASON] = plan["reasons"].get(
                change_review.NO_SYSTEM_ACTOR_REASON, 0) + 1
            continue
        checks, target = change_review.run_checks(AIChatApprovalService(actor.id), row, actor)
        failed = next((c for c in checks if not c.passed), None)
        if failed is not None:
            kind = "retire" if failed.name == change_review.EXPIRED_CHECK_NAME else "decline"
            plan[kind] += 1
            key = "expired" if kind == "retire" else failed.reason
            plan["reasons"][key] = plan["reasons"].get(key, 0) + 1
            continue
        identity = (target.label or "").casefold(), (change_review._payload_of(row) or {}).get("archimate_type")
        if target.link or (identity[0] and identity in seen_names):
            plan["link"] += 1  # an exact repeat of the model, or of an item earlier in this same batch
            continue
        seen_names.add(identity)
        plan["apply"] += 1
        if target.near:
            plan["to_reviewer"] += 1
    return plan


@click.command("settle-waiting-proposals")
@click.option("--dry-run", is_flag=True, help="Say what would happen; write nothing and call no model.")
@click.option("--organization-id", type=int, default=None, help="Only this organisation.")
@with_appcontext
def settle_waiting_proposals(dry_run, organization_id):
    from app.jobs.tenant_safe_job import platform_scope, tenant_scope
    from app.modules.ai_chat.services import change_review

    with platform_scope("settle waiting proposals"):
        organisations = _waiting_organisations(organization_id)
    if not organisations:
        click.echo("No proposal is waiting.")
        return
    totals: dict = {}
    for org_id in organisations:
        with tenant_scope(org_id):
            if dry_run:
                plan = plan_organisation(org_id)
                click.echo(
                    "organisation %d: %d waiting; would link %d, apply %d (%d of them go to the reviewer for a near "
                    "match), decline %d, retire %d" % (org_id, plan["total"], plan["link"], plan["apply"],
                                                       plan["to_reviewer"], plan["decline"], plan["retire"]))
                for reason, count in sorted(plan["reasons"].items()):
                    click.echo("    %d: %s" % (count, reason))
                for key in ("link", "apply", "decline", "retire", "total"):
                    totals[key] = totals.get(key, 0) + plan[key]
            else:
                budget = change_review.ReviewBudget(change_review.CALLS_PER_BATCH * 4)
                counts = change_review.settle_proposals(org_id, budget=budget)
                click.echo("organisation %d: %s; reviewer calls %d" % (
                    org_id, ", ".join("%d %s" % (v, k.replace("_", " ")) for k, v in counts.items()), budget.used))
                for key, value in counts.items():
                    totals[key] = totals.get(key, 0) + value
    click.echo(("dry run, all organisations: " if dry_run else "all organisations: ") + ", ".join(
        "%s %d" % (k, v) for k, v in totals.items()))


def init_app(app):
    app.cli.add_command(settle_waiting_proposals)
