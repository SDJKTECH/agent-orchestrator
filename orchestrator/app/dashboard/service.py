# Builds the dashboard overview from the database in one snapshot.
# Works out each task's stage states, branches, usage and milestone totals.
from datetime import UTC, datetime
from functools import partial

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session, selectinload

from orchestrator.app.core.config import get_settings
from orchestrator.app.dashboard.schemas import (
    ActivityEvent,
    BranchInfo,
    DashboardCheck,
    DashboardMilestone,
    DashboardOverview,
    DashboardProject,
    DashboardRun,
    DashboardSummary,
    DashboardTask,
    HierarchyNode,
    RoleUsage,
    RunDetail,
    StageInfo,
    TaskActions,
    UsageTotals,
    WorkItem,
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
from orchestrator.app.jira.models import JiraItem
from orchestrator.app.planning.dependencies import get_blockers

AGENT_ROLES = ("builder", "tester", "reviewer")

STAGE_LABELS = {
    "builder": "Builder",
    "tester": "Tester",
    "checks": "Checks",
    "reviewer": "Reviewer",
    "pull_request": "Pull request",
}

ACTIVE_RUN_STATUSES = ("STARTING", "RUNNING", "PAUSED")

MAX_ACTIVITY_EVENTS = 40
# Check results saved within this many seconds belong to one run of the checks.
CHECK_BATCH_SECONDS = 10

_RUN_STATE = {
    "STARTING": "running",
    "RUNNING": "running",
    "PAUSED": "paused",
    "COMPLETED": "done",
    "FAILED": "failed",
    "CANCELLED": "cancelled",
}


def _run_view(run: AgentRun) -> DashboardRun:
    """Turn an agent run row into the dashboard's run model."""
    view = DashboardRun.model_validate(run)
    return view.model_copy(update={"has_result": bool(run.result_text)})


def run_detail(session: Session, run_id: int) -> RunDetail | None:
    """One run with the agent's final message, or None when it does not exist."""
    run = session.get(AgentRun, run_id)

    if run is None:
        return None

    task = run.task
    fields = _run_view(run).model_dump()
    return RunDetail(
        **fields,
        task_title=task.title,
        jira_key=task.jira_key,
        result_text=run.result_text,
    )


def _seconds(run: AgentRun) -> float:
    """How long a finished run took, in seconds (0 if unfinished)."""
    if run.started_at is None or run.finished_at is None:
        return 0.0
    return max((run.finished_at - run.started_at).total_seconds(), 0.0)


def _role_usage(runs: list[AgentRun]) -> list[RoleUsage]:
    """Runs, tokens, cost and time per agent role, builder first."""
    roles = []

    for role in AGENT_ROLES:
        role_runs = [run for run in runs if run.agent_role == role]

        if role_runs:
            totals = _usage(role_runs)
            roles.append(
                RoleUsage(
                    role=role,
                    runs=len(role_runs),
                    active=sum(run.status in ACTIVE_RUN_STATUSES for run in role_runs),
                    input_tokens=totals.input_tokens,
                    output_tokens=totals.output_tokens,
                    cost_usd=totals.cost_usd,
                    seconds=sum(_seconds(run) for run in role_runs),
                )
            )

    return roles


def _agent_stage(task: Task, role: str, runs: list[AgentRun]) -> StageInfo:
    """State of the builder, tester or reviewer stage, from the task and its runs."""
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
    """The newest result of each verification check of the task."""
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
    """State of the checks stage; stale if the builder ran after the last tester."""
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
    """State of the pull request stage; done once the task has a PR URL."""
    label = STAGE_LABELS["pull_request"]

    if task.pull_request_url:
        return StageInfo(
            name="pull_request", label=label, state="done", detail=task.pull_request_url
        )

    state = "skipped" if task.status in FINISHED_STATUSES else "pending"
    return StageInfo(name="pull_request", label=label, state=state)


def _usage(runs: list[AgentRun]) -> UsageTotals:
    """Add up tokens and cost over the given runs."""
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


def _jira_url(key: str | None) -> str | None:
    """The web address of a Jira issue, when Jira is configured."""
    base = get_settings().jira_base_url
    return f"{base.rstrip('/')}/browse/{key}" if key and base else None


def _hierarchy(task: Task, items: dict[str, JiraItem]) -> list[HierarchyNode]:
    """The task's Epic and Story, outermost first (empty without a Jira record)."""
    item = items.get(task.jira_key) if task.jira_key else None
    chain: list[HierarchyNode] = []
    seen = {task.jira_key}
    key = item.parent_key if item else None

    while key and key not in seen and key in items:
        parent = items[key]
        chain.append(
            HierarchyNode(
                key=parent.key,
                type=parent.type,
                title=parent.summary,
                url=_jira_url(parent.key),
            )
        )
        seen.add(key)
        key = parent.parent_key

    chain.reverse()
    return chain


def _work_items(items: dict[str, JiraItem], task_ids: dict[str, int]) -> list[WorkItem]:
    """Every Jira issue on record, with the orchestrator task that builds it."""
    return [
        WorkItem(
            key=item.key,
            type=item.type,
            title=item.summary,
            parent_key=item.parent_key,
            url=_jira_url(item.key),
            task_id=task_ids.get(item.key),
        )
        for item in sorted(items.values(), key=lambda item: item.id)
    ]


def _utc(moment: datetime) -> datetime:
    """The time as naive UTC, which is how the database and the page treat it."""
    if moment.tzinfo is not None:
        return moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def _short(seconds: float) -> str:
    """A run time like '45s' or '3m 12s'."""
    whole = round(seconds)
    return f"{whole}s" if whole < 60 else f"{whole // 60}m {whole % 60}s"


def _event(
    events: list[ActivityEvent], task: Task, at: datetime, kind: str, text: str
) -> None:
    """Append one activity event for `task`."""
    events.append(
        ActivityEvent(
            at=_utc(at),
            kind=kind,
            task_id=task.id,
            label=task.jira_key or f"#{task.id}",
            text=text,
        )
    )


def _activity(tasks: list[Task]) -> list[ActivityEvent]:
    """What happened lately, newest first: tasks added, agents started and finished,
    and checks run. Built from rows already saved, so it survives restarts."""
    events: list[ActivityEvent] = []

    for task in tasks:
        add = partial(_event, events, task)

        add(task.created_at, "added", "Task added")

        for run in task.agent_runs:
            role = STAGE_LABELS.get(run.agent_role, run.agent_role)

            if run.started_at:
                add(run.started_at, "started", f"{role} started")

            if run.finished_at:
                took = _short(_seconds(run))
                cost = f"${run.reported_cost_usd:.2f}" if run.reported_cost_usd else ""
                detail = " · ".join(part for part in (took, cost) if part)
                outcome = run.status.lower()

                if outcome == "completed":
                    add(run.finished_at, "finished", f"{role} finished · {detail}")
                else:
                    add(run.finished_at, "stopped", f"{role} {outcome} · {detail}")

        batches: list[list] = []
        for result in sorted(task.verification_results, key=lambda r: r.id):
            last = batches[-1][-1] if batches else None
            gap = (
                (_utc(result.created_at) - _utc(last.created_at)).total_seconds()
                if last
                else None
            )
            if gap is not None and gap <= CHECK_BATCH_SECONDS:
                batches[-1].append(result)
            else:
                batches.append([result])

        for batch in batches:
            passed = sum(result.passed for result in batch)
            failed = [r.check_name for r in batch if not r.passed]
            add(
                batch[-1].created_at,
                "checks_ok" if not failed else "checks_failed",
                f"Checks passed · {passed}/{len(batch)}"
                if not failed
                else f"Checks failed · {', '.join(failed)}",
            )

    events.sort(key=lambda event: event.at, reverse=True)
    return events[:MAX_ACTIVITY_EVENTS]


def _task_view(
    task: Task, repo: str | None, items: dict[str, JiraItem] | None = None
) -> DashboardTask:
    """Build the dashboard model of one task: stages, checks, usage and actions."""
    items = items or {}
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

    jira_url = _jira_url(task.jira_key)
    own_item = items.get(task.jira_key) if task.jira_key else None

    session = object_session(task)
    waiting = []
    for entry in get_blockers(task):
        blocker = session.get(Task, entry["task_id"]) if session else None
        waiting.append(
            blocker.jira_key or f"#{blocker.id}" if blocker else str(entry["task_id"])
        )

    return DashboardTask(
        id=task.id,
        title=task.title,
        github_issue_number=task.github_issue_number,
        issue_url=issue_url,
        jira_key=task.jira_key,
        jira_url=jira_url,
        jira_type=own_item.type if own_item else None,
        hierarchy=_hierarchy(task, items),
        blocked_by=waiting,
        status=task.status,
        milestone=task.milestone.code if task.milestone else None,
        board_column=task.board_column,
        project=task.milestone.project.name if task.milestone else None,
        current_stage=task.current_stage,
        pull_request_url=task.pull_request_url,
        created_at=task.created_at,
        stages=stages,
        branches=_branches(task, runs, repo),
        runs=[_run_view(run) for run in runs],
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
    items = {item.key: item for item in session.scalars(select(JiraItem))}
    views = [_task_view(task, repo, items) for task in tasks]

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
            roles=_role_usage(all_runs),
            polling_enabled=polling_enabled,
        ),
        milestones=milestones,
        tasks=views,
        work_items=_work_items(
            items, {task.jira_key: task.id for task in tasks if task.jira_key}
        ),
        activity=_activity(tasks),
    )
