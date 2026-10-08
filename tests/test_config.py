import pytest
from helpers import make_config, repo_config
from pydantic import ValidationError

from digest.config import archive_url, load_config, user_agent

BASE = """
feeds:
  - name: Feed A
    url: https://example.org/a.xml
interests: optics
top_k: 10
watch_authors: [Public Person]
"""


def test_committed_config_is_valid_and_quotas_fit_the_cap():
    config = repo_config()
    assert config.model == "claude-haiku-5-5"
    assert sum(feed.quota or 0 for feed in config.feeds) <= config.max_candidates
    assert all(feed.fixture for feed in config.feeds)  # every feed works in a dry run


def test_local_file_replaces_top_level_keys(tmp_path):
    (tmp_path / "config.yaml").write_text(BASE, encoding="utf-8")
    (tmp_path / "config.local.yaml").write_text(
        "top_k: 3\nwatch_authors: [Private Person]\n", encoding="utf-8"
    )
    config = load_config(tmp_path)
    assert config.top_k == 3
    assert config.watch_authors == ["Private Person"]
    assert config.interests == "optics"  # untouched keys keep the committed value


def test_watch_authors_can_come_from_the_environment(tmp_path):
    (tmp_path / "config.yaml").write_text(BASE, encoding="utf-8")
    config = load_config(tmp_path, env={"WATCH_AUTHORS": "A. Einstein; N. Bohr ;"})
    assert config.watch_authors == ["Public Person", "A. Einstein", "N. Bohr"]


def test_unknown_key_is_rejected(tmp_path):
    (tmp_path / "config.yaml").write_text(BASE + "topk: 5\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(tmp_path)


def test_archive_url():
    config = make_config()
    assert archive_url(config, {}) == ""
    env = {"GITHUB_REPOSITORY": "someone/research-digest"}
    assert (
        archive_url(config, env) == "https://github.com/someone/research-digest/blob/main/digests"
    )
    explicit = make_config(archive_url="https://example.org/digests/")
    assert archive_url(explicit, env) == "https://example.org/digests"


def test_user_agent_names_the_project():
    assert user_agent({}).startswith("research-digest/")
    assert "github.com/someone/repo" in user_agent({"GITHUB_REPOSITORY": "someone/repo"})


def test_local_file_can_be_ignored(tmp_path):
    (tmp_path / "config.yaml").write_text(BASE, encoding="utf-8")
    (tmp_path / "config.local.yaml").write_text("top_k: 3\n", encoding="utf-8")
    assert load_config(tmp_path, use_local=False).top_k == 10  # what a dry run does


@pytest.mark.parametrize(
    "extra",
    [
        "top_k: 0\n",
        "max_candidates: 0\n",
        "feeds:\n  - {name: A, url: 'https://example.org/1', quota: -1}\n",
        "max_summary_chars: 5\n",
        "lookback_hours: -1\n",
        "feeds:\n  - {name: Same, url: 'https://example.org/1'}\n  - {name: Same, url: 'https://example.org/2'}\n",
    ],
)
def test_values_that_would_silently_misbehave_are_rejected(tmp_path, extra):
    (tmp_path / "config.yaml").write_text(
        "interests: optics\nfeeds: []\n" + extra, encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        load_config(tmp_path)
