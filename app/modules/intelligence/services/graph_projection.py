"""One per-organisation graph projection: compact adjacency over the model.

The relational store (``archimate_elements``, ``archimate_relationships`` and
``entity_history``) stays the authority. This module holds a rebuildable,
in-memory copy of it for one organisation so a bounded traversal does not pay
one SQL round trip per hop. Nothing here is written to the model; the only
table it owns is ``graph_projection_snapshots``, a compressed copy of the
projection so a fresh process loads it instead of rebuilding.

What it carries, per organisation:

* nodes: every ``archimate_elements`` row (every domain record is one element
  node, R1-B18), with its type and layer;
* edges: every ``archimate_relationships`` row ("explicit") and the
  column-stored element parent link ("column");
* relationship versions: every ``entity_history`` version of a relationship,
  as an interval edge, so the model as of a date can be traversed
  (``traverse(as_of=...)``) and agrees with ``HistoryService.as_of``.

How it stays current: the event log (``app/services/event_log_service.py``) is
the only change feed. An element or relationship event only says which row
changed, so applying it re-reads that row from the relational store by id and
organisation and replaces its node or edge (or removes it when the row is
gone). Applying the same event twice, or an event out of order, therefore
converges to the same state, and ``rebuild == incrementally maintained`` holds
by construction (``fingerprint``). A write that bypasses the ORM (raw SQL)
emits no event; ``refresh_organisation(verify=True)`` rebuilds and reports the
drift for that case.

This is the projection only. ``query_service.py`` still owns the canonical
impact walk; moving that walk onto this projection is proposed to its owner
with the comparison test in ``tests/test_graph_projection.py``, not done here.

Organisation scoping: every function takes the organisation id explicitly and
every read names it. An unknown organisation (``None``) reads nothing and
writes nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import zlib
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.extensions import db
from app.modules.intelligence.services.reason_codes import validate_reason_code

logger = logging.getLogger(__name__)

SNAPSHOT_FORMAT = 1

KIND_EXPLICIT = "explicit"
KIND_COLUMN = "column"
ALL_KINDS = (KIND_EXPLICIT, KIND_COLUMN)

# The one relationship stored as a column on an element row rather than as a row
# of archimate_relationships is ``archimate_elements.parent_id``. Its edge runs
# from the parent to the row that carries the column (the child) and has the
# negative key ``-child_id`` so it can never collide with a relationship id.
PARENT_EDGE_TYPE = "parent"

DIRECTIONS = ("downstream", "upstream", "both")
MAX_HOPS_CAP = 10
DEFAULT_NODE_BUDGET = 10_000
MAX_CACHED_ORGANISATIONS = 16
EVENT_BATCH = 1000
ID_CHUNK = 5000

ENTITY_ELEMENT = "archimate_element"
ENTITY_RELATIONSHIP = "archimate_relationship"
HISTORY_RELATIONSHIPS = "archimate_relationships"
HISTORY_ELEMENTS = "archimate_elements"

NO_TENANT_CONTEXT_REASON = validate_reason_code("no_tenant_context")
ELEMENT_NOT_FOUND_REASON = validate_reason_code("element_not_found")
NO_DATA_RECORDED_REASON = validate_reason_code("no_data_recorded")


def column_edge_key(child_id: int) -> int:
    return -int(child_id)


@dataclass(frozen=True)
class TraversalRow:
    element_id: int
    hop: int
    via_edge: int
    via_type: str
    via_kind: str
    from_element_id: int
    type: str
    layer: str


@dataclass
class TraversalResult:
    organization_id: Optional[int]
    root_id: Optional[int]
    max_hops: int
    direction: str
    as_of: Optional[datetime]
    rows: List[TraversalRow] = field(default_factory=list)
    by_layer: Dict[str, int] = field(default_factory=dict)
    by_type: Dict[str, int] = field(default_factory=dict)
    truncated: bool = False
    reason: Optional[str] = None  # closed reason code when there is nothing to show

    def depth_by_element(self) -> Dict[int, int]:
        return {row.element_id: row.hop for row in self.rows}


class GraphProjection:
    """Adjacency for one organisation. Mutators and readers share one lock."""

    def __init__(self, organization_id: int):
        self.organization_id = int(organization_id)
        self.last_ordinal = 0
        self.built_at: Optional[datetime] = None
        self.verified_at: Optional[datetime] = None
        self._lock = threading.RLock()
        self._strings: List[str] = []
        self._string_index: Dict[str, int] = {}
        # element id -> (type code, layer code)
        self._nodes: Dict[int, Tuple[int, int]] = {}
        # edge key -> (source, target, type code, kind code)
        self._edges: Dict[int, Tuple[int, int, int, int]] = {}
        self._out: Dict[int, List[int]] = {}
        self._in: Dict[int, List[int]] = {}
        # relationship id -> [(valid_from, valid_to, source, target, type code)]
        self._versions: Dict[int, List[Tuple[datetime, Optional[datetime], int, int, int]]] = {}
        self._versions_out: Dict[int, set] = {}
        self._versions_in: Dict[int, set] = {}
        # Element ids that have history but are no longer elements: an as-of
        # walk may still reach them, a current walk may not.
        self._retired: set = set()

    # ------------------------------------------------------------------ #
    # Interning
    # ------------------------------------------------------------------ #
    def _code(self, value: Optional[str]) -> int:
        text = value or ""
        code = self._string_index.get(text)
        if code is None:
            code = len(self._strings)
            self._strings.append(text)
            self._string_index[text] = code
        return code

    def _kind_code(self, kind: str) -> int:
        return ALL_KINDS.index(kind)

    # ------------------------------------------------------------------ #
    # Size
    # ------------------------------------------------------------------ #
    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    @property
    def version_count(self) -> int:
        return sum(len(v) for v in self._versions.values())

    def has_node(self, element_id: int) -> bool:
        return element_id in self._nodes

    # ------------------------------------------------------------------ #
    # Mutation (called while building, or under the lock when applying events)
    # ------------------------------------------------------------------ #
    def set_node(self, element_id: int, type_: Optional[str], layer: Optional[str]) -> None:
        self._nodes[int(element_id)] = (self._code(type_), self._code(layer))

    def remove_node(self, element_id: int) -> None:
        """Drop an element. Relationship edges stay (the relationship rows own them,
        and a walk only crosses an edge whose far end is a node); the element's own
        parent link goes with it."""
        element_id = int(element_id)
        self._nodes.pop(element_id, None)
        self.remove_edge(column_edge_key(element_id))

    def set_edge(self, key: int, source: int, target: int, type_: Optional[str], kind: str) -> None:
        key = int(key)
        if key in self._edges:
            self.remove_edge(key)
        source, target = int(source), int(target)
        self._edges[key] = (source, target, self._code(type_), self._kind_code(kind))
        self._out.setdefault(source, []).append(key)
        self._in.setdefault(target, []).append(key)

    def remove_edge(self, key: int) -> None:
        edge = self._edges.pop(int(key), None)
        if edge is None:
            return
        source, target = edge[0], edge[1]
        for adjacency, node in ((self._out, source), (self._in, target)):
            keys = adjacency.get(node)
            if keys is not None:
                try:
                    keys.remove(int(key))
                except ValueError:
                    pass
                if not keys:
                    adjacency.pop(node, None)

    def set_versions(self, relationship_id: int, versions: Iterable[Tuple[datetime, Optional[datetime], int, int, str]]) -> None:
        relationship_id = int(relationship_id)
        self.clear_versions(relationship_id)
        stored = []
        for valid_from, valid_to, source, target, type_ in versions:
            stored.append((valid_from, valid_to, int(source), int(target), self._code(type_)))
        if not stored:
            return
        self._versions[relationship_id] = stored
        for _from, _to, source, target, _type in stored:
            self._versions_out.setdefault(source, set()).add(relationship_id)
            self._versions_in.setdefault(target, set()).add(relationship_id)

    def clear_versions(self, relationship_id: int) -> None:
        old = self._versions.pop(int(relationship_id), None)
        if not old:
            return
        for _from, _to, source, target, _type in old:
            for index, node in ((self._versions_out, source), (self._versions_in, target)):
                ids = index.get(node)
                if ids is not None:
                    ids.discard(int(relationship_id))
                    if not ids:
                        index.pop(node, None)

    # ------------------------------------------------------------------ #
    # Traversal
    # ------------------------------------------------------------------ #
    def traverse(
        self,
        root_id: int,
        max_hops: int = 3,
        direction: str = "downstream",
        *,
        kinds: Sequence[str] = ALL_KINDS,
        as_of: Optional[datetime] = None,
        max_nodes: int = DEFAULT_NODE_BUDGET,
    ) -> TraversalResult:
        """Breadth-first walk of at most ``max_hops`` hops from ``root_id``.

        A node is reported once, at the first (shortest) hop that reaches it,
        so a cycle terminates. ``max_nodes`` bounds the answer: when more
        nodes than that are reachable the rows stop at the budget and
        ``truncated`` is set. ``as_of`` walks the relationship versions that
        covered that instant instead of the current edges.
        """
        if direction not in DIRECTIONS:
            raise ValueError(f"direction must be one of {DIRECTIONS}")
        if not (1 <= int(max_hops) <= MAX_HOPS_CAP):
            raise ValueError(f"max_hops must be between 1 and {MAX_HOPS_CAP}")
        if int(max_nodes) < 1:
            raise ValueError("max_nodes must be at least 1")
        unknown = [k for k in kinds if k not in ALL_KINDS]
        if unknown:
            raise ValueError(f"unknown edge kind(s): {unknown}")
        root_id = int(root_id)
        if as_of is not None and as_of.tzinfo is not None:
            # entity_history stores naive UTC instants.
            as_of = as_of.astimezone(timezone.utc).replace(tzinfo=None)
        result = TraversalResult(
            organization_id=self.organization_id,
            root_id=root_id,
            max_hops=int(max_hops),
            direction=direction,
            as_of=as_of,
        )
        with self._lock:
            if not self._nodes:
                result.reason = NO_DATA_RECORDED_REASON
                return result
            if root_id not in self._nodes:
                result.reason = ELEMENT_NOT_FOUND_REASON
                return result
            kind_codes = {self._kind_code(k) for k in kinds}
            if as_of is None:
                neighbours = lambda node: self._current_neighbours(node, direction, kind_codes)  # noqa: E731
            elif KIND_EXPLICIT not in kinds:
                # Only relationships carry history; column links have no versions.
                neighbours = lambda node: ()  # noqa: E731
            else:
                neighbours = lambda node: self._as_of_neighbours(node, direction, as_of)  # noqa: E731

            seen = {root_id}
            frontier = deque([root_id])
            hop = 0
            while frontier and hop < result.max_hops and not result.truncated:
                hop += 1
                next_frontier: deque = deque()
                for node in frontier:
                    for key, other, type_code, kind_code in neighbours(node):
                        if other in seen:
                            continue
                        if len(seen) - 1 >= int(max_nodes):
                            result.truncated = True
                            break
                        seen.add(other)
                        node_type, node_layer = self._nodes.get(other, (None, None))
                        row = TraversalRow(
                            element_id=other,
                            hop=hop,
                            via_edge=key,
                            via_type=self._strings[type_code],
                            via_kind=ALL_KINDS[kind_code],
                            from_element_id=node,
                            type=self._strings[node_type] if node_type is not None else "",
                            layer=self._strings[node_layer] if node_layer is not None else "",
                        )
                        result.rows.append(row)
                        next_frontier.append(other)
                    if result.truncated:
                        break
                frontier = next_frontier
            for row in result.rows:
                result.by_layer[row.layer] = result.by_layer.get(row.layer, 0) + 1
                result.by_type[row.type] = result.by_type.get(row.type, 0) + 1
        return result

    def _current_neighbours(self, node: int, direction: str, kind_codes: set):
        if direction in ("downstream", "both"):
            for key in self._out.get(node, ()):
                source, target, type_code, kind_code = self._edges[key]
                if kind_code in kind_codes and target in self._nodes:
                    yield key, target, type_code, kind_code
        if direction in ("upstream", "both"):
            for key in self._in.get(node, ()):
                source, target, type_code, kind_code = self._edges[key]
                if kind_code in kind_codes and source in self._nodes:
                    yield key, source, type_code, kind_code

    def _as_of_neighbours(self, node: int, direction: str, as_of: datetime):
        explicit = self._kind_code(KIND_EXPLICIT)
        if direction in ("downstream", "both"):
            for relationship_id in sorted(self._versions_out.get(node, ())):
                for valid_from, valid_to, source, target, type_code in self._versions[relationship_id]:
                    if (
                        source == node and valid_from <= as_of and (valid_to is None or valid_to > as_of)
                        and (target in self._nodes or target in self._retired)
                    ):
                        yield relationship_id, target, type_code, explicit
        if direction in ("upstream", "both"):
            for relationship_id in sorted(self._versions_in.get(node, ())):
                for valid_from, valid_to, source, target, type_code in self._versions[relationship_id]:
                    if (
                        target == node and valid_from <= as_of and (valid_to is None or valid_to > as_of)
                        and (source in self._nodes or source in self._retired)
                    ):
                        yield relationship_id, source, type_code, explicit

    # ------------------------------------------------------------------ #
    # Canonical content, equality and the snapshot form
    # ------------------------------------------------------------------ #
    def _canonical(self) -> Dict[str, Any]:
        strings = self._strings
        return {
            "nodes": sorted((nid, strings[t], strings[lay]) for nid, (t, lay) in self._nodes.items()),
            "edges": sorted(
                (key, s, t, strings[ty], ALL_KINDS[k]) for key, (s, t, ty, k) in self._edges.items()
            ),
            "versions": sorted(
                (rid, vf.isoformat(), vt.isoformat() if vt else None, s, t, strings[ty])
                for rid, versions in self._versions.items()
                for vf, vt, s, t, ty in versions
            ),
            "retired": sorted(self._retired),
        }

    def fingerprint(self) -> str:
        """Hash of the content only (not the offset, not the string-code order)."""
        with self._lock:
            body = json.dumps(self._canonical(), separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def same_content_as(self, other: "GraphProjection") -> bool:
        return self.organization_id == other.organization_id and self.fingerprint() == other.fingerprint()

    def to_payload(self) -> bytes:
        with self._lock:
            body = self._canonical()
            document = {
                "format": SNAPSHOT_FORMAT,
                "organization_id": self.organization_id,
                "last_ordinal": self.last_ordinal,
                "nodes": body["nodes"],
                "edges": body["edges"],
                "versions": body["versions"],
                "retired": body["retired"],
            }
        return zlib.compress(json.dumps(document, separators=(",", ":")).encode("utf-8"), 6)

    @classmethod
    def from_payload(cls, payload: bytes, organization_id: int) -> "GraphProjection":
        document = json.loads(zlib.decompress(bytes(payload)).decode("utf-8"))
        if document.get("format") != SNAPSHOT_FORMAT:
            raise ValueError("unsupported snapshot format")
        if int(document.get("organization_id", -1)) != int(organization_id):
            raise ValueError("snapshot belongs to another organisation")
        projection = cls(organization_id)
        projection.last_ordinal = int(document["last_ordinal"])
        for nid, type_, layer in document["nodes"]:
            projection.set_node(nid, type_, layer)
        for key, source, target, type_, kind in document["edges"]:
            projection.set_edge(key, source, target, type_, kind)
        grouped: Dict[int, list] = {}
        for rid, valid_from, valid_to, source, target, type_ in document["versions"]:
            grouped.setdefault(int(rid), []).append(
                (
                    datetime.fromisoformat(valid_from),
                    datetime.fromisoformat(valid_to) if valid_to else None,
                    source,
                    target,
                    type_,
                )
            )
        for rid, versions in grouped.items():
            projection.set_versions(rid, versions)
        projection._retired = {int(x) for x in document.get("retired", ())}
        return projection


# ---------------------------------------------------------------------- #
# Reading the relational authority (every read names the organisation)
# ---------------------------------------------------------------------- #

def _models():
    from app.models import ArchiMateElement, ArchiMateRelationship

    return ArchiMateElement, ArchiMateRelationship


def _apply_element_row(projection: GraphProjection, element_id: int, type_, layer, parent_id) -> None:
    projection.set_node(element_id, type_, layer)
    projection.remove_edge(column_edge_key(element_id))
    if parent_id is not None:
        projection.set_edge(column_edge_key(element_id), parent_id, element_id, PARENT_EDGE_TYPE, KIND_COLUMN)


def _apply_relationship_row(projection: GraphProjection, rel_id, source_id, target_id, type_) -> None:
    if source_id is None or target_id is None:
        projection.remove_edge(rel_id)
        return
    projection.set_edge(rel_id, source_id, target_id, type_, KIND_EXPLICIT)


def _history_versions(organization_id: int, relationship_ids: Optional[Sequence[int]]):
    from app.models.entity_history import EntityHistory

    query = db.select(
        EntityHistory.record_id, EntityHistory.valid_from, EntityHistory.valid_to, EntityHistory.snapshot
    ).where(
        EntityHistory.organization_id == organization_id,
        EntityHistory.table_name == HISTORY_RELATIONSHIPS,
    )
    if relationship_ids is not None:
        query = query.where(EntityHistory.record_id.in_(list(relationship_ids)))
    grouped: Dict[int, list] = {}
    for record_id, valid_from, valid_to, snapshot in db.session.execute(query):
        snapshot = snapshot or {}
        source, target = snapshot.get("source_id"), snapshot.get("target_id")
        if source is None or target is None or valid_from is None:
            continue
        grouped.setdefault(int(record_id), []).append(
            (valid_from, valid_to, int(source), int(target), snapshot.get("type"))
        )
    return grouped


def _history_element_ids(organization_id: int, element_ids: Optional[Sequence[int]]) -> set:
    from app.models.entity_history import EntityHistory

    query = db.select(EntityHistory.record_id).where(
        EntityHistory.organization_id == organization_id,
        EntityHistory.table_name == HISTORY_ELEMENTS,
    ).distinct()
    if element_ids is not None:
        query = query.where(EntityHistory.record_id.in_(list(element_ids)))
    return {int(row[0]) for row in db.session.execute(query)}


def build_projection(organization_id: Optional[int]) -> GraphProjection:
    """Rebuild the projection for one organisation from the relational store.

    The log offset is read BEFORE the tables, so an event committed while the
    build runs is replayed afterwards (applying an event is idempotent) and is
    never missed.
    """
    if organization_id is None:
        raise ValueError("organization_id is required")
    from app.services.event_log_service import max_ordinal

    element_model, relationship_model = _models()
    projection = GraphProjection(organization_id)
    projection.last_ordinal = max_ordinal(organization_id)
    projection.built_at = datetime.now(timezone.utc)

    with projection._lock:
        elements = db.session.execute(
            db.select(
                element_model.id, element_model.type, element_model.layer, element_model.parent_id
            ).where(element_model.organization_id == organization_id)
        )
        for element_id, type_, layer, parent_id in elements:
            _apply_element_row(projection, element_id, type_, layer, parent_id)
        relationships = db.session.execute(
            db.select(
                relationship_model.id, relationship_model.source_id, relationship_model.target_id,
                relationship_model.type,
            ).where(relationship_model.organization_id == organization_id)
        )
        for rel_id, source_id, target_id, type_ in relationships:
            _apply_relationship_row(projection, rel_id, source_id, target_id, type_)
        for rel_id, versions in _history_versions(organization_id, None).items():
            projection.set_versions(rel_id, versions)
        projection._retired = _history_element_ids(organization_id, None) - set(projection._nodes)
    return projection


def _chunks(ids: Sequence[int]):
    for start in range(0, len(ids), ID_CHUNK):
        yield list(ids[start:start + ID_CHUNK])


def apply_events(projection: GraphProjection, events: Sequence[Dict[str, Any]]) -> int:
    """Apply a batch of event-log rows by re-reading the rows they name.

    Returns the number of element and relationship ids reloaded. Events of any
    other entity type only advance the offset. Rows are looked up by id AND the
    projection's organisation, so an id belonging to another organisation is
    treated as absent.
    """
    element_ids: set = set()
    relationship_ids: set = set()
    highest = projection.last_ordinal
    for event in events:
        highest = max(highest, int(event["ordinal"]))
        entity_id = event.get("entity_id")
        if entity_id is None:
            continue
        if event.get("entity_type") == ENTITY_ELEMENT:
            element_ids.add(int(entity_id))
        elif event.get("entity_type") == ENTITY_RELATIONSHIP:
            relationship_ids.add(int(entity_id))

    element_model, relationship_model = _models()
    org = projection.organization_id
    with projection._lock:
        for chunk in _chunks(sorted(element_ids)):
            found = {}
            for element_id, type_, layer, parent_id in db.session.execute(
                db.select(element_model.id, element_model.type, element_model.layer, element_model.parent_id).where(
                    element_model.organization_id == org, element_model.id.in_(chunk)
                )
            ):
                found[element_id] = (type_, layer, parent_id)
            had_history = _history_element_ids(org, chunk)
            for element_id in chunk:
                if element_id in found:
                    _apply_element_row(projection, element_id, *found[element_id])
                else:
                    projection.remove_node(element_id)
                if element_id not in found and element_id in had_history:
                    projection._retired.add(element_id)
                else:
                    projection._retired.discard(element_id)
        for chunk in _chunks(sorted(relationship_ids)):
            found_rel = {}
            for rel_id, source_id, target_id, type_ in db.session.execute(
                db.select(
                    relationship_model.id, relationship_model.source_id, relationship_model.target_id,
                    relationship_model.type,
                ).where(relationship_model.organization_id == org, relationship_model.id.in_(chunk))
            ):
                found_rel[rel_id] = (source_id, target_id, type_)
            versions = _history_versions(org, chunk)
            for rel_id in chunk:
                if rel_id in found_rel:
                    _apply_relationship_row(projection, rel_id, *found_rel[rel_id])
                else:
                    projection.remove_edge(rel_id)
                projection.set_versions(rel_id, versions.get(rel_id, ()))
        projection.last_ordinal = highest
    return len(element_ids) + len(relationship_ids)


def catch_up(projection: GraphProjection) -> int:
    """Apply every logged event after the projection's offset. Returns how many ids were reloaded."""
    from app.services.event_log_service import read_from_offset

    reloaded = 0
    while True:
        events = read_from_offset(projection.organization_id, projection.last_ordinal, limit=EVENT_BATCH)
        if not events:
            return reloaded
        reloaded += apply_events(projection, events)


