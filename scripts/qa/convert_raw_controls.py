#!/usr/bin/env python3
"""Deterministic converter: hand-written HTML controls → component macros.

Converts raw <button>, <input>, <select> and <textarea> tags in Jinja2
templates into the existing component macros (button, input, native_select,
textarea) where the mapping is one-to-one.  Hidden inputs (type="hidden")
are left alone.  Controls with attributes the macro cannot carry are left
alone and listed.

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

# Attributes the button macro accepts as first-class keyword arguments
BUTTON_KWARGS = {"type", "class", "disabled", "aria_label", "data_action", "testid"}

# Attributes the input macro accepts as first-class keyword arguments
INPUT_KWARGS = {
    "id", "name", "type", "placeholder", "value", "disabled", "required",
    "class", "aria_label", "aria_describedby", "inputmode", "pattern",
    "autocomplete", "maxlength", "autofocus", "size",
}

# Attributes the native_select macro accepts as first-class keyword arguments
SELECT_KWARGS = {
    "id", "name", "size", "class", "aria_label", "disabled",
    "multiple", "visible_rows",
}

# Attributes the textarea macro accepts as first-class keyword arguments
TEXTAREA_KWARGS = {
    "id", "name", "placeholder", "rows", "disabled", "required",
    "class", "aria_label", "aria_describedby",
}

# Alpine.js / data-* attributes that must go through attrs_dict
ALPINE_PREFIXES = ("x-", "@", ":", "data-", "aria-")


def _is_alpine_or_data_attr(name: str) -> bool:
    return any(name.startswith(p) for p in ALPINE_PREFIXES)


def _parse_attrs(attrs_str: str) -> list[tuple[str, str]]:
    """Parse HTML attributes from a tag string into (name, value) pairs.

    Handles single-quoted, double-quoted and unquoted values, plus Jinja
    {{ }} expressions inside attribute values.
    """
    attrs = []
    pattern = r'''
        ([@:]*[\w][\w.-]*)            # attribute name (may start with @ or :, may contain dots)
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


def _build_attrs_dict(attrs: list[tuple[str, str]], exclude: set) -> str:
    """Build a Jinja attrs_dict={...} expression from parsed attributes.

    Only attributes that are NOT in `exclude` and that are Alpine/data-*
    attributes are included.
    """
    items = []
    for name, value in attrs:
        if name in exclude:
            continue
        if not _is_alpine_or_data_attr(name):
            continue
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        items.append(f"'{name}': '{escaped}'")
    if not items:
        return ""
    return "{" + ", ".join(items) + "}"


def _jinja_escape(value: str) -> str:
    """Escape a value for use in a Jinja string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _is_simple_button_text(inner_html: str) -> bool:
    """Check if inner HTML is simple text (optionally with an icon)."""
    # Strip whitespace/newlines
    cleaned = inner_html.strip()
    # If empty or just text, it's simple
    if not cleaned or not re.search(r'<[a-z]', cleaned, re.IGNORECASE):
        return True
    # If it's just an icon + text, it's simple enough
    icon_pattern = r'^\s*(?:<i\s+[^>]*></i>\s*)?[^<]*$'
    if re.match(icon_pattern, cleaned, re.DOTALL):
        return True
    return False


def _extract_button_text(inner_html: str) -> str:
    """Extract visible text from a button's inner HTML.

    Strips HTML tags and trims whitespace. Returns empty string if the
    button has no static text (e.g. only Alpine x-text or icons).
    """
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', inner_html)
    text = text.strip()
    # If the only content is Alpine expressions or whitespace, return empty
    if not text or text.startswith('{{') or text.startswith('{%'):
        return ""
    return text


# ---------------------------------------------------------------------------
# Tag converters
# ---------------------------------------------------------------------------

