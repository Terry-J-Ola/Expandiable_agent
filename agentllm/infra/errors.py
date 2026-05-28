from __future__ import annotations


class AgentLLMError(RuntimeError):
    """Base class for project-specific runtime errors."""


class ConfigurationError(AgentLLMError):
    """Raised when required configuration is missing or invalid."""


class ProviderError(AgentLLMError):
    """Raised when an LLM provider call fails."""


class ProviderNetworkError(ProviderError):
    """Raised when a provider cannot be reached."""


class ProviderHTTPError(ProviderError):
    """Raised when a provider returns an HTTP error."""


class MCPConnectionError(AgentLLMError):
    """Raised when an MCP client cannot connect or shuts down unexpectedly."""


class MCPRequestError(AgentLLMError):
    """Raised when an MCP request fails after connection."""
