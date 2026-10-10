"""Decision C — static checks over every pack file under
``app/seed_data/reference_packs/``. Parametrised so every later pack is
checked by the same ten rules without a new test being written for it."""
from __future__ import annotations

import pytest

from app.modules.reference_packs.services.pack_files import (
    DEFAULT_ROOT,
    discover_pack_files,
    load_pack_file,
    validate_pack,
)

PACK_FILES = discover_pack_files(DEFAULT_ROOT)
PACK_FILE_IDS = [f"{p.parent.name}@{p.stem}" for p in PACK_FILES]


def test_thirteen_pack_files_exist():
    assert len(PACK_FILES) == 13, (
        f"expected thirteen pack files under {DEFAULT_ROOT}, found {len(PACK_FILES)}: {PACK_FILES}"
    )


@pytest.mark.parametrize("path", PACK_FILES, ids=PACK_FILE_IDS)
def test_pack_file_passes_every_static_check(path):
    """Checks (1)-(10) of decision C, run in one pass by ``validate_pack``:
    element type/layer agreement, relationship validity and no self-reference,
    module coverage and R-2, capability mapping shape, sources.yml shape and
    licence vocabulary, no forbidden key or placeholder text, key-shape rules,
    and the word-window test against any non-open source's text_file."""
    pack = load_pack_file(path)
    errors = validate_pack(pack)
    assert errors == [], f"{path}: {errors}"


def test_word_window_skip_counts_are_logged():
    """Check (9): sources without a ``text_file`` are skipped, not failed —
    counted here so the count is visible in the test's own output rather than
    silently assumed."""
    total_skipped = 0
    for path in PACK_FILES:
        pack = load_pack_file(path)
        validate_pack(pack)
        total_skipped += pack.get("_word_window_skipped_no_text_file", 0)
    print(f"word-window test: {total_skipped} source entries skipped (no text_file)")
    assert total_skipped >= 0
