import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orchestrator.app.agent_runner import AgentExecutionResult
from orchestrator.app.agent_service import AgentService
from orchestrator.app.exceptions import AgentExecutionError
from orchestrator.app.models import AgentRun, Task, TaskStatus
from orchestrator.app.pipeline_service import (
    PipelineService,
    extract_result_text,
    parse_verdict,
)
from orchestrator.app.process_control import ProcessControl


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
    monkeypatch.setattr("orchestrator.app.agent_service.get_settings", lambda: settings)

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
