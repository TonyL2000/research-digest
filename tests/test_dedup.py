import json
from datetime import date, timedelta

from helpers import NOW, make_item

from digest.dedup import SeenStore, is_duplicate, merge_duplicates, normalize_title
from digest.normalize import url_hash

TODAY = date(2026, 10, 7)


def test_seen_store_remembers_urls_by_hash():
    store = SeenStore()
    assert not store.contains("https://example.org/a")
    store.add(["https://example.org/a"], TODAY)
    assert store.contains("https://example.org/a")
    assert store.entries == {url_hash("https://example.org/a"): "2026-10-07"}


def test_seen_store_keeps_the_first_seen_date():
    store = SeenStore()
    store.add(["https://example.org/a"], TODAY)
    store.add(["https://example.org/a"], TODAY + timedelta(days=3))
    assert list(store.entries.values()) == ["2026-10-07"]


def test_seen_store_prunes_entries_older_than_60_days():
    store = SeenStore()
    store.add(["https://example.org/old"], TODAY - timedelta(days=61))
    store.add(["https://example.org/edge"], TODAY - timedelta(days=60))
    store.add(["https://example.org/new"], TODAY)
    store.prune(TODAY)
    assert not store.contains("https://example.org/old")
    assert store.contains("https://example.org/edge")
    assert store.contains("https://example.org/new")


def test_seen_store_round_trip_is_sorted_json(tmp_path):
    path = tmp_path / "data" / "seen.json"
    store = SeenStore()
    store.add(["https://example.org/b", "https://example.org/a"], TODAY)
    store.save(path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert list(saved) == sorted(saved)
    assert SeenStore.load(path).entries == store.entries
    assert SeenStore.load(tmp_path / "missing.json").entries == {}


def test_normalize_title():
    assert normalize_title("  Photon-Blockade:  a NEW Look! ") == "photon blockade a new look"


def test_same_url_from_two_feeds_is_one_item():
    a = make_item("Paper", "https://arxiv.org/abs/2610.00001", source="arXiv quant-ph")
    b = make_item("Paper", "https://arxiv.org/abs/2610.00001", source="arXiv q-fin")
    merged = merge_duplicates([a, b])
    assert len(merged) == 1
    assert merged[0].source == "arXiv quant-ph"
    assert merged[0].also_in == ["arXiv q-fin"]


def test_near_duplicate_titles_across_feeds_keep_the_earliest():
    late = make_item(
        "How a Quantum Network Survived a Citywide Blackout",
        "https://quanta.example.org/story",
        source="Quanta",
        published=NOW,
    )
    early = make_item(
        "How a quantum network survived a city-wide blackout",
        "https://pw.example.org/story",
        source="Physics World",
        published=NOW - timedelta(hours=5),
    )
    merged = merge_duplicates([late, early])
    assert [item.source for item in merged] == ["Physics World"]
    assert merged[0].also_in == ["Quanta"]


def test_similar_titles_inside_one_feed_are_not_merged():
    one = make_item("Quantum walks on graphs, Part I", "https://arxiv.org/abs/1")
    two = make_item("Quantum walks on graphs, Part II", "https://arxiv.org/abs/2")
    assert len(merge_duplicates([one, two])) == 2


def test_a_short_title_does_not_swallow_a_longer_one():
    short = make_item("Photon blockade", "https://example.org/1", source="Feed A")
    long = make_item(
        "Photon blockade in a driven nonlinear cavity coupled to two atoms",
        "https://example.org/2",
        source="Feed B",
    )
    assert not is_duplicate(short, long)


def test_different_titles_are_kept_apart():
    a = make_item("Squeezed light improves optical clocks", "https://example.org/1", source="A")
    b = make_item("Factor timing with machine learning", "https://example.org/2", source="B")
    assert len(merge_duplicates([a, b])) == 2


def test_merging_keeps_the_watched_flag():
    plain = make_item("Same story", "https://example.org/1", source="A")
    watched = make_item("Same story", "https://example.org/2", source="B", watched=True)
    assert merge_duplicates([plain, watched])[0].watched


def test_a_title_that_extends_another_is_a_different_item():
    # Every word of the first title is in the second, so token_set_ratio alone says 100.
    base = make_item("Photon blockade in a driven cavity", "https://example.org/1", source="A")
    longer = make_item(
        "Photon blockade in a driven cavity with two atoms", "https://example.org/2", source="B"
    )
    assert not is_duplicate(base, longer)