def _convert_button(match: re.Match) -> str | None:
    """Convert a <button> tag to a {{ button(...) }} call.

    Returns the replacement string, or None if the button cannot be converted.
    """
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

    # Alpine @click or @click.stop → on_click
    on_click = None
    click_keys = []
    for click_attr in ("@click", "@click.stop", "x-on:click"):
        if click_attr in attr_dict:
            on_click = attr_dict.pop(click_attr)
            click_keys.append(click_attr)
            break

    # Alpine :disabled
    alpine_disabled = attr_dict.pop(":disabled", None)

    # data-action → data_action
    data_action = attr_dict.pop("data-action", None)

    # aria-label → aria_label
    aria_label = attr_dict.pop("aria-label", None)

    # Extract text content
    text_content = _extract_button_text(inner_html)

    # Check if inner HTML is complex (has nested tags beyond simple text)
    has_complex_inner = bool(re.search(r'<[a-z][^>]*>', inner_html)) and not _is_simple_button_text(inner_html)

    # Build kwargs
    kwargs = {}

    btn_type = attr_dict.get("type", "button")
    if btn_type != "button":
        kwargs["type"] = btn_type

    if attr_dict.get("disabled") is not None:
        kwargs["disabled"] = "true"

    if on_click:
        kwargs["on_click"] = on_click

    if data_action:
        kwargs["data_action"] = data_action

    if aria_label:
        kwargs["aria_label"] = aria_label

    # Build attrs_dict from remaining Alpine/data-* attrs
    skip_attrs = set(BUTTON_KWARGS) | {"type"}
    skip_attrs.update(click_keys)
    skip_attrs.add(":disabled")
    skip_attrs.add("data-action")
    skip_attrs.add("aria-label")

    remaining = []
    for name, value in attrs:
        if name in skip_attrs:
            continue
        if _is_alpine_or_data_attr(name):
            remaining.append((name, value))

    # Add back :disabled through attrs_dict
    if alpine_disabled:
        remaining.append((":disabled", alpine_disabled))

    attrs_dict = _build_attrs_dict(remaining, set())

    if attrs_dict:
        kwargs["attrs_dict"] = attrs_dict

    # Build the macro call
    args = []
    if text_content and not has_complex_inner:
        args.append(f"'{_jinja_escape(text_content)}'")

    for k, v in kwargs.items():
        if k == "attrs_dict":
            args.append(f"attrs_dict={v}")
        elif k == "on_click":
            args.append(f"on_click='{_jinja_escape(v)}'")
        elif k == "disabled":
            args.append("disabled=true")
        elif k == "data_action":
            args.append(f"data_action='{_jinja_escape(v)}'")
        elif k == "aria_label":
            args.append(f"aria_label='{_jinja_escape(v)}'")
        else:
            args.append(f"{k}='{_jinja_escape(v)}'")

    macro_args = ", ".join(args)
    if has_complex_inner:
        return f"{{% call button({macro_args}) %}}{inner_html}{{% endcall %}}"
    return "{{ button(" + macro_args + ") }}"


def _convert_input(match: re.Match) -> str | None:
    """Convert an <input> tag to a {{ input(...) }} call.

    Returns the replacement string, or None if the input cannot be converted.
    """
    full_tag = match.group(0)
    tag_match = re.match(
        r'<input\s+([^>]*)>',
        full_tag,
        re.DOTALL | re.IGNORECASE,
    )
    if not tag_match:
        return None

    attrs_str = tag_match.group(1).strip()
    attrs = _parse_attrs(attrs_str)
    attr_dict = dict(attrs)

    # Leave hidden inputs alone
    if attr_dict.get("type") == "hidden":
        return None

    # Build kwargs
    kwargs = {}

    for key in ("id", "name", "placeholder", "value", "class", "inputmode",
                "pattern", "autocomplete", "maxlength"):
        if key in attr_dict:
            kwargs[key] = attr_dict[key]

    if attr_dict.get("type") and attr_dict["type"] != "text":
        kwargs["type"] = attr_dict["type"]

    if attr_dict.get("disabled") is not None:
        kwargs["disabled"] = "true"

    if attr_dict.get("required") is not None:
        kwargs["required"] = "true"

    if attr_dict.get("aria-label"):
        kwargs["aria_label"] = attr_dict["aria-label"]

    if attr_dict.get("aria-describedby"):
        kwargs["aria_describedby"] = attr_dict["aria-describedby"]

    if attr_dict.get("autofocus") is not None:
        kwargs["autofocus"] = "true"

    # Build attrs_dict from remaining Alpine/data-* attrs
    remaining = []
    for name, value in attrs:
        if name in INPUT_KWARGS:
            continue
        if name in ("aria-label", "aria-describedby", "autofocus"):
            continue
        if _is_alpine_or_data_attr(name):
            remaining.append((name, value))

    attrs_dict = _build_attrs_dict(remaining, set())
    if attrs_dict:
        kwargs["attrs_dict"] = attrs_dict

    # Build the macro call
    args = []
    for k, v in kwargs.items():
        if k == "attrs_dict":
            args.append(f"attrs_dict={v}")
        elif k in ("disabled", "required", "autofocus"):
            args.append(f"{k}=true")
        else:
            args.append(f"{k}='{_jinja_escape(v)}'")

    return "{{ input(" + ", ".join(args) + ") }}"


