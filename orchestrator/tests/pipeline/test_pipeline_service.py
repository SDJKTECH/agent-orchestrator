import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orchestrator.app.agents.agent_runner import AgentExecutionResult
from orchestrator.app.agents.agent_service import AgentService
from orchestrator.app.agents.process_control import ProcessControl
from orchestrator.app.core.exceptions import AgentExecutionError
from orchestrator.app.db.models import AgentRun, Task, TaskStatus
from orchestrator.app.pipeline.pipeline_service import (
    PipelineService,
    extract_result_text,
    parse_verdict,
)


def ok(text: str = "done") -> AgentExecutionResult:
    return AgentExecutionResult(
        return_code=0,
        stdout=json.dumps(
            {
                "result": text,
                "total_cost_usd": 0.01,
                "usage": {"input_tokens": 5, "output_tokens": 7},
            }
        ),
        stderr="",
        process_id=1,
    )


def passing_checks():
    return [
        SimpleNamespace(check_name=name, passed=True, details="ok")
        for name in ("unit_tests", "ruff_check", "ruff_format")
    ]


@pytest.fixture
def pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    worktree = tmp_path / "worktree"
    agents_dir = worktree / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    for role in ("builder", "tester", "reviewer"):
        (agents_dir / f"{role}.md").write_text(
            f"---\nname: {role}\ndescription: test\n---\n", encoding="utf-8"
        )

    task = Task(
        id=1,
        title="Add a health endpoint",
        description="Return a status payload.",
        status=TaskStatus.QUEUED,
        worktree_path=str(worktree),
    )
    db = MagicMock()
    db.get.return_value = task

    settings = SimpleNamespace(
        claude_command="claude",
        max_task_cost_usd=None,
        agent_timeout_seconds=60,
        verification_timeout_seconds=300,
        verification_ruff_target="orchestrator",
        default_model="sonnet",
        default_effort="medium",
        builder_model=None,
        builder_effort="high",
        tester_model="haiku",
        tester_effort="medium",
        reviewer_model="opus",
        reviewer_effort="high",
    )
    monkeypatch.setattr(
        "orchestrator.app.agents.agent_service.get_settings", lambda: settings
    )

    results = {
        "builder": ok("built"),
        "tester": ok("tested"),
        "reviewer": ok("Looks good.\nVERDICT: APPROVE - fine"),
    }
    calls: list[str] = []

    def run(command, **kwargs):
        role = command[command.index("--agent") + 1]
        calls.append(role)
        outcome = results[role]
        return outcome() if callable(outcome) else outcome

    runner = MagicMock()
    runner.run.side_effect = run

    verification_runner = MagicMock()
    verification_runner.run_checks.return_value = passing_checks()

    control = ProcessControl()
    agent_service = AgentService(
        db=db,
        runner=runner,
        verification_runner=verification_runner,
        control=control,
    )

    return SimpleNamespace(
        service=PipelineService(db=db, agent_service=agent_service, control=control),
        db=db,
        task=task,
        results=results,
        calls=calls,
        runner=runner,
        verification_runner=verification_runner,
        control=control,
    )


def added_runs(db) -> list[AgentRun]:
    return [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], AgentRun)
    ]


def test_pipeline_runs_all_stages_and_completes(pipeline) -> None:
    task = pipeline.service.run(1)

    assert task.status == TaskStatus.COMPLETED
    assert task.current_stage == "reviewer"
    assert pipeline.calls == ["builder", "tester", "reviewer"]
    pipeline.verification_runner.run_checks.assert_called_once()
    assert [run.status for run in added_runs(pipeline.db)] == ["COMPLETED"] * 3


def test_pipeline_routes_model_and_effort_per_role(pipeline) -> None:
    pipeline.service.run(1)

    runs = {run.agent_role: run for run in added_runs(pipeline.db)}

    assert (runs["builder"].model, runs["builder"].effort) == ("sonnet", "high")
    assert (runs["tester"].model, runs["tester"].effort) == ("haiku", "medium")
    assert (runs["reviewer"].model, runs["reviewer"].effort) == ("opus", "high")


