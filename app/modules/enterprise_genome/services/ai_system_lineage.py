"""Lineage queries for the AI Systems Register.

A system's provider is the provider string on its own stored profile, joined
to the organisation's provider register; its data entities are the DataObject
elements linked to it through ArchiMateRelationship. No new table.

Reuse
-----
* ``ai_system_profile.is_ai_system`` / ``_read_owner`` — the AI-system predicate
  and the one owner read.
* ``ai_systems_slice._iter_ai_system_elements`` — the organisation-scoped
  iteration over AI-system elements.
* ``ModelProvider`` (``app.models.model_provider``) — the one provider register
  and its ``is_allowed_for_org`` answer.
* ArchiMateRelationship (``app.models.models``) for data-entity links.
"""

from __future__ import annotations

import logging

from app import db
from app.modules.enterprise_genome.services.ai_system_profile import (
    AI_SYSTEM_MARKER,
    UNKNOWN,
    _read_owner,
    is_ai_system,
)
from app.modules.enterprise_genome.services.ai_systems_slice import (
    _iter_ai_system_elements,
)

logger = logging.getLogger(__name__)

# A data entity is a DataObject element.
DATA_ENTITY_TYPE = "DataObject"


def _profile_provider(element) -> tuple[str | None, str | None]:
    """The (provider, model_id) strings recorded in the AI system's stored
    profile (``custom_properties['ai_system']``), or ``(None, None)``-style
    blanks when nothing is recorded. Never invents a value."""
    props = getattr(element, "custom_properties", None) or {}
    ai = props.get(AI_SYSTEM_MARKER) if isinstance(props, dict) else None
    if not isinstance(ai, dict):
        return None, None
    provider = (ai.get("provider") or "").strip() or None
    model_id = (ai.get("model_id") or "").strip() or None
    if provider and provider.lower() == UNKNOWN:
        provider = None
    return provider, model_id


def _provider_register_rows(provider: str, organization_id: int) -> list:
    """``ModelProvider`` rows for *provider* visible to *organization_id*: the
    organisation's own rows plus the platform defaults (organisation NULL).
    Scoped explicitly; never another organisation's rows."""
    from sqlalchemy import func, or_

    from app.models.model_provider import ModelProvider

    return (
        ModelProvider.query.filter(
            func.lower(func.trim(ModelProvider.provider)) == provider.strip().lower(),
            or_(
                ModelProvider.organization_id == organization_id,
                ModelProvider.organization_id.is_(None),
            ),
        )
        .order_by(ModelProvider.id.asc())
        .all()
    )


def get_providers_for_system(element_id: int, organization_id: int) -> list[dict]:
    """The provider an AI system uses, from its stored profile.

    The provider is the string recorded on the system's own profile
    (``custom_properties['ai_system']['provider']``) — the same fact the
    register's Provider column shows — joined to the organisation's provider
    register (``ModelProvider``: the organisation's rows plus the platform
    defaults). No separate provider element or marker is needed.

    Returns a list of ``{name, model_id, registered, allowed}`` (at most one
    entry: a profile carries one provider), or ``[]`` when the system is not in
    *organization_id* or records no provider. ``registered`` is True when the
    provider register has a row for it; ``allowed`` is the register's own
    allow/block answer for the provider and model
    (``ModelProvider.is_allowed_for_org``).
    """
    from app.models.model_provider import ModelProvider
    from app.models.models import ArchiMateElement

    element = ArchiMateElement.query.filter_by(
        id=element_id, organization_id=organization_id
    ).first()
    if element is None or not is_ai_system(element):
        return []

    provider, model_id = _profile_provider(element)
    if provider is None:
        return []

    return [{
        "name": provider,
        "model_id": model_id,
        "registered": bool(_provider_register_rows(provider, organization_id)),
        "allowed": ModelProvider.is_allowed_for_org(
            provider, model_id, organization_id
        ),
    }]


def get_data_entities_for_system(element_id: int, organization_id: int) -> list[dict]:
    """Data entities linked to an AI system via ArchiMateRelationship.

    Returns a list of data-entity element dicts (``{id, name, type}``), or an
    empty list when none are linked. A data entity is a ``DataObject`` element.

    Scoped to *organization_id*.
    """
    from app.models.models import ArchiMateElement, ArchiMateRelationship

    rels = (
        ArchiMateRelationship.query.filter(
            (
                (ArchiMateRelationship.source_id == element_id)
                | (ArchiMateRelationship.target_id == element_id)
            ),
            ArchiMateRelationship.type.in_(
                ("serving", "association", "access", "flow")
            ),
        )
        .filter(ArchiMateRelationship.organization_id == organization_id)
        .all()
    )

    entities: list[dict] = []
    seen: set[int] = set()

    for rel in rels:
        peer_id = rel.target_id if rel.source_id == element_id else rel.source_id
        if peer_id in seen:
            continue
        seen.add(peer_id)

        peer = ArchiMateElement.query.filter_by(
            id=peer_id,
            organization_id=organization_id,
        ).first()
        if peer is None:
            continue
        if peer.type == DATA_ENTITY_TYPE:
            entities.append({
                "id": peer.id,
                "name": peer.name or "not recorded",
                "type": peer.type,
            })

    return entities


def get_systems_using_provider(
    provider: str,
    organization_id: int,
) -> list[dict]:
    """All of an organisation's AI systems whose profile names *provider*, with
    owner and data entities.

    An organisation-scoped query over the AI-system elements: a system is
    listed when its stored profile provider equals *provider* (case and
    surrounding space ignored). Nothing about a link or a marker decides it.

    Returns::
        [
            {
                "id": <element id>,
                "name": "...",
                "owner": {"user_id": ..., "user_name": ..., ...} | None,
                "data_entities": [{"id": ..., "name": ..., "type": ...}, ...],
            },
            ...
        ]

    Scoped to *organization_id*; another organisation's systems never appear.
    """
    wanted = (provider or "").strip().lower()
    if not wanted:
        return []

    systems: list[dict] = []
    for peer in _iter_ai_system_elements(organization_id, db.session):
        peer_provider, _ = _profile_provider(peer)
        if peer_provider is None or peer_provider.lower() != wanted:
            continue
        systems.append({
            "id": peer.id,
            "name": peer.name or "not recorded",
            "owner": _read_owner(peer),
            "data_entities": get_data_entities_for_system(peer.id, organization_id),
        })

    return systems
