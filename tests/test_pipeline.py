"""End-to-end runs on the recorded fixtures. No network, no API, nothing sent."""

import socket
from datetime import timedelta

import pytest
from helpers import FakeClient, make_pick, model_reply, repo_config

import digest.__main__ as cli
from digest import deliver as channels
from digest.config import load_config
from digest.dedup import SeenStore
from digest.dryrun import FIXTURE_NOW, read_fixtures
from digest.dryrun import FakeClient as DryRunClient
from digest.pipeline import run, write_archive

EMAIL_ENV = {
    "SMTP_HOST": "smtp.example.org",
    "SMTP_USER": "me@example.org",
    "SMTP_PASS": "app-password",
    "MAIL_TO": "me@example.org",
}
ALL_ENV = EMAIL_ENV | {"SERVERCHAN_SENDKEY": "SCT1Tkey"}


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("this test must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)


def run_fixtures(
    tmp_path, *, client=None, seen=True, send=False, env=None, raw=None, now=FIXTURE_NOW
):
    config = repo_config()
    return run(
        config,
        now=now,
        raw=read_fixtures(config.feeds) if raw is None else raw,
        client=client or DryRunClient(),
        seen_path=tmp_path / "data" / "seen.json" if seen else None,
        archive_dir=tmp_path / "digests",
        preview_dir=tmp_path / "out",
        send=send,
        env=env or {},
    )


def test_cli_dry_run_is_offline_and_writes_to_out(tmp_path, monkeypatch, no_network):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    assert cli.main(["run", "--dry-run"]) == 0

    out = tmp_path / "out"
    assert sorted(path.name for path in out.iterdir()) == [
        "2026-10-07.md",
        "email.html",
        "wechat.md",
    ]
    assert not (tmp_path / "data").exists()  # a dry run never touches the seen store
    assert not (tmp_path / "digests").exists()
    markdown = (out / "2026-10-07.md").read_text(encoding="utf-8")
    assert markdown.startswith("# Research digest: 2026-10-07")
    assert markdown.count("\n### [") == load_config(use_local=False).top_k
    assert "Model fake model (dry run)" in markdown  # the footer does not credit a real model
    assert "<h1>Research digest: 2026-10-07</h1>" in (out / "email.html").read_text(
        encoding="utf-8"
    )

    assert cli.main(["run", "--dry-run"]) == 0  # a second dry run overwrites, no "-2" file
    assert len(list(out.iterdir())) == 3

    # Private settings must not change a dry run: watching an author would otherwise
    # force that paper into the digest.
    monkeypatch.setenv("WATCH_AUTHORS", "Kaisa Laiho; Anjali Thawait")
    assert cli.main(["run", "--dry-run"]) == 0
    assert (out / "2026-10-07.md").read_text(encoding="utf-8") == markdown


def test_pipeline_filters_merges_and_archives(tmp_path):
    picker = FakeClient()

    def answer(**kwargs):
        picker.calls.append(kwargs)
        return DryRunClient.messages.parse(**kwargs)

    picker.parse = answer
    assert run_fixtures(tmp_path, client=picker) == 0

    prompt = picker.calls[0]["messages"][0]["content"]
    assert "Announce Type" not in prompt  # arXiv boilerplate is gone
    assert "outside the lookback window" not in prompt.lower()  # three old fixture items
    assert prompt.count("Citywide Blackout") == 0  # the later near-duplicate was merged away
    assert "city-wide blackout" in prompt
    assert "Ignore previous instructions" in prompt  # present, but only as JSON data
    assert prompt.count("</candidates>") == 1

    # 26 arXiv items minus 9 replacements, plus 2 new q-fin, 2 APS, 3 Physics World and
    # 2 Quanta in the window, minus 1 merged near-duplicate
    assert prompt.count('"id":') == 17 + 2 + 2 + 3 + 2 - 1

    archive = tmp_path / "digests" / "2026-10-07.md"
    assert archive.exists()
    assert "also in Quanta Magazine" in archive.read_text(encoding="utf-8")


def test_second_run_sees_nothing_new_and_makes_no_api_call(tmp_path):
    assert run_fixtures(tmp_path) == 0
    seen = SeenStore.load(tmp_path / "data" / "seen.json")
    assert len(seen.entries) == 26  # every eligible item, including the merged twin

    client = FakeClient()
    assert run_fixtures(tmp_path, client=client) == 0
    assert client.calls == []
    assert len(list((tmp_path / "digests").iterdir())) == 1


def test_items_outside_the_window_are_not_marked_seen(tmp_path):
    run_fixtures(tmp_path)
    seen = SeenStore.load(tmp_path / "data" / "seen.json")
    assert not seen.contains("https://quanta.example.org/an-old-story-20260914")
    assert seen.contains("https://pw.example.org/a/entangled-photon-source-fits-on-a-fingernail")


def test_unranked_fallback_still_produces_a_digest(tmp_path):
    refused = FakeClient(model_reply(None, "refusal"))
    assert run_fixtures(tmp_path, client=refused) == 0
    text = (tmp_path / "digests" / "2026-10-07.md").read_text(encoding="utf-8")
    assert "Unranked: the model step did not complete (stop_reason=refusal)" in text
    assert text.count("\n### [") == 10
    assert "Integrated device emits pairs" not in text  # news teasers stay out of the archive


def test_model_naming_an_unknown_id_falls_back(tmp_path):
    client = FakeClient(model_reply([make_pick("0123456789ab")]))
    assert run_fixtures(tmp_path, client=client) == 0
    assert "Unranked" in (tmp_path / "digests" / "2026-10-07.md").read_text(encoding="utf-8")


def test_failed_feed_is_reported_and_the_run_continues(tmp_path):
    raw = read_fixtures(repo_config().feeds)
    del raw["Quanta Magazine"]
    assert run_fixtures(tmp_path, raw=raw) == 0
    text = (tmp_path / "digests" / "2026-10-07.md").read_text(encoding="utf-8")
    assert "Feeds that failed: Quanta Magazine." in text


def test_every_feed_failing_is_an_error(tmp_path):
    assert run_fixtures(tmp_path, raw={}) == 1
    assert not (tmp_path / "digests").exists()


def test_quiet_day_makes_no_call_and_sends_nothing(tmp_path):
    client = FakeClient()
    later = FIXTURE_NOW + timedelta(days=30)  # everything is outside the lookback window
    assert run_fixtures(tmp_path, client=client, send=True, env=ALL_ENV, now=later) == 0
    assert client.calls == []
    assert not (tmp_path / "digests").exists()


def test_same_day_archives_do_not_overwrite(tmp_path):
    first = write_archive(tmp_path, FIXTURE_NOW.date(), "one", keep_existing=True)
    second = write_archive(tmp_path, FIXTURE_NOW.date(), "two", keep_existing=True)
    assert (first.name, second.name) == ("2026-10-07.md", "2026-10-07-2.md")
    assert first.read_text(encoding="utf-8") == "one"


# --- delivery and the exit code ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("email_ok", "wechat_ok", "exit_code"),
    [(True, True, 0), (True, False, 0), (False, True, 0), (False, False, 1)],
)
def test_exit_code_is_nonzero_only_if_every_enabled_channel_fails(
    tmp_path, monkeypatch, email_ok, wechat_ok, exit_code
):
    sent = []

    def fake_email(env, subject, text, html):
        sent.append(("email", subject))
        if not email_ok:
            raise RuntimeError("smtp down")

    def fake_wechat(env, title, body):
        sent.append(("wechat", title))
        if not wechat_ok:
            raise RuntimeError("serverchan down")

    monkeypatch.setattr(channels, "send_email", fake_email)
    monkeypatch.setattr(channels, "send_wechat", fake_wechat)
    assert run_fixtures(tmp_path, send=True, env=ALL_ENV) == exit_code
    assert sent == [
        ("email", "Research digest: 2026-10-07, 10 items"),
        ("wechat", "Digest 2026-10-07 (10 items)"),
    ]
    assert (tmp_path / "digests" / "2026-10-07.md").exists()  # archived even if delivery failed


