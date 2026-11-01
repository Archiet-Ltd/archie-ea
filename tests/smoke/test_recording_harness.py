"""Recording in the browser journeys: off, fail and all, and the one helper that makes product clips.

A person who sees a journey fail needs the video of it, with its path in the failure output, and a passing
journey must leave nothing behind. A clip made for a customer must only ever show the demonstration
organisation. The harness runs the probe journeys in a child pytest so a deliberately failing journey does not
fail this file.
"""

import os
import pathlib
import subprocess
import sys

import pytest

from scripts.capture.clip_kit import ClipRefused, have_ffmpeg, record_clip

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROBE = "tests/smoke/recording_probe.py"


def _run_probe(tmp_path, mode, select):
    env = {k: v for k, v in os.environ.items() if k not in ("SMOKE_VIDEO", "SMOKE_VIDEO_DIR", "CI")}
    if mode:
        env["SMOKE_VIDEO"] = mode
    env["SMOKE_VIDEO_DIR"] = str(tmp_path / "videos")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", PROBE, "-k", select, "-q", "-p", "no:randomly", "-W", "ignore",
         "--timeout=300", "--rootdir", str(ROOT)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=900)
    videos = sorted(p.name for p in (tmp_path / "videos").glob("*.webm")) if (tmp_path / "videos").exists() else []
    return result, videos


def test_a_failing_journey_keeps_its_video_and_says_where(tmp_path):
    result, videos = _run_probe(tmp_path, "fail", "probe_pass or probe_fail")
    assert "1 failed, 1 passed" in result.stdout, result.stdout[-2000:]
    assert len(videos) == 1 and "probe_fail" in videos[0], videos
    kept = tmp_path / "videos" / videos[0]
    assert kept.stat().st_size > 0
    assert str(kept) in result.stdout, "the failure output must name the kept video"


def test_a_passing_journey_leaves_no_video_in_fail_mode(tmp_path):
    result, videos = _run_probe(tmp_path, "fail", "probe_pass")
    assert "1 passed" in result.stdout, result.stdout[-2000:]
    assert videos == []


def test_all_mode_keeps_every_video_and_draws_the_pointer(tmp_path):
    result, videos = _run_probe(tmp_path, "all", "probe_pass or probe_pointer")
    assert "2 passed" in result.stdout, result.stdout[-2000:]
    assert len(videos) == 2, videos


def test_off_mode_records_nothing_and_draws_no_pointer(tmp_path):
    result, videos = _run_probe(tmp_path, "off", "probe_pass or probe_no_pointer")
    assert "2 passed" in result.stdout, result.stdout[-2000:]
    assert videos == []


def test_a_clip_is_refused_for_anyone_but_the_demonstration_person(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_USER_PASSWORD", "unused")
    out = tmp_path / "clips"
    with pytest.raises(ClipRefused):
        record_clip(None, "http://127.0.0.1:1", "refused", lambda page: None, out_dir=str(out),
                    email="someone@another-company.example.com")
    monkeypatch.delenv("DEMO_USER_PASSWORD")
    with pytest.raises(ClipRefused):
        record_clip(None, "http://127.0.0.1:1", "refused", lambda page: None, out_dir=str(out))
    assert not list(out.glob("*")), "a refused clip must leave nothing behind"


@pytest.fixture(scope="module")
def demonstration_organisation(live_server):
    from app.commands.seed_demo_company import seed_demo_company

    os.environ["DEMO_USER_PASSWORD"] = "ClipDemo!2026-test"
    from app import create_app

    with create_app("testing").app_context():
        seed_demo_company()
    yield str(live_server)


@pytest.mark.parametrize("label,size,dark", [("1440-light", (1440, 900), False), ("390-dark", (390, 844), True)])
def test_a_clip_opens_the_applications_list_at_the_asked_size_and_theme(
        demonstration_organisation, browser, tmp_path, label, size, dark):
    seen = {}

    def steps(page):
        page.goto(demonstration_organisation + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.mouse.move(size[0] // 2, size[1] // 2)
        page.wait_for_timeout(1500)
        seen["viewport"] = page.viewport_size
        seen["dark"] = page.evaluate("window.matchMedia('(prefers-color-scheme: dark)').matches")
        seen["body"] = page.inner_text("body")
        seen["pointer"] = page.evaluate("!!document.getElementById('__cur')")

    out = os.environ.get("CLIP_SAMPLE_DIR") or str(tmp_path)
    clip = record_clip(browser, demonstration_organisation, "applications-" + label, steps, size=size, dark=dark,
                       out_dir=out)
    assert pathlib.Path(clip).stat().st_size > 0
    assert seen["viewport"] == {"width": size[0], "height": size[1]}
    assert seen["dark"] is dark
    assert "/account/login" not in seen["body"] and "Applications" in seen["body"]
    assert seen["pointer"]
    if have_ffmpeg():
        assert clip.endswith(".webm") and not clip.endswith(".raw.webm")
        assert (pathlib.Path(out) / ("applications-%s.poster.webp" % label)).stat().st_size > 0
    else:
        assert clip.endswith(".raw.webm")


def test_a_clip_shows_the_clock_and_each_caption_at_its_time(demonstration_organisation, browser, tmp_path):
    seen = []

    def steps(page):
        page.goto(demonstration_organisation + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for _ in range(60):
            seen.append(page.evaluate(
                """() => { const t = document.getElementById('__txt'), k = document.getElementById('__clk');
                           return [k ? k.textContent : null, t ? t.textContent : null]; }"""))
            if seen[-1][1] == "Then open one to see who owns it":
                break
            page.wait_for_timeout(500)

    out = os.environ.get("CLIP_SAMPLE_DIR") or str(tmp_path)
    record_clip(browser, demonstration_organisation, "applications-clock-captions", steps, clock=True,
                captions=[(0, "Every application, in one list"), (12, "Then open one to see who owns it")],
                out_dir=out)

    def seconds(clock):
        minutes, secs = clock.split(":")
        return int(minutes) * 60 + int(secs)

    first = [clock for clock, text in seen if text == "Every application, in one list"]
    second = [clock for clock, text in seen if text == "Then open one to see who owns it"]
    assert first and second, seen
    assert all(clock for clock, _ in seen), "the clock must show on every look"
    assert seconds(first[0]) < 12 <= seconds(second[0])
