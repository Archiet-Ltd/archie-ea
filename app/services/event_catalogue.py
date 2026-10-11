"""The versioned event catalogue and the producer-side schema check.

The catalogue is generated from the event model by
``scripts/gen_event_catalogue.py`` and committed as
``app/seed_data/event_catalogue/catalogue-v1.json``. Producers call
:func:`validate` before an event is queued; subscribers read the catalogue to
choose event types.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from jsonschema import Draft202012Validator

_CATALOGUE_PATH = (
    Path(__file__).resolve().parent.parent / "seed_data" / "event_catalogue" / "catalogue-v1.json"
)


class EventCatalogueError(ValueError):
    """Base class for a refusal by the event catalogue."""

    code = "event_catalogue_error"

    def __init__(self, event_type: str, message: str, *, path: str = "$", detail: str = ""):
        super().__init__(message)
        self.event_type = event_type
        self.path = path
        self.detail = detail or message


class UnknownEventType(EventCatalogueError):
    code = "event_type_unknown"

    def __init__(self, event_type: str):
        super().__init__(event_type, f"event type {event_type!r} is not in the event catalogue")


class EventSchemaError(EventCatalogueError):
    code = "event_schema_invalid"

    def __init__(self, event_type: str, path: str, message: str):
        super().__init__(
            event_type,
            f"payload for {event_type!r} failed its schema at {path}: {message}",
            path=path,
            detail=message,
        )


@lru_cache(maxsize=1)
def get_catalogue() -> dict:
    """The whole catalogue, loaded once per process."""
    with _CATALOGUE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def _index() -> dict[str, dict]:
    cat = get_catalogue()
    index: dict[str, dict] = {}
    for entry in cat["events"]:
        index[entry["type"]] = {**entry, "source_block": "events"}
    for entry in cat["product_events"]:
        index.setdefault(entry["type"], {**entry, "source_block": "product_events"})
    return index


def get_event(event_type: str) -> Optional[dict]:
    """The catalogue entry for *event_type* with its schema resolved, or None."""
    entry = _index().get(event_type)
    if entry is None:
        return None
    resolved = dict(entry)
    resolved["schema"] = _schema_for(entry)
    if isinstance(entry.get("schema"), str):
        resolved["schema_name"] = entry["schema"]
    return resolved


def is_known(event_type: str) -> bool:
    return event_type in _index()


def all_types() -> list[str]:
    return sorted(_index())


def _schema_for(entry: dict) -> dict:
    schema = entry["schema"]
    if isinstance(schema, str):
        return get_catalogue()["schemas"][schema]
    return schema


@lru_cache(maxsize=None)
def _validator_for(event_type: str) -> Draft202012Validator:
    schema = _schema_for(_index()[event_type])
    if "#/schemas/" in json.dumps(schema):
        # Inner references point at the catalogue's shared ``schemas`` map.
        schema = {"schemas": get_catalogue()["schemas"], **schema}
    return Draft202012Validator(schema)


def validate(event_type: str, payload: Any) -> None:
    """Raise ``UnknownEventType`` or ``EventSchemaError``; return None when valid."""
    if not isinstance(event_type, str) or event_type not in _index():
        raise UnknownEventType(str(event_type))
    errors = sorted(
        _validator_for(event_type).iter_errors(payload),
        key=lambda e: [str(p) for p in e.absolute_path],
    )
    if errors:
        first = errors[0]
        parts = [str(p) for p in first.absolute_path]
        path = "$" + "".join(f"[{p}]" if p.isdigit() else f".{p}" for p in parts)
        raise EventSchemaError(event_type, path, first.message)


def matches(pattern: str, event_type: str) -> bool:
    """Subscription filter: ``*``, an exact type, or a prefix ending ``.*``."""
    if not pattern or not event_type:
        return False
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return event_type.startswith(pattern[:-1])
    return pattern == event_type


__all__ = [
    "EventCatalogueError",
    "EventSchemaError",
    "UnknownEventType",
    "all_types",
    "get_catalogue",
    "get_event",
    "is_known",
    "matches",
    "validate",
]
