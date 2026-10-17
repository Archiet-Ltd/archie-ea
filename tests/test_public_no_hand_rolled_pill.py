"""Public templates carry one pill, the badge macro; no second hand-rolled pill or chip."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "app" / "templates"
FILES = [
    ROOT / "main" / "index.html",
    *sorted((ROOT / "public").rglob("*.html")),
    *sorted((ROOT / "components").glob("public_*.html")),
]
CLASS_ATTR = re.compile(r'class="([^"]*)"')


def test_no_template_keeps_the_old_hero_chip():
    offenders = [str(f) for f in FILES if "home-hero__chip" in f.read_text(encoding="utf-8")]
    assert not offenders, offenders


def test_no_public_template_hand_rolls_a_pill():
    offenders = []
    for f in FILES:
        for m in CLASS_ATTR.finditer(f.read_text(encoding="utf-8")):
            classes = m.group(1).split()
            if "rounded-full" in classes and "border" in classes and "px-4" in classes:
                offenders.append(f"{f.relative_to(ROOT)}: {m.group(1)}")
    assert not offenders, offenders
