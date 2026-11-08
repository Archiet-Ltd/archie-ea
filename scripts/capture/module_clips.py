# -*- coding: utf-8 -*-
"""The whole-journey recordings of the module pages: each is a module's job done in the demonstration organisation.

``scripts/capture_screenshots.py --modules`` seeds the demonstration organisation, boots the app and calls
``record_all``. Each recording is registered in ``MODULE_RECORDINGS`` in ``app/services/public_pages.py`` (the
same registry the page reads for its text) and written beside the module screenshots, so a re-recording replaces
the file the page already serves. A module whose job cannot be shown end to end is named in
``MODULE_RECORDING_DEFECTS`` in the same file, with the screen and what is wrong with it.

A recording opens on the answer (its first caption states the result), then shows how, with captions that say
what the person now knows, and ends on the closing card. It is recorded with ``record_clip`` (demonstration
person only). Every screen it showed is scanned for a secret, for the name of another organisation and for text
that would put an empty or broken state in frame; a clip that fails any scan, or that runs outside 30 to 90
seconds or over 2 MB, is deleted and the run fails. What each clip measured is written to ``recordings.json``.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import time

from scripts.capture.clip_kit import ClipRefused, ffprobe_duration_seconds, record_clip
from app.services.public_pages import MODULE_RECORDINGS
from scripts.capture.help_clips import Check, Seen, _click, _open, run_checks, scan_for_leaks

MIN_SECONDS = 31.0
MAX_SECONDS = 90.0
MAX_BYTES = 2 * 1024 * 1024
MANIFEST = "recordings.json"
SIGN_UP = "Start free at entelim.org"
CAPTION_HOLD = 4.4
CLOSING_HOLD = 5.0
MIN_CAPTIONS = 2
MAX_CAPTIONS = 4
RESERVE_BOTTOM = 120

# Page furniture a recording leaves out: the banner asking the person to set up two-factor sign-in.
HIDE = ["[data-testid='two-factor-banner']"]

# Text that puts an empty, unavailable, unset or placeholder state in frame, or a bare zero standing in for a
# measurement. A clip showing any of it is refused; there is no per-clip exception.
FORBIDDEN_IN_FRAME = re.compile(
    r"Not recorded|Not shown for your role|unavailable|No [\w ]+ yet|Something went wrong|don't have access|"
    r"Nothing here|No data|\bN/A\b|\bTBC\b|\bundefined\b|\bNaN\b|"
    r"\bNot set\b|\bNot assessed\b|\bNot reviewed\b|\bNo [\w ]+ recorded\b|(?i:\bno (?:owner|capability|vendor|licence|DR)\b)|"
    r"Appears here|Missing to reach|\bUnassigned\b|"
    r"(?<![\w.,\u00a3$%/-])0(?![\w.,%/-])|^\u2014$|\t\u2014\t|\t\u2014$|^\u2014\t", re.M)


_IN_VIEW_JS = """() => {
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.nodeValue.trim()) continue;
    const el = node.parentElement;
    if (!el || el.closest('#__cap, #__tc, script, style')) continue;
    const css = getComputedStyle(el);
    if (css.visibility === 'hidden' || css.display === 'none') continue;
    range.selectNodeContents(node);
    const r = range.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth)
      out.push(node.nodeValue.trim());
  }
  return out.join('\\n');
}"""


class FrameSeen(Seen):
    """What each screen said (whole page, for the leak scan) and what was in the frame (for the empty-state scan)."""

    def __init__(self):
        super().__init__()
        self.frames = []

    def __call__(self, page):
        super().__call__(page)
        self.frames.append(page.evaluate(_IN_VIEW_JS) or "")


def frame_problems(frames):
    """One line for each screen (text in view) that shows an empty, unset or placeholder state or a bare zero."""
    out = []
    for text in frames:
        found = FORBIDDEN_IN_FRAME.search(text)
        if found:
            out.append("on screen: %r in %r" % (found.group(0).strip(), text[max(found.start() - 50, 0):found.end() + 30]))
    return out


def said(see, page, text, hold=CAPTION_HOLD, needs=()):
    """Show ``text`` as the caption, keep it on screen for ``hold`` seconds, and keep what the screen said.

    ``needs`` are words the caption claims; the screen it is said over must show every one of them, else the
    recording is refused (a caption states what is on screen at that moment, never a fixed claim).
    """
    see(page)
    shown = see.frames[-1] if hasattr(see, "frames") else ""
    missing = [word for word in needs if word.lower() not in shown.lower()]
    if missing:
        raise ClipRefused("the caption %r is said over a screen that does not show %s" % (text, missing))
    if hasattr(page, "clip_begin"):
        page.clip_begin()
    page.evaluate("t => window.__caption('', t)", text)
    page.wait_for_timeout(int(hold * 1000))


def fig(page, pattern, scope="main"):
    """The figure(s) a caption quotes, read from the screen it is said on, so a caption never states a literal.

    Returns the match groups; raises ``ClipRefused`` when the screen does not show the figure.
    """
    text = page.locator(scope).first.inner_text()
    found = re.search(pattern, text, re.I | re.S)
    if not found:
        raise ClipRefused("the screen does not show the figure the caption quotes: %s" % pattern)
    return found.groups()


def wander(page, points, pause=450):
    for x, y in points:
        page.mouse.move(x, y, steps=22)
        page.wait_for_timeout(pause)


def closing_card(page, promise):
    """The one closing card: the module's promise and the sign-up call to action."""
    page.evaluate("() => { const c = document.getElementById('__cap'); if (c) c.remove(); }")
    page.evaluate("([t, s]) => window.__title(t, s)", [promise, SIGN_UP])
    page.wait_for_timeout(int(CLOSING_HOLD * 1000))


