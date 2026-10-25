"""What each canvas box is for: its colour zone (chart token 1 to 5), element type and guidance.

The data is the canvas family's own table (components/canvas/_boxes.json, vendored with the macros), so a box
reads the same here as in the pattern library. A box the table does not know falls back to zone 1 and no guidance."""

import json
from functools import lru_cache
from pathlib import Path

_TABLE = Path(__file__).resolve().parents[2] / "templates" / "components" / "canvas" / "_boxes.json"
_NAMES = {"business-model-canvas": "Business Model Canvas", "value-proposition-canvas": "Value Proposition Canvas"}


@lru_cache(maxsize=1)
def _boxes():
    return json.loads(_TABLE.read_text(encoding="utf8"))


def canvas_box_meta(canvas_id, key):
    meta = _boxes().get(canvas_id, {}).get(key, {})
    return {
        "zone": meta.get("zone", 1),
        "zone_label": meta.get("zone_label", ""),
        "element": meta.get("element", "Note"),
        "guidance": meta.get("guidance", ""),
        "example": meta.get("example", ""),
        "canvas": _NAMES.get(canvas_id, canvas_id),
    }
