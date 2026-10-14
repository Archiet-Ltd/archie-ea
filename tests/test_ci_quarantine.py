"""Quarantined tests leave the blocking shards and run in their own job."""

from pathlib import Path

import pytest

from scripts.ci import quarantine as q

REPO = Path(__file__).resolve().parent.parent


class _Item:
    def __init__(self, nodeid):
        self.nodeid = nodeid


class _Hook:
    def __init__(self):
        self.deselected = []

    def pytest_deselected(self, items):
        self.deselected.extend(i.nodeid for i in items)


class _Config:
    def __init__(self):
        self.hook = _Hook()


def test_entries_need_a_reason():
    assert q.parse_entries("tests/test_a.py::test_x  # timing flake\n") == {
        "tests/test_a.py::test_x": "timing flake"
    }
    for bad in ("tests/test_a.py::test_x\n", "tests/test_a.py::test_x #\n", "x  #\n"):
        with pytest.raises(q.QuarantineFormatError):
            q.parse_entries(bad)


def test_comments_and_blank_lines_are_skipped():
    assert q.parse_entries("# heading\n\n   \n") == {}


def test_a_node_id_without_parameters_covers_every_parameter_set():
    entries = {"tests/test_a.py::test_x": "r", "tests/test_b.py::test_y[one]": "r"}
    assert q.is_quarantined("tests/test_a.py::test_x", entries)
    assert q.is_quarantined("tests/test_a.py::test_x[chromium]", entries)
    assert q.is_quarantined("tests/test_b.py::test_y[one]", entries)
    assert not q.is_quarantined("tests/test_b.py::test_y[two]", entries)
    assert not q.is_quarantined("tests/test_a.py::test_xy", entries)


IDS = [
    "tests/test_a.py::test_flaky",
    "tests/test_a.py::test_solid",
    "tests/test_b.py::test_other",
]


def _run(monkeypatch, tmp_path, mode, ids):
    listing = tmp_path / "quarantine.txt"
    listing.write_text("tests/test_a.py::test_flaky  # timing flake\n", encoding="utf-8")
    monkeypatch.setenv("CI_QUARANTINE_FILE", str(listing))
    if mode:
        monkeypatch.setenv("CI_QUARANTINE_MODE", mode)
    else:
        monkeypatch.delenv("CI_QUARANTINE_MODE", raising=False)
    items = [_Item(i) for i in ids]
    config = _Config()
    q.pytest_collection_modifyitems(config, items)
    return [i.nodeid for i in items], config.hook.deselected


def test_blocking_shards_deselect_only_the_listed_tests(monkeypatch, tmp_path):
    kept, dropped = _run(monkeypatch, tmp_path, "exclude", IDS)
    assert kept == IDS[1:]
    assert dropped == [IDS[0]]


def test_the_quarantine_job_runs_only_the_listed_tests(monkeypatch, tmp_path):
    kept, dropped = _run(monkeypatch, tmp_path, "only", IDS)
    assert kept == [IDS[0]]
    assert dropped == IDS[1:]


def test_without_a_mode_everything_runs(monkeypatch, tmp_path):
    kept, dropped = _run(monkeypatch, tmp_path, "", IDS)
    assert kept == IDS and dropped == []


def test_a_test_not_on_the_list_is_never_dropped(monkeypatch, tmp_path):
    kept, _ = _run(monkeypatch, tmp_path, "exclude", ["tests/test_c.py::test_new_failure"])
    assert kept == ["tests/test_c.py::test_new_failure"]


def test_every_test_is_in_exactly_one_of_the_two_jobs(monkeypatch, tmp_path):
    blocking, _ = _run(monkeypatch, tmp_path, "exclude", IDS)
    quarantined, _ = _run(monkeypatch, tmp_path, "only", IDS)
    assert sorted(blocking + quarantined) == sorted(IDS)
    assert not set(blocking) & set(quarantined)


def test_stale_entries_are_reported():
    entries = {"tests/test_a.py::test_gone": "r", "tests/test_a.py::test_here": "r"}
    items = [_Item("tests/test_a.py::test_here[x]")]
    assert q.unmatched_entries(items, entries) == ["tests/test_a.py::test_gone"]


def test_the_checked_in_list_is_well_formed_and_every_entry_names_a_test_file():
    entries = q.load_entries(REPO / "scripts" / "ci" / "quarantine.txt")
    assert entries, "the seeded list should not be empty"
    assert len(entries) <= 20, "the quarantine list is meant to stay small"
    for node_id, reason in entries.items():
        path = node_id.split("::", 1)[0]
        assert (REPO / path).is_file(), f"{node_id} names a file that does not exist"
        assert reason


def test_the_shards_deselect_and_the_quarantine_job_selects():
    import yaml

    text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    jobs = yaml.safe_load(text)["jobs"]
    assert jobs["tests-shard"]["env"]["CI_QUARANTINE_MODE"] == "exclude"
    shard_run = next(
        s["run"] for s in jobs["tests-shard"]["steps"] if "--ignore=tests/smoke" in s.get("run", "")
    )
    assert "-p scripts.ci.quarantine" in shard_run
    quarantine = jobs["quarantine-tests"]
    assert quarantine["env"]["CI_QUARANTINE_MODE"] == "only"
    assert quarantine["continue-on-error"] is True
    # Nothing waits on the non-blocking job.
    for job_id, job in jobs.items():
        needs = job.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        assert "quarantine-tests" not in needs, job_id
