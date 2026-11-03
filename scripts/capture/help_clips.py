# -*- coding: utf-8 -*-
"""The "See how" clips: each is the next step done in the demonstration organisation.

``scripts/capture_screenshots.py --help-clips`` seeds the demonstration organisation, boots the app and calls
``record_all``. The clips are keyed by ``HELP_CLIPS`` in ``app/services/public_pages.py`` (the same registry the
empty screens read) and written to the image tree, so a re-recording replaces the file the screen already serves.

Every clip is recorded with ``record_clip`` (demonstration person only) and then the text of every screen it
showed is scanned for a secret and for the name of any other organisation. A clip that fails the scan is deleted
and the run fails; a clip outside 10 to 25 seconds is deleted and the run fails.

Each clip also declares in ``CHECKS`` what it must show (a filled canvas box, two applications and a link, the
controls of a framework). The recorder measures each on the screen the clip ends on, before the recording is
closed, and refuses the clip with ``ClipRefused`` when one falls short, so a clip never goes out showing less
than its line promises. What was measured is written to ``checks.json`` beside the clips, with the hash of the
clip it describes.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

from scripts.capture.clip_kit import ClipRefused, ffprobe_duration_seconds, record_clip, sign_in_state

MIN_SECONDS = 10.0
MAX_SECONDS = 25.0
CHECKS_FILE = "checks.json"

SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
]


def scan_for_leaks(texts, other_organisation_names=()):
    """What a clip must not show: a secret, or the name of an organisation other than the demonstration one."""
    findings = []
    for text in texts:
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append("a secret matching %s" % pattern.pattern[:24])
        lowered = text.lower()
        for name in other_organisation_names:
            if name and name.strip().lower() in lowered:
                findings.append("the name of another organisation: %s" % name)
    return findings


class Seen:
    """The text of every screen a clip showed, kept for the scan. Never written to a file."""

    def __init__(self):
        self.texts = []

    def __call__(self, page):
        self.texts.append(page.evaluate("() => document.body.innerText") or "")


class Check:
    """One thing a clip must show: ``measure(page, seen)`` returns a number that must reach ``at_least``."""

    def __init__(self, name, measure, at_least=1):
        self.name = name
        self.measure = measure
        self.at_least = at_least


def _count(locator):
    return lambda page, seen: locator(page).count()


def _seen(pattern):
    """How many screens the clip showed whose text matches ``pattern``."""
    rx = re.compile(pattern)
    return lambda page, seen: sum(1 for text in seen.texts if rx.search(text))


def _canvas_figure(index):
    """The elements (0) or relationships (1) the composer's status line reports for the open canvas."""
    def measure(page, seen):
        found = re.search(r"(\d+) elements?, (\d+) relationships?", page.locator("body").inner_text())
        return int(found.group(index + 1)) if found else 0
    return measure


def _absent(pattern):
    """1 when ``pattern`` is nowhere on the final screen, 0 when it is (an empty-box notice, say)."""
    rx = re.compile(pattern)
    return lambda page, seen: 0 if rx.search(page.locator("main").inner_text()) else 1


CHECKS = {
    "applications-first": [
        Check("the applications list holds at least two applications",
              _count(lambda page: page.get_by_role("region", name="Applications table").locator("tbody tr")), 2),
        Check("the Connect GitHub page was shown", _seen(r"Read-only token")),
    ],
    "connectors-github": [
        Check("the Connect GitHub page is open", _count(lambda page: page.get_by_role("heading", name="Connect GitHub"))),
        Check("the token field is shown", _count(lambda page: page.get_by_label("Read-only token"))),
    ],
    "model-from-github": [
        Check("the canvas holds at least two elements", _canvas_figure(0), 2),
        Check("the canvas holds at least one relationship", _canvas_figure(1), 1),
        Check("an application read from GitHub was opened", _seen(r"Payments Service")),
    ],
    "chat-citation": [
        Check("an answer names its record", _seen(r"Open relationship record")),
        Check("the record the citation opens lists at least one relationship",
              _count(lambda page: page.locator("main table tbody tr"))),
    ],
    "compliance-controls": [
        Check("the controls of the opened framework are listed (at least two)",
              _count(lambda page: page.locator("table table tbody tr:visible")), 2),
        Check("no framework says its controls are missing", _absent(r"No controls recorded")),
    ],
    "canvases-open": [
        Check("at least five boxes of the open canvas are filled from the model",
              _count(lambda page: page.locator("main a[href^='/architecture/']")), 5),
        Check("no box of the open canvas is empty", _absent(r"No \w+ yet")),
    ],
}


