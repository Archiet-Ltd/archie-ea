"""The local change to the vendored component recipes survives a re-vendor."""
import json
import re
from pathlib import Path

RECIPES = Path(__file__).resolve().parent.parent / "app" / "basecoat_kit" / "data" / "recipes.json"
WRONG_ORDER = re.compile(r"(?<![\w&-])\*:\[[a-z][a-z0-9]*\]:")


def test_no_recipe_writes_the_tag_after_the_star():
    text = RECIPES.read_text(encoding="utf-8")
    assert not WRONG_ORDER.findall(text), (
        "a recipe writes `*:[tag]:`, which matches the children of the tag; write `[tag]:*:` "
        "as the local change recorded in app/basecoat_kit/__init__.py does"
    )


def test_the_attachment_image_variant_keeps_the_tag_first():
    recipes = json.loads(RECIPES.read_text(encoding="utf-8"))
    image = recipes["attachment-media"]["variants"]["variants"]["variant"]["image"]
    for utility in ("aspect-square", "w-full", "object-cover"):
        assert f"[img]:*:{utility}" in image.split(), image
