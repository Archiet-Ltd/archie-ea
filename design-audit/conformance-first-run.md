# Conformance checker, first run (report only)

Branch `enforcement/conformance-checker` at the integration base of 20 October 2026. Nothing was fixed
and no template was changed; this is the fix list. The full report is `design-audit/conformance-report.json`
(written by the script, not committed).

## Scope
- Files scanned: 684 (`app/templates/` and every `app/modules/*/templates/`; the brief named only
  `app/templates/`, but the Ask page and seven other Tier 1 templates live in module folders).
- Tier-1 files: 27, all present (`design/tier1_routes.txt`; the brief's paths are mapped to this repository's
  layout and the originals are kept there as comments). Tier-1 files with at least one finding: 25 of 27.
- Exit codes observed: full run 1, tier-1 run 1 (tier-1 errors exist).

## Violations per rule and tier

| Rule | Severity | Tier 1 | Tier 2 | Total |
|---|---|---|---|---|
| SHADOW | error | 169 | 1,038 | 1,207 |
| ROUNDED_XL_CARD | error | 99 | 552 | 651 |
| RAW_BUTTON | error | 71 | 1,091 | 1,162 |
| RAW_TABLE | error | 22 | 340 | 362 |
| RAW_MODAL | error | 10 | 186 | 196 |
| BANNED_PATTERN | error | 2 | 0 | 2 |
| HARDCODED_HEX | warn | 0 | 48 | 48 |
| INPUT_HEIGHT | error | 21 | 196 | 217 |
| MULTI_COLUMN_FORM | error | 7 | 110 | 117 |
| NAMED_BUTTON | error | 13 | 318 | 331 |
| NAMED_INPUT | warn | 1 | 11 | 12 |
| MIXED_CURRENCY | warn | 0 | 1 | 1 |
| MATRIX_STICKY | error | 0 | 0 | 0 |
| MATRIX_LEGEND | error | 0 | 0 | 0 |
| **All** | | **415** | **3,891** | **4,306** |

Tier-1 errors (what blocks CI once the hook and step are live): 414. Tier-2 findings are reported as
warnings and do not fail.

## What the tier-1 numbers are made of
The macros on this branch are still the pre-north-star ones, so the checker reports the canonical files
themselves: `components/card.html` (rounded-xl and shadow-sm), `components/modal.html` and
`components/dialog.html` (shadow-lg, backdrop blur), `components/input.html` and `components/form_field.html`
(h-10 controls), `components/data_table.html` and `components/table.html` (no `dense-table` class). Those
fixes already exist on PRs 771 (card, shadows), 783 (input height, dialog backdrop) and 784 (dense table);
when they merge, every page that calls the macros inherits the fix and the tier-1 count falls without a
per-page edit. The five largest tier-1 files: application detail 145, capability map 64, AI chat 43,
applications list 40, capability roadmap 37.

## Top 10 files overall

| Count | File | One example |
|---|---|---|
| 198 | `archimate/partials/_composer_toolbar.html` | HARDCODED_HEX line 96: `style="background:#c8a200; border-color:#a08200;"` |
| 145 | `applications/dashboard.html` | BANNED_PATTERN line 584: `mermaid` in `class="mermaid text-sm"` |
| 122 | `applications/rationalization.html` | INPUT_HEIGHT line 1052: a text input with a non-canonical height |
| 97 | `codegen/_wb_ide.html` | INPUT_HEIGHT line 74: `class="h-7 …"` on a readonly input |
| 96 | `adm_kanban/board_v2.html` | INPUT_HEIGHT line 25: `<select … class="h-8 …">` |
| 92 | `solutions/detail.html` | NAMED_BUTTON line 147: an icon-only button with no accessible name |
| 91 | `solutions/blueprint.html` | NAMED_BUTTON line 510: an icon-only button with no accessible name |
| 73 | `archimate/composer.html` | NAMED_BUTTON line 646: breadcrumb button with no text or label |
| 65 | `codegen/_wb_right_panel.html` | INPUT_HEIGHT line 451: `<select … class="flex-1 h-7 …">` |
| 64 | `capability_map/index.html` | RAW_BUTTON line 1502: a close button with no canonical variant token |

