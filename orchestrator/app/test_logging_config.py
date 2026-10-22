
import logging

from orchestrator.app.exceptions import (
    AgentExecutionError,
    OrchestratorError,
    RepositoryError,
)
from orchestrator.app.logging_config import get_logger


def test_get_logger_returns_named_logger() -> None:
    logger = get_logger("orchestrator.tests")

    assert isinstance(logger, logging.Logger)
    assert logger.name == "orchestrator.tests"


def test_custom_exceptions_share_base_class() -> None:
    assert issubclass(AgentExecutionError, OrchestratorError)
    assert issubclass(RepositoryError, OrchestratorError)


def test_logger_can_emit_messages(caplog) -> None:
    logger = get_logger("orchestrator.test_messages")

    with caplog.at_level(logging.INFO, logger=logger.name):
        logger.info("Agent run started: run_id=%s", "test-run-001")

    assert "Agent run started" in caplog.text
    assert "test-run-001" in caplog.text
