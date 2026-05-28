from __future__ import annotations

"""Assemble configuration, provider, tools, and memory into a runnable Agent."""

import os

from agentllm.core.agent import Agent
from agentllm.core.memory import WindowMemory
from agentllm.core.policy import LLMPolicy
from agentllm.core.safety import SafetyPolicy
from agentllm.infra.config import OpenAISettings
from agentllm.integrations.mcp import MCPClient, MCPServerConfig, create_mcp_client
from agentllm.providers.openai import OpenAIProvider
from agentllm.tools import (
    ToolRegistry,
    discover_mcp_tools_sync,
    make_calculator_tool,
    make_read_docx_file_tool,
    make_utc_now_tool,
    make_read_file_tool,
    make_read_pdf_file_tool,
    make_weather_tool,
    make_web_search_tool,
    make_write_text_tool,
)


def build_agent(settings: OpenAISettings) -> Agent:
    """Build a production agent without MCP integration."""

    registry = _build_base_registry()
    return _make_agent(settings, registry)


def build_agent_with_mcp(
    settings: OpenAISettings,
    server_command: list[str],
) -> tuple[Agent, MCPClient]:
    """Build a production agent with MCP-backed tools attached."""

    registry, mcp_client = _build_registry_with_stdio_mcp(server_command)
    return _make_agent(settings, registry), mcp_client


def build_agent_with_remote_mcp(
    settings: OpenAISettings,
    server_url: str,
    headers: dict[str, str] | None = None,
) -> tuple[Agent, MCPClient]:
    """Build a production agent with tools discovered from a remote HTTP MCP server."""

    registry, mcp_client = _build_registry_with_remote_mcp(server_url, headers=headers)
    return _make_agent(settings, registry), mcp_client


def _make_agent(settings: OpenAISettings, registry: ToolRegistry) -> Agent:
    provider = OpenAIProvider(settings)
    policy = LLMPolicy(provider)
    safety = SafetyPolicy(
        max_steps=12,
        max_tool_calls_per_step=8,
    ).with_allowed_tools(registry.names())

    # 在运行时读取环境变量。这样根目录 .env 先被 OpenAISettings.load() 加载后，
    # 这里才能正确拿到自定义系统提示词；如果没配置，就回退默认提示词。
    system_prompt = os.getenv("SYSTEM_PROMPT", "").strip() or Agent.default_system_prompt()

    return Agent(
        policy=policy,
        tool_registry=registry,
        safety_policy=safety,
        memory=WindowMemory(
            system_prompt=system_prompt,
            max_messages=16,
        ),
    )


def _build_base_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(make_calculator_tool())
    registry.register(make_utc_now_tool())
    registry.register(make_web_search_tool())
    registry.register(make_read_file_tool())
    registry.register(make_read_pdf_file_tool())
    registry.register(make_read_docx_file_tool())
    registry.register(make_weather_tool())
    registry.register(make_write_text_tool())
    return registry


def _build_registry_with_stdio_mcp(
    server_command: list[str],
) -> tuple[ToolRegistry, MCPClient]:
    return _build_registry_with_mcp(
        MCPServerConfig(
            transport="stdio",
            command=server_command,
        )
    )


def _build_registry_with_remote_mcp(
    server_url: str,
    headers: dict[str, str] | None = None,
) -> tuple[ToolRegistry, MCPClient]:
    return _build_registry_with_mcp(
        MCPServerConfig(
            transport="remote-http",
            server_url=server_url,
            headers=headers,
        )
    )


def _build_registry_with_mcp(config: MCPServerConfig) -> tuple[ToolRegistry, MCPClient]:
    registry = _build_base_registry()
    mcp_client = create_mcp_client(config)
    mcp_client.connect()

    for tool in discover_mcp_tools_sync(mcp_client):
        registry.register(tool)

    return registry, mcp_client
