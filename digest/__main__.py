"""Command line: python -m digest run [--dry-run] [--no-send]"""

import argparse
import logging
import os
import sys
from datetime import UTC, datetime

from digest.config import ROOT, load_config, user_agent
from digest.dryrun import FIXTURE_NOW, FakeClient, read_fixtures
from digest.fetch import fetch_all
from digest.pipeline import run
from digest.selection import make_client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m digest")
    commands = parser.add_subparsers(dest="command", required=True)
    run_command = commands.add_parser("run", help="build today's digest and deliver it")
    run_command.add_argument(
        "--dry-run",
        action="store_true",
        help="use recorded feeds and a fake model; no network, no cost; output in ./out/",
    )
    run_command.add_argument(
        "--no-send",
        action="store_true",
        help="do everything for real except sending the email and the WeChat message",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    config = load_config(env=os.environ)

    if args.dry_run:
        return run(
            config,
            now=FIXTURE_NOW,
            raw=read_fixtures(config.feeds),
            client=FakeClient(),
            seen_path=None,
            archive_dir=ROOT / "out",
            preview_dir=ROOT / "out",
            send=False,
            env={},
        )
    return run(
        config,
        now=datetime.now(UTC),
        raw=fetch_all(config.feeds, user_agent(os.environ)),
        client=make_client(os.environ),
        seen_path=ROOT / "data" / "seen.json",
        archive_dir=ROOT / "digests",
        send=not args.no_send,
        env=os.environ,
    )


if __name__ == "__main__":
    sys.exit(main())
