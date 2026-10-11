"""Every module's own tests are collected by a bare ``pytest``, which is what CI runs.

``app/modules/<module>/tests/`` sits outside ``tests/``. CI's backend job runs
``pytest`` with no path, so only the directories in ``pytest.ini``'s
``testpaths`` are collected; a module whose tests are not covered there has
tests that never run anywhere. The shared fixtures reach those directories
through one bridge, ``app/modules/conftest.py``.
"""

from __future__ import annotations

import configparser
import glob
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _testpaths() -> list[str]:
    parser = configparser.ConfigParser()
    parser.read(ROOT / "pytest.ini")
    return parser.get("pytest", "testpaths").split()


def _collected_dirs() -> set[Path]:
    dirs: set[Path] = set()
    for entry in _testpaths():
        for match in glob.glob(str(ROOT / entry)):
            dirs.add(Path(match).resolve())
    return dirs


def _module_test_dirs() -> list[Path]:
    return sorted(
        path.parent.resolve()
        for path in (ROOT / "app" / "modules").glob("*/tests/test_*.py")
    )


def test_every_module_test_directory_is_collected():
    collected = _collected_dirs()
    missing = sorted(
        {str(d.relative_to(ROOT)) for d in _module_test_dirs()}
        - {str(d.relative_to(ROOT)) for d in collected}
    )
    assert _module_test_dirs(), "no module test directories found; the layout changed"
    assert missing == [], f"module tests CI never runs: {missing}"
    assert (ROOT / "tests").resolve() in collected


def test_module_tests_get_the_shared_fixtures_from_one_bridge():
    bridge = ROOT / "app" / "modules" / "conftest.py"
    assert bridge.exists(), "module tests have no route to the shared fixtures"
    text = bridge.read_text(encoding="utf-8")
    for fixture in ("app", "client", "db_session", "make_org", "tenant_ctx", "login_as"):
        assert fixture in text, fixture
    copies = sorted(
        str(p.relative_to(ROOT))
        for p in (ROOT / "app" / "modules").glob("*/tests/conftest.py")
        if "from tests.conftest import" in p.read_text(encoding="utf-8")
    )
    assert copies == [], f"a second copy of the fixture bridge: {copies}"
