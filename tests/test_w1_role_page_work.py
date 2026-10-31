"""W1 performance: a role page reads the pages it shows, not the whole content directory.

Before: GET /roles/<slug> read, rendered and HTML-sanitised all 215 content pages, twice, to build the "Other roles"
strip (5.0 s of a 5.2 s profile; p95 1.6 to 2.9 s in the sandbox run). The strip needs the role pages and six named
pages, so that is all it may read. The assertion counts page reads, which is what the time was spent on, and the
equivalence tests pin that the strip is unchanged.
"""
from app.services import public_pages as pp


def _role_slug():
    return sorted(p.stem for p in (pp.CONTENT_ROOT / pp.FAMILY_DIR_MAP["role"]).glob("*.md"))[0]


def _count_page_reads(monkeypatch):
    reads = []
    real = pp._load_page

    def counting(md_file, family, slug, url):
        reads.append(url)
        return real(md_file, family, slug, url)

    monkeypatch.setattr(pp, "_load_page", counting)
    return reads


def test_a_role_page_reads_only_the_role_pages_and_the_pages_its_strip_names(client, monkeypatch):
    reads = _count_page_reads(monkeypatch)
    slug = _role_slug()
    assert client.get("/roles/%s" % slug).status_code == 200

    role_pages = len(list((pp.CONTENT_ROOT / pp.FAMILY_DIR_MAP["role"]).glob("*.md")))
    named = sum(len(urls) for _label, urls in pp.ROLE_HUB_EXTRA_ENTRIES)
    everything = len(pp.load_all_pages())
    reads.clear()
    assert client.get("/roles/%s" % slug).status_code == 200
    assert len(reads) <= role_pages + named + 1, "%d pages read; the strip needs %d" % (len(reads), role_pages + named)
    assert len(reads) < everything / 4, "%d of %d pages read" % (len(reads), everything)


def test_the_other_roles_strip_is_the_same_as_when_every_page_was_read():
    page = pp.load_page("role", slug=_role_slug())

    def every_page_way(entries):
        by_url = {p.url: p for p in pp.load_feed_pages()}
        return [{"label": label, "pages": [by_url[u] for u in urls if u in by_url]}
                for label, urls in entries if any(u in by_url for u in urls)]

    got = pp.hub_extra_entries(pp.ROLE_HUB_EXTRA_ENTRIES)
    want = every_page_way(pp.ROLE_HUB_EXTRA_ENTRIES)
    assert [(g["label"], [p.url for p in g["pages"]]) for g in got] == \
        [(g["label"], [p.url for p in g["pages"]]) for g in want]

    others = [p.url for p in sorted((p for p in pp.load_feed_pages() if p.family == "role" and p.url != page.url),
                                    key=pp._title_key)]
    strip = pp.role_siblings(page)
    assert [p.url for p in strip[0]["pages"]] == others and strip[0]["label"] == ""
    assert page.url not in [p.url for g in strip for p in g["pages"]]


def test_the_connector_hub_extras_are_the_same_as_when_every_page_was_read():
    got = pp.hub_extra_entries(pp.CONNECTOR_HUB_EXTRA_ENTRIES)
    by_url = {p.url: p for p in pp.load_feed_pages()}
    want = [(label, [u for u in urls if u in by_url]) for label, urls in pp.CONNECTOR_HUB_EXTRA_ENTRIES
            if any(u in by_url for u in urls)]
    assert [(g["label"], [p.url for p in g["pages"]]) for g in got] == want and got


def test_a_url_with_no_page_is_left_out_and_nothing_else_is():
    got = pp.hub_extra_entries((("Real and missing", ("/modules/arb", "/modules/no-such-page-at-all")),
                                ("Only missing", ("/use-cases/no-such-page-either",))))
    assert [(g["label"], [p.url for p in g["pages"]]) for g in got] == [("Real and missing", ["/modules/arb"])]
