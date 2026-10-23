"""End-to-end demo of the agent orchestrator.

Creates a throwaway sample repository, starts the API, imports issues as tasks,
runs the builder -> tester -> reviewer pipeline for all of them in parallel, and
shows live status, pause/resume, cancel, cost tracking and verification results.

Stub mode (default) uses demo/fake_claude.py, so it is offline and free.
Real mode (--real) uses the `claude` CLI and costs money.

    python demo/run_demo.py
    python demo/run_demo.py --delay 10
    python demo/run_demo.py --real --issues path/to/issues.json
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = ROOT / "demo"
MARKER = ".agent-orchestrator-demo"
TERMINAL = {"completed", "failed", "cancelled"}


def say(message: str = "") -> None:
    print(message, flush=True)


def run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def create_sample_repo(repo: Path) -> None:
    """Create a tiny Python project with tests and the agent definitions."""
    (repo / "sample_app").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "sample_app" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "tests" / "test_smoke.py").write_text(
        "def test_smoke() -> None:\n    assert True\n", encoding="utf-8"
    )
    (repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["."]\n', encoding="utf-8"
    )
    (repo / ".gitignore").write_text(
        "__pycache__/\n.pytest_cache/\n.ruff_cache/\n", encoding="utf-8"
    )
    (repo / "README.md").write_text("# Sample app\n", encoding="utf-8")
    shutil.copytree(ROOT / ".claude" / "agents", repo / ".claude" / "agents")

    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.name", "Demo User")
    run_git(repo, "config", "user.email", "demo@example.com")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "Initial sample app")


def prepare_workdir(workdir: Path) -> None:
    if workdir.exists():
        if not (workdir / MARKER).exists():
            sys.exit(f"Refusing to delete {workdir}: not a demo directory.")
        shutil.rmtree(workdir, onexc=_make_writable_and_retry)
    workdir.mkdir(parents=True)
    (workdir / MARKER).write_text("demo\n", encoding="utf-8")


def _make_writable_and_retry(function, path, _exc) -> None:
    os.chmod(path, 0o700)
    function(path)


class Api:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def get(self, path: str):
        return self.call("GET", path)[1]

    def post(self, path: str, body: dict | None = None):
        status, payload = self.call("POST", path, body)
        if status >= 400:
            sys.exit(f"POST {path} failed with {status}: {payload}")
        return payload


def wait_for_server(api: Api, process: subprocess.Popen, log_file: Path) -> None:
    for _ in range(120):
        if process.poll() is not None:
            tail = log_file.read_text(encoding="utf-8", errors="replace")[-2000:]
            sys.exit(f"Server exited early:\n{tail}")
        try:
            if api.call("GET", "/health")[0] == 200:
                return
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.5)
    sys.exit("Server did not become healthy in time.")


def usage_by_task(api: Api, task_ids: list[int]) -> dict[int, dict]:
    return {task_id: api.get(f"/tasks/{task_id}/usage") for task_id in task_ids}


def print_table(api: Api, task_ids: list[int], started: float) -> str:
    tasks = {task["id"]: task for task in api.get("/tasks")}
    usage = usage_by_task(api, task_ids)
    lines = [
        f"[{time.monotonic() - started:6.1f}s] "
        f"{'task':<5}{'issue':<7}{'status':<11}{'stage':<10}{'runs':<6}"
        f"{'in_tok':>8}{'out_tok':>9}{'cost_usd':>10}"
    ]
    for task_id in task_ids:
        task = tasks[task_id]
        stats = usage[task_id]
        lines.append(
            f"{'':9}{task_id:<5}{task['github_issue_number'] or '-':<7}"
            f"{task['status']:<11}{task['current_stage'] or '-':<10}"
            f"{stats['run_count']:<6}{stats['input_tokens']:>8}"
            f"{stats['output_tokens']:>9}{stats['reported_cost_usd']:>10.4f}"
        )
    return "\n".join(lines)


def wait_until(api: Api, predicate, timeout: float, task_ids, started, label: str):
    """Poll until predicate(tasks_by_id) is true, printing table changes."""
    last = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        tasks = {task["id"]: task for task in api.get("/tasks")}
        if predicate(tasks):
            return tasks
        table = print_table(api, task_ids, started)
        snapshot = "\n".join(line[10:] for line in table.splitlines())
        if snapshot != last:
            say(table)
            say()
            last = snapshot
        time.sleep(1)
    sys.exit(f"Timed out waiting for: {label}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--real", action="store_true", help="use the real claude CLI")
    parser.add_argument("--delay", type=float, default=6, help="stub seconds per run")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--issues", type=Path, default=DEMO_DIR / "issues.json")
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "agent-orchestrator-demo",
    )
    parser.add_argument("--no-control", action="store_true", help="skip pause/cancel")
    parser.add_argument("--keep", action="store_true", help="leave server running")
    args = parser.parse_args()

    workdir = args.workdir.resolve()
    prepare_workdir(workdir)
    repo = workdir / "source"
    say(f"Demo directory: {workdir}")

    say("Creating sample repository ...")
    create_sample_repo(repo)

    if args.real:
        claude_command = "claude"
    else:
        claude_command = f'"{sys.executable}" "{DEMO_DIR / "fake_claude.py"}"'

    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{(workdir / 'demo.db').as_posix()}",
        "REPOSITORY_PATH": str(repo),
        "CLAUDE_COMMAND": claude_command,
        "ISSUE_SOURCE": "file",
        "ISSUE_FILE_PATH": str(args.issues.resolve()),
        "MAX_CONCURRENT_AGENTS": str(args.concurrency),
        "VERIFICATION_RUFF_TARGET": ".",
        "FAKE_CLAUDE_DELAY": str(args.delay),
        "LOG_LEVEL": "INFO",
        "ENVIRONMENT": "development",
    }

    log_file = workdir / "server.log"
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
        say(f"Starting API on port {args.port} ...")
        wait_for_server(api, server, log_file)

        project = api.post("/projects", {"name": "Sample App", "local_path": str(repo)})
        milestone = api.post(
            f"/projects/{project['id']}/milestones",
            {"code": "M1", "title": "Demo milestone"},
        )
        say(f"Project {project['id']}, milestone {milestone['id']} created.")

        say("Importing issues as tasks (each gets its own branch + worktree) ...")
        sync = api.post(f"/milestones/{milestone['id']}/sync")
        tasks = sync["imported"]
        for task in tasks:
            say(
                f"  issue #{task['github_issue_number']} -> task {task['id']} "
                f"branch {task['branch_name']}"
            )
        if sync["failed"]:
            sys.exit(f"Some issues failed to import: {sync['failed']}")

        again = api.post(f"/milestones/{milestone['id']}/sync")
        say(
            f"Re-sync is idempotent: imported {len(again['imported'])}, "
            f"skipped {len(again['skipped_issue_numbers'])}."
        )

        task_ids = [task["id"] for task in tasks]
        say(
            f"\nStarting pipelines for {len(task_ids)} tasks "
            f"(max {args.concurrency} run at once) ...\n"
        )
        started = time.monotonic()
        for task_id in task_ids:
            api.post(f"/tasks/{task_id}/pipeline")

        if not args.no_control and len(task_ids) >= 2:
            pause_id, cancel_id = task_ids[0], task_ids[1]

            wait_until(
                api,
                lambda t: all(
                    t[i]["status"] == "running" for i in (pause_id, cancel_id)
                ),
                60,
                task_ids,
                started,
                "first two tasks running",
            )
            say(f">>> CANCELLING task {cancel_id} (agent process is killed)")
            api.post(f"/tasks/{cancel_id}/cancel")

            say(f">>> PAUSING task {pause_id} (agent process is suspended)")
            api.post(f"/tasks/{pause_id}/pause")
            say(f"    active agents: {[a['id'] for a in api.get('/agents/active')]}")
            say(f"    task {pause_id} -> {api.get(f'/tasks/{pause_id}')['status']}")
            time.sleep(4)
            say(f">>> RESUMING task {pause_id}")
            api.post(f"/tasks/{pause_id}/resume")
            say()

        wait_until(
            api,
            lambda t: all(t[i]["status"] in TERMINAL for i in task_ids),
            600,
            task_ids,
            started,
            "all tasks to finish",
        )
        say(print_table(api, task_ids, started))

        say("\n=== Per-task detail ===")
        for task_id in task_ids:
            detail = api.get(f"/tasks/{task_id}")
            say(
                f"\nTask {task_id} (issue #{detail['github_issue_number']}) "
                f"{detail['title']!r}: {detail['status'].upper()}"
            )
            for run in detail["agent_runs"]:
                cost = run["reported_cost_usd"] or 0.0
                say(
                    f"  run {run['id']:<3}{run['agent_role']:<9}"
                    f"{run['model'] or '-':<8}{run['effort'] or '-':<7}"
                    f"{run['status']:<10}in={run['input_tokens']:<6}"
                    f"out={run['output_tokens']:<6}cost=${cost:.4f}"
                )
            for check in detail["verification_results"]:
                outcome = "PASS" if check["passed"] else "FAIL"
                say(f"  check {check['check_name']:<12}{outcome}")

        total = api.get(f"/milestones/{milestone['id']}/usage")
        say("\n=== Milestone usage ===")
        say(
            f"runs={total['run_count']}  input_tokens={total['input_tokens']}  "
            f"output_tokens={total['output_tokens']}  "
            f"cost=${total['reported_cost_usd']:.4f}"
        )
        say(f"\nWorktrees: {workdir / 'agent-worktrees'}")
        say(f"Server log: {log_file}")

        if args.keep:
            say(f"\nAPI left running: {api.base_url}/docs (Ctrl+C to stop)")
            server.wait()
    except KeyboardInterrupt:
        say("\nInterrupted.")
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    main()