def run_checks(page, seen, checks):
    """Measure each declared check on the screen the clip ends on. Returns the record; raises ClipRefused if any falls short."""
    record = []
    for check in checks:
        measured = int(check.measure(page, seen))
        record.append({"check": check.name, "at_least": check.at_least, "measured": measured,
                       "passed": measured >= check.at_least})
    failed = [row for row in record if not row["passed"]]
    if failed:
        raise ClipRefused("the clip would not show what its line says: " + "; ".join(
            "%s (found %d, needs %d)" % (row["check"], row["measured"], row["at_least"]) for row in failed))
    return record


def _click(page, locator, pause=700):
    """Move the pointer to the control, wait a beat so a viewer sees it, then click."""
    locator.first.wait_for(state="visible")
    box = locator.first.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=18)
    page.wait_for_timeout(pause)
    locator.first.click()


def _open(page, base, path, hold=1800):
    page.goto(base + path, wait_until="domcontentloaded")
    page.wait_for_timeout(hold + 600)


def _applications_first(page, base, see):
    _open(page, base, "/applications/", 900)
    see(page)
    _click(page, page.get_by_text("Add Application", exact=True).locator("visible=true"))
    dialog = page.get_by_role("dialog").first
    dialog.wait_for(state="visible")
    page.wait_for_timeout(600)
    field = dialog.get_by_role("textbox").first
    field.click()
    field.type("Billing Portal", delay=60)
    page.wait_for_timeout(700)
    see(page)
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    _open(page, base, "/admin/connectors", 500)
    _click(page, page.get_by_role("link", name="Connect GitHub"))
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(700)
    see(page)
    _open(page, base, "/applications/", 900)
    see(page)


def _connectors_github(page, base, see):
    _open(page, base, "/admin/connectors", 3000)
    see(page)
    _click(page, page.get_by_role("link", name="Connect GitHub"))
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2500)
    page.mouse.wheel(0, 300)
    page.wait_for_timeout(3500)
    see(page)


def _model_from_github(page, base, see):
    """From the applications GitHub filled in to one of them opened on the canvas with its links."""
    _open(page, base, "/applications/", 1800)
    see(page)
    _click(page, page.get_by_role("link", name="Payments Service"))
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1200)
    see(page)
    _click(page, page.get_by_role("link", name="Open in the Composer"))
    page.get_by_text("Neighbourhood loaded").first.wait_for(timeout=30000)
    page.wait_for_timeout(2500)
    see(page)
    page.mouse.move(900, 450, steps=20)
    page.wait_for_timeout(1500)


def prepare_github_model(browser, base_url, token):
    """Build the model the GitHub clip shows by the real path: connect, let the read finish, approve what it found.

    Done as the demonstration person in a context that is not recorded. The server it runs against answers
    GitHub from the recorded organisation (``scripts/capture/github_fixture.py``), so nothing leaves the machine.
    """
    context = browser.new_context(viewport={"width": 1440, "height": 900}, storage_state=sign_in_state(browser, base_url))
    try:
        page = context.new_page()
        page.set_default_timeout(60000)
        page.goto(base_url + "/admin/connectors/github", wait_until="networkidle")
        form = page.get_by_test_id("github-connect-form")
        form.get_by_label("GitHub organisation").fill("acme")
        form.get_by_label("Read-only token").fill(token)
        form.get_by_role("button", name=re.compile(r"Connect and read|Save and read again")).click()
        page.get_by_text(re.compile(r"proposals are ready")).first.wait_for(timeout=180000)
        page.get_by_role("link", name="Review what was found").click()
        page.wait_for_url("**/review")
        approve = page.get_by_role("button", name=re.compile(r"Approve all"))
        if approve.count():
            approve.click()
            page.get_by_text(re.compile(r"Added \d+ items to your model")).first.wait_for()
    finally:
        context.close()


