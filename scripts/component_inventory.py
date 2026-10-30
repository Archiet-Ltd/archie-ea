#!/usr/bin/env python
"""Component inventory generator.

Reads every Jinja template and writes docs/component-inventory.json listing,
per route, every button, form, table and modal, plus every endpoint the
templates reference. Deterministic: same tree, byte-identical JSON.
"""
from __future__ import annotations

import ast as _ast
import hashlib
import importlib.util
import inspect
import json
import re
import textwrap
import sys
from collections import defaultdict
from pathlib import Path

from jinja2 import Environment
from jinja2 import nodes as jinja_nodes

from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parent.parent
APP_TEMPLATES = ROOT / "app" / "templates"
APP_MODULES = ROOT / "app" / "modules"
STATIC_DIR = ROOT / "docs"

JINJA_SUFFIXES = (".html", ".jinja", ".jinja2")
SKIP_DIRS = {"__pycache__", "node_modules", ".git"}

# ---------------------------------------------------------------------------
# Reused functions from existing scripts (imported by path via importlib)
# ---------------------------------------------------------------------------

def _load_module_from_path(module_name: str, file_path: Path):
    """Load a module from a file path."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# Load reused modules
_check_canonical_route = _load_module_from_path(
    "check_canonical_route", ROOT / "scripts" / "check_canonical_route.py"
)
_check_template_syntax = _load_module_from_path(
    "check_template_syntax", ROOT / "scripts" / "check_template_syntax.py"
)
_check_template_references = _load_module_from_path(
    "check_template_references", ROOT / "scripts" / "check_template_references.py"
)
_check_macro_kwargs = _load_module_from_path(
    "check_macro_kwargs", ROOT / "scripts" / "check_macro_kwargs.py"
)

# Reused functions
_rules = _check_canonical_route._rules
template_files = _check_template_syntax.template_files
_render_target = _check_template_references._render_target
collect_signatures = _check_macro_kwargs.collect_signatures

# ---------------------------------------------------------------------------
# Macro category mapping (Decision 2)
# ---------------------------------------------------------------------------

MACRO_CATEGORY = {
    "button": "button",
    "modal": "modal",
    "modal_form": "modal",
    "modal_standardized": "modal",
    "confirmation_dialog": "modal",
    "dialog": "modal",
    "drawer": "modal",
    "data_table": "table",
    "workbench_table": "table",
    "dense_table": "table",
    "table": "table",
}

# Macro file to macro name mapping for known components
MACRO_SOURCE_FILE = {
    "button": "components/button.html",
    "modal": "components/modal.html",
    "modal_form": "components/modal_form.html",
    "modal_standardized": "components/modal_standardized.html",
    "confirmation_dialog": "components/confirmation_dialog.html",
    "dialog": "components/dialog.html",
    "drawer": "components/drawer.html",
    "data_table": "components/data_table.html",
    "workbench_table": "components/workbench_table.html",
    "dense_table": "components/dense_table.html",
    "table": "components/table.html",
}

# ---------------------------------------------------------------------------
# Jinja AST walk for macro calls and url_for
# ---------------------------------------------------------------------------

class JinjaASTWalker:
    """Walk Jinja AST to extract macro calls, url_for calls, and template references."""

    def __init__(self, template_path: str, template_source: str, macro_signatures: dict):
        self.template_path = template_path
        self.template_source = template_source
        self.macro_signatures = macro_signatures
        self.macro_calls = []  # List of (macro_name, source_file, kwargs_dict, lineno)
        self.url_for_calls = []  # List of (endpoint, args, kwargs, lineno)
        self.template_refs = {
            "includes": set(),
            "extends": set(),
            "imports": set(),
            "from_imports": set(),
        }
        self.imported_macros = {}  # alias -> (source_file, macro_name)
        self.parse_errors = []

    def walk(self):
        env = Environment()
        try:
            ast = env.parse(self.template_source, filename=self.template_path)
        except Exception as e:
            self.parse_errors.append(str(e))
            return

        # First pass: collect imports
        for node in ast.find_all(jinja_nodes.FromImport):
            template = node.template.value if hasattr(node.template, 'value') else str(node.template)
            for name in node.names:
                if isinstance(name, tuple):
                    orig, alias = name
                else:
                    orig = alias = name
                self.imported_macros[alias] = (template, orig)
                self.template_refs["from_imports"].add(template)

        for node in ast.find_all(jinja_nodes.Import):
            template = node.template.value if hasattr(node.template, 'value') else str(node.template)
            alias = node.target.name if hasattr(node.target, 'name') else str(node.target)
            self.imported_macros[alias] = (template, None)  # namespace import
            self.template_refs["imports"].add(template)

        for node in ast.find_all(jinja_nodes.Include):
            template = node.template.value if hasattr(node.template, 'value') else str(node.template)
            self.template_refs["includes"].add(template)

        for node in ast.find_all(jinja_nodes.Extends):
            template = node.template.value if hasattr(node.template, 'value') else str(node.template)
            self.template_refs["extends"].add(template)

        # Second pass: find macro calls and url_for
        for node in ast.find_all(jinja_nodes.Call):
            self._process_call_node(node)

    def _process_call_node(self, node: jinja_nodes.Call):
        # Get line number
        lineno = getattr(node, 'lineno', 0)

        # Check if it's a macro call (via imported alias)
        if isinstance(node.node, jinja_nodes.Name):
            name = node.node.name
            if name in self.imported_macros:
                source_file, macro_name = self.imported_macros[name]
                if macro_name is not None:
                    # Extract keyword arguments
                    kwargs = self._extract_call_kwargs(node)
                    self.macro_calls.append((macro_name, source_file, kwargs, lineno))
                return

        # Check if it's a namespaced macro call (e.g., macros.button())
        if isinstance(node.node, jinja_nodes.Getattr):
            obj = node.node.node
            attr = node.node.attr
            if isinstance(obj, jinja_nodes.Name) and obj.name in self.imported_macros:
                source_file, _ = self.imported_macros[obj.name]
                kwargs = self._extract_call_kwargs(node)
                self.macro_calls.append((attr, source_file, kwargs, lineno))
                return

        # Check for url_for calls
        if isinstance(node.node, jinja_nodes.Name) and node.node.name == "url_for":
            self._extract_url_for(node, lineno)

    def _extract_call_kwargs(self, node: jinja_nodes.Call) -> dict:
        """Extract keyword arguments from a Call node as expression text.
        Also handles first positional argument as 'text' for button macro.
        """
        kwargs = {}
        # Handle positional arguments - first one is often 'text' for button
        if node.args:
            first_arg_text = self._node_to_expr_text(node.args[0])
            # Strip quotes if it's a string literal
            if first_arg_text.startswith('"') and first_arg_text.endswith('"'):
                first_arg_text = first_arg_text[1:-1]
            elif first_arg_text.startswith("'") and first_arg_text.endswith("'"):
                first_arg_text = first_arg_text[1:-1]
            kwargs["text"] = first_arg_text
        for kw in node.kwargs:
            key = kw.key
            # Get the expression text for the value
            val_expr = self._node_to_expr_text(kw.value)
            # Strip quotes from string literals
            if val_expr.startswith('"') and val_expr.endswith('"'):
                val_expr = val_expr[1:-1]
            elif val_expr.startswith("'") and val_expr.endswith("'"):
                val_expr = val_expr[1:-1]
            kwargs[key] = val_expr
        return kwargs

    def _extract_url_for(self, node: jinja_nodes.Call, lineno: int):
        """Extract endpoint from url_for call."""
        if node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, jinja_nodes.Const) and isinstance(first_arg.value, str):
                endpoint = first_arg.value
                args = [self._node_to_expr_text(a) for a in node.args[1:]]
                kwargs = self._extract_call_kwargs(node)
                self.url_for_calls.append((endpoint, args, kwargs, lineno))

    def _node_to_expr_text(self, node) -> str:
        """Convert a Jinja AST node back to expression text."""
        if isinstance(node, jinja_nodes.Const):
            if isinstance(node.value, str):
                return f'"{node.value}"'
            return repr(node.value)
        elif isinstance(node, jinja_nodes.Name):
            return node.name
        elif isinstance(node, jinja_nodes.Getattr):
            return f"{self._node_to_expr_text(node.node)}.{node.attr}"
        elif isinstance(node, jinja_nodes.Call):
            func = self._node_to_expr_text(node.node)
            args = ", ".join(self._node_to_expr_text(a) for a in node.args)
            kwargs = ", ".join(f"{k.key}={self._node_to_expr_text(k.value)}" for k in node.kwargs)
            parts = [p for p in [args, kwargs] if p]
            return f"{func}({', '.join(parts)})"
        elif isinstance(node, jinja_nodes.List):
            items = ", ".join(self._node_to_expr_text(i) for i in node.items)
            return f"[{items}]"
        elif isinstance(node, jinja_nodes.Dict):
            items = []
            for pair in node.items:
                key_text = self._node_to_expr_text(pair.key)
                val_text = self._node_to_expr_text(pair.value)
                items.append(f"{key_text}: {val_text}")
            return f"{{{', '.join(items)}}}"
        elif isinstance(node, jinja_nodes.Getitem):
            return f"{self._node_to_expr_text(node.node)}[{self._node_to_expr_text(node.arg)}]"
        elif isinstance(node, jinja_nodes.Filter):
            return f"{self._node_to_expr_text(node.node)}|{node.name}"
        elif isinstance(node, jinja_nodes.Test):
            return f"{self._node_to_expr_text(node.node)} is {node.test.name}"
        elif isinstance(node, jinja_nodes.Compare):
            left = self._node_to_expr_text(node.expr)
            ops = []
            for operand in node.ops:
                op_text = operand.op
                right_text = self._node_to_expr_text(operand.expr)
                ops.append(f"{op_text} {right_text}")
            return f"{left} {' '.join(ops)}"
        else:
            return f"<expr:{type(node).__name__}>"


# ---------------------------------------------------------------------------
# HTML reconstruction from lex tokens
# ---------------------------------------------------------------------------

def rebuild_html_from_lex(template_source: str, macro_calls: list, url_for_calls: list) -> tuple[str, dict]:
    """Rebuild HTML source from lex tokens.

    Replaces {{ ... }} with placeholders mapped to expression text.
    Removes {% %} tags (keeps both branches of if).
    Drops comments.
    Returns (cleaned_html, placeholder_map).
    """
    env = Environment()
    tokens = list(env.lex(template_source))

    output = []
    placeholder_map = {}
    placeholder_counter = 0
    i = 0

    while i < len(tokens):
        lineno, token_type, token_value = tokens[i]

        if token_type == 'data':
            output.append(token_value)
        elif token_type == 'variable_begin':
            # {{ ... }} - find the matching variable_end
            expr_parts = []
            i += 1
            while i < len(tokens) and tokens[i][1] != 'variable_end':
                expr_parts.append(tokens[i][2])
                i += 1
            expr_text = "".join(expr_parts).strip()
            placeholder = f"__JINJA_EXPR_{placeholder_counter}__"
            placeholder_counter += 1
            output.append(placeholder)
            placeholder_map[placeholder] = expr_text
        elif token_type == 'block_begin':
            # {% ... %} - skip the block but keep content for if/else
            i += 1
            while i < len(tokens) and tokens[i][1] != 'block_end':
                i += 1
            # Content inside blocks is emitted as separate data tokens
        elif token_type == 'comment' or token_type == 'comment_begin':
            # {# ... #} - drop entirely
            # Skip until comment_end
            while i < len(tokens) and tokens[i][1] not in ('comment_end', 'comment'):
                i += 1
        # variable_end, block_end, comment_end are handled above

        i += 1

    return "".join(output), placeholder_map


# ---------------------------------------------------------------------------
# HTML element extraction with macro call context
# ---------------------------------------------------------------------------

def _click_handler(attrs):
    """The click handler of an element: onclick, Alpine @click / x-on:click, or an htmx request."""
    for key in ("onclick", "@click", "x-on:click", "@click.prevent", "hx-post", "hx-delete", "hx-put", "hx-get"):
        if attrs.get(key):
            return attrs[key]
    return None


def _is_destructive_class(class_attr):
    """A destructive button style (bg-destructive, btn-destructive), not a destructive-coloured icon or border."""
    return any("destructive" in t and not t.startswith(("text-", "border-", "hover:text-", "focus"))
               for t in str(class_attr or "").split())


class TemplateElementExtractor(HTMLParser):
    """Extract buttons, forms, tables, and modals from cleaned HTML source."""

    def __init__(self, placeholder_map: dict, macro_calls: list, url_for_calls: list, template_path: str):
        super().__init__(convert_charrefs=True)
        self.placeholder_map = placeholder_map
        self.macro_calls = macro_calls
        self.url_for_calls = url_for_calls
        self.template_path = template_path
        self.buttons = []
        self.forms = []
        self.tables = []
        self.modals = []
        self._tag_stack = []
        self._tag_attrs_stack = []
        self._tag_data = ""
        self.links = []
        self.testids = []
        self._text_stack = []
        self._form_stack = []
        self._in_script = False
        self._in_style = False
        self._macro_call_iter = iter(macro_calls)
        self._current_macro_call = None

    VOID = {"br", "hr", "img", "input", "meta", "link", "source", "wbr", "col", "area",
            "base", "embed", "param", "track"}

    def _maybe_modal(self, tag_l, attrs_d):
        if tag_l == "dialog" or attrs_d.get("role") in ("dialog", "alertdialog") or attrs_d.get("aria-modal") == "true":
            self.modals.append({
                "id": self._resolve_placeholder(attrs_d.get("id") or ""),
                "title": self._resolve_placeholder(attrs_d.get("aria-label") or ""),
                "buttons": [],
                "submit_handler": None,
                "source_template": self.template_path,
                "via_macro": None,
            })

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self.VOID:
            self.handle_endtag(tag)

    def handle_starttag(self, tag, attrs):
        tag_l = tag.lower()
        attrs_d = dict(attrs)
        if "data-testid" in attrs_d:
            self.testids.append({
                "tag": tag_l,
                "testid": self._resolve_placeholder(attrs_d["data-testid"] or ""),
                "source_template": self.template_path,
            })
        self._maybe_modal(tag_l, attrs_d)
        if tag_l == "input" and self._form_stack:
            name = attrs_d.get("name")
            if name:
                self._form_stack[-1]["fields"].append(self._resolve_placeholder(name))
        if tag_l in self.VOID:
            return
        self._tag_stack.append(tag_l)
        self._tag_attrs_stack.append(attrs_d)
        self._text_stack.append("")
        if tag_l == "form":
            self._form_stack.append({"fields": [], "method": (attrs_d.get("method") or "GET").upper()})
        elif tag_l == "script":
            self._in_script = True
        elif tag_l == "style":
            self._in_style = True

    def handle_endtag(self, tag):
        tag_l = tag.lower()
        if tag_l not in self._tag_stack:
            return
        while self._tag_stack:
            top = self._tag_stack.pop()
            popped_attrs = self._tag_attrs_stack.pop()
            text = self._text_stack.pop()
            if self._text_stack:
                self._text_stack[-1] += text
            if top == tag_l:
                self._tag_data = text
                self._process_tag(tag_l, popped_attrs)
                break
        if tag_l == "script":
            self._in_script = False
        elif tag_l == "style":
            self._in_style = False

    def handle_data(self, data):
        if self._in_script or self._in_style:
            return
        if self._text_stack:
            self._text_stack[-1] += data
        self._tag_data += data

    def _process_tag(self, tag_l, attrs):
        if tag_l == "button":
            if self._form_stack:
                attrs = dict(attrs, **{"__form_method": self._form_stack[-1]["method"]})
            self._extract_button(attrs)
        elif tag_l == "form":
            fm = self._form_stack.pop() if self._form_stack else {"fields": []}
            self._extract_form(attrs, fm["fields"])
        elif tag_l == "table":
            self._extract_table(attrs)
        elif tag_l == "a":
            self._extract_link(attrs)
            class_attr = attrs.get("class", "")
            if isinstance(class_attr, str) and "public-btn" in class_attr:
                self._extract_button_as_link(attrs)

    def _extract_link(self, attrs):
        href = attrs.get("href")
        label = re.sub(r"\s+", " ", self._resolve_placeholder(self._tag_data.strip()))[:100]
        testid = attrs.get("data-testid")
        if href is None and not testid:
            return
        link = {
            "label": label,
            "href": self._resolve_placeholder(href) if href is not None else None,
            "testid": self._resolve_placeholder(testid) if testid else None,
            "modal_id": attrs.get("data-modal-open"),
            "onclick": attrs.get("onclick"),
            "source_template": self.template_path,
        }
        self.links.append(link)

    def _resolve_placeholder(self, value: str) -> str:
        """Resolve placeholder to expression text if it's a placeholder."""
        if value in self.placeholder_map:
            return self.placeholder_map[value]
        if "__JINJA_EXPR_" in value:
            return re.sub(
                r"__JINJA_EXPR_\d+__",
                lambda m: "{{ " + self.placeholder_map.get(m.group(0), m.group(0)) + " }}",
                value,
            )
        return value

    def _extract_button(self, attrs):
        btn_attrs = dict(attrs) if not isinstance(attrs, dict) else attrs

        # Resolve placeholders in attributes
        for key, val in btn_attrs.items():
            if isinstance(val, str):
                btn_attrs[key] = self._resolve_placeholder(val)

        btn = {
            "label": "",
            "type": "noop",
            "handler": None,
            "data_action": None,
            "href": None,
            "modal_id": None,
            "download": False,
            "resolved": True,
            "source_template": self.template_path,
            "button_type": btn_attrs.get("type", "submit" if btn_attrs.get("__form_method") else "button"),
            "form_method": btn_attrs.get("__form_method"),
            "testid": btn_attrs.get("data-testid"),
        }

        # Check for data-action
        if "data-action" in btn_attrs:
            btn["data_action"] = btn_attrs["data-action"]

        # Check for download attribute
        if "download" in btn_attrs:
            btn["download"] = True
            val = btn_attrs["download"]
            if val and re.search(r"\.(csv|xlsx|pdf|export)$", val, re.I):
                btn["type"] = "download"

        # Check for href navigation
        if "href" in btn_attrs:
            btn["href"] = btn_attrs["href"]
            btn["type"] = "navigate"
            btn["resolved"] = False

        # Determine type based on class, onclick, etc.
        on_click = _click_handler(btn_attrs)
        class_attr = btn_attrs.get("class", "")

        # Check for modal opener attributes
        data_modal_open = btn_attrs.get("data-modal-open")

        # 1. destructive
        if _is_destructive_class(class_attr) or re.search(r"Platform\.modal\.confirm", on_click or ""):
            btn["type"] = "destructive"
        # 2. download
        elif btn["download"]:
            btn["type"] = "download"
        # 3. navigate (href or JS navigation)
        elif btn["href"]:
            btn["type"] = "navigate"
        elif on_click and (re.search(r"window\.location", on_click) or re.search(r"location\.href", on_click)):
            btn["type"] = "navigate"
            btn["resolved"] = False
            btn["handler"] = on_click
        # 4. modal - opener patterns
        elif data_modal_open:
            btn["type"] = "modal"
            btn["modal_id"] = data_modal_open
        elif on_click and re.search(r"Platform\.modal\.open", on_click):
            btn["type"] = "modal"
            # Extract modal id from Platform.modal.open('id')
            match = re.search(r"Platform\.modal\.open\(['\"]([^'\"]+)['\"]\)", on_click)
            if match:
                btn["modal_id"] = match.group(1)
        # 5. inline-render (any other handler)
        elif on_click:
            btn["type"] = "inline-render"
        # 6. noop
        else:
            btn["type"] = "noop"

        # Extract label from tag content (resolve placeholders)
        if self._tag_data.strip():
            btn["label"] = self._resolve_placeholder(self._tag_data.strip())[:100]

        # If modal type, try to extract modal id (if not already set)
        if btn["type"] == "modal" and not btn["modal_id"]:
            if "data-modal-open" in btn_attrs:
                btn["modal_id"] = btn_attrs["data-modal-open"]
            if "id" in btn_attrs:
                btn["modal_id"] = btn_attrs["id"]

        btn["label"] = re.sub(r"\s+", " ", btn["label"])
        self.buttons.append(btn)

    def _extract_button_as_link(self, attrs):
        btn = {
            "label": "",
            "type": "noop",
            "handler": None,
            "data_action": None,
            "href": None,
            "modal_id": None,
            "download": False,
            "resolved": False,
            "source_template": self.template_path,
            "from_anchor": True,
        }

        for key, val in attrs.items():
            if isinstance(val, str):
                attrs[key] = self._resolve_placeholder(val)

        if "href" in attrs:
            btn["href"] = attrs["href"]
            btn["type"] = "navigate"

        if "data-action" in attrs:
            btn["data_action"] = attrs["data-action"]

        if "download" in attrs:
            btn["download"] = True
            val = attrs["download"]
            if val and re.search(r"\.(csv|xlsx|pdf|export)$", val, re.I):
                btn["type"] = "download"

        class_attr = attrs.get("class", "")
        on_click = _click_handler(attrs)
        data_modal_open = attrs.get("data-modal-open")

        if isinstance(class_attr, str) and _is_destructive_class(class_attr):
            btn["type"] = "destructive"
        elif btn["download"]:
            btn["type"] = "download"
        elif btn["href"]:
            btn["type"] = "navigate"
        elif data_modal_open:
            btn["type"] = "modal"
            btn["modal_id"] = data_modal_open
        elif on_click and re.search(r"Platform\.modal\.open", on_click):
            btn["type"] = "modal"
            match = re.search(r"Platform\.modal\.open\(['\"]([^'\"]+)['\"]\)", on_click)
            if match:
                btn["modal_id"] = match.group(1)
        elif on_click:
            btn["type"] = "inline-render"
        else:
            btn["type"] = "noop"

        if self._tag_data.strip():
            btn["label"] = self._resolve_placeholder(self._tag_data.strip())[:100]

        if btn["type"] == "modal" and not btn["modal_id"]:
            if "data-modal-open" in attrs:
                btn["modal_id"] = attrs["data-modal-open"]
            if "id" in attrs:
                btn["modal_id"] = attrs["id"]

        self.buttons.append(btn)

    def _extract_form(self, attrs, fields=None):
        btn_attrs = dict(attrs) if not isinstance(attrs, dict) else attrs
        for key, val in btn_attrs.items():
            if isinstance(val, str):
                btn_attrs[key] = self._resolve_placeholder(val)

        form = {
            "action": btn_attrs.get("action", ""),
            "method": (btn_attrs.get("method") or "GET").upper(),
            "fields": sorted(set(fields or [])),
            "testid": btn_attrs.get("data-testid"),
            "source_template": self.template_path,
        }
        self.forms.append(form)

    def _extract_table(self, attrs):
        btn_attrs = dict(attrs) if not isinstance(attrs, dict) else attrs
        for key, val in btn_attrs.items():
            if isinstance(val, str):
                btn_attrs[key] = self._resolve_placeholder(val)

        table = {
            "source_endpoint": btn_attrs.get("source_endpoint"),
            "source_template": self.template_path,
        }
        self.tables.append(table)


