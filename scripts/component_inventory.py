#!/usr/bin/env python
"""Component inventory generator.

Reads every Jinja template and writes docs/component-inventory.json listing,
per route, every button, form, table and modal, plus every endpoint the
templates reference. Deterministic: same tree, byte-identical JSON.
"""
from __future__ import annotations

import ast as _ast
import hashlib
import json
import os
import re
import inspect
import sys
from collections import defaultdict
from datetime import datetime, timezone

from jinja2 import Environment
from jinja2 import meta as _meta

from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_TEMPLATES = os.path.join(ROOT, "app", "templates")
APP_MODULES = os.path.join(ROOT, "app", "modules")
STATIC_DIR = os.path.join(ROOT, "docs")

JINJA_SUFFIXES = (".html", ".jinja", ".jinja2")
SKIP_DIRS = {"__pycache__", "node_modules", ".git"}

# ---------------------------------------------------------------------------
# Template file discovery
# ---------------------------------------------------------------------------


def template_files():
    """Yield (root, full_path) for every Jinja template in the tree."""
    seen = set()
    roots = [APP_TEMPLATES]
    if os.path.isdir(APP_MODULES):
        for entry in sorted(os.listdir(APP_MODULES)):
            mod_templates = os.path.join(APP_MODULES, entry, "templates")
            if os.path.isdir(mod_templates):
                roots.append(mod_templates)
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in sorted(filenames):
                if fn.endswith(".j2"):
                    continue
                if fn.endswith(JINJA_SUFFIXES):
                    full = os.path.join(dirpath, fn)
                    if full not in seen:
                        seen.add(full)
                        yield root, full


# ---------------------------------------------------------------------------
# Jinja template reference extraction via jinja2.meta
# ---------------------------------------------------------------------------

def extract_template_refs(template_path: str) -> dict:
    """Extract all template references from a Jinja template file.

    Returns dict with keys: "includes", "extends", "imports", "from_imports"
    each mapping to a set of template names referenced.
    """
    try:
        with open(template_path, encoding="utf-8", errors="replace") as f:
            src = f.read()
    except OSError:
        return {
            "includes": set(),
            "extends": set(),
            "imports": set(),
            "from_imports": set(),
        }

    env = Environment()
    try:
        ast = env.parse(src)
    except Exception:
        return {
            "includes": set(),
            "extends": set(),
            "imports": set(),
            "from_imports": set(),
        }

    # Use jinja2.meta to get all referenced templates
    referenced = _meta.find_referenced_templates(ast)

    # Now categorise by parsing the source for directive patterns
    includes = set()
    extends = set()
    imports = set()
    from_imports = set()

    # Normalise: collapse whitespace and handle multi-line templates
    # Search for {% include %}, {% extends %}, {% import %}, {% from ... import %}

    # Find include targets
    for m in re.finditer(r"{%\s*include\s+['\"]([^'\"]+)['\"]\s*%}", src):
        includes.add(m.group(1))

    # Find extends targets
    for m in re.finditer(r"{%\s*extends\s+['\"]([^'\"]+)['\"]\s*%}", src):
        extends.add(m.group(1))

    # Find import targets
    for m in re.finditer(r"{%\s*import\s+['\"]([^'\"]+)['\"]\s*%}", src):
        imports.add(m.group(1))

    # Find from-import targets (handle both single and multi-import lines)
    for m in re.finditer(r"{%\s*from\s+['\"]([^'\"]+)\"\s+import\s+([^%}]+)\s*%}", src):
        from_imports.add(m.group(1))  # the template name

    # Cross-reference with jinja2.meta findings
    # Any referenced template not caught by regex should still be included
    for name in referenced:
        # Normalise the name - strip path prefixes and extensions
        norm_name = os.path.splitext(os.path.basename(name))[0]
        # Check if it's already in any category
        found = False
        for cat in (includes, extends, imports, from_imports):
            if norm_name in cat or name in cat:
                found = True
                break
        if not found:
            # Add to from_imports as fallback
            from_imports.add(norm_name)

    return {
        "includes": includes,
        "extends": extends,
        "imports": imports,
        "from_imports": from_imports,
    }


# ---------------------------------------------------------------------------
# Modal/macro name mapping
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


# ---------------------------------------------------------------------------
# HTML element extraction from cleaned template source
# ---------------------------------------------------------------------------

