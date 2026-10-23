"""The pytest shards balance by recorded file durations and never drop or repeat a test.

Covers scripts/ci/test_sharding.py (the shard plugin), tests/shard_durations.json
(its committed duration table), scripts/ci/update_shard_durations.py (which
regenerates the table from CI junit files) and scripts/ci/check_shard_balance.py
(the drift warning). Also pins the job names the merge gate waits on.
"""
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import check_shard_balance  # noqa: E402
import test_sharding as sharding  # noqa: E402
import update_shard_durations as updater  # noqa: E402

TABLE_PATH = REPO / "tests" / "shard_durations.json"
TOTAL = 8


class _Item:
    def __init__(self, nodeid):
        self.nodeid = nodeid


def _config():
    deselected = []
    hook = types.SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items))
    return types.SimpleNamespace(hook=hook), deselected


def _run_hook(monkeypatch, nodeids, index, total=TOTAL, table_path=None):
    monkeypatch.setenv("CI_SHARD_TOTAL", str(total))
    monkeypatch.setenv("CI_SHARD_INDEX", str(index))
    if table_path is None:
        monkeypatch.delenv("CI_SHARD_DURATIONS", raising=False)
    else:
        monkeypatch.setenv("CI_SHARD_DURATIONS", str(table_path))
    items = [_Item(n) for n in nodeids]
    config, deselected = _config()
    sharding.pytest_collection_modifyitems(config, items)
    return [i.nodeid for i in items], [i.nodeid for i in deselected]


def _table():
    return json.loads(TABLE_PATH.read_text(encoding="utf-8"))


def _synthetic_nodeids(extra_files=()):
    """Every test the table knows (at its recorded count), plus files it has never seen."""
    nodeids = []
    for path, entry in _table()["files"].items():
        nodeids += [f"{path}::test_{n}" for n in range(entry["tests"])]
    for path in extra_files:
        nodeids += [f"{path}::test_{n}" for n in range(7)]
    return nodeids


def test_every_test_is_in_exactly_one_shard_including_files_the_table_has_never_seen(monkeypatch):
    nodeids = _synthetic_nodeids(extra_files=["tests/test_brand_new_file.py", "app/modules/x/tests/test_new.py"])
    seen = []
    for index in range(TOTAL):
        kept, deselected = _run_hook(monkeypatch, nodeids, index)
        assert sorted(kept + deselected) == sorted(nodeids)
        seen += kept
    assert sorted(seen) == sorted(nodeids), "a collected test is in no shard or in two shards"
    assert len(seen) == len(set(seen))


def test_a_missing_table_still_places_every_test_in_exactly_one_shard(monkeypatch, tmp_path):
    nodeids = [f"tests/test_f{f}.py::test_{n}" for f in range(25) for n in range(f % 5 + 1)]
    seen = []
    for index in range(TOTAL):
        kept, _ = _run_hook(monkeypatch, nodeids, index, table_path=tmp_path / "absent.json")
        seen += kept
    assert sorted(seen) == sorted(nodeids)


