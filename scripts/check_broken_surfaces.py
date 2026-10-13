#!/usr/bin/env python
"""Front-end surfaces that cannot work, checked against the real route table.

Every other gate proves a page renders. None of them proves the things ON that
page go anywhere. This boots the app, takes its url_map, and resolves what the
templates and scripts actually point at.

Classes, each a distinct user-visible failure:

  dead-link      href="/x" where no route matches /x. The user clicks and gets
                 a 404 on a link the product itself rendered.
  dead-fetch     fetch('/api/x') where no route matches. The request 404s, and
                 combined with a silent `if (r.ok)` guard the user sees nothing
                 at all. (check_dead_interactions.py covers the guard; this
                 covers the target.)
  swallowed      catch blocks whose entire body is empty or a lone console call.
                 The failure happened, the user was told nothing, and no
                 telemetry recorded it.
  form-no-action <form> with neither an action nor a submit handler: pressing
                 Enter reloads the page and loses what was typed.
  forbidden-ui   alert() / confirm() in shipped UI code. DESIGN.md forbids both;
                 they are unstyled, unblockable and untestable.
  orphan-page    a template that extends a layout — so it is a PAGE, not a
                 partial — that no render_template() call names and no other
                 template includes. It cannot be reached by any URL. Either
                 dead code, or a feature that was built and never wired up.

Dynamic URLs (anything containing a template expression or string
concatenation) are skipped rather than guessed at — a false positive here costs
more attention than it saves.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

import hygiene_text

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "templates"
STATIC_JS = ROOT / "app" / "static" / "js"

_SKIP_PREFIX = ("http://", "https://", "//", "mailto:", "tel:", "#", "javascript:", "data:")



def _blank_comments(text: str, jinja: bool) -> str:
    """Blank comments while preserving offsets, so line numbers stay correct.

    Every remaining false positive in this checker was a comment: JS docstrings
    in core/04-toast.js and core/07-dialog.js documenting the REPLACEMENT for
    alert()/confirm(), and Jinja `{# ... #}` blocks in components/drawer.html and
    macros/form_macros.html showing example <form> markup. Documentation that
    names an anti-pattern is not an instance of it — flagging it trains readers
    to ignore the checker, which is the only thing that makes a checker useless.

    The scan and the blanking primitive live in hygiene_text, shared with
    the checkers below that need the identical length-preserving blank;
    see that module's own docstring for why this checker's own
    character-scan comment detection stays here rather than moving to the
    regex-span detection a sibling checker uses instead.
    """
    return hygiene_text.blank_comments(text, jinja)


# The reason must be on the SAME line as the marker. With \s* the class matched
# across the newline onto the `catch` keyword itself, so a bare `swallow-ok:`
# with no reason silenced the finding — turning a reviewable exception back
# into the silent one it exists to prevent.
_SWALLOW_OK = re.compile(r"swallow-ok:[ \t]*[A-Za-z0-9]")


def _swallow_ok(text: str, start: int) -> bool:
    """True when this catch carries a reviewable `swallow-ok: <reason>` marker.

    A catch that is deliberately silent and one nobody looked at are
    indistinguishable from the outside — that is the entire defect this class
    exists to find. Prose in a comment does not fix that: it reads as intent to
    a human and as neglect to the checker, so a fully triaged file still
    counted, and the gate could only ever be a ratchet.

    This is the same per-line hatch the rest of the repo uses (`fabricated-ok:`,
    `air-gap-ok`, `tenancy-ok:`): it makes the exception explicit and greppable
    rather than silent, and it puts the reason next to the code instead of in a
    review comment nobody will find again.

    The marker is accepted inside the catch body or on the line above it, so
    both of these count:

        catch (e) { /* swallow-ok: localStorage throws in private mode */ }

        // swallow-ok: optional telemetry; a failure here must not surface
        catch (e) {}
    """
    line_start = text.rfind("\n", 0, start) + 1
    prev_start = text.rfind("\n", 0, line_start - 1) + 1
    body_end = text.find("}", start)
    body_end = len(text) if body_end == -1 else body_end + 1
    return bool(_SWALLOW_OK.search(text[prev_start:body_end]))


def _route_matcher():
    """Return a predicate that says whether a concrete path resolves to a route."""
    os.environ.setdefault("FLASK_CONFIG", "testing")
    sys.path.insert(0, str(ROOT))
    try:
        from app import create_app

        app = create_app("testing")
    except Exception as exc:  # noqa: BLE001
        # Say WHY, loudly, instead of dying in a traceback. This gate resolves
        # URLs against the app's real url_map, so it cannot run anywhere the app
        # cannot be imported - and when it was mistakenly tagged `static` it
        # crashed in CI's dependency-free job on every run for a week. An opaque
        # traceback reads as "the gate found something"; it took reading the CI
        # log to see the gate had never run at all.
        raise SystemExit(
            "check_broken_surfaces cannot run: the Flask app failed to import "
            "or boot (%s: %s).\n"
            "This gate needs the application dependencies installed and a "
            "bootable app - it belongs in a job that runs `pip install -r "
            "requirements.txt`, alongside boot-health. It is NOT a static "
            "gate." % (type(exc).__name__, str(exc)[:200])
        )
    rules = list(app.url_map.iter_rules())

    # Convert each rule into a regex. <path:x> matches slashes; every other
    # converter does not. Collapsing both to [^/]+ made every /static/vendor/...
    # link look dead when the file was there all along — a scanner that cries
    # wolf costs more attention than it saves, so the distinction matters.
    patterns = []
    for rule in rules:
        pat = re.escape(rule.rule)
        pat = re.sub(r"\<path:[^>]+\>", ".+", pat)
        pat = re.sub(r"\<[^>]+\>", "[^/]+", pat)
        patterns.append(re.compile("^" + pat.rstrip("/") + "/?$"))

    def resolves(path: str) -> bool:
        p = path.split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
        return any(rx.match(p) for rx in patterns)

    return resolves, len(rules)


def _is_dynamic(url: str) -> bool:
    return any(t in url for t in ("{{", "{%", "${", "' +", '" +', "+ '", '+ "', "<%"))


def scan() -> dict[str, list[str]]:
    resolves, _n = _route_matcher()
    out: dict[str, list[str]] = {k: [] for k in
                                ("dead-link", "dead-fetch", "swallowed",
                                 "form-no-action", "forbidden-ui", "orphan-page",
                                 "silent-empty")}

    html = sorted(TEMPLATES.rglob("*.html"))
    js = sorted(STATIC_JS.rglob("*.js"))

    href_re = re.compile(r'href\s*=\s*"([^"]+)"')
    fetch_re = re.compile(r"""fetch\(\s*['"]([^'"]+)['"]""")
    fetch_tpl_re = re.compile(r"fetch\(\s*`([^`]+)`")
    form_re = re.compile(r"<form\b([^>]*)>", re.I)
    # The comment and console-call groups must not cross a brace. With re.S and
    # a lazy .*? they could reach a LATER */ further down the file, matching a
    # catch that plainly does report the failure — composer.js:1781 calls
    # _toast('error', ...) inside a commented catch and was reported as silent.
    swallow_re = re.compile(
        r"catch\s*\([^)]*\)\s*\{\s*(?:/\*[^{}]*?\*/\s*)?"
        r"(?:console\.\w+\([^;{}]*\);?\s*)?\}")
    # The statement form above is only half of it. A promise chain's handler is
    # an EXPRESSION — .catch(() => {}) and .catch(function () {}) — and this
    # class was blind to all of them, so the gate read zero while 13 fully
    # silent catches shipped. Same defect, different syntax.
    swallow_expr_re = re.compile(
        r"\.catch\(\s*(?:\([^)]*\)|\w+)\s*=>\s*\{\s*(?:/\*[^{}]*?\*/\s*)?"
        r"(?:console\.\w+\([^;{}]*\);?\s*)?\}\s*\)"
        r"|"
        r"\.catch\(\s*function\s*\([^)]*\)\s*\{\s*(?:/\*[^{}]*?\*/\s*)?"
        r"(?:console\.\w+\([^;{}]*\);?\s*)?\}\s*\)")
    # ── silent-empty ────────────────────────────────────────────────────────
    # The `swallowed` classes above find handlers that do NOTHING. This finds
    # handlers that do something worse: hand back [] / {} / null / 0, which the
    # UI then renders as data. An empty list is a legitimate answer on most
    # screens - "no results", "nothing blocking this retirement" - so a 500
    # reads to the user as a fact about their portfolio.
    _EMPTY = r"(?:\[\s*\]|\{\s*\}|null|0|''|\"\")"
    silent_empty_res = [
        re.compile(r"catch\s*\([^)]*\)\s*\{[^{}]{0,200}?\breturn\s+" + _EMPTY + r"\s*;?\s*\}", re.S),
        re.compile(r"\.catch\(\s*(?:\([^)]*\)|\w+)\s*=>\s*\(?" + _EMPTY + r"\)?\s*\)"),
        re.compile(r"\.catch\(\s*function\s*\([^)]*\)\s*\{\s*return\s+" + _EMPTY + r"\s*;?\s*\}\s*\)", re.S),
        re.compile(r"if\s*\(\s*![\w.]*\.?ok\s*\)\s*(?:\{\s*)?return\s+" + _EMPTY + r"\s*;?", re.S),
        re.compile(r"\.ok\s*\?[^;:]{0,80}:\s*" + _EMPTY),
    ]
    # If the failure is reported anywhere near the handler, it is not silent.
    # This is what separates `resp.json().catch(() => ({}))` - parsing an error
    # BODY before toasting err.error, which is correct - from a load path that
    # swallows the error and returns an empty list.
    # swallow-ok is deliberately NOT in this alternation: a marker is matched
    # precisely by _swallow_ok() against the handler itself. Folding it in here
    # would let a marker anywhere in a 600-character window silence an
    # unrelated finding further down the same function.
    # Assignment to ANY identifier ending in Error/error counts as reporting.
    # \berror\b missed the common case: suggestionsError, relError, loadError
    # and this.errorMsg are how these components record a failure for the
    # template to render, and every one of them was reported as silent.
    reports_re = re.compile(
        r"toast|throw\b|console\.(error|warn)|"
        r"[A-Za-z_$]*[Ee]rror[A-Za-z_$]*\s*[=:]|"
        # Consuming the server's own error message - `err.error || 'Request
        # failed.'` - is the failure being surfaced, even when it lands
        # somewhere with no "error" in its name. The chat panel assigns it to
        # assistantMsg.content, which no identifier pattern can recognise.
        r"\.error\s*\|\|", re.I)

    forbidden_re = re.compile(r"(?<![\w.])(alert|confirm)\s*\(")
    # A DEFINITION named alert/confirm is the replacement for the native call,
    # not an instance of it. Both remaining findings were the cure, not the
    # disease: components/alert.html is `{% macro alert(variant=...) %}`, the
    # macro that replaces native alerts, and alpine-architecture.js is
    # `confirm() { ... }`, the modal method Platform hands callers instead of
    # window.confirm. A definition is followed by `) {`; a call is not.
    definition_re = re.compile(r"(?:\bmacro\s+|\bfunction\s+)?(?:alert|confirm)"
                               r"\s*\([^()]*\)\s*(?:\{|%\})")

    def rel(p): return p.relative_to(ROOT).as_posix()
    def line_of(text, idx): return text[:idx].count("\n") + 1

    for path in html:
        text = _blank_comments(path.read_text(encoding="utf-8", errors="ignore"), jinja=True)
        for m in href_re.finditer(text):
            url = m.group(1).strip()
            if not url or url.startswith(_SKIP_PREFIX) or _is_dynamic(url):
                continue
            if not url.startswith("/"):
                continue
            if not resolves(url):
                out["dead-link"].append(
                    "%s:%d: dead-link: href=\"%s\" matches no route — the product "
                    "renders a link to its own 404" % (rel(path), line_of(text, m.start()), url))
        for m in form_re.finditer(text):
            attrs = m.group(1)
            if "action=" in attrs or "@submit" in attrs or "onsubmit" in attrs:
                continue
            if 'id="' not in attrs:      # a handler may bind by id; only flag anonymous forms
                out["form-no-action"].append(
                    "%s:%d: form-no-action: <form> with no action and no submit handler — "
                    "Enter reloads the page and discards the input"
                    % (rel(path), line_of(text, m.start())))

    for path in html + js:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        text = _blank_comments(raw, jinja=path.suffix == ".html")
        for m in fetch_re.finditer(text):
            url = m.group(1).strip()
            if not url.startswith("/") or _is_dynamic(url):
                continue
            # The literal may be only the PREFIX of a concatenated URL:
            #     fetch('/ai-chat/approvals/' + id + '/approve')
            # _is_dynamic sees only the captured substring, in which the `+`
            # does not appear — so every such call was reported as a dead route
            # at its bare prefix. Look past the closing quote instead.
            after = text[m.end():m.end() + 12].lstrip()
            if after.startswith(("+", "$", "`")):
                continue
            if not resolves(url):
                out["dead-fetch"].append(
                    "%s:%d: dead-fetch: fetch(\"%s\") matches no route — the request 404s"
                    % (rel(path), line_of(text, m.start()), url))

        # Backtick template literals were invisible to the pattern above, which
        # only matches quoted strings — so all 254 of them went unchecked, and
        # 10 pointed at no route at all. One pair was admin/ai_corrections.html
        # calling /api/archimate-elements/<id>/correct when the route is
        # /dashboard/api/... (the application_mgmt blueprint carries a
        # url_prefix); every AI correction submitted from that screen 404d.
        #
        # Substituting a plausible segment for each interpolation is what makes
        # them resolvable: ${id} and a Jinja {{ id }} both stand in for one
        # path segment. Jinja must be substituted too, or a server-rendered id
        # is left in the probe as literal braces and never matches.
        for m in fetch_tpl_re.finditer(text):
            raw_url = m.group(1).strip()
            if not raw_url.startswith("/"):
                continue
            probe = re.sub(r"\$\{[^}]*\}", "1", raw_url)
            probe = re.sub(r"\{\{[^}]*\}\}", "1", probe)
            if "${" in probe or "{{" in probe:
                continue                      # nested braces — cannot resolve
            if not resolves(probe):
                out["dead-fetch"].append(
                    "%s:%d: dead-fetch: fetch(`%s`) matches no route — the request 404s"
                    % (rel(path), line_of(text, m.start()), raw_url))
        # Deliberately the RAW text, not the comment-blanked copy: a catch
        # containing only a comment is DOCUMENTED intent, which is materially
        # different from a bare `catch {}`. Blanking the comment first turned
        # every considered no-op into an ignored one and inflated this by 27.
        # Third-party code is not ours to annotate or fix. Editing a vendored
        # minified bundle to satisfy our own checker would break its SRI hash,
        # which the `sri` gate would then fail — the fix and the gate would be
        # in direct conflict.
        if "/static/js/vendor/" not in rel(path):
            seen_lines = set()
            for pat in (swallow_re, swallow_expr_re):
                for m in pat.finditer(raw):
                    if _swallow_ok(raw, m.start()):
                        continue
                    ln = line_of(text, m.start())
                    if ln in seen_lines:
                        continue          # both forms can match one construct
                    seen_lines.add(ln)
                    out["swallowed"].append(
                        "%s:%d: swallowed: catch block tells neither the user nor the logs"
                        % (rel(path), ln))
        # Network paths only. A JSON.parse of our own server-rendered data
        # attribute failing is a different thing and legitimately silent.
        if "/static/js/vendor/" not in rel(path) and "fetch(" in raw:
            se_lines = set()
            for pat in silent_empty_res:
                # Match against the comment-BLANKED copy. Unlike `swallowed` -
                # where a comment inside a catch is documented intent and must
                # be read - a comment merely QUOTING `r.ok ? r.json() : {}` to
                # explain the anti-pattern is not an instance of it, and was
                # being reported as one. Offsets are preserved, so line numbers
                # and the marker lookup below stay correct.
                for m in pat.finditer(text):
                    if _swallow_ok(raw, m.start()):
                        continue
                    ctx = text[max(0, m.start() - 300):m.end() + 300]
                    if reports_re.search(ctx):
                        continue
                    ln = line_of(text, m.start())
                    if ln in se_lines:
                        continue
                    se_lines.add(ln)
                    out["silent-empty"].append(
                        "%s:%d: silent-empty: a failed request returns an empty value the "
                        "UI renders as data" % (rel(path), ln))

        for m in forbidden_re.finditer(text):
            if definition_re.match(text, m.start()):
                continue
            out["forbidden-ui"].append(
                "%s:%d: forbidden-ui: %s() — DESIGN.md requires Platform.toast/modal"
                % (rel(path), line_of(text, m.start()), m.group(1)))


    # ── orphan-page ─────────────────────────────────────────────────────────
    # Extending a layout is what distinguishes a page from a partial or a macro
    # file. A page nothing renders and nothing includes has no URL at all.
    #
    # Verified against dynamic dispatch before trusting this: the whole codebase
    # contains exactly ONE render_template() with a variable template name
    # (solution_narrative_service.py, for narrative documents), so a template
    # cannot be reached by indirection that this check cannot see.
    py_src = "".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in (ROOT / "app").rglob("*.py")
    )
    tpl_src = "".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in TEMPLATES.rglob("*.html")
    )
    extends_layout = re.compile(r"{%-?\s*extends\s+['\"]layouts/")
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not extends_layout.search(text):
            continue                      # partial, macro file, or component
        name = path.relative_to(TEMPLATES).as_posix()
        if name in py_src or name in tpl_src:
            continue                      # rendered by a route, or included
        out["orphan-page"].append(
            "%s:1: orphan-page: extends a layout but no route renders it and no "
            "template includes it — the page has no URL" % path.relative_to(ROOT).as_posix())

    return {k: sorted(set(v)) for k, v in out.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", action="store_true")
    ap.add_argument("--kind")
    args = ap.parse_args()

    found = scan()
    if args.kind:
        found = {args.kind: found.get(args.kind, [])}

    total = sum(len(v) for v in found.values())
    if args.count:
        print(total)
        return 0
    for kind, items in found.items():
        if not items:
            continue
        print("\n=== %s (%d) ===" % (kind, len(items)))
        for i in items:
            print("  " + i)
    print("\n%d broken surface(s)." % total)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
