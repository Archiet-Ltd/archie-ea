"""``build_css.py --check`` judges the committed manifest and leaves the tree as it found it."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "build_css.py"
CSS_DIR = REPO / "app" / "static" / "css"
MANIFEST = REPO / "app" / "static" / "manifest.json"

sys.path.insert(0, str(SCRIPT.parent))
import build_css  # noqa: E402

pytestmark = pytest.mark.skipif(build_css.find_tailwind() is None, reason="no Tailwind CLI to rebuild with")


def _tree():
    files = [MANIFEST, CSS_DIR / "tailwind-output.css", *CSS_DIR.glob("tailwind-output.*.css")]
    return {path.name: path.read_bytes() for path in files}


def _status():
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def test_a_manifest_naming_a_missing_copy_fails_the_check_and_changes_nothing():
    original = MANIFEST.read_bytes()
    try:
        stale = json.loads(original)
        stale["css/tailwind-output.css"] = "css/tailwind-output.00000000.css"
        MANIFEST.write_text(json.dumps(stale, indent=2) + "\n", encoding="utf-8")
        before_tree, before_status = _tree(), _status()

        run = subprocess.run([sys.executable, str(SCRIPT), "--check"], cwd=REPO, capture_output=True, text=True)

        assert run.returncode != 0, "a manifest naming a copy that is not there passed the check:\n" + run.stdout
        assert "00000000" in run.stderr
        assert _tree() == before_tree, "--check rewrote tracked build files"
        assert _status() == before_status
    finally:
        MANIFEST.write_bytes(original)
