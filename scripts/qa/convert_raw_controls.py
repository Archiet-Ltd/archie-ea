#!/usr/bin/env python3
"""Deterministic converter: hand-written HTML controls → component macros.

Converts raw <button>, <input>, <select> and <textarea> tags in Jinja2
templates into the existing component macros (button, input, native_select,
textarea) where the mapping is one-to-one.  Hidden inputs (type="hidden")
are left alone.

Behaviour-preservation rules (the defects the first pass introduced, and that
this pass fixes):

* Every attribute the macro cannot carry as a first-class keyword is passed
  through ``attrs_dict`` untouched — ``id``, ``name``, ``form``, ``formaction``,
  ``value`` and any ``@event.modifier`` / ``x-*`` / ``:attr`` / ``data-*``.
* A Jinja ``{{ expr }}`` inside an attribute value becomes the bare expression,
  and a value that mixes text with ``{{ expr }}`` becomes a ``~`` concatenation,
  so it is never rendered as the literal string ``{{ expr }}``.
* A lucide icon inside a button is preserved through the macro's ``icon``
  parameter (or the call-block form when the inner markup is complex).
* A ``<button>`` with no ``type`` keeps the HTML default of ``submit``.
* ``on_click`` is used only for a plain ``@click``; ``@click.stop`` and
  friends pass through ``attrs_dict`` with their modifier intact.

Controls with attributes the macro cannot express are left alone and listed.

Usage:
    python scripts/qa/convert_raw_controls.py app/templates/vendors/  # convert
    python scripts/qa/convert_raw_controls.py app/templates/vendors/ --dry-run  # report only
    python scripts/qa/convert_raw_controls.py --dry-run  # report for whole tree
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TPL_ROOT = ROOT / "app" / "templates"

# ---------------------------------------------------------------------------
# Attribute helpers
# ---------------------------------------------------------------------------

# Attributes the button macro accepts as first-class keyword arguments, keyed
# by the HTML attribute name they come from.  `@click` is handled separately
# (only a plain `@click` becomes on_click).
BUTTON_STRING_ATTRS = {
    "class": "class",
    "aria-label": "aria_label",
    "data-action": "data_action",
    "data-testid": "testid",
}
BUTTON_FLAG_ATTRS = {"disabled": "disabled"}

# Attributes the input macro accepts as first-class keyword arguments.
INPUT_STRING_ATTRS = {
    "id": "id",
    "name": "name",
    "placeholder": "placeholder",
    "value": "value",
    "class": "class",
    "inputmode": "inputmode",
    "pattern": "pattern",
    "autocomplete": "autocomplete",
    "maxlength": "maxlength",
    "aria-label": "aria_label",
    "aria-describedby": "aria_describedby",
}
INPUT_FLAG_ATTRS = {"disabled": "disabled", "required": "required", "autofocus": "autofocus"}

# Attributes the native_select macro accepts as first-class keyword arguments.
SELECT_STRING_ATTRS = {
    "id": "id",
    "name": "name",
    "class": "class",
    "aria-label": "aria_label",
}
SELECT_FLAG_ATTRS = {"disabled": "disabled", "multiple": "multiple"}

# Attributes the textarea macro accepts as first-class keyword arguments.
TEXTAREA_STRING_ATTRS = {
    "id": "id",
    "name": "name",
    "placeholder": "placeholder",
    "class": "class",
    "aria-label": "aria_label",
    "aria-describedby": "aria_describedby",
}
TEXTAREA_FLAG_ATTRS = {"disabled": "disabled", "required": "required"}


def _parse_attrs(attrs_str: str) -> list[tuple[str, str]]:
    """Parse HTML attributes from a tag string into (name, value) pairs.

    Handles single-quoted, double-quoted and unquoted values, plus Jinja
    {{ }} expressions inside attribute values.
    """
    attrs = []
    pattern = r'''
        ([@:]*[\w][\w:.-]*)            # attribute name (may start with @ or :, may contain : or dots)
        (?:
            \s*=\s*
            (?:
                "([^"\\]*(?:\\.[^"\\]*)*)"   # double-quoted
                |
                '([^'\\]*(?:\\.[^'\\]*)*)'   # single-quoted
                |
                ([^\s>]+)                     # unquoted
            )
        )?
    '''
    for m in re.finditer(pattern, attrs_str, re.VERBOSE):
        name = m.group(1)
        value = m.group(2) or m.group(3) or m.group(4) or ""
        attrs.append((name, value))
    return attrs


def _jinja_escape(value: str) -> str:
    """Escape a value for use in a Jinja string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _quote_literal(s: str) -> str:
    """Quote a plain string for use as a Jinja string literal."""
    if "'" in s and '"' not in s:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return "'" + _jinja_escape(s) + "'"


