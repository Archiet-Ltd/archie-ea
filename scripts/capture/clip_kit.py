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
import subprocess
import time

MAX_IMAGE_BYTES = 200 * 1024
MAX_VIDEO_BYTES = 3 * 1024 * 1024
CLIP_MAX_BYTES = 8 * 1024 * 1024

# The demonstration organisation's signed-in person. ``record_clip`` refuses any other.
DEMO_ORG_NAME = "Lantern Quay Systems"
DEMO_USER_EMAIL = "demo@lantern-quay.example.com"
DEMO_PASSWORD_VARIABLE = "DEMO_USER_PASSWORD"
DEMO_TOTP_VARIABLE = "DEMO_USER_TOTP_SECRET"

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
                     width: int = 1280, max_bytes: int = MAX_VIDEO_BYTES) -> None:
    """Transcode Playwright's raw (VP8) capture to a size-capped VP9 WebM.

    No audio track (Playwright's recording never has one; -an is explicit
    rather than assumed). Tightens crf upward until the cap is met -- product
    UI footage with little motion compresses hard, so a wide crf range rarely
    takes more than one or two passes in practice.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    for crf in (32, 36, 40, 44, 48, 52):
        _ffmpeg(
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
    page.wait_for_url(lambda url: "/account/login" not in url, wait_until="commit", timeout=90000)
    if "/account/mfa-challenge" in page.url:
        secret = os.environ.get(DEMO_TOTP_VARIABLE)
        if not secret:
            raise ClipRefused(f"the demonstration person has two-factor sign-in on; set {DEMO_TOTP_VARIABLE}")
        import pyotp

        page.fill("#mfa-code", pyotp.TOTP(secret).now())
        page.locator("#mfa-code").press("Enter")
        page.wait_for_url(lambda url: "/account/" not in url, wait_until="commit", timeout=90000)
    if "/account/login" in page.url:
        raise ClipRefused("could not sign in as the demonstration person")


def record_clip(browser, base_url, name, steps, *, size=(1440, 900), dark=False, clock=False,
                captions=None, out_dir="test-results/clips", email=None):
    """Record ``steps(page)`` as signed in to the demonstration organisation; return the clip path.

    Signing in happens in a separate, unrecorded context so the clip never shows the form, and the person's
    password and two-factor secret (read from the environment) never reach a caption, file name or log line.
    ``captions`` is a list of (seconds, text) shown as a lower-third from that many seconds into the clip.
    Writes ``<name>.webm`` and ``<name>.poster.webp`` in ``out_dir``; without ffmpeg the raw recording is kept
    as ``<name>.raw.webm`` and a line says so.
    """
    email = email or os.environ.get("DEMO_USER_EMAIL") or DEMO_USER_EMAIL
    if email.strip().lower() != DEMO_USER_EMAIL:
        raise ClipRefused(f"clips are made only as the demonstration organisation's person; refused {email!r}")
    password = os.environ.get(DEMO_PASSWORD_VARIABLE)
    if not password:
        raise ClipRefused(f"{DEMO_PASSWORD_VARIABLE} is not set")
    width, height = size
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = slug(name)
    scheme = "dark" if dark else "light"
    base_url = base_url.rstrip("/")

    login_ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    try:
        login_page = login_ctx.new_page()
        _sign_in(login_page, base_url, email, password)
        state = login_ctx.storage_state()
    finally:
        login_ctx.close()

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
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.goto(base_url + "/", wait_until="domcontentloaded")
        if "/account/login" in page.url:
            raise ClipRefused("the recording context is not signed in as the demonstration organisation")
        steps(page)
        video = page.video
        ctx.close()
        raw = pathlib.Path(video.path())
    except BaseException:
        ctx.close()
        raise

    clip = out / f"{stem}.webm"
    if not have_ffmpeg():
        kept = out / f"{stem}.raw.webm"
        raw.replace(kept)
        print(f"ffmpeg is not installed: kept the raw recording at {kept}, not encoded and without a poster")
        raw_dir.rmdir()
        return str(kept)
    encode_recording(raw, clip, width=width, max_bytes=CLIP_MAX_BYTES)
    extract_poster(clip, out / f"{stem}.poster.webp", at_seconds=min(1.0, ffprobe_duration_seconds(clip) / 2),
                   target_width=width)
    raw.unlink()
    raw_dir.rmdir()
    return str(clip)
