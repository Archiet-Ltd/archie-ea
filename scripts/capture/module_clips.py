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
CAPTION_HOLD = 3.4
CLOSING_HOLD = 5.0

# Page furniture a recording leaves out: the banner asking the person to set up two-factor sign-in.
HIDE = ["[data-testid='two-factor-banner']"]

# Text that puts an empty, unavailable or placeholder state in frame. A clip showing any of it is refused.
FORBIDDEN_IN_FRAME = re.compile(
    r"Not recorded|Not shown for your role|unavailable|No [\w ]+ yet|Something went wrong|don't have access|"
    r"Nothing here|No data|\bN/A\b|\bTBC\b|\bundefined\b|\bNaN\b|\t—\t|\t—$|^—\t", re.M)


# Text a particular recording may show although it is on the forbidden list, each with the reason it is not an
# empty screen. Everything else on the list is refused.
ALLOWED_IN_FRAME: dict[str, list[str]] = {
    # the side panel of the impact graph, beside a graph that is full: nothing has been decided about this element
    "applications": ["No decision is recorded against this element yet."],
    "risk-register": ["No decision is recorded against this element yet."],
    # a measured zero on the gap register's summary, with its explanation beside it
    "gap-analysis": ["No gap has been closed yet"],
}


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


def said(see, page, text, hold=CAPTION_HOLD):
    """Show ``text`` as the caption, keep it on screen for ``hold`` seconds, and keep what the screen said."""
    see(page)
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
    say("%s applications, each with its owner, lifecycle stage and capability" % fig(page, r"(\d+) shown of")[0])
    _click(page, page.get_by_role("link", name="71%").first)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1800)
    say("One fact sheet holds the owner, cost, criticality and lifecycle")
    link = page.get_by_role("link", name="View impact graph")
    link.scroll_into_view_if_needed()
    page.wait_for_timeout(900)
    _click(page, link)
    page.wait_for_selector("text=depend on it", timeout=30000)
    page.wait_for_timeout(2500)
    page.clip_mark()
    say("%s things depend on Calibration Ledger, and it depends on %s" % fig(page, r"(\d+) depend on it\s*\S*\s*it depends on (\d+)"))


def _gap_analysis(page, base, say):
    _open(page, base, "/enterprise/implementation/gap-analysis", 600)
    say(MODULE_RECORDINGS["gap-analysis"]["title"])
    wander(page, [(700, 520), (900, 640), (760, 560)])
    say("%s recorded gaps, each with its severity, status and the work that closes it" % fig(page, r"(\d+)\s+Recorded gaps")[0])
    _click(page, page.get_by_role("link", name="Calibration throughput gap").first)
    page.get_by_text("What depends on Calibration throughput gap").wait_for(timeout=30000)
    page.wait_for_timeout(1500)
    page.clip_mark()
    say("If this gap is not closed, %s elements of the model are affected" % fig(page, r"(\d+) elements are affected")[0])
    wander(page, [(700, 480), (820, 420)])
    page.mouse.wheel(0, 460)
    page.wait_for_timeout(1200)
    wander(page, [(700, 400), (860, 520)])
    say("Its connections name the projects and stages that depend on it")


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
    say("Select any entry to open the model element behind it")


def _compliance_frameworks(page, base, say):
    _open(page, base, "/dashboard/compliance", 600)
    say(MODULE_RECORDINGS["compliance-frameworks"]["title"])
    wander(page, [(640, 300), (900, 340), (700, 420)])
    say("%s controls across %s frameworks, mapped to the applications that carry them" % tuple(reversed(fig(page, r"Active frameworks\s+(\d+)\s+Controls\s+(\d+)"))))
    page.mouse.wheel(0, 360)
    page.wait_for_timeout(900)
    _click(page, page.get_by_role("link", name=re.compile(r"^A\.5\.11")))
    page.get_by_role("heading", name="Control A.5.11").wait_for(timeout=30000)
    page.wait_for_timeout(1500)
    page.clip_mark()
    say("Each control lists its applications and whether it is implemented, verified or planned")
    wander(page, [(500, 420), (700, 440), (560, 470)])
    say("Every control in the list opens the same way, so an audit question is one click away")


