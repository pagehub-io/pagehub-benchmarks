"""Scripted fakes for the runner tests — no real `claude`, no real pagehub-evals."""

from __future__ import annotations

from collections.abc import Sequence

from pagehub_benchmarks.grader import GraderResult
from pagehub_benchmarks.harnesses.base import AttemptResult, Harness


class FakeHarness(Harness):
    """Returns scripted AttemptResults; the last one repeats if asked again.

    Records ``calls`` as a list of ``("start", worktree, prompt, model, config)``
    or ``("continue", session_handle, followup)`` tuples.
    """

    name = "fake"

    def __init__(self, results: Sequence[AttemptResult]) -> None:
        if not results:
            raise ValueError("FakeHarness needs at least one scripted AttemptResult")
        self._results = list(results)
        self._i = 0
        self.calls: list[tuple] = []

    def _next(self) -> AttemptResult:
        r = self._results[min(self._i, len(self._results) - 1)]
        self._i += 1
        return r

    def start_build(self, worktree_dir, prompt, model, config) -> AttemptResult:  # noqa: ANN001
        self.calls.append(("start", worktree_dir, prompt, model, dict(config)))
        return self._next()

    def continue_build(self, session_handle, followup_prompt) -> AttemptResult:  # noqa: ANN001
        self.calls.append(("continue", session_handle, followup_prompt))
        return self._next()


class FakeGrader:
    """Returns scripted GraderResults; the last one repeats. ``setup_calls`` counts setup()."""

    def __init__(self, results: Sequence[GraderResult]) -> None:
        if not results:
            raise ValueError("FakeGrader needs at least one scripted GraderResult")
        self._results = list(results)
        self._i = 0
        self.setup_calls = 0
        self.grade_calls = 0

    def setup(self) -> None:
        self.setup_calls += 1

    def grade(self) -> GraderResult:
        self.grade_calls += 1
        r = self._results[min(self._i, len(self._results) - 1)]
        self._i += 1
        return r


def ar(
    input_tokens: int = 100,
    output_tokens: int = 50,
    wall_time_seconds: float = 1.0,
    session_handle: str = "sess-1",
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> AttemptResult:
    return AttemptResult(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        wall_time_seconds=wall_time_seconds,
        session_handle=session_handle,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
    )


def gr(passed: bool, failures: list[str] | None = None) -> GraderResult:
    return GraderResult(passed=passed, failures=list(failures or []))
