# Build report - Q-002a: WCAG 2.2 AA audit extension

Brief: `tasks/Q-002a-wcag22-audit-extension.md` (archie-ea-four-intelligences-extension bucket).
Branch `feat/q002a-wcag22-audit-extension`, based on `origin/main` at `1cdd8c4d` (the brief names `31fbaece`; main
gained one unrelated dashboard commit since, so `git diff 1cdd8c4d..HEAD` is used wherever the brief says
`git diff 31fbaece..HEAD`). One commit, not pushed, not merged.

## Summary

The browser accessibility audit now runs the WCAG 2.2 AA tag set (the four 2.1 tags plus `wcag22aa`) at both places
that carried the list. A missing `axe-playwright-python` is a hard failure instead of a silent skip. The audit reads the
bundled axe-core version at run time, prints it, and fails if the `target-size` rule is not in the loaded rule list.
The dependency is pinned to a release whose bundled axe-core carries that rule. The eight audited pages were
re-measured under the new tag set.

Results, all observed in this environment:

- Pinned `axe-playwright-python==0.1.8`, which bundles axe-core **4.12.1** (as printed by the run; 105 rules loaded;
  `target-size` present with tags `cat.sensory-and-visual-cues, wcag22aa, wcag258`).
- The old floor (`>=0.1.4`) genuinely admitted a bundle with no such rule: `0.1.4` bundles axe-core 4.4.3, 97 rules,
  `target-size` absent. Run against it, the audit goes red (section 4).
- Re-measurement of the eight pages: **nothing new appeared**. Zero violations of any kind on all eight pages under
  the extended tag set, so the baseline's accepted set is unchanged (eight pages, zero entries); only its `_comment`
  changed, to name 2.2. `target-size` inspected 30 to 83 elements per page and passed all of them (section 5).
- With the package uninstalled and a real browser present, the audit fails (6 errors, exit 1) with a message naming
  the package (section 3).

## What this change does and does not mean

- It adds **one** automatable WCAG 2.2 rule, `target-size` (2.5.8, targets of at least 24 by 24 CSS pixels), and an
  audit that can no longer pass by not running. That is the whole of the gain.
- It is a **build-and-test standard, not a conformance claim.** It does not say the product is WCAG 2.2 AA
  conformant, that a green audit means the product is accessible, or that it replaces manual assistive-technology
  testing.
- axe-core has no rule for 2.4.11 Focus Not Obscured (Minimum), 2.5.7 Dragging Movements or 3.2.6 Consistent Help, and
  the programme's surfaces do not exercise 3.3.7 or 3.3.8. Those remain manual. The module docstring says so. **No
  automated check for them was written**: the brief classifies them as having no automatable check, and a hand-rolled
  substitute would be new product logic outside the brief's scope. If the coordinator intended a different reading of
  "manual checks for 2.4.11, 2.5.7 and 3.2.6", that is the one place this report departs from that follow-up message.
- `target-size` does not perfectly evaluate the spacing exception; edge cases need manual measurement.
- EN 301 549 v4.1.1 adopts WCAG 2.2 but is not yet the legal reference (v3.2.1 remains so until v4.1.1 is cited in
  the Official Journal). The comments say exactly that and treat it as context, not as the reason for the move.

## Files changed

Test and dependency files, two operating-note lines, and this report. Nothing under `app/`.

| File | Change |
|---|---|
| `tests/smoke/test_accessibility_audit.py` | tag set, skip-to-fail, engine version print, rule-set assertion, positive control, tag-mirror check, baseline-note check, docstring and comments |
| `tests/smoke/test_typed_arb_governance_journey.py` | local `tags` list extended with a one-line mirror comment; the docstring and two message strings that said "2.1" now say "2.2" |
| `tests/smoke/a11y_baseline.json` | regenerated through `SMOKE_A11Y_UPDATE_BASELINE=1`; only `_comment` changed |
| `requirements.txt` | the one `axe-playwright-python` line: floor to exact pin, comment names 2.2 AA |
| `CLAUDE.md` | the two operating-note lines that said the audit runs 2.1 |
| `docs/buckets/q002a-wcag22-audit-extension/build-report.md` | this report |