def extract_elements_from_template(template_path: str, template_source: str, macro_signatures: dict, extra: dict | None = None):
    """Extract all elements from a template using Jinja AST walk + HTML reconstruction."""
    # Walk AST for macro calls, url_for, template refs
    walker = JinjaASTWalker(template_path, template_source, macro_signatures)
    walker.walk()

    if walker.parse_errors:
        return [], [], [], [], walker.parse_errors, walker.template_refs, walker.macro_calls, walker.url_for_calls

    # Rebuild HTML from lex tokens
    cleaned_html, placeholder_map = rebuild_html_from_lex(
        template_source, walker.macro_calls, walker.url_for_calls
    )

    # Parse cleaned HTML
    parser = TemplateElementExtractor(placeholder_map, walker.macro_calls, walker.url_for_calls, template_path)
    try:
        parser.feed(cleaned_html)
    except Exception as e:
        return [], [], [], [], [str(e)], walker.template_refs, walker.macro_calls, walker.url_for_calls

    if extra is not None:
        extra["links"] = parser.links
        extra["testids"] = parser.testids

    # Now process macro calls to add macro-generated elements
    buttons, forms, tables, modals = process_macro_calls(
        walker.macro_calls, walker.url_for_calls, template_path, macro_signatures, parser.buttons
    )

    # Combine raw HTML buttons with macro-generated buttons
    all_buttons = parser.buttons + buttons
    all_forms = parser.forms + forms
    all_tables = parser.tables + tables
    all_modals = parser.modals + modals

    return all_buttons, all_forms, all_tables, all_modals, [], walker.template_refs, walker.macro_calls, walker.url_for_calls