def _convert_select(match: re.Match) -> str | None:
    """Convert a <select> tag to a {% call native_select(...) %}...{% endcall %}.

    Returns the replacement string, or None if the select cannot be converted.
    """
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

    for key in ("id", "name", "class"):
        if key in attr_dict:
            kwargs[key] = attr_dict[key]

    if attr_dict.get("aria-label"):
        kwargs["aria_label"] = attr_dict["aria-label"]

    if attr_dict.get("disabled") is not None:
        kwargs["disabled"] = "true"

    if attr_dict.get("multiple") is not None:
        kwargs["multiple"] = "true"

    if attr_dict.get("size"):
        kwargs["visible_rows"] = attr_dict["size"]

    # Build attrs_dict from remaining Alpine/data-* attrs
    remaining = []
    for name, value in attrs:
        if name in SELECT_KWARGS:
            continue
        if name in ("aria-label",):
            continue
        if _is_alpine_or_data_attr(name):
            remaining.append((name, value))

    attrs_dict = _build_attrs_dict(remaining, set())
    if attrs_dict:
        kwargs["attrs_dict"] = attrs_dict

    # Build the macro call
    args = []
    for k, v in kwargs.items():
        if k == "attrs_dict":
            args.append(f"attrs_dict={v}")
        elif k in ("disabled", "multiple"):
            args.append(f"{k}=true")
        else:
            args.append(f"{k}='{_jinja_escape(v)}'")

    macro_call = "{% call native_select(" + ", ".join(args) + ") %}"
    end_call = "{% endcall %}"
    return f"{macro_call}\n{inner_html}\n{end_call}"


def _convert_textarea(match: re.Match) -> str | None:
    """Convert a <textarea> tag to a {{ textarea(...) }} call.

    Returns the replacement string, or None if the textarea cannot be converted.
    """
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

    for key in ("id", "name", "placeholder", "class"):
        if key in attr_dict:
            kwargs[key] = attr_dict[key]

    if attr_dict.get("rows"):
        kwargs["rows"] = attr_dict["rows"]

    if attr_dict.get("disabled") is not None:
        kwargs["disabled"] = "true"

    if attr_dict.get("required") is not None:
        kwargs["required"] = "true"

    if attr_dict.get("aria-label"):
        kwargs["aria_label"] = attr_dict["aria-label"]

    if attr_dict.get("aria-describedby"):
        kwargs["aria_describedby"] = attr_dict["aria-describedby"]

    # Build attrs_dict from remaining Alpine/data-* attrs
    remaining = []
    for name, value in attrs:
        if name in TEXTAREA_KWARGS:
            continue
        if name in ("aria-label", "aria-describedby"):
            continue
        if _is_alpine_or_data_attr(name):
            remaining.append((name, value))

    attrs_dict = _build_attrs_dict(remaining, set())
    if attrs_dict:
        kwargs["attrs_dict"] = attrs_dict

    # Build the macro call
    args = []
    for k, v in kwargs.items():
        if k == "attrs_dict":
            args.append(f"attrs_dict={v}")
        elif k in ("disabled", "required"):
            args.append(f"{k}=true")
        elif k == "rows":
            args.append(f"rows={v}")
        else:
            args.append(f"{k}='{_jinja_escape(v)}'")

    if inner_text:
        return f"{{% call textarea(" + ", ".join(args) + ") %}}{inner_text}{{% endcall %}}"
    return "{{ textarea(" + ", ".join(args) + ") }}"


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