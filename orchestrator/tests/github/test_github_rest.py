import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import IssueSourceError
from orchestrator.app.db.models import Task
from orchestrator.app.github.client import GitHubApi, detect_github_repo
from orchestrator.app.github.intake import (
    GhCliIssueSource,
    RestIssueSource,
    build_issue_source,
)
from orchestrator.app.github.publisher import (
    GhPullRequestPublisher,
    RestPullRequestPublisher,
    make_publisher,
)
from orchestrator.app.github.reporter import (
    GhIssueReporter,
    RestIssueReporter,
    make_reporter,
)


def fake_response(payload) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode()
    response.__enter__.return_value = response
    return response


def sent_request(mock_urlopen):
    return mock_urlopen.call_args.args[0]


def test_api_sends_headers_and_token_only_when_present() -> None:
    with patch("urllib.request.urlopen", return_value=fake_response([])) as opened:
        GitHubApi("o/r").request("GET", "/issues")
        anonymous = sent_request(opened)

    with patch("urllib.request.urlopen", return_value=fake_response([])) as opened:
        GitHubApi("o/r", "secret-token").request("GET", "/issues")
        authenticated = sent_request(opened)

    assert anonymous.full_url == "https://api.github.com/repos/o/r/issues"
    assert anonymous.get_header("Authorization") is None
    assert authenticated.get_header("Authorization") == "Bearer secret-token"
    assert authenticated.get_header("User-agent") == "agent-orchestrator"


def test_api_wraps_http_and_network_errors() -> None:
    http_error = urllib.error.HTTPError(
        "url", 404, "Not Found", {}, BytesIO(b'{"message": "Not Found"}')
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(IssueSourceError, match="404"):
            GitHubApi("o/r").request("GET", "/labels/x")

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        with pytest.raises(IssueSourceError, match="unreachable"):
            GitHubApi("o/r").request("GET", "/issues")


def test_api_rejects_invalid_repo_names() -> None:
    with pytest.raises(IssueSourceError, match="Invalid"):
        GitHubApi("not a repo")


@pytest.mark.parametrize(
    "remote",
    [
        "https://github.com/SDJKTECH/agent-orchestrator.git",
        "https://github.com/SDJKTECH/agent-orchestrator",
        "git@github.com:SDJKTECH/agent-orchestrator.git",
    ],
)
def test_detect_github_repo_parses_remote_urls(remote) -> None:
    completed = MagicMock(stdout=remote + "\n")

    with patch("orchestrator.app.github.client.subprocess.run", return_value=completed):
        assert detect_github_repo(None) == "SDJKTECH/agent-orchestrator"


def test_detect_github_repo_returns_none_for_other_hosts() -> None:
    completed = MagicMock(stdout="https://gitlab.com/o/r.git\n")

    with patch("orchestrator.app.github.client.subprocess.run", return_value=completed):
        assert detect_github_repo(None) is None


def test_rest_issue_source_filters_label_and_skips_pull_requests() -> None:
    items = [
        {"number": 1, "title": "Real issue", "body": None, "labels": [{"name": "a"}]},
        {"number": 2, "title": "A pull request", "pull_request": {}, "labels": []},
    ]

    with patch("urllib.request.urlopen", return_value=fake_response(items)) as opened:
        issues = RestIssueSource(GitHubApi("o/r"), label="agent task").list_issues()

    assert [issue.number for issue in issues] == [1]
    assert "labels=agent%20task" in sent_request(opened).full_url
    assert "state=open" in sent_request(opened).full_url


def test_rest_issue_source_rejects_unexpected_payload() -> None:
    with patch("urllib.request.urlopen", return_value=fake_response({"x": 1})):
        with pytest.raises(IssueSourceError, match="unexpected"):
            RestIssueSource(GitHubApi("o/r")).list_issues()


def test_factories_follow_the_configured_backend() -> None:
    gh = Settings(_env_file=None, github_repo="o/r")
    rest = Settings(
        _env_file=None,
        github_backend="rest",
        github_repo="o/r",
        github_token=SecretStr("t"),
    )

    assert isinstance(build_issue_source(gh), GhCliIssueSource)
    assert isinstance(make_reporter(gh), GhIssueReporter)
    assert isinstance(make_publisher(gh), GhPullRequestPublisher)
    assert isinstance(build_issue_source(rest), RestIssueSource)
    assert isinstance(make_reporter(rest), RestIssueReporter)
    assert isinstance(make_publisher(rest), RestPullRequestPublisher)


def test_rest_backend_needs_a_resolvable_repo() -> None:
    settings = Settings(_env_file=None, github_backend="rest")

    with patch("orchestrator.app.github.intake.detect_github_repo", return_value=None):
        with pytest.raises(IssueSourceError, match="GITHUB_REPO"):
            build_issue_source(settings)


def test_token_is_never_shown_in_settings_repr() -> None:
    settings = Settings(_env_file=None, github_token=SecretStr("super-secret"))

    assert "super-secret" not in repr(settings)


def test_rest_reporter_skips_without_token_and_posts_with_one() -> None:
    with patch("urllib.request.urlopen") as opened:
        assert RestIssueReporter(GitHubApi("o/r")).comment(5, "hi") is False
        opened.assert_not_called()

    with patch(
        "urllib.request.urlopen", return_value=fake_response({"id": 1})
    ) as opened:
        assert RestIssueReporter(GitHubApi("o/r", "t")).comment(5, "hi") is True

    request = sent_request(opened)
    assert request.full_url.endswith("/repos/o/r/issues/5/comments")
    assert json.loads(request.data) == {"body": "hi"}


def test_rest_reporter_swallows_api_errors() -> None:
    error = urllib.error.HTTPError("u", 403, "Forbidden", {}, BytesIO(b"{}"))

    with patch("urllib.request.urlopen", side_effect=error):
        assert RestIssueReporter(GitHubApi("o/r", "t")).comment(1, "x") is False


def test_rest_publisher_returns_compare_link_without_token() -> None:
    task = Task(id=4, title="Do it")

    with patch("urllib.request.urlopen") as opened:
        url = RestPullRequestPublisher(GitHubApi("o/r"), "main").open_pull_request(
            task, "agent/task-4-tester"
        )

    opened.assert_not_called()
    assert url == "https://github.com/o/r/compare/main...agent/task-4-tester?expand=1"


def test_rest_publisher_creates_pull_request_with_token() -> None:
    task = Task(id=4, title="Do it", github_issue_number=9)

    with patch(
        "urllib.request.urlopen",
        return_value=fake_response({"html_url": "https://github.com/o/r/pull/3"}),
    ) as opened:
        url = RestPullRequestPublisher(GitHubApi("o/r", "t"), "main").open_pull_request(
            task, "agent/task-4-tester"
        )

    body = json.loads(sent_request(opened).data)
    assert url == "https://github.com/o/r/pull/3"
    assert body["head"] == "agent/task-4-tester"
    assert body["base"] == "main"
    assert "Closes #9" in body["body"]


def test_rest_publisher_returns_none_when_github_rejects() -> None:
    error = urllib.error.HTTPError("u", 422, "Unprocessable", {}, BytesIO(b"{}"))

    with patch("urllib.request.urlopen", side_effect=error):
        assert (
            RestPullRequestPublisher(GitHubApi("o/r", "t")).open_pull_request(
                Task(id=1, title="t"), "branch"
            )
            is None
        )