### Why `requirements.txt` changed

One existing line, nothing added: `axe-playwright-python>=0.1.4` became `axe-playwright-python==0.1.8`, with the comment
updated from "WCAG 2.1 AA" to "WCAG 2.2 AA". It is required by brief section D4 (pin to a specific version known to
bundle an axe-core carrying `target-size`), and it is needed because the floor admits `0.1.3`/`0.1.4`, whose bundled
axe-core 4.4.3 has no such rule. It does not change which packages are installed: `pip install -r requirements.txt`
against the old floor resolved to `0.1.8` here as well (the install log reads "Collecting axe-playwright-python>=0.1.4"
and the venv ends up on 0.1.8), so a fresh environment gets the same bytes as before. It does not add a production
dependency. It is a test-time tool, but it already sat in `requirements.txt` rather than `requirements-test.txt`, and
the CI smoke job installs only `requirements.txt` plus `playwright` and `pytest-timeout`, so moving it would break that
job; I left its placement alone (out of scope, and flagged under Known gaps). The `deployed-deps` gate handles `==`
lines (it checks installed version against the pinned floor) and passes.

### Why `CLAUDE.md` changed

Brief B1, B5 and D5 permit the operating-note lines that state the audit runs 2.1. There were exactly two: the bullet
under "Browser-based role testing standard" that names `test_accessibility_audit.py`, and the `smoke` row of the CI
jobs table. The table row is a one-word change. The bullet was reworded to say 2.2 AA is a build-and-test standard (not
a conformance claim), that once a browser is available it fails rather than skips when the package is missing or its
axe-core lacks `target-size`, and that it does not replace manual assistive-technology testing. That is B5/B6 wording
plus one sentence documenting the new failure behaviour so the note cannot contradict the code. No other line changed.

## 1. The dependency: what I found and what I pinned

Question from the brief: which axe-core does the installed package bundle, and does it carry `target-size`?

The installed version was whatever the old floor resolved to on a fresh install: `0.1.8`. To answer the question for
every version the floor admits, I downloaded each published wheel from PyPI (no repository or production access), read
the header of the bundled `axe.min.js`, and searched it for the strings `target-size` and `wcag22aa`:

| axe-playwright-python | published | bundled axe-core | contains `target-size` / `wcag22aa` |
|---|---|---|---|
| 0.1.3 | 2023-07-24 | 4.4.3 | no / no |
| 0.1.4 (the old floor) | 2023-07-25 | 4.4.3 | no / no |
| 0.1.5 | 2025-06-22 | 4.10.3 | yes / yes |
| 0.1.6 | 2025-10-24 | 4.11.0 | yes / yes |
| 0.1.7 | 2025-12-01 | 4.11.0 | yes / yes |
| **0.1.8 (pinned)** | 2026-07-24 | **4.12.1** | yes / yes |

That table is file inspection only. I **ran** two of these versions in a browser (0.1.4 and 0.1.8); I did not run
0.1.5 to 0.1.7. The runs:

```
[a11y] axe-playwright-python 0.1.4 bundles axe-core 4.4.3; 97 rules loaded; tags audited: wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa
[a11y] required rule target-size: ABSENT
```
```
[a11y] axe-playwright-python 0.1.8 bundles axe-core 4.12.1; 105 rules loaded; tags audited: wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa
[a11y] required rule target-size: loaded, tags cat.sensory-and-visual-cues,wcag22aa,wcag258
```

Pinned `0.1.8`: the newest release, and the one a fresh install of the old floor resolves to today, so the
re-measurement below describes what a fresh install gives now (I cannot see what CI's cached environments have
resolved in the past). The library exposes neither a version
attribute nor a rule-listing call; the audit injects the bundled script into a real page and asks it
(`axe.version`, `axe.getRules()`), so what is printed is what is running, not what a requirements file asked for.

## 2. Both tag sites, and the drift check

