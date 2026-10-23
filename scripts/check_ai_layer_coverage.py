#!/usr/bin/env python
"""What an ArchiMate practitioner can model, and what the AI can model for them.

The owner's goal for this product, stated 31 Aug 2026: architecture should not
be exclusive to organisations that can afford a team of architects. That means
the AI has to do the practitioner's job, not assist with it -- and the first
question is simply *how much of ArchiMate 3.2 can it actually produce*.

Measured when this gate was written: `_ELEMENT_TYPE_LAYER` in
app/models/archimate_core.py declares 58 element types across the seven layers
(technology 13, business 13, motivation 10, application 9, implementation 5,
strategy 4, physical 4). The AI agent has 37 write tools, of which the dedicated
element-creation ones cover FIVE types -- driver, goal, constraint, requirement
and risk, all motivation.

So the assistant can reason about why an architecture exists and design
solutions, and cannot model the business, technology, strategy or migration
layers, which is most of the notation and most of the work.

There is a generic `create_archimate_element` that will emit any type it is
told to. That is deliberately NOT counted as coverage, and the distinction is
the whole point of this gate: emitting a typed node is not the same as knowing
that a Business Process is a behaviour owned by a Business Actor while a
Business Function groups behaviour by capability. A practitioner's value is in
choosing the right element and relating it correctly. A tool that accepts a
type string pushes that judgement back onto the user -- which is the situation
this product exists to remove.

Not every type needs its own tool, and some never will: Physical (equipment,
facility, distribution network, material) matters to manufacturing estates and
almost nobody else. Say so with the escape hatch rather than leaving the number
misleadingly high.

A RATCHET, and one that should fall steadily. Each element type that gains a
proper AI creation path -- one that knows the type's semantics and its legal
relationships -- is a piece of the practitioner's job the product does for
someone who cannot hire one.

Escape hatch: `ai-layer-ok: <reason>` beside the type in the layer map, for a
type genuinely out of scope. Say why, and who models it instead.

    python scripts/check_ai_layer_coverage.py
    python scripts/check_ai_layer_coverage.py --count

Proven-against: `_tool_create_driver` renamed in the executor -- the count rises
by one naming `driver`, and returns when restored. Pinned by
tests/test_archimate_layer_taxonomy.py, which parses the real tree and asserts
58 element types across the seven ArchiMateLayer names with their per-layer
counts. It is not one of tests/test_gates_actually_fail.py's synthetic-tree
CASES, and cannot be: that harness plants a defect and asserts the bad count
is higher than the good one (`bad_count > 0`, `bad_count > good_count`), which
fits a gate whose failure mode is reporting MORE problems than it should. This
gate's failure mode is the opposite -- a defect in the anchor pattern below
makes `_element_types` find nothing at all and the count silently drops to
zero, which the synthetic-tree harness cannot express.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOW = re.compile(r"ai-layer-ok:[ \t]*\S")

LAYER_MAP = os.path.join("app", "models", "archimate_core.py")
TOOL_SOURCES = [
    os.path.join("app", "modules", "ai_chat", "tools", "executor.py"),
    os.path.join("app", "modules", "ai_chat", "tools", "registry.py"),
]

# A generic "create an element of type X" tool is not coverage -- it hands the
# modelling judgement back to the user. See the module docstring.
GENERIC = {"create_archimate_element", "create_element"}


def _element_types(root: str) -> dict:
    """{element type: line number} from the product's own layer map."""
    path = os.path.join(root, LAYER_MAP)
    try:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    except (OSError, UnicodeDecodeError):
        return {}
    start = source.find("_ELEMENT_TYPE_LAYER = {")
    if start < 0:
        return {}
    end = source.find("\n}", start)
    block = source[start:end if end > 0 else len(source)]

    types = {}
    for match in re.finditer(r'"([a-z_ &]+)"\s*:\s*"([a-z_ &]+)"', block):
        line_no = source.count("\n", 0, start + match.start()) + 1
        line = source.split("\n")[line_no - 1]
        if ALLOW.search(line):
            continue
        types[match.group(1)] = (match.group(2), line_no)
    return types


def _ai_creatable(root: str) -> set:
    """Element types the AI has a DEDICATED creation tool for.

    Reads archimate_specs.ELEMENT_SPECS as well as grepping for hand-written
    tools. The specs module is pure data with no application imports, so this
    stays runnable without a database — and it is the honest source: a type in
    ELEMENT_SPECS has a tool whose description carries the element's definition,
    when to use it and what it is confused with.

    Grepping alone missed them entirely. The per-type tools are installed onto
    ToolExecutor from the spec table rather than written out 58 times, so there
    is no literal `def _tool_create_business_process` to find. This checker
    reported 54 uncovered while 6 were genuinely covered -- a gate scoped to a
    MECHANISM rather than to the condition, which is the recurring flaw in this
    estate.
    """
    creatable = set()
    specs = os.path.join(root, "app", "modules", "ai_chat", "tools",
                         "archimate_specs.py")
    if os.path.exists(specs):
        try:
            with open(specs, encoding="utf-8") as fh:
                spec_source = fh.read()
            # Parsed, not imported: importing reaches app packages and this gate
            # must run with no database and no app context.
            import ast as _ast

            tree = _ast.parse(spec_source)
            for node in _ast.walk(tree):
                # AnnAssign as well as Assign: the table is declared
                # `ELEMENT_SPECS: Dict[str, dict] = {...}`, and matching only
                # Assign silently found nothing while reporting a confident 54.
                if isinstance(node, _ast.AnnAssign):
                    target_name = getattr(node.target, "id", "")
                elif isinstance(node, _ast.Assign) and node.targets:
                    target_name = getattr(node.targets[0], "id", "")
                else:
                    continue
                if (target_name == "ELEMENT_SPECS"
                        and isinstance(node.value, _ast.Dict)):
                    for key in node.value.keys:
                        if isinstance(key, _ast.Constant) and isinstance(key.value, str):
                            creatable.add(key.value)
        except (OSError, SyntaxError, UnicodeDecodeError, IndexError):
            pass
    for rel in TOOL_SOURCES:
        path = os.path.join(root, rel)
        try:
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        for match in re.finditer(r"def _tool_create_([a-z_]+)", source):
            name = match.group(1)
            if "create_" + name in GENERIC:
                continue
            creatable.add(name)
        # Tools declared in a registry rather than defined as a method.
        for match in re.finditer(r'"name"\s*:\s*"create_([a-z_]+)"', source):
            name = match.group(1)
            if "create_" + name in GENERIC:
                continue
            creatable.add(name)
    return creatable


def scan(root: str) -> list:
    types = _element_types(root)
    if not types:
        return []
    creatable = _ai_creatable(root)

    problems = []
    for element, (layer, line_no) in sorted(types.items()):
        normalised = element.replace(" ", "_").replace("&", "and")
        if normalised in creatable or element in creatable:
            continue
        problems.append(
            "%s:%d [ai-layer-coverage] the AI has no dedicated way to create a "
            "%s (%s layer) -- a practitioner models it and the assistant cannot"
            % (LAYER_MAP.replace(os.sep, "/"), line_no, element, layer)
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--root", default=ROOT)
    parser.add_argument("--by-layer", action="store_true",
                        help="summarise the gap per ArchiMate layer")
    args = parser.parse_args()

    problems = scan(os.path.abspath(args.root))
    if args.by_layer:
        counts = {}
        for line in problems:
            layer = line.split("(")[-1].split(" layer")[0]
            counts[layer] = counts.get(layer, 0) + 1
        for layer, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print("  %-16s %d element types with no AI creation path" % (layer, n))
    elif not args.count:
        for line in problems:
            print("  " + line)
        if problems:
            print()
            print(
                "Give the AI a tool that knows this element's semantics and its\n"
                "legal relationships, or append 'ai-layer-ok: <reason>' saying why\n"
                "the type is out of scope and who models it instead."
            )
    print(len(problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())
