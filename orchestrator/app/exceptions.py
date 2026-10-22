class OrchestratorError(Exception):
    """Base exception for expected orchestrator failures."""


class ConfigurationError(OrchestratorError):
    """Raised when required configuration is invalid."""


class RepositoryError(OrchestratorError):
    """Raised when a Git repository operation fails."""


class AgentExecutionError(OrchestratorError):
    """Raised when an agent cannot execute successfully."""


class VerificationError(OrchestratorError):
    """Raised when a verification operation fails."""
