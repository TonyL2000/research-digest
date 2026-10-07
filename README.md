# research-digest

A small Python tool that turns RSS feeds into a daily reading list.

Once a day it fetches arXiv and physics-news feeds, removes what it has already shown
you, and asks Claude, in a single API call, to pick the ten items that best match a
paragraph describing your interests. The result arrives by email and as a WeChat
message, and is committed to [`digests/`](digests/) as a Markdown file.

It runs unattended on GitHub Actions. If the Claude call fails for any reason, you still
get a digest: the newest items, marked "unranked".

**Status:** version 0.1. The test suite and the offline dry run pass. The first real
digest will appear in [`digests/`](digests/) after the first scheduled run.

## How it works

```mermaid
flowchart LR
    CFG[config.yaml] --> F
    F["fetch<br/>(retries, skip failing feeds)"] --> N["normalize<br/>(clean URLs, strip HTML)"]
    N --> D["dedup<br/>(seen.json, similar titles)"]
    D --> P["prefilter<br/>(time window, keywords, cap)"]
    P --> S{"select<br/>(one Claude call)"}
    S -- ok --> R["render<br/>(Markdown, HTML)"]
    S -- any failure --> U["newest items,<br/>marked unranked"] --> R
    R --> A[("digests/DATE.md<br/>data/seen.json")]
    R --> E["email<br/>(SMTP + STARTTLS)"]
    R --> W["WeChat<br/>(ServerChan)"]
```

Each box is one module in [`digest/`](digest/). [`digest/pipeline.py`](digest/pipeline.py)
calls them in order and is the best place to start reading. [`DESIGN.md`](DESIGN.md)
explains every decision, the alternatives, and how to switch to them.

## Try it offline

No API key, no network, nothing sent. Needs Python 3.14, the version the tests and the
scheduled job run on.

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

(On macOS or Linux: `source .venv/bin/activate`.)

```bash
python -m pip install -r requirements-dev.txt
```

```bash
python -m digest run --dry-run
```

The dry run reads the recorded feeds in [`fixtures/`](fixtures/), uses a fake model that
picks the first ten candidates, and writes three files to `out/`: the Markdown digest,
`email.html` and `wechat.md`.

```bash
python -m pytest
```

## Run it for real on your machine

Set `ANTHROPIC_API_KEY` in your environment, then:

```bash
python -m digest run --no-send
```