- `TAGS` in the audit is `["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]`.
- The ARB governance journey's local `tags` list is the same five entries, with a one-line comment saying it
  deliberately mirrors the audit's list.
- `test_audit_tag_set_is_wcag22_aa_and_keeps_wcag21` pins the audit's list (it can gain, not lose, a 2.1 tag).
- `test_arb_journey_tag_set_mirrors_the_audit_tag_set` reads the ARB file with `ast`, finds the single `tags = [...]`
  assignment inside the axe test, and asserts it equals `TAGS`. If it finds zero or two assignments it fails rather than
  passing vacuously. These need no browser and passed in every environment below.
- Text that said "2.1" in the ARB file (its docstring and two assertion/xfail message strings) now says 2.2, so no
  comment or message there still states the audit runs 2.1. Brief B1 says "only the tag list and any comment beside
  it"; I read acceptance item 12 (no comment or docstring may still say 2.1) as covering these three strings and
  changed only the standard's name in them.

## 3. The skip became a failure

`pytest.importorskip("axe_playwright_python.sync_playwright", ...)` at import time is gone. The import now happens in a
module-scoped `axe_module` fixture that requests the `browser` fixture first, so the tier's existing skip
(Playwright or a browser unavailable) still wins on a developer machine, and the failure can only happen once a real
browser exists - the situation the brief describes. On `ImportError` the fixture calls `pytest.fail` with a message
naming the package, saying the audit could not run, and why it fails rather than skips.

**No opt-out environment variable was added.** The brief allows one only "if needed"; none is, because the fix on a
developer machine is `pip install -r requirements.txt`, and a hatch is another way to reproduce the defect. So
acceptance item 3 holds trivially: there is no hatch to be on by default, and CI sets nothing for it. The default path
failing is the demonstration below.

Demonstration, throwaway environment (a copy of the task venv, then `pip uninstall axe-playwright-python`;
Playwright installed and importable, Chromium present, the audit run in the default environment with
`SMOKE_REQUIRE_BROWSER` unset):

```
$ python -m pytest tests/smoke/test_accessibility_audit.py -q -s -p no:randomly --timeout=900 -W ignore::DeprecationWarning -rA
_______ ERROR at setup of test_no_new_serious_or_critical_violations _________
No module named 'axe_playwright_python'
During handling of the above exception, another exception occurred:
The accessibility audit could not run: the axe-playwright-python package is not installed (No module named 'axe_playwright_python').
This fails rather than skips because a skipped test reports as passed, and a CI run could then say 'no accessibility violations' when no accessibility check ran at all.
Install it with:  pip install -r requirements.txt
...
PASSED tests/smoke/test_accessibility_audit.py::test_audit_tag_set_is_wcag22_aa_and_keeps_wcag21
PASSED tests/smoke/test_accessibility_audit.py::test_arb_journey_tag_set_mirrors_the_audit_tag_set
PASSED tests/smoke/test_accessibility_audit.py::test_every_accepted_baseline_entry_carries_a_date_and_a_reason
ERROR tests/smoke/test_accessibility_audit.py::test_the_rule_set_carries_the_wcag22_rules
ERROR tests/smoke/test_accessibility_audit.py::test_the_tag_set_flags_a_seeded_small_target
ERROR tests/smoke/test_accessibility_audit.py::test_no_new_serious_or_critical_violations
ERROR tests/smoke/test_accessibility_audit.py::test_all_violations_do_not_increase
ERROR tests/smoke/test_accessibility_audit.py::test_the_audit_actually_ran - ...
ERROR tests/smoke/test_accessibility_audit.py::test_write_baseline_when_asked
============= 3 passed, 1 warning, 6 errors in 240.93s (0:04:00) ==============
EXIT 1
```

It is reported as ERROR at setup (a fixture failure) rather than FAILED, but the run is red, exits 1, and nothing is
skipped. Then the package was reinstalled at the pinned version and the audit re-run in the same environment:

