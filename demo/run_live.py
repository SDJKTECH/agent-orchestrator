"""Live demo: real GitHub issues, real Claude agents, one branch per agent.

Starts the orchestrator against THIS repository's GitHub project. Issues with the
`agent-task` label are picked up; for each one a builder, tester and reviewer
agent run in their own branches (agent/task-N, agent/task-N-tester,
agent/task-N-reviewer). Branches are pushed, a pull request is opened when the
reviewer approves, and progress is commented on the issue. Nothing is merged.

This costs money (Claude) and writes to GitHub (branches, PRs, comments).

    python demo/run_live.py --create-label
    python demo/run_live.py --create-issue "Add a module docstring to exceptions.py"
    python demo/run_live.py            # start and watch
    python demo/run_live.py --no-publish --no-comments   # keep GitHub read-only
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_demo import ROOT, Api, say, wait_for_server  # noqa: E402

LABEL = "agent-task"
REQUIRED_AGENTS = ("builder", "tester", "reviewer")


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", cwd=ROOT, **kwargs
    )


def fail(message: str) -> None:
    sys.exit(f"Preflight failed: {message}")


def preflight(args) -> str:
    """Check everything the live run depends on. Return the GitHub repo name."""
    if shutil.which("gh") is None:
        fail("GitHub CLI not found. Install it (winget install GitHub.cli).")
    if run(["gh", "auth", "status"]).returncode != 0:
        fail("gh is not signed in. Run: gh auth login")
    if not args.stub and shutil.which("claude") is None:
        fail("Claude Code CLI ('claude') not found on PATH.")

    remote = run(["git", "remote", "get-url", "origin"])
    if remote.returncode != 0:
        fail("this clone has no 'origin' remote.")

    repo = run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if repo.returncode != 0:
        fail(f"cannot resolve the GitHub repo: {repo.stderr.strip()}")
    name = args.repo or repo.stdout.strip()

    for role in REQUIRED_AGENTS:
        if run(["git", "cat-file", "-e", f"main:.claude/agents/{role}.md"]).returncode:
            fail(f"local main is missing .claude/agents/{role}.md (commit it).")

    ahead = run(["git", "rev-list", "--count", "origin/main..main"])
    if ahead.returncode == 0 and ahead.stdout.strip() != "0":
        fail(
            f"local main is {ahead.stdout.strip()} commit(s) ahead of origin/main. "
            "Agents branch from local main, so push it first."
        )

    labels = run(["gh", "label", "list", "--json", "name", "--repo", name])
    if LABEL not in labels.stdout:
        if args.create_label:
            created = run(
                [
                    "gh",
                    "label",
                    "create",
                    LABEL,
                    "--repo",
                    name,
                    "--description",
                    "Picked up by the agent orchestrator",
                ]
            )
            if created.returncode != 0:
                fail(f"could not create label: {created.stderr.strip()}")
            say(f"Created label '{LABEL}' on {name}.")
        else:
            fail(f"label '{LABEL}' does not exist on {name}. Use --create-label.")

    return name


def create_issue(repo: str, title: str, body: str) -> None:
    result = run(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            title,
            "--body",
            body,
            "--label",
            LABEL,
        ]
    )
    if result.returncode != 0:
        fail(f"could not create issue: {result.stderr.strip()}")
    say(f"Created issue: {result.stdout.strip()}")


def snapshot(api: Api) -> str:
    tasks = api.get("/tasks")
    if not tasks:
        return "(no tasks yet - waiting for an issue labelled agent-task)"

    lines = [
        f"{'task':<5}{'issue':<7}{'status':<11}{'stage':<10}"
        f"{'in_tok':>8}{'out_tok':>9}{'cost_usd':>10}  pull request"
    ]
    for task in tasks:
        usage = api.get(f"/tasks/{task['id']}/usage")
        lines.append(
            f"{task['id']:<5}{task['github_issue_number'] or '-':<7}"
            f"{task['status']:<11}{task['current_stage'] or '-':<10}"
            f"{usage['input_tokens']:>8}{usage['output_tokens']:>9}"
            f"{usage['reported_cost_usd']:>10.4f}  {task['pull_request_url'] or '-'}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", help="owner/name (default: this clone's repo)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--poll", type=int, default=15, help="poll seconds")
    parser.add_argument("--budget", type=float, default=2.0, help="USD per agent run")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--create-label", action="store_true")
    parser.add_argument("--create-issue", metavar="TITLE")
    parser.add_argument("--issue-body", default="Created for the orchestrator demo.")
    parser.add_argument("--no-publish", action="store_true", help="do not push")
    parser.add_argument("--no-pr", action="store_true", help="do not open PRs")
    parser.add_argument("--no-comments", action="store_true", help="no issue comments")
    parser.add_argument("--stub", action="store_true", help="fake claude (testing)")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation")
    args = parser.parse_args()

    repo = preflight(args)

    if args.create_issue:
        create_issue(repo, args.create_issue, args.issue_body)

    publish = not args.no_publish
    open_pr = publish and not args.no_pr
    comments = not args.no_comments

    say(f"Repository:      {repo}")
    say(f"Issue label:     {LABEL}   poll every {args.poll}s")
    say(f"Agents:          {'STUB (fake claude)' if args.stub else 'real Claude'}"
        f", budget ${args.budget} per run, {args.concurrency} at once")
    say(f"Push branches:   {publish}   Open PRs: {open_pr}   Comment: {comments}")
    if not args.yes:
        if input("Continue? [y/N] ").strip().lower() != "y":
            sys.exit("Cancelled.")

    claude = (
        f'"{sys.executable}" "{ROOT / "demo" / "fake_claude.py"}"'
        if args.stub
        else "claude"
    )
    env = {
        **os.environ,
        "REPOSITORY_PATH": str(ROOT),
        "ISSUE_SOURCE": "gh",
        "ISSUE_LABEL": LABEL,
        "GITHUB_REPO": repo,
        "POLL_INTERVAL_SECONDS": str(args.poll),
        "CLAUDE_COMMAND": claude,
        "MAX_TASK_COST_USD": str(args.budget),
        "MAX_CONCURRENT_AGENTS": str(args.concurrency),
        "PUBLISH_BRANCHES": str(publish).lower(),
        "OPEN_PULL_REQUEST": str(open_pr).lower(),
        "COMMENT_ON_ISSUES": str(comments).lower(),
        "FAKE_CLAUDE_DELAY": "5",
    }

    log_file = ROOT / "logs" / "live_server.log"
    log_file.parent.mkdir(exist_ok=True)
    api = Api(f"http://127.0.0.1:{args.port}")

    with log_file.open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "orchestrator.app.main:app",
                "--port",
                str(args.port),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    try:
        say("Starting the orchestrator ...")
        wait_for_server(api, server, log_file)
        say(f"Running at {api.base_url}/docs  (Ctrl+C to stop; log: {log_file})\n")
        say(f"Add the '{LABEL}' label to an issue on GitHub to start an agent run.\n")

        last = ""
        while True:
            try:
                current = snapshot(api)
            except (urllib.error.URLError, ConnectionError):
                current = last
            if current != last:
                say(f"[{time.strftime('%H:%M:%S')}]\n{current}\n")
                last = current
            if server.poll() is not None:
                sys.exit(f"Server stopped unexpectedly; see {log_file}")
            time.sleep(5)
    except KeyboardInterrupt:
        say("\nStopping ...")
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    main()
