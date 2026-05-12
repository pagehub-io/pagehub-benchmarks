"""Abstract harness adapter.

A harness is a headless LLM coding tool (Claude Code, etc.). The runner
talks to it through exactly two operations:

- ``start_build(worktree_dir, prompt, model, config)`` — invoke the harness
  fresh in ``worktree_dir`` with the build ``prompt``; it writes code.
- ``continue_build(session_handle, followup_prompt)`` — continue the *same*
  session (context carries) with a follow-up prompt (the failing-eval output).

Both return an :class:`AttemptResult`: token counts, wall time, and an opaque
``session_handle`` the runner threads back into ``continue_build``.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AttemptResult:
    """Outcome of one harness invocation (one attempt).

    ``input_tokens`` / ``output_tokens`` are the non-cached counts. Prompt-cache
    traffic is split into ``cache_creation_tokens`` (cache writes) and
    ``cache_read_tokens`` because they price differently; ``cache_tokens``
    sums them for the headline number recorded in the run record.
    """

    input_tokens: int
    output_tokens: int
    wall_time_seconds: float
    session_handle: str
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    # Whatever the harness reported as its own cost figure, if any (advisory —
    # the run record's cost_usd is recomputed from pricing.yaml).
    reported_cost_usd: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def cache_tokens(self) -> int:
        return self.cache_creation_tokens + self.cache_read_tokens


class Harness(abc.ABC):
    """Adapter contract. One instance is created per run."""

    #: Stable key used in benchmark YAML (``harnesses: [{harness: "claude-code"}]``).
    name: str = "abstract"

    @abc.abstractmethod
    def start_build(
        self,
        worktree_dir: str,
        prompt: str,
        model: str,
        config: dict[str, Any],
    ) -> AttemptResult:
        ...

    @abc.abstractmethod
    def continue_build(
        self,
        session_handle: str,
        followup_prompt: str,
    ) -> AttemptResult:
        ...