def _seen_count(pattern):
    """How many screens the clip showed whose text matches ``pattern``."""
    rx = re.compile(pattern)
    return lambda page, seen: sum(1 for text in seen.texts if rx.search(text))


# ── storyboards ─────────────────────────────────────────────────────────────
# Each story takes (page, base, say) where say(text) shows a result caption, and ends on the screen that carries
# its answer after calling page.clip_mark() (the poster frame). CHECKS says what the last screen must hold.

def _applications(page, base, say):
    _open(page, base, "/applications/", 600)
    say(MODULE_RECORDINGS["applications"]["title"])
    wander(page, [(700, 640), (900, 700), (760, 780)])
    say("%s applications, each with its owner, lifecycle stage and capability" % fig(page, r"(\d+) shown of")[0],
        needs=("Owner", "Lifecycle"))
    box = page.get_by_placeholder("Name, description, stack...")
    _click(page, box)
    page.keyboard.type("Calibration", delay=140)
    page.wait_for_function("() => !!document.body && /1 shown of/.test(document.body.innerText)", timeout=30000)
    page.wait_for_timeout(1200)
    page.clip_mark()
    name, owners = fig(page, r"(Calibration Ledger);\s*Owner:\s*([^\n;]+)")
    say("%s is owned by %s" % (name, owners.strip()), hold=6.5, needs=("Calibration Ledger",))
    wander(page, [(700, 640), (880, 600), (760, 700), (940, 640)])
    page.wait_for_timeout(1500)


def _business_model_canvas(page, base, say):
    _open(page, base, "/business-model/", 600)
    say(MODULE_RECORDINGS["business-model-canvas"]["title"])
    wander(page, [(700, 520), (900, 440)])
    _click(page, page.get_by_role("link", name=re.compile(r"^Lantern Quay Systems(?!:)")).first)
    page.get_by_text("Component suppliers").first.wait_for(timeout=30000)
    page.wait_for_timeout(1500)
    page.clip_mark()
    say("Every box is made of elements your team already modelled, so nothing is typed twice")
    wander(page, [(420, 760), (640, 720), (900, 700), (1150, 740)])
    say("Partners, activities, resources, offer and customers, each one a real element of the model")
    page.mouse.wheel(0, 420)
    page.wait_for_timeout(1000)
    wander(page, [(700, 520), (1000, 600)])
    say("Every entry opens the model element behind it, so the canvas and the model never disagree", needs=("Key",))


def _compliance_frameworks(page, base, say):
    _open(page, base, "/dashboard/compliance", 600)
    say(MODULE_RECORDINGS["compliance-frameworks"]["title"])
    wander(page, [(640, 300), (900, 340), (700, 420)])
    say("%s controls across %s frameworks, mapped to the applications that carry them" % tuple(reversed(fig(page, r"Active frameworks\s+(\d+)\s+Controls\s+(\d+)"))))
    page.mouse.wheel(0, 360)
    page.wait_for_timeout(900)
    _click(page, page.get_by_role("link", name=re.compile(r"^A\.5\.11")))
    page.get_by_role("heading", name="Control A.5.11").wait_for(timeout=30000)
    page.get_by_text("Applications this control is mapped to").first.evaluate("e => e.scrollIntoView({block: 'start'})")
    page.wait_for_timeout(1500)
    page.clip_mark()
    say("Each control lists its applications and whether it is implemented, verified or planned",
        needs=("Calibration Ledger",))
    wander(page, [(500, 420), (700, 440), (560, 470)])
    say("%s applications are mapped to this control, each with its own status" % fig(page, r"(\d+) rows", "main")[0])


