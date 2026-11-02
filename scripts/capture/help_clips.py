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
PHONE_SUFFIX = ".phone"

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
        self.headings = []
        self.cited = None

    def __call__(self, page):
        self.texts.append(page.evaluate("() => document.body.innerText") or "")
        self.headings.append(page.evaluate(
            "() => Array.from(document.querySelectorAll('main h1, main h2')).map(h => h.innerText.trim())") or [])


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


def _heading_seen(pattern):
    """How many screens the clip showed with a page heading (not just any text) matching ``pattern``."""
    rx = re.compile(pattern)
    return lambda page, seen: sum(1 for heads in seen.headings if any(rx.fullmatch(h) for h in heads))


def _cited_row_in_view(page, seen):
    """1 when the row the citation points at (``#relationship-<id>``) is on the final screen and inside the window.

    The citation's address is kept when the clip follows it; the row is looked up by that exact id, so a list that
    holds other relationships but not the cited one measures 0, and so does one whose cited row is not marked selected.
    """
    found = re.search(r"#(relationship-[\w-]+)$", seen.cited or "")
    if not found:
        return 0
    row = page.locator("tr[id='%s']" % found.group(1))
    if row.count() != 1 or row.get_attribute("data-state") != "selected" or "Cited" not in row.inner_text():
        return 0
    box = row.bounding_box()
    window = page.viewport_size
    caption = page.evaluate(
        "() => { const c = document.getElementById('__cap'); return c ? c.getBoundingClientRect().top : window.innerHeight; }")
    return int(box is not None and box["y"] >= 0 and box["y"] + box["height"] <= min(window["height"], caption))


# What the composer's own drawing is read from: the label of every box in the neighbourhood.
_FOCUS_VIEW_JS = r"""() => {
  const root = document.getElementById('composer-canvas');
  const view = root.getBoundingClientRect();
  const inView = (e) => { const r = e.getBoundingClientRect();
    return r.width > 0 && r.right > view.left && r.left < view.right && r.bottom > view.top && r.top < view.bottom; };
  const labels = Array.from(root.querySelectorAll('.joint-element text')).filter(
    t => (t.textContent || '').trim() && inView(t));
  const faded = Array.from(root.querySelectorAll('.joint-element')).filter(
    e => inView(e) && (parseFloat(getComputedStyle(e).opacity) < 0.9 || e.classList.contains('dimmed'))).length;
  const status = Array.from(document.querySelectorAll('body *')).find(
    e => e.children.length === 0 && /\d+ elements?, \d+ relationships?/.test(e.textContent || ''));
  const cap = document.getElementById('__cap');
  const shown = cap && getComputedStyle(cap).display !== 'none';
  const under = !!(status && shown && (() => {
    const a = status.getBoundingClientRect(), b = cap.getBoundingClientRect();
    return a.bottom > b.top && a.top < b.bottom && a.right > b.left && a.left < b.right;
  })());
  return {smallest: labels.length ? Math.min(...labels.map(t => t.getBoundingClientRect().height)) : 0,
          count: labels.length, faded: faded, underCaption: under,
          hint: /Drag from a blue port/.test(document.body.innerText)};
}"""

# A spot on the canvas with no box, link or card on it: where a person clicks to let go of the opened application.
_BLANK_CANVAS_POINT_JS = r"""() => {
  const view = document.getElementById('composer-canvas').getBoundingClientRect();
  for (let fy = 0.3; fy < 0.95; fy += 0.05) for (let fx = 0.1; fx < 0.95; fx += 0.1) {
    const x = view.left + view.width * fx, y = view.top + view.height * fy;
    const hit = document.elementFromPoint(x, y);
    if (hit && hit.closest('#composer-canvas') && !hit.closest('.joint-cell, .joint-link, .joint-element')
        && (hit.tagName === 'svg' || hit.classList.contains('joint-paper') || hit.id === 'composer-canvas'
            || hit.closest('.joint-paper-background, .joint-paper-grid'))) return {x: x, y: y};
  }
  return {x: view.left + 8, y: view.bottom - 8};
}"""

# A label this tall on the 1440 frame is still 9 px high where the dialog shows the clip about 1000 px wide.
READABLE_LABEL_PX = 13
MAX_ZOOM_TURNS = 40


def _focus_view(field):
    return lambda page, seen: page.evaluate(_FOCUS_VIEW_JS)[field]


_COMPOSER_STATE_JS = ("() => { try { const a = Alpine.$data(document.querySelector('[x-data=\"composerApp()\"]')); "
                      "return %s; } catch (e) { return false; } }")


