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
## Demo

The demo imports issues as tasks, then runs a **builder -> tester -> reviewer**
pipeline for each one in parallel, each task in its own branch and Git worktree.
It shows live status, per-role model/effort routing, pause/resume, cancel,
token and cost tracking, and verification results.

```
pip install -r requirements.txt
python demo/run_demo.py            # offline, free (uses demo/fake_claude.py)
python demo/run_demo.py --delay 10 # slower agents, easier to watch
python demo/run_demo.py --keep     # leave the API running at http://127.0.0.1:8765/docs
```

Issues come from `demo/issues.json` (same shape as `gh issue list --json`).
Issue #104 contains `DEMO:REJECT`, which makes the stub reviewer reject it so you
can see the review gate fail a task. To use real GitHub issues set
`ISSUE_SOURCE=gh` (requires an authenticated `gh` CLI; optional `GITHUB_REPO`).

Real Claude Code (costs money): `python demo/run_demo.py --real --issues <file>`.
Set `MAX_TASK_COST_USD` as a per-run budget guardrail.

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