# ---------------------------------------------------------------------- #
# Snapshot storage
# ---------------------------------------------------------------------- #

def save_snapshot(projection: GraphProjection) -> None:
    """Store the projection as the organisation's one snapshot row (flushes, never commits)."""
    from app.models.graph_projection_snapshot import GraphProjectionSnapshot

    org = projection.organization_id
    row = db.session.execute(
        db.select(GraphProjectionSnapshot).where(GraphProjectionSnapshot.organization_id == org)
    ).scalar_one_or_none()
    if row is None:
        row = GraphProjectionSnapshot(organization_id=org)
        db.session.add(row)
    row.format_version = SNAPSHOT_FORMAT
    row.last_ordinal = projection.last_ordinal
    row.node_count = projection.node_count
    row.edge_count = projection.edge_count
    payload = projection.to_payload()
    row.payload = payload
    row.fingerprint = hashlib.sha256(payload).hexdigest()
    row.built_at = (projection.built_at or datetime.now(timezone.utc)).replace(tzinfo=None)
    row.verified_at = projection.verified_at.replace(tzinfo=None) if projection.verified_at else None
    db.session.flush()


def load_snapshot(organization_id: Optional[int]) -> Optional[GraphProjection]:
    """The stored projection, or None when there is none or it cannot be trusted.

    A snapshot that fails to decode, has another format, belongs to another
    organisation or no longer matches its recorded checksum (SHA-256 of the
    stored bytes) is ignored (the caller rebuilds), never partly used.
    """
    if organization_id is None:
        return None
    from app.models.graph_projection_snapshot import GraphProjectionSnapshot

    row = db.session.execute(
        db.select(GraphProjectionSnapshot).where(GraphProjectionSnapshot.organization_id == organization_id)
    ).scalar_one_or_none()
    if row is None or row.format_version != SNAPSHOT_FORMAT:
        return None
    if hashlib.sha256(bytes(row.payload)).hexdigest() != row.fingerprint:
        logger.error("graph projection snapshot for organisation %s failed its checksum; rebuilding", organization_id)
        return None
    try:
        projection = GraphProjection.from_payload(row.payload, organization_id)
    except Exception:  # noqa: BLE001 - an unreadable snapshot is a cache miss, logged
        logger.exception("graph projection snapshot for organisation %s is unreadable; rebuilding", organization_id)
        return None
    if projection.last_ordinal != row.last_ordinal:
        logger.error("graph projection snapshot for organisation %s disagrees with its offset; rebuilding", organization_id)
        return None
    projection.built_at = row.built_at.replace(tzinfo=timezone.utc) if row.built_at else None
    projection.verified_at = row.verified_at.replace(tzinfo=timezone.utc) if row.verified_at else None
    return projection