```
$ python -m pip install "axe-playwright-python==0.1.8"      ->  Successfully installed axe-playwright-python-0.1.8
[a11y] axe-playwright-python 0.1.8 bundles axe-core 4.12.1; 105 rules loaded; tags audited: wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa
[a11y] required rule target-size: loaded, tags cat.sensory-and-visual-cues,wcag22aa,wcag258
.[a11y] seeded small targets, axe-core 4.12.1: {'target-size': 2}
..[a11y] audited 8 pages under axe-core 4.12.1
[a11y] /ai-chat  target-size elements: violations=0, passes=66, incomplete=0, inapplicable=0
[a11y] /applications/  target-size elements: violations=0, passes=83, incomplete=0, inapplicable=0
[a11y] /capability-map/  target-size elements: violations=0, passes=50, incomplete=0, inapplicable=0
[a11y] /dashboard/overview  target-size elements: violations=0, passes=53, incomplete=0, inapplicable=0
[a11y] /my-applications/  target-size elements: violations=0, passes=35, incomplete=0, inapplicable=0
[a11y] /procurement/compliance  target-size elements: violations=0, passes=30, incomplete=0, inapplicable=0
[a11y] /procurement/contracts  target-size elements: violations=0, passes=33, incomplete=0, inapplicable=0
[a11y] /procurement/contracts/new  target-size elements: violations=0, passes=46, incomplete=0, inapplicable=0
================== 9 passed, 1 warning in 194.56s (0:03:14) ==================
EXIT 0
```

### The developer path still skips

With Playwright installed but no browser binary (`PLAYWRIGHT_BROWSERS_PATH` pointed at an empty directory) **and
`axe-playwright-python` still uninstalled**, so both conditions at once, in the default environment:

```
PASSED tests/smoke/test_accessibility_audit.py::test_audit_tag_set_is_wcag22_aa_and_keeps_wcag21
PASSED tests/smoke/test_accessibility_audit.py::test_arb_journey_tag_set_mirrors_the_audit_tag_set
PASSED tests/smoke/test_accessibility_audit.py::test_every_accepted_baseline_entry_carries_a_date_and_a_reason
SKIPPED [1] tests\smoke\test_accessibility_audit.py:381: chromium unavailable: BrowserType.launch: Executable doesn't exist at C:\wt\empty-playwright-browsers\chromium_headless_shell-1243\chrome-head
... (six skips in all, one per browser-dependent test)
============= 3 passed, 6 skipped, 1 warning in 112.36s (0:01:52) =============
EXIT 0
```

So a missing browser skips, and it wins over the missing library. That is the `browser` fixture's own skip in
`conftest.py`, which I did not touch.

Playwright not installed at all (uninstalled in the throwaway environment): the smoke `conftest.py` raises `Skipped`
from its module-level `importorskip`. Reached through directory collection this is reported as
`SKIPPED [4] tests\smoke\conftest.py:32: playwright not installed - smoke journeys skipped`. Named directly on the
command line (`pytest tests/smoke/test_accessibility_audit.py`) under pytest 9.1.1, that same unchanged line makes
pytest print a traceback and exit 1 instead of skipping, because the file's own conftest is loaded at start-up. That
behaviour is in a file this task does not touch and comes from before my change (the traceback originates in
`conftest.py` line 32, before the audit module is imported); it is recorded under Known gaps, not fixed.

## 4. `target-size` is asserted present, and the assertion was mutation-proved

`test_the_rule_set_carries_the_wcag22_rules` fails when `target-size` is absent from the loaded rule list (or present
but not tagged `wcag22aa`), and the `audited` fixture makes the same check before running any page, so none of the
result-reading tests can pass when the rule set could not have produced a 2.2 finding. `test_the_audit_actually_ran`
also asserts that `target-size` was reported (in violations, passes, incomplete or inapplicable) on every one of the
eight pages, and that the axe-core that produced the results is the one whose rule list was checked.

Mutation: run the audit against the real old library (`axe-playwright-python==0.1.4`, the floor the old requirement
allowed), in the throwaway environment:

