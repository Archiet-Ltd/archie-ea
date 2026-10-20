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
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
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
        self._in_script = False
        self._in_style = False
        self._macro_call_iter = iter(macro_calls)
        self._current_macro_call = None

    def handle_starttag(self, tag, attrs):
        tag_l = tag.lower()
        self._tag_stack.append(tag_l)
        self._tag_attrs_stack.append(dict(attrs))
        self._tag_data = ""

        if tag_l == "script":
            self._in_script = True
        elif tag_l == "style":
            self._in_style = True

    def handle_endtag(self, tag):
        tag_l = tag.lower()
        found = False
        while self._tag_stack:
            top = self._tag_stack.pop()
            if top == tag_l:
                if self._tag_attrs_stack:
                    popped_attrs = self._tag_attrs_stack.pop()
                else:
                    popped_attrs = {}
                found = True
                self._process_tag(tag_l, popped_attrs)
                break
        if not found and self._tag_attrs_stack:
            self._tag_attrs_stack = []
        if self._in_script and tag_l == "script":
            self._in_script = False
        elif self._in_style and tag_l == "style":
            self._in_style = False

    def handle_data(self, data):
        self._tag_data += data

    def _process_tag(self, tag_l, attrs):
        if tag_l == "button":
            self._extract_button(attrs)
        elif tag_l == "form":
            self._extract_form(attrs)
        elif tag_l == "table":
            self._extract_table(attrs)
        elif tag_l == "a":
            class_attr = attrs.get("class", "")
            if isinstance(class_attr, str) and "public-btn" in class_attr:
                self._extract_button_as_link(attrs)

    def _resolve_placeholder(self, value: str) -> str:
        """Resolve placeholder to expression text if it's a placeholder."""
        return self.placeholder_map.get(value, value)

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
        on_click = btn_attrs.get("onclick")
        class_attr = btn_attrs.get("class", "")

        # Check for modal opener attributes
        data_modal_open = btn_attrs.get("data-modal-open")

        # 1. destructive
        if "destructive" in class_attr.lower() or re.search(r"Platform\.modal\.confirm", on_click or ""):
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
        on_click = attrs.get("onclick")
        data_modal_open = attrs.get("data-modal-open")

        if isinstance(class_attr, str) and "destructive" in class_attr.lower():
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

    def _extract_form(self, attrs):
        btn_attrs = dict(attrs) if not isinstance(attrs, dict) else attrs
        for key, val in btn_attrs.items():
            if isinstance(val, str):
                btn_attrs[key] = self._resolve_placeholder(val)

        form = {
            "action": btn_attrs.get("action", ""),
            "method": btn_attrs.get("method", "GET").upper(),
            "fields": [],
            "source_template": self.template_path,
        }

        if form["action"] or form["method"] != "GET":
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


def extract_elements_from_template(template_path: str, template_source: str, macro_signatures: dict):
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
    route_templates = {}  # rule_str -> list of (template, template_unresolved)

    for rule in application.url_map.iter_rules():
        rule_str = str(rule)
        endpoint = rule.endpoint
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
            tree = _ast.parse(view_src)
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
# Generated at timestamp (committer date of last commit touching template dirs)
# ---------------------------------------------------------------------------

