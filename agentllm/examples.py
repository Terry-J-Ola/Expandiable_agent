"""Compatibility wrapper for legacy imports."""

from agentllm.application.bootstrap import build_agent, build_agent_with_mcp

build_openai_agent = build_agent
build_openai_agent_with_mcp = build_agent_with_mcp

__all__ = ["build_agent", "build_agent_with_mcp", "build_openai_agent", "build_openai_agent_with_mcp"]
