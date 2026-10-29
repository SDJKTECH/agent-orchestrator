# GraphQL client for a GitHub Projects (v2) board: list cards, move them.
# Also defines the board column names the orchestrator uses for each stage.
import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import IssueSourceError
from orchestrator.app.github.client import detect_github_repo

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.github.com/graphql"

# Board columns (options of the project's "Status" field). A person puts a card
# in TODO to start work; the orchestrator moves it through the rest.
TODO = "Todo"
QUEUED = "Queued"
BUILDING = "Building"
TESTING = "Testing"
CHECKS = "Checks"
REVIEWING = "Reviewing"
FIXING = "Fixing"
PR_OPEN = "PR open"
DONE = "Done"
FAILED = "Failed"
# Shown on Jira only: the task waits for others to be merged.
BLOCKED = "Blocked"

BOARD_COLUMNS = (
    TODO,
    QUEUED,
    BUILDING,
    TESTING,
    CHECKS,
    REVIEWING,
    FIXING,
    PR_OPEN,
    DONE,
    FAILED,
)

STATUS_FIELD = "Status"

_NAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")

_BOARD_QUERY = """
query($owner: String!, $number: Int!, $field: String!) {
  OWNER(login: $owner) {
    projectV2(number: $number) {
      id
      title
      field(name: $field) {
        ... on ProjectV2SingleSelectField { id options { id name color description } }
      }
      items(first: 100) {
        nodes {
          id
          fieldValueByName(name: $field) {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          content {
            __typename
            ... on DraftIssue { title body }
            ... on Issue {
              number
              title
              body
              state
              repository { nameWithOwner }
              labels(first: 20) { nodes { name } }
              milestone { title number }
            }
          }
        }
      }
    }
  }
}
"""

_FIELD_MUTATION = """
mutation($field: ID!, $options: [ProjectV2SingleSelectFieldOptionInput!]!) {
  updateProjectV2Field(input: {fieldId: $field, singleSelectOptions: $options}) {
    projectV2Field { ... on ProjectV2SingleSelectField { id } }
  }
}
"""

