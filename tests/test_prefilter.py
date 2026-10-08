from datetime import timedelta

from helpers import NOW, make_item

from digest.config import Feed
from digest.prefilter import (
    cap,
    keep_announce_types,
    keyword_filter,
    mark_watched,
    within_lookback,
)


def numbered(count: int, source: str = "Feed A", **fields) -> list:
    return [
        make_item(
            f"{source} item {n}", f"https://example.org/{source}/{n}", source=source, **fields
        )
        for n in range(count)
    ]


def test_lookback_window():
    inside = make_item("in", "https://example.org/1", published=NOW - timedelta(hours=36))
    outside = make_item(
        "out", "https://example.org/2", published=NOW - timedelta(hours=36, seconds=1)
    )
    assert within_lookback([inside, outside], NOW, 36) == [inside]


def test_announce_types_only_apply_to_feeds_that_list_them():
    feeds = [
        Feed(name="arXiv", url="https://example.org/a", announce_types=["new", "cross"]),
        Feed(name="News", url="https://example.org/n"),
    ]
    new = make_item("new", "https://example.org/1", source="arXiv", announce_type="new")
    replaced = make_item("old", "https://example.org/2", source="arXiv", announce_type="replace")
    news = make_item("news", "https://example.org/3", source="News")
    assert keep_announce_types([new, replaced, news], feeds) == [new, news]


def test_mark_watched_matches_names_inside_the_author_string():
    solo = make_item("a", "https://example.org/1", authors="Jane Q. Doe")
    group = make_item("b", "https://example.org/2", authors="A. Other (Univ, City), JANE Q. DOE")
    other = make_item("c", "https://example.org/3", authors="John Smith")
    mark_watched([solo, group, other], ["jane q. doe", "  "])
    assert [item.watched for item in (solo, group, other)] == [True, True, False]


def test_keyword_filter():
    optics = make_item("Photon blockade in rings", "https://example.org/1")
    finance = make_item("Momentum crashes", "https://example.org/2", summary="factor investing")
    crypto = make_item("Photon coin", "https://example.org/3", summary="a cryptocurrency")
    star = make_item("Unrelated", "https://example.org/4", watched=True)
    items = [optics, finance, crypto, star]

    assert keyword_filter(items, [], []) == items
    assert keyword_filter(items, ["PHOTON"], []) == [optics, crypto, star]
    assert keyword_filter(items, ["photon", "factor"], ["crypto"]) == [optics, finance, star]


def test_cap_applies_each_feeds_quota():
    feeds = [
        Feed(name="Big", url="https://example.org/big", quota=3),
        Feed(name="Small", url="https://example.org/small", quota=5),
    ]
    items = numbered(10, "Big") + numbered(2, "Small")
    candidates, left_out = cap(items, feeds, [], 150)
    assert sum(item.source == "Big" for item in candidates) == 3
    assert sum(item.source == "Small" for item in candidates) == 2  # not crowded out
    assert left_out == 7


def test_cap_prefers_priority_keywords_then_feed_order():
    feeds = [Feed(name="Feed A", url="https://example.org/a", quota=2)]
    items = numbered(4)  # identical timestamps, like arXiv
    items[3].title = "Photon blockade with antibunching"
    candidates, _ = cap(items, feeds, ["photon blockade", "antibunching"], 150)
    assert [item.title for item in candidates] == [
        "Photon blockade with antibunching",
        "Feed A item 0",
    ]


def test_cap_prefers_newer_items_when_timestamps_differ():
    feeds = [Feed(name="Feed A", url="https://example.org/a", quota=1)]
    old = make_item("old", "https://example.org/1", published=NOW - timedelta(hours=5))
    new = make_item("new", "https://example.org/2", published=NOW)
    candidates, _ = cap([old, new], feeds, [], 150)
    assert candidates == [new]


def test_watched_authors_are_exempt_from_the_quota():
    feeds = [Feed(name="Feed A", url="https://example.org/a", quota=1)]
    items = numbered(3)
    items[1].watched = True
    items[2].watched = True
    candidates, _ = cap(items, feeds, [], 150)
    assert candidates == [items[1], items[2]]


def test_global_limit_keeps_watched_first_then_newest():
    feeds = [Feed(name="Feed A", url="https://example.org/a")]
    older = numbered(3, published=NOW - timedelta(hours=2))
    newer = make_item("newer", "https://example.org/newer", published=NOW)
    older[2].watched = True
    candidates, left_out = cap([*older, newer], feeds, [], 2)
    assert candidates == [older[2], newer]
    assert left_out == 2


def test_watched_names_match_whole_names_only():
    authors = [
        "Wei Li",
        "Wei Liang",
        "Xiaowei Liu",
        "A. Other, Wei Li (Some University, City), B. Third",
        "J. Doe Jr., K. Roe",
    ]
    items = [
        make_item(str(n), f"https://example.org/{n}", authors=a) for n, a in enumerate(authors)
    ]
    mark_watched(items, ["wei li", "J. Doe Jr."])
    assert [item.watched for item in items] == [True, False, False, True, True]
