from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from agentllm.core.agent import Agent
from agentllm.infra.config import OpenAISettings
from agentllm.infra.errors import ConfigurationError, MCPConnectionError
from agentllm.infra.logging import configure_logging
from agentllm.integrations.mcp import MCPClient

from agentllm.application.bootstrap import build_agent, build_agent_with_mcp


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MCP_SERVER = PROJECT_ROOT / "agentllm" / "mcp_servers" / "workspace.py"


def run_cli() -> None:
    """Start the production agent CLI."""

    configure_logging()
    use_mcp = os.getenv("ENABLE_MCP", "").strip() == "1"

    try:
        agent, mcp_client = _build_runtime_agent(use_mcp=use_mcp)
    except ConfigurationError as exc:
        print(f"Startup error: {exc}")
        print("Please set API_KEY, BASE_URL, and MODEL before starting the agent.")
        return
    except MCPConnectionError as exc:
        print(f"Startup error: failed to initialize MCP: {exc}")
        return

    try:
        _run_repl(agent, use_mcp=use_mcp)
    finally:
        if mcp_client is not None:
            mcp_client.close()


def _build_runtime_agent(
    *, use_mcp: bool
) -> tuple[Agent, Optional[MCPClient]]:
    settings = OpenAISettings.load()
    if use_mcp:
        return build_agent_with_mcp(
            settings,
            [sys.executable, str(DEFAULT_MCP_SERVER)],
        )
    return build_agent(settings), None


def _run_repl(agent: Agent, *, use_mcp: bool) -> None:
    print("Real agent is ready.")
    print("Type `exit` or `quit` to stop.")
    print(f"MCP enabled: {'yes' if use_mcp else 'no'}")
    print("-" * 80)

    while True:
        try:
            user_input = input("USER> ").strip()
        except KeyboardInterrupt:
            print("\nInterrupted. Type `exit` to quit.")
            print("-" * 80)
            continue
        except EOFError:
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            break

        try:
            result = agent.run(user_input)
            print(f"ASSISTANT> {result.output}")
        except Exception as exc:
            print(f"ASSISTANT> Runtime failure: {exc}")
        print("-" * 80)
