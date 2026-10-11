#!/usr/bin/env python
"""Generate the versioned event catalogue from the event model.

Usage:
    python scripts/gen_event_catalogue.py <event-model-v1.yaml> <output.json>

The event model (AsyncAPI 3.0 style) names every event type under
``components.messages`` and its payload schema under ``components.schemas``.
This script writes ``app/seed_data/event_catalogue/catalogue-v1.json``: one
entry per message with ``$ref`` payload schemas resolved to names inside a
shared ``schemas`` map, plus a small ``product_events`` block for the types the
product emits today that the model does not name.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

CATALOGUE_VERSION = "1.0.0"
EVENT_VERSION = "1.0.0"
_COMPONENT_PREFIX = "#/components/schemas/"
_LOCAL_PREFIX = "#/schemas/"

_PRODUCT_OBJECT_SCHEMA = {
    "type": "object",
    "required": ["action", "id"],
    "properties": {
        "action": {"type": "string"},
        "id": {"type": ["integer", "null"]},
        "name": {},
        "type": {},
        "layer": {},
    },
    "additionalProperties": True,
}

_PRODUCT_NOTE = (
    "Emitted by the ArchiMate element and relationship sync; the event model "
    "names these changes per element family instead."
)


def _rewrite_refs(node):
    """Point every ``#/components/schemas/X`` at ``#/schemas/X``."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(_COMPONENT_PREFIX):
                out[key] = _LOCAL_PREFIX + value[len(_COMPONENT_PREFIX) :]
            else:
                out[key] = _rewrite_refs(value)
        return out
    if isinstance(node, list):
        return [_rewrite_refs(v) for v in node]
    return node


def _product_events() -> list[dict]:
    events = []
    for family in ("archimate_element", "archimate_relationship"):
        for action in ("created", "updated", "deleted"):
            events.append(
                {
                    "type": f"{family}.{action}",
                    "version": EVENT_VERSION,
                    "schema": _PRODUCT_OBJECT_SCHEMA,
                    "maps_to": None,
                    "note": _PRODUCT_NOTE,
                }
            )
    events.append(
        {
            "type": "webhook.test",
            "version": EVENT_VERSION,
            "schema": {"type": "object", "additionalProperties": True},
            "maps_to": None,
            "note": "Sent by the send-test action on a webhook subscription.",
        }
    )
    return sorted(events, key=lambda e: e["type"])


def build(model: dict) -> dict:
    components = model["components"]
    messages = components["messages"]
    raw_schemas = components["schemas"]
    envelope = _rewrite_refs(raw_schemas["CloudEventEnvelope"])
    schemas = {
        name: _rewrite_refs(body)
        for name, body in raw_schemas.items()
        if name != "CloudEventEnvelope"
    }
    events = []
    seen = set()
    for key, message in messages.items():
        event_type = message["x-event-type"]
        if event_type in seen:
            raise SystemExit(f"duplicate event type {event_type!r}")
        seen.add(event_type)
        ref = (message.get("payload") or {}).get("$ref", "")
        if not ref.startswith(_COMPONENT_PREFIX):
            raise SystemExit(f"message {key} has no resolvable payload $ref")
        schema_name = ref[len(_COMPONENT_PREFIX) :]
        if schema_name not in schemas:
            raise SystemExit(f"message {key} names unknown schema {schema_name!r}")
        events.append(
            {
                "type": event_type,
                "version": EVENT_VERSION,
                "id": message["x-id"],
                "channel": message.get("x-channel"),
                "entity": message.get("x-entity"),
                "action": message.get("x-action"),
                "summary": message.get("summary"),
                "schema": schema_name,
            }
        )
    events.sort(key=lambda e: e["type"])
    counts = (model.get("info") or {}).get("x-counts") or {}
    return {
        "catalogue_version": CATALOGUE_VERSION,
        "source": "event-model v1.0.0",
        "event_model_count": counts.get("event_types"),
        "envelope": envelope,
        "schemas": schemas,
        "events": events,
        "product_events": _product_events(),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    model = yaml.safe_load(Path(argv[1]).read_text(encoding="utf-8"))
    catalogue = build(model)
    Path(argv[2]).write_text(
        json.dumps(catalogue, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(catalogue['events'])} events, {len(catalogue['schemas'])} schemas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
