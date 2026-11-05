"""The phone tap-target and input-zoom classes must exist in the built stylesheet.

A class written in a template is only a request: Tailwind emits a rule for it only when the
theme and the plugins know it. ``text-2xs`` was once a plain rule outside the theme, so
``md:text-2xs`` was written 21 times and never emitted, and 21 fields went from 11 to 16 px at
every width. This builds the stylesheet from the working tree with the repository's own
Tailwind config and fails for any phone class a template uses that the build does not emit.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import build_css  # noqa: E402

pytestmark = pytest.mark.skipif(build_css.find_tailwind() is None, reason="no Tailwind CLI to build with")

PHONE_CLASSES = (
    "max-md:min-h-6", "max-md:min-w-6", "max-md:py-1.5", "max-md:inline-flex",
    "max-md:items-center", "max-md:justify-center", "max-md:flex", "md:contents",
    "md:text-sm", "md:text-xs", "md:text-2xs", "text-base", "text-2xs",
    "max-md:[&_li_a]:py-1.5",
)
SIZE_VARIANT = re.compile(r"(?<![\w:-])((?:max-md:|md:)text-(?:2xs|xs|sm|base))(?![\w-])")


def _selector(cls: str) -> str:
    """The CSS selector text Tailwind writes for a class name."""
    return "." + re.sub(r"([^A-Za-z0-9_-])", r"\\\1", cls)


@pytest.fixture(scope="module")
def built_css(tmp_path_factory):
    out = tmp_path_factory.mktemp("css") / "out.css"
    cmd = [*build_css.find_tailwind(), "--config", str(build_css.CONFIG),
           "--input", str(build_css.INPUT_CSS), "--output", str(out)]
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return out.read_text()



def test_every_phone_class_is_emitted(built_css):
    missing = [c for c in PHONE_CLASSES if _selector(c) not in built_css]
    assert not missing, f"classes written in templates but not emitted by the build: {missing}"


def test_every_size_variant_used_in_a_template_is_emitted(built_css):
    used = set()
    for path in (REPO / "app" / "templates").rglob("*.html"):
        used.update(SIZE_VARIANT.findall(path.read_text(errors="ignore")))
    assert "md:text-2xs" in used, "the 21 form fields no longer use md:text-2xs: update this test with them"
    missing = sorted(c for c in used if _selector(c) not in built_css)
    assert not missing, f"size variants used in templates but not emitted: {missing}"