def _risk_register(page, base, say):
    _open(page, base, "/risks/", 600)
    say(MODULE_RECORDINGS["risk-register"]["title"])
    wander(page, [(700, 640), (900, 700), (760, 760)])
    say("The top risk reaches %s elements of the model, and the register names them" % fig(page, r"reaches (\d+) elements")[0])
    _click(page, page.get_by_role("link", name="Calibration Ledger").first)
    page.wait_for_selector("text=depend on it", timeout=30000)
    page.wait_for_timeout(2500)
    page.clip_mark()
    say("One click shows what an application at risk supports and what depends on it")
    _click(page, page.get_by_role("button", name="3"))
    page.wait_for_timeout(1500)
    say("Widen the view to three steps to see the full reach of a failure")
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
    say("Open any category to see the processes inside it")
    _click(page, page.get_by_text("Manage Customer Service", exact=False).first)
    page.wait_for_timeout(1200)
    say("Six industry frameworks are available, each with its own processes")
    wander(page, [(640, 500), (860, 560), (700, 420)])


def _capability_maturity(page, base, say):
    _open(page, base, "/capability-maturity/heatmap", 600)
    say(MODULE_RECORDINGS["capability-maturity"]["title"])
    wander(page, [(700, 300), (900, 340)])
    say("%s capabilities assessed: average maturity %s against a target of %s" % (fig(page, r"Assessed\s+(\d+)")[0], *fig(page, r"Average current\s+([\d.]+).*?Average target\s+([\d.]+)")))
    page.mouse.wheel(0, 420)
    page.wait_for_timeout(1200)
    page.mouse.move(700, 560, steps=24)
    page.clip_mark()
    say("Each capability shows its current level, its target and the gap between them")
    page.mouse.wheel(0, 380)
    page.wait_for_timeout(1200)
    page.mouse.move(640, 480, steps=24)
    say("The widest gaps are the capabilities to invest in first")
    page.mouse.wheel(0, 420)
    page.wait_for_timeout(1000)
    page.mouse.move(700, 420, steps=24)
    say("Every row is dated, so the picture is a record and not an opinion")
    wander(page, [(560, 500), (860, 460)])


def _duplicate_detection(page, base, say):
    _open(page, base, "/duplicate-detection/simple", 600)
    say(MODULE_RECORDINGS["duplicate-detection"]["title"])
    wander(page, [(700, 300), (900, 340), (700, 420)])
    say("%s groups of overlapping applications, worth about %s a year" % (fig(page, r"Duplicate Groups\s+(\d+)")[0], fig(page, r"Estimated Savings\s+(\u00a3[\d,]+)")[0]))
    page.mouse.wheel(0, 700)
    page.wait_for_timeout(1500)
    page.mouse.move(700, 500, steps=24)
    page.clip_mark()
    say("Each group names its applications, ready to add to the consolidation list")
    page.mouse.wheel(0, 500)
    page.wait_for_timeout(1000)
    page.mouse.move(760, 520, steps=24)
    say("Exact matches and near matches are told apart, so the decision is yours")
    page.mouse.wheel(0, 600)
    page.wait_for_timeout(1200)
    wander(page, [(640, 460), (880, 520)])
    say("Run Detection counts the groups again whenever you choose")


def _digital_twin(page, base, say):
    _open(page, base, "/intelligence/twin-map", 600)
    page.get_by_text("Neighbourhood loaded").first.wait_for(timeout=45000)
    page.wait_for_timeout(1500)
    say(MODULE_RECORDINGS["digital-twin"]["title"])
    wander(page, [(700, 700), (860, 660), (640, 720), (1000, 690)])
    say("%s elements and %s relationships around Calibration Ledger" % fig(page, r"(\d+) elements, (\d+) relationships", "body"))
    _click(page, page.get_by_role("button", name=re.compile(r"Show the connections as a list")))
    page.get_by_role("columnheader", name="Relationship").wait_for(timeout=30000)
    page.wait_for_timeout(1500)
    page.clip_mark()
    say("Every connection is named, from one system to the next")
    wander(page, [(760, 380), (900, 440), (760, 500)])
    say("The same map opens on any system, drawn from the records you hold")


def _roadmaps(page, base, say):
    _open(page, base, "/capability-roadmap", 600)
    say(MODULE_RECORDINGS["roadmaps"]["title"])
    wander(page, [(700, 300), (900, 340)])
    page.mouse.wheel(0, 520)
    page.wait_for_timeout(1200)
    say("%s plateaus, %s gaps and %s work packages on one timeline" % (fig(page, r"Plateaus\s+(\d+)")[0], fig(page, r"Recorded gaps\s+(\d+)")[0], fig(page, r"Work Packages\s+(\d+)")[0]))
    wander(page, [(640, 520), (900, 560)])
    link = page.locator("main a[href='/implementation/work-packages/1']").first
    link.scroll_into_view_if_needed()
    page.wait_for_timeout(900)
    _click(page, link)
    page.get_by_role("heading", name="Deploy automated calibration rigs").wait_for(timeout=30000)
    page.wait_for_timeout(1500)
    page.clip_mark()
    say("Open any work package to see its owner, progress and the gap it closes")
    wander(page, [(700, 300), (800, 420)])
    say("The timeline runs from the baseline architecture to the target")