def strip_jinja_blocks(src: str) -> str:
    """Strip {% ... %} blocks from rebuilt template source.

    Keeps both branches of {% if %} ... {% endif %}.
    Drops comments ({# #}).
    """
    output = []
    i = 0
    depth = 0
    in_if = False

    while i < len(src):
        # Look for {% ... %}
        if i + 1 < len(src) and src[i] == '{' and src[i + 1] == '%':
            j = src.find("%}", i + 2)
            if j == -1:
                j = len(src)
            else:
                j += 2

            block_content = src[i + 2:j].strip()
            stripped = block_content.lstrip()

            if stripped.startswith("if "):
                depth += 1
                in_if = True
                i = j
                continue
            elif stripped.startswith("else ") and in_if:
                # Track else but don't increase depth beyond if
                in_if = False
                i = j
                continue
            elif stripped.startswith("endif"):
                depth = max(0, depth - 1)
                in_if = False
                i = j
                continue
            else:
                i = j
                continue

        # Look for {# ... #} comment – drop it
        if i + 1 < len(src) and src[i] == '{' and src[i + 1] == '#':
            j = src.find("#}", i + 2)
            if j == -1:
                j = len(src)
            else:
                j += 2
            i = j
            continue

        # Regular text – emit it
        output.append(src[i])
        i += 1

    return "".join(output)