```
[a11y] axe-playwright-python 0.1.4 bundles axe-core 4.4.3; 97 rules loaded; tags audited: wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa
[a11y] required rule target-size: ABSENT
================================== FAILURES ===================================
_________________ test_the_rule_set_carries_the_wcag22_rules __________________
The WCAG 2.2 rule set is not loaded:
  the bundled axe-core 4.4.3 predates the 'target-size' rule: it is absent from the loaded rule list, so the wcag22aa tag would select nothing and a clean result would say nothing about WCAG 2.2 (a false green). Raise the axe-playwright-python pin in requirements.txt to a release that bundles a newer axe-core.
________________ test_the_tag_set_flags_a_seeded_small_target _________________
E   AssertionError: two adjacent 10x10px buttons should be reported by target-size (2 nodes) under the audit's tag set, got {} - the tag set cannot catch a WCAG 2.2 target-size failure
...
FAILED tests/smoke/test_accessibility_audit.py::test_the_rule_set_carries_the_wcag22_rules
FAILED tests/smoke/test_accessibility_audit.py::test_the_tag_set_flags_a_seeded_small_target
ERROR tests/smoke/test_accessibility_audit.py::test_no_new_serious_or_critical_violations   (same message)
ERROR tests/smoke/test_accessibility_audit.py::test_all_violations_do_not_increase           (same message)
ERROR tests/smoke/test_accessibility_audit.py::test_the_audit_actually_ran                   (same message)
ERROR tests/smoke/test_accessibility_audit.py::test_write_baseline_when_asked                (same message)
======== 2 failed, 3 passed, 1 warning, 4 errors in 144.12s (0:02:24) =========
EXIT 1
```

The test id that went red: `tests/smoke/test_accessibility_audit.py::test_the_rule_set_carries_the_wcag22_rules`.
I did not also mutate the source's expected-rule set; the real old library is the stronger mutation.

## 5. Seeded violation, and the re-measurement

### A seeded violation goes red (the one new automated check)

`target-size` is the only new automated check. Two ways to show the audit fails on a real one:

1. **Permanent positive control** - `test_the_tag_set_flags_a_seeded_small_target`: two adjacent, named 10 by 10 CSS
   pixel buttons on a bare page, audited with the real tag set, must yield `target-size` with 2 nodes. (A single small
   button on its own passes through the spacing exception, so the pair is what makes it a genuine failure; I confirmed
   that by probing.) Output on the pinned version: `[a11y] seeded small targets, axe-core 4.12.1: {'target-size': 2}`.
2. **Through the real audit path** - a temporary copy of the audit module (never committed, deleted after) injected the
   same pair of buttons into `/procurement/contracts` before `axe.run`. Both ratchet tests went red:

```
E   AssertionError: 1 new or worsened serious/critical WCAG 2.2 AA violation(s):
E       /procurement/contracts: NEW target-size (serious, 2 elements; targets: button[aria-label="Seeded one"], button[aria-label="Seeded two"])
...
E   AssertionError: 1 accessibility regression(s):
E       /procurement/contracts: NEW target-size (serious, 2; targets: button[aria-label="Seeded one"], button[aria-label="Seeded two"])
FAILED tests/smoke/test_zz_seeded_mutation_tmp.py::test_no_new_serious_or_critical_violations
FAILED tests/smoke/test_zz_seeded_mutation_tmp.py::test_all_violations_do_not_increase
============= 2 failed, 7 passed, 1 warning in 226.04s (0:03:46) ==============
```

Note: `target-size` carries impact `serious`, so a real finding of it fails immediately under the unchanged
`BLOCKING = {"critical", "serious"}` until it is deliberately accepted into the baseline with a date and a reason.

### The re-measurement

The eight pages in `AUDIT` are unchanged and `BLOCKING` is unchanged. Under the extended tag set, run against the
untouched committed baseline (eight pages, zero accepted):

