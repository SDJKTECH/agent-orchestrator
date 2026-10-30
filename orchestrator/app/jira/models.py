# The Jira side of the database: one row per Jira issue (Epic, Story, Task, Sub-task).
# `parent_key` links an issue to its parent, so Epic > Story > Task is a chain of rows.
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from orchestrator.app.db.database import Base


def utc_now() -> datetime:
    """The current time in UTC, used as a column default."""
    return datetime.now(UTC)


class JiraItem(Base):
    """A Jira issue as last read from Jira; only Tasks become orchestrator tasks."""

    __tablename__ = "jira_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Jira keys are unique across a site, e.g. "SG-12".
    key: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    # The Jira issue type name: "Epic", "Story", "Task", "Sub-task", "Bug"...
    type: Mapped[str] = mapped_column(String(50), default="")
    summary: Mapped[str] = mapped_column(String(500), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    jira_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Key of the parent issue (a Task's Story, a Story's Epic); None at the top.
    parent_key: Mapped[str | None] = mapped_column(
        ForeignKey("jira_items.key"), nullable=True, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    parent: Mapped["JiraItem | None"] = relationship(
        back_populates="children", remote_side=[key]
    )
    children: Mapped[list["JiraItem"]] = relationship(back_populates="parent")

    def ancestors(self) -> list["JiraItem"]:
        """The parent chain, outermost first (Epic, then Story)."""
        chain: list[JiraItem] = []
        seen = {self.key}
        item = self.parent

        while item is not None and item.key not in seen:
            chain.append(item)
            seen.add(item.key)
            item = item.parent

        chain.reverse()
        return chain
