"""ARCH-101 — the standalone modal components must share one shell.

Eight modal/dialog components had been hand-built independently and had drifted
into three backdrop treatments (`bg-black/50`, `bg-black/80`, inline
`rgba(0,0,0,.5)`), three z-index strategies (`z-50`, inline `z-index:1000`, and
`var(--modal-z, 1000)`) and two alignments (flex-centred vs `relative top-10`).
`components/modal.html` is the canonical macro (DESIGN.md § Modals) and defines
the shell: `bg-background/80` backdrop, `z-index: var(--modal-z, 1000)` on the
root with `+1` on the panel, `role="dialog" aria-modal="true"`, and close via
`data-modal-close` / `data-modal-backdrop` / Escape (all handled by
`static/js/ui/modal.js`).

This is a static scan, not a render test. The visual result of the shell is not
assertable without a browser — that belongs to the Playwright smoke suite — but
the class/attribute contract that produces it is exactly what drifted, and that
is checkable here. Fail-first: every one of these assertions failed on at least
one file before the ARCH-101 pass.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

MODAL_FILES = [
    "app/templates/components/modal.html",
    "app/templates/components/drawer.html",
    "app/templates/components/global_search_modal.html",
    "app/templates/components/confirmation_dialog.html",
    "app/templates/components/archimate_mapping_modal.html",
    "app/templates/components/apqc_mapping_modal.html",
    "app/templates/components/unified_mapping_modal.html",
    "app/templates/application_mgmt/application_import_modal.html",
]

# The canonical backdrop, taken from components/modal.html. Semantic token, and
# compiled into app/static/css/tailwind-output.css.
CANONICAL_BACKDROP = "bg-background/80"


def _read(rel: str) -> str:
    path = _ROOT / rel
    assert path.exists(), f"{rel} is missing — update MODAL_FILES"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", MODAL_FILES)
def test_backdrop_uses_the_canonical_token(rel):
    """One backdrop treatment, and it is the semantic one."""
    src = _read(rel)
    assert CANONICAL_BACKDROP in src, f"{rel} has no {CANONICAL_BACKDROP} backdrop"
    assert "bg-black" not in src, f"{rel} still uses a raw bg-black backdrop"
    assert "rgba(" not in src, f"{rel} still uses an inline rgba() backdrop"


@pytest.mark.parametrize("rel", MODAL_FILES)
def test_z_index_comes_from_the_single_scale(rel):
    """tailwind.config.js defines no zIndex scale and `z-[1000]` is not compiled,
    so the one scale that exists is the `--modal-z` custom property used by
    components/modal.html. No component may re-introduce `z-50` or an arbitrary
    `z-[...]` on its modal chrome."""
    src = _read(rel)
    assert "var(--modal-z" in src, f"{rel} does not use the --modal-z scale"
    assert "calc(var(--modal-z, 1000) + 1)" in src, (
        f"{rel} does not raise its panel above its own backdrop"
    )
    stray = re.findall(r'class="[^"]*\bz-(?:50|\[[^\]]+\])', src)
    assert not stray, f"{rel} still carries an off-scale z-index class: {stray}"


@pytest.mark.parametrize("rel", MODAL_FILES)
def test_dialog_aria_contract(rel):
    """Screen readers need the role and the modal flag on every one of them."""
    src = _read(rel)
    assert 'aria-modal="true"' in src, f"{rel} is missing aria-modal"
    assert 'role="dialog"' in src or 'role="alertdialog"' in src, (
        f"{rel} is missing role=dialog/alertdialog"
    )
    assert "aria-label" in src or "aria-labelledby" in src, (
        f"{rel} gives its dialog no accessible name"
    )


@pytest.mark.parametrize("rel", MODAL_FILES)
def test_has_a_close_affordance(rel):
    """Esc and backdrop-click are handled centrally by ui/modal.js for anything
    carrying `data-modal-backdrop`; Alpine-driven components bind Escape
    themselves. Either way there must also be an explicit close control."""
    src = _read(rel)
    has_backdrop_close = "data-modal-backdrop" in src
    has_escape = "keydown.escape" in src
    assert has_backdrop_close or has_escape, (
        f"{rel} offers neither backdrop-click nor Escape to dismiss"
    )
    has_button = "data-modal-close" in src or "Platform.modal.close" in src or (
        "aria-label=\"Close" in src
    )
    assert has_button, f"{rel} has no explicit close button"


def test_no_native_dialog_or_console_calls_in_modal_shells():
    """DESIGN.md / CLAUDE.md: no alert()/confirm(), no console.log, no onclick=."""
    for rel in MODAL_FILES:
        src = _read(rel)
        for banned in ("console.log", "onclick=", "alert(", "confirm("):
            # Platform.modal.confirm() is the sanctioned replacement for
            # window.confirm and must not trip the substring check.
            hits = [
                m for m in re.finditer(re.escape(banned), src)
                if not src[max(0, m.start() - 15):m.start()].endswith("Platform.modal.")
            ]
            assert not hits, f"{rel} uses banned {banned}"


def test_unified_mapping_modal_zindex_is_not_a_tailwind_arbitrary_class():
    """A CSP fix once moved this modal's z-index onto Tailwind arbitrary-value
    classes (`z-[var(--modal-z,1000)]` on the root, `z-[calc(var(--modal-z,1000)+1)]`
    on the panel). Tailwind's JIT compiler does not reliably emit a usable rule
    for an arbitrary value that mixes a comma and a `+`, so the compiled
    stylesheet silently dropped the panel's raised z-index and the panel sat
    level with its own backdrop instead of above it. The working fix sets both
    z-index levels as real CSS in the nonce-scoped <style> block at the top of
    the file, and the modal's root and panel elements must never again carry
    an arbitrary z-[...] class for this. Fail-first: this failed against the
    Tailwind-arbitrary-class version of the file before the fix, and passes
    against the <style>-block version."""
    rel = "app/templates/components/unified_mapping_modal.html"
    src = _read(rel)

    root_rule = re.search(
        r"#unified-mapping-modal\s*\{\s*z-index:\s*var\(--modal-z,\s*(\d+)\)\s*;?\s*\}",
        src,
    )
    panel_rule = re.search(
        r"#unified-mapping-modal\s*\[data-modal-panel\]\s*\{\s*z-index:\s*"
        r"calc\(var\(--modal-z,\s*(\d+)\)\s*\+\s*(\d+)\)\s*;?\s*\}",
        src,
    )
    assert root_rule, (
        f"{rel}: no explicit '#unified-mapping-modal {{ z-index: var(--modal-z, N) }}' "
        "rule in a <style> block"
    )
    assert panel_rule, (
        f"{rel}: no explicit panel z-index rule "
        "('#unified-mapping-modal [data-modal-panel] { z-index: calc(...) }') "
        "in a <style> block"
    )
    assert root_rule.group(1) == panel_rule.group(1), (
        f"{rel}: the root and panel z-index rules fall back to different "
        "--modal-z values"
    )
    assert int(panel_rule.group(2)) > 0, (
        f"{rel}: the panel's z-index must be raised strictly above the root, "
        "not merely equal to it"
    )

    root_tag = re.search(r'<div id="unified-mapping-modal"[^>]*>', src)
    panel_tag = re.search(r"<div data-modal-panel\b[^>]*>", src)
    assert root_tag and "z-[" not in root_tag.group(0), (
        f"{rel}: the modal root carries a Tailwind arbitrary z-[...] class again"
    )
    assert panel_tag and "z-[" not in panel_tag.group(0), (
        f"{rel}: the modal panel carries a Tailwind arbitrary z-[...] class again"
    )