def test_a_corrupt_table_is_treated_as_missing(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert sharding.load_table(bad) == {}
    nodeids = [f"tests/test_f{f}.py::test_a" for f in range(20)]
    seen = []
    for index in range(TOTAL):
        kept, _ = _run_hook(monkeypatch, nodeids, index, table_path=bad)
        seen += kept
    assert sorted(seen) == sorted(nodeids)


def test_all_tests_of_one_file_stay_in_one_shard(monkeypatch):
    nodeids = _synthetic_nodeids()
    homes = {}
    for index in range(TOTAL):
        kept, _ = _run_hook(monkeypatch, nodeids, index)
        for nodeid in kept:
            homes.setdefault(nodeid.split("::")[0], set()).add(index)
    assert all(len(shards) == 1 for shards in homes.values())


def test_no_sharding_outside_ci(monkeypatch):
    monkeypatch.delenv("CI_SHARD_TOTAL", raising=False)
    items = [_Item("tests/test_a.py::test_x"), _Item("tests/test_b.py::test_y")]
    config, deselected = _config()
    sharding.pytest_collection_modifyitems(config, items)
    assert len(items) == 2 and deselected == []


def test_an_out_of_range_index_is_refused(monkeypatch):
    with pytest.raises(ValueError):
        _run_hook(monkeypatch, ["tests/test_a.py::test_x"], index=TOTAL)


def test_the_plan_is_deterministic():
    counts = {e: e_n for e, e_n in ((p, v["tests"]) for p, v in _table()["files"].items())}
    counts["tests/test_brand_new_file.py"] = 4
    assert sharding.plan(counts, TOTAL) == sharding.plan(dict(reversed(list(counts.items()))), TOTAL)


def test_a_file_the_table_does_not_know_is_weighted_by_the_median_rate():
    table = {"default_seconds_per_test": 0.5, "files": {"tests/test_known.py": {"seconds": 10.0, "tests": 5}}}
    weights = sharding.file_weights({"tests/test_known.py": 10, "tests/test_new.py": 4}, table)
    assert weights["tests/test_known.py"] == 20000  # 2 s per test, ten tests today
    assert weights["tests/test_new.py"] == 2000


def test_the_committed_table_balances_the_shards_within_fifteen_percent_of_the_mean():
    table = _table()
    counts = {path: entry["tests"] for path, entry in table["files"].items()}
    _assignment, loads = sharding.plan(counts, TOTAL, table)
    mean = sum(loads) / TOTAL
    assert max(loads) <= mean * 1.15, [round(ms / 60000, 1) for ms in loads]


def test_the_committed_table_balances_within_1_3_with_head_start():
    """Verify the head-start bin-pack keeps every shard within 1.3x of the mean.

    The shard-0 connector-tests head start of 246s is designed exactly so the
    longest-processing shard does not exceed 1.3x the mean when the table is
    well-formed.
    """
    table = _table()
    counts = {path: entry["tests"] for path, entry in table["files"].items()}
    _assignment, loads = sharding.plan(counts, TOTAL, table)
    mean = sum(loads) / TOTAL
    assert max(loads) <= mean * 1.3, [round(ms / 60000, 1) for ms in loads]


def test_shard_zero_receives_ordinary_files_with_committed_table():
    """Shard 0 must receive at least one ordinary test file with the committed table.

    The old 20_000 ms head start (treated as seconds = 20,000s) gave shard 0 such a
    large lead that it received zero ordinary files. The corrected 246s head start
    ensures shard 0 gets a fair share of ordinary work.
    """
    table = _table()
    counts = {path: entry["tests"] for path, entry in table["files"].items()}
    assignment, _loads = sharding.plan(counts, TOTAL, table)
    shard_zero_files = [path for path, shard in assignment.items() if shard == 0]
    assert shard_zero_files, "shard 0 received no ordinary test files"


def test_the_committed_table_is_well_formed():
    table = _table()
    assert table["default_seconds_per_test"] > 0
    assert table["files"], "an empty table silently falls back to test-count balancing"
    for path, entry in table["files"].items():
        assert entry["tests"] > 0 and entry["seconds"] >= 0, path


def test_the_real_plugin_places_every_collected_test_in_exactly_one_shard():
    """Collect a few real test files once unsharded and once per shard, through
    the same -p flag the workflow uses."""
    targets = ["tests/test_ci_shard_balance.py", "tests/test_detect_content_only.py"]

    def collect(extra_env):
        env = {**os.environ, **extra_env}
        env.pop("PYTEST_ADDOPTS", None)
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-qq", "-p", "no:cacheprovider",
             "-p", "scripts.ci.test_sharding", *targets],
            cwd=REPO, env=env, capture_output=True, text=True, timeout=300,
        )
        assert out.returncode in (0, 5), out.stdout[-2000:] + out.stderr[-2000:]
        return [line for line in out.stdout.splitlines() if "::" in line and not line.startswith(" ")]

    everything = collect({"CI_SHARD_TOTAL": "0"})
    assert everything
    seen = []
    for index in range(4):  # four shards keep the subprocess count (each imports conftest) low
        seen += collect({"CI_SHARD_TOTAL": "4", "CI_SHARD_INDEX": str(index)})
    assert sorted(seen) == sorted(everything)


