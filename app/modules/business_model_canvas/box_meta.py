"""What each canvas box is for: its colour zone (chart token 1 to 5), element type and guidance.

The data is the canvas family's own table (components/canvas/_boxes.json, vendored with the macros), so a box
reads the same here as in the pattern library. A box the table does not know falls back to zone 1 and no guidance."""

import json
import re
from functools import lru_cache
from pathlib import Path

_TABLE = Path(__file__).resolve().parents[2] / "templates" / "components" / "canvas" / "_boxes.json"
_NAMES = {
    "business-model-canvas": "Business Model Canvas", "value-proposition-canvas": "Value Proposition Canvas",
    "lean-canvas": "Lean Canvas", "empathy-map": "Empathy Map", "swot-analysis": "SWOT", "pestle-analysis": "PESTLE",
    "porters-five-forces": "Porter's Five Forces", "ansoff-matrix": "Ansoff Matrix", "mckinsey-7s": "McKinsey 7S",
    "blue-ocean": "Blue Ocean", "wardley-mapping": "Wardley Map", "balanced-scorecard": "Balanced Scorecard",
    "okr": "OKR", "product-vision-board": "Product Vision Board",
}


@lru_cache(maxsize=1)
def _boxes():
    return json.loads(_TABLE.read_text(encoding="utf8"))


def _readable(element_type):
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", element_type)


def _zone_meta(canvas_id, key):
    """What the box takes, for a box the library table does not describe: read from the box's own element types."""
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES

    template = CANVAS_TEMPLATES.get(canvas_id.replace("-", "_"))
    for zone in (template or {}).get("zones", []):
        if zone["box_key"] == key.replace("-", "_"):
            types = [_readable(t) for t in zone["element_types"]]
            return {
                "zone": 1 + (zone["order"] - 1) % 5, "zone_label": zone["colour"].capitalize(),
                "element": types[0] if types else "Note",
                "guidance": "Takes " + ", ".join(types) + " from your model. An element you add here can also appear on any other canvas that takes its type.",
            }
    return {}


def canvas_box_meta(canvas_id, key):
    meta = _boxes().get(canvas_id, {}).get(key) or _zone_meta(canvas_id, key)
    if meta.get("element", "Note") == "Note":
        # The library draws a free note here; Entelim keeps every entry as an element, so the box names that type.
        meta = {**meta, "element": _zone_meta(canvas_id, key).get("element", "Note")}
    return {
        "zone": meta.get("zone", 1),
        "zone_label": meta.get("zone_label", ""),
        "element": meta.get("element", "Note"),
        "guidance": meta.get("guidance", ""),
        "example": meta.get("example", ""),
        "canvas": _NAMES.get(canvas_id, canvas_id),
    }
