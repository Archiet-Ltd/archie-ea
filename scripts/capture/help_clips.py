# -*- coding: utf-8 -*-
"""The "See how" clips: each is the next step done in the demonstration organisation.

``scripts/capture_screenshots.py --help-clips`` seeds the demonstration organisation, boots the app and calls
``record_all``. The clips are keyed by ``HELP_CLIPS`` in ``app/services/public_pages.py`` (the same registry the
empty screens read) and written to the image tree, so a re-recording replaces the file the screen already serves.

Every clip is recorded with ``record_clip`` (demonstration person only) and then the text of every screen it
showed is scanned for a secret and for the name of any other organisation. A clip that fails the scan is deleted
and the run fails; a clip outside 10 to 25 seconds is deleted and the run fails.
"""
from __future__ import annotations

import pathlib
import re

from scripts.capture.clip_kit import ffprobe_duration_seconds, record_clip

MIN_SECONDS = 10.0
MAX_SECONDS = 25.0

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


def _click(page, locator, pause=900):
    """Move the pointer to the control, wait a beat so a viewer sees it, then click."""
    locator.first.wait_for(state="visible")
    box = locator.first.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=18)
    page.wait_for_timeout(pause)
    locator.first.click()


def _open(page, base, path, hold=1800):
    page.goto(base + path, wait_until="networkidle")
    page.wait_for_timeout(hold)


def _applications_first(page, base, see):
    _open(page, base, "/applications/", 1500)
    see(page)
    _click(page, page.get_by_text("Add Application", exact=True).locator("visible=true"))
    dialog = page.get_by_role("dialog").first
    dialog.wait_for(state="visible")
    page.wait_for_timeout(900)
    field = dialog.get_by_role("textbox").first
    field.click()
    field.type("Billing Portal", delay=110)
    page.wait_for_timeout(900)
    see(page)
    page.keyboard.press("Escape")
    page.wait_for_timeout(900)
    _open(page, base, "/admin/connectors", 1000)
    _click(page, page.get_by_role("link", name="Connect GitHub"))
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1200)
    see(page)
    _open(page, base, "/applications/", 1500)
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
    _open(page, base, "/archimate/composer", 3000)
    see(page)
    _click(page, page.get_by_role("button", name=re.compile(r"Supplier integration")))
    page.wait_for_timeout(5000)
    see(page)
    page.mouse.move(900, 450, steps=20)
    page.wait_for_timeout(3500)
    see(page)


def _chat_citation(page, base, see):
    _open(page, base, "/ai-chat", 2500)
    see(page)
    _click(page, page.get_by_role("button", name=re.compile(r"^What breaks if .* fails\?$")))
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
    page.wait_for_timeout(5000)
    see(page)


def _canvases_open(page, base, see):
    _open(page, base, "/business-model/", 3000)
    see(page)
    _click(page, page.locator("main a[href*='/business-model/']").filter(has_text="Lantern Quay").first)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(6500)
    see(page)


STEPS = {
    "applications-first": (_applications_first, [
        (0, "Your applications, in one list"), (4, "Add one by hand"), (10, "Or let GitHub fill it for you")]),
    "connectors-github": (_connectors_github, [
        (0, "Connectors, for this organisation"), (5, "Connect GitHub to read your repositories")]),
    "model-from-github": (_model_from_github, [
        (0, "The model, built from what GitHub holds"), (5, "Open a view to see its applications and links")]),
    "chat-citation": (_chat_citation, [
        (0, "Ask what breaks if a system fails"), (7, "Every fact names its record"), (11, "Open a citation to see it")]),
    "compliance-controls": (_compliance_controls, [
        (0, "Regulatory frameworks"), (5, "Choose one to see its controls")]),
    "canvases-open": (_canvases_open, [
        (0, "Your canvases"), (5, "Open one: each box is an element of your model")]),
}


def record_all(browser, base_url, out_dir, other_organisation_names=(), only=None):
    """Record every clip (or ``only`` those keys). Returns (written paths, failure lines)."""
    from app.services.public_pages import HELP_CLIPS

    out = pathlib.Path(out_dir)
    written, failed = [], []
    for key in HELP_CLIPS:
        if only and key not in only:
            continue
        step, captions = STEPS[key]
        seen = Seen()
        try:
            clip = record_clip(browser, base_url, key, lambda page, step=step, seen=seen: step(page, base_url, seen),
                               captions=captions, out_dir=str(out), clock=False)
            clip_path = pathlib.Path(clip)
            leaks = scan_for_leaks(seen.texts + [text for _, text in captions], other_organisation_names)
            seconds = ffprobe_duration_seconds(clip_path)
            problem = None
            if leaks:
                problem = "; ".join(sorted(set(leaks)))
            elif not (MIN_SECONDS <= seconds <= MAX_SECONDS):
                problem = "it runs %.1f seconds, outside %d to %d" % (seconds, MIN_SECONDS, MAX_SECONDS)
            if problem:
                clip_path.unlink(missing_ok=True)
                (out / (key + ".poster.webp")).unlink(missing_ok=True)
                failed.append("%s: refused, %s" % (key, problem))
                continue
            written.append(str(clip_path))
            print("  clip   %-22s %7d bytes  %.1fs" % (key, clip_path.stat().st_size, seconds))
        except Exception as exc:  # noqa: BLE001 - one clip failing must not hide the others
            failed.append("%s: %s: %s" % (key, type(exc).__name__, str(exc)[:160]))
    return written, failed
