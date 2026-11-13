"""One rule and one masker decide every figure in pounds.

No page, route, service or template asks ``can_see_costs`` to decide whether a cost shows: the rule is
``may_see_spend_pages``, asked through ``app/services/cost_visibility.py``, where the one masker lives.
"""
import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parent.parent / "app"
RULE_FILES = {"decorators/requires_role.py", "utils/role_access.py"}
MASKER_FILE = "services/cost_visibility.py"
MASKER_NAMES = {"hide_costs", "limit_costs_to_viewer", "withhold_costs"}


def _trees():
    for path in sorted(APP.rglob("*.py")):
        yield path.relative_to(APP).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _called_name(node):
    func = node.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)


def test_only_the_rule_files_call_can_see_costs():
    found = ["%s:%d" % (rel, n.lineno) for rel, tree in _trees() if rel not in RULE_FILES
             for n in ast.walk(tree) if isinstance(n, ast.Call) and _called_name(n) == "can_see_costs"]
    assert not found, "ask costs_visible (cost_visibility), not can_see_costs: %s" % found


def test_the_masker_is_defined_once_in_cost_visibility():
    found = ["%s:%s" % (rel, n.name) for rel, tree in _trees() if rel != MASKER_FILE
             for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in MASKER_NAMES]
    assert not found, "a second masker beside cost_visibility.withhold_costs: %s" % found
    defined = {n.name for rel, tree in _trees() if rel == MASKER_FILE
               for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "withhold_costs" in defined and "costs_visible" in defined
