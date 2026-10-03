"""Duration-balanced test sharding with file-size fallback."""

import json
import pytest

from scripts.ci.test_sharding import (
    _bin_pack,
    _file_of,
    build_durations_from_junit,
)


# ── Pytest item stand-in for testing _file_of ─────────────────────────────

class _FakeItem:
    def __init__(self, nodeid):
        self.nodeid = nodeid


def test_file_of_extracts_path_before_first_double_colon():
    assert _file_of(_FakeItem("tests/test_foo.py::TestClass::test_method")) == "tests/test_foo.py"
    assert _file_of(_FakeItem("tests/bar.py::test_func")) == "tests/bar.py"
    assert _file_of(_FakeItem("single_file.py")) == "single_file.py"


# ── Greedy bin-pack ───────────────────────────────────────────────────────

def test_bin_pack_every_file_assigned_to_one_shard():
    weights = {"a.py": 10.0, "b.py": 5.0, "c.py": 8.0}
    assignment = _bin_pack(weights, 3)
    assert set(assignment) == {"a.py", "b.py", "c.py"}
    assert set(assignment.values()) == {0, 1, 2}


def test_bin_pack_deterministic_same_input_same_output():
    weights = {f"tests/file_{i}.py": float(100 - i) for i in range(20)}
    assert _bin_pack(weights, 8) == _bin_pack(weights, 8)


def test_bin_pack_within_10_percent_of_even():
    """With recorded durations as weights, no shard deviates more than 10%
    from the mean load — the acceptance criterion from the brief."""
    weights = {
        "tests/slow_a.py": 180.0,
        "tests/slow_b.py": 150.0,
        "tests/medium_a.py": 72.0,
        "tests/medium_b.py": 64.0,
        "tests/medium_c.py": 58.0,
        "tests/fast_a.py": 38.0,
        "tests/fast_b.py": 35.0,
        "tests/fast_c.py": 31.0,
        "tests/tiny_a.py": 18.0,
        "tests/tiny_b.py": 14.0,
        "tests/tiny_c.py": 12.0,
        "tests/tiny_d.py": 10.0,
        "tests/tiny_e.py": 9.0,
        "tests/tiny_f.py": 8.0,
        "tests/tiny_g.py": 7.0,
        "tests/tiny_h.py": 6.0,
        "tests/tiny_i.py": 5.0,
        "tests/tiny_j.py": 4.0,
        "tests/tiny_k.py": 3.0,
        "tests/tiny_l.py": 2.0,
    }
    total = sum(weights.values())
    shard_count = 4
    assignment = _bin_pack(weights, shard_count)

    assert len(assignment) == len(weights)

    loads = {shard: 0.0 for shard in range(shard_count)}
    for fpath, shard in assignment.items():
        loads[shard] += weights[fpath]
    mean = total / shard_count

    for shard, load in loads.items():
        deviation = abs(load - mean) / mean
        assert deviation <= 0.10, (
            f"shard {shard} load {load:.1f} deviates {deviation:.1%} "
            f"from mean {mean:.1f}"
        )


def test_bin_pack_twelve_shards_spread_across_all():
    """With many files of varying weight, all 12 shards get at least one file."""
    weights = {f"tests/f{i}.py": float(10 + i % 30) for i in range(50)}
    assignment = _bin_pack(weights, 12)
    assert set(range(12)) == set(assignment.values()), (
        "every shard must receive at least one file"
    )


# ── File-size fallback for unknown files ──────────────────────────────────

def test_unknown_file_gets_test_count_as_weight():
    """When durations exist but a file is missing from them, its test-count
    (item count) is used as the fallback weight."""
    durations = {"tests/known.py": 120.0}
    # Simulate what the plugin does: use durations.get(path, count_as_float)
    file_counts = {"tests/known.py": 5, "tests/new_file.py": 12}
    file_weights = {
        k: durations.get(k, float(v)) for k, v in file_counts.items()
    }
    assignment = _bin_pack(file_weights, 2)
    assert assignment["tests/known.py"] != assignment["tests/new_file.py"], (
        "known (120) and new (12) should go to different shards"
    )


# ── build_durations_from_junit ────────────────────────────────────────────

_JUNIT_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="0" skipped="0" tests="3" time="0">
    {}
  </testsuite>
</testsuites>"""


def _write_junit(path, cases):
    xml_lines = [
        '<testcase classname="t" file="{}" line="0" name="{}" time="{}" />'.format(f, n, t)
        for f, n, t in cases
    ]
    path.write_text(_JUNIT_TEMPLATE.format("\n    ".join(xml_lines)))


def test_build_durations_aggregates_by_file(tmp_path):
    xml = tmp_path / "shard-0.xml"
    _write_junit(xml, [
        ("tests/a.py", "t1", "12.5"),
        ("tests/a.py", "t2", "7.3"),
        ("tests/b.py", "t3", "5.0"),
    ])
    result = build_durations_from_junit([str(xml)])
    assert result["files"] == {"tests/a.py": 19.8, "tests/b.py": 5.0}


def test_build_durations_skips_missing_file_attribute(tmp_path):
    xml = tmp_path / "shard.xml"
    xml.write_text(
        '<?xml version="1.0"?><testsuites><testsuite name="p">'
        '<testcase classname="t" name="no_file" time="1.5" />'
        "</testsuite></testsuites>"
    )
    result = build_durations_from_junit([str(xml)])
    assert result["files"] == {}


def test_build_durations_skips_negative_and_non_numeric_time(tmp_path):
    xml = tmp_path / "shard.xml"
    _write_junit(xml, [
        ("tests/a.py", "t1", "-1.0"),
        ("tests/a.py", "t2", "nope"),
        ("tests/a.py", "t3", "2.0"),
    ])
    result = build_durations_from_junit([str(xml)])
    assert result["files"] == {"tests/a.py": 2.0}


def test_build_durations_skips_unreadable_xml(tmp_path):
    xml = tmp_path / "bad.xml"
    xml.write_text("not xml")
    result = build_durations_from_junit([str(xml)])
    assert result["files"] == {}