from orchestrator.app import intake_routes
from orchestrator.app.config import Settings
from orchestrator.app.github_intake import Issue
from orchestrator.tests.test_github_intake import StaticIssueSource, init_repository


def test_create_and_list_projects(routes_client) -> None:
    client = routes_client.client

    response = client.post(
        "/projects", json={"name": " Demo ", "local_path": "/tmp/repo"}
    )

    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "Demo"
    assert created["local_path"] == "/tmp/repo"

    listing = client.get("/projects").json()
    assert [project["id"] for project in listing] == [created["id"]]


def test_duplicate_project_name_returns_conflict(routes_client) -> None:
    client = routes_client.client
    client.post("/projects", json={"name": "Demo"})

    response = client.post("/projects", json={"name": "Demo"})

    assert response.status_code == 409


def test_invalid_project_payload_returns_422(routes_client) -> None:
    assert routes_client.client.post("/projects", json={"name": ""}).status_code == 422


def test_create_and_list_milestones(routes_client) -> None:
    client = routes_client.client
    project_id = client.post("/projects", json={"name": "Demo"}).json()["id"]

    created = client.post(
        f"/projects/{project_id}/milestones",
        json={"code": "M1", "title": "First", "description": "Details"},
    )

    assert created.status_code == 201
    assert created.json()["project_id"] == project_id
    assert created.json()["status"] == "planned"

    listing = client.get(f"/projects/{project_id}/milestones").json()
    assert [item["code"] for item in listing] == ["M1"]


def test_milestone_routes_return_404_for_unknown_project(routes_client) -> None:
    client = routes_client.client

    create = client.post("/projects/99/milestones", json={"code": "M1", "title": "x"})
    listing = client.get("/projects/99/milestones")

    assert create.status_code == 404
    assert listing.status_code == 404


def test_sync_endpoint_imports_issues_once(
    routes_client, tmp_path, monkeypatch
) -> None:
    repository = tmp_path / "source"
    init_repository(repository)

    client = routes_client.client
    project_id = client.post(
        "/projects", json={"name": "Sync", "local_path": str(repository)}
    ).json()["id"]
    milestone = client.post(
        f"/projects/{project_id}/milestones", json={"code": "M1", "title": "Sync"}
    ).json()

    monkeypatch.setattr(
        intake_routes,
        "get_settings",
        lambda: Settings(_env_file=None, repository_path=repository),
    )
    routes_client.app.dependency_overrides[intake_routes.get_issue_source] = lambda: (
        StaticIssueSource([Issue(number=5, title="Imported issue", body="Body")])
    )

    first = client.post(f"/milestones/{milestone['id']}/sync")
    second = client.post(f"/milestones/{milestone['id']}/sync")

    assert first.status_code == 200
    imported = first.json()["imported"]
    assert [task["github_issue_number"] for task in imported] == [5]
    assert imported[0]["status"] == "queued"
    assert second.json()["imported"] == []
    assert second.json()["skipped_issue_numbers"] == [5]


def test_sync_endpoint_requires_repository_and_known_milestone(
    routes_client, milestone_id, monkeypatch
) -> None:
    client = routes_client.client
    routes_client.app.dependency_overrides[intake_routes.get_issue_source] = lambda: (
        StaticIssueSource([])
    )

    monkeypatch.setattr(intake_routes, "get_settings", lambda: Settings(_env_file=None))
    assert client.post(f"/milestones/{milestone_id}/sync").status_code == 503

    monkeypatch.setattr(
        intake_routes,
        "get_settings",
        lambda: Settings(_env_file=None, repository_path="."),
    )
    assert client.post("/milestones/999/sync").status_code == 404


def test_sync_endpoint_reports_unavailable_issue_source(
    routes_client, milestone_id, monkeypatch
) -> None:
    monkeypatch.setattr(
        intake_routes,
        "get_settings",
        lambda: Settings(_env_file=None, issue_source="file"),
    )

    response = routes_client.client.post(f"/milestones/{milestone_id}/sync")

    assert response.status_code == 503
    assert "ISSUE_FILE_PATH" in response.json()["detail"]
