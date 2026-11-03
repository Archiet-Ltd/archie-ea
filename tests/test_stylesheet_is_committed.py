"""A clean checkout serves the stylesheet every page links to.

Pages link the fingerprinted copy the manifest names. The ignore rules hide those copies, so a
rebuild can leave the manifest naming a file git does not hold and every page loses its styling.
"""
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_git_tracks_the_stylesheet_the_manifest_names():
    named = json.loads((REPO / "app/static/manifest.json").read_text())["css/tailwind-output.css"]
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "app/static/" + named],
        cwd=REPO, capture_output=True, text=True,
    )
    assert tracked.returncode == 0, "the manifest names %s, which git does not track: %s" % (named, tracked.stderr.strip())
