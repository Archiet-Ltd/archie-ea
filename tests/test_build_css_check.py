"""``build_css.py --check`` judges the committed manifest and leaves the tree as it found it.

Each test runs on a scratch copy of the build inputs, so the tracked manifest and stylesheet
are never rewritten while a server or another test reads them.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "build_css.py"

sys.path.insert(0, str(SCRIPT.parent))
import build_css  # noqa: E402


def _standalone_cli():
    """The standalone Tailwind CLI, not the npx fallback (which fetches a package to build)."""
    exe = "tailwindcss.exe" if sys.platform == "win32" else "tailwindcss"
    vendored = build_css.BIN_DIR / exe
    if vendored.exists():
        return vendored
    on_path = shutil.which("tailwindcss")
    return Path(on_path) if on_path else None


pytestmark = pytest.mark.skipif(_standalone_cli() is None, reason="no standalone Tailwind CLI to rebuild with")

BUILD_FILES = ("scripts/build_css.py", "tailwind.config.js", "tailwind.shadcn-syntax.js", "package.json", "postcss.config.js")
BUILD_DIRS = (
    "app/templates", "app/modules", "app/static/js", "app/static/vendor/basecoat",
    "app/basecoat_kit/data", "app/static/css",
)


@pytest.fixture
def scratch(tmp_path):
    root = tmp_path / "repo"
    for name in BUILD_FILES:
        if (REPO / name).exists():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, root / name)
    for name in BUILD_DIRS:
        shutil.copytree(REPO / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "app/static/manifest.json", root / "app/static/manifest.json")
    (root / "scripts" / "bin").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "bin" / _standalone_cli().name).symlink_to(_standalone_cli())
    return root


def _tree(root):
    css = root / "app" / "static" / "css"
    files = [root / "app/static/manifest.json", css / "tailwind-output.css", *css.glob("tailwind-output.*.css")]
    return {path.name: path.read_bytes() for path in files}


def test_a_manifest_naming_a_missing_copy_fails_the_check_and_changes_nothing(scratch):
    manifest = scratch / "app/static/manifest.json"
    stale = json.loads(manifest.read_text())
    stale["css/tailwind-output.css"] = "css/tailwind-output.00000000.css"
    manifest.write_text(json.dumps(stale, indent=2) + "\n", encoding="utf-8")
    before = _tree(scratch)

    run = subprocess.run(
        [sys.executable, "scripts/build_css.py", "--check"], cwd=scratch, capture_output=True, text=True
    )

    assert run.returncode != 0, "a manifest naming a copy that is not there passed the check:\n" + run.stdout
    assert "00000000" in run.stderr
    assert _tree(scratch) == before, "--check rewrote tracked build files"


def test_a_class_with_the_tag_after_the_star_fails_the_build_in_plain_words(scratch):
    page = scratch / "app/templates/_wrong_order_probe.html"
    page.write_text('<div class="*:[img]:w-full"></div>\n', encoding="utf-8")

    run = subprocess.run([sys.executable, "scripts/build_css.py"], cwd=scratch, capture_output=True, text=True)

    assert run.returncode != 0, "the build accepted a class that matches nothing:\n" + run.stdout
    output = run.stdout + run.stderr
    assert "write the tag first" in output
    assert "_wrong_order_probe.html" in output