def _projects(page, base, say):
    _open(page, base, "/enterprise/implementation/work-packages", 600)
    say(MODULE_RECORDINGS["projects"]["title"])
    wander(page, [(700, 300), (900, 340), (760, 420)])
    say("%s work packages: %s under way and %s finished" % (fig(page, r"(\d+)\s+Work packages")[0], fig(page, r"(\d+)\s+In progress")[0], fig(page, r"(\d+)\s+Completed")[0]))
    wander(page, [(900, 520), (700, 640)])
    say("Nothing is overdue")
    _click(page, page.locator("main a[href='/implementation/work-packages/1']").first)
    page.get_by_role("heading", name="Deploy automated calibration rigs").wait_for(timeout=30000)
    page.wait_for_timeout(1500)
    page.clip_mark()
    say("One work package shows its owner, dates, progress and the gap it closes")
    wander(page, [(700, 320), (860, 420)])
    say("Each work package is tied to the gap it closes, so progress is progress on the gap")


STORIES = {
    "applications": _applications,
    "business-model-canvas": _business_model_canvas,
    "capability-maturity": _capability_maturity,
    "digital-twin": _digital_twin,
    "projects": _projects,
    "roadmaps": _roadmaps,
    "compliance-frameworks": _compliance_frameworks,
    "duplicate-detection": _duplicate_detection,
    "gap-analysis": _gap_analysis,
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
    "capability-maturity": [
        Check("the heatmap lists capabilities with a current and a target level", _in_main(r"CURRENT")),
        Check("the average current and target maturity are stated", _in_main(r"AVERAGE TARGET")),
    ],
    "compliance-frameworks": [
        Check("a control is open", _in_main(r"Control A\.5\.11")),
        Check("the control lists the applications that carry it", _in_main(r"Calibration Ledger")),
    ],
    "digital-twin": [
        Check("the map holds at least ten elements", lambda page, seen: _count_of(r"(\d+) elements?", page), 10),
        Check("the connections are listed with their relationship", _count(lambda page: page.get_by_role("columnheader", name="Relationship"))),
    ],
    "projects": [
        Check("the work package is open with its progress", _in_main(r"Progress")),
        Check("the work package names the gap it closes", _in_main(r"Calibration throughput gap")),
    ],
    "roadmaps": [
        Check("the work package opened from the roadmap shows its progress", _in_main(r"Progress")),
        Check("the roadmap itself was shown with its plateaus", _seen_count(r"Plateaus")),
    ],
    "duplicate-detection": [
        Check("the duplicate groups are listed", _in_main(r"Fuzzy: Inventory Manager")),
        Check("the yearly saving is stated in pounds", _in_main(r"Estimated Savings\s+\u00a3[\d,]+")),
    ],
    "gap-analysis": [
        Check("the gap's page states how many elements are affected", _in_main(r"\d+ elements are affected")),
    ],
    "industry-apqc": [
        Check("the framework's categories are listed", _in_main(r"Develop Vision and Strategy")),
    ],
    "risk-register": [
        Check("the impact graph of an application is open", _url_has("/impact")),
        Check("the graph states what depends on the application", _seen_count(r"\d+ depend on it")),
    ],
    "applications": [
        Check("the impact graph of the application is open", lambda page, seen: int("/impact" in page.url)),
        Check("the graph states what depends on the application and what it depends on",
              _seen_count(r"\d+ depend on it . it depends on \d+")),
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
            def say(text, hold=CAPTION_HOLD):
                captions.append(text)
                said(seen, page, text, hold)

            STORIES[slug](page, base_url, say)
            seen(page)
            measured["rows"] = run_checks(page, seen, CHECKS.get(slug, []))
            closing_card(page, entry["promise"])

        try:
            clip = record_clip(browser, base_url, slug, steps, out_dir=str(out), hide_selectors=HIDE,
                               start_at_steps=True, max_bytes=MAX_BYTES - 64 * 1024)
            clip_path = pathlib.Path(clip)
            texts = seen.texts + captions + [entry["promise"], SIGN_UP]
            leaks = scan_for_leaks(texts, other_organisation_names)
            for text in seen.frames:
                for allowed in ALLOWED_IN_FRAME.get(slug, []):
                    text = text.replace(allowed, "")
                found = FORBIDDEN_IN_FRAME.search(text)
                if found:
                    leaks.append("on screen: %r in %r" % (found.group(0).strip(),
                                                          text[max(found.start() - 50, 0):found.end() + 30]))
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
