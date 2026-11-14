"""Every module recording has a share page, a link-preview card, three shapes and one manifest entry.

The files are cut from the recordings by ``scripts/capture/share_assets.py``; ``manifest.json`` is what
a distribution tool reads from the public site. This file holds the manifest to what it promises: every field present
for every recording, every file there with the size and length it states, every caption on screen long enough
to read, every shape cut from the recording the module page serves, and the share page's preview tags pointing
at files that answer.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess

import pytest

from tests._published_text_rules import BUILD_PROCESS_WORDS, DATES, PLACEHOLDER_PERSON_NAME
from app.services.origin import canonical_origin
from app.services.public_pages import (
    IMG_MODULES_DIR,
    MODULE_RECORDINGS,
    MODULE_RECORDINGS_MANIFEST,
    SHARE_DIR,
    SHARE_MANIFEST,
    STATIC_ROOT,
    load_share_manifest,
)

FIELDS = {"slug", "title", "message", "summary", "captions", "caption_seconds", "transcript", "claims", "cover",
          "files", "share_url", "module_url", "alt", "source_sha256"}
SHAPES = {"wide": (1280, 720), "square": (1080, 1080), "upright": (1080, 1920)}
MAX_BYTES = 2 * 1024 * 1024


def _entries():
    return load_share_manifest()


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,codec_name:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_manifest_exists_and_names_every_recording():
    assert (SHARE_DIR / SHARE_MANIFEST).is_file(), "the share manifest has not been generated"
    assert {e["slug"] for e in _entries()} == set(MODULE_RECORDINGS)
    assert json.loads((SHARE_DIR / SHARE_MANIFEST).read_text(encoding="utf-8"))["site"] == canonical_origin()


@pytest.mark.parametrize("slug", sorted(MODULE_RECORDINGS))
def test_an_entry_holds_every_field(slug):
    entry = next(e for e in _entries() if e["slug"] == slug)
    assert FIELDS <= set(entry), "missing: %s" % sorted(FIELDS - set(entry))
    for text_field in ("title", "message", "summary", "transcript", "alt"):
        assert entry[text_field].strip(), "%s is empty" % text_field
    assert entry["claims"] and all(c.strip() for c in entry["claims"])
    assert len(entry["captions"]) == len(entry["caption_seconds"])
    assert entry["share_url"] == "%s/see/%s" % (canonical_origin(), slug)
    assert entry["module_url"] == "%s/modules/%s" % (canonical_origin(), slug)
    assert set(entry["files"]) == set(SHAPES)
    recorded = MODULE_RECORDINGS[slug]
    assert entry["title"] == recorded["title"] and entry["message"] == recorded["promise"]
    assert entry["captions"][0] == entry["title"]
    # nothing a reader should not meet (a date, a wave, how the product was built, a placeholder-looking person) in anything that is published
    everything = json.dumps({k: entry[k] for k in ("title", "message", "summary", "captions", "transcript", "claims", "alt")})
    assert not DATES.search(everything), DATES.search(everything).group(0)
    assert not BUILD_PROCESS_WORDS.search(everything), BUILD_PROCESS_WORDS.search(everything).group(0)
    assert not PLACEHOLDER_PERSON_NAME.search(everything), PLACEHOLDER_PERSON_NAME.search(everything).group(0)
    # every claim is something the recording says: its title, a caption, or its closing promise
    spoken = {c.rstrip(".") for c in entry["claims"]}
    assert spoken == set(entry["captions"]) | {recorded["promise"].rstrip(".")}, "a claim the recording does not make"


@pytest.mark.parametrize("slug", sorted(MODULE_RECORDINGS))
def test_every_file_exists_with_the_size_and_length_the_entry_states(slug):
    entry = next(e for e in _entries() if e["slug"] == slug)
    cover = STATIC_ROOT / entry["cover"].removeprefix("/static/")
    assert cover.is_file() and cover.suffix == ".jpg"
    from PIL import Image

    with Image.open(cover) as im:
        assert im.size == (1200, 630), "the preview card is 1200 by 630"
    for shape, (width, height) in SHAPES.items():
        meta = entry["files"][shape]
        path = STATIC_ROOT / meta["path"].removeprefix("/static/")
        assert path.is_file(), "%s: %s is missing" % (slug, meta["path"])
        assert path.stat().st_size == meta["bytes"], "%s %s: the entry states %d bytes, the file is %d" % (
            slug, shape, meta["bytes"], path.stat().st_size)
        assert meta["bytes"] <= MAX_BYTES, "%s %s: over 2 MB" % (slug, shape)
        probed = _probe(path)
        stream = probed["streams"][0]
        assert (stream["width"], stream["height"]) == (width, height) == (meta["width"], meta["height"])
        assert stream["codec_name"] == "h264"
        assert abs(float(probed["format"]["duration"]) - meta["duration_seconds"]) <= 0.1
        assert 30 <= meta["duration_seconds"] <= 90, "%s %s: %s seconds" % (slug, shape, meta["duration_seconds"])


@pytest.mark.parametrize("slug", sorted(MODULE_RECORDINGS))
def test_each_caption_stays_on_screen_for_three_seconds(slug):
    entry = next(e for e in _entries() if e["slug"] == slug)
    for number, (start, end) in enumerate(entry["caption_seconds"]):
        assert end - start >= 3.0, "%s: caption %d is on screen for %.1f seconds" % (slug, number + 1, end - start)


@pytest.mark.parametrize("slug", sorted(MODULE_RECORDINGS))
def test_the_shapes_were_cut_from_the_recording_the_page_serves(slug):
    entry = next(e for e in _entries() if e["slug"] == slug)
    recorded = json.loads((IMG_MODULES_DIR / MODULE_RECORDINGS_MANIFEST).read_text(encoding="utf-8"))[slug]
    video = IMG_MODULES_DIR / ("%s.webm" % slug)
    assert entry["source_sha256"] == recorded["clip_sha256"] == hashlib.sha256(video.read_bytes()).hexdigest(), \
        "%s: cut from an older recording; run python -m scripts.capture.share_assets" % slug
    assert entry["captions"] == recorded["captions"]


@pytest.mark.parametrize("shape", ["square", "upright"])
@pytest.mark.parametrize("slug", sorted(MODULE_RECORDINGS))
def test_on_the_closing_card_no_sentence_is_written_a_second_time_under_the_picture(slug, shape, tmp_path):
    """The closing card shows the closing sentence inside the picture; the space below it, which carries the
    captions earlier, must be empty then, so the sentence is read once."""
    from PIL import Image

    entry = next(e for e in _entries() if e["slug"] == slug)
    meta = entry["files"][shape]
    frame = tmp_path / "closing.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.2f" % (meta["duration_seconds"] - 1.0),
                    "-i", str(STATIC_ROOT / meta["path"].removeprefix("/static/")), "-frames:v", "1", str(frame)],
                   check=True)
    with Image.open(frame).convert("L") as im:
        width, height = im.size
        picture_bottom = {"square": 70, "upright": 360}[shape] + round(width * 810 / 1440 / 2) * 2
        below = im.crop((0, picture_bottom + 10, width, height))
        low, high = below.getextrema()
    assert high - low <= 24, "%s %s: text is drawn under the closing card (brightness range %d)" % (slug, shape, high - low)
