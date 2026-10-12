"""Shared text sanitization utilities.

Functions here are used by multiple modules (AI chat, MCP tools) to prevent
untrusted content from injecting fence-like delimiters into structured output.
"""

from __future__ import annotations

import re

_FENCE_LOOKALIKE = re.compile(r"={3,}")


def neutralize_fence_lookalikes(text: str) -> str:
    """Break any byte-identical match to our own '=== BEGIN/END ... ===' fence
    syntax that might appear inside untrusted content.

    Runs of 3+ '=' are spaced apart ("===" -> "= = =") so untrusted content can
    describe or quote fence syntax (still legible) but can never produce the
    exact delimiter the real fence uses, the same way user input gets HTML-
    escaped rather than trusted not to contain "<script>".
    """
    return _FENCE_LOOKALIKE.sub(lambda m: " ".join("=" * len(m.group(0))), text)