def _jinja_value(value: str) -> str:
    """Turn a raw attribute value into a Jinja expression.

    A plain value becomes a string literal.  A value that is exactly one
    ``{{ expr }}`` becomes the bare expression.  A value mixing literal text
    and ``{{ expr }}`` becomes a ``~`` concatenation, so the template renders
    the evaluated expression rather than the literal ``{{ ... }}`` braces.
    """
    if "{{" not in value and "{%" not in value:
        return _quote_literal(value)

    # A value that is exactly one {{ expr }} becomes the bare expression.
    stripped = value.strip()
    if stripped.startswith("{{") and stripped.endswith("}}"):
        inner = stripped[2:-2].strip()
        if "{{" not in inner and "{%" not in inner:
            return inner

    parts = re.split(r"(\{\{.*?\}\}|\{%.*?%\})", value)
    segments = []
    for part in parts:
        if not part:
            continue
        if part.startswith("{{") and part.endswith("}}"):
            segments.append(part[2:-2].strip())
        elif part.startswith("{%") and part.endswith("%}"):
            # A statement tag inside an attribute value is not an expression;
            # keep it as literal text (this never occurs in practice).
            segments.append(_quote_literal(part))
        else:
            segments.append(_quote_literal(part))
    if not segments:
        return "''"
    return "(" + " ~ ".join(segments) + ")"


def _build_attrs_dict(attrs: list[tuple[str, str]]) -> str:
    """Build a Jinja ``attrs_dict={...}`` literal from parsed attributes."""
    items = []
    for name, value in attrs:
        if not name:
            continue
        items.append(f"'{_jinja_escape(name)}': {_jinja_value(value)}")
    if not items:
        return ""
    return "{" + ", ".join(items) + "}"


# ---------------------------------------------------------------------------
# Button inner-content helpers
# ---------------------------------------------------------------------------

ICON_TAG = re.compile(r"<i\b[^>]*\bdata-lucide\s*=\s*[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)


def _extract_button_icon(inner_html: str) -> str | None:
    """Return the lucide icon name of the first <i data-lucide> in inner_html."""
    m = ICON_TAG.search(inner_html)
    return m.group(1) if m else None


def _is_simple_button_inner(inner_html: str) -> bool:
    """True when the inner markup is text plus at most one lucide icon.

    Anything else (nested tags, or text that itself carries a Jinja
    expression) must be preserved verbatim through the call-block form.
    """
    stripped = ICON_TAG.sub("", inner_html)
    if re.search(r"<[a-z][^>]*>", stripped, re.IGNORECASE):
        return False
    text = re.sub(r"<[^>]+>", "", stripped).strip()
    return "{{" not in text and "{%" not in text


def _extract_button_text(inner_html: str) -> str:
    """Extract the visible static text from a button's inner HTML."""
    text = re.sub(r"<[^>]+>", "", inner_html)
    text = text.strip()
    if not text or text.startswith("{{") or text.startswith("{%"):
        return ""
    return text


# ---------------------------------------------------------------------------
# Tag converters
# ---------------------------------------------------------------------------

def _convert_button(match: re.Match) -> str | None:
    """Convert a <button> tag to a {{ button(...) }} call (or call block)."""
    full_tag = match.group(0)
    tag_match = re.match(
        r'<button\s+([^>]*)>(.*?)</button>',
        full_tag,
        re.DOTALL | re.IGNORECASE,
    )
    if not tag_match:
        return None

    attrs_str = tag_match.group(1).strip()
    inner_html = tag_match.group(2).strip()

    attrs = _parse_attrs(attrs_str)
    attr_dict = dict(attrs)

    icon = _extract_button_icon(inner_html)
    text_content = _extract_button_text(inner_html)
    has_complex_inner = not _is_simple_button_inner(inner_html)

    kwargs = {}
    # The HTML default type for a <button> is submit; keep it.
    kwargs["type"] = attr_dict.get("type") or "submit"

    for attr, kwarg in BUTTON_STRING_ATTRS.items():
        if attr in attr_dict:
            kwargs[kwarg] = attr_dict[attr]

    if "disabled" in attr_dict:
        kwargs["disabled"] = True

    # A plain @click becomes on_click; @click.modifier stays in attrs_dict.
    if "@click" in attr_dict:
        kwargs["on_click"] = attr_dict["@click"]

    if icon and not has_complex_inner:
        kwargs["icon"] = icon

    # Everything not mapped above passes through attrs_dict.
    handled = set(BUTTON_STRING_ATTRS) | set(BUTTON_FLAG_ATTRS) | {"type", "@click"}
    remaining = [(n, v) for (n, v) in attrs if n not in handled]
    attrs_dict = _build_attrs_dict(remaining)
    if attrs_dict:
        kwargs["attrs_dict"] = attrs_dict

    args = []
    if not has_complex_inner and text_content:
        args.append(_quote_literal(text_content))
    args.extend(_render_button_kwargs(kwargs))
    macro_args = ", ".join(args)

    if has_complex_inner:
        return f"{{% call button({macro_args}) %}}{inner_html}{{% endcall %}}"
    return "{{ button(" + macro_args + ") }}"


def _render_button_kwargs(kwargs: dict) -> list[str]:
    order = ["type", "class", "disabled", "icon", "on_click",
             "aria_label", "data_action", "testid", "attrs_dict"]
    parts = []
    for key in order:
        if key not in kwargs:
            continue
        value = kwargs[key]
        if key == "disabled":
            parts.append("disabled=true")
        elif key == "attrs_dict":
            parts.append(f"attrs_dict={value}")
        else:
            parts.append(f"{key}={_jinja_value(value)}")
    return parts


def _convert_input(match: re.Match) -> str | None:
    """Convert an <input> tag to a {{ input(...) }} call."""
    full_tag = match.group(0)
    tag_match = re.match(r'<input\s+([^>]*)>', full_tag, re.DOTALL | re.IGNORECASE)
    if not tag_match:
        return None

    attrs_str = tag_match.group(1).strip()
    attrs = _parse_attrs(attrs_str)
    attr_dict = dict(attrs)

    # Leave hidden inputs alone.
    if attr_dict.get("type") == "hidden":
        return None

    kwargs = {}
    for attr, kwarg in INPUT_STRING_ATTRS.items():
        if attr in attr_dict:
            kwargs[kwarg] = attr_dict[attr]

    # type defaults to "text" in both HTML and the macro; emit only when set
    # to something else so the intent is explicit.
    if attr_dict.get("type") and attr_dict["type"] != "text":
        kwargs["type"] = attr_dict["type"]

    for attr in INPUT_FLAG_ATTRS:
        if attr in attr_dict:
            kwargs[INPUT_FLAG_ATTRS[attr]] = True

    handled = set(INPUT_STRING_ATTRS) | set(INPUT_FLAG_ATTRS) | {"type"}
    remaining = [(n, v) for (n, v) in attrs if n not in handled]
    attrs_dict = _build_attrs_dict(remaining)
    if attrs_dict:
        kwargs["attrs_dict"] = attrs_dict

    return "{{ input(" + ", ".join(_render_input_kwargs(kwargs)) + ") }}"


def _render_input_kwargs(kwargs: dict) -> list[str]:
    order = ["id", "name", "type", "placeholder", "value", "disabled",
             "required", "class", "aria_label", "aria_describedby",
             "inputmode", "pattern", "autocomplete", "maxlength",
             "autofocus", "attrs_dict"]
    parts = []
    for key in order:
        if key not in kwargs:
            continue
        value = kwargs[key]
        if key in ("disabled", "required", "autofocus"):
            parts.append(f"{key}=true")
        elif key == "attrs_dict":
            parts.append(f"attrs_dict={value}")
        else:
            parts.append(f"{key}={_jinja_value(value)}")
    return parts


def _convert_select(match: re.Match) -> str | None:
    """Convert a <select> tag to a {% call native_select(...) %}...{% endcall %}."""
    full_tag = match.group(0)
    tag_match = re.match(
        r'<select\s+([^>]*)>(.*?)</select>',
        full_tag,
        re.DOTALL | re.IGNORECASE,
    )
    if not tag_match:
        return None

    attrs_str = tag_match.group(1).strip()
    inner_html = tag_match.group(2).strip()

    attrs = _parse_attrs(attrs_str)
    attr_dict = dict(attrs)

    kwargs = {}
    for attr, kwarg in SELECT_STRING_ATTRS.items():
        if attr in attr_dict:
            kwargs[kwarg] = attr_dict[attr]

    for attr in SELECT_FLAG_ATTRS:
        if attr in attr_dict:
            kwargs[SELECT_FLAG_ATTRS[attr]] = True

    # The macro's `size` is the control height (sm/xs); the HTML size (list
    # box rows) maps to `visible_rows`.
    if attr_dict.get("size"):
        kwargs["visible_rows"] = attr_dict["size"]

    handled = set(SELECT_STRING_ATTRS) | set(SELECT_FLAG_ATTRS) | {"size"}
    remaining = [(n, v) for (n, v) in attrs if n not in handled]
    attrs_dict = _build_attrs_dict(remaining)
    if attrs_dict:
        kwargs["attrs_dict"] = attrs_dict

    macro_call = "{% call native_select(" + ", ".join(_render_select_kwargs(kwargs)) + ") %}"
    return f"{macro_call}\n{inner_html}\n{{% endcall %}}"


def _render_select_kwargs(kwargs: dict) -> list[str]:
    order = ["id", "name", "class", "aria_label", "disabled", "multiple",
             "visible_rows", "attrs_dict"]
    parts = []
    for key in order:
        if key not in kwargs:
            continue
        value = kwargs[key]
        if key in ("disabled", "multiple"):
            parts.append(f"{key}=true")
        elif key == "attrs_dict":
            parts.append(f"attrs_dict={value}")
        else:
            parts.append(f"{key}={_jinja_value(value)}")
    return parts


def _convert_textarea(match: re.Match) -> str | None:
    """Convert a <textarea> tag to a {{ textarea(...) }} call (or call block)."""
    full_tag = match.group(0)
    tag_match = re.match(
        r'<textarea\s+([^>]*)>(.*?)</textarea>',
        full_tag,
        re.DOTALL | re.IGNORECASE,
    )
    if not tag_match:
        return None

    attrs_str = tag_match.group(1).strip()
    inner_text = tag_match.group(2).strip()

    attrs = _parse_attrs(attrs_str)
    attr_dict = dict(attrs)

    kwargs = {}
    for attr, kwarg in TEXTAREA_STRING_ATTRS.items():
        if attr in attr_dict:
            kwargs[kwarg] = attr_dict[attr]

    if attr_dict.get("rows"):
        kwargs["rows"] = attr_dict["rows"]

    for attr in TEXTAREA_FLAG_ATTRS:
        if attr in attr_dict:
            kwargs[TEXTAREA_FLAG_ATTRS[attr]] = True

    handled = set(TEXTAREA_STRING_ATTRS) | set(TEXTAREA_FLAG_ATTRS) | {"rows"}
    remaining = [(n, v) for (n, v) in attrs if n not in handled]
    attrs_dict = _build_attrs_dict(remaining)
    if attrs_dict:
        kwargs["attrs_dict"] = attrs_dict

    args = _render_textarea_kwargs(kwargs)
    if inner_text:
        return "{% call textarea(" + ", ".join(args) + ") %}" + inner_text + "{% endcall %}"
    return "{{ textarea(" + ", ".join(args) + ") }}"


def _render_textarea_kwargs(kwargs: dict) -> list[str]:
    order = ["id", "name", "placeholder", "rows", "disabled", "required",
             "class", "aria_label", "aria_describedby", "attrs_dict"]
    parts = []
    for key in order:
        if key not in kwargs:
            continue
        value = kwargs[key]
        if key in ("disabled", "required"):
            parts.append(f"{key}=true")
        elif key == "rows":
            parts.append(f"rows={value}")
        elif key == "attrs_dict":
            parts.append(f"attrs_dict={value}")
        else:
            parts.append(f"{key}={_jinja_value(value)}")
    return parts


# ---------------------------------------------------------------------------
# Main conversion logic
# ---------------------------------------------------------------------------

# Patterns for finding raw controls (matching the compliance checker)
RAW_BUTTON = re.compile(r'<button\b(?![^>]*data-slot=)([^>]*>.*?</button>)', re.DOTALL | re.IGNORECASE)
RAW_INPUT = re.compile(r'<input\b(?![^>]*data-slot=)([^>]*/?>)', re.DOTALL | re.IGNORECASE)
RAW_SELECT = re.compile(r'<select\b(?![^>]*data-slot=)([^>]*>.*?</select>)', re.DOTALL | re.IGNORECASE)
RAW_TEXTAREA = re.compile(r'<textarea\b(?![^>]*data-slot=)([^>]*>.*?</textarea>)', re.DOTALL | re.IGNORECASE)

CONVERTERS = {
    "button": (RAW_BUTTON, _convert_button),
    "input": (RAW_INPUT, _convert_input),
    "select": (RAW_SELECT, _convert_select),
    "textarea": (RAW_TEXTAREA, _convert_textarea),
}


def convert_file(path: Path, dry_run: bool = False) -> dict:
    """Convert raw controls in a single template file.

    Returns a dict with:
        path: relative path
        converted: list of (tag_type, original_snippet, replacement)
        skipped: list of (tag_type, reason, original_snippet)
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        rel_path = str(path.relative_to(ROOT))
    except ValueError:
        rel_path = str(path)
    result = {
        "path": rel_path,
        "converted": [],
        "skipped": [],
    }

    new_text = text

    for tag_type, (pattern, converter) in CONVERTERS.items():
        def _make_replacer(_tag=tag_type, _conv=converter):
            def _replacer(m):
                replacement = _conv(m)
                if replacement is None:
                    result["skipped"].append((_tag, "no one-to-one mapping", m.group(0)[:100]))
                    return m.group(0)
                result["converted"].append((_tag, m.group(0)[:100], replacement[:100]))
                return replacement
            return _replacer
        new_text = pattern.sub(_make_replacer(), new_text)

    if not dry_run and new_text != text:
        path.write_text(new_text, encoding="utf-8")

    return result


def scan_directory(directory: Path, dry_run: bool = False) -> list[dict]:
    """Scan all .html files in a directory and convert/report."""
    results = []
    for path in sorted(directory.rglob("*.html")):
        results.append(convert_file(path, dry_run=dry_run))
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Convert raw HTML controls to component macros"
    )
    parser.add_argument(
        "target", nargs="?", type=Path, default=None,
        help="File or directory to convert (default: whole templates tree, dry-run only)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report only, do not modify files",
    )
    args = parser.parse_args()

    if args.target:
        target = args.target
        if not target.is_absolute():
            target = ROOT / target
        if target.is_file():
            results = [convert_file(target, dry_run=args.dry_run)]
        else:
            results = scan_directory(target, dry_run=args.dry_run)
    else:
        results = scan_directory(TPL_ROOT, dry_run=True)

    total_converted = 0
    total_skipped = 0

    for r in results:
        if r["converted"] or r["skipped"]:
            print(f"\n{r['path']}:")
            for tag, orig, repl in r["converted"]:
                print(f"  CONVERTED <{tag}>: {orig}")
                print(f"    → {repl}")
                total_converted += 1
            for tag, reason, orig in r["skipped"]:
                print(f"  SKIPPED <{tag}> ({reason}): {orig}")
                total_skipped += 1

    print(f"\n{'─' * 60}")
    print(f"Total converted: {total_converted}")
    print(f"Total skipped:   {total_skipped}")
    print(f"Mode: {'DRY RUN (no files modified)' if args.dry_run else 'FILES MODIFIED'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