def test_pipeline_records_usage_for_every_stage(pipeline) -> None:
    pipeline.service.run(1)

    runs = added_runs(pipeline.db)

    assert [run.input_tokens for run in runs] == [5, 5, 5]
    assert [run.output_tokens for run in runs] == [7, 7, 7]
    assert [run.reported_cost_usd for run in runs] == [0.01] * 3


def test_pipeline_prompts_are_role_specific(pipeline) -> None:
    pipeline.service.run(1)

    prompts = {
        call.args[0][call.args[0].index("--agent") + 1]: call.args[0][-1]
        for call in pipeline.runner.run.call_args_list
    }

    assert "Add a health endpoint" in prompts["builder"]
    assert "Do not write tests" in prompts["builder"]
    assert "Modify test files only" in prompts["tester"]
    assert "VERDICT: APPROVE" in prompts["reviewer"]


def test_pipeline_stops_when_builder_fails(pipeline) -> None:
    pipeline.results["builder"] = AgentExecutionResult(1, "", "boom", None)

    task = pipeline.service.run(1)

    assert task.status == TaskStatus.FAILED
    assert pipeline.calls == ["builder"]
    pipeline.verification_runner.run_checks.assert_not_called()


def test_pipeline_fails_when_verification_fails(pipeline) -> None:
    pipeline.verification_runner.run_checks.return_value = [
        SimpleNamespace(check_name="unit_tests", passed=False, details="1 failed")
    ]

    task = pipeline.service.run(1)

    assert task.status == TaskStatus.FAILED
    assert pipeline.calls == ["builder", "tester"]


def test_pipeline_fails_when_reviewer_rejects(pipeline) -> None:
    pipeline.results["reviewer"] = ok("Problems found.\nVERDICT: REJECT - bug")

    task = pipeline.service.run(1)

    assert task.status == TaskStatus.FAILED
    assert pipeline.calls == ["builder", "tester", "reviewer"]


def test_pipeline_fails_when_reviewer_gives_no_verdict(pipeline) -> None:
    pipeline.results["reviewer"] = ok("Seems fine to me.")

    assert pipeline.service.run(1).status == TaskStatus.FAILED


def test_pipeline_marks_task_failed_when_stage_raises(pipeline) -> None:
    def explode():
        raise AgentExecutionError("Process timed out")

    pipeline.results["tester"] = explode

    with pytest.raises(AgentExecutionError, match="timed out"):
        pipeline.service.run(1)

    assert pipeline.task.status == TaskStatus.FAILED


def test_pipeline_rejects_task_that_cannot_run(pipeline) -> None:
    pipeline.task.status = TaskStatus.COMPLETED

    with pytest.raises(ValueError, match="cannot run"):
        pipeline.service.run(1)

    assert pipeline.calls == []


def test_pipeline_cancelled_before_start_runs_nothing(pipeline) -> None:
    pipeline.control.cancel(1)

    task = pipeline.service.run(1)

    assert task.status == TaskStatus.CANCELLED
    assert pipeline.calls == []


def test_pipeline_cancel_during_stage_stops_pipeline(pipeline) -> None:
    def cancelled_builder():
        pipeline.control.cancel(1)
        return AgentExecutionResult(1, "", "", 1)

    pipeline.results["builder"] = cancelled_builder

    task = pipeline.service.run(1)

    assert task.status == TaskStatus.CANCELLED
    assert pipeline.calls == ["builder"]
    assert added_runs(pipeline.db)[0].status == "CANCELLED"


def test_pipeline_cancel_after_stage_skips_remaining_stages(pipeline) -> None:
    def builder():
        outcome = ok("built")
        pipeline.control.pause(1)
        threading.Timer(0.2, pipeline.control.cancel, args=(1,)).start()
        return outcome

    pipeline.results["builder"] = builder

    task = pipeline.service.run(1)

    assert task.status == TaskStatus.CANCELLED
    assert pipeline.calls == ["builder"]


