"""Command-line entry point for the agentllm runtime."""

from __future__ import annotations

from agentllm.application.cli import run_cli


def main() -> None:
    """Start the interactive CLI."""

    run_cli()


if __name__ == "__main__":
    main()
