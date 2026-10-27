# Adds columns that newer versions need to existing SQLite databases.
# Safe to run on every start; it skips columns that already exist.
from sqlalchemy import Engine, inspect, text


def migrate_usage_columns(engine: Engine) -> None:
    """Add usage-tracking columns to existing agent_runs tables."""
    inspector = inspect(engine)

    if "agent_runs" not in inspector.get_table_names():
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("agent_runs")
    }

    migrations = {
        "cache_creation_input_tokens": "INTEGER",
        "cache_read_input_tokens": "INTEGER",
        "reported_cost_usd": "FLOAT",
    }

    with engine.begin() as connection:
        for column_name, column_type in migrations.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE agent_runs ADD COLUMN {column_name} {column_type}"
                    )
                )


def migrate_task_columns(engine: Engine) -> None:
    """Add pipeline-tracking columns to existing tasks tables."""
    inspector = inspect(engine)

    if "tasks" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("tasks")}

    migrations = {
        "current_stage": "VARCHAR(50)",
        "pull_request_url": "VARCHAR(500)",
        "project_item_id": "VARCHAR(100)",
        "board_number": "INTEGER",
        "board_column": "VARCHAR(50)",
        "jira_key": "VARCHAR(50)",
        "blocked_by": "TEXT",
    }

    with engine.begin() as connection:
        for column_name, column_type in migrations.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE tasks ADD COLUMN {column_name} {column_type}")
                )
