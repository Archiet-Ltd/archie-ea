"""The external half of the Operational lens: an adapter contract with no
implementation shipped yet.

Data classes, closed vocabulary:

  * Class **C** — the crosswalk reference an adapter joins through:
    ``ExternalRef`` (``source_system``, ``external_id``, ``element_id``,
    ``confidence``). An adapter resolves which external records belong to
    which picked element ONLY by looking up its ``ExternalRef`` rows —
    never by matching an element's name or any other free-text field
    against the external system, which is not a verifiable identity link
    (FR-11).
  * Class **D** — the master record itself, read-only and re-fetched on
    every call, never copied into a model table: ``IncidentRecord``,
    ``ChangeRecord``, ``TelemetrySample``. Each carries the ``source_system``
    that produced it and a ``fetched_at`` timestamp, and reports
    ``truth_class="authoritative_fact"`` — it is the external system's own
    record of itself, not something this product derived.

``registered_adapter(organization_id)`` returns ``None`` today: no incident,
change or telemetry reader exists anywhere in this tree yet, and this
module ships the contract precisely so the first one is a builder task
against a fixed shape rather than a design step. No class in this module
(or anywhere else) implements ``OperationalSourceAdapter`` — a "null"
adapter that returned empty lists would make "nothing happened" and "no
system is connected" indistinguishable, exactly the collision the
fabrication rule exists to prevent. When a real adapter is
built, it is constructed per tenant from that tenant's own
``OrgConnectorConfig`` row and an allowlisted connector id (ADR-008); no
adapter may be registered that returns empty lists in place of ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Protocol


@dataclass(frozen=True)
class ExternalRef:
    """Class C: the Release 3 crosswalk row. The only link between a picked
    ArchiMate element and any record in an external system — an adapter
    joins through this, never by name."""

    source_system: str
    external_id: str
    element_id: int
    confidence: float


@dataclass(frozen=True)
class IncidentRecord:
    """Class D: one incident, as its system of record holds it."""

    uri: str
    external_id: str
    status: str
    severity: str
    opened_at: datetime
    closed_at: Optional[datetime]
    source_system: str
    fetched_at: datetime
    truth_class: str = "authoritative_fact"


@dataclass(frozen=True)
class ChangeRecord:
    """Class D: one change record, as its system of record holds it."""

    uri: str
    external_id: str
    state: str
    planned_start: Optional[datetime]
    planned_end: Optional[datetime]
    risk: Optional[str]
    source_system: str
    fetched_at: datetime
    truth_class: str = "authoritative_fact"


@dataclass(frozen=True)
class TelemetrySample:
    """Class D: one telemetry reading, as its system of record holds it."""

    metric: str
    value: float
    unit: str
    observed_at: datetime
    uri: str
    source_system: str
    fetched_at: datetime
    truth_class: str = "authoritative_fact"


class OperationalSourceAdapter(Protocol):
    """The contract a per-tenant external source connects through (SDD §4).

    No class in this tree implements this Protocol today (task constraint);
    the first implementation is a builder task against this fixed shape.
    """

    source_system: str

    def incidents_for(
        self, refs: List[ExternalRef], since: datetime
    ) -> List[IncidentRecord]: ...

    def changes_for(
        self, refs: List[ExternalRef], since: datetime
    ) -> List[ChangeRecord]: ...

    def telemetry_for(
        self, refs: List[ExternalRef], window: timedelta
    ) -> List[TelemetrySample]: ...


def registered_adapter(organization_id: int) -> Optional[OperationalSourceAdapter]:
    """The tenant's connected operational-source adapter, or ``None``.

    Returns ``None`` unconditionally: no adapter is constructed by this
    task. When a real source is named, this function is the one place that
    changes — it will construct an adapter per tenant from that tenant's
    own ``OrgConnectorConfig`` row and an allowlisted connector id
    (ADR-008), never a shared or cross-tenant instance. No adapter that
    returns empty lists in place of ``None`` may ever be registered here:
    that would collide "nothing happened" with "no source is connected",
    which is exactly the distinction the ``feed_not_connected`` reason code
    exists to preserve.
    """
    return None


__all__ = [
    "ExternalRef",
    "IncidentRecord",
    "ChangeRecord",
    "TelemetrySample",
    "OperationalSourceAdapter",
    "registered_adapter",
]
