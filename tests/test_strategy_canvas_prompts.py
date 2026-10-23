"""The three strategy-canvas prompt files exist and name every box of their
template.

Each framework canvas (BCG, Ansoff, Blue Ocean) carries one prompt file at
``app/prompts/strategy/<key>.txt`` for the drafting engine. A prompt that
does not name one of its template's boxes would leave that box with no
guidance, so this pins that every box label appears in its own prompt.
"""
from __future__ import annotations

import os

import pytest

from app.config.archimate_viewpoints import CANVAS_TEMPLATES

PROMPT_DIR = os.path.join("app", "prompts", "strategy")

STRATEGY_CANVASES = ["bcg_matrix", "ansoff_matrix", "blue_ocean_strategy"]


@pytest.mark.parametrize("key", STRATEGY_CANVASES)
def test_prompt_file_exists(key):
    path = os.path.join(PROMPT_DIR, f"{key}.txt")
    assert os.path.isfile(path), f"missing prompt file {path}"
    with open(path, encoding="utf-8") as fh:
        assert fh.read().strip(), f"empty prompt file {path}"


@pytest.mark.parametrize("key", STRATEGY_CANVASES)
def test_prompt_names_every_box_of_its_template(key):
    path = os.path.join(PROMPT_DIR, f"{key}.txt")
    with open(path, encoding="utf-8") as fh:
        text = fh.read().lower()

    missing = [
        zone["label"]
        for zone in CANVAS_TEMPLATES[key]["zones"]
        if zone["label"].lower() not in text
    ]
    assert not missing, f"{key} prompt does not name boxes: {missing}"