def test_pipeline_waits_while_paused_then_continues(pipeline) -> None:
    def builder():
        pipeline.control.pause(1)
        threading.Timer(0.3, pipeline.control.resume, args=(1,)).start()
        return ok("built")

    pipeline.results["builder"] = builder

    task = pipeline.service.run(1)

    assert task.status == TaskStatus.COMPLETED
    assert pipeline.calls == ["builder", "tester", "reviewer"]


def test_parse_verdict_uses_last_verdict_in_json_result() -> None:
    stdout = json.dumps({"result": "VERDICT: REJECT early\n...\nverdict: approve"})

    assert parse_verdict(stdout) == "APPROVE"


def test_parse_verdict_handles_plain_text_and_missing() -> None:
    assert parse_verdict("VERDICT: REJECT") == "REJECT"
    assert parse_verdict("nothing here") is None
    assert parse_verdict("") is None


def test_extract_result_text_falls_back_to_raw_output() -> None:
    assert extract_result_text("plain") == "plain"
    assert extract_result_text('{"other": 1}') == '{"other": 1}'
    assert extract_result_text(json.dumps({"result": "text"})) == "text"


def test_pipeline_does_not_open_pull_request_when_reviewer_rejects(pipeline) -> None:
    from orchestrator.app.pipeline.stage_isolation import StageOutcome, StageWorkspace

    isolation = MagicMock()
    isolation.prepare.side_effect = lambda task, role: StageWorkspace(
        role, "agent/task-1", Path(task.worktree_path)
    )
    isolation.finish.return_value = StageOutcome(committed=True, pushed=True)
    publisher = MagicMock()
    service = PipelineService(
        db=pipeline.db,
        agent_service=pipeline.service.agents,
        control=pipeline.control,
        isolation=isolation,
        publisher=publisher,
    )
    pipeline.results["reviewer"] = ok("Bad.\nVERDICT: REJECT - bug")

    task = service.run(1)

    assert task.status == TaskStatus.FAILED
    assert isolation.finish.call_count == 3
    publisher.open_pull_request.assert_not_called()


def test_pipeline_opens_pull_request_from_the_task_branch_when_approved(
    pipeline,
) -> None:
    from orchestrator.app.pipeline.stage_isolation import StageOutcome, StageWorkspace

    isolation = MagicMock()
    isolation.prepare.side_effect = lambda task, role: StageWorkspace(
        role, "agent/task-1", Path(task.worktree_path)
    )
    isolation.finish.return_value = StageOutcome(committed=True, pushed=True)
    publisher = MagicMock()
    publisher.open_pull_request.return_value = "https://example/pull/1"
    service = PipelineService(
        db=pipeline.db,
        agent_service=pipeline.service.agents,
        control=pipeline.control,
        isolation=isolation,
        publisher=publisher,
    )

    task = service.run(1)

    assert task.status == TaskStatus.COMPLETED
    assert task.pull_request_url == "https://example/pull/1"
    publisher.open_pull_request.assert_called_once_with(task, "agent/task-1")


def test_pipeline_skips_pull_request_when_branch_was_not_pushed(pipeline) -> None:
    from orchestrator.app.pipeline.stage_isolation import StageOutcome, StageWorkspace

    isolation = MagicMock()
    isolation.prepare.side_effect = lambda task, role: StageWorkspace(
        role, "agent/task-1", Path(task.worktree_path)
    )
    isolation.finish.return_value = StageOutcome(committed=True, pushed=False)
    publisher = MagicMock()
    service = PipelineService(
        db=pipeline.db,
        agent_service=pipeline.service.agents,
        control=pipeline.control,
        isolation=isolation,
        publisher=publisher,
    )

    assert service.run(1).status == TaskStatus.COMPLETED
    publisher.open_pull_request.assert_not_called()


def with_fixes(pipeline, attempts: int, isolation=None) -> PipelineService:
    return PipelineService(
        db=pipeline.db,
        agent_service=pipeline.service.agents,
        control=pipeline.control,
        isolation=isolation,
        max_fix_attempts=attempts,
    )


def failing_check(details: str = "1 failed: test_x"):
    return [SimpleNamespace(check_name="unit_tests", passed=False, details=details)]