# ---------------------------------------------------------------------------
# Macro call processing
# ---------------------------------------------------------------------------

def process_macro_calls(macro_calls, url_for_calls, template_path, macro_signatures, raw_buttons):
    """Process macro calls to generate buttons, forms, tables, modals."""
    buttons = []
    forms = []
    tables = []
    modals = []

    for macro_name, source_file, kwargs, lineno in macro_calls:
        category = MACRO_CATEGORY.get(macro_name)
        if not category:
            # Unknown macro - check if its body has raw elements (handled at call site via via_macro)
            continue

        if category == "button":
            btn = process_button_macro(macro_name, source_file, kwargs, template_path, lineno)
            if btn:
                buttons.append(btn)
        elif category == "modal":
            modal = process_modal_macro(macro_name, source_file, kwargs, template_path, lineno, raw_buttons)
            if modal:
                modals.append(modal)
        elif category == "table":
            table = process_table_macro(macro_name, source_file, kwargs, template_path, lineno)
            if table:
                tables.append(table)

    return buttons, forms, tables, modals


def process_button_macro(macro_name, source_file, kwargs, template_path, lineno):
    """Process a button macro call."""
    btn = {
        "label": kwargs.get("text", kwargs.get("label", "")),
        "type": "noop",
        "handler": None,
        "data_action": kwargs.get("data_action", kwargs.get("on_click")),
        "href": kwargs.get("href"),
        "modal_id": None,
        "download": False,
        "resolved": True,
        "source_template": template_path,
        "via_macro": f"{source_file}:{macro_name}",
        "line": lineno,
    }

    # Resolve href if it's a url_for expression
    if btn["href"] and "url_for" in btn["href"]:
        btn["resolved"] = False

    # Determine type
    variant = kwargs.get("variant", "")
    on_click = kwargs.get("on_click", kwargs.get("data_action", ""))

    # 1. destructive
    if variant == "destructive" or "destructive" in variant.lower() or re.search(r"Platform\.modal\.confirm", on_click):
        btn["type"] = "destructive"
    # 2. download
    elif btn["download"]:
        btn["type"] = "download"
    # 3. navigate (href)
    elif btn["href"]:
        btn["type"] = "navigate"
        btn["resolved"] = False
    # 4. modal opener
    elif re.search(r"data-modal-open=", on_click) or re.search(r"Platform\.modal\.open", on_click):
        btn["type"] = "modal"
        # Extract modal id from on_click
        match = re.search(r"data-modal-open=['\"]([^'\"]+)['\"]", on_click)
        if match:
            btn["modal_id"] = match.group(1)
        match = re.search(r"Platform\.modal\.open\(['\"]([^'\"]+)['\"]\)", on_click)
        if match:
            btn["modal_id"] = match.group(1)
    # 5. inline-render
    elif on_click:
        btn["type"] = "inline-render"
    # 6. noop
    else:
        btn["type"] = "noop"

    return btn


