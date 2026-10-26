# Builds the dashboard overview from the database in one snapshot.
# Works out each task's stage states, branches, usage and milestone totals.
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from orchestrator.app.api.schemas import AgentRunUsageResponse
from orchestrator.app.dashboard.schemas import (
    BranchInfo,
    DashboardCheck,
    DashboardMilestone,
    DashboardOverview,
    DashboardProject,
    DashboardSummary,
    DashboardTask,
    StageInfo,
    TaskActions,
    UsageTotals,
)
from orchestrator.app.db.models import (
    CANCELLABLE_STATUSES,
    FINISHED_STATUSES,
    PAUSABLE_STATUSES,
    AgentRun,
    Milestone,
    Project,
    Task,
    TaskStatus,
)

AGENT_ROLES = ("builder", "tester", "reviewer")

STAGE_LABELS = {
    "builder": "Builder",
    "tester": "Tester",
    "checks": "Checks",
    "reviewer": "Reviewer",
    "pull_request": "Pull request",
}

ACTIVE_RUN_STATUSES = ("STARTING", "RUNNING", "PAUSED")

_RUN_STATE = {
    "STARTING": "running",
    "RUNNING": "running",
    "PAUSED": "paused",
    "COMPLETED": "done",
    "FAILED": "failed",
    "CANCELLED": "cancelled",
}


def _agent_stage(task: Task, role: str, runs: list[AgentRun]) -> StageInfo:
    role_runs = [run for run in runs if run.agent_role == role]
    label = STAGE_LABELS[role]

    if not role_runs:
        if task.status in FINISHED_STATUSES:
            return StageInfo(name=role, label=label, state="skipped")
        if task.status == TaskStatus.PAUSED and task.current_stage == role:
            return StageInfo(name=role, label=label, state="paused")
        return StageInfo(name=role, label=label, state="pending")

    latest = max(role_runs, key=lambda run: run.id)
    state = _RUN_STATE.get(latest.status, "pending")

    # The builder was sent back to fix a problem, so this stage has to run again.
    if role != "builder" and _fix_in_progress(runs, latest):
        state = "pending"

    # The reviewer finished but the task still failed: it rejected the change.
    if role == "reviewer" and state == "done" and task.status == TaskStatus.FAILED:
        state = "rejected"

    detail = "/".join(part for part in (latest.model, latest.effort) if part)
    if len(role_runs) > 1:
        detail += f" · attempt {len(role_runs)}"
    return StageInfo(name=role, label=label, state=state, detail=detail or None)


def _fix_in_progress(runs: list[AgentRun], latest: AgentRun) -> bool:
    """True when a builder run started after `latest` (a fix is under way)."""
    return any(run.agent_role == "builder" and run.id > latest.id for run in runs)


def _latest_checks(task: Task) -> list[DashboardCheck]:
    latest: dict[str, DashboardCheck] = {}

    for result in sorted(task.verification_results, key=lambda item: item.id):
        latest[result.check_name] = DashboardCheck(
            check_name=result.check_name,
            passed=result.passed,
            created_at=result.created_at,
        )

    return list(latest.values())


def _checks_stage(
    task: Task, checks: list[DashboardCheck], runs: list[AgentRun]
) -> StageInfo:
    label = STAGE_LABELS["checks"]

    if task.status == TaskStatus.VERIFYING:
        return StageInfo(name="checks", label=label, state="running")

    # A newer builder run than the last tester run means the checks are stale.
    testers = [run for run in runs if run.agent_role == "tester"]
    if testers and _fix_in_progress(runs, max(testers, key=lambda run: run.id)):
        return StageInfo(name="checks", label=label, state="pending")

    if not checks:
        state = "skipped" if task.status in FINISHED_STATUSES else "pending"
        return StageInfo(name="checks", label=label, state=state)

    passed = sum(check.passed for check in checks)
    state = "done" if passed == len(checks) else "failed"
    return StageInfo(
        name="checks", label=label, state=state, detail=f"{passed}/{len(checks)} passed"
    )


def _pull_request_stage(task: Task) -> StageInfo:
    label = STAGE_LABELS["pull_request"]

    if task.pull_request_url:
        return StageInfo(
            name="pull_request", label=label, state="done", detail=task.pull_request_url
        )

    state = "skipped" if task.status in FINISHED_STATUSES else "pending"
    return StageInfo(name="pull_request", label=label, state=state)


def _usage(runs: list[AgentRun]) -> UsageTotals:
    return UsageTotals(
        input_tokens=sum(run.input_tokens or 0 for run in runs),
        output_tokens=sum(run.output_tokens or 0 for run in runs),
        cache_creation_input_tokens=sum(
            run.cache_creation_input_tokens or 0 for run in runs
        ),
        cache_read_input_tokens=sum(run.cache_read_input_tokens or 0 for run in runs),
        cost_usd=sum(run.reported_cost_usd or 0.0 for run in runs),
    )