def load_or_build(organization_id: int) -> GraphProjection:
    return load_snapshot(organization_id) or build_projection(organization_id)


# ---------------------------------------------------------------------- #
# The process cache and the query entry point
# ---------------------------------------------------------------------- #

_CACHE: "OrderedDict[int, GraphProjection]" = OrderedDict()
_CACHE_GUARD = threading.Lock()
_ORG_LOCKS: Dict[int, threading.Lock] = {}


def _org_lock(organization_id: int) -> threading.Lock:
    with _CACHE_GUARD:
        return _ORG_LOCKS.setdefault(int(organization_id), threading.Lock())


def clear_cache(organization_id: Optional[int] = None) -> None:
    with _CACHE_GUARD:
        if organization_id is None:
            _CACHE.clear()
        else:
            _CACHE.pop(int(organization_id), None)


def get_projection(organization_id: Optional[int]) -> Optional[GraphProjection]:
    """The current projection for the organisation (cached, brought up to date from the log).

    ``None`` for an unknown organisation: nothing is read for it.
    """
    if organization_id is None:
        return None
    organization_id = int(organization_id)
    with _org_lock(organization_id):
        with _CACHE_GUARD:
            projection = _CACHE.get(organization_id)
            if projection is not None:
                _CACHE.move_to_end(organization_id)
        if projection is None:
            projection = load_or_build(organization_id)
            with _CACHE_GUARD:
                _CACHE[organization_id] = projection
                while len(_CACHE) > MAX_CACHED_ORGANISATIONS:
                    _CACHE.popitem(last=False)
        catch_up(projection)
    return projection


