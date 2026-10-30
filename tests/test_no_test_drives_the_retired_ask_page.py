"""Every question is asked in the AI chat, so no test drives the retired Ask page.

The old address only redirects into the chat; the two files that test that redirect are
the only ones allowed to name it.
"""
import pathlib
import re

TESTS = pathlib.Path(__file__).resolve().parent
REDIRECT_TESTS = {"test_ai_chat_estate_answer.py", "test_ask_in_the_chat_journey.py"}
# The page's own address; the JSON interface under /api/v1 is a different thing.
RETIRED_ADDRESS = re.compile(r"(?<!v1)/intelligence/ask(?![\w./-])")


def offenders(root: pathlib.Path):
    found = []
    for path in sorted(root.rglob("test_*.py")) + sorted(root.rglob("*.py")):
        if path.name in REDIRECT_TESTS or path == pathlib.Path(__file__).resolve():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if RETIRED_ADDRESS.search(text):
            found.append(str(path.relative_to(root)))
    return sorted(set(found))


def test_no_test_drives_the_retired_ask_page():
    assert offenders(TESTS) == []