```
[a11y] axe-playwright-python 0.1.8 bundles axe-core 4.12.1; 105 rules loaded; tags audited: wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa
[a11y] required rule target-size: loaded, tags cat.sensory-and-visual-cues,wcag22aa,wcag258
.[a11y] seeded small targets, axe-core 4.12.1: {'target-size': 2}
[a11y] audited 8 pages under axe-core 4.12.1
================== 9 passed, 1 warning in 186.27s (0:03:06) ==================
```

Then regenerated once through the update path:

```
$ SMOKE_A11Y_UPDATE_BASELINE=1 python -m pytest tests/smoke/test_accessibility_audit.py -q -s ...
a11y baseline written: 8 page(s), 0 rule instance(s)
================== 9 passed, 1 warning in 171.97s (0:02:51) ==================
```

**Nothing new appeared.** No violation of any rule, at any impact level, on any of the eight pages, so there is no new
baseline entry and therefore no per-entry date or reason to record; the baseline's `accepted` object is unchanged.
This is an observed result, not a skipped run: the rule-set and per-page activity assertions passed, `target-size`
examined 30 to 83 elements on each page (per-page counts in section 3), and the positive control shows the same tag set
does flag a genuine failure. The only diff to `a11y_baseline.json` is its `_comment` (now names the 2.2 tag set and
says entries carry a dated reason in `accepted_notes`).

The audit ran three times in total over the eight pages under the extended tags (untouched-baseline run, the update
run, and the reinstall re-run above), each time with identical results; the brief's "once" is met by treating the
update run as the recorded measurement. The first two runs were before I added the per-page activity print and
assertion; the third and later runs use the final source.

Per-entry notes: to make "no entry without a date and a reason" enforceable and to stop regeneration erasing notes,
the baseline writer now carries existing `accepted_notes` (page, rule to `YYYY-MM-DD: reason`) across regeneration and
writes new entries without a note; `test_every_accepted_baseline_entry_carries_a_date_and_a_reason` fails until each
has a real date and a reason of at least ten characters. It currently passes vacuously because there are no entries.

No surface the programme is about to build is in the baseline or in `AUDIT` (grep for `intelligence` finds nothing in
either).

The ARB governance journey, which audits `/arb/` and a review page at 390px and 1024px with its own copy of the tag
set, was also run: its axe test recorded no findings of any kind under the extended tags at either width
(`[journey-g] axe @390px: {"/arb/": {}, "/arb/reviews/18": {}}` and the same at 1024px), and passed rather than xfailed.

## 6. Commands run and results

Environment: Windows 11, Git Bash, Python 3.12.10, a private PostgreSQL 17.5 cluster on port 5572 (own data
directory, `archie_test` database, trust authentication; the service on 5432 was not used), venv
`C:\wt\venv-archie-q002a` with `requirements.txt` plus `playwright` and `pytest-timeout`, Playwright 1.63.0, Chromium
headless shell 153.0.8010.12. The schema was created as the CI smoke job does (`flask --app manage init-db` then
`reconcile-schema`, both exit 0). Environment variables: `TEST_DATABASE_URL` and `DATABASE_URL` both
`postgresql+psycopg2://postgres@127.0.0.1:5572/archie_test`, `FLASK_CONFIG=testing`, `SMOKE_BOOT_TIMEOUT=300`, and
`SMOKE_REQUIRE_BROWSER=1` for the main-venv runs (as CI sets it) but unset for the throwaway-environment runs.

