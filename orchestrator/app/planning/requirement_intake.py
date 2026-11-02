# Watches GitHub for issues labelled as requirements and plans each into Jira.
# Runs on its own thread, because a planner run takes minutes. Every requirement is
# planned once: the Epic made for it is remembered under the reference "github#<n>".
import logging
import threading
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orchestrator.app.core.config import Settings
from orchestrator.app.core.exceptions import IssueSourceError, OrchestratorError
from orchestrator.app.github.client import GitHubApi, detect_github_repo
from orchestrator.app.github.intake import Issue
from orchestrator.app.jira.client import JiraApi, make_jira_api
from orchestrator.app.jira.models import Requirement
from orchestrator.app.planning.requirement_service import (
    RequirementResult,
    plan_requirement,
)
from orchestrator.app.planning.requirements import RequirementPlanner

logger = logging.getLogger(__name__)

# A requirement that keeps failing to plan is given up on after this many tries
# (until the server restarts), so a bad one does not cost a planner run every cycle.
MAX_ATTEMPTS = 3
# Planner runs per cycle; the rest wait for the next one.
MAX_PER_CYCLE = 3
# Comments of an issue that are read along with its text.
MAX_COMMENTS = 20


def reference_for(issue: Issue) -> str:
    """The name a requirement is remembered under, e.g. 'github#12'."""
    return f"github#{issue.number}"


class RequirementSource(Protocol):
    """Where requirements come from, and where the outcome is reported."""

    def list_requirements(self) -> list[Issue]: ...

    def discussion(self, number: int) -> list[str]: ...

    def comment(self, number: int, body: str) -> bool: ...

    def mark_planned(self, number: int) -> bool: ...


class GitHubRequirementSource:
    """Requirements are the open GitHub issues that carry a label."""

    def __init__(
        self, api: GitHubApi, label: str, planned_label: str | None = None
    ) -> None:
        self.api = api
        self.label = label
        self.planned_label = planned_label

    def list_requirements(self) -> list[Issue]:
        """Open, labelled issues; pull requests are left out."""
        found = self.api.request(
            "GET", f"/issues?state=open&per_page=50&labels={self.label}"
        )

        if not isinstance(found, list):
            raise IssueSourceError("GitHub did not return a list of issues.")

        issues = []

        for item in found:
            if (
                not isinstance(item, dict)
                or "pull_request" in item
                or not isinstance(item.get("number"), int)
                or not str(item.get("title") or "").strip()
            ):
                continue

            issues.append(
                Issue(
                    number=item["number"],
                    title=str(item["title"]).strip(),
                    body=item.get("body") or "",
                    labels=[
                        str(label.get("name"))
                        for label in item.get("labels") or []
                        if isinstance(label, dict)
                    ],
                )
            )

        return issues

    def discussion(self, number: int) -> list[str]:
        """What people wrote in the issue's comments, oldest first.

        Requirements are often clarified there. A failure to read them just
        leaves them out.
        """
        try:
            found = self.api.request(
                "GET", f"/issues/{number}/comments?per_page={MAX_COMMENTS}"
            )
        except OrchestratorError as exc:
            logger.warning("Could not read comments of #%s: %s", number, exc)
            return []

        if not isinstance(found, list):
            return []

        return [
            str(item["body"]).strip()
            for item in found[:MAX_COMMENTS]
            if isinstance(item, dict) and str(item.get("body") or "").strip()
        ]

    def comment(self, number: int, body: str) -> bool:
        """Comment on the issue; False when there is no token or GitHub refuses."""
        return self._write(f"/issues/{number}/comments", {"body": body})

    def mark_planned(self, number: int) -> bool:
        """Add the "planned" label, if one is configured."""
        if not self.planned_label:
            return False

        return self._write(f"/issues/{number}/labels", {"labels": [self.planned_label]})

    def _write(self, path: str, body: dict) -> bool:
        if not self.api.can_write:
            return False

        try:
            self.api.request("POST", path, body)
        except OrchestratorError as exc:
            logger.warning("Could not write to GitHub: %s", exc)
            return False

        return True


def requirement_text(issue: Issue, comments: list[str]) -> str:
    """Everything written about a requirement: its text, labels and discussion."""
    parts = [issue.body.strip()]

    if issue.labels:
        parts.append("Labels: " + ", ".join(issue.labels))

    if comments:
        parts.append("Discussion on the issue:\n\n" + "\n\n---\n\n".join(comments))

    return "\n\n".join(part for part in parts if part)


