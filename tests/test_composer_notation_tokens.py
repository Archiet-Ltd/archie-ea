"""The Composer draws ArchiMate notation from one token set and one icon sprite.

Layer colours are defined once in the token file for light and dark; the 60 element types each
have a notation icon in one sprite; the engine reads the tokens (and writes no colour) when the
composer turns notation mode on, and every other caller keeps its colours.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOKENS = (ROOT / "app/static/css/shadcn_tokens.css").read_text(encoding="utf-8")
SPRITE = (ROOT / "app/static/img/archimate-notation.svg").read_text(encoding="utf-8")
RENDERER = (ROOT / "app/static/js/archimate/composer_renderer.js").read_text(encoding="utf-8")
SHELL = (ROOT / "app/static/js/archimate/composer_shell.js").read_text(encoding="utf-8")

LAYERS = ["strategy", "business", "application", "technology", "physical", "motivation",
          "implementation", "migration", "location"]


def _block(selector):
    start = TOKENS.index(selector)
    return TOKENS[start:TOKENS.index("\n  }\n", start)]


def test_every_layer_has_a_fill_and_a_stroke_in_light_and_dark():
    light, dark = _block("  :root {\n    --background"), _block("  .dark {\n    --background")
    for layer in LAYERS:
        for block in (light, dark):
            assert re.search(r"--notation-%s:\s*\d+ \d+%% \d+%%;" % layer, block), layer
            assert re.search(r"--notation-%s-stroke:\s*\d+ \d+%% \d+%%;" % layer, block), layer


def test_the_standard_hues_are_the_ones_taught():
    light = _block("  :root {\n    --background")

    def hue(name):
        return int(re.search(r"--notation-%s:\s*(\d+) " % name, light).group(1))
    assert 50 <= hue("business") <= 65          # yellow
    assert 170 <= hue("application") <= 200     # cyan-blue
    assert 90 <= hue("technology") <= 140       # green
    assert 230 <= hue("motivation") <= 270      # lilac


def test_sprite_has_one_symbol_for_each_of_the_sixty_palette_types():
    ids = re.findall(r'<symbol id="am-(\w+)"', SPRITE)
    groups = SHELL[SHELL.index("let GROUPS"):SHELL.index("let RELATION_LABELS")]
    palette_types = [t for lst in re.findall(r"types:\s*\[(.*?)\]", groups, re.S) for t in re.findall(r"'(\w+)'", lst)]
    assert len(ids) == 60 and len(set(ids)) == 60
    assert sorted(ids) == sorted(palette_types)


def test_engine_reads_tokens_in_notation_mode_and_stays_opt_in():
    assert "function useNotation" in RENDERER and "useNotation: useNotation" in RENDERER
    assert "NOTATION_ON = false" in RENDERER          # off unless the composer asks
    assert "'--notation-' + key" in RENDERER
    assert "ComposerRenderer.useNotation(true)" in (ROOT / "app/static/js/archimate/composer.js").read_text(encoding="utf-8")
    # The notation helpers hold no colour literal.
    helpers = RENDERER[RENDERER.index("let NOTATION_ON"):RENDERER.index("function layerColor")]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", helpers)
