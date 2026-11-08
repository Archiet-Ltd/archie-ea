# -*- coding: utf-8 -*-
"""The one recording kit: pointer, caption and title overlay, encode, poster, and ``record_clip``.

Playwright's video shows no mouse pointer, so recordings add a drawn one that follows real mouse events. The
overlay lives only in recordings and in the clips made here, which is why it carries inline styles rather than
the product's tokens. The scripts that capture product clips and the browser test harness all import this
module; nothing else in the repository defines the overlay.

The capture standard for every public product image
---------------------------------------------------
``capture_standard_image`` is the one way a product image for a public page is made. An image that does not
meet it is refused, never shipped:

* content only: the page's content element, with no sidebar, top bar, breadcrumb, banner, person's name, build
  id or date in frame; the frame around it is drawn by the page (``.public-capture``), never by the picture;
* two versions of one screen: desktop (content 1104 CSS px wide at most) and phone (390 wide, content 358),
  each taken at 2x, so each is shown at half its pixel width, one image pixel to one screen pixel;
* every piece of text in frame is 12 CSS px or larger, at 1440 and at 390;
* no empty state, unset value, bare dash or zero in frame (``FORBIDDEN_IN_FRAME``);
* each file under 300 KB, written at its full size (quality falls, size never does);
* what was measured is written to ``captures.json`` beside the images, which the test reads.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from app.services.public_pages import DEMO_PERSONA

MAX_IMAGE_BYTES = 200 * 1024
STANDARD_IMAGE_BYTES = 300 * 1024
STANDARD_DENSITY = 2
STANDARD_MIN_TEXT_PX = 12
STANDARD_DESKTOP = {"viewport": (1408, 2600), "content_max": 1104}
STANDARD_PHONE = {"viewport": (390, 3200), "content_max": 358}
STANDARD_MANIFEST = "captures.json"
MAX_VIDEO_BYTES = 3 * 1024 * 1024
CLIP_MAX_BYTES = 8 * 1024 * 1024

# The demonstration organisation's signed-in person. ``record_clip`` refuses any other.
DEMO_USER_EMAIL = DEMO_PERSONA
DEMO_PASSWORD_VARIABLE = "DEMO_USER_PASSWORD"
DEMO_TOTP_VARIABLE = "DEMO_MFA_SECRET"

SIGN_IN_WAIT_MS = 10000

# Text that puts an empty, unavailable, unset or placeholder state in frame, or a bare zero standing in for a
# measurement. A clip showing any of it is refused; there is no per-clip exception.
FORBIDDEN_IN_FRAME = re.compile(
    r"Not recorded|Not shown for your role|unavailable|No [\w ]+ yet|Something went wrong|don't have access|"
    r"Nothing here|No data|\bN/A\b|\bTBC\b|\bundefined\b|\bNaN\b|"
    r"\bNot set\b|\bNot assessed\b|\bNot reviewed\b|\bNo [\w ]+ recorded\b|(?i:\bno (?:owner|capability|vendor|licence|DR)\b)|"
    r"Appears here|Missing to reach|\bUnassigned\b|^None$|\tNone\t|\tNone$|^None\t|\bNone (?:renew|due|recorded|set)\b|"
    r"(?<![\w.,\u00a3$%/-])0(?![\w.,%/-])|^\u2014$|\t\u2014\t|\t\u2014$|^\u2014\t", re.M)

_POINTER_JS = r"""
  const pointer = () => {
    let c = document.getElementById('__cur');
    if (!c) {
      c = document.createElement('div'); c.id = '__cur';
      c.style.cssText = 'position:fixed;left:-100px;top:-100px;z-index:2147483647;width:24px;height:24px;margin:-12px 0 0 -12px;border-radius:50%;background:rgba(37,99,235,.22);border:2.5px solid #2563eb;box-shadow:0 0 12px rgba(37,99,235,.75);pointer-events:none;transition:transform .12s ease';
      (document.documentElement).appendChild(c);
    }
    return c;
  };
  const place = (x, y) => { const c = pointer(); c.style.left = x + 'px'; c.style.top = y + 'px'; };
  const pulse = () => { const c = pointer(); c.style.transform = 'scale(.5)'; setTimeout(() => { c.style.transform = 'scale(1)'; }, 160); };
  window.addEventListener('mousemove', (e) => place(e.clientX, e.clientY), true);
  window.addEventListener('mousedown', (e) => { place(e.clientX, e.clientY); pulse(); }, true);
  window.__cur_set = place;
  window.__cur_pulse = pulse;
