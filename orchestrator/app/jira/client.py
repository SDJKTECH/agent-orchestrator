# Small Jira Cloud REST client (API v3) built on urllib; basic auth with an API token.
# Also turns Jira's rich-text (ADF) descriptions into plain text for the agents.
import base64
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import IssueSourceError

logger = logging.getLogger(__name__)

Transport = Callable[[str, str, dict | None], Any]

SEARCH_FIELDS = [
    "summary",
    "description",
    "labels",
    "fixVersions",
    "project",
    "status",
    "issuelinks",
    "issuetype",
    "parent",
]

# What is read of a parent when walking up from a Task to its Story and Epic.
ISSUE_FIELDS = "summary,description,issuetype,parent,status"


def adf_to_text(node: Any) -> str:
    """Flatten an Atlassian Document Format description to plain text."""
    if node is None:
        return ""

    if isinstance(node, str):
        return node

    if not isinstance(node, dict):
        return ""

    kind = node.get("type")
    children = node.get("content") or []

    if kind == "text":
        return str(node.get("text", ""))

    if kind == "hardBreak":
        return "\n"

    if kind in ("mention", "emoji", "inlineCard"):
        attrs = node.get("attrs") or {}
        return str(
            attrs.get("text") or attrs.get("url") or attrs.get("shortName") or ""
        )

    inner = "".join(adf_to_text(child) for child in children)

    if kind == "listItem":
        return "- " + inner.strip() + "\n"

    if kind == "codeBlock":
        return "```\n" + inner.strip("\n") + "\n```\n\n"

    if kind in ("paragraph", "heading", "blockquote", "bulletList", "orderedList"):
        return inner.rstrip("\n") + "\n\n"

    return inner


def text_to_adf(text: str) -> dict:
    """Wrap plain text in the document format Jira comments require."""
    paragraphs = [part for part in text.split("\n\n") if part.strip()] or [text]
    content = []

    for part in paragraphs:
        lines = part.split("\n")
        inline: list[dict] = []
        for index, line in enumerate(lines):
            if index:
                inline.append({"type": "hardBreak"})
            if line:
                inline.append({"type": "text", "text": line})
        content.append({"type": "paragraph", "content": inline})

    return {"type": "doc", "version": 1, "content": content}


class JiraApi:
    """Minimal Jira Cloud client. Tests replace `transport`."""

    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._auth = base64.b64encode(f"{email}:{token}".encode()).decode("ascii")
        self._transport = transport or self._send

    def browse_url(self, key: str) -> str:
        """The web address of an issue, e.g. https://site.atlassian.net/browse/SG-1."""
        return f"{self.base_url}/browse/{key}"

    def request(self, method: str, path: str, body: dict | None = None) -> Any:
        """Send a request to the Jira REST API and return the decoded JSON reply."""
        return self._transport(method, path, body)

    def _send(self, method: str, path: str, body: dict | None) -> Any:
        """Make the HTTP call with basic auth; raise IssueSourceError on failure."""
        headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {self._auth}",
            "User-Agent": "agent-orchestrator",
        }
        data = None

        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            f"{self.base_url}/rest/api/3{path}",
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
                f"Jira API {method} {path} failed with {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise IssueSourceError(f"Jira is unreachable: {exc}") from exc

        return json.loads(payload) if payload else None

    def search(self, jql: str, limit: int = 50) -> list[dict]:
        """Issues matching `jql`, oldest first as the query orders them."""
        result = self.request(
            "POST",
            "/search/jql",
            {"jql": jql, "fields": SEARCH_FIELDS, "maxResults": limit},
        )
        issues = result.get("issues") if isinstance(result, dict) else None
        return issues if isinstance(issues, list) else []

    def issue(self, key: str) -> dict | None:
        """One issue with the fields needed to read its type, parent and text."""
        found = self.request("GET", f"/issue/{key}?fields={ISSUE_FIELDS}")
        return found if isinstance(found, dict) and found.get("key") else None

    def status_name(self, key: str) -> str | None:
        """The issue's current status name, or None if it cannot be read."""
        issue = self.request("GET", f"/issue/{key}?fields=status")
        fields = issue.get("fields") if isinstance(issue, dict) else None
        status = fields.get("status") if isinstance(fields, dict) else None
        return status.get("name") if isinstance(status, dict) else None

    def transitions(self, key: str) -> list[dict]:
        """The status changes Jira currently allows for the issue."""
        result = self.request("GET", f"/issue/{key}/transitions")
        items = result.get("transitions") if isinstance(result, dict) else None
        return items if isinstance(items, list) else []

    def transition(self, key: str, transition_id: str) -> None:
        """Move the issue by applying a transition ID."""
        self.request(
            "POST", f"/issue/{key}/transitions", {"transition": {"id": transition_id}}
        )

    def link_blocks(self, blocker: str, blocked: str) -> None:
        """Record that `blocker` blocks `blocked` ("is blocked by" on the latter).

        In Jira's API the inward issue is the one doing the blocking.
        """
        self.request(
            "POST",
            "/issueLink",
            {
                "type": {"name": "Blocks"},
                "inwardIssue": {"key": blocker},
                "outwardIssue": {"key": blocked},
            },
        )

    def comment(self, key: str, text: str) -> None:
        """Add a plain-text comment to the issue."""
        self.request("POST", f"/issue/{key}/comment", {"body": text_to_adf(text)})


def make_jira_api(settings: Settings) -> JiraApi:
    """Create the Jira client from settings, or say what is missing."""
    missing = [
        name
        for name, value in (
            ("JIRA_BASE_URL", settings.jira_base_url),
            ("JIRA_EMAIL", settings.jira_email),
            ("JIRA_API_TOKEN", settings.jira_api_token),
        )
        if not value
    ]

    if missing:
        raise IssueSourceError(
            f"ISSUE_SOURCE=jira needs {', '.join(missing)} in the environment."
        )

    assert settings.jira_base_url and settings.jira_email and settings.jira_api_token
    return JiraApi(
        settings.jira_base_url,
        settings.jira_email,
        settings.jira_api_token.get_secret_value(),
    )


def quote_jql(value: str) -> str:
    """A JQL string literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
