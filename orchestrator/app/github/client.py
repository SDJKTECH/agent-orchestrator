# Small GitHub REST API client built on urllib, with an optional token.
# Also detects the owner/name of the repository from the git remote.
import json
import logging
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from orchestrator.app.core.exceptions import IssueSourceError

logger = logging.getLogger(__name__)

API_URL = "https://api.github.com"

_REMOTE_PATTERN = re.compile(
    r"github\.com[:/](?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


def detect_github_repo(repository_path: Path | None) -> str | None:
    """Return owner/name from the clone's origin remote, if it is on GitHub."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repository_path or "."), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    match = _REMOTE_PATTERN.search(result.stdout.strip())
    return f"{match['owner']}/{match['name']}" if match else None


class GitHubApi:
    """Minimal GitHub REST client. Works without a token for public reads."""

    def __init__(
        self,
        repo: str,
        token: str | None = None,
        base_url: str = API_URL,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise IssueSourceError(f"Invalid GitHub repository name: {repo}")

        self.repo = repo
        self.token = token or None
        self.base_url = base_url.rstrip("/")

    @property
    def can_write(self) -> bool:
        return self.token is not None

    def request(self, method: str, path: str, body: dict | None = None) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "agent-orchestrator",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            f"{self.base_url}/repos/{self.repo}{path}",
            data=data,
            method=method,
            headers=headers,
        )

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise IssueSourceError(
                f"GitHub API {method} {path} failed with {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise IssueSourceError(f"GitHub API is unreachable: {exc}") from exc

        return json.loads(payload) if payload else None
