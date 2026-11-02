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

from scripts.capture import clip_kit
from scripts.capture.clip_kit import ClipRefused, have_ffmpeg, record_clip

from . import conftest as smoke_conftest
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


def test_a_context_made_by_a_module_fixture_leaves_no_orphan_video_in_all_mode(tmp_path):
    result, videos = _run_probe(tmp_path, "all", "probe_shared_one or probe_shared_two")
    assert "2 passed" in result.stdout, result.stdout[-2000:]
    assert videos and all("probe" in name and not name.startswith("page@") for name in videos), videos


def test_a_failing_journey_keeps_the_video_of_its_module_fixture_context(tmp_path):
    result, videos = _run_probe(tmp_path, "fail", "probe_shared_one or probe_shared_fail")
    assert "1 failed, 1 passed" in result.stdout, result.stdout[-2000:]
    assert len(videos) == 1 and "recording_probe.py.shared" in videos[0], videos
    assert (tmp_path / "videos" / videos[0]).stat().st_size > 0


def test_a_skipped_or_expected_failure_journey_keeps_no_video_and_is_not_called_a_failure(tmp_path):
    result, videos = _run_probe(tmp_path, "all", "probe_skipped or probe_xfailed")
    assert "1 skipped" in result.stdout and "1 xfailed" in result.stdout, result.stdout[-2000:]
    assert videos == []
    assert "video of this failed journey" not in result.stdout


class _FakeContext:
    def __init__(self):
        self.pages = []

    def add_init_script(self, script):
        pass

    def on(self, name, handler):
        pass


class _FakeBrowser:
    def __init__(self):
        self.seen = []

    def new_context(self, *args, **kwargs):
        self.seen.append(kwargs)
        return _FakeContext()


@pytest.mark.parametrize("asked,expected", [
    ({"viewport": {"width": 390, "height": 844}}, {"width": 390, "height": 844}),
    ({"viewport": {"width": 1440, "height": 900}}, {"width": 1440, "height": 900}),
    ({}, {"width": 1280, "height": 720}),
])
def test_kept_videos_are_as_large_as_the_page_they_show(monkeypatch, tmp_path, asked, expected):
    monkeypatch.setenv("SMOKE_VIDEO", "all")
    monkeypatch.setenv("SMOKE_VIDEO_DIR", str(tmp_path))
    fake = _FakeBrowser()
    smoke_conftest._record_contexts(fake)
    try:
        fake.new_context(**asked)
    finally:
        smoke_conftest._RECORDING["contexts"] = []
        smoke_conftest._RECORDING["shared"] = []
    assert fake.seen[0]["record_video_size"] == expected


class _TimedOut(Exception):
    pass


class _SignInPage:
    """Stands in for the sign-in page of a person whose two-factor code is refused."""

    url = "http://127.0.0.1:1/account/mfa-challenge"

    def __init__(self):
        self.waits = []
        self.typed = []

    def goto(self, *args, **kwargs):
        pass

    def fill(self, selector, value):
        self.typed.append(selector)

    def locator(self, selector):
        return self

    def click(self):
        pass

    def press(self, key):
        pass

    def wait_for_url(self, matcher, wait_until=None, timeout=None):
        self.waits.append(timeout)
        if timeout is not None and timeout <= 15000 and len(self.waits) > 1:
            raise _TimedOut("no navigation")


def test_a_wrong_two_factor_code_is_refused_within_fifteen_seconds(monkeypatch):
    monkeypatch.setenv("DEMO_MFA_SECRET", "JBSWY3DPEHPK3PXP")
    monkeypatch.delenv("DEMO_USER_TOTP_SECRET", raising=False)
    monkeypatch.setattr(clip_kit, "PlaywrightTimeout", _TimedOut)
    page = _SignInPage()
    with pytest.raises(ClipRefused, match="sign-in failed"):
        clip_kit._sign_in(page, "http://127.0.0.1:1", clip_kit.DEMO_USER_EMAIL, "unused")
    assert "#mfa-code" in page.typed, "the secret named DEMO_MFA_SECRET must be the one used"
    assert page.waits[-1] <= 15000, page.waits


