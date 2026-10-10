"""Tests for scripts/check_composer_url_params.py — the FIX 5 permanent gate.

Proves the gate fails on a deliberately-reintroduced bad example (the exact
bug class fixed across D1-D4 and tonight's follow-up round: a literal
`/archimate/composer?...` link carrying a parameter the composer never
reads) and passes on the current clean tree.
"""

import subprocess
import sys
import tempfile
import os

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts", "check_composer_url_params.py")


def _run(*paths):
    return subprocess.run(
        [sys.executable, SCRIPT, "--count", *paths],
        capture_output=True, text=True,
    )


@pytest.fixture
def tmp_html():
    fd, path = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    yield path
    os.remove(path)


def test_flags_unknown_param_name(tmp_html):
    with open(tmp_html, "w", encoding="utf-8") as fh:
        fh.write('<a href="/archimate/composer?element_id=42">Open</a>\n')
    result = _run(tmp_html)
    assert result.stdout.strip().splitlines()[-1] == "1"


def test_element_is_now_a_known_good_param(tmp_html):
    """`element` moved from the bad list to the known-good list on 2026-09-21,
    when the composer gained the ability to read it. This is the inverse of
    test_flags_unknown_param_name above, which now uses `element_id` (still
    unread) as its bad-param example."""
    with open(tmp_html, "w", encoding="utf-8") as fh:
        fh.write('<a href="/archimate/composer?element=42">Open</a>\n')
    result = _run(tmp_html)
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_flags_elements_param(tmp_html):
    with open(tmp_html, "w", encoding="utf-8") as fh:
        fh.write("composer_url = f\"/archimate/composer?elements={','.join(ids)}\"\n")
    result = _run(tmp_html)
    assert result.stdout.strip().splitlines()[-1] == "1"


def test_passes_known_good_params(tmp_html):
    with open(tmp_html, "w", encoding="utf-8") as fh:
        fh.write('<a href="/archimate/composer?viewpoint=layered&layer=Motivation">+ Add</a>\n')
        fh.write('<a href="/archimate/composer?viewpoint_id=42">Open</a>\n')
        fh.write('<a href="/archimate/composer?solution_id=1&prefill=1">Open</a>\n')
    result = _run(tmp_html)
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_honours_escape_hatch(tmp_html):
    with open(tmp_html, "w", encoding="utf-8") as fh:
        fh.write('<a href="/archimate/composer?element_id=42">Open</a>  {# composer-url-ok: legacy, tracked #}\n')
    result = _run(tmp_html)
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_flags_numeric_viewpoint_value(tmp_html):
    with open(tmp_html, "w", encoding="utf-8") as fh:
        fh.write('<a href="/archimate/composer?viewpoint=42">Open</a>\n')
    result = _run(tmp_html)
    assert result.stdout.strip().splitlines()[-1] == "1"


def test_flags_interpolated_viewpoint_value(tmp_html):
    with open(tmp_html, "w", encoding="utf-8") as fh:
        fh.write("composer_url = '/archimate/composer?viewpoint=' + existingId\n")
        fh.write('composer_url2 = f"/archimate/composer?viewpoint={existingId}"\n')
    result = _run(tmp_html)
    assert result.stdout.strip().splitlines()[-1] == "2"


def test_flags_url_for_composer_pattern(tmp_html):
    with open(tmp_html, "w", encoding="utf-8") as fh:
        fh.write(
            "<a href=\"{{ url_for('archimate.composer_page') }}?element_id=1\">Open</a>\n"
        )
    result = _run(tmp_html)
    assert result.stdout.strip().splitlines()[-1] == "1"


def test_flags_bad_param_after_double_escaped_ampersand(tmp_html):
    with open(tmp_html, "w", encoding="utf-8") as fh:
        fh.write(
            '<a href="/archimate/composer?viewpoint=layered&amp;amp;element_id=1">Open</a>\n'
        )
    result = _run(tmp_html)
    assert result.stdout.strip().splitlines()[-1] == "1"


def test_current_tree_is_clean():
    """The whole app/ tree, as it stands after tonight's fixes, is clean."""
    result = _run()
    assert result.returncode == 0, result.stdout
    assert result.stdout.strip().splitlines()[-1] == "0"