This fetches the live feeds, makes one Claude call (about one US cent, see
[Cost](#cost)), writes `digests/YYYY-MM-DD.md` and updates `data/seen.json`. Without
`--no-send` it also delivers, using the environment variables listed below.

## Configuration

Everything is in [`config.yaml`](config.yaml), which is commented. The keys you are most
likely to change:

| Key | Default | Meaning |
|---|---|---|
| `interests` | a paragraph | What you care about. Claude reads this text as written. |
| `feeds` | 2 arXiv feeds, 3 news feeds | Name, URL and a per-feed `quota` of candidates. |
| `watch_authors` | empty | Papers by these authors always make the digest. |
| `priority_keywords` | a short list | Decide which items go first when a feed exceeds its quota. |
| `top_k` | 10 | Entries in the digest. |
| `max_candidates` | 150 | Most items shown to the model in one call. |
| `lookback_hours` | 36 | Items older than this are ignored. |
| `model` | `claude-haiku-5-5` | Any Claude model that supports structured outputs. |

For private settings, create `config.local.yaml` next to it. It is gitignored, and any
top-level key in it replaces the same key in `config.yaml`:

```yaml
watch_authors:
  - Jane Q. Example
top_k: 15
```

## Deploy on GitHub Actions

1. Create the repository and push this code. With the GitHub CLI:

   ```bash
   gh repo create research-digest --public --source=. --push
   ```

2. Add the secrets. Each command prompts for the value, so it never appears in your
   shell history:

   ```bash
   gh secret set ANTHROPIC_API_KEY
   ```

   | Secret | Needed for | Notes |
   |---|---|---|
   | `ANTHROPIC_API_KEY` | ranking | Without it every digest is unranked. |
   | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_TO` | email | Port 587 with STARTTLS. `SMTP_PORT` may be left unset (587). Gmail needs an app password, not your account password. |
   | `SERVERCHAN_SENDKEY` | WeChat | Must be a ServerChan **Turbo** key (starts with `SCT`). See below. |
   | `MAIL_FROM` | optional | Sender address, if it differs from `SMTP_USER`. |
   | `WATCH_AUTHORS` | optional | Private watch list, names separated by semicolons. |

   A channel whose secrets are missing is skipped. The job fails only if every
   configured channel fails.

3. Trigger a first run and watch it:

   ```bash
   gh workflow run daily.yml
   ```

   ```bash
   gh run watch
   ```

After that, [`daily.yml`](.github/workflows/daily.yml) runs every day at about 12:17 UTC
and commits the new digest and `data/seen.json` as `github-actions[bot]`.
[`ci.yml`](.github/workflows/ci.yml) runs the linter and the tests on every push and pull
request.

### About the WeChat channel

Messages go through [ServerChan](https://sct.ftqq.com), a third-party relay.

- Use a Turbo key (`SCT...`). ServerChan's other product has keys starting with `sctp`
  and delivers to its own phone app, not to WeChat. The code refuses those keys.
- On the free plan the WeChat card shows only the title. Tapping it opens a detail page
  with the full text, which ServerChan keeps for about a day. The message therefore
  starts with a link to the permanent copy in this repository.
- The free plan allows five messages a day. One digest uses one.
- ServerChan stores the message body on its servers, and anyone with the detail link can
  read it. The digest contains only public paper and news metadata, so this is
  acceptable here. Do not reuse this channel for anything private.

### Scheduled runs can stop

- **The 60-day rule.** In public repositories GitHub disables scheduled workflows after
  60 days without repository activity. This project commits a digest on most days, and
  in other public repositories such bot commits have kept schedules alive, but GitHub
  does not document what counts as activity. If the Actions tab says "This scheduled
  workflow is disabled because there hasn't been activity in this repository for at
  least 60 days", click **Enable workflow**, or run:

  ```bash
  gh workflow enable daily.yml
  ```

- **Late or missing runs.** GitHub starts scheduled jobs when it has capacity. A run can
  be tens of minutes late, and on a busy day it can be dropped. The arXiv feed only
  holds the current day's papers, so a dropped day is lost. You can always start a run
  by hand with `gh workflow run daily.yml`.

## Cost

One Claude call per run. With the default model the price is $0.10 per million input
tokens and $0.50 per million output tokens (prompts up to 100,000 tokens; Anthropic's
pricing page, October 2026).

A full day's snapshot of the five feeds produced 114 candidates and a prompt of about
91,000 characters, roughly 23,000 to 37,000 tokens. With a few thousand output tokens
that is **well under one US cent per run**, a few dollars a year. This is an estimate:
every run logs its real token counts and cost, and prints them in the digest footer.

Nothing else costs money. GitHub Actions is free for public repositories and ServerChan
has a free plan.

## Limitations

- arXiv's RSS feed has no history. A day on which the job does not run is not caught up.
- On weekends and arXiv holidays the arXiv feeds are empty. If nothing at all is new,
  the job makes no API call and sends nothing.
- Email uses STARTTLS (usually port 587). Servers that only accept implicit TLS on port
  465 are not supported.
- The plain-text part of the email is the Markdown digest, escape codes included. Mail
  clients show the HTML part.
- A story is recognised as a repeat only if its URL is the same, or if two feeds publish
  it under nearly the same title within the same run.
- Author names appear as arXiv writes them, including TeX accents such as `\'e`.
- ServerChan reports "accepted", not "delivered to WeChat". A muted or unfollowed
  service account will not be noticed.
- Summaries are written by a language model from a title and an abstract. They can be
  wrong. Follow the link before relying on one.

## Prior art

Daily paper digests are a well-populated category. About fifteen existing projects were
surveyed before this one was written. Ideas were borrowed; no code was copied.

- [zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) and
  [gpt_paper_assistant](https://github.com/tatsu-lab/gpt_paper_assistant): read
  everything from the arXiv RSS feed, and filter by announce type.
- Several small digest projects keep a seen-URL file with an expiry date.
- Two of the surveyed projects name Claude models that have since been retired. Here
  the model is one line in `config.yaml`.

None of the surveyed projects had all of the following, which is what this one adds: a
single structured-output call instead of one call per paper, a fallback for every way
that call can fail, and a dry run that exercises the whole pipeline offline.

## Project layout

| Path | What it does |
|---|---|
| `digest/pipeline.py` | The whole run, top to bottom |
| `digest/config.py` | Loads and validates `config.yaml` |
| `digest/fetch.py` | Downloads feeds with timeouts and retries |
| `digest/normalize.py` | Feed entry to `Item`: canonical URL, plain-text summary |
| `digest/dedup.py` | Seen store and near-duplicate titles |
| `digest/prefilter.py` | Time window, keywords, candidate cap |
| `digest/selection.py` | The Claude call, its checks and the fallback |
| `digest/render.py` | Markdown, HTML and the WeChat body |
| `digest/deliver.py` | Email and WeChat, isolated from each other |
| `digest/dryrun.py` | Recorded feeds and a fake model for `--dry-run` |
| `fixtures/` | Feeds used by the dry run and the tests |
| `tests/` | Offline tests, one file per module |

## License

MIT. See [LICENSE](LICENSE).

This project is not affiliated with or endorsed by arXiv, APS, Physics World, Quanta
Magazine, ServerChan or Anthropic.