| Command | Result |
|---|---|
| `pytest tests/smoke/test_accessibility_audit.py -q -s -p no:randomly --timeout=900 ...` (untouched baseline) | 9 passed, 186.27s |
| same with `SMOKE_A11Y_UPDATE_BASELINE=1` | 9 passed, 171.97s; "a11y baseline written: 8 page(s), 0 rule instance(s)" |
| `pytest tests/smoke/test_typed_arb_governance_journey.py -q ...` (whole file) | **26 passed**, 332.15s |
| `pytest tests/smoke/test_typed_arb_governance_journey.py -k journey_g_axe -q -s ...` | 2 passed; both viewports report `{"/arb/": {}, "/arb/reviews/18": {}}` |
| audit file, throwaway env, axe uninstalled, browser present | 3 passed, 6 errors, exit 1 (section 3) |
| audit file, throwaway env, axe uninstalled, no browser binary | 3 passed, **6 skipped**, exit 0 (section 3) |
| audit file, throwaway env, `axe-playwright-python==0.1.4` | 2 failed, 3 passed, 4 errors, exit 1 (section 4) |
| audit file, throwaway env, reinstalled `==0.1.8` | 9 passed, 194.56s (section 3) |
| audit file with a temporary seeded-violation copy | 2 failed, 7 passed (section 5) |
| `python scripts/verify.py --gate docs-drift --gate deployed-deps` | `ok docs-drift [0 <= 0]`, `ok deployed-deps [0 <= 0]`, 2 passed, 0 failed, 0 skipped |
| `python scripts/verify.py` with every gate except `tests` (56 gates, run with `--gate` for each; ruff 0.16.0 as CI pins it, `TEST_DATABASE_URL` set) | 55 passed, 1 failed, 1 skipped, 23m45s (below) |

The gates the brief names, from that run:

```
  ok    compile                     5.6s
  ok    lint-core                   0.9s  [0 <= 0]
  ok    docs-drift                  0.3s
  ok    smoke-coverage-on-change    1.7s  [0 <= 0]
  ok    deployed-deps               0.9s  [0 <= 0]
```

The rest of that run, verbatim: every other gate is `ok` (including `fabricated-data`, `dead-interactions`,
`dependency-cves`, `boot-health`, `csrf-coverage`, `schema-drift`), plus:

```
  skip  css-build                   0.0s
  FAIL  nav-verified                0.2s

FAIL: nav-verified
    no audit data: run the behavioural suite with -p scripts.route_verification_audit
    -> complete the audited test run before release

1 gate(s) skipped and therefore NOT verified:
    css-build: no vendored Tailwind CLI at scripts/bin/tailwindcss[.exe]; an npm-resolved Tailwind is not byte-reproducible against the committed build (caniuse-lite floats), so this cannot be verified here

55 passed, 1 failed, 1 skipped
```

**A clean full `python scripts/verify.py` was not observed.** Two reasons, both about this run rather than this change:
`nav-verified` needs the audit data that the `tests` gate writes, and I deliberately left the `tests` gate out (it runs
the whole non-smoke suite and then the whole smoke tier, each with a one-hour ceiling; I started the unrestricted run,
saw it enter that gate, and stopped it rather than let it run for hours on a machine other work was also using).
`css-build` reports skip because the vendored Tailwind CLI is absent here. The change touches no template, style or
JavaScript, so neither is expected to move, but I have not observed them green.

## 7. Interpretations and deviations from the brief

- **Base commit**: `1cdd8c4d`, not `31fbaece`, per the coordinator; the diff check uses `1cdd8c4d..HEAD`.
- **Blocking versus baseline.** Brief B4 says record findings in the baseline, and also that serious and critical
  findings "still fail immediately rather than entering the baseline". The unchanged code fails a *new* serious finding
  until it is baselined, so I read the second sentence as "`BLOCKING` and the ratchet semantics are unchanged". No
  finding appeared, so the tension was not exercised; I flag it because `target-size` is `serious`.
- **Extra tests beyond the two the brief names.** Added: the tag-set pin, the ARB mirror check (acceptance item 1 asks
  for "a test or a static check"), the baseline date-and-reason check, the seeded-violation positive control, and the
  per-page `target-size` activity assertion. Each is small, none touches application code, and each backs an
  acceptance item or makes a clean result distinguishable from an idle rule.
- **`accepted_notes`** is a new top-level key in the baseline JSON; the reader only looks at `accepted`, so nothing
  that consumed the file changes.
- **ARB file wording**: see section 2.
- **Throwaway environment** was made by copying the task venv and then adding and removing packages in the copy, not by
  re-resolving `requirements.txt` from scratch.

## 8. Honesty section - what I could not verify, and what happened