def _chat_citation(page, base, see):
    _open(page, base, "/ai-chat", 2500)
    see(page)
    # The question is typed, not picked from the suggestions: the first suggestion is whichever application
    # comes first, which may have nothing connected to it.
    question = page.get_by_role("textbox").last
    question.click()
    question.type("What breaks if Calibration Ledger fails?", delay=45)
    page.keyboard.press("Enter")
    page.get_by_role("link", name="Open relationship record").first.wait_for(timeout=45000)
    page.wait_for_timeout(1500)
    see(page)
    citation = page.get_by_role("link", name="Open relationship record").first
    citation.scroll_into_view_if_needed()
    page.wait_for_timeout(1200)
    _click(page, citation)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2500)
    see(page)


def _compliance_controls(page, base, see):
    _open(page, base, "/dashboard/compliance", 3000)
    see(page)
    _click(page, page.get_by_role("button", name=re.compile(r"^Show controls for")))
    page.get_by_text("Loading controls").first.wait_for(state="detached", timeout=20000)
    page.locator("table table").first.scroll_into_view_if_needed()
    page.wait_for_timeout(4500)
    see(page)


def _canvases_open(page, base, see):
    _open(page, base, "/business-model/", 3000)
    see(page)
    # the canvas the model fills (named for the organisation alone), not a hand-typed one beside it
    _click(page, page.get_by_role("link", name=re.compile(r"^Lantern Quay Systems(?!:)")).first)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(6500)
    see(page)


STEPS = {
    "applications-first": (_applications_first, [
        (0, "Your applications, in one list"), (4, "Add one by hand"), (10, "Or let GitHub fill it for you")]),
    "connectors-github": (_connectors_github, [
        (0, "Connectors, for this organisation"), (5, "Connect GitHub to read your repositories")]),
    "model-from-github": (_model_from_github, [
        (0, "The model, built from what GitHub holds"), (5, "Open an application to see it and its links")]),
    "chat-citation": (_chat_citation, [
        (0, "Ask what breaks if a system fails"), (7, "Every fact names its record"), (11, "Open a citation to see it")]),
    "compliance-controls": (_compliance_controls, [
        (0, "Regulatory frameworks"), (5, "Choose one to see its controls")]),
    "canvases-open": (_canvases_open, [
        (0, "Your canvases"), (5, "Open one: each box is an element of your model")]),
}


def _sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def record_all(browser, base_url, out_dir, other_organisation_names=(), only=None):
    """Record every clip (or ``only`` those keys). Returns (written paths, failure lines).

    A clip whose declared checks fall short, or that leaks, or that is the wrong length is refused: its files are
    removed so the screen stops offering it. ``checks.json`` keeps what each written clip's checks measured.
    """
    from app.services.public_pages import HELP_CLIPS

    out = pathlib.Path(out_dir)
    record_path = out / CHECKS_FILE
    kept = json.loads(record_path.read_text(encoding="utf-8")) if record_path.is_file() else {}
    written, failed = [], []
    for key in HELP_CLIPS:
        if only and key not in only:
            continue
        step, captions = STEPS[key]
        seen = Seen()
        measured = {}

        def steps(page, step=step, seen=seen, key=key, measured=measured):
            step(page, base_url, seen)
            seen(page)
            measured["rows"] = run_checks(page, seen, CHECKS[key])

        try:
            clip = record_clip(browser, base_url, key, steps, captions=captions, out_dir=str(out), clock=False)
            clip_path = pathlib.Path(clip)
            leaks = scan_for_leaks(seen.texts + [text for _, text in captions], other_organisation_names)
            seconds = ffprobe_duration_seconds(clip_path)
            problem = None
            if leaks:
                problem = "; ".join(sorted(set(leaks)))
            elif not (MIN_SECONDS <= seconds <= MAX_SECONDS):
                problem = "it runs %.1f seconds, outside %d to %d" % (seconds, MIN_SECONDS, MAX_SECONDS)
            if problem:
                raise ClipRefused(problem)
            kept[key] = {"clip_sha256": _sha256(clip_path), "seconds": round(seconds, 1), "checks": measured["rows"]}
            written.append(str(clip_path))
            print("  clip   %-22s %7d bytes  %.1fs  %d checks passed" % (key, clip_path.stat().st_size, seconds,
                                                                         len(measured["rows"])))
        except Exception as exc:  # noqa: BLE001 - one clip failing must not hide the others
            (out / (key + ".webm")).unlink(missing_ok=True)
            (out / (key + ".poster.webp")).unlink(missing_ok=True)
            kept.pop(key, None)
            failed.append("%s: refused, %s: %s" % (key, type(exc).__name__, str(exc)[:240]))
    record_path.write_text(json.dumps(kept, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return written, failed
