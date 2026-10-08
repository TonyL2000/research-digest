"""Load config.yaml; config.local.yaml can replace keys, WATCH_AUTHORS can add names."""

from collections.abc import Mapping
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from digest import __version__

ROOT = Path(__file__).resolve().parent.parent


class Feed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    quota: int | None = Field(None, ge=0)  # most candidates this feed may send per run
    announce_types: list[str] = []  # arXiv: keep only these announce types (empty = all)
    plain_text: bool = False  # summaries are plain text, so do not strip HTML from them
    republish_summary: bool = False  # the feed's own text may appear in the public archive
    fixture: str = ""  # file in fixtures/ that --dry-run reads instead of the network


class Pricing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_per_mtok: float = 0.10  # US dollars per million input tokens
    output_per_mtok: float = 0.50


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled key is an error, not a no-op

    feeds: list[Feed]
    interests: str
    watch_authors: list[str] = []
    priority_keywords: list[str] = []
    include_keywords: list[str] = []
    exclude_keywords: list[str] = []
    top_k: int = Field(10, ge=1)  # ge=1: "greater than or equal to 1"
    max_candidates: int = Field(150, ge=1)
    lookback_hours: int = Field(36, ge=1)
    model: str = "claude-haiku-5-5"
    max_summary_chars: int = Field(600, ge=20)
    pricing: Pricing = Pricing()
    archive_url: str = ""  # folder URL of the public archive; empty = work it out on GitHub

    @model_validator(mode="after")
    def feed_names_are_unique(self) -> Self:
        # Items are tied to their feed by name (quotas, announce types, fallback text).
        names = [feed.name for feed in self.feeds]
        if len(set(names)) != len(names):
            raise ValueError("feed names must be unique")
        return self


def load_config(
    root: Path = ROOT, env: Mapping[str, str] | None = None, use_local: bool = True
) -> Config:
    """Read config.yaml; any top-level key in config.local.yaml replaces the same key."""
    data = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    local = root / "config.local.yaml"
    if use_local and local.exists():
        data.update(yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    config = Config(**data)
    # config.local.yaml is gitignored, so it never reaches GitHub Actions. A private watch
    # list can be passed there as a secret instead: names separated by semicolons.
    extra = (env or {}).get("WATCH_AUTHORS", "")
    config.watch_authors += [name.strip() for name in extra.split(";") if name.strip()]
    return config


def archive_url(config: Config, env: Mapping[str, str]) -> str:
    """URL of the folder that holds the committed digests, or "" if it is not known."""
    if config.archive_url:
        return config.archive_url.rstrip("/")
    repo = env.get("GITHUB_REPOSITORY", "")
    if not repo:
        return ""
    server = env.get("GITHUB_SERVER_URL", "https://github.com")
    branch = env.get("GITHUB_REF_NAME", "main")
    return f"{server}/{repo}/blob/{branch}/digests"


def user_agent(env: Mapping[str, str]) -> str:
    """Identify the bot to feed servers. Some sites reject Python's built-in user agent."""
    repo = env.get("GITHUB_REPOSITORY", "")
    where = f"+https://github.com/{repo}" if repo else "personal RSS digest"
    return f"research-digest/{__version__} ({where})"