- **The browser tier did run here.** Real PostgreSQL, real Flask server, real Chromium. Every audit result above was
  observed in this environment. Nothing was inferred from source.
- **Chromium had to be downloaded.** The venv's Playwright (1.63.0) expects a headless-shell build that was not cached
  (the cache held older builds), so I ran `python -m playwright install chromium`, which downloaded from the Playwright
  CDN. No production server or droplet was contacted; PyPI was used to install packages and download wheels.
- **`python-magic-bin` hangs on this machine and I removed it from my venv.** With `python-magic-bin` 0.4.14 installed
  beside `python-magic` 0.4.27, `import magic` hung for more than 100 seconds on Python 3.12.10 / Windows, which stalled
  `flask init-db` and the first audit attempt. `requirements.txt` says the hang is a Python 3.13 problem and installs
  the package for Windows below 3.13; on this machine it hangs on 3.12 too. I uninstalled it from my private venv only.
  No repository file changed for this. It is not relevant to the audit's result, but it means my venv differs from a
  fresh `pip install -r requirements.txt` by that one Windows-only package.
- **Noise in the output.** During the in-process app import, the run prints `Windows fatal exception: code 0x8007000e`
  with a stack from Python's WMI platform query. It is printed by the fault handler and the runs continued and passed;
  I did not investigate it.
- **Only the "absent" branch of the rule-set check was exercised** (against 0.1.4). The "present but not tagged
  `wcag22aa`" branch is written and reads correctly but no library I ran produces that state.
- **Only 0.1.4 and 0.1.8 were run.** 0.1.3, 0.1.5, 0.1.6 and 0.1.7 are characterised by inspecting the bundled script,
  not by running them.
- **Runs 1 and 2 predate the per-page activity assertion**; later runs use the final source.
- **Playwright-absent behaviour** is described in section 3; I did not change it.
- **Full `verify.py` was not run to completion**: see section 6. I did run the two smoke files that this change
  touches directly (audit file, ARB journey file), not the rest of the smoke tier or the unit suite.

## 9. Known gaps

- **The ARB journey's axe test still uses `pytest.importorskip("axe_playwright_python.sync_playwright", ...)`** a few
  lines above the tag list I changed. It has the same silent-skip defect this task closes for the main audit. The brief
  limits that file to the tag list and its comment, so I left it; it needs a one-line follow-up in the same style
  (the ARB file's tag list is already covered by the mirror check). The result above (2 passed, not skipped) is only
  true because axe is installed in this environment.
- **`axe-playwright-python` sits in `requirements.txt`** (so the production image installs it) although it is a
  test-time tool; `requirements-test.txt` exists for that. The CI smoke job installs only `requirements.txt`, so moving
  it is a separate, coordinated change.
- **Playwright-absent, file-named invocation** exits 1 with a traceback under pytest 9.1.1 (section 3): pre-existing,
  in `conftest.py`, untouched.
- **Manual WCAG 2.2 work is still owed**: 2.4.11, 2.5.7, 3.2.6 (and 3.3.7, 3.3.8 where a surface exercises them), plus
  screen-reader and keyboard testing generally. Any page fix a future `target-size` finding needs is separate work.
- The eight audited pages were measured signed in as one archetype each at 1440 by 900; nothing here says anything about
  other viewports or states for those pages beyond the ARB journey's 390px and 1024px runs.

## Open Questions

None outstanding. (Resolved by reading: the base commit, the blocking-versus-baseline wording, and the ARB file's
message strings; see section 7.)

## Handoff

Next role: **refuter**, against the fourteen acceptance items in the brief.

Gate conditions I believe are met: `tests_reported_passing` - the audit file (9 passed) and the ARB governance journey
file (26 passed) pass in a real browser tier; the failing and skipping demonstrations are recorded with their output;
the five gates the brief names (`docs-drift`, `deployed-deps`, `smoke-coverage-on-change`, `compile`, `lint-core`)
pass. The unrestricted `verify.py` was not run to completion (section 6), so "verify.py runs clean" is not claimed.