# -- the workflow keeps what the merge gate and branch protection wait on ------

def _ci():
    return yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


def test_the_shard_job_names_and_count_are_unchanged():
    jobs = _ci()["jobs"]
    shard = jobs["tests-shard"]
    assert shard["name"] == "Tests (pytest + coverage) — shard ${{ matrix.shard }}"
    assert shard["strategy"]["matrix"]["shard"] == list(range(8))
    assert shard["env"]["CI_SHARD_TOTAL"] == "8"
    assert jobs["tests"]["name"] == "Tests (pytest + coverage)"
    assert "changes" in jobs["tests"]["needs"]


def test_the_balance_report_step_warns_without_failing_the_job():
    steps = _ci()["jobs"]["tests"]["steps"]
    report = [s for s in steps if s.get("name") == "Shard balance report"]
    assert len(report) == 1
    assert report[0]["continue-on-error"] is True
    assert "scripts/ci/check_shard_balance.py" in report[0]["run"]


# -- the regeneration script ---------------------------------------------------

def _junit(path, cases):
    body = "".join(f'<testcase classname="{c}" name="{n}" time="{t}" />' for c, n, t in cases)
    path.write_text(f'<testsuites><testsuite name="pytest">{body}</testsuite></testsuites>', encoding="utf-8")


def test_the_updater_sums_per_file_and_takes_the_median_across_runs(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "test_b.py").write_text("", encoding="utf-8")
    runs = []
    for seconds in (10, 30, 20):
        xml = tmp_path / f"backend-tests-{seconds}.xml"
        _junit(xml, [("tests.test_a", "t1", seconds), ("tests.test_a.TestK", "t2", 1),
                     ("tests.test_b", "t1", 2), ("not.a.repo.file", "t", 99)])
        runs.append(updater.read_junit([xml], tmp_path))
    assert runs[0]["tests/test_a.py"] == {"seconds": 11.0, "tests": 2}
    assert "not/a/repo/file.py" not in runs[0] and len(runs[0]) == 2
    table = updater.build_table(runs, ["1", "2", "3"])
    assert table["files"]["tests/test_a.py"] == {"seconds": 21.0, "tests": 2}
    assert table["files"]["tests/test_b.py"]["seconds"] == 2.0
    assert table["source_runs"] == ["1", "2", "3"]


# -- the drift warning -----------------------------------------------------------

def test_drift_flags_only_a_shard_off_the_runs_overall_ratio():
    even = [(i, 600.0, 600.0, []) for i in range(8)]
    assert check_shard_balance.drifted(even) == []
    slower_everywhere = [(i, 900.0, 600.0, []) for i in range(8)]  # runner load, not drift
    assert check_shard_balance.drifted(slower_everywhere) == []
    one_drifted = even[:7] + [(7, 1500.0, 600.0, [])]
    flagged = check_shard_balance.drifted(one_drifted)
    assert [shard for shard, _ in flagged] == [7]


def test_the_balance_check_never_fails_the_job(tmp_path, capsys):
    (tmp_path / "tests").mkdir()
    xml = tmp_path / "backend-tests-0.xml"
    _junit(xml, [("tests.test_ci_shard_balance", "t", 1)])
    assert check_shard_balance.main([str(xml), "--table", str(tmp_path / "missing.json")]) == 0
    assert "::warning::" in capsys.readouterr().out


def test_no_shard_is_left_empty_when_the_head_start_exceeds_the_predicted_work():
    # A table that underestimates the suite made the fixed 246 s head start larger than all
    # the predicted work, so shard 0 collected nothing and exited with code 5 (23 Oct 2026).
    table = {"default_seconds_per_test": 0.3, "files": {}}
    counts = {f"tests/smoke/test_{i}.py": 5 for i in range(40)}
    assignment, loads = sharding.plan(counts, 3, table)
    assert set(assignment.values()) == {0, 1, 2}
    assert all(load > 0 for load in loads)
    assert max(loads) <= 2 * min(loads)