def process_modal_macro(macro_name, source_file, kwargs, template_path, lineno, raw_buttons):
    """Process a modal macro call."""
    modal_id = kwargs.get("id", "")
    title = kwargs.get("title", "")

    # Find internal buttons from raw buttons that are inside this modal
    # For now, we'll extract from the macro's typical structure
    internal_buttons = []
    submit_handler = kwargs.get("action", kwargs.get("submit_handler"))

    # Standard close button
    internal_buttons.append({
        "label": "Close",
        "type": "inline-render",
        "handler": "close-modal",
        "data_action": "close-modal",
        "source_template": template_path,
        "via_macro": f"{source_file}:{macro_name}",
    })

    # Confirm button if modal_form
    if macro_name in ("modal_form", "modal_standardized"):
        internal_buttons.append({
            "label": "Submit",
            "type": "inline-render",
            "handler": "submit-form",
            "data_action": "submit-form",
            "source_template": template_path,
            "via_macro": f"{source_file}:{macro_name}",
        })

    modal = {
        "id": modal_id,
        "title": title,
        "buttons": internal_buttons,
        "submit_handler": submit_handler,
        "source_template": template_path,
        "via_macro": f"{source_file}:{macro_name}",
        "line": lineno,
    }
    return modal


def process_table_macro(macro_name, source_file, kwargs, template_path, lineno):
    """Process a table macro call."""
    source_endpoint = kwargs.get("source_endpoint", kwargs.get("url", kwargs.get("endpoint")))
    row_actions = kwargs.get("row_actions", [])

    table = {
        "source_endpoint": source_endpoint,
        "row_actions": row_actions,
        "source_template": template_path,
        "via_macro": f"{source_file}:{macro_name}",
        "line": lineno,
    }
    return table