def test_only_configured_channels_are_used(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(channels, "send_email", lambda *args: sent.append("email"))
    monkeypatch.setattr(channels, "send_wechat", lambda *args: sent.append("wechat"))
    assert run_fixtures(tmp_path, send=True, env=EMAIL_ENV) == 0
    assert sent == ["email"]


def test_no_configured_channel_is_not_a_failure(tmp_path):
    assert run_fixtures(tmp_path, send=True, env={}) == 0
    assert (tmp_path / "digests" / "2026-10-07.md").exists()


def test_no_send_skips_delivery(tmp_path, monkeypatch):
    def boom(*args):
        raise AssertionError("nothing may be sent")

    monkeypatch.setattr(channels, "send_email", boom)
    monkeypatch.setattr(channels, "send_wechat", boom)
    assert run_fixtures(tmp_path, send=False, env=ALL_ENV) == 0


# --- isolation of feeds, and what an unranked day uses up ----------------------------------------


def test_a_feed_that_cannot_be_parsed_is_reported_and_the_rest_continue(tmp_path, monkeypatch):
    import digest.pipeline as pipeline

    real_parse = pipeline.parse_feed

    def parse(feed, *args):
        if feed.name == "Physics World":
            raise RuntimeError("parser blew up")
        return real_parse(feed, *args)

    monkeypatch.setattr(pipeline, "parse_feed", parse)
    assert run_fixtures(tmp_path) == 0
    text = (tmp_path / "digests" / "2026-10-07.md").read_text(encoding="utf-8")
    assert "Feeds that failed: Physics World." in text


def test_every_feed_failing_to_parse_is_an_error(tmp_path, monkeypatch):
    import digest.pipeline as pipeline

    def parse(*args):
        raise RuntimeError("parser blew up")

    monkeypatch.setattr(pipeline, "parse_feed", parse)
    assert run_fixtures(tmp_path) == 1
    assert not (tmp_path / "digests").exists()


def test_an_unranked_run_uses_up_only_what_it_showed(tmp_path):
    seen_path = tmp_path / "data" / "seen.json"
    assert run_fixtures(tmp_path, client=FakeClient(model_reply(None, "refusal"))) == 0
    # the ten items shown, plus the Quanta copy of a story that was shown from Physics World
    seen = SeenStore.load(seen_path)
    assert len(seen.entries) == 11
    assert seen.contains(
        "https://quanta.example.org/how-a-quantum-network-survived-a-citywide-blackout-20261007"
    )

    # The same day, once the model works again: the other items are still there to rank.
    assert run_fixtures(tmp_path) == 0
    names = sorted(path.name for path in (tmp_path / "digests").iterdir())
    assert names == ["2026-10-07-2.md", "2026-10-07.md"]
    second = (tmp_path / "digests" / "2026-10-07-2.md").read_text(encoding="utf-8")
    assert "Unranked" not in second
    assert len(SeenStore.load(seen_path).entries) == 26


def test_old_entries_are_pruned_from_the_seen_store_on_each_run(tmp_path):
    seen_path = tmp_path / "data" / "seen.json"
    old = SeenStore()
    old.add(["https://example.org/long-ago"], FIXTURE_NOW.date() - timedelta(days=90))
    old.save(seen_path)
    assert run_fixtures(tmp_path) == 0
    assert not SeenStore.load(seen_path).contains("https://example.org/long-ago")


def test_watch_list_reaches_the_prompt_and_the_digest(tmp_path):
    picker = FakeClient(model_reply([make_pick("placeholder")]))

    def answer(**kwargs):
        picker.calls.append(kwargs)
        return DryRunClient.messages.parse(**kwargs)

    picker.parse = answer
    config = repo_config()
    config.watch_authors = ["Anjali Thawait"]  # author of a replaced q-fin paper: not eligible
    config.watch_authors.append("Carmine Ventre")  # co-author of a new q-fin paper
    code = run(
        config,
        now=FIXTURE_NOW,
        raw=read_fixtures(config.feeds),
        client=picker,
        seen_path=None,
        archive_dir=tmp_path,
        send=False,
        env={},
    )
    assert code == 0
    prompt = picker.calls[0]["messages"][0]["content"]
    data = prompt.split("</candidates>")[0]  # the instructions mention the flag as well
    assert data.count('"must_include": true') == 1
    assert "FactorBench" in (tmp_path / "2026-10-07.md").read_text(encoding="utf-8")


def truncated(raw: bytes) -> bytes:
    """A download that was cut off before the first entry was complete."""
    return raw[: raw.index(b"<item") + 40]


def test_a_feed_cut_off_before_its_first_entry_counts_as_failed(tmp_path):
    raw = read_fixtures(repo_config().feeds)
    raw["Quanta Magazine"] = truncated(raw["Quanta Magazine"])
    assert run_fixtures(tmp_path, raw=raw) == 0
    text = (tmp_path / "digests" / "2026-10-07.md").read_text(encoding="utf-8")
    assert "Feeds that failed: Quanta Magazine." in text


def test_every_feed_cut_off_is_an_error_not_a_quiet_day(tmp_path):
    raw = {name: truncated(body) for name, body in read_fixtures(repo_config().feeds).items()}
    assert run_fixtures(tmp_path, raw=raw) == 1
