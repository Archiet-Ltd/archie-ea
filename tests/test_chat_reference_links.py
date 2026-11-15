"""A cited name in a chat answer becomes a link as a whole word and never leaves a stray bold mark.

Runs the real `referencesAsLinks` from the chat renderer under node and checks the markdown it hands on.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "ai_chat" / "render.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _function_source(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    depth = 0
    for i in range(source.index("{", start), len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(name)


def _markdown(text, sources):
    source = SCRIPT.read_text(encoding="utf-8")
    program = "\n".join(
        [
            _function_source(source, "referencesAsLinks"),
            "process.stdout.write(JSON.stringify(referencesAsLinks(%s, %s)));" % (json.dumps(text), json.dumps(sources)),
        ]
    )
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(done.stdout)


def _source(ident, name):
    return {"id": str(ident), "name": name, "url": "/intelligence/answers/element/%s" % ident}


def test_a_name_inside_a_longer_word_is_not_linked():
    out = _markdown("We take ApplePay [ref:12] for orders.", [_source(12, "Pay")])
    assert "ApplePay" in out
    assert "Apple[Pay]" not in out
    assert "[Pay](/intelligence/answers/element/12)" in out


def test_bold_that_covers_more_than_the_name_keeps_both_marks():
    out = _markdown("**The Ledgerpay** [ref:10] is the ledger.", [_source(10, "Ledgerpay")])
    assert out.count("**") % 2 == 0, out
    assert "[Ledgerpay](/intelligence/answers/element/10)" in out
    assert out.count("Ledgerpay") == 1, out


def test_bold_around_only_the_name_goes_with_it():
    out = _markdown("**Ledgerpay** [ref:10] is the ledger.", [_source(10, "Ledgerpay")])
    assert out == "[Ledgerpay](/intelligence/answers/element/10) is the ledger."


def test_names_with_a_dash_or_a_dot_link_once():
    out = _markdown("Uses pay-gate [ref:3] and ledger.core [ref:4].", [_source(3, "pay-gate"), _source(4, "ledger.core")])
    assert out == "Uses [pay-gate](/intelligence/answers/element/3) and [ledger.core](/intelligence/answers/element/4)."