# ---------------------------------------------------------------------------
# Route to template mapping
# ---------------------------------------------------------------------------

def get_route_templates(application):
    """Map each route to its rendered template(s) using _render_target on unwrapped view functions."""
    route_templates = {}  # endpoint -> list of (template, template_unresolved)

    for rule in application.url_map.iter_rules():
        endpoint = rule.endpoint
        rule_str = endpoint
        if rule_str in route_templates:
            continue
        view_func = application.view_functions.get(endpoint)
        if view_func is None:
            route_templates[rule_str] = [(None, None)]
            continue

        try:
            unwrapped = inspect.unwrap(view_func)
            view_src = inspect.getsource(unwrapped)
        except (TypeError, OSError):
            try:
                view_src = inspect.getsource(view_func)
            except (TypeError, OSError):
                route_templates[rule_str] = [(None, None)]
                continue

        # Use _render_target to find literal render_template targets
        targets = set()
        unresolved_targets = set()
        try:
            tree = _ast.parse(textwrap.dedent(view_src))
            for node in _ast.walk(tree):
                if not isinstance(node, _ast.Call):
                    continue
                target = _render_target(node)
                if target:
                    targets.add(target)
                else:
                    # Check if this is a render_template/stream_template call with non-literal first arg
                    func = node.func
                    name = func.attr if isinstance(func, _ast.Attribute) else getattr(func, "id", None)
                    if name in ("render_template", "stream_template") and node.args and not isinstance(node.args[0], _ast.Constant):
                        unresolved = _ast.unparse(node.args[0]) if hasattr(_ast, 'unparse') else str(node.args[0])
                        unresolved_targets.add(unresolved)
        except SyntaxError:
            pass

        # Sort for determinism
        sorted_targets = sorted(targets)
        sorted_unresolved = sorted(unresolved_targets)
        combined = [(t, None) for t in sorted_targets] + [(None, u) for u in sorted_unresolved]

        if combined:
            route_templates[rule_str] = combined
        else:
            route_templates[rule_str] = [(None, None)]

    return route_templates