def test_the_kit_knows_each_demonstration_setting_by_one_name():
    from app.services.public_pages import DEMO_PERSONA

    assert clip_kit.DEMO_USER_EMAIL is DEMO_PERSONA or clip_kit.DEMO_USER_EMAIL == DEMO_PERSONA
    assert clip_kit.DEMO_TOTP_VARIABLE == "DEMO_MFA_SECRET"
    assert not hasattr(clip_kit, "DEMO_ORG_NAME")
    source = (ROOT / "scripts" / "capture" / "clip_kit.py").read_text()
    demo_source = (ROOT / "scripts" / "demo" / "record_demo.py").read_text()
    assert "DEMO_USER_TOTP_SECRET" not in source and "DEMO_PASS\"" not in demo_source
    assert 'lantern-quay.example.com"' not in source


@pytest.fixture(scope="module")
def demonstration_organisation(live_server):
    from app.commands.seed_demo_company import seed_demo_company

    os.environ["DEMO_USER_PASSWORD"] = "ClipDemo!2026-test"
    from app import create_app

    with create_app("testing").app_context():
        seed_demo_company()
    yield str(live_server)


def _refusal_seconds(browser, base_url, tmp_path):
    import time

    started = time.monotonic()
    with pytest.raises(ClipRefused, match="sign-in failed"):
        record_clip(browser, base_url, "refused", lambda page: None, out_dir=str(tmp_path))
    return time.monotonic() - started


def test_a_wrong_password_is_refused_within_fifteen_seconds(demonstration_organisation, browser, tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_USER_PASSWORD", "not-the-password-" + "x" * 8)
    seconds = _refusal_seconds(browser, demonstration_organisation, tmp_path)
    assert seconds < 15, seconds


@pytest.fixture
def two_factor_demonstration_person(request, ai_protocol_stub, app, demonstration_organisation):
    """A server that, unlike the smoke one, does not skip the administrator's second step."""
    from app.extensions import db
    from app.models.user import User

    with app.app_context():
        user = User.query.filter_by(email=clip_kit.DEMO_USER_EMAIL).one()
        before = (user.mfa_enabled, user.mfa_secret, user.is_platform_admin)
        user.mfa_enabled, user.mfa_secret, user.is_platform_admin = True, "JBSWY3DPEHPK3PXP", True
        db.session.commit()
    server = smoke_conftest.boot_live_server(request, ai_protocol_stub, app, extra_env={"FLASK_CONFIG": "testing"})
    try:
        yield str(server)
    finally:
        with app.app_context():
            user = User.query.filter_by(email=clip_kit.DEMO_USER_EMAIL).one()
            user.mfa_enabled, user.mfa_secret, user.is_platform_admin = before
            db.session.commit()


def test_a_wrong_code_for_a_two_factor_person_is_refused_in_a_real_browser_within_fifteen_seconds(
        two_factor_demonstration_person, browser, tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MFA_SECRET", "KRSXG5CTMVRXEZLU")
    seconds = _refusal_seconds(browser, two_factor_demonstration_person, tmp_path)
    assert seconds < 15, seconds


@pytest.mark.parametrize("label,size,dark", [("1440-light", (1440, 900), False), ("390-dark", (390, 844), True),
                                        ("1440-dark", (1440, 900), True), ("390-light", (390, 844), False)])
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
                       out_dir=out, subject="[data-testid^=\"app-owner\"]:visible")
    assert pathlib.Path(clip).stat().st_size > 0
    assert seen["viewport"] == {"width": size[0], "height": size[1]}
    assert seen["dark"] is dark
    assert "/account/login" not in seen["body"] and "Applications" in seen["body"]
    assert seen["pointer"]
    if have_ffmpeg():
        assert clip.endswith(".webm") and not clip.endswith(".raw.webm")
        poster = pathlib.Path(out) / ("applications-%s.poster.webp" % label)
        assert poster.stat().st_size > 0
        from PIL import Image, ImageStat

        spread = max(ImageStat.Stat(Image.open(poster).convert("L")).stddev)
        assert spread > 8, "the poster is a single colour (spread %.1f)" % spread
        assert clip.poster_at >= clip.subject_seen_at > 0, (clip.poster_at, clip.subject_seen_at)
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


def test_every_browser_journey_job_uploads_the_kept_videos():
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    steps = [block for block in text.split("- uses: actions/upload-artifact@v6")[1:] if "tests/smoke/_shots/" in block]
    steps += [block for block in text.split("- name: Upload compatibility evidence")[1:] if "tests/smoke/_shots/" in block]
    assert len(steps) >= 4, "expected the browser journey jobs' evidence uploads"
    for block in steps:
        head = block.split("retention-days")[0]
        assert "test-results/videos/" in head, head
