"""Every progress bar says what it measures (brief a11y01b, D5).

components/progress.html names a bar "Progress" when its caller gives no label. On a page with
several bars, a screen-reader user then hears the same name for each. Two checks keep that out:
every caller of the progress macros passes a label, and a page that renders two bars with the
same accessible name fails. The browser proof is the screen-reader tree dump of each caller.
"""

import os
import re

import pytest

TEMPLATES = os.path.join(os.path.dirname(__file__), os.pardir, "app", "templates")
MACRO_FILES = ("components/progress.html", "basecoat/progress.html")
MACRO_NAMES = {"progress", "progress_dynamic", "progress_bound"}
BAR = re.compile(r"<[^>]*role=\"progressbar\"[^>]*>", re.S)


def duplicate_progress_names(html):
    """Accessible names shared by two or more progress bars in `html`."""
    seen, repeated = set(), []
    for tag in BAR.findall(html):
        label = re.search(r'(?::)?aria-label="([^"]*)"', tag)
        name = label.group(1) if label else ""
        if name in seen and name not in repeated:
            repeated.append(name)
        seen.add(name)
    return repeated


def _templates():
    for root, _dirs, files in os.walk(TEMPLATES):
        for name in sorted(files):
            if name.endswith(".html"):
                path = os.path.join(root, name)
                yield os.path.relpath(path, TEMPLATES).replace(os.sep, "/"), path


def _call_args(source, start):
    """The text between the parentheses opening at `start`, balanced; None when unbalanced."""
    depth, quote = 0, None
    for i in range(start, len(source)):
        ch = source[i]
        if quote:
            if ch == quote and source[i - 1] != "\\":
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return source[start + 1:i]
    return None


def _progress_callers():
    """[(template, line, call text)] for every call of a progress macro outside the macro files."""
    found = []
    for rel, path in _templates():
        if rel in MACRO_FILES:
            continue
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        names = set()
        for m in re.finditer(r"\{%-?\s*from\s+['\"](?:components|basecoat)/progress\.html['\"]\s+import\s+([^%]+?)\s*-?%\}", source):
            for part in m.group(1).split(","):
                bits = part.strip().split(" as ")
                if bits[0].strip() in MACRO_NAMES:
                    names.add(bits[-1].strip())
        for m in re.finditer(r"\{%-?\s*import\s+['\"](?:components|basecoat)/progress\.html['\"]\s+as\s+(\w+)", source):
            names.update("%s.%s" % (m.group(1), n) for n in MACRO_NAMES)
        for name in names:
            for m in re.finditer(r"(?<![\w.])%s\(" % re.escape(name), source):
                if source[max(0, m.start() - 12):m.start()].lstrip().endswith("macro"):
                    continue
                args = _call_args(source, m.end() - 1)
                line = source.count("\n", 0, m.start()) + 1
                found.append((rel, line, args))
    return found


def test_the_walk_finds_the_known_progress_callers():
    templates = {rel for rel, _l, _a in _progress_callers()}
    for known in ("framework_config/instance_details.html", "batch_import/job_detail.html",
                  "integration/instance_detail.html", "implementation_planning/work_package_detail.html"):
        assert known in templates, "the walk did not find %s, so it is not reading the callers" % known


def test_every_progress_caller_says_what_its_bar_measures():
    unnamed = [
        "%s:%d" % (rel, line) for rel, line, args in _progress_callers()
        if args is None or not re.search(r"\blabel(_expr)?\s*=|aria-label", args)
    ]
    assert not unnamed, (
        "%d progress bar call(s) give no label, so the bar is named \"Progress\" for a screen reader: %s"
        % (len(unnamed), ", ".join(unnamed)))


def test_basecoat_progress_callers_name_their_bar():
    unnamed = []
    for rel, path in _templates():
        if rel.startswith("basecoat/"):
            continue
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        for m in re.finditer(r"(?:call\s+|\{\{\s*)(?:ui\.)?progress\(", source):
            args = _call_args(source, m.end() - 1)
            if rel in {r for r, _l, _a in _progress_callers()}:
                continue          # a components/progress.html caller, checked above
            if args is None or "aria-label" not in args:
                unnamed.append("%s:%d" % (rel, source.count("\n", 0, m.start()) + 1))
    assert not unnamed, "basecoat progress calls with no aria-label: %s" % ", ".join(unnamed)


def _render(app, source, **ctx):
    with app.test_request_context("/"):
        return app.jinja_env.from_string(source).render(**ctx)


def test_two_bars_with_the_same_name_are_reported(app):
    html = _render(app, "{% from 'components/progress.html' import progress %}{{ progress(value=1) }}{{ progress(value=2) }}")
    assert duplicate_progress_names(html) == ["Progress"]


def test_bars_named_for_what_they_measure_are_not_reported(app):
    html = _render(
        app,
        "{% from 'components/progress.html' import progress, progress_bound %}"
        "{{ progress(value=1, label='Data quality score') }}{{ progress(value=2, label='Compliance score') }}"
        "{{ progress_bound('3', label_expr=\"'Confidence for ' + element.name\") }}",
    )
    assert duplicate_progress_names(html) == []
    assert ':aria-label="&#39;Confidence for &#39; + element.name"' in html