class TemplateElementExtractor(HTMLParser):
    """Extract buttons, forms, tables, and modals from cleaned HTML source."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.buttons = []
        self.forms = []
        self.tables = []
        self.modals = []
        self._tag_stack = []  # stack of tag names currently open
        self._tag_attrs_stack = []  # parallel stack of attrs dicts
        self._tag_data = ""
        self._deep = 0
        self._in_script = False
        self._in_style = False

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
        # Pop tags from the stack until we find the matching one
        found = False
        while self._tag_stack:
            top = self._tag_stack.pop()
            if top == tag_l:
                # Pop the corresponding attrs from the parallel stack
                if self._tag_attrs_stack:
                    popped_attrs = self._tag_attrs_stack.pop()
                else:
                    popped_attrs = {}
                found = True
                self._process_tag(tag_l, popped_attrs)
                break
        # If the tag wasn't found in the stack, just clean up
        if not found and self._tag_attrs_stack:
            self._tag_attrs_stack = []
        # Clean up script/style tracking
        if self._in_script and tag_l == "script":
            self._in_script = False
        elif self._in_style and tag_l == "style":
            self._in_style = False

    def _process_tag(self, tag_l, attrs):
        if tag_l == "button":
            self._extract_button(attrs)
        elif tag_l == "form":
            self._extract_form(attrs)
        elif tag_l == "table":
            self._extract_table(attrs)
        elif tag_l == "a":
            # Check if this <a> tag functions as a button (has public-btn class)
            class_attr = attrs.get("class", "")
            if isinstance(class_attr, str) and "public-btn" in class_attr:
                self._extract_button_as_link(attrs)

    def _extract_button(self, attrs):
        # attrs is a dict in Python 3.12's HTMLParser
        btn_attrs = dict(attrs) if not isinstance(attrs, dict) else attrs

        btn = {
            "label": "",
            "type": "noop",
            "handler": None,
            "data_action": None,
            "href": None,
            "modal_id": None,
            "download": False,
            "resolved": True,
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

        # 1. destructive
        if "destructive" in class_attr.lower() or re.search(
            r"Platform\.modal\.confirm", on_click or ""
        ):
            btn["type"] = "destructive"
        # 2. download
        elif btn["download"]:
            btn["type"] = "download"
        # 3. navigate (already handled href, but check for url_for patterns)
        elif btn["href"]:
            btn["type"] = "navigate"
        # 4. modal - opener patterns
        elif re.search(r"data-modal-open=", str(btn_attrs)) or re.search(
            r"Platform\.modal\.open", on_click or ""
        ):
            btn["type"] = "modal"
        # 5. inline-render (any other handler)
        elif on_click:
            btn["type"] = "inline-render"
        # 6. noop
        else:
            btn["type"] = "noop"

        # Extract label from tag content
        if self._tag_data.strip():
            btn["label"] = self._tag_data.strip()[:100]

        # If modal type, try to extract modal id from data-modal-open
        if btn["type"] == "modal":
            if "data-modal-open" in btn_attrs:
                btn["modal_id"] = btn_attrs["data-modal-open"]
            # Also check for id attribute
            if "id" in btn_attrs:
                btn["modal_id"] = btn_attrs["id"]

        self.buttons.append(btn)

    def _extract_button_as_link(self, attrs):
        """Extract button data from an <a> tag with public-btn class."""
        btn = {
            "label": "",
            "type": "noop",
            "handler": None,
            "data_action": None,
            "href": None,
            "modal_id": None,
            "download": False,
            "resolved": False,  # <a> tags are typically not "resolved" in the same way
        }

        # Extract href
        if "href" in attrs:
            btn["href"] = attrs["href"]
            btn["type"] = "navigate"

        # Check for data-action
        if "data-action" in attrs:
            btn["data_action"] = attrs["data-action"]

        # Check for download attribute
        if "download" in attrs:
            btn["download"] = True
            val = attrs["download"]
            if val and re.search(r"\.(csv|xlsx|pdf|export)$", val, re.I):
                btn["type"] = "download"

        # Determine type based on class, onclick, etc.
        class_attr = attrs.get("class", "")
        on_click = attrs.get("onclick")

        # 1. destructive
        if isinstance(class_attr, str) and "destructive" in class_attr.lower():
            btn["type"] = "destructive"
        # 2. download
        elif btn["download"]:
            btn["type"] = "download"
        # 3. navigate (already handled href)
        elif btn["href"]:
            btn["type"] = "navigate"
        # 4. modal - opener patterns
        elif re.search(r"data-modal-open=", str(attrs)) or re.search(
            r"Platform\.modal\.open", on_click or ""
        ):
            btn["type"] = "modal"
        # 5. inline-render (any other handler)
        elif on_click:
            btn["type"] = "inline-render"
        # 6. noop
        else:
            btn["type"] = "noop"

        # Extract label from tag content
        if self._tag_data.strip():
            btn["label"] = self._tag_data.strip()[:100]

        # If modal type, try to extract modal id from data-modal-open
        if btn["type"] == "modal":
            if "data-modal-open" in attrs:
                btn["modal_id"] = attrs["data-modal-open"]
            if "id" in attrs:
                btn["modal_id"] = attrs["id"]

        self.buttons.append(btn)

    def _extract_form(self, attrs):
        # attrs is a dict in Python 3.12's HTMLParser
        btn_attrs = dict(attrs) if not isinstance(attrs, dict) else attrs

        form = {
            "action": "",
            "method": "GET",
            "fields": [],
        }

        if "action" in btn_attrs:
            form["action"] = btn_attrs["action"]
        if "method" in btn_attrs:
            form["method"] = btn_attrs["method"].upper()

        if form["action"] or form["method"] != "GET":
            self.forms.append(form)

    def _extract_table(self, attrs):
        # attrs is a dict in Python 3.12's HTMLParser
        btn_attrs = dict(attrs) if not isinstance(attrs, dict) else attrs

        table = {
            "source_endpoint": btn_attrs.get("source_endpoint"),
        }
        self.tables.append(table)


def extract_elements_from_source(src: str, filename: str):
    """Extract buttons, forms, tables, modals from a template source.

    Returns (buttons, forms, tables, modals) tuples.
    """
    cleaned = strip_jinja_blocks(src)
    parser = TemplateElementExtractor()
    try:
        parser.feed(cleaned)
    except Exception:
        pass
    return parser.buttons, parser.forms, parser.tables, parser.modals


# ---------------------------------------------------------------------------
# Endpoint extraction from url_map rules
# ---------------------------------------------------------------------------

def extract_endpoints_from_rules(rules):
    """Extract endpoint references from url_map rules.

    Returns dict keyed by "METHOD /rule" with {"referenced_by": []}.
    """
    endpoints = {}
    for rule in rules:
        rule_str = str(rule)
        primary_methods = (rule.methods or {"GET"}) - {"HEAD", "OPTIONS"}
        if primary_methods:
            method = sorted(primary_methods)[0]
        else:
            method = "GET"
        key = f"{method} {rule_str}"
        endpoints[key] = {"referenced_by": []}
    return endpoints


# ---------------------------------------------------------------------------
# View function render target extraction
# ---------------------------------------------------------------------------

def _render_target_from_source(view_src: str) -> set:
    """Extract render_template()/stream_template() target names from view source."""
    targets = set()
    try:
        tree = _ast.parse(view_src)
    except SyntaxError:
        return targets

    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, _ast.Attribute) else getattr(func, "id", None)
        if name not in ("render_template", "stream_template") or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, _ast.Constant) and isinstance(first.value, str):
            targets.add(first.value)

    return targets


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

    # Step 1: Extract template references from each file
    template_refs = {}  # path -> {includes, extends, imports, from_imports}
    template_names = set()  # all template basenames (without extension)

    for root, path in template_files_list:
        rel = os.path.relpath(path, ROOT)
        basename = os.path.basename(path)
        name = os.path.splitext(basename)[0]
        template_names.add(name)

        refs = extract_template_refs(path)
        template_refs[path] = refs

    # Step 2: Compute templates_sha256
    template_contents = []
    for root, path in template_files_list:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                template_contents.append((os.path.relpath(path, ROOT), f.read()))
        except OSError:
            pass
    template_contents.sort(key=lambda x: x[0])
    sorted_conts = "".join(c for _, c in template_contents)
    templates_sha256 = hashlib.sha256(sorted_conts.encode("utf-8")).hexdigest()

    # Step 3: Build reference graph
    # reached_from: {target_template_name: set of source_template_relative_paths}
    reached_from = defaultdict(set)  # target name -> source paths

    for root, path in template_files_list:
        refs = template_refs[path]
        rel_path = os.path.relpath(path, ROOT).replace("\\", "/")

        for inc in refs["includes"]:
            inc_name = os.path.splitext(os.path.basename(inc))[0]
            reached_from[inc_name].add(rel_path)

        for ext in refs["extends"]:
            ext_name = os.path.splitext(os.path.basename(ext))[0]
            reached_from[ext_name].add(rel_path)

        for imp in refs["imports"]:
            imp_name = os.path.splitext(os.path.basename(imp))[0]
            reached_from[imp_name].add(rel_path)

        for ftarget in refs["from_imports"]:
            reached_from[ftarget].add(rel_path)

    # Step 4: Boot the app and get rules
    sys.path.insert(0, ROOT)
    from app import create_app

    application = create_app("testing")
    rules = list(application.url_map.iter_rules())

    # Step 5: Extract route entries
    route_templates = set()  # set of template names rendered by views
    routes = []

    for rule in rules:
        rule_str = str(rule)
        endpoint = rule.endpoint
        view_func = application.view_functions.get(endpoint)
        if view_func is None:
            route_entry = {
                "route": rule_str,
                "template": None,
                "buttons": [],
                "forms": [],
                "tables": [],
                "modals": [],
            }
            routes.append(route_entry)
            continue

        # Get view source
        try:
            view_src = inspect.getsource(view_func)
        except (TypeError, OSError):
            route_entry = {
                "route": rule_str,
                "template": None,
                "buttons": [],
                "forms": [],
                "tables": [],
                "modals": [],
            }
            routes.append(route_entry)
            continue

        # Find render_template targets
        rendered = _render_target_from_source(view_src)

        # Also try unwrapped function
        try:
            unwrapped = inspect.unwrap(view_func)
            view_src2 = inspect.getsource(unwrapped)
            rendered.update(_render_target_from_source(view_src2))
        except (TypeError, OSError):
            pass

        # Determine the primary template for this route
        # If exactly one template is rendered, use it; otherwise None
        if len(rendered) == 1:
            template = rendered.pop()
            route_templates.add(template)
        elif len(rendered) > 1:
            # Multiple templates – pick the first as primary
            template = next(iter(rendered)) if rendered else None
            route_templates.update(rendered)
        else:
            template = None

        # Extract elements from the associated template(s)
        elements = {
            "buttons": [],
            "forms": [],
            "tables": [],
            "modals": [],
        }

# For each rendered template, find the file and extract elements
        for tmpl_name in rendered:
            # Find template file by matching the template name
            tf = None
            for root, path in template_files_list:
                rel = os.path.relpath(path, ROOT).replace("\\", "/")
                # Normalize: strip app/templates/ or app/modules/*/templates/ prefix
                normalized_rel = rel
                if rel.startswith("app/templates/"):
                    normalized_rel = rel[len("app/templates/"):]
                elif rel.startswith("app/modules/"):
                    # Strip app/modules/<name>/templates/ prefix
                    idx = rel.find("/templates/")
                    if idx >= 0:
                        normalized_rel = rel[idx + len("/templates/"):]
                # Match either by normalized path, basename (without extension) or by full relative path
                basename = os.path.basename(path)
                tpl_name_noext = os.path.splitext(basename)[0]
                # Check if the template name matches (either normalized path, just name, or basename)
                if normalized_rel == tmpl_name or tpl_name_noext == tmpl_name or basename == tmpl_name:
                    tf = (root, path)
                    break

            if tf:
                root, path = tf
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        tpl_src = f.read()
                    buttons, forms, tables, modals = extract_elements_from_source(
                        tpl_src, os.path.relpath(path, ROOT)
                    )
                    elements["buttons"].extend(buttons)
                    elements["forms"].extend(forms)
                    elements["tables"].extend(tables)
                    elements["modals"].extend(modals)
                except Exception:
                    pass

        # Also extract elements from templates reached via include/extends/import
        # that are part of this route's template graph
        for target_name, source_paths in reached_from.items():
            if target_name in rendered:
                for source_rel in source_paths:
                    # Find the source template file
                    for root, path in template_files_list:
                        basename = os.path.basename(path)
                        source_name = os.path.splitext(basename)[0]
                        if source_name == target_name:
                            try:
                                with open(path, encoding="utf-8", errors="replace") as f:
                                    tpl_src = f.read()
                                buttons, forms, tables, modals = extract_elements_from_source(
                                    tpl_src, os.path.relpath(path, ROOT)
                                )
                                elements["buttons"].extend(buttons)
                                elements["forms"].extend(forms)
                                elements["tables"].extend(tables)
                                elements["modals"].extend(modals)
                            except Exception:
                                pass

        # Deduplicate buttons by label (keep first occurrence)
        seen_labels = set()
        deduped_buttons = []
        for btn in elements["buttons"]:
            label = btn.get("label", "")
            if label not in seen_labels:
                seen_labels.add(label)
                deduped_buttons.append(btn)
        elements["buttons"] = deduped_buttons

        # Deduplicate forms by action
        seen_actions = set()
        deduped_forms = []
        for frm in elements["forms"]:
            action = frm.get("action", "")
            if action not in seen_actions:
                seen_actions.add(action)
                deduped_forms.append(frm)
        elements["forms"] = deduped_forms

        # Deduplicate tables by source_endpoint
        seen_endpoints = set()
        deduped_tables = []
        for tbl in elements["tables"]:
            ep = tbl.get("source_endpoint", "")
            if ep not in seen_endpoints:
                seen_endpoints.add(ep)
                deduped_tables.append(tbl)
        elements["tables"] = deduped_tables

        # Deduplicate modals by id
        seen_modal_ids = set()
        deduped_modals = []
        for md in elements["modals"]:
            m_id = md.get("id", "")
            if m_id not in seen_modal_ids:
                seen_modal_ids.add(m_id)
                deduped_modals.append(md)
        elements["modals"] = deduped_modals

        # Build route entry
        route_entry = {
            "route": rule_str,
            "template": template,
            "buttons": elements["buttons"],
            "forms": elements["forms"],
            "tables": elements["tables"],
            "modals": elements["modals"],
        }

        routes.append(route_entry)

    # Step 6: Extract endpoints
    endpoints = extract_endpoints_from_rules(rules)

    # Step 7: Find unrouted templates
    # A template is unrouted if it's not rendered by any view AND not reached
    # from any other template via include/extends/import
    reached_set = set()
    for target in reached_from:
        reached_set.add(target)

    unrouted = sorted(
        name for name in template_names
        if name not in route_templates and name not in reached_set
    )

    # Step 8: Assemble inventory
    inventory = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "templates_sha256": templates_sha256,
        "routes": routes,
        "endpoints": endpoints,
    }

    if unrouted:
        inventory["unrouted_templates"] = unrouted

    return inventory


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def main():
    """Boot the app, build the inventory, write docs/component-inventory.json."""
    # Get all template files
    templates = list(template_files())

    # Boot the app for rule extraction
    sys.path.insert(0, ROOT)
    from app import create_app

    application = create_app("testing")
    rules = list(application.url_map.iter_rules())

    # Build the inventory
    inventory = build_inventory(template_files_list=templates)

    # Write the output file
    output_path = os.path.join(STATIC_DIR, "component-inventory.json")
    os.makedirs(STATIC_DIR, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(inventory, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"Component inventory written to {output_path}")
    print(f"Routes: {len(inventory['routes'])}")
    print(f"Endpoints: {len(inventory['endpoints'])}")
    print(f"Templates SHA256: {inventory['templates_sha256']}")
    if "unrouted_templates" in inventory:
        print(f"Unrouted templates: {len(inventory['unrouted_templates'])}")


if __name__ == "__main__":
    main()