def _canvas_figure(index):
    """The elements (0) or relationships (1) the composer holds for the open canvas (its status line says the same, wide screens only)."""
    field = ("elementCount", "relCount")[index]
    return lambda page, seen: int(page.evaluate(_COMPOSER_STATE_JS % ("a." + field)) or 0)


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
        Check("the opened application's own page was shown, headed with its name", _heading_seen(r"Payments Service")),
        Check("every box label on the canvas is large enough to read",
              lambda page, seen: int(_focus_view("smallest")(page, seen) >= READABLE_LABEL_PX)),
        Check("no box of the neighbourhood is still faded", lambda page, seen: int(_focus_view("faded")(page, seen) == 0)),
        Check("the drag hint is gone", lambda page, seen: int(not _focus_view("hint")(page, seen))),
        Check("the status line is not under the caption bar",
              lambda page, seen: int(not _focus_view("underCaption")(page, seen))),
    ],
    "chat-citation": [
        Check("an answer names its record", _seen(r"Open relationship record")),
        Check("the cited relationship's own row is on the screen and in view", _cited_row_in_view),
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
    _open(page, base, "/applications/", 0)
    see(page)
    _click(page, page.get_by_role("link", name="Payments Service"), pause=400)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    see(page)
    _click(page, page.get_by_role("link", name="Open in the Composer"), pause=400)
    # the status line is hidden below md, so the load is read from the composer's own state
    page.wait_for_function(_COMPOSER_STATE_JS % "a.statusText === 'Neighbourhood loaded'", timeout=30000)
    see(page)
    _focus_on_the_application(page)


def _focus_on_the_application(page):
    """End on the opened application, large enough to read, by what a person does with the mouse.

    Clicking empty canvas puts every box back to full strength; the scroll wheel over the opened application zooms
    towards it a turn at a time until the smallest label can be read; the drag hint is waited out if it showed; and
    the recording's own caption bar is cleared so the status line beneath it can be read.
    """
    page.wait_for_timeout(1200)  # the canvas keeps refitting itself for three seconds after the load
    blank = page.evaluate(_BLANK_CANVAS_POINT_JS)
    page.mouse.move(blank["x"], blank["y"], steps=10)
    page.mouse.click(blank["x"], blank["y"])
    page.wait_for_timeout(600)
    box = page.locator("#composer-canvas .joint-element text", has_text="Payments Service").first.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=12)
    for _ in range(MAX_ZOOM_TURNS):
        if page.evaluate(_FOCUS_VIEW_JS)["smallest"] >= READABLE_LABEL_PX + 1:
            break
        page.mouse.wheel(0, -100)
        page.wait_for_timeout(25)
    page.wait_for_timeout(500)
    hint = page.get_by_text("Drag from a blue port")
    if hint.count():
        hint.first.wait_for(state="detached", timeout=20000)
    page.evaluate("() => window.__caption('', '')")
    page.wait_for_timeout(1600)


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
    _open(page, base, "/ai-chat", 1500)
    see(page)
    # The question is typed, not picked from the suggestions: the first suggestion is whichever application
    # comes first, which may have nothing connected to it.
    question = page.get_by_role("textbox").last
    question.click()
    question.type("What breaks if Calibration Ledger fails?", delay=45)
    page.keyboard.press("Enter")
    page.get_by_role("link", name="Open relationship record").first.wait_for(timeout=45000)
    page.wait_for_timeout(900)
    see(page)
    citation = page.get_by_role("link", name="Open relationship record").first
    citation.scroll_into_view_if_needed()
    page.wait_for_timeout(700)
    see.cited = citation.get_attribute("href")
    _click(page, citation)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)
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


PHONE_SIZE = (390, 844)


def _record_one(browser, base_url, out, key, step, captions, size, name, other_organisation_names):
    """Record one clip at ``size`` under ``name``, measure its declared checks, scan and time it. Returns the record entry."""
    seen = Seen()
    measured = {}

    def steps(page):
        step(page, base_url, seen)
        seen(page)
        measured["rows"] = run_checks(page, seen, CHECKS[key])

    clip = record_clip(browser, base_url, name, steps, size=size, captions=captions, out_dir=str(out), clock=False)
    clip_path = pathlib.Path(clip)
    leaks = scan_for_leaks(seen.texts + [text for _, text in captions], other_organisation_names)
    seconds = ffprobe_duration_seconds(clip_path)
    problem = None
    if leaks:
        problem = "; ".join(sorted(set(leaks)))
    elif not (MIN_SECONDS <= seconds <= MAX_SECONDS):
        problem = "it runs %.1f seconds, outside %d to %d" % (seconds, MIN_SECONDS, MAX_SECONDS)
    if problem:
        raise ClipRefused(("the phone recording: " if size == PHONE_SIZE else "") + problem)
    return clip_path, {"clip_sha256": _sha256(clip_path), "seconds": round(seconds, 1), "checks": measured["rows"]}


def record_all(browser, base_url, out_dir, other_organisation_names=(), only=None):
    """Record every clip (or ``only`` those keys) at 1440 and again at 390 wide. Returns (written paths, failure lines).

    The same steps and the same declared checks run at both sizes; the dialog plays the 390 version on a phone. A
    clip whose declared checks fall short at either size, or that leaks, or that is the wrong length is refused:
    its files are removed so the screen stops offering it. ``checks.json`` keeps what each written clip's checks
    measured, the phone recording's under ``phone``.
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
        try:
            clip_path, entry = _record_one(browser, base_url, out, key, step, captions, (1440, 900), key,
                                           other_organisation_names)
            phone_path, entry["phone"] = _record_one(browser, base_url, out, key, step, captions, PHONE_SIZE,
                                                     key + PHONE_SUFFIX, other_organisation_names)
            kept[key] = entry
            written += [str(clip_path), str(phone_path)]
            print("  clip   %-22s %7d bytes  %.1fs  %d checks passed; phone %7d bytes  %.1fs  %d checks passed" % (
                key, clip_path.stat().st_size, entry["seconds"], len(entry["checks"]),
                phone_path.stat().st_size, entry["phone"]["seconds"], len(entry["phone"]["checks"])))
        except Exception as exc:  # noqa: BLE001 - one clip failing must not hide the others
            for stem in (key, key + PHONE_SUFFIX):
                (out / (stem + ".webm")).unlink(missing_ok=True)
                (out / (stem + ".poster.webp")).unlink(missing_ok=True)
            kept.pop(key, None)
            failed.append("%s: refused, %s: %s" % (key, type(exc).__name__, str(exc)[:240]))
    record_path.write_text(json.dumps(kept, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return written, failed
