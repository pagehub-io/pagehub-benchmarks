"""Harness adapters: a uniform interface over headless LLM coding tools."""

from pagehub_benchmarks.harnesses.base import AttemptResult, Harness
from pagehub_benchmarks.harnesses.claude_code import ClaudeCodeHarness
from pagehub_benchmarks.harnesses.codex_cli import CodexCliHarness

# Registry: benchmark YAML names a harness by key; the runner looks it up here.
HARNESSES: dict[str, type[Harness]] = {
    "claude-code": ClaudeCodeHarness,
    "codex-cli": CodexCliHarness,
}


def get_harness(name: str) -> Harness:
    try:
        cls = HARNESSES[name]
    except KeyError as exc:
        raise ValueError(
            f"unknown harness {name!r}; known: {sorted(HARNESSES)}"
        ) from exc
    return cls()


__all__ = [
    "AttemptResult",
    "Harness",
    "ClaudeCodeHarness",
    "CodexCliHarness",
    "HARNESSES",
    "get_harness",
]
