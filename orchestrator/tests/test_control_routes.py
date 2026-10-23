from threading import Event

import pytest

from orchestrator.app import control_routes, task_routes
from orchestrator.app.models import AgentRun, Task, TaskStatus
from orchestrator.app.process_control import ProcessControl


@pytest.fixture
def control(monkeypatch) -> ProcessControl:
    fresh = ProcessControl()
    monkeypatch.setattr(control_routes, "process_control", fresh)
    monkeypatch.setattr(task_routes, "process_control", fresh)
    return fresh


def make_task(routes_client, milestone_id, status, run_status=None) -> int:
    with routes_client.session_factory() as session:
        task = Task(
            milestone_id=milestone_id,
            title="Controlled task",
            status=status,
            current_stage="builder",
        )
        session.add(task)
        session.flush()

        if run_status:
            session.add(AgentRun(task_id=task.id, status=run_status))

        session.commit()
        return task.id


def task_state(routes_client, task_id):
    with routes_client.session_factory() as session:
        task = session.get(Task, task_id)
        runs = [run.status for run in task.agent_runs]
        return task.status, runs


def test_pause_then_resume_updates_task_run_and_control(
    routes_client, milestone_id, control
) -> None:
    client = routes_client.client
    task_id = make_task(routes_client, milestone_id, TaskStatus.RUNNING, "RUNNING")

    paused = client.post(f"/tasks/{task_id}/pause")

    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert control.is_paused(task_id)
    assert task_state(routes_client, task_id) == (TaskStatus.PAUSED, ["PAUSED"])

    resumed = client.post(f"/tasks/{task_id}/resume")

    assert resumed.status_code == 200
    assert resumed.json()["status"] == "running"
    assert not control.is_paused(task_id)
    assert task_state(routes_client, task_id) == (TaskStatus.RUNNING, ["RUNNING"])


def test_cancel_marks_task_and_active_runs(
    routes_client, milestone_id, control
) -> None:
    task_id = make_task(routes_client, milestone_id, TaskStatus.RUNNING, "RUNNING")

    response = routes_client.client.post(f"/tasks/{task_id}/cancel")

    assert response.status_code == 200
    assert control.is_cancelled(task_id)
    assert task_state(routes_client, task_id) == (TaskStatus.CANCELLED, ["CANCELLED"])


def test_cancel_queued_task(routes_client, milestone_id, control) -> None:
    task_id = make_task(routes_client, milestone_id, TaskStatus.QUEUED)

    response = routes_client.client.post(f"/tasks/{task_id}/cancel")

    assert response.status_code == 200
    assert task_state(routes_client, task_id)[0] == TaskStatus.CANCELLED


@pytest.mark.parametrize(
    ("action", "status"),
    [
        ("pause", TaskStatus.QUEUED),
        ("pause", TaskStatus.PAUSED),
        ("resume", TaskStatus.RUNNING),
        ("cancel", TaskStatus.COMPLETED),
        ("cancel", TaskStatus.CANCELLED),
    ],
)
def test_control_rejects_invalid_state_transitions(
    routes_client, milestone_id, control, action, status
) -> None:
    task_id = make_task(routes_client, milestone_id, status)

    response = routes_client.client.post(f"/tasks/{task_id}/{action}")

    assert response.status_code == 409
    assert task_state(routes_client, task_id)[0] == status


@pytest.mark.parametrize("action", ["pause", "resume", "cancel"])
def test_control_returns_404_for_unknown_task(routes_client, control, action) -> None:
    assert routes_client.client.post(f"/tasks/999/{action}").status_code == 404


def test_active_agents_lists_only_unfinished_runs(routes_client, milestone_id) -> None:
    make_task(routes_client, milestone_id, TaskStatus.RUNNING, "RUNNING")
    make_task(routes_client, milestone_id, TaskStatus.PAUSED, "PAUSED")
    make_task(routes_client, milestone_id, TaskStatus.COMPLETED, "COMPLETED")
    make_task(routes_client, milestone_id, TaskStatus.CANCELLED, "CANCELLED")

    response = routes_client.client.get("/agents/active")

    assert response.status_code == 200
    assert sorted(run["status"] for run in response.json()) == ["PAUSED", "RUNNING"]


def test_pipeline_endpoint_schedules_queued_task(
    routes_client, milestone_id, control, monkeypatch
) -> None:
    task_id = make_task(routes_client, milestone_id, TaskStatus.QUEUED)
    control.cancel(task_id)
    finished = Event()
    scheduled: list[int] = []

    def fake_pipeline(scheduled_id: int) -> None:
        scheduled.append(scheduled_id)
        finished.set()

    monkeypatch.setattr(task_routes, "_run_pipeline_in_background", fake_pipeline)

    response = routes_client.client.post(f"/tasks/{task_id}/pipeline")

    assert response.status_code == 202
    assert response.json()["task_id"] == task_id
    assert finished.wait(timeout=5)
    assert scheduled == [task_id]
    assert not control.is_cancelled(task_id)


def test_pipeline_endpoint_rejects_unknown_and_non_queued_tasks(
    routes_client, milestone_id, control
) -> None:
    running_id = make_task(routes_client, milestone_id, TaskStatus.RUNNING)

    assert routes_client.client.post("/tasks/999/pipeline").status_code == 404
    assert routes_client.client.post(f"/tasks/{running_id}/pipeline").status_code == 409


def test_pipeline_background_skips_cancelled_task(control, monkeypatch) -> None:
    called = Event()
    monkeypatch.setattr(
        task_routes,
        "PipelineService",
        lambda db: called.set(),
    )
    control.cancel(4)

    task_routes._run_pipeline_in_background(4)

    assert not called.is_set()


def test_list_tasks_supports_status_filter(routes_client, milestone_id) -> None:
    queued_id = make_task(routes_client, milestone_id, TaskStatus.QUEUED)
    make_task(routes_client, milestone_id, TaskStatus.FAILED)

    everything = routes_client.client.get("/tasks").json()
    queued = routes_client.client.get("/tasks", params={"status": "queued"}).json()
    invalid = routes_client.client.get("/tasks", params={"status": "bogus"})

    assert len(everything) == 2
    assert [task["id"] for task in queued] == [queued_id]
    assert invalid.status_code == 422


def test_get_task_includes_runs_and_verification_results(
    routes_client, milestone_id
) -> None:
    from orchestrator.app.models import VerificationResult

    task_id = make_task(routes_client, milestone_id, TaskStatus.FAILED, "FAILED")
    with routes_client.session_factory() as session:
        session.add(
            VerificationResult(
                task_id=task_id,
                check_name="unit_tests",
                passed=False,
                details="1 failed",
            )
        )
        session.commit()

    response = routes_client.client.get(f"/tasks/{task_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["current_stage"] == "builder"
    assert [run["status"] for run in body["agent_runs"]] == ["FAILED"]
    assert [check["check_name"] for check in body["verification_results"]] == [
        "unit_tests"
    ]
    assert routes_client.client.get("/tasks/999").status_code == 404
