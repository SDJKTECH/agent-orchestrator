"""Live demo: real GitHub issues, real Claude agents, one branch per agent.

Starts the orchestrator against THIS repository's GitHub project. Issues with the
`agent-task` label are picked up; for each one a builder, tester and reviewer
agent run in their own branches (agent/task-N, agent/task-N-tester,
agent/task-N-reviewer). Branches are pushed with your normal git credentials.
When the reviewer approves, a pull request is opened (or, without a token, a
compare link is shown). Nothing is merged.

GitHub access works two ways (--backend auto picks one):
  gh    the GitHub CLI, signed in with `gh auth login`
  rest  the GitHub API directly, no CLI. Reading public issues needs no token;
        set GITHUB_TOKEN to also open PRs, comment, and create labels/issues.

This costs money (Claude) and writes to GitHub (branches, PRs, comments).

    python demo/run_live.py                      # start and watch
    python demo/run_live.py --create-label --create-issue "Add a docstring"
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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_demo import ROOT, Api, say, wait_for_server  # noqa: E402

from orchestrator.app.core.config import Settings  # noqa: E402
from orchestrator.app.core.exceptions import OrchestratorError  # noqa: E402
from orchestrator.app.github.client import GitHubApi, detect_github_repo  # noqa: E402

LABEL = "agent-task"
REQUIRED_AGENTS = ("builder", "tester", "reviewer")


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", cwd=ROOT
    )


def fail(message: str) -> None:
    sys.exit(f"Preflight failed: {message}")


def choose_backend(requested: str) -> str:
    gh_ready = (
        shutil.which("gh") is not None and run(["gh", "auth", "status"]).returncode == 0
    )

    if requested == "gh" and not gh_ready:
        fail("gh is missing or not signed in (gh auth login). Try --backend rest.")

    return requested if requested != "auto" else ("gh" if gh_ready else "rest")


def preflight(args) -> tuple[str, str, GitHubApi]:
    """Check what the run depends on. Return (backend, repo name, REST client)."""
    backend = choose_backend(args.backend)

    if not args.stub and shutil.which("claude") is None:
        fail("Claude Code CLI ('claude') not found on PATH.")

    repo = args.repo or detect_github_repo(ROOT)
    if repo is None:
        fail("cannot work out the GitHub repo from 'origin'; pass --repo owner/name.")

    for role in REQUIRED_AGENTS:
        if run(["git", "cat-file", "-e", f"main:.claude/agents/{role}.md"]).returncode:
            fail(f"local main is missing .claude/agents/{role}.md (commit it).")

    run(["git", "fetch", "origin", "main"])
    ahead = run(["git", "rev-list", "--count", "origin/main..main"])
    if ahead.returncode == 0 and ahead.stdout.strip() != "0":
        fail(
            f"local main is {ahead.stdout.strip()} commit(s) ahead of origin/main. "
            "Agents branch from local main, so push it first."
        )

    api = GitHubApi(repo, load_token())
    ensure_label(api, backend, repo, args.create_label)
    return backend, repo, api


def load_token() -> str | None:
    """Read GITHUB_TOKEN from the environment or the repo's .env (never printed)."""
    settings = Settings(_env_file=ROOT / ".env")
    return settings.github_token.get_secret_value() if settings.github_token else None


def write_or_fail(api: GitHubApi, method: str, path: str, body: dict):
    """Call the GitHub API, turning a permission error into a clear message."""
    try:
        return api.request(method, path, body)
    except OrchestratorError as exc:
        hint = ""
        if "403" in str(exc) or "404" in str(exc):
            hint = (
                " The token needs 'Issues: Read and write' (and 'Pull requests: "
                "Read and write' for PRs) on this repository."
            )
        fail(f"GitHub rejected {method} {path}: {exc}{hint}")


def label_exists(api: GitHubApi) -> bool:
    try:
        api.request("GET", f"/labels/{LABEL}")
    except OrchestratorError as exc:
        if " 404" in str(exc):
            return False
        fail(f"cannot reach GitHub: {exc}")
    return True


def ensure_label(api: GitHubApi, backend: str, repo: str, create: bool) -> None:
    if label_exists(api):
        return

    if not create:
        fail(f"label '{LABEL}' does not exist on {repo}. Use --create-label.")

    description = "Picked up by the agent orchestrator"

    if backend == "gh":
        result = run(
            [
                "gh",
                "label",
                "create",
                LABEL,
                "--repo",
                repo,
                "--description",
                description,
            ]
        )
        if result.returncode != 0:
            fail(f"could not create label: {result.stderr.strip()}")
    elif api.can_write:
        write_or_fail(
            api, "POST", "/labels", {"name": LABEL, "description": description}
        )
    else:
        fail(
            f"create the '{LABEL}' label at https://github.com/{repo}/labels "
            "or set GITHUB_TOKEN and use --create-label."
        )

    say(f"Created label '{LABEL}' on {repo}.")


def create_issue(backend: str, api: GitHubApi, title: str, body: str) -> None:
    if backend == "gh":
        result = run(
            [
                "gh",
                "issue",
                "create",
                "--repo",
                api.repo,
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
    elif api.can_write:
        created = write_or_fail(
            api, "POST", "/issues", {"title": title, "body": body, "labels": [LABEL]}
        )
        say(f"Created issue: {created['html_url']}")
    else:
        fail(
            "creating issues needs GITHUB_TOKEN. Instead create an issue at "
            f"https://github.com/{api.repo}/issues/new and add the '{LABEL}' label."
        )


def snapshot(api: Api) -> str:
    tasks = api.get("/tasks")
    if not tasks:
        return "(no tasks yet - waiting for an issue labelled agent-task)"

    lines = [
        f"{'task':<5}{'issue':<7}{'status':<11}{'stage':<10}"
        f"{'in_tok':>8}{'out_tok':>9}{'cost_usd':>10}  pull request / compare link"
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
    parser.add_argument("--backend", choices=["auto", "gh", "rest"], default="auto")
    parser.add_argument("--repo", help="owner/name (default: from origin)")
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

    backend, repo, github = preflight(args)

    if args.create_issue:
        create_issue(backend, github, args.create_issue, args.issue_body)

    publish = not args.no_publish
    open_pr = publish and not args.no_pr
    comments = not args.no_comments
    read_only_api = backend == "rest" and not github.can_write

    say(f"Repository:      {repo}   (GitHub access: {backend})")
    say(f"Issue label:     {LABEL}   poll every {args.poll}s")
    say(
        f"Agents:          {'STUB (fake claude)' if args.stub else 'real Claude'}"
        f", budget ${args.budget} per run, {args.concurrency} at once"
    )
    say(f"Push branches:   {publish}   Open PRs: {open_pr}   Comment: {comments}")
    if read_only_api:
        say(
            "No GITHUB_TOKEN: issues are read from the public API, branches are "
            "pushed with your git credentials, PRs are shown as compare links "
            "and issue comments are skipped."
        )
    if not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
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
        "GITHUB_BACKEND": backend,
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
    server_api = Api(f"http://127.0.0.1:{args.port}")

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
        wait_for_server(server_api, server, log_file)
        say(f"Dashboard:  {server_api.base_url}/dashboard   <- open this in a browser")
        say(f"API docs:   {server_api.base_url}/docs   (Ctrl+C to stop)")
        say(f"Server log: {log_file}\n")
        say(f"Add the '{LABEL}' label to an issue on GitHub to start an agent run.\n")

        last = ""
        while True:
            try:
                current = snapshot(server_api)
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