def get_generated_at():
    """Get committer date of last commit touching template directories."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ci", "--", "app/templates", "app/modules"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------

def build_inventory(template_files_list=None):
    """Build the component inventory dict.

    Pure function: build_inventory(template_files) -> dict

    Returns:
        dict suitable for json.dump with sort_keys=True, lists sorted,
        two-space indent, trailing newline.
    """
    if template_files_list is None:
        template_files_list = list(template_files())

    # Collect macro signatures from all templates
    macro_signatures = collect_signatures(str(ROOT))

    # Step 1: Process each template file
    template_data = {}  # path -> {buttons, forms, tables, modals, refs, macro_calls, url_for_calls, parse_errors}
    template_names = set()
    all_macro_calls = []
    all_url_for_calls = []

    for root, path in template_files_list:
        rel_path = Path(path).relative_to(ROOT).as_posix()
        basename = Path(path).name
        name = Path(basename).stem
        template_names.add(name)

        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                tpl_src = f.read()
        except OSError:
            template_data[rel_path] = {
                "buttons": [], "forms": [], "tables": [], "modals": [],
                "refs": {"includes": set(), "extends": set(), "imports": set(), "from_imports": set()},
                "macro_calls": [], "url_for_calls": [], "parse_errors": ["read_error"],
            }
            continue

        buttons, forms, tables, modals, parse_errors, refs, macro_calls, url_for_calls = extract_elements_from_template(
            rel_path, tpl_src, macro_signatures
        )

        template_data[rel_path] = {
            "buttons": buttons,
            "forms": forms,
            "tables": tables,
            "modals": modals,
            "refs": refs,
            "macro_calls": macro_calls,
            "url_for_calls": url_for_calls,
            "parse_errors": parse_errors,
        }

        all_macro_calls.extend([(rel_path, *mc) for mc in macro_calls])
        all_url_for_calls.extend([(rel_path, *uc) for uc in url_for_calls])

    # Step 2: Compute templates_sha256
    template_contents = []
    for root, path in template_files_list:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                template_contents.append((Path(path).relative_to(ROOT).as_posix(), f.read()))
        except OSError:
            pass
    template_contents.sort(key=lambda x: x[0])
    sorted_conts = "".join(c for _, c in template_contents)
    templates_sha256 = hashlib.sha256(sorted_conts.encode("utf-8")).hexdigest()

    # Step 3: Build reference graph (reached_from)
    reached_from = defaultdict(set)
    for rel_path, data in template_data.items():
        refs = data["refs"]
        for inc in refs["includes"]:
            inc_name = Path(inc).stem
            reached_from[inc_name].add(rel_path)
        for ext in refs["extends"]:
            ext_name = Path(ext).stem
            reached_from[ext_name].add(rel_path)
        for imp in refs["imports"]:
            imp_name = Path(imp).stem
            reached_from[imp_name].add(rel_path)
        for ftarget in refs["from_imports"]:
            reached_from[ftarget].add(rel_path)

    # Step 4: Boot app and get rules + route templates
    sys.path.insert(0, str(ROOT))
    from app import create_app

    application = create_app("testing")
    rules = list(application.url_map.iter_rules())
    route_templates = get_route_templates(application)

    # Step 5: Build route entries
    route_templates_rendered = set()
    routes = []

    for rule in rules:
        rule_str = str(rule)
        rendered_info = route_templates.get(rule_str, [(None, None)])

        for template, template_unresolved in rendered_info:
            route_entry = {
                "route": rule_str,
                "template": template,
                "template_unresolved": template_unresolved,
                "buttons": [],
                "forms": [],
                "tables": [],
                "modals": [],
            }

            if template:
                route_templates_rendered.add(template)
                # Find template file
                tf = None
                for root, path in template_files_list:
                    rel = Path(path).relative_to(ROOT).as_posix()
                    normalized_rel = rel
                    if rel.startswith("app/templates/"):
                        normalized_rel = rel[len("app/templates/"):]
                    elif rel.startswith("app/modules/"):
                        idx = rel.find("/templates/")
                        if idx >= 0:
                            normalized_rel = rel[idx + len("/templates/"):]
                    basename = Path(path).name
                    tpl_name_noext = Path(basename).stem
                    if normalized_rel == template or tpl_name_noext == template or basename == template:
                        tf = (root, path)
                        break

                if tf:
                    _, tpl_path = tf
                    rel_tpl = Path(tpl_path).relative_to(ROOT).as_posix()
                    if rel_tpl in template_data:
                        tpl_data = template_data[rel_tpl]
                        route_entry["buttons"].extend(tpl_data["buttons"])
                        route_entry["forms"].extend(tpl_data["forms"])
                        route_entry["tables"].extend(tpl_data["tables"])
                        route_entry["modals"].extend(tpl_data["modals"])

            # Mark modal_missing for opener buttons
            modal_ids = {m.get("id") for m in route_entry["modals"] if m.get("id")}
            for btn in route_entry["buttons"]:
                if btn.get("type") == "modal" and btn.get("modal_id"):
                    if btn["modal_id"] not in modal_ids:
                        btn["modal_missing"] = True

            # Deduplicate
            for key in ["buttons", "forms", "tables", "modals"]:
                seen = set()
                deduped = []
                for item in route_entry[key]:
                    identifier = item.get("id") or item.get("label") or item.get("action") or item.get("source_endpoint") or str(item)
                    if identifier not in seen:
                        seen.add(identifier)
                        deduped.append(item)
                route_entry[key] = deduped

            routes.append(route_entry)

    # Step 6: Add unrouted templates (templates not rendered by any route)
    reached_set = set(reached_from.keys())
    for name in sorted(template_names):
        if name not in route_templates_rendered and name not in reached_set:
            # Find the template file
            tf = None
            for root, path in template_files_list:
                basename = Path(path).name
                tpl_name_noext = Path(basename).stem
                if tpl_name_noext == name:
                    tf = (root, path)
                    break

            route_entry = {
                "route": None,
                "template": name,
                "template_unresolved": None,
                "buttons": [],
                "forms": [],
                "tables": [],
                "modals": [],
                "reached_from": sorted(reached_from.get(name, [])),
                "unrouted": True,
            }

            if tf:
                _, tpl_path = tf
                rel_tpl = Path(tpl_path).relative_to(ROOT).as_posix()
                if rel_tpl in template_data:
                    tpl_data = template_data[rel_tpl]
                    route_entry["buttons"].extend(tpl_data["buttons"])
                    route_entry["forms"].extend(tpl_data["forms"])
                    route_entry["tables"].extend(tpl_data["tables"])
                    route_entry["modals"].extend(tpl_data["modals"])

            # Mark modal_missing for opener buttons
            modal_ids = {m.get("id") for m in route_entry["modals"] if m.get("id")}
            for btn in route_entry["buttons"]:
                if btn.get("type") == "modal" and btn.get("modal_id"):
                    if btn["modal_id"] not in modal_ids:
                        btn["modal_missing"] = True

            # Deduplicate
            for key in ["buttons", "forms", "tables", "modals"]:
                seen = set()
                deduped = []
                for item in route_entry[key]:
                    identifier = item.get("id") or item.get("label") or item.get("action") or item.get("source_endpoint") or str(item)
                    if identifier not in seen:
                        seen.add(identifier)
                        deduped.append(item)
                route_entry[key] = deduped

            routes.append(route_entry)

    # Step 7: Extract endpoints with references
    endpoints = extract_endpoints_with_refs(rules, [uc[1:] for uc in all_url_for_calls])

    # Step 8: Assemble inventory
    inventory = {
        "generated_at": get_generated_at(),
        "templates_sha256": templates_sha256,
        "routes": routes,
        "endpoints": endpoints,
    }

    return inventory


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def main():
    """Boot the app, build the inventory, write docs/component-inventory.json."""
    templates = list(template_files())
    inventory = build_inventory(template_files_list=templates)

    output_path = STATIC_DIR / "component-inventory.json"
    STATIC_DIR.mkdir(exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(inventory, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"Component inventory written to {output_path}")
    print(f"Routes: {len(inventory['routes'])}")
    print(f"Endpoints: {len(inventory['endpoints'])}")
    print(f"Templates SHA256: {inventory['templates_sha256']}")

    # Count totals for report
    total_buttons = sum(len(r.get("buttons", [])) for r in inventory["routes"])
    total_forms = sum(len(r.get("forms", [])) for r in inventory["routes"])
    total_tables = sum(len(r.get("tables", [])) for r in inventory["routes"])
    total_modals = sum(len(r.get("modals", [])) for r in inventory["routes"])
    print(f"Buttons: {total_buttons}")
    print(f"Forms: {total_forms}")
    print(f"Tables: {total_tables}")
    print(f"Modals: {total_modals}")

    unrouted = [r for r in inventory["routes"] if r.get("unrouted")]
    if unrouted:
        print(f"Unrouted templates: {len(unrouted)}")

    parse_errors = sum(1 for r in inventory["routes"] if r.get("parse_error"))
    if parse_errors:
        print(f"Parse errors: {parse_errors}")


if __name__ == "__main__":
    main()