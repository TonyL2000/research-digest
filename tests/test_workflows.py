"""The workflow files cannot be run locally, so at least check that they parse and say
what the documentation claims."""

import yaml

from digest.config import ROOT

WORKFLOWS = ROOT / ".github" / "workflows"


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def triggers(workflow: dict) -> dict:
    return workflow[True]  # YAML reads the bare key "on" as the boolean True


def test_daily_workflow():
    workflow = load("daily.yml")
    assert triggers(workflow)["schedule"] == [{"cron": "17 12 * * *"}]
    assert "workflow_dispatch" in triggers(workflow)
    assert workflow["permissions"] == {"contents": "write"}
    assert workflow["concurrency"] == {"group": "daily-digest", "cancel-in-progress": False}

    job = workflow["jobs"]["digest"]
    assert job["timeout-minutes"] <= 30
    run_step, commit_step = job["steps"][-2:]
    assert run_step["run"] == "python -m digest run"
    assert set(run_step["env"]) >= {
        "ANTHROPIC_API_KEY",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASS",
        "MAIL_TO",
        "SERVERCHAN_SENDKEY",
    }
    assert commit_step["if"] == "${{ !cancelled() }}"  # archive even if delivery failed
    script = commit_step["run"]
    assert "git add digests data/seen.json" in script
    assert "git diff --cached --quiet" in script  # skip the commit when nothing changed
    assert "github-actions[bot]" in script
    assert "&&" not in script  # each git command on its own line, so a failure stops the step


def test_ci_workflow():
    workflow = load("ci.yml")
    assert set(triggers(workflow)) == {"push", "pull_request"}
    assert workflow["permissions"] == {"contents": "read"}
    commands = "\n".join(step.get("run", "") for step in workflow["jobs"]["test"]["steps"])
    assert "ruff check ." in commands
    assert "python -m pytest" in commands
    assert "secrets." not in (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")


def test_commit_targets_exist_in_the_repository():
    # `git add` fails on a path that does not exist, so both must be committed.
    assert (ROOT / "data" / "seen.json").exists()
    assert (ROOT / "digests").is_dir()
