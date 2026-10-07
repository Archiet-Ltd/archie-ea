"""Fail CI on NEW bandit findings only.

Why not `bandit -b baseline.json`? Its baseline matching keys on file path and
line number, so it breaks two ways that both happened here:

  * Paths differ by platform. A baseline generated on Windows records
    "app\\models\\x.py"; bandit on the Linux CI runner reports "./app/models/x.py",
    so nothing matches and every pre-existing finding is reported as new.
  * Line numbers drift. Any edit above a finding shifts it, and bandit then
    emits "-- Candidate Issues --" and exits non-zero even though nothing new
    was introduced.

This gate fingerprints a finding by (test_id, normalised path, stripped source
line) and ignores line numbers entirely, so it is stable across platforms and
survives unrelated edits to the same file.

A HIGH-severity finding is different: the baseline cannot accept one. The
--high mode fails while any HIGH finding is open, baselined or not, so a release
never ships one. A finding counts as dispositioned only when its own lines carry
"# nosec <test id> -- <reason>"; a bare "# nosec" hides a finding from bandit
but not from this check, because --high runs bandit with --ignore-nosec.

Usage:
    python scripts/ci/bandit_gate.py --update   # regenerate the accepted set
    python scripts/ci/bandit_gate.py            # fail if anything new appeared
    python scripts/ci/bandit_gate.py --high     # fail while a HIGH finding is open

Proven-against: a seeded subprocess.call(cmd, shell=True) (B602, HIGH) -- --high
red at 1, then red again under a bare "# nosec", green under
"# nosec B602 -- <reason>" and green once removed (tests/test_high_findings_gate.py);
and main's own tree, red at 15 before the generators and manage.py were fixed.
"""

import argparse
import json
import re
import os
import subprocess
import sys

BASELINE = ".bandit-baseline.json"
TARGETS = ["app", "config.py", "manage.py"]
EXCLUDE = "./app/static/vendor,./app/modules/solutions_product/templates,./tests"


def normalise(path):
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def strip_line_numbers(code):
    """Drop bandit's leading line numbers from each line of a code snippet.

    bandit's `code` field embeds the line number at the start of every line:

        "379 search_query = text(\n380 f\\"\\"\\"\n"

    Fingerprinting that raw means any edit ABOVE a finding shifts its numbers,
    changes the hash, and reports an untouched finding as new - the very
    brittleness this gate replaced `bandit -b baseline.json` to avoid. Adding a
    single import once produced a bogus "1 NEW finding" for code nobody had
    touched.
    """
    out = []
    for line in code.splitlines():
        out.append(re.sub(r"^\s*\d+\s?", "", line))
    return " ".join(" ".join(out).split())


def fingerprint(result):
    """Identity of a finding, independent of platform and line number."""
    code = strip_line_numbers(result.get("code", ""))
    return "%s|%s|%s" % (result["test_id"], normalise(result["filename"]), code)


def run_bandit(targets=None, ignore_nosec=False):
    cmd = [
        sys.executable, "-m", "bandit", "-r", *(targets or TARGETS),
        "-x", EXCLUDE, "-ll", "-f", "json", "-q",
    ]
    if ignore_nosec:
        cmd.append("--ignore-nosec")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if not proc.stdout.strip():
        sys.stderr.write(proc.stderr)
        raise SystemExit("bandit produced no output")
    return json.loads(proc.stdout).get("results", [])


def _dispositioned(result):
    """True when the finding's own lines say why it is not a vulnerability."""
    marker = re.compile(r"#\s*nosec\b[^\n]*\b%s\b[^\n]*--\s*\S" % re.escape(result["test_id"]))
    try:
        with open(result["filename"], encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return False
    span = result.get("line_range") or [result["line_number"]]
    first, last = min(span), max(span)
    return any(marker.search(line) for line in lines[first - 1:last])


def open_high(results):
    return [r for r in results if r["issue_severity"] == "HIGH" and not _dispositioned(r)]


def report_high(targets=None):
    found = open_high(run_bandit(targets, ignore_nosec=True))
    print("high-severity findings open: %d" % len(found))
    for r in sorted(found, key=lambda x: (normalise(x["filename"]), x["line_number"])):
        print("  [%s] %s:%s  %s" % (r["test_id"], normalise(r["filename"]),
                                   r["line_number"], r["issue_text"][:100]))
    if found:
        print("\nFix each one. A finding that is genuinely not a vulnerability is")
        print("dispositioned on its own line: # nosec <test id> -- <why>")
    return 1 if found else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="write the accepted set")
    ap.add_argument("--high", action="store_true",
                    help="fail while any HIGH-severity finding is open, baselined or not")
    ap.add_argument("--targets", nargs="+", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.high:
        return report_high(args.targets)

    results = run_bandit()
    current = {fingerprint(r): r for r in results}

    if args.update:
        with open(BASELINE, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(sorted(current), fh, indent=1)
        print("baseline updated: %d accepted finding(s)" % len(current))
        return 0

    if not os.path.exists(BASELINE):
        raise SystemExit("%s missing - run with --update" % BASELINE)
    with open(BASELINE, encoding="utf-8") as fh:
        accepted = set(json.load(fh))

    new = [current[k] for k in current if k not in accepted]
    if not new:
        fixed = len(accepted) - len(set(current) & accepted)
        print("bandit: no new findings (%d accepted, %d of them now fixed)"
              % (len(accepted), fixed))
        return 0

    print("bandit: %d NEW finding(s):\n" % len(new))
    for r in sorted(new, key=lambda x: (x["issue_severity"], x["filename"])):
        print("  [%s/%s] %s" % (r["issue_severity"], r["issue_confidence"], r["test_id"]))
        print("    %s:%s" % (normalise(r["filename"]), r["line_number"]))
        print("    %s" % r["issue_text"][:100])
        snippet = " ".join(r.get("code", "").split())[:100]
        if snippet:
            print("    > %s" % snippet)
        print()
    print("If a finding is genuinely acceptable, justify it in the commit message")
    print("and run: python scripts/ci/bandit_gate.py --update")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