# ---------------------------------------------------------------------------
# Endpoint extraction with reference tracking
# ---------------------------------------------------------------------------

def extract_endpoints_with_refs(rules, all_url_for_calls):
    """Extract endpoints from rules and track references from url_for calls."""
    endpoints = {}

    # First, add all registered rules
    for rule in rules:
        rule_str = str(rule)
        primary_methods = (rule.methods or {"GET"}) - {"HEAD", "OPTIONS"}
        method = sorted(primary_methods)[0] if primary_methods else "GET"
        key = f"{method} {rule_str}"
        endpoints[key] = {"referenced_by": [], "registered": True}

    # Track url_for references
    for endpoint, args, kwargs, lineno in all_url_for_calls:
        # Try to match to a registered rule
        matched = False
        for rule in rules:
            if rule.endpoint == endpoint:
                rule_str = str(rule)
                primary_methods = (rule.methods or {"GET"}) - {"HEAD", "OPTIONS"}
                method = sorted(primary_methods)[0] if primary_methods else "GET"
                key = f"{method} {rule_str}"
                if key in endpoints:
                    endpoints[key]["referenced_by"].append(endpoint)
                matched = True
                break
        if not matched:
            # Unregistered endpoint reference
            key = f"UNREGISTERED {endpoint}"
            if key not in endpoints:
                endpoints[key] = {"referenced_by": [], "registered": False}
            endpoints[key]["referenced_by"].append(endpoint)

    return endpoints



# ---------------------------------------------------------------------------
# Inventory assembly (schema version 2)
#
# Counting rules, stated once so the counts mean one thing:
#   routes   one entry per rule in the Flask url_map (static included), never per template
#   pages    routes that answer GET and render a template
#   controls one entry per link, button, form, modal, table or data-testid in a template
#            (macro-made buttons and modals count once, at the call site)
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 2
SHARED_PAGE_LIMIT = 15
URL_FOR_RE = re.compile(r"""url_for\(\s*['"]([^'"]+)['"]""")
GUARD_RE = re.compile(r"(?:^|_)(?:required|require|requires|permission|permissions)(?:_|$)|^(?:admin|is_org_admin)$|login_required")
MUTATING_WORDS = re.compile(
    r"\b(delete|remove|approve|reject|submit|save|create|confirm|archive|publish|revoke|reset|"
    r"disconnect|sign[ -]?out|log[ -]?out|logout|invite|send|update|apply|accept|decline|upgrade|"
    r"purchase|subscribe|pay|cancel subscription|deactivate|activate|promote|merge|restore)\b",
    re.I,
)
MUTATING_PATH = re.compile(r"/(delete|remove|logout|signout|sign-out|approve|reject|revoke|disconnect|archive|deactivate)(/|$|\?)", re.I)


def template_index(template_files_list):
    """name as Jinja resolves it (path under its templates root) -> repo-relative path."""
    index = {}
    for root, path in template_files_list:
        key = Path(path).relative_to(Path(root)).as_posix()
        rel = Path(path).relative_to(ROOT).as_posix()
        index.setdefault(key, rel)
    return index


def _is_guard_function(func_node: _ast.FunctionDef) -> bool:
    """Check if a function is a guard function by analyzing its body.
    
    A guard function is one that ENFORCES access control by:
    - Calling abort(401) or abort(403)
    - Returning a response tuple with status 401 or 403 (e.g., jsonify(...), 403)
    
    Simply checking current_user.is_authenticated is not enough - the function
    must take action (abort or return error) when the check fails.
    """
    has_auth_check = False
    has_enforcement = False
    
    for node in _ast.walk(func_node):
        # Check for abort(401) or abort(403) calls
        if isinstance(node, _ast.Call):
            if isinstance(node.func, _ast.Name) and node.func.id == "abort":
                for arg in node.args:
                    if isinstance(arg, _ast.Constant) and arg.value in (401, 403):
                        has_enforcement = True
        
        # Check for current_user attribute access patterns
        if isinstance(node, _ast.Attribute):
            if isinstance(node.value, _ast.Name) and node.value.id == "current_user":
                if node.attr in ("is_authenticated", "is_org_admin", "is_platform_admin", "has_role"):
                    has_auth_check = True
        
        # Check for return statements with 401/403 status (tuple return)
        if isinstance(node, _ast.Return):
            if isinstance(node.value, _ast.Tuple):
                for elt in node.value.elts:
                    if isinstance(elt, _ast.Constant) and elt.value in (401, 403):
                        has_enforcement = True
    
    # A guard function must have BOTH an auth check AND enforcement
    return has_auth_check and has_enforcement


