"""What earlier releases loaded for a catalogue, kept as fingerprints beside the catalogue itself.

A catalogue row an organisation holds is corrected to the current catalogue only while it still reads
exactly as one of these earlier loads, which is how an edited row is told from an untouched one. Each
entry is the first 16 hex digits of the SHA-256 of the row's title, heading, reference and description
(joined by the unit separator, a missing reference counting as empty), keyed by control code and
listed oldest load first. A later change to a catalogue adds its previous load here.
"""

import hashlib
import re

EARLIER_SEEDS = {'DORA': [{'DORA-Art.5': 'cb64874529df0b75',
             'DORA-Art.6': '1499a257751fe791',
             'DORA-Art.7': '8908aa5612835ca0',
             'DORA-Art.8': 'b12ff8897193fb0f',
             'DORA-Art.9': '0122656203e73c85',
             'DORA-Art.10': 'bd638e47ed83d5ad',
             'DORA-Art.11': '8e240fa541a0f0e3',
             'DORA-Art.12': 'c897fcb0462a7f56',
             'DORA-Art.13': 'cef38fabd3f37fcf',
             'DORA-Art.15': '87234e4fb83ac6e3',
             'DORA-Art.17': 'ecdf820a99012027',
             'DORA-Art.19': '4da281801735d398',
             'DORA-Art.24': '65057be08d3afdcf',
             'DORA-Art.26': '645f35e01e11d01c',
             'DORA-Art.28': 'f877feeb08074dd8'},
            {'DORA-Art.5': 'ab0717e05764e4c8',
             'DORA-Art.6': 'e55912cb9843596c',
             'DORA-Art.7': 'fff454a5223772bd',
             'DORA-Art.8': '8e22daa0f3e5fc7c',
             'DORA-Art.9': '28e62093151f78f6',
             'DORA-Art.10': 'd845db4578add63d',
             'DORA-Art.11': 'c6e4a5f96209846c',
             'DORA-Art.12': 'd1cc4e75db2c68e7',
             'DORA-Art.13': 'ae5d8c00f3ed1f7a',
             'DORA-Art.14': 'c62be8326bbad90d',
             'DORA-Art.15': 'b020a371347da714',
             'DORA-Art.16': '673b7deaec04f511',
             'DORA-Art.17': '898b952c73f50722',
             'DORA-Art.18': '36eed808023c78a4',
             'DORA-Art.19': '8a33bc96c3e0f752',
             'DORA-Art.20': '2523e3ac1895fb8e',
             'DORA-Art.21': 'f19a5a70e5dd1c7f',
             'DORA-Art.22': 'ecdd899706319f60',
             'DORA-Art.23': '2a12966dcc5dcec6',
             'DORA-Art.24': '0f4d801cef2659e2',
             'DORA-Art.25': '614ea75ad248fc31',
             'DORA-Art.26': '4cc8ce471fc0f93e',
             'DORA-Art.27': '755e68119e881207',
             'DORA-Art.28': 'c72780308e81e1e5',
             'DORA-Art.29': '19c63b8fddc9d984',
             'DORA-Art.30': '48dc61a2f2fd15e0',
             'DORA-Art.31': '022259da9e74d30b',
             'DORA-Art.32': 'f88d1686e4bf2422',
             'DORA-Art.33': 'e83fd55fa36d7a6e',
             'DORA-Art.34': '50aea1afa4e7ef7a',
             'DORA-Art.35': 'f004919e1ec01a76',
             'DORA-Art.36': '53316dd6d6ada224',
             'DORA-Art.37': 'abd21100e367f603',
             'DORA-Art.38': '6d1977360be01645',
             'DORA-Art.39': '6eaf62836cd2c096',
             'DORA-Art.40': '60f56de77b5d4add',
             'DORA-Art.41': '1a0f590d05cacb91',
             'DORA-Art.42': 'c97a1895f18a9971',
             'DORA-Art.43': 'bcf43728a172ddca',
             'DORA-Art.44': '1f3c3b14b7554e8e',
             'DORA-Art.45': 'd383606360bb0244'}],
   'NIS2': [{'NIS2-Art.21(2)(a)': '056fbe3ff1e64945',
             'NIS2-Art.21(2)(b)': 'ad705b7af3ce6d93',
             'NIS2-Art.21(2)(c)': '158b06690805e3e3',
             'NIS2-Art.21(2)(d)': '13928eced9c7c2bb',
             'NIS2-Art.21(2)(e)': '1802080bc47e155d',
             'NIS2-Art.21(2)(f)': '56810b8d3f85fc26',
             'NIS2-Art.21(2)(g)': '2af1068f92bc042d',
             'NIS2-Art.21(2)(h)': 'b38d4994e2fa8e15',
             'NIS2-Art.21(2)(i)': '2b2adb0370189704',
             'NIS2-Art.21(2)(j)': 'aea092c5dc64b8ba',
             'NIS2-Art.23(4)(a)': 'd9b354245133bb1d',
             'NIS2-Art.23(4)(b)': '867ad49502006682',
             'NIS2-Art.23(4)(d)': '81fa71d7d8a7efae'}]}

def fingerprint(title, category, reference, description) -> str:
    """The fingerprint of one catalogue row as held in the database."""
    text = "\x1f".join([title or "", category or "", reference or "", description or ""])
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def matches_an_earlier_load(framework_code: str, control_code: str, title, category, reference, description) -> bool:
    """True when the row still has the values an earlier release loaded for it."""
    wanted = fingerprint(title, category, reference, description)
    return any(load.get(control_code) == wanted for load in EARLIER_SEEDS.get(framework_code, ()))


_ARTICLE = re.compile(r"Art\.(\d+)(.*)$")


def article_order(control_code: str, row_id: int = 0):
    """Sort key giving a catalogue its article order (DORA-Art.9 before DORA-Art.10, NIS2-Art.21(2)(a) before
    NIS2-Art.23(4)(a)); codes that are not articles keep the order they were loaded in."""
    found = _ARTICLE.search(control_code or "")
    if found is None:
        return (0, "", row_id)
    return (int(found.group(1)), found.group(2), row_id)