def _risk_register(page, base, say):
    _open(page, base, "/risks/", 600)
    say(MODULE_RECORDINGS["risk-register"]["title"])
    wander(page, [(700, 640), (900, 700), (760, 760)])
    say("The top risk reaches %s elements of the model, and the register names them" % fig(page, r"reaches (\d+) elements")[0])
    _click(page, page.get_by_role("link", name="Calibration Ledger").first)
    page.wait_for_selector("text=depend on it", timeout=30000)
    page.wait_for_timeout(2500)
    page.clip_mark()
    near = fig(page, r"(\d+) depend on it\s*\S*\s*it depends on (\d+)")
    say("%s things depend on this application, and it depends on %s" % near)
    _click(page, page.get_by_role("button", name="3"))
    page.wait_for_function("(n) => !new RegExp('it depends on ' + n + '\\\\b').test(document.body.innerText)", arg=near[1], timeout=30000)
    page.wait_for_timeout(1500)
    say("Three steps out it depends on %s things, not %s: the full reach of a failure" % (fig(page, r"it depends on (\d+)")[0], near[1]))
    wander(page, [(600, 520), (800, 600), (700, 460)])


def _industry_apqc(page, base, say):
    _open(page, base, "/industry-apqc/", 600)
    say(MODULE_RECORDINGS["industry-apqc"]["title"])
    wander(page, [(700, 420), (900, 520), (600, 600)])
    _click(page, page.get_by_role("link", name=re.compile(r"^Banking & Financial Services")))
    page.get_by_text("Develop Vision and Strategy").first.wait_for(timeout=30000)
    page.wait_for_timeout(1500)
    say("%s categories and %s processes, ready to map to your model" % fig(page, r"Categories\s+(\d+)\s+Processes\s+(\d+)"))
    row = page.get_by_text("Deliver Services", exact=False).first
    _click(page, row)
    page.wait_for_timeout(1500)
    page.clip_mark()
    say("Each category lists the processes inside it", needs=("Deliver Services",))
    wander(page, [(640, 500), (860, 560), (700, 420), (820, 520), (640, 600)])
    page.wait_for_timeout(1800)


def _digital_twin(page, base, say):
    _open(page, base, "/intelligence/twin-map", 600)
    page.get_by_text("Neighbourhood loaded").first.wait_for(timeout=45000)
    page.wait_for_timeout(1500)
    say(MODULE_RECORDINGS["digital-twin"]["title"])
    wander(page, [(700, 700), (860, 660), (640, 720), (1000, 690)])
    centre = fig(page, r"Unsaved view: (.+?), \d+ hops", "body")[0]
    say("%s elements and %s relationships around %s" % (*fig(page, r"(\d+) elements, (\d+) relationships", "body"), centre))
    listed = fig(page, r"connections as a list \((\d+)\)", "body")[0]
    _click(page, page.get_by_role("button", name=re.compile(r"Show the connections as a list")))
    page.get_by_role("columnheader", name="Relationship").wait_for(timeout=30000)
    page.wait_for_timeout(1500)
    page.clip_mark()
    say("Every connection is named, from one system to the next", needs=("Relationship",))
    wander(page, [(760, 380), (900, 440), (760, 500)])
    say("All %s connections are listed, each one named and recorded as explicit" % listed, needs=("Explicit",))


STORIES = {
    "applications": _applications,
    "business-model-canvas": _business_model_canvas,
    "digital-twin": _digital_twin,
    "compliance-frameworks": _compliance_frameworks,
    "industry-apqc": _industry_apqc,
    "risk-register": _risk_register,
}

def _count(locator):
    return lambda page, seen: locator(page).count()


def _count_of(pattern, page):
    found = re.search(pattern, page.locator("body").inner_text())
    return int(found.group(1)) if found else 0


def _url_has(fragment):
    return lambda page, seen: int(fragment in page.url)


def _in_main(pattern):
    rx = re.compile(pattern)
    return lambda page, seen: len(rx.findall(page.locator("main").inner_text()))


