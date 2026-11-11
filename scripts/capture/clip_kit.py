# -*- coding: utf-8 -*-
"""The one recording kit: pointer, caption and title overlay, encode, poster, and ``record_clip``.

Playwright's video shows no mouse pointer, so recordings add a drawn one that follows real mouse events. The
overlay lives only in recordings and in the clips made here, which is why it carries inline styles rather than
the product's tokens. The scripts that capture product clips and the browser test harness all import this
module; nothing else in the repository defines the overlay.
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
MAX_VIDEO_BYTES = 3 * 1024 * 1024
CLIP_MAX_BYTES = 8 * 1024 * 1024

# The demonstration organisation's signed-in person. ``record_clip`` refuses any other.
DEMO_USER_EMAIL = DEMO_PERSONA
DEMO_PASSWORD_VARIABLE = "DEMO_USER_PASSWORD"
DEMO_TOTP_VARIABLE = "DEMO_MFA_SECRET"

SIGN_IN_WAIT_MS = 10000

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


# ── shapes for sharing ──────────────────────────────────────────────────────
# The same capture, cut three ways: wide for the site and YouTube, square for LinkedIn, upright for Shorts and
# Reels. The recording's own lower caption bar (it sits below this many pixels) is left out of every shape and
# its words are drawn again, larger, at the time each one was on screen, so no second recording is made.

CAPTION_BAR_TOP = 850
SHAPES = {"wide": (1280, 720), "square": (1080, 1080), "upright": (1080, 1920)}
SHAPE_BYTES = 2 * 1024 * 1024
MIN_CAPTION_SECONDS = 3.0
_SHAPE_BACKGROUND = "0x0b1220"
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
)


def _font_file() -> str:
    for candidate in _FONT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    raise SystemExit("no bold font found for burned-in captions; install fonts-freefont-ttf or fonts-dejavu-core")


def caption_intervals(video: pathlib.Path, expected: int) -> list[tuple[float, float]]:
    """When each caption of a recording was on screen, read from the pixels of its lower bar.

    The bar's text is the only near-white thing inside it, so a change of that white mask is a new caption. While
    the bar is not drawn (a page just loaded, or the closing card) the strip is empty or all page white, which is
    not a caption. Raises when the count found is not ``expected``.
    """
    import numpy as np

    fps = 10
    x0, y0, w, h = 20, CAPTION_BAR_TOP + 8, 1000, 34
    result = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(video), "-vf", f"fps={fps},crop={w}:{h}:{x0}:{y0},format=gray",
         "-f", "rawvideo", "-"], capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg failed reading the caption bar of {video}:\n{result.stderr.decode(errors='replace')}")
    frames = np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, h, w) > 200
    lit = frames.reshape(len(frames), -1).sum(axis=1)
    present = (lit > 40) & (lit < w * h // 4)
    # A caption is a run of lit frames; a new caption is a run that begins, or whose white mask changes by more
    # than half. A run shorter than a second is the card fading in, not a caption.
    starts, ends, previous = [], [], None
    for index, mask in enumerate(frames):
        if not present[index]:
            if previous is not None:
                ends.append(index)
            previous = None
            continue
        if previous is None or (mask ^ previous).sum() > 0.5 * max(mask.sum(), previous.sum()):
            if previous is not None:
                ends.append(index)
            starts.append(index)
        previous = mask
    if previous is not None:
        ends.append(len(frames))
    runs = [(a, b) for a, b in zip(starts, ends) if b - a >= fps]
    if len(runs) != expected:
        raise SystemExit(f"{video}: found {len(runs)} captions on screen, expected {expected}")
    bounds = [a / fps for a, _ in runs] + [runs[-1][1] / fps]
    return [(bounds[i], bounds[i + 1]) for i in range(expected)]


def _wrap(text: str, width: int) -> str:
    import textwrap

    return "\n".join(textwrap.wrap(text, width=width))


def cut_shape(video: pathlib.Path, dest: pathlib.Path, shape: str, captions: list[str],
              intervals: list[tuple[float, float]], max_bytes: int = SHAPE_BYTES) -> None:
    """Write one shape of a recording as H.264 in an MP4 under ``max_bytes``, with its captions burned in.

    Each caption is drawn from its own start to the start of the next, so none is shorter than it was in the
    recording (``MIN_CAPTION_SECONDS`` is the floor the tests hold). The closing sentence is not drawn: the
    recording's closing card already carries it, so on every shape it appears once.
    """
    import tempfile

    if shape not in SHAPES:
        raise ValueError(f"unknown shape {shape!r}")
    width, height = SHAPES[shape]
    duration = ffprobe_duration_seconds(video)
    font = _font_file()
    spans = [(intervals[i][0], intervals[i + 1][0] if i + 1 < len(intervals) else intervals[i][1])
             for i in range(len(intervals))]
    drawn = list(zip(captions, spans))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shape-captions-") as work:
        chain = []
        if shape == "wide":
            source = "crop=1440:810:0:40,scale=%d:%d" % (width, height)
            size, wrap_at, top = 28, 70, height - 82
            chain.append(f"drawbox=x=0:y={top}:w={width}:h=82:color=black@0.78:t=fill")
            text_y = f"{top}+(82-text_h)/2"
        else:
            scaled_h = round(width * 810 / 1440 / 2) * 2
            video_y = 70 if shape == "square" else 360
            source = "crop=1440:810:0:40,scale=%d:%d" % (width, scaled_h)
            size, wrap_at = (44, 38) if shape == "square" else (56, 30)
            text_y = f"{video_y + scaled_h + 40}"
        for number, (text, (start, end)) in enumerate(drawn):
            text_file = pathlib.Path(work) / f"c{number}.txt"
            text_file.write_text(_wrap(text, wrap_at), encoding="utf-8")
            chain.append(
                f"drawtext=fontfile={font}:textfile={text_file}:fontsize={size}:fontcolor=white:line_spacing=10:"
                f"x=(w-text_w)/2:y={text_y}:enable='between(t,{start:.2f},{end:.2f})'")
        if shape == "wide":
            graph = f"[0:v]{source},{','.join(chain)}[out]"
        else:
            graph = (f"color=c={_SHAPE_BACKGROUND}:s={width}x{height}:d={duration:.2f}[bg];[0:v]{source}[v];"
                     f"[bg][v]overlay=0:{video_y}:shortest=1,{','.join(chain)}[out]")
        for crf in (28, 32, 36, 40):
            _ffmpeg("-i", str(video), "-filter_complex", graph, "-map", "[out]", "-an", "-c:v", "libx264",
                    "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest))
            if dest.stat().st_size <= max_bytes:
                return
    raise SystemExit(f"could not cut {shape} of {video} under {max_bytes} bytes")


def cover_image(poster: pathlib.Path, dest: pathlib.Path, size=(1200, 630)) -> None:
    """The link-preview cover: the recording's outcome frame (its poster) cropped above the caption bar."""
    from PIL import Image

    im = Image.open(poster).convert("RGB")
    scale = im.width / size[0]
    crop_h = min(round(size[1] * scale), CAPTION_BAR_TOP * im.width // 1440)
    cropped = im.crop((0, 0, im.width, crop_h)).resize(size, Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(dest, format="JPEG", quality=82, optimize=True)
