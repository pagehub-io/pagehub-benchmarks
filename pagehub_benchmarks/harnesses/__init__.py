"""Harness adapters: a uniform interface over headless LLM coding tools."""

from pagehub_benchmarks.harnesses.base import AttemptResult, Harness
from pagehub_benchmarks.harnesses.claude_code import ClaudeCodeHarness

# Registry: benchmark YAML names a harness by key; the runner looks it up here.
HARNESSES: dict[str, type[Harness]] = {
    "claude-code": ClaudeCodeHarness,
}


def get_harness(name: str) -> Harness:
    try:
        cls = HARNESSES[name]
    except KeyError as exc:
        raise ValueError(
            f"unknown harness {name!r}; known: {sorted(HARNESSES)}"
        ) from exc
    return cls()


__all__ = ["AttemptResult", "Harness", "ClaudeCodeHarness", "HARNESSES", "get_harness"]
