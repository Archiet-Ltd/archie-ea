"""Shared access control for every inbound Jira webhook receiver.

Three receivers accept a Jira status-change event and move the matching card:
``POST /webhooks/jira`` (the dedicated, unauthenticated blueprint) and
``POST /jira-settings/webhook`` in both the v1 and v2 admin trees. They shared
two defects, which this module closes in one place so the three receivers call
the same code rather than each carrying its own copy:

1. They accepted the call when no webhook secret was configured. An unset secret
   means the integration is not set up, which must mean "reject", never "trust
   every caller". ``webhook_secret_configured`` answers whether a secret exists,
   and the receivers refuse (503) when it does not.

2. They resolved the card with an unscoped
   ``KanbanCard.query.filter_by(jira_issue_key=...)``. The dedicated receiver
   runs with no tenant context, so the organisation filter is a no-op, and the
   issue key is unique across the whole instance: a correctly signed call for one
   organisation could move another organisation's card.
   ``card_for_issue_in_org`` resolves the card only inside the organisation it is
   given, so a card belonging to a different organisation is never returned.

This is a single shared resolution path for the fix, not a fourth receiver: the
three receivers stay where they are (consolidating them is a separate decision).
"""

from __future__ import annotations

from typing import Optional

from app.models.adm_kanban import KanbanCard


def jira_connection_org_id() -> Optional[int]:
    """The organisation that owns the configured Jira connection, or None.

    The dedicated webhook receiver runs unauthenticated, so it cannot read an
    active organisation from the session. It resolves the owning organisation
    from the Jira connection itself the way the push and sync paths resolve the
    connection: the single ``external_systems`` row named ``jira``. A connection
    with no organisation recorded yields None, and the receiver then touches no
    card.
    """
    from app.models.models import ExternalSystem

    connection = ExternalSystem.query.filter_by(system_name="jira").first()
    if connection is None:
        return None
    return connection.organization_id


def webhook_secret_configured(secret: Optional[str]) -> bool:
    """True only when a non-empty webhook secret is configured.

    An empty or missing secret means the integration is not configured; the
    receiver must refuse rather than accept an unverified call.
    """
    return bool(secret)


def card_for_issue_in_org(issue_key: str, organization_id: Optional[int]) -> Optional[KanbanCard]:
    """Return the card for ``issue_key`` owned by ``organization_id``, else None.

    The organisation is resolved from the Jira connection the signed call belongs
    to. Passing it in explicitly scopes the lookup even in the dedicated
    receiver, which runs outside any request-scoped tenant context (where the
    ORM tenant filter is a no-op). When ``organization_id`` is None the
    integration cannot name an owning organisation, so nothing is returned and no
    card is touched.
    """
    if not issue_key or organization_id is None:
        return None
    return KanbanCard.query.filter_by(
        jira_issue_key=issue_key,
        organization_id=organization_id,
    ).first()
