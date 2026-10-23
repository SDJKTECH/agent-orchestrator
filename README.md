# AI Coding Agent Orchestrator

A platform for coordinating AI coding agents using Claude Code, Git branches, Git worktrees, and GitHub milestones.

## Planned capabilities

- Task and milestone management
- Independent agent execution in Git worktrees
- Agent lifecycle monitoring and control
- Model and effort selection
- Token usage and cost tracking
- Automated testing and code verification
- Dashboard for monitoring tasks and agents

## Technology Stack

- Python
- Claude Code
- Git and GitHub
- SQLite
- FastAPI
- Streamlit (planned)
## Project structure

```
orchestrator/app/
  main.py          FastAPI app and startup (uvicorn orchestrator.app.main:app)
  core/            config, exceptions, logging
  db/              database engine, ORM models, migrations
  services/        task creation and usage aggregation
  vcs/             git worktrees, commits, pushes
  agents/          Claude command building and process runner, model routing,
                   process control (pause/resume/cancel), job scheduling, usage parsing
  pipeline/        builder -> tester -> reviewer pipeline, one branch per agent
  verification/    pytest/ruff quality gate and result storage
  github/          REST client, issue intake, polling, comments, pull requests
  api/             HTTP routes and request/response schemas
  dashboard/       monitoring dashboard (page, overview endpoint, stage logic)
orchestrator/tests/   mirrors the layout above; shared helpers in helpers.py
demo/                 run_live.py (real GitHub + Claude), run_demo.py (offline)
```

Dependencies point one way: `core` <- `db` <- `vcs`/`services`/`verification`/`agents`
<- `pipeline`/`github` <- `api` <- `main`.

## Dashboard

Open `http://127.0.0.1:8000/dashboard` while the orchestrator is running. It refreshes
every 2 seconds and shows, for every task:

- **Progress by stage:** Builder -> Tester -> Checks -> Reviewer -> Pull request, each
  marked pending / running / paused / done / failed / rejected / cancelled / skipped,
  with the model and effort each agent used.
- **Controls:** Start (queued tasks), Pause, Resume and Cancel, plus **Sync issues** to
  import new GitHub issues immediately.
- **Cost and tokens:** per agent run, per task and in total (input, output, cache, USD),
  and how many agents are running out of the allowed maximum.
- **Milestones:** each issue's GitHub milestone (for example "M6 - Token and Cost
  Tracking") becomes a milestone in the orchestrator, created on first use. A panel
  shows progress, failures, tokens and cost per milestone, and each task card shows a
  milestone chip. Issues without a milestone go to a default "GitHub issues" group.
- **Verification:** each check (unit tests, ruff check, ruff format) with its output.
- **GitHub links:** the issue, each agent's branch and the pull request.

Add `?theme=dark` or `?theme=light` to override the OS theme. The data comes from
`GET /dashboard/overview`. The dashboard has no login, so only run it on a trusted
machine or network.

## Demo (live: real GitHub + real Claude)

`demo/run_live.py` runs the orchestrator against this repository's GitHub project.
Issues labelled `agent-task` are picked up automatically. For each issue, three
Claude agents run one after another, **each in its own branch and Git worktree**:

| Agent | Branch | Starts from |
|---|---|---|
| builder (sonnet/high) | `agent/task-N` | `main` |
| tester (haiku/medium) | `agent/task-N-tester` | builder's commit |
| reviewer (opus/high) | `agent/task-N-reviewer` | tester's commit |

The orchestrator commits each agent's work (agents never commit), pushes each
branch to `origin`, runs the pytest/ruff gate after the tester, and - when the
reviewer ends with `VERDICT: APPROVE` - opens a pull request from the tester
branch into `main` with `Closes #<issue>`. It comments progress and a cost summary
on the issue. It never merges anything.
If the tester, the verification checks or the reviewer find a problem, the builder
receives the report and fixes it in a new branch (`agent/task-N-builder-r2`, then
`-tester-r2`, `-reviewer-r2`), and the stages run again - up to `MAX_FIX_ATTEMPTS`
times (default 2, `--fix-attempts` in the live runner, 0 turns it off). Every attempt
costs agent runs, so the per-task cost can be up to three times higher.

Tasks for different issues run in
parallel (`MAX_CONCURRENT_AGENTS`), and you can pause/resume/cancel any of them.

Prerequisites: the `claude` CLI and local `main` pushed to `origin` (agents branch
from local `main`). For GitHub access use either the `gh` CLI (`gh auth login`) or
no CLI at all: `--backend rest` calls the GitHub API directly. On a public repo
that needs no token to read issues; branches are pushed with your normal git
credentials. Set `GITHUB_TOKEN` (fine-grained, Issues + Pull requests: write) to
also open real PRs, comment on issues and create labels/issues from the script;
without it you get a compare link instead of a PR.

```
python demo/run_live.py --create-label
python demo/run_live.py --create-issue "Add a module docstring to exceptions.py"
python demo/run_live.py                 # start the orchestrator and watch
```

Useful switches: `--no-publish` (no pushes), `--no-pr`, `--no-comments`,
`--budget 1.0` (USD cap per agent run), `--poll 15`. It costs money and writes to
GitHub, so it asks for confirmation first (`--yes` skips that).

### Offline mode (no GitHub, no Claude, no cost)

`demo/run_demo.py` runs the same pipeline against a throwaway local repo with a
stub standing in for Claude (`demo/fake_claude.py`) and issues from
`demo/issues.json`. It also demonstrates pause, resume and cancel. Issue #104
contains `DEMO:REJECT` so the stub reviewer rejects it.

```
python demo/run_demo.py [--delay 10] [--keep]
```

### API summary

| Endpoint | Purpose |
|---|---|
| `POST /projects`, `POST /projects/{id}/milestones` | Create projects and milestones |
| `POST /milestones/{id}/sync` | Import open issues as queued tasks (idempotent) |
| `POST /tasks/{id}/pipeline` | Run builder -> tester -> reviewer for a task |
| `POST /tasks/{id}/run` | Run a single builder agent (original behavior) |
| `POST /tasks/{id}/pause`, `/resume`, `/cancel` | Control a running task |
| `GET /tasks`, `GET /tasks/{id}`, `GET /agents/active` | Monitor tasks and agents |
| `GET /tasks/{id}/usage`, `/tasks/{id}/runs`, `/milestones/{id}/usage` | Tokens and cost |

Pause, resume and cancel act on in-memory process state, so they only work against
the API process that started the agents.

## Live GitHub mode

The orchestrator can watch the GitHub repo it lives in and run agents on new issues.

1. Install and sign in to the GitHub CLI: `gh auth login`.
2. Make sure local `main` contains `.claude/agents/{builder,tester,reviewer}.md`
   (worktrees are created from local `main`).
3. Create a label named `agent-task`. Only issues with this label are picked up.
4. Set in `.env`:
   ```
   REPOSITORY_PATH=<path to your local clone of the repo>
   ISSUE_SOURCE=gh
   POLL_INTERVAL_SECONDS=60
   CLAUDE_COMMAND=claude
   MAX_TASK_COST_USD=2.0
   MAX_CONCURRENT_AGENTS=1
   # COMMENT_ON_ISSUES=true   # optional, writes comments to GitHub
   ```
5. Start the API: `python -m uvicorn orchestrator.app.main:app`.
   Label an issue `agent-task` and within one poll interval it becomes a task and
   its builder -> tester -> reviewer pipeline starts. Watch it with `GET /tasks`
   and `GET /agents/active`.

Agents work in `agent/task-N` branches in worktrees next to the clone and never
push; review and merge their output yourself.