def outcome_comment(result: RequirementResult, jira: JiraApi | None) -> str:
    """The comment left on a requirement issue once it is planned in Jira."""

    def link(key: str) -> str:
        return jira.browse_url(key) if jira is not None else key

    keys = result.epic_keys or ([result.epic_key] if result.epic_key else [])

    if result.existing or result.plan is None or result.created is None:
        return "This requirement is planned in Jira: " + ", ".join(
            link(key) for key in keys
        )

    several = len(result.plan.epics) > 1
    lines = [
        "Planned in Jira as "
        + (
            "Epics " + ", ".join(keys)
            if several
            else f"Epic {keys[0]}: {link(keys[0])}"
        ),
        "",
        "The agents build each Subtask on its own branch; one pull request is "
        "opened for each Story once all its Subtasks are done.",
        "",
    ]

    if result.plan.assumptions:
        lines.extend(
            [
                "I had to assume the following. Please correct anything that is "
                "wrong, then re-plan.",
                "",
                *[f"- {item}" for item in result.plan.assumptions],
                "",
            ]
        )

    created = result.created
    story_index = 0

    for epic_index, epic in enumerate(result.plan.epics):
        if several:
            key = created.epics[epic_index]
            lines.extend([f"Epic {key} {epic.summary}: {link(key)}", ""])

        for story in epic.stories:
            lines.append(f"- {created.stories[story_index]} {story.summary}")
            story_index += 1

            for sub in story.subtasks:
                needs = ", ".join(created.subtasks[ref] for ref in sub.depends_on)
                lines.append(
                    f"  - {created.subtasks[sub.ref]} {sub.summary}"
                    + (f" (after {needs})" if needs else "")
                )

        if several:
            lines.append("")

    return "\n".join(lines).rstrip()


class RequirementIntake:
    """Plans every new requirement issue into Jira."""

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        source: RequirementSource,
        planner: RequirementPlanner,
        jira: JiraApi,
        interval_seconds: float | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.source = source
        self.planner = planner
        self.jira = jira
        self.interval_seconds = (
            settings.poll_interval_seconds
            if interval_seconds is None
            else interval_seconds
        )
        self._attempts: dict[int, int] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background thread."""
        self._thread = threading.Thread(
            target=self._loop, name="requirement-intake", daemon=True
        )
        self._thread.start()
        logger.info("Requirement intake started: label=%s", self.source_label())

    def stop(self) -> None:
        """Ask the thread to stop and wait for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def source_label(self) -> str:
        """The label requirements are found by, for the log."""
        return str(getattr(self.source, "label", ""))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("Requirement intake failed")
            self._stop.wait(self.interval_seconds)

    def run_once(self) -> list[str]:
        """Plan the new requirements. Returns the keys of the Epics made."""
        epics: list[str] = []

        with self.session_factory() as session:
            known = set(session.scalars(select(Requirement.reference)))
            waiting = [
                issue
                for issue in self.source.list_requirements()
                if reference_for(issue) not in known
                and self._attempts.get(issue.number, 0) < MAX_ATTEMPTS
            ]

            for issue in waiting[:MAX_PER_CYCLE]:
                epic = self._plan_one(session, issue)

                if epic:
                    epics.append(epic)

        return epics

    def is_structured(self, issue: Issue) -> bool:
        """True if the issue carries the label that says it spells out its structure."""
        label = self.settings.requirement_structured_label.strip().casefold()
        return bool(label) and label in (name.casefold() for name in issue.labels)

    def _plan_one(self, session: Session, issue: Issue) -> str | None:
        """Plan one requirement, report it on the issue; None if it failed."""
        self._attempts[issue.number] = self._attempts.get(issue.number, 0) + 1

        try:
            result = plan_requirement(
                session,
                self.planner,
                self.jira,
                self.settings,
                issue.title,
                requirement_text(issue, self.source.discussion(issue.number)),
                reference_for(issue),
                structured=self.is_structured(issue),
            )
        except Exception as exc:
            session.rollback()
            logger.warning(
                "Could not plan requirement #%s (try %s of %s): %s",
                issue.number,
                self._attempts[issue.number],
                MAX_ATTEMPTS,
                exc,
            )

            if self._attempts[issue.number] >= MAX_ATTEMPTS:
                self.source.comment(
                    issue.number,
                    "The orchestrator could not plan this requirement into Jira "
                    f"after {MAX_ATTEMPTS} tries: {exc}",
                )

            return None

        self.source.comment(issue.number, outcome_comment(result, self.jira))
        self.source.mark_planned(issue.number)
        logger.info(
            "Requirement planned: issue=#%s epic=%s", issue.number, result.epic_key
        )
        return result.epic_key


def make_requirement_intake(
    settings: Settings, session_factory: sessionmaker[Session]
) -> RequirementIntake | None:
    """Build the intake for the settings, or None if GitHub or Jira is not set up."""
    repo = settings.github_repo or detect_github_repo(settings.repository_path)

    if repo is None:
        logger.warning("Requirement planning needs GITHUB_REPO; it is off.")
        return None

    try:
        jira: Any = make_jira_api(settings)
    except IssueSourceError as exc:
        logger.warning("Requirement planning is off: %s", exc)
        return None

    token = settings.github_token.get_secret_value() if settings.github_token else None
    api = GitHubApi(repo, token)

    if not api.can_write:
        logger.warning("No GITHUB_TOKEN: requirements are read, but not commented on.")

    return RequirementIntake(
        settings,
        session_factory,
        GitHubRequirementSource(
            api, settings.requirement_label, settings.requirement_planned_label
        ),
        RequirementPlanner(settings),
        jira,
    )