def _get_guard_functions_from_module(module) -> dict[str, bool]:
    """Get a mapping of function name -> is_guard for all functions in a module."""
    import inspect
    guard_map = {}
    try:
        src = inspect.getsource(module)
        tree = _ast.parse(src)
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                guard_map[node.name] = _is_guard_function(node)
    except (TypeError, OSError, SyntaxError):
        pass
    return guard_map


def view_guards(view_func):
    """Names of the decorators and in-function calls that gate access (login, role, permission)."""
    import textwrap
    import inspect
    try:
        src = textwrap.dedent(inspect.getsource(inspect.unwrap(view_func)))
        tree = _ast.parse(src)
    except (TypeError, OSError, SyntaxError):
        return []
    
    guards = []
    view_module = inspect.getmodule(view_func)
    guard_func_map = _get_guard_functions_from_module(view_module) if view_module else {}
    
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            # Check decorators
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, _ast.Call) else dec
                name = target.attr if isinstance(target, _ast.Attribute) else getattr(target, "id", "")
                if name and GUARD_RE.search(name):
                    guards.append(name)
            
            # Check in-function calls in the first 10 statements
            for stmt in node.body[:10]:
                # Direct call: _require_org_admin()
                if isinstance(stmt, _ast.Expr) and isinstance(stmt.value, _ast.Call):
                    call = stmt.value
                    if isinstance(call.func, _ast.Name):
                        func_name = call.func.id
                        if guard_func_map.get(func_name, False):
                            guards.append(func_name)
                # Assignment with call: err = _require_org_admin()
                elif isinstance(stmt, _ast.Assign):
                    for target in stmt.targets:
                        if isinstance(stmt.value, _ast.Call) and isinstance(stmt.value.func, _ast.Name):
                            func_name = stmt.value.func.id
                            if guard_func_map.get(func_name, False):
                                guards.append(func_name)
                # If statement with call: if err := _require_org_admin(): ...
                elif isinstance(stmt, _ast.If):
                    if isinstance(stmt.test, _ast.Call) and isinstance(stmt.test.func, _ast.Name):
                        func_name = stmt.test.func.id
                        if guard_func_map.get(func_name, False):
                            guards.append(func_name)
                    # Walrus operator: if (err := _require_org_admin()):
                    elif isinstance(stmt.test, _ast.NamedExpr) and isinstance(stmt.test.value, _ast.Call):
                        if isinstance(stmt.test.value.func, _ast.Name):
                            func_name = stmt.test.value.func.id
                            if guard_func_map.get(func_name, False):
                                guards.append(func_name)
            break
    return sorted(set(guards))


def _endpoint_of(*texts):
    for t in texts:
        if isinstance(t, str):
            m = URL_FOR_RE.search(t)
            if m:
                return m.group(1)
    return None


def _is_mutating(kind, c, methods_by_endpoint):
    t = c.get("target", {})
    if kind == "form":
        return t.get("method", "GET") != "GET"
    if kind == "button":
        if c.get("type") == "destructive":
            return True
        if t.get("form_method") and t["form_method"] != "GET" and t.get("button_type") != "button":
            return True
        text = " ".join(str(x or "") for x in (c.get("label"), t.get("handler"), t.get("data_action"), c.get("testid")))
        if t.get("href") is None and MUTATING_WORDS.search(text):
            return True
    href = t.get("href") or ""
    ep = t.get("endpoint")
    if kind in ("link", "button") and href:
        if ep and ep in methods_by_endpoint and "GET" not in methods_by_endpoint[ep]:
            return True
        if MUTATING_PATH.search(href):
            return True
    return False


def build_template_inventory(template_files_list=None):
    """The template half of the inventory: needs no running app, so a test can regenerate it in seconds."""
    if template_files_list is None:
        template_files_list = list(template_files())
    macro_signatures = collect_signatures(str(ROOT))
    index = template_index(template_files_list)
    rel_to_key = {v: k for k, v in index.items()}

    per_template = {}
    contents = []
    for root, path in sorted(template_files_list, key=lambda rp: Path(rp[1]).relative_to(ROOT).as_posix()):
        rel = Path(path).relative_to(ROOT).as_posix()
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                src = f.read()
        except OSError:
            continue
        contents.append((rel, src))
        extra = {}
        buttons, forms, tables, modals, errors, refs, macro_calls, url_for_calls = extract_elements_from_template(
            rel, src, macro_signatures, extra
        )
        per_template[rel] = {
            "buttons": buttons, "forms": forms, "tables": tables, "modals": modals,
            "links": extra.get("links", []), "testids": extra.get("testids", []),
            "refs": refs, "errors": errors,
        }
    templates_sha256 = hashlib.sha256("".join(c for _, c in sorted(contents)).encode("utf-8")).hexdigest()

    controls = []
    for rel in sorted(per_template):
        d = per_template[rel]
        counters = defaultdict(int)

        def add(kind, c, target):
            n = counters[kind]
            counters[kind] += 1
            controls.append({
                "id": f"{rel}::{kind}::{n}",
                "kind": kind,
                "template": rel,
                "label": c.get("label") or c.get("title") or "",
                "testid": c.get("testid"),
                "type": c.get("type"),
                "via_macro": c.get("via_macro"),
                "line": c.get("line"),
                "target": {k: v for k, v in target.items() if v not in (None, "", [])},
            })

        for c in d["links"]:
            add("link", c, {"href": c["href"], "endpoint": _endpoint_of(c["href"]),
                            "modal_id": c.get("modal_id"), "handler": c.get("onclick")})
        for c in d["buttons"]:
            if c.get("from_anchor"):
                continue
            add("button", c, {"href": c.get("href"), "endpoint": _endpoint_of(c.get("href"), c.get("data_action")),
                              "modal_id": c.get("modal_id"), "handler": c.get("handler"),
                              "data_action": c.get("data_action"), "form_method": c.get("form_method"),
                              "button_type": c.get("button_type")})
        for c in d["forms"]:
            add("form", c, {"action": c.get("action"), "endpoint": _endpoint_of(c.get("action")),
                            "method": c.get("method"), "fields": c.get("fields")})
        for c in d["modals"]:
            add("modal", c, {"modal_id": c.get("id"), "handler": c.get("submit_handler")})
        for c in d["tables"]:
            add("table", c, {"endpoint": _endpoint_of(c.get("source_endpoint")), "href": c.get("source_endpoint")})
        for c in d["testids"]:
            if c["tag"] in ("a", "button", "form"):
                continue  # the link, button or form control already carries its testid
            add("testid", {"label": c["tag"], "testid": c["testid"]}, {})

    templates = {}
    for rel, d in sorted(per_template.items()):
        refs = d["refs"]
        every_ref = refs["extends"] | refs["includes"] | refs["imports"] | refs["from_imports"]
        templates[rel] = {
            "name": rel_to_key.get(rel),
            "extends": sorted(i for i in (index.get(x) for x in refs["extends"]) if i),
            "includes": sorted(i for i in (index.get(x) for x in refs["includes"]) if i),
            "imports": sorted(i for i in (index.get(x) for x in (refs["imports"] | refs["from_imports"])) if i),
            "unresolved_refs": sorted(x for x in every_ref if isinstance(x, str) and x not in index),
            "parse_errors": d["errors"],
        }
    return {"templates_sha256": templates_sha256, "controls": controls, "templates": templates, "_index": index}