"""

_CAPTION_JS = r"""
  const bar = () => {
    let b = document.getElementById('__cap');
    if (!b) {
      b = document.createElement('div'); b.id = '__cap';
      b.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:2147483646;padding:13px 26px;background:linear-gradient(0deg,rgba(15,23,42,.94),rgba(15,23,42,.74));color:#fff;font-family:system-ui,Segoe UI,sans-serif;display:flex;align-items:center;gap:14px;box-shadow:0 -2px 24px rgba(0,0,0,.35);pointer-events:none';
      b.innerHTML = '<span id="__who" style="background:#2563eb;padding:5px 13px;border-radius:999px;font:600 14px system-ui;white-space:nowrap"></span><span id="__txt" style="font:500 17px system-ui;opacity:.96"></span>';
      document.documentElement.appendChild(b);
    }
    return b;
  };
  const ensure = () => { if (window.__eager_bar) bar(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ensure); else ensure();
  window.__caption = (w, t) => { bar(); const W = document.getElementById('__who'), T = document.getElementById('__txt'); if (W) { W.textContent = w; W.style.display = w ? '' : 'none'; } if (T) T.textContent = t; };
  window.__title = (t, s) => { const o = document.createElement('div'); o.id = '__tc';
    o.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:radial-gradient(circle at 50% 38%,#1e293b,#0b1220);color:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;font-family:system-ui,Segoe UI,sans-serif;opacity:0;transition:opacity .45s;pointer-events:none';
    o.innerHTML = '<div style="font:800 46px/1.15 system-ui;letter-spacing:-1px;text-align:center">' + t + '</div><div style="font:400 22px system-ui;opacity:.82;max-width:780px;text-align:center;line-height:1.4">' + s + '</div>';
    document.documentElement.appendChild(o); requestAnimationFrame(() => { o.style.opacity = '1'; }); };
  window.__title_off = () => { const o = document.getElementById('__tc'); if (o) { o.style.opacity = '0'; setTimeout(() => o.remove(), 450); } };
"""

_TIMED_JS = r"""
  const t0 = window.__clip_t0 || Date.now();
  const caps = window.__clip_captions || [];
  const showClock = !!window.__clip_clock;
  const tick = () => {
    const s = (Date.now() - t0) / 1000;
    if (showClock) {
      let k = document.getElementById('__clk');
      if (!k) {
        k = document.createElement('div'); k.id = '__clk';
        k.style.cssText = 'position:fixed;top:12px;right:12px;z-index:2147483646;padding:6px 12px;border-radius:8px;background:rgba(15,23,42,.88);color:#fff;font:600 18px/1 ui-monospace,Menlo,monospace;pointer-events:none';
        document.documentElement.appendChild(k);
      }
      const m = Math.floor(s / 60), r = Math.floor(s % 60);
      k.textContent = (m < 10 ? '0' : '') + m + ':' + (r < 10 ? '0' : '') + r;
    }
    let text = null;
    for (const c of caps) { if (s >= c[0]) text = c[1]; }
    if (text !== null && text !== window.__shown) { window.__shown = text; window.__caption('', text); }
  };
  if (showClock || caps.length) setInterval(tick, 200);
"""


def overlay_script(*, caption_bar: bool = False, timed: bool = False) -> str:
    """The overlay as an immediately-invoked script for ``add_init_script``.

    ``caption_bar`` draws the lower bar at load (the walkthrough recorder wants it); otherwise it appears when a
    caption is first set. ``timed`` adds the clock badge and timed captions read from ``window.__clip_*``.
    """
    eager = "window.__eager_bar = true;" if caption_bar else ""
    timed_js = _TIMED_JS if timed else ""
    return "(() => {" + eager + _POINTER_JS + _CAPTION_JS + timed_js + "})();"


def pointer_script() -> str:
    """Only the pointer: what the test harness adds while recording, so no bar or card can appear."""
    return "(() => {" + _POINTER_JS + "})();"


def _ffmpeg(*args: str) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg failed ({' '.join(cmd)}):\n{result.stderr}")


def have_ffmpeg() -> bool:
    try:
        return subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode == 0
    except OSError:
        return False


def ffprobe_duration_seconds(path: pathlib.Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe failed on {path}:\n{result.stderr}")
    return float(result.stdout.strip())


def compress_to_webp(png_bytes: bytes, dest: pathlib.Path, target_width: int,
                     max_bytes: int = MAX_IMAGE_BYTES) -> tuple[int, int]:
    """Write a PNG screenshot out as a WebP under max_bytes. Returns (width, height).

    Shrinks width in steps before dropping quality much further than ~40: a
    smaller-but-crisp image reads better than a full-size, muddier one, for a
    product screenshot full of small text and icons.
    """
    import io

    from PIL import Image

    dest.parent.mkdir(parents=True, exist_ok=True)
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")

    width = min(target_width, im.width)
    quality = 82
    while True:
        height = round(im.height * (width / im.width))
        resized = im.resize((width, height), Image.LANCZOS)
        resized.save(dest, format="WEBP", quality=quality, method=6)
        size = dest.stat().st_size
        if size <= max_bytes or (quality <= 35 and width <= 480):
            return width, height
        if quality > 35:
            quality -= 12
        else:
            width = round(width * 0.85)
            quality = 60


def encode_recording(raw_webm: pathlib.Path, dest: pathlib.Path,
                     width: int = 1280, max_bytes: int = MAX_VIDEO_BYTES, start_seconds: float = 0.0) -> None:
    """Transcode Playwright's raw (VP8) capture to a size-capped VP9 WebM.

    No audio track (Playwright's recording never has one; -an is explicit
    rather than assumed). Tightens crf upward until the cap is met -- product
    UI footage with little motion compresses hard, so a wide crf range rarely
    takes more than one or two passes in practice.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    seek = ["-ss", "%.2f" % start_seconds] if start_seconds > 0 else []
    for crf in (32, 36, 40, 44, 48, 52):
        _ffmpeg(
            *seek,
            "-i", str(raw_webm),
            "-an",
            "-vf", f"scale={width}:-2",
            "-c:v", "libvpx-vp9",
            "-crf", str(crf), "-b:v", "0",
            "-row-mt", "1", "-deadline", "good", "-cpu-used", "2",
            str(dest),
        )
        if dest.stat().st_size <= max_bytes:
            return
    raise SystemExit(f"could not encode {raw_webm} under {max_bytes} bytes")


def extract_poster(video: pathlib.Path, dest_webp: pathlib.Path, at_seconds: float,
                   target_width: int = 1280) -> tuple[int, int]:
    tmp_png = video.with_suffix(".poster.png")
    _ffmpeg("-ss", str(at_seconds), "-i", str(video), "-frames:v", "1", str(tmp_png))
    width, height = compress_to_webp(tmp_png.read_bytes(), dest_webp, target_width=target_width)
    tmp_png.unlink(missing_ok=True)
    return width, height


def slug(text: str) -> str:
    """A file-name-safe form of a test id or clip name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:180] or "clip"


class ClipRefused(RuntimeError):
    """The clip was not started because it would not show the demonstration organisation."""


def _sign_in(page, base_url: str, email: str, password: str) -> None:
    page.goto(base_url + "/account/login", wait_until="domcontentloaded", timeout=60000)
    page.fill("#email", email)
    page.fill("#password", password)
    page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, wait_until="commit", timeout=SIGN_IN_WAIT_MS)
    except PlaywrightTimeout:
        raise ClipRefused("sign-in failed: the password was not accepted") from None
    if "/account/mfa-challenge" in page.url:
        secret = os.environ.get(DEMO_TOTP_VARIABLE)
        if not secret:
            raise ClipRefused(f"the demonstration person has two-factor sign-in on; set {DEMO_TOTP_VARIABLE}")
        import pyotp

        page.fill("#mfa-code", pyotp.TOTP(secret).now())
        page.locator("#mfa-code").press("Enter")
        try:
            page.wait_for_url(lambda url: "/account/" not in url, wait_until="commit", timeout=SIGN_IN_WAIT_MS)
        except PlaywrightTimeout:
            raise ClipRefused("sign-in failed: the two-factor code was not accepted") from None
    if "/account/login" in page.url:
        raise ClipRefused("could not sign in as the demonstration person")


def sign_in_state(browser, base_url, size=(1440, 900), dark=False, email=None):
    """The browser state of the demonstration person signed in, from a context that is closed straight away.

    Only the demonstration organisation's person is accepted, and the password and two-factor secret come from
    the environment, never from a caller.
    """
    email = email or os.environ.get("DEMO_USER_EMAIL") or DEMO_USER_EMAIL
    if email.strip().lower() != DEMO_USER_EMAIL:
        raise ClipRefused(f"clips are made only as the demonstration organisation's person; refused {email!r}")
    password = os.environ.get(DEMO_PASSWORD_VARIABLE)
    if not password:
        raise ClipRefused(f"{DEMO_PASSWORD_VARIABLE} is not set")
    context = browser.new_context(viewport={"width": size[0], "height": size[1]},
                                  color_scheme="dark" if dark else "light")
    try:
        _sign_in(context.new_page(), base_url.rstrip("/"), email, password)
        return context.storage_state()
    finally:
        context.close()


class Clip(str):
    """The clip's path, plus when (in seconds of the recording) its subject showed and its poster was taken."""

    def __new__(cls, path, subject_seen_at=0.0, poster_at=0.0):
        obj = super().__new__(cls, path)
        obj.subject_seen_at = subject_seen_at
        obj.poster_at = poster_at
        return obj


POSTER_SETTLE_MS = 600


HIDE_JS = """(() => {
  // Removed as they are inserted: the product's security policy forbids an injected style rule.
  const sel = %s;
  const sweep = () => document.querySelectorAll(sel).forEach((e) => e.remove());
  new MutationObserver(sweep).observe(document, {childList: true, subtree: true});
  sweep();
})();"""

RESERVE_JS = """(() => {
  // Inline style set from script (an injected style rule is forbidden by the product's security policy).
  const gap = "%dpx";
  const pad = () => document.querySelectorAll("main").forEach((e) => { if (e.style.paddingBottom !== gap) e.style.paddingBottom = gap; });
  new MutationObserver(pad).observe(document, {childList: true, subtree: true});
  pad();
})();"""


def start_trim(marks, steps_started):
    """Seconds cut from the start of a recording made with ``start_at_steps``.

    The clip begins at the first caption (``clip_begin``), once the page it opens on is drawn; a recording that
    never marks one begins at its first step. Either way the sign-in landing page is never in frame.
    """
    return max(marks.get("begin", steps_started) - 0.15, 0.0)


def record_clip(browser, base_url, name, steps, *, size=(1440, 900), dark=False, clock=False,
                captions=None, out_dir="test-results/clips", email=None, subject=None,
                hide_selectors=(), start_at_steps=False, max_bytes=CLIP_MAX_BYTES, reserve_bottom=0):
    """Record ``steps(page)`` as signed in to the demonstration organisation; return the clip path.

    Signing in happens in a separate, unrecorded context so the clip never shows the form, and the person's
    password and two-factor secret (read from the environment) never reach a caption, file name or log line.
    ``captions`` is a list of (seconds, text) shown as a lower-third from that many seconds into the clip.
    The poster is taken once ``steps`` has run and the ``subject`` selector (when given) is visible and has
    painted, so it shows what the clip is about and not the page it opened on.
    ``hide_selectors`` are page furniture a recording leaves out (a banner asking the person to do something
    else first), never data. With ``start_at_steps`` the opening page load is cut: the clip begins at the moment
    ``steps`` calls ``page.clip_begin()`` (its first caption, once the page it opens on is drawn), or at the
    first step when it never calls it, so the sign-in landing page is never in frame; ``steps`` may call
    ``page.clip_mark()`` when the poster frame is on screen. ``reserve_bottom`` leaves that many pixels free at
    the foot of every page so the caption bar never covers the last rows.
    ``max_bytes`` caps the encoded clip (the encoder tightens its quality until it fits).
    Writes ``<name>.webm`` and ``<name>.poster.webp`` in ``out_dir``; without ffmpeg the raw recording is kept
    as ``<name>.raw.webm`` and a line says so.
    """
    width, height = size
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = slug(name)
    scheme = "dark" if dark else "light"
    base_url = base_url.rstrip("/")
    state = sign_in_state(browser, base_url, size=size, dark=dark, email=email)

    raw_dir = out / f".{stem}.raw"
    ctx = browser.new_context(
        viewport={"width": width, "height": height}, color_scheme=scheme, storage_state=state,
        record_video_dir=str(raw_dir), record_video_size={"width": width, "height": height},
    )
    try:
        ctx.add_init_script(
            f"window.__clip_t0 = {int(time.time() * 1000)};"
            f"window.__clip_clock = {json.dumps(bool(clock))};"
            f"window.__clip_captions = {json.dumps([[float(s), str(t)] for s, t in (captions or [])])};"
        )
        ctx.add_init_script(overlay_script(timed=True))
        if hide_selectors:
            ctx.add_init_script(HIDE_JS % json.dumps(", ".join(hide_selectors)))
        if reserve_bottom:
            ctx.add_init_script(RESERVE_JS % int(reserve_bottom))
        opened = time.monotonic()
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.goto(base_url + "/", wait_until="domcontentloaded")
        if "/account/login" in page.url:
            raise ClipRefused("the recording context is not signed in as the demonstration organisation")
        marks = {}
        page.clip_mark = lambda: marks.__setitem__("poster", time.monotonic() - opened)
        page.clip_begin = lambda: marks.setdefault("begin", time.monotonic() - opened)
        steps_started = time.monotonic() - opened
        steps(page)
        if subject:
            page.locator(subject).first.wait_for(state="visible")
        subject_seen_at = time.monotonic() - opened
        page.wait_for_timeout(POSTER_SETTLE_MS)
        poster_at = marks.get("poster", time.monotonic() - opened)
        video = page.video
        ctx.close()
        raw = pathlib.Path(video.path())
    except BaseException:
        ctx.close()
        shutil.rmtree(raw_dir, ignore_errors=True)
        raise

    clip = out / f"{stem}.webm"
    if not have_ffmpeg():
        kept = out / f"{stem}.raw.webm"
        raw.replace(kept)
        print(f"ffmpeg is not installed: kept the raw recording at {kept}, not encoded and without a poster")
        raw_dir.rmdir()
        return Clip(kept, subject_seen_at, poster_at)
    trim = start_trim(marks, steps_started) if start_at_steps else 0.0
    encode_recording(raw, clip, width=width, max_bytes=max_bytes, start_seconds=trim)
    last_frame = max(ffprobe_duration_seconds(clip) - 0.2, 0.0)
    extract_poster(clip, out / f"{stem}.poster.webp", at_seconds=min(max(poster_at - trim, 0.0), last_frame),
                   target_width=width)
    shutil.rmtree(raw_dir, ignore_errors=True)
    return Clip(clip, subject_seen_at, poster_at)


# What a standard image never shows besides the empty and unset states: a date, the signed-in person, a build id.
NOT_IN_STANDARD_FRAME = re.compile(
    r"\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4}\b|\b\d{4}-\d{2}-\d{2}\b|"
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}, \d{4}\b|"
    r"\bDemo User\b|\bdemo@|\bBuild [0-9a-f]{7,}\b")

# Page furniture the standard leaves out of a content crop (never data).
STANDARD_HIDE = ["[data-testid='two-factor-banner']", "nav[aria-label='Breadcrumb']", "[aria-label='Breadcrumb']",
                 "[data-testid='service-status']"]

_SMALL_TEXT_JS = """(el) => {
  const small = [];
  let smallest = null;
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const n = walker.currentNode;
    if (!n.nodeValue.trim()) continue;
    const e = n.parentElement;
    const css = getComputedStyle(e);
    if (css.visibility === 'hidden' || css.display === 'none' || !e.getClientRects().length) continue;
    const px = parseFloat(css.fontSize);
    if (smallest === null || px < smallest) smallest = px;
    if (px < %d) small.push(px + 'px: ' + n.nodeValue.trim().slice(0, 40));
  }
  return {small: small, smallest: smallest};
}""" % STANDARD_MIN_TEXT_PX


def write_webp_at_size(png_bytes: bytes, dest: pathlib.Path, max_bytes: int = STANDARD_IMAGE_BYTES) -> tuple[int, int]:
    """Write a PNG as WebP at its full pixel size, lowering quality (never size) until under ``max_bytes``."""
    import io

    from PIL import Image

    dest.parent.mkdir(parents=True, exist_ok=True)
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    for quality in (88, 80, 72, 64, 56, 48):
        im.save(dest, format="WEBP", quality=quality, method=6)
        if dest.stat().st_size <= max_bytes:
            return im.size
    raise ClipRefused(f"{dest.name} is over {max_bytes} bytes at full size even at the lowest quality; crop less")


def _capture_variant(browser, base_url, state, path, dest, spec, *, selector, prepare, hide, variant):
    ctx = browser.new_context(viewport={"width": spec["viewport"][0], "height": spec["viewport"][1]},
                              device_scale_factor=STANDARD_DENSITY, storage_state=state, color_scheme="light")
    try:
        ctx.add_init_script(HIDE_JS % json.dumps(", ".join([*STANDARD_HIDE, *hide])))
        page = ctx.new_page()
        page.set_default_timeout(30000)
        response = page.goto(base_url + path, wait_until="networkidle")
        if response is None or response.status >= 400:
            raise ClipRefused(f"{path} answered {response.status if response else 'nothing'}")
        page.wait_for_timeout(1000)
        if prepare:
            prepare(page, variant)
        target = page.locator(selector).first
        target.wait_for(state="visible")
        text = target.inner_text()
        for pattern, what in ((FORBIDDEN_IN_FRAME, "an empty, unset or placeholder state"),
                              (NOT_IN_STANDARD_FRAME, "a date, a person's name or a build id")):
            found = pattern.search(text)
            if found:
                raise ClipRefused(f"{path} ({variant}) shows {what} in frame: {found.group(0).strip()!r}")
        measured = target.evaluate(_SMALL_TEXT_JS)
        if measured["small"]:
            raise ClipRefused(f"{path} ({variant}) has text under {STANDARD_MIN_TEXT_PX}px: {measured['small'][:3]}")
        if measured["smallest"] is None:
            raise ClipRefused(f"{path} ({variant}) shows no text to measure")
        box = target.bounding_box()
        width = min(box["width"], spec["content_max"])
        png = page.screenshot(full_page=True, clip={"x": box["x"], "y": box["y"], "width": width,
                                                     "height": box["height"]})
        px_w, px_h = write_webp_at_size(png, dest)
        return {"css_width": px_w // STANDARD_DENSITY, "css_height": px_h // STANDARD_DENSITY,
                "bytes": dest.stat().st_size, "min_text_px": measured["smallest"]}
    finally:
        ctx.close()


def capture_standard_image(browser, base_url, slug, path, out_dir, *, selector="main", prepare=None, hide=(),
                           state=None):
    """Make the desktop and phone images of one screen to the standard above; return what was measured.

    Writes ``<slug>.webp`` and ``<slug>-mobile.webp`` in ``out_dir`` and records them in ``captures.json``.
    Signed in as the demonstration person only. ``prepare(page, variant)`` opens the state to show (variant is
    ``desktop`` or ``phone``); ``hide`` adds selectors for furniture to leave out. Raises ``ClipRefused`` and
    leaves no file behind when the screen breaks any line of the standard.
    """
    out = pathlib.Path(out_dir)
    base_url = base_url.rstrip("/")
    state = state or sign_in_state(browser, base_url)
    scratch = out / f".{slug}.standard"
    shutil.rmtree(scratch, ignore_errors=True)
    written = {}
    try:
        for variant, spec, name in (("desktop", STANDARD_DESKTOP, f"{slug}.webp"),
                                    ("phone", STANDARD_PHONE, f"{slug}-mobile.webp")):
            written[variant] = _capture_variant(browser, base_url, state, path, scratch / name, spec,
                                                selector=selector, prepare=prepare, hide=hide, variant=variant)
        for name in (f"{slug}.webp", f"{slug}-mobile.webp"):
            (scratch / name).replace(out / name)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    manifest = out / STANDARD_MANIFEST
    data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
    data[slug] = {"density": STANDARD_DENSITY, **written}
    manifest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data[slug]
