from datetime import UTC, datetime

import pytest
from helpers import FIXTURES, NOW, repo_config

from digest.config import Feed
from digest.normalize import (
    canonical_url,
    clean_summary,
    parse_feed,
    squeeze,
    strip_html,
    truncate,
    url_hash,
)


def feed_named(name: str) -> Feed:
    return next(feed for feed in repo_config().feeds if feed.name == name)


def parse_fixture(name: str, max_summary_chars: int = 600):
    feed = feed_named(name)
    return parse_feed(feed, (FIXTURES / feed.fixture).read_bytes(), NOW, max_summary_chars)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "http://Example.COM/Path/?utm_source=x&id=7&fbclid=abc#frag",
            "https://example.com/Path?id=7",
        ),
        ("https://example.com/", "https://example.com"),
        ("https://example.com/a/b/", "https://example.com/a/b"),
        ("https://example.com/a?b=2&a=1", "https://example.com/a?b=2&a=1"),
        ("  https://example.com/a  ", "https://example.com/a"),
        ("https://example.com/a?UTM_Campaign=x&gclid=1", "https://example.com/a"),
        ("https://arxiv.org/abs/2610.06947", "https://arxiv.org/abs/2610.06947"),
        ("http://example.com:80/a", "https://example.com/a"),
        ("https://example.com:8443/a", "https://example.com:8443/a"),
        # other parameters are kept exactly as the site wrote them
        (
            "https://example.com/a?q=a%20b+c&flag&utm_id=1&x=",
            "https://example.com/a?q=a%20b+c&flag&x=",
        ),
    ],
)
def test_canonical_url(raw, expected):
    assert canonical_url(raw) == expected


def test_two_spellings_of_one_page_hash_the_same():
    a = canonical_url("http://example.com/story/?utm_medium=rss")
    b = canonical_url("https://EXAMPLE.com/story")
    assert url_hash(a) == url_hash(b)
    assert len(url_hash(a)) == 64


def test_strip_html_drops_tags_and_scripts():
    text = "<p>Hello <b>world</b>.</p><script>alert(1)</script><p>Next &amp; last</p>"
    assert squeeze(strip_html(text)) == "Hello world. Next & last"


def test_truncate_cuts_at_a_word_and_marks_it():
    assert truncate("short", 10) == "short"
    cut = truncate("one two three four", 12)
    assert cut == "one two…"
    assert len(cut) <= 12


def test_plain_text_summary_keeps_math_and_loses_the_arxiv_prefix():
    feed = Feed(name="arXiv", url="https://example.org", plain_text=True)
    raw = "arXiv:2609.22190v2 Announce Type: replace \nAbstract: for $1<q<3/2$ both are finite"
    assert clean_summary(raw, feed, "", 600) == "for $1<q<3/2$ both are finite"


def test_arxiv_fixture():
    items = parse_fixture("arXiv quant-ph + optics")
    assert len(items) == 26
    assert len({item.id for item in items}) == 26
    assert {item.announce_type for item in items} == {"new", "cross", "replace", "replace-cross"}
    assert all(item.published == datetime(2026, 10, 7, 4, 0, tzinfo=UTC) for item in items)
    assert all(item.url.startswith("https://arxiv.org/abs/") for item in items)
    assert all(len(item.summary) <= 600 for item in items)
    assert not any(item.summary.startswith("arXiv:") for item in items)
    assert all(item.authors for item in items)


def test_arxiv_math_survives_parsing():
    # feedparser's own sanitizer would turn this into "$1<q><3/2$"
    items = parse_fixture("arXiv quant-ph + optics", max_summary_chars=5000)
    math = next(item for item in items if item.url.endswith("2609.22190"))
    assert "$1<q<3/2$" in math.summary


def test_aps_fixture():
    first = parse_fixture("APS Physics Magazine")[0]
    assert first.url == "https://aps.example.org/doi/10.0000/Example.19.101"  # http became https
    assert first.authors == "Ada Example"
    assert first.published == datetime(2026, 10, 7, 10, 0, tzinfo=UTC)  # from dc:date
    assert first.summary == (
        "A chip-scale resonator lets photons through one at a time, a signature of photon blockade."
    )


def test_physics_world_fixture():
    items = parse_fixture("Physics World")
    first = items[0]
    assert first.url == "https://pw.example.org/a/entangled-photon-source-fits-on-a-fingernail"
    assert (
        first.summary == "Integrated device emits pairs of entangled photons at telecom wavelengths"
    )
    injected = next(item for item in items if item.title.startswith("Ignore previous"))
    assert "alert(1)" not in injected.summary  # script content is dropped
    assert "appeared first" not in injected.summary


def test_quanta_fixture():
    first = parse_fixture("Quanta Magazine")[0]
    assert first.summary.endswith("physicists learned something about robustness.")
    second = parse_fixture("Quanta Magazine")[1]
    assert second.authors == "Hana Columnist and Ivo Coauthor"


def test_entries_without_title_or_web_link_are_dropped_and_missing_dates_mean_now():
    raw = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>Good</title><link>https://example.org/good</link></item>
    <item><title>Script link</title><link>javascript:alert(1)</link></item>
    <item><title></title><link>https://example.org/untitled</link></item>
    </channel></rss>"""
    items = parse_feed(Feed(name="Tiny", url="https://example.org/feed"), raw, NOW, 600)
    assert [item.title for item in items] == ["Good"]
    assert items[0].published == NOW
    assert items[0].source == "Tiny"


def test_bad_links_are_skipped_without_stopping_the_feed():
    raw = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>Broken host</title><link>http://[oops/story</link></item>
    <item><title>No host</title><link>http:javascript:alert(1)</link></item>
    <item><title>Fine</title><link>https://example.org/fine</link></item>
    </channel></rss>"""
    items = parse_feed(Feed(name="Tiny", url="https://example.org/feed"), raw, NOW, 600)
    assert [item.title for item in items] == ["Fine"]


def test_html_in_titles_is_stripped_except_for_plain_text_feeds():
    raw = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>Light &lt;i&gt;squeezed&lt;/i&gt; for $a&lt;b$</title>
    <link>https://example.org/1</link></item></channel></rss>"""
    news = parse_feed(Feed(name="News", url="https://example.org/feed"), raw, NOW, 600)
    assert news[0].title == "Light squeezed for $a"  # an HTML parser eats the "<b$"
    arxiv = Feed(name="arXiv", url="https://example.org/feed", plain_text=True)
    assert parse_feed(arxiv, raw, NOW, 600)[0].title == "Light <i>squeezed</i> for $a<b$"


def test_wordpress_footer_is_cut_only_at_the_end():
    feed = Feed(name="News", url="https://example.org/feed")
    footer = " The post A Title appeared first on Some Site."
    teaser = "The post office problem, first appeared on a blackboard in 1947, is solved."
    assert clean_summary(teaser + footer, feed, "", 600) == teaser
    plain = "The post office problem is finally solved."
    assert clean_summary(plain, feed, "", 600) == plain  # no footer: nothing is cut
    assert clean_summary(footer, feed, "", 600) == ""  # an entry that is only a footer