def _closure(rel, templates):
    seen, stack = set(), [rel]
    while stack:
        t = stack.pop()
        if t in seen or t not in templates:
            continue
        seen.add(t)
        info = templates[t]
        stack.extend(info["extends"] + info["includes"] + info["imports"])
    return sorted(seen)


def build_inventory(template_files_list=None, application=None):
    """Build the whole inventory dict (routes need the app booted; controls do not).

    Deterministic: sorted keys and lists, no timestamps. Same tree -> same bytes.
    """
    if template_files_list is None:
        template_files_list = list(template_files())
    tpl = build_template_inventory(template_files_list)
    index = tpl.pop("_index")
    controls, templates = tpl["controls"], tpl["templates"]

    if application is None:
        sys.path.insert(0, str(ROOT))
        from app import create_app
        application = create_app("testing")

    route_templates = get_route_templates(application)
    methods_by_endpoint = defaultdict(set)
    for rule in application.url_map.iter_rules():
        methods_by_endpoint[rule.endpoint] |= set(rule.methods or ())

    routes = []
    rendered_on = defaultdict(set)
    reached = set()
    for rule in application.url_map.iter_rules():
        methods = sorted((rule.methods or {"GET"}) - {"HEAD", "OPTIONS"})
        found = route_templates.get(rule.endpoint, [(None, None)])
        names = sorted({t for t, _ in found if t})
        unresolved = sorted({u for _, u in found if u})
        primary = sorted({index[n] for n in names if n in index})
        closure = sorted({c for p in primary for c in _closure(p, templates)})
        reached.update(closure)
        for c in closure:
            rendered_on[c].add(rule.rule)
        view = application.view_functions.get(rule.endpoint)
        guards = view_guards(view) if view else []
        routes.append({
            "id": f"{rule.endpoint} {rule.rule}",
            "rule": rule.rule,
            "endpoint": rule.endpoint,
            "blueprint": rule.endpoint.rsplit(".", 1)[0] if "." in rule.endpoint else None,
            "methods": methods,
            "login_required": bool(guards),
            "guards": guards,
            "templates": primary,
            "templates_missing": sorted(n for n in names if n not in index),
            "template_unresolved": unresolved,
            "closure": closure,
            "page": "GET" in methods and bool(primary),
            "has_parameters": "<" in rule.rule,
        })
    routes.sort(key=lambda r: r["id"])

    for c in controls:
        t = c["target"]
        c["mutating"] = _is_mutating(c["kind"], c, methods_by_endpoint)
        pages = sorted(rendered_on.get(c["template"], ()))
        c["rendered_on_count"] = len(pages)
        c["rendered_on"] = pages if len(pages) <= SHARED_PAGE_LIMIT else None
        if t.get("endpoint") and t["endpoint"] not in methods_by_endpoint:
            t["endpoint_registered"] = False
    for rel, info in templates.items():
        info["rendered_on_count"] = len(rendered_on.get(rel, ()))
    unreached = sorted(rel for rel in templates if rel not in reached)

    kinds = defaultdict(int)
    for c in controls:
        kinds[c["kind"]] += 1
    counts = {
        "routes": len(routes),
        "pages": sum(1 for r in routes if r["page"]),
        "pages_without_parameters": sum(1 for r in routes if r["page"] and not r["has_parameters"]),
        "controls": len(controls),
        "controls_by_kind": dict(sorted(kinds.items())),
        "controls_mutating": sum(1 for c in controls if c["mutating"]),
        "templates": len(templates),
        "templates_not_reached_by_a_route": len(unreached),
    }
    return {
        "schema": SCHEMA_VERSION,
        "templates_sha256": tpl["templates_sha256"],
        "counts": counts,
        "routes": routes,
        "controls": controls,
        "templates": templates,
        "templates_not_reached_by_a_route": unreached,
    }


def write_inventory(inventory, output_path=None):
    output_path = Path(output_path) if output_path else STATIC_DIR / "component-inventory.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(inventory, f, indent=1, sort_keys=True)
        f.write("\n")
    return output_path


def main():
    """Boot the app, build the inventory, write docs/component-inventory.json."""
    inventory = build_inventory()
    path = write_inventory(inventory)
    print(f"Component inventory written to {path}")
    for k, v in inventory["counts"].items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
