"""The brand token checker measures the token file and fails when a pair drops."""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_brand_tokens", ROOT / "scripts/check_brand_tokens.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

CSS = checker.TOKENS.read_text(encoding="utf-8")


def test_decided_tokens_pass_every_threshold():
    assert checker.contrast_failures(CSS) == []


def test_primary_is_the_logo_blue_in_both_themes():
    light = checker._block(CSS, ":root")
    dark = checker._block(CSS, ".dark")
    assert tuple(round(c * 255) for c in checker._rgb(light["primary"])) == (19, 95, 188)
    assert tuple(round(c * 255) for c in checker._rgb(dark["primary"])) == (99, 159, 244)
    assert light["brand"] == light["primary"] and dark["brand"] == dark["primary"]


def test_control_borders_reach_three_to_one():
    for selector in (":root", ".dark"):
        tokens = checker._block(CSS, selector)
        assert checker.contrast(tokens["input"], tokens["background"]) >= 3.0


def test_a_weakened_border_fails_the_check():
    weak = re.sub(r"--input: 0 0% 56\.1%", "--input: 0 0% 85%", CSS, count=1)
    assert any("input" in f for f in checker.contrast_failures(weak))


def test_a_drifting_logo_blue_fails_the_check():
    drift = re.sub(r"--brand: 213\.2 81\.3% 40\.7%", "--brand: 243 75% 58%", CSS, count=1)
    assert any("brand" in f for f in checker.contrast_failures(drift))
