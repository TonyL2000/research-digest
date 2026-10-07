"""Load config.yaml, then let config.local.yaml and the environment override it."""

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from digest import __version__

ROOT = Path(__file__).resolve().parent.parent


class Feed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    quota: int | None = None  # most candidates this feed may contribute per run
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
    top_k: int = 10
    max_candidates: int = 150
    lookback_hours: int = 36
    model: str = "claude-haiku-5-5"
    max_summary_chars: int = 600
    pricing: Pricing = Pricing()
    archive_url: str = ""  # folder URL of the public archive; empty = work it out on GitHub


def load_config(root: Path = ROOT, env: Mapping[str, str] | None = None) -> Config:
    """Read config.yaml; any top-level key in config.local.yaml replaces the same key."""
    data = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    local = root / "config.local.yaml"
    if local.exists():
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
    """Identify the bot to feed servers. Several sites reject the default library name."""
    repo = env.get("GITHUB_REPOSITORY", "")
    where = f"+https://github.com/{repo}" if repo else "personal RSS digest"
    return f"research-digest/{__version__} ({where})"
