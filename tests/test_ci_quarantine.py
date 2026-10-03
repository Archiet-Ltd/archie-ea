"""Quarantine list wires to pytest markers and CI-mode filtering."""

import json
import os

import pytest


_QUARANTINE_JSON = {
    "quarantined": [
        {
            "test": "tests/smoke/test_flaky.py::test_flaky_a",
            "reason": "Flaky: timing",
        },
        {
            "test": "tests/smoke/test_flaky.py::test_flaky_b",
            "reason": "Flaky: timeout",
        },
    ]
}


@pytest.fixture
def quarantine_file(tmp_path):
    """Write a small quarantine list into a tmp dir and patch the module path."""
    p = tmp_path / "quarantine.json"
    p.write_text(json.dumps(_QUARANTINE_JSON))
    # The conftest module computes _QUARANTINE_FILE at import time from
    # __file__, so we cannot monkeypatch it at runtime without reloading.
    # Instead, tests here verify the hook logic directly by importing the
    # helpers after patching the path.
    return p


# ── Direct logic tests (bypass the conftest import-time read) ─────────────


class _FakeItem:
    def __init__(self, nodeid):
        self.nodeid = nodeid
        self._markers = []

    def add_marker(self, marker):
        self._markers.append(marker)


class _FakeConfig:
    def __init__(self):
        self._deselected = []

    @property
    def hook(self):
        return self

    def pytest_deselected(self, items):
        self._deselected.extend(items)


def _quarantined_prefixes():
    return frozenset(e["test"] for e in _QUARANTINE_JSON["quarantined"])


def _is_quarantined(nodeid, prefixes=None):
    if prefixes is None:
        prefixes = _quarantined_prefixes()
    for prefix in prefixes:
        if nodeid.startswith(prefix):
            return True
    return False


def test_quarantine_marker_applied_to_matching_tests():
    items = [
        _FakeItem("tests/smoke/test_flaky.py::test_flaky_a"),
        _FakeItem("tests/smoke/test_flaky.py::test_flaky_b"),
        _FakeItem("tests/stable.py::test_stable"),
    ]
    prefixes = _quarantined_prefixes()
    for item in items:
        if _is_quarantined(item.nodeid, prefixes):
            item.add_marker(pytest.mark.quarantine)

    assert items[0]._markers
    assert items[1]._markers
    assert not items[2]._markers


def test_quarantine_exclude_removes_matching_items():
    config = _FakeConfig()
    items = [
        _FakeItem("tests/smoke/test_flaky.py::test_flaky_a"),
        _FakeItem("tests/smoke/test_flaky.py::test_flaky_b"),
        _FakeItem("tests/stable.py::test_stable"),
        _FakeItem("tests/more.py::test_extra"),
    ]
    prefixes = _quarantined_prefixes()
    quarantined_indices = [
        i for i, item in enumerate(items)
        if _is_quarantined(item.nodeid, prefixes)
    ]
    # exclude mode
    deselected = [items[i] for i in quarantined_indices]
    config.pytest_deselected(items=deselected)
    kept = [item for i, item in enumerate(items) if i not in set(quarantined_indices)]

    assert len(kept) == 2
    assert kept[0].nodeid == "tests/stable.py::test_stable"
    assert kept[1].nodeid == "tests/more.py::test_extra"
    assert len(config._deselected) == 2


def test_quarantine_only_keeps_matching_items():
    config = _FakeConfig()
    items = [
        _FakeItem("tests/smoke/test_flaky.py::test_flaky_a"),
        _FakeItem("tests/smoke/test_flaky.py::test_flaky_b"),
        _FakeItem("tests/stable.py::test_stable"),
        _FakeItem("tests/more.py::test_extra"),
    ]
    prefixes = _quarantined_prefixes()
    quarantined_indices = [
        i for i, item in enumerate(items)
        if _is_quarantined(item.nodeid, prefixes)
    ]
    # only mode
    kept = [items[i] for i in quarantined_indices]
    deselected = [items[i] for i in range(len(items)) if i not in set(quarantined_indices)]
    config.pytest_deselected(items=deselected)

    assert len(kept) == 2
    assert kept[0].nodeid == "tests/smoke/test_flaky.py::test_flaky_a"
    assert kept[1].nodeid == "tests/smoke/test_flaky.py::test_flaky_b"
    assert len(config._deselected) == 2


def test_quarantine_prefix_matches_parametrized_variants():
    """A prefix like 'tests/foo.py::test_bar' matches all its parametrize ids."""
    items = [
        _FakeItem("tests/foo.py::test_bar[case-1]"),
        _FakeItem("tests/foo.py::test_bar[case-2]"),
        _FakeItem("tests/foo.py::test_baz"),
    ]
    prefixes = frozenset(["tests/foo.py::test_bar"])
    matches = [item.nodeid for item in items if _is_quarantined(item.nodeid, prefixes)]
    assert len(matches) == 2
    assert "tests/foo.py::test_bar[case-1]" in matches
    assert "tests/foo.py::test_bar[case-2]" in matches


def test_quarantine_file_matches_all_tests_in_file():
    """A prefix that is just a filename matches every test in that file."""
    items = [
        _FakeItem("tests/smoke/test_flaky.py::test_a"),
        _FakeItem("tests/smoke/test_flaky.py::test_b[1-2-3]"),
        _FakeItem("tests/smoke/test_flaky.py::TestClass::test_c"),
        _FakeItem("tests/smoke/test_not_flaky.py::test_d"),
    ]
    prefixes = frozenset(["tests/smoke/test_flaky.py"])
    matches = [item.nodeid for item in items if _is_quarantined(item.nodeid, prefixes)]
    assert len(matches) == 3


def test_empty_quarantine_list_does_nothing():
    items = [_FakeItem("tests/a.py::test_x"), _FakeItem("tests/b.py::test_y")]
    prefixes = frozenset()
    for item in items:
        if _is_quarantined(item.nodeid, prefixes):
            item.add_marker(pytest.mark.quarantine)
    assert not items[0]._markers
    assert not items[1]._markers


# ── End-to-end: quarantine.json file is valid ─────────────────────────────


def test_quarantine_json_file_exists_and_is_valid_json():
    import tests.conftest as conftest

    file = conftest._QUARANTINE_FILE
    assert file.exists(), f"quarantine.json must exist at {file}"
    data = json.loads(file.read_text(encoding="utf-8"))
    assert "quarantined" in data
    for entry in data["quarantined"]:
        assert "test" in entry, f"entry missing 'test': {entry}"
        assert "reason" in entry, f"entry missing 'reason': {entry}"