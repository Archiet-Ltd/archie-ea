"""Every question is asked in the AI chat, so no test drives the retired Ask page.

The old address only redirects into the chat; the files named in REDIRECT_TESTS are
the only ones allowed to name it.

The guard reads each test as a syntax tree rather than as text, so it sees the address
however it is written: one literal, joined literals (implicit or with `+`), an f-string, a
`%` or `.format` template, or a module-level string constant. A test may say the address is
absent, and the guard accepts exactly these forms of that:

* `assert not ...` and `assert ... not in ...`, which cover `not any(X in h ...)` too;
* `assert [...] == []`, the list of offending links being empty;
* `expect(page).not_to_have_url(...)` and any other `not_to_*` check.

A test that passes the address to a call that opens it (`goto`, `get`, `post`, `request`,
`follow_redirects` and the like) is rejected, as is any other use of it.
"""
import ast
import pathlib
import re
import warnings

TESTS = pathlib.Path(__file__).resolve().parent
# The two redirect tests, and the sibling guard that plants the address in a scratch page to prove
# it sees a link (it opens nothing).
REDIRECT_TESTS = {
    "test_ai_chat_estate_answer.py",
    "test_ask_in_the_chat_journey.py",
    "test_ask_lives_only_in_the_chat.py",
}
# The page's own address; the JSON interface under /api/v1 is a different thing.
RETIRED_ADDRESS = re.compile(r"(?<!v1)/intelligence/ask(?![\w./-])")
OPENING_CALLS = {
    "goto", "get", "post", "put", "patch", "delete", "head", "options", "open", "request",
    "visit", "navigate", "fetch", "wait_for_url", "to_have_url", "urlopen", "follow",
}
UNKNOWN = "\0"


def _resolve(node, constants):
    """The string an expression builds, with `\\0` where a part is not known; None if not a string."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                parts.append(_resolve(value.value, constants) or UNKNOWN)
            else:
                parts.append(_resolve(value, constants) or UNKNOWN)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        left = _resolve(node.left, constants)
        if isinstance(node.op, ast.Mod):
            return left
        right = _resolve(node.right, constants)
        if left is None and right is None:
            return None
        return (left or UNKNOWN) + (right or UNKNOWN)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"format", "format_map", "join", "strip", "lstrip", "rstrip"}
    ):
        return _resolve(node.func.value, constants) if node.func.attr != "join" else None
    return None


def _constants(tree):
    found = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            value = _resolve(statement.value, found)
            if value is not None:
                found[statement.targets[0].id] = value
    return found


def _says_absent(test):
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return True
    if isinstance(test, ast.Compare):
        if all(isinstance(op, ast.NotIn) for op in test.ops):
            return True
        if (
            all(isinstance(op, ast.Eq) for op in test.ops)
            and any(isinstance(c, ast.List) and not c.elts for c in test.comparators)
        ):
            return True
    return False


def _opens_page(call):
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name in OPENING_CALLS or any(k.arg == "follow_redirects" for k in call.keywords)


def _is_absence_check(call):
    func = call.func
    return isinstance(func, ast.Attribute) and func.attr.startswith("not_to_")


def _names_address(source):
    """Each use of the retired address in one file's source: 'opens' or 'names'."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tree = ast.parse(source)
    constants = _constants(tree)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    module_constants = {s for s in tree.body if isinstance(s, ast.Assign)}

    def names_it(node):
        value = _resolve(node, constants)
        return value is not None and RETIRED_ADDRESS.search(value) is not None

    uses = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.expr) or not names_it(node):
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.expr) and names_it(parent):
            continue
        verdict = "names"
        cursor = node
        while cursor in parents:
            cursor = parents[cursor]
            if cursor in module_constants:
                verdict = None
                break
            if isinstance(cursor, ast.Call) and cursor is not node:
                if _is_absence_check(cursor):
                    verdict = None
                    break
                if _opens_page(cursor):
                    verdict = "opens"
                    break
            if isinstance(cursor, ast.Assert):
                verdict = None if _says_absent(cursor.test) else verdict
                break
        if verdict:
            uses.append(verdict)
    return uses


def offenders(root: pathlib.Path):
    found = []
    for path in sorted(root.rglob("*.py")):
        if path.name in REDIRECT_TESTS or path == pathlib.Path(__file__).resolve():
            continue
        try:
            uses = _names_address(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            found.append(str(path.relative_to(root)) + " (unreadable)")
            continue
        if uses:
            found.append(str(path.relative_to(root)))
    return sorted(set(found))


def test_no_test_drives_the_retired_ask_page():
    assert offenders(TESTS) == []


def _plant(tmp_path, source):
    (tmp_path / "test_planted.py").write_text(source, encoding="utf-8")
    return offenders(tmp_path)


def test_a_navigation_through_a_joined_string_is_caught(tmp_path):
    assert _plant(tmp_path, 'ASK = "/intelligence/" + "ask"\ndef test_x(page):\n    page.goto("http://h" + ASK + "?q=1")\n')
    assert _plant(tmp_path, 'def test_x(client):\n    client.get("/intelligence/" "ask")\n')
    assert _plant(tmp_path, 'def test_x(client, n):\n    client.get(f"/intelligence/ask?q={n}")\n')
    assert _plant(tmp_path, 'def test_x(client):\n    client.get("/intelligence/ask", follow_redirects=True)\n')


def test_a_navigation_through_a_modulo_template_is_caught(tmp_path):
    assert _plant(tmp_path, 'ASK = "/intelligence/ask" % q\ndef test_x(page):\n    page.goto("http://h" + ASK + "?q=1")\n')
    assert _plant(tmp_path, 'ASK = "/intelligence/ask" % q\ndef test_x(client):\n    client.get("/intelligence/ask" % q)\n')


def test_a_navigation_through_a_format_template_is_caught(tmp_path):
    assert _plant(tmp_path, 'ASK = "/intelligence/ask".format(q)\ndef test_x(page):\n    page.goto("http://h" + ASK + "?q=1")\n')
    assert _plant(tmp_path, 'ASK = "/intelligence/ask".format(q)\ndef test_x(client):\n    client.get("/intelligence/ask".format(q))\n')


def test_a_navigation_through_a_format_map_template_is_caught(tmp_path):
    assert _plant(tmp_path, 'ASK = "/intelligence/ask".format_map(q)\ndef test_x(page):\n    page.goto("http://h" + ASK + "?q=1")\n')
    assert _plant(tmp_path, 'ASK = "/intelligence/ask".format_map(q)\ndef test_x(client):\n    client.get("/intelligence/ask".format_map(q))\n')


def test_an_absence_assertion_is_accepted(tmp_path):
    assert _plant(tmp_path, 'ASK = "/intelligence/" + "ask"\ndef test_x(html):\n    assert ASK not in html\n') == []
    assert _plant(tmp_path, 'ASK = "/intelligence/ask"\ndef test_x(hrefs):\n    assert not any(ASK in h for h in hrefs)\n') == []
    assert _plant(tmp_path, 'ASK = "/intelligence/ask"\ndef test_x(hrefs):\n    assert [h for h in hrefs if ASK in h] == []\n') == []
    assert _plant(tmp_path, 'def test_x(page, expect):\n    expect(page).not_to_have_url("/intelligence/" + "ask")\n') == []
    assert _plant(tmp_path, 'def test_x(client):\n    client.get("/api/v1/intelligence/ask")\n') == []