def query(
    organization_id: Optional[int],
    root_id: int,
    max_hops: int = 3,
    direction: str = "downstream",
    *,
    kinds: Sequence[str] = ALL_KINDS,
    as_of: Optional[datetime] = None,
    max_nodes: int = DEFAULT_NODE_BUDGET,
) -> TraversalResult:
    """Everything within ``max_hops`` of ``root_id`` in the organisation's own graph.

    With no organisation the answer is empty with the closed reason
    ``no_tenant_context``; nothing is read. Callers pass the ACTIVE
    organisation (``current_org_id()``), never the user's home organisation.
    """
    if organization_id is None:
        return TraversalResult(
            organization_id=None, root_id=root_id, max_hops=max_hops, direction=direction,
            as_of=as_of, reason=NO_TENANT_CONTEXT_REASON,
        )
    projection = get_projection(organization_id)
    return projection.traverse(
        root_id, max_hops, direction, kinds=kinds, as_of=as_of, max_nodes=max_nodes
    )


# ---------------------------------------------------------------------- #
# Refresh (what the scheduled job runs for one organisation)
# ---------------------------------------------------------------------- #

SNAPSHOT_EVERY_EVENTS = 500
VERIFY_EVERY_HOURS = 24


def refresh_organisation(organization_id: Optional[int], *, verify: Optional[bool] = None) -> Dict[str, Any]:
    """Bring one organisation's stored snapshot up to date from the log.

    Loads the snapshot (or builds), applies the log tail, and saves a new
    snapshot when there was none, when enough events were applied, or after a
    verification. ``verify`` rebuilds from the relational store and compares: a
    difference means a write bypassed the event log, and the rebuilt projection
    replaces the maintained one. ``None`` verifies when the stored snapshot has
    not been checked for ``VERIFY_EVERY_HOURS``.
    """
    if organization_id is None:
        return {"skipped": NO_TENANT_CONTEXT_REASON}
    organization_id = int(organization_id)
    now = datetime.now(timezone.utc)
    snapshot = load_snapshot(organization_id)
    started_from_snapshot = snapshot is not None
    projection = snapshot or build_projection(organization_id)
    if snapshot is None:
        projection.verified_at = projection.built_at
    if verify is None:
        verify = started_from_snapshot and (
            projection.verified_at is None
            or (now - projection.verified_at).total_seconds() >= VERIFY_EVERY_HOURS * 3600
        )
    starting_ordinal = projection.last_ordinal
    reloaded = catch_up(projection)
    drift = None
    if verify:
        rebuilt = build_projection(organization_id)
        catch_up(rebuilt)
        drift = not projection.same_content_as(rebuilt)
        if drift:
            logger.error(
                "graph projection for organisation %s drifted from the relational store "
                "(a write bypassed the event log); replaced by a rebuild", organization_id,
            )
        projection = rebuilt
        projection.verified_at = now
    saved = False
    outcome = "ok"
    if projection.node_count == 0 and projection.edge_count == 0:
        outcome = "empty"
    else:
        events_applied = projection.last_ordinal - starting_ordinal
        if (not started_from_snapshot) or verify or events_applied >= SNAPSHOT_EVERY_EVENTS:
            save_snapshot(projection)
            saved = True
    clear_cache(organization_id)
    return {
        "outcome": outcome,
        "nodes": projection.node_count,
        "edges": projection.edge_count,
        "versions": projection.version_count,
        "last_ordinal": projection.last_ordinal,
        "from_snapshot": started_from_snapshot,
        "reloaded": reloaded,
        "saved": saved,
        "verified": bool(verify),
        "drift": drift,
    }


__all__ = [
    "ALL_KINDS",
    "PARENT_EDGE_TYPE",
    "GraphProjection",
    "KIND_COLUMN",
    "KIND_EXPLICIT",
    "TraversalResult",
    "TraversalRow",
    "apply_events",
    "build_projection",
    "catch_up",
    "clear_cache",
    "get_projection",
    "load_or_build",
    "load_snapshot",
    "query",
    "refresh_organisation",
    "save_snapshot",
]