_REPO_ID_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) { id }
}
"""

_CONVERT_MUTATION = """
mutation($item: ID!, $repo: ID!) {
  convertProjectV2DraftIssueItemToIssue(input: {itemId: $item, repositoryId: $repo}) {
    item {
      id
      content {
        ... on Issue {
          number
          title
          body
          state
          repository { nameWithOwner }
          labels(first: 20) { nodes { name } }
          milestone { title number }
        }
      }
    }
  }
}
"""

# Colours given to columns the orchestrator adds to a board.
_COLUMN_COLORS = {
    "Todo": "GRAY",
    "Queued": "GRAY",
    "Building": "BLUE",
    "Testing": "PURPLE",
    "Checks": "YELLOW",
    "Reviewing": "ORANGE",
    "Fixing": "PINK",
    "PR open": "BLUE",
    "Done": "GREEN",
    "Failed": "RED",
}

_MOVE_MUTATION = """
mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $project, itemId: $item, fieldId: $field,
    value: { singleSelectOptionId: $option }
  }) { projectV2Item { id } }
}
"""


@dataclass(frozen=True)
class BoardCard:
    """One issue card on the board."""

    item_id: str
    column: str | None
    repo: str
    number: int
    title: str
    body: str = ""
    state: str = "OPEN"
    labels: list[str] = field(default_factory=list)
    milestone: str | None = None
    milestone_number: int | None = None
    # A draft task (not an issue yet); it is converted to an issue before use.
    draft: bool = False


Transport = Callable[[dict[str, Any]], dict[str, Any]]


def post_graphql(token: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Send one GraphQL request to GitHub and return the decoded reply."""
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "agent-orchestrator",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise IssueSourceError(
            f"GitHub GraphQL failed with {exc.code}: {detail}"
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise IssueSourceError(f"GitHub GraphQL is unreachable: {exc}") from exc


class ProjectsApi:
    """Minimal GraphQL client for one GitHub Projects (v2) board."""

    def __init__(
        self,
        owner: str,
        number: int,
        token: str,
        owner_type: str = "user",
        transport: Transport | None = None,
    ) -> None:
        if not _NAME_PATTERN.fullmatch(owner):
            raise IssueSourceError(f"Invalid project owner: {owner}")

        if owner_type not in ("user", "org"):
            raise IssueSourceError(f"Invalid project owner type: {owner_type}")

        self.owner = owner
        self.number = number
        self.owner_type = owner_type
        self._token = token
        self._transport = transport or self._post
        self.title: str | None = None
        self._project_id: str | None = None
        self._field_id: str | None = None
        self._options: dict[str, str] = {}
        self._option_details: list[dict[str, Any]] = []

    @property
    def columns(self) -> list[str]:
        """Column names that exist on the board (after the first load)."""
        return list(self._options)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one GraphQL request to GitHub."""
        return post_graphql(self._token, payload)

    def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Run a GraphQL query and return its data; raise on GraphQL errors."""
        reply = self._transport({"query": query, "variables": variables})

        if reply.get("errors"):
            message = "; ".join(
                str(error.get("message", error)) for error in reply["errors"]
            )
            raise IssueSourceError(f"GitHub GraphQL error: {message[:300]}")

        return reply.get("data") or {}

    def load(self) -> list[BoardCard]:
        """Read the board: remember its ids and columns, return its issue cards."""
        root = "user" if self.owner_type == "user" else "organization"
        data = self._query(
            _BOARD_QUERY.replace("OWNER", root),
            {"owner": self.owner, "number": self.number, "field": STATUS_FIELD},
        )
        project = (data.get(root) or {}).get("projectV2")

        if not project:
            raise IssueSourceError(
                f"Project {self.number} of {self.owner} was not found, or the "
                "token cannot read it (user boards need a classic token with "
                "the 'project' scope)."
            )

        status_field = project.get("field") or {}

        if not status_field.get("id"):
            raise IssueSourceError(
                f"Project {self.number} has no single-select '{STATUS_FIELD}' field."
            )

        self._project_id = project["id"]
        self.title = project.get("title")
        self._field_id = status_field["id"]
        self._option_details = status_field.get("options", [])
        self._options = {
            option["name"]: option["id"] for option in self._option_details
        }

        return [
            card
            for node in project["items"]["nodes"]
            if (card := _parse_card(node)) is not None
        ]

    def missing_columns(self) -> list[str]:
        """Board columns the orchestrator needs that this board lacks."""
        return [name for name in BOARD_COLUMNS if name not in self._options]

    def ensure_columns(self) -> None:
        """Add the Status columns this board lacks, keeping the ones it has.

        A new project starts with Todo / In Progress / Done, so this is what
        makes a freshly linked project usable without any manual setup.
        """
        if self._field_id is None:
            self.load()

        missing = self.missing_columns()

        if not missing:
            return

        existing = [
            {
                "name": option["name"],
                "color": option.get("color") or "GRAY",
                "description": option.get("description") or "",
            }
            for option in self._option_details
        ]
        added = [
            {"name": name, "color": _COLUMN_COLORS[name], "description": ""}
            for name in missing
        ]

        try:
            # Passing the ids keeps the cards that are already in a column.
            self._query(
                _FIELD_MUTATION,
                {
                    "field": self._field_id,
                    "options": [
                        {**option, "id": detail["id"]}
                        for option, detail in zip(
                            existing, self._option_details, strict=True
                        )
                    ]
                    + added,
                },
            )
        except IssueSourceError:
            self._query(
                _FIELD_MUTATION,
                {"field": self._field_id, "options": existing + added},
            )

        self.load()
        logger.info(
            "Board columns added: board=%s columns=%s", self.number, ", ".join(missing)
        )

    def convert_draft(self, item_id: str, repo: str) -> BoardCard | None:
        """Turn a draft task into a real issue of `repo`; return its card."""
        owner, _, name = repo.partition("/")
        repository = (
            self._query(_REPO_ID_QUERY, {"owner": owner, "name": name}).get(
                "repository"
            )
            or {}
        )

        if not repository.get("id"):
            raise IssueSourceError(f"Repository {repo} was not found.")

        data = self._query(
            _CONVERT_MUTATION, {"item": item_id, "repo": repository["id"]}
        )
        node = (data.get("convertProjectV2DraftIssueItemToIssue") or {}).get("item")
        card = _parse_card(node) if node else None
        return replace(card, column=TODO) if card else None

    def set_column(self, item_id: str, column: str) -> None:
        """Move a card to `column`. The column must exist on the board."""
        if self._project_id is None:
            self.load()

        option = self._options.get(column)

        if option is None:
            raise IssueSourceError(f"The board has no '{column}' column.")

        self._query(
            _MOVE_MUTATION,
            {
                "project": self._project_id,
                "item": item_id,
                "field": self._field_id,
                "option": option,
            },
        )


def _parse_card(node: dict[str, Any]) -> BoardCard | None:
    """Turn a GraphQL project item into a BoardCard, or None if unusable."""
    content = node.get("content") or {}

    value = node.get("fieldValueByName") or {}

    if content.get("__typename") == "DraftIssue":
        return BoardCard(
            item_id=node["id"],
            column=value.get("name"),
            repo="",
            number=0,
            title=content.get("title") or "",
            body=content.get("body") or "",
            draft=True,
        )

    # Pull requests and anything else carry no issue number.
    if not isinstance(content.get("number"), int):
        return None

    milestone = content.get("milestone") or {}

    return BoardCard(
        item_id=node["id"],
        column=value.get("name"),
        repo=(content.get("repository") or {}).get("nameWithOwner", ""),
        number=content["number"],
        title=content.get("title") or "",
        body=content.get("body") or "",
        state=content.get("state") or "OPEN",
        labels=[
            label["name"] for label in (content.get("labels") or {}).get("nodes", [])
        ],
        milestone=milestone.get("title"),
        milestone_number=milestone.get("number"),
    )


_DISCOVER_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    owner { __typename login }
    projectsV2(first: 50) { nodes { number title closed } }
  }
}
"""


@dataclass(frozen=True)
class BoardRef:
    """A Projects board that is linked to the repository."""

    number: int
    title: str
    owner: str
    owner_type: str


def discover_boards(
    token: str, repo: str, transport: Transport | None = None
) -> list[BoardRef]:
    """Return the open Projects boards linked to `repo` (owner/name)."""
    owner, _, name = repo.partition("/")

    if not _NAME_PATTERN.fullmatch(owner) or not _NAME_PATTERN.fullmatch(name):
        raise IssueSourceError(f"Invalid GitHub repository name: {repo}")

    send = transport or (lambda payload: post_graphql(token, payload))
    reply = send(
        {"query": _DISCOVER_QUERY, "variables": {"owner": owner, "name": name}}
    )

    if reply.get("errors"):
        message = "; ".join(str(e.get("message", e)) for e in reply["errors"])
        raise IssueSourceError(f"GitHub GraphQL error: {message[:300]}")

    repository = (reply.get("data") or {}).get("repository")

    if not repository:
        raise IssueSourceError(f"Repository {repo} was not found.")

    repo_owner = repository.get("owner") or {}
    owner_type = "org" if repo_owner.get("__typename") == "Organization" else "user"

    return [
        BoardRef(
            number=node["number"],
            title=node.get("title") or f"Project {node['number']}",
            owner=repo_owner.get("login") or owner,
            owner_type=owner_type,
        )
        for node in (repository.get("projectsV2") or {}).get("nodes", [])
        if node and not node.get("closed")
    ]


def board_numbers(settings: Settings) -> list[int]:
    """Board numbers from PROJECT_NUMBERS ("4,5") and PROJECT_NUMBER, in order."""
    numbers: list[int] = []

    for part in (settings.project_numbers or "").split(","):
        if part.strip():
            try:
                numbers.append(int(part))
            except ValueError as exc:
                raise IssueSourceError(
                    f"PROJECT_NUMBERS must be comma-separated numbers, got '{part}'."
                ) from exc

    if settings.project_number:
        numbers.append(settings.project_number)

    return list(dict.fromkeys(numbers))


def make_projects_apis(
    settings: Settings, transport: Transport | None = None
) -> list[ProjectsApi]:
    """Create one client per board: the configured numbers, else the repo's boards.

    With no board numbers set, the boards linked to the repository on GitHub
    are used, so linking a new project is enough to have it picked up.
    """
    token = settings.project_token or settings.github_token

    if token is None:
        return []

    secret = token.get_secret_value()
    repo = settings.github_repo or detect_github_repo(settings.repository_path)
    numbers = board_numbers(settings)

    if numbers:
        owner = settings.project_owner or (repo.split("/")[0] if repo else None)

        if not owner:
            return []

        return [
            ProjectsApi(owner, number, secret, settings.project_owner_type, transport)
            for number in numbers
        ]

    if repo is None:
        return []

    return [
        ProjectsApi(ref.owner, ref.number, secret, ref.owner_type, transport)
        for ref in discover_boards(secret, repo, transport)
    ]