def test_failed_checks_send_the_task_back_to_the_builder(pipeline) -> None:
    pipeline.verification_runner.run_checks.side_effect = [
        failing_check(),
        passing_checks(),
    ]

    task = with_fixes(pipeline, 2).run(1)

    assert task.status == TaskStatus.COMPLETED
    assert pipeline.calls == [
        "builder",
        "tester",
        "builder",
        "tester",
        "reviewer",
    ]


def test_fix_prompt_contains_the_failure_report(pipeline) -> None:
    pipeline.verification_runner.run_checks.side_effect = [
        failing_check("AssertionError in test_x"),
        passing_checks(),
    ]
    prompts: list[str] = []
    original = pipeline.runner.run.side_effect

    def spy(command, **kwargs):
        prompts.append(command[-1])
        return original(command, **kwargs)

    pipeline.runner.run.side_effect = spy

    with_fixes(pipeline, 1).run(1)

    fix_prompt = next(p for p in prompts if "problem was found" in p)
    assert "[unit_tests]" in fix_prompt
    assert "AssertionError in test_x" in fix_prompt


def test_reviewer_rejection_is_fixed_then_reviewed_again(pipeline) -> None:
    verdicts = iter(
        [ok("Bug.\nVERDICT: REJECT - bug"), ok("Fixed.\nVERDICT: APPROVE - ok")]
    )
    pipeline.results["reviewer"] = lambda: next(verdicts)

    task = with_fixes(pipeline, 1).run(1)

    assert task.status == TaskStatus.COMPLETED
    assert pipeline.calls == [
        "builder",
        "tester",
        "reviewer",
        "builder",
        "tester",
        "reviewer",
    ]


def test_tester_failure_is_retried_through_the_builder(pipeline) -> None:
    broken = AgentExecutionResult(return_code=1, stdout="", stderr="boom", process_id=1)
    outcomes = iter([broken, ok("tested")])
    pipeline.results["tester"] = lambda: next(outcomes)

    task = with_fixes(pipeline, 1).run(1)

    assert task.status == TaskStatus.COMPLETED
    assert pipeline.calls == ["builder", "tester", "builder", "tester", "reviewer"]


def test_pipeline_gives_up_after_the_fix_attempts_are_used(pipeline) -> None:
    pipeline.verification_runner.run_checks.return_value = failing_check()

    task = with_fixes(pipeline, 2).run(1)

    assert task.status == TaskStatus.FAILED
    assert pipeline.calls == ["builder", "tester"] * 3
    assert "reviewer" not in pipeline.calls


def test_builder_failure_is_not_retried(pipeline) -> None:
    pipeline.results["builder"] = AgentExecutionResult(
        return_code=1, stdout="", stderr="crash", process_id=1
    )

    task = with_fixes(pipeline, 2).run(1)

    assert task.status == TaskStatus.FAILED
    assert pipeline.calls == ["builder"]


def test_fix_attempts_reuse_the_branch_and_open_one_pull_request(pipeline) -> None:
    from orchestrator.app.pipeline.stage_isolation import StageOutcome, StageWorkspace

    isolation = MagicMock()
    isolation.prepare.side_effect = lambda task, role: StageWorkspace(
        role, "agent/task-1", Path(task.worktree_path)
    )
    isolation.finish.return_value = StageOutcome(committed=True, pushed=True)
    publisher = MagicMock()
    publisher.open_pull_request.return_value = "https://example/pull/2"
    pipeline.verification_runner.run_checks.side_effect = [
        failing_check(),
        passing_checks(),
    ]
    service = PipelineService(
        db=pipeline.db,
        agent_service=pipeline.service.agents,
        control=pipeline.control,
        isolation=isolation,
        publisher=publisher,
        max_fix_attempts=1,
    )

    task = service.run(1)

    roles = [call.args[1] for call in isolation.prepare.call_args_list]
    assert roles == ["builder", "tester", "builder", "tester", "reviewer"]
    assert {call.args[0].id for call in isolation.prepare.call_args_list} == {1}
    publisher.open_pull_request.assert_called_once_with(task, "agent/task-1")
    assert task.pull_request_url == "https://example/pull/2"
