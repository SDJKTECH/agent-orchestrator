from sqlalchemy import create_engine, inspect, text

from orchestrator.app.migrations import migrate_usage_columns


def test_migrate_usage_columns_adds_missing_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE agent_runs (
                    id INTEGER PRIMARY KEY,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, input_tokens, output_tokens) "
                "VALUES (1, 12, 8)"
            )
        )

    migrate_usage_columns(engine)
    migrate_usage_columns(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("agent_runs")}
    assert "cache_creation_input_tokens" in columns
    assert "cache_read_input_tokens" in columns
    assert "reported_cost_usd" in columns

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT input_tokens, output_tokens FROM agent_runs WHERE id = 1")
        ).one()

    assert row == (12, 8)
    engine.dispose()
