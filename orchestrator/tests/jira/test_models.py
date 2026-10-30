import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from orchestrator.app.db.database import Base
from orchestrator.app.db.migrations import migrate_task_columns
from orchestrator.app.db.models import Milestone, Project, Task
from orchestrator.app.jira.models import JiraItem


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with sessionmaker(bind=engine)() as db:
        db.add(Project(name="Snake Game"))
        db.commit()
        yield db


def item(key: str, kind: str, parent: str | None = None) -> JiraItem:
    return JiraItem(key=key, project_id=1, type=kind, summary=key, parent_key=parent)


def test_a_task_knows_its_story_and_epic(session) -> None:
    session.add_all([item("SG-1", "Epic"), item("SG-2", "Story", "SG-1")])
    session.flush()
    session.add(item("SG-3", "Task", "SG-2"))
    session.commit()

    task = session.query(JiraItem).filter_by(key="SG-3").one()

    assert [parent.key for parent in task.ancestors()] == ["SG-1", "SG-2"]
    assert [child.key for child in task.parent.children] == ["SG-3"]


def test_an_item_without_a_parent_has_no_ancestors(session) -> None:
    session.add(item("SG-9", "Task"))
    session.commit()

    assert session.query(JiraItem).one().ancestors() == []


def test_a_parent_loop_does_not_hang(session) -> None:
    session.add_all([item("SG-1", "Story"), item("SG-2", "Story")])
    session.flush()
    session.query(JiraItem).filter_by(key="SG-1").one().parent_key = "SG-2"
    session.query(JiraItem).filter_by(key="SG-2").one().parent_key = "SG-1"
    session.commit()

    first = session.query(JiraItem).filter_by(key="SG-1").one()

    assert [parent.key for parent in first.ancestors()] == ["SG-2"]


def test_a_jira_key_is_stored_once(session) -> None:
    session.add(item("SG-1", "Task"))
    session.commit()
    session.add(item("SG-1", "Task"))

    with pytest.raises(IntegrityError):
        session.commit()


def test_a_task_points_at_its_jira_item(session) -> None:
    session.add(Milestone(project_id=1, code="M1", title="M1"))
    session.add(item("SG-3", "Task"))
    session.flush()
    jira_item = session.query(JiraItem).one()
    session.add(Task(milestone_id=1, title="SG-3: t", jira_item_id=jira_item.id))
    session.commit()

    assert session.query(Task).one().jira_item.key == "SG-3"


def test_migration_adds_the_jira_item_column(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE tasks (id INTEGER PRIMARY KEY)"))

    migrate_task_columns(engine)
    migrate_task_columns(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("tasks")}
    assert "jira_item_id" in columns