def _branches(task: Task, runs: list[AgentRun], repo: str | None) -> list[BranchInfo]:
    """The task's one branch, which every agent works in; shown once work began."""
    if not task.branch_name or not runs:
        return []

    return [
        BranchInfo(
            role="branch",
            name=task.branch_name,
            url=(
                f"https://github.com/{repo}/tree/{task.branch_name}" if repo else None
            ),
        )
    ]


def _task_view(task: Task, repo: str | None) -> DashboardTask:
    runs = sorted(task.agent_runs, key=lambda run: run.id)
    checks = _latest_checks(task)

    stages = [
        _agent_stage(task, "builder", runs),
        _agent_stage(task, "tester", runs),
        _checks_stage(task, checks, runs),
        _agent_stage(task, "reviewer", runs),
        _pull_request_stage(task),
    ]

    issue_url = None
    if repo and task.github_issue_number:
        issue_url = f"https://github.com/{repo}/issues/{task.github_issue_number}"

    return DashboardTask(
        id=task.id,
        title=task.title,
        github_issue_number=task.github_issue_number,
        issue_url=issue_url,
        status=task.status,
        milestone=task.milestone.code if task.milestone else None,
        board_column=task.board_column,
        project=task.milestone.project.name if task.milestone else None,
        current_stage=task.current_stage,
        pull_request_url=task.pull_request_url,
        created_at=task.created_at,
        stages=stages,
        branches=_branches(task, runs, repo),
        runs=[AgentRunUsageResponse.model_validate(run) for run in runs],
        checks=checks,
        usage=_usage(runs),
        actions=TaskActions(
            start=task.status == TaskStatus.QUEUED,
            pause=task.status in PAUSABLE_STATUSES,
            resume=task.status == TaskStatus.PAUSED,
            cancel=task.status in CANCELLABLE_STATUSES,
        ),
    )


def _projects(session: Session) -> list[DashboardProject]:
    """Totals per project, over every task (not only the listed ones)."""
    active_statuses = PAUSABLE_STATUSES | {TaskStatus.PAUSED}
    projects = []

    for project in session.scalars(
        select(Project)
        .options(
            selectinload(Project.milestones)
            .selectinload(Milestone.tasks)
            .selectinload(Task.agent_runs)
        )
        .order_by(Project.id)
    ):
        tasks = [task for ms in project.milestones for task in ms.tasks]

        if not tasks:
            continue

        columns: dict[str, int] = {}
        for task in tasks:
            if task.board_column:
                columns[task.board_column] = columns.get(task.board_column, 0) + 1

        numbers = [task.board_number for task in tasks if task.board_number]
        projects.append(
            DashboardProject(
                name=project.name,
                board_number=max(set(numbers), key=numbers.count) if numbers else None,
                task_count=len(tasks),
                active_count=sum(task.status in active_statuses for task in tasks),
                completed_count=sum(
                    task.status == TaskStatus.COMPLETED for task in tasks
                ),
                failed_count=sum(
                    task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED)
                    for task in tasks
                ),
                columns=columns,
                usage=_usage([run for task in tasks for run in task.agent_runs]),
            )
        )

    return projects


def build_overview(
    session: Session,
    *,
    repo: str | None,
    max_concurrent_agents: int,
    polling_enabled: bool,
    limit: int = 50,
) -> DashboardOverview:
    """Everything the dashboard shows, in one query-light snapshot."""
    tasks = list(
        session.scalars(
            select(Task)
            .options(
                selectinload(Task.milestone).selectinload(Milestone.project),
                selectinload(Task.agent_runs),
                selectinload(Task.verification_results),
            )
            .order_by(Task.id.desc())
            .limit(limit)
        )
    )
    views = [_task_view(task, repo) for task in tasks]

    status_counts: dict[str, int] = {}
    for view in views:
        status_counts[view.status.value] = status_counts.get(view.status.value, 0) + 1

    all_runs = [run for task in tasks for run in task.agent_runs]
    active_agents = sum(run.status in ACTIVE_RUN_STATUSES for run in all_runs)

    milestones = []
    for milestone in session.scalars(
        select(Milestone)
        .options(
            selectinload(Milestone.project),
            selectinload(Milestone.tasks).selectinload(Task.agent_runs),
        )
        .order_by(Milestone.id)
    ):
        milestone_tasks = milestone.tasks
        milestones.append(
            DashboardMilestone(
                id=milestone.id,
                project=milestone.project.name,
                code=milestone.code,
                title=milestone.title,
                task_count=len(milestone_tasks),
                completed_count=sum(
                    task.status == TaskStatus.COMPLETED for task in milestone_tasks
                ),
                failed_count=sum(
                    task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED)
                    for task in milestone_tasks
                ),
                usage=_usage([run for t in milestone_tasks for run in t.agent_runs]),
            )
        )

    return DashboardOverview(
        projects=_projects(session),
        generated_at=datetime.now(UTC),
        repo=repo,
        summary=DashboardSummary(
            status_counts=status_counts,
            active_agents=active_agents,
            max_concurrent_agents=max_concurrent_agents,
            usage=_usage(all_runs),
            polling_enabled=polling_enabled,
        ),
        milestones=milestones,
        tasks=views,
    )