CHECKS: dict[str, list[Check]] = {
    "business-model-canvas": [
        Check("the canvas boxes are filled with model elements (at least ten entries link to the model)",
              lambda page, seen: page.locator("main a[href^='/architecture/']").count() // 2, 10),
    ],
    "compliance-frameworks": [
        Check("a control is open", _in_main(r"Control A\.5\.11")),
        Check("the control lists the applications that carry it", _in_main(r"Calibration Ledger")),
    ],
    "digital-twin": [
        Check("the map holds at least ten elements", lambda page, seen: _count_of(r"(\d+) elements?", page), 10),
        Check("the connections are listed with their relationship", _count(lambda page: page.get_by_role("columnheader", name="Relationship"))),
    ],
    "industry-apqc": [
        Check("the framework's categories are listed", _in_main(r"Develop Vision and Strategy")),
    ],
    "risk-register": [
        Check("the impact graph of an application is open", _url_has("/impact")),
        Check("the graph states what depends on the application", _seen_count(r"\d+ depend on it")),
    ],
    "applications": [
        Check("the list is narrowed to the one application searched for", _in_main(r"1 shown of")),
        Check("the application's owner is on the row", _in_main(r"Owner: ")),
    ],
}


def _sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def record_all(browser, base_url, out_dir, other_organisation_names=(), only=None):
    """Record every module recording (or ``only`` those slugs). Returns (written paths, failure lines)."""
    out = pathlib.Path(out_dir)
    manifest_path = out / MANIFEST
    kept = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    written, failed = [], []
    for slug in sorted(STORIES):
        if only and slug not in only:
            continue
        entry = MODULE_RECORDINGS[slug]
        seen = FrameSeen()
        measured = {}
        captions = []

        def steps(page, slug=slug, seen=seen, measured=measured, entry=entry, captions=captions):
            def say(text, hold=CAPTION_HOLD, needs=()):
                captions.append(text)
                said(seen, page, text, hold, needs)

            STORIES[slug](page, base_url, say)
            seen(page)
            measured["rows"] = run_checks(page, seen, CHECKS.get(slug, []))
            closing_card(page, entry["promise"])

        try:
            clip = record_clip(browser, base_url, slug, steps, out_dir=str(out), hide_selectors=HIDE,
                               start_at_steps=True, max_bytes=MAX_BYTES - 64 * 1024, reserve_bottom=RESERVE_BOTTOM)
            clip_path = pathlib.Path(clip)
            texts = seen.texts + captions + [entry["promise"], SIGN_UP]
            leaks = scan_for_leaks(texts, other_organisation_names)
            leaks += frame_problems(seen.frames)
            if not MIN_CAPTIONS <= len(captions) <= MAX_CAPTIONS:
                leaks.append("%d captions, outside %d to %d" % (len(captions), MIN_CAPTIONS, MAX_CAPTIONS))
            seconds = ffprobe_duration_seconds(clip_path)
            size = clip_path.stat().st_size
            problem = None
            if leaks:
                problem = "; ".join(sorted(set(leaks)))
            elif not (MIN_SECONDS <= seconds <= MAX_SECONDS):
                problem = "it runs %.1f seconds, outside %d to %d" % (seconds, MIN_SECONDS, MAX_SECONDS)
            elif size > MAX_BYTES:
                problem = "it is %d bytes, over %d" % (size, MAX_BYTES)
            if problem:
                raise ClipRefused(problem)
            kept[slug] = {"width": 1440, "height": 900, "duration_seconds": round(seconds, 1), "bytes": size,
                          "captured_date": time.strftime("%Y-%m-%d"), "clip_sha256": _sha256(clip_path),
                          "captions": captions, "closing": [entry["promise"], SIGN_UP],
                          "checks": measured["rows"]}
            written.append(str(clip_path))
            print("  clip   %-26s %7d bytes  %.1fs  %d checks passed" % (slug, size, seconds, len(measured["rows"])))
        except Exception as exc:  # noqa: BLE001 - one recording failing must not hide the others
            (out / (slug + ".webm")).unlink(missing_ok=True)
            (out / (slug + ".poster.webp")).unlink(missing_ok=True)
            kept.pop(slug, None)
            failed.append("%s: refused, %s: %s" % (slug, type(exc).__name__, str(exc)[:300]))
    manifest_path.write_text(json.dumps(kept, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return written, failed
