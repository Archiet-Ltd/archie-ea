"""The four website prompts, as OperateIQ wrote them, and the small renderer they need.

The files under ``prompts/`` are verbatim copies; the first line of each names the file
and the commit they were copied from. They use Handlebars, so this renders the subset
they use: ``{{name}}``, ``{{a.b}}``, ``{{this}}``, ``{{@key}}``, ``{{#if}}`` and ``{{#each}}``.
Values are written as they are (a prompt is not HTML, so nothing is escaped).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SOURCE_COMMIT = "cbc6a9e"
PROMPT_NAMES = ("website_analysis", "website_inference", "page_rendering_strategy", "html_structure_discovery")

_DIRECTORY = Path(__file__).parent / "prompts"
_TAG = re.compile(r"\{\{(!--.*?--|[^{}]*?)\}\}", re.S)
_MISSING = object()


def load(name: str) -> str:
    if name not in PROMPT_NAMES:
        raise KeyError(name)
    return (_DIRECTORY / (name + ".hbs")).read_text(encoding="utf-8")


def _lookup(path: str, scopes: list) -> Any:
    path = path.strip()
    if path in ("this", "."):
        return scopes[-1]["this"]
    if path.startswith("@"):
        return scopes[-1].get(path)
    if path.startswith("this."):
        path = path[5:]
        scopes = scopes[-1:]
    for scope in reversed(scopes):
        value: Any = scope["this"]
        for part in path.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                value = _MISSING
                break
        if value is not _MISSING:
            return value
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, (list, tuple, dict, str)):
        return len(value) > 0
    return bool(value)


def _is_block(inner: str) -> bool:
    return inner.startswith(("#", "/", "!"))


def _strip_block_lines(text: str) -> str:
    """Remove the indentation and line break around a block or comment tag that sits alone on its
    line, as Handlebars does, so a skipped block leaves no blank line behind."""
    def tidy(line: str) -> str:
        tags = _TAG.findall(line)
        if tags and _TAG.sub("", line).strip() == "" and all(_is_block(t.strip()) for t in tags):
            return line.strip() + "\x00"
        return line + "\n"

    return "".join(tidy(line) for line in text.split("\n")).replace("\x00", "").rstrip("\n") + "\n"


def _parse(text: str):
    """A tree of text, ``("var", path)``, ``("if", path, children)`` and ``("each", path, children)``."""
    root: list = []
    stack = [root]
    names = []
    position = 0
    for match in _TAG.finditer(text):
        if match.start() > position:
            stack[-1].append(text[position:match.start()])
        position = match.end()
        inner = match.group(1).strip()
        if inner.startswith("!"):
            continue
        if inner.startswith("#"):
            kind, _, path = inner[1:].partition(" ")
            node = (kind, path.strip(), [])
            stack[-1].append(node)
            stack.append(node[2])
            names.append(kind)
        elif inner.startswith("/"):
            if not names or names.pop() != inner[1:]:
                raise ValueError("unbalanced {{%s}}" % inner)
            stack.pop()
        else:
            stack[-1].append(("var", inner))
    stack[-1].append(text[position:])
    if names:
        raise ValueError("unclosed {{#%s}}" % names[-1])
    return root


def _emit(nodes: list, scopes: list, out: list) -> None:
    for node in nodes:
        if isinstance(node, str):
            out.append(node)
        elif node[0] == "var":
            value = _lookup(node[1], scopes)
            out.append("" if value is None else str(value))
        elif node[0] == "if":
            if _truthy(_lookup(node[1], scopes)):
                _emit(node[2], scopes, out)
        elif node[0] == "each":
            items = _lookup(node[1], scopes)
            if isinstance(items, dict):
                pairs = list(items.items())
            else:
                pairs = [(index, item) for index, item in enumerate(items or [])]
            for key, item in pairs:
                _emit(node[2], scopes + [{"this": item, "@key": key, "@index": key}], out)
        else:
            raise ValueError("unknown block %s" % node[0])


def render(name: str, context: dict) -> str:
    text = _strip_block_lines(load(name))
    out: list = []
    _emit(_parse(text), [{"this": context}], out)
    return "".join(out)
