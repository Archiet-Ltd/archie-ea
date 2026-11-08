"""The stylesheet every signed-in page loads is in the repository, not only on the builder's disk."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"


def _named():
    return json.loads((STATIC / "manifest.json").read_text(encoding="utf-8"))["css/tailwind-output.css"]


def test_manifest_names_a_committed_stylesheet_identical_to_the_built_one():
    named = _named()
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "app/static/" + named],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert tracked.returncode == 0, "the manifest names %s, which git does not track" % named
    assert (STATIC / named).read_bytes() == (STATIC / "css" / "tailwind-output.css").read_bytes()