## Rules with zero violations, and what that means
- MATRIX_STICKY and MATRIX_LEGEND: **untested, not passing.** No table in the repository carries the
  `matrix` class yet (the variant arrives with PR 786), so the rules have had nothing to match. Their
  fixtures in the test file are the only evidence they work.
- HARDCODED_HEX on tier 1: passing on the 27 files (the 48 findings are all tier 2, 198 of them inside the
  composer toolbar's inline styles).
- MIXED_CURRENCY on tier 1: passing; the one finding is a tier-2 journey step that prints both £ and $.
- BANNED_PATTERN on tier 2: zero because the rule reads class and data attributes only; Mermaid, D3 and
  Sankey usages that live in `<script>` tags or in `{% block extra_js %}` are not seen (see "not checked").

## Fix effort per rule
| Rule | Effort | Why |
|---|---|---|
| SHADOW | S on tier 1, M overall | 169 tier-1 sites fall to a handful when the card and modal macros merge (771, 783); the remaining 1,038 are the scripted class map that 771 already applies repository-wide. |
| ROUNDED_XL_CARD | S / M | Same shape: the card macro plus the class map. |
| RAW_BUTTON | M | 1,162 hand-built buttons; most carry a sensible class set but none of the six variant strings. A scripted pass maps common class sets to variants; the long tail is by hand. |
| RAW_TABLE | M | 362 tables; the 22 tier-1 ones are converted on PR 784 (dense_table); the rest follow the same converter. |
| RAW_MODAL | M | 196 dialogs toggled by `x-show` without `role="dialog"` and `aria-modal`; the modal macro conversion on 784 covers 17 of them; the rest need the same treatment. |
| BANNED_PATTERN | L | Two Mermaid diagrams on the application detail; replacing them is drawing-engine work, not a template fix. |
| HARDCODED_HEX | M | 48 inline hex colours, mostly the composer toolbar and context menus; each needs a token. |
| INPUT_HEIGHT | S / M | 217 controls; the input macro fix on 783 covers the macro callers; `h-7` and `h-8` toolbar controls (codegen, kanban) are deliberate dense controls and need a written decision or a size variant. |
| MULTI_COLUMN_FORM | M | 117 grids inside forms; the forms class map on 783 collapses 121 of them. |
| NAMED_BUTTON | M | 331 icon-only buttons without a name; each is one `aria-label`. |
| NAMED_INPUT | S | 12 controls. |
| MIXED_CURRENCY | S | One template. |
| MATRIX_* | none yet | Untested until a matrix exists. |

## Not checked, and why
- Banned patterns inside `<script>` blocks, `{% block extra_js %}` or JavaScript files: the rule reads class
  and data attributes only. The Mermaid, D3 force and Sankey usages that live in scripts (traceability,
  solution blueprint, the two Sankey partials) are known from the earlier source checker and the register
  rows, not from this run.
- Hardcoded hex inside a Jinja variable assignment (`{% set colour = '#…' %}`), a `<style>` block or a
  JavaScript string: only `class` and `style` attributes are read.
- A `<form>` split across includes or opened in one template and closed in another: MULTI_COLUMN_FORM sees
  only a `<form>…</form>` pair in one file.
- Markup built in a Jinja variable or by a macro call (`form_field(label=…)`): the macro's own control is
  reported on the macro file, not on each caller; a label passed as a macro argument is not seen as text.
- Nested HTML (a dialog whose `role` and `aria-modal` sit on different nested elements): RAW_MODAL reads the
  opening tag only. A real parser would be needed; this run does not fake it.
- Generated files and vendored assets are not scanned (`app/static/`), nor are e-mail templates' inline
  colours beyond their class and style attributes.
- MIXED_CURRENCY counts a `$` only before a digit or a Jinja value, so a currency printed by a filter that
  the template calls with a symbol argument is not seen.

## What happens next
This report is the fix list. The fix branch comes after review; nothing in this branch changes a template.
