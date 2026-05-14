"""Scripted fakes for the runner tests — no real `claude`, no real pagehub-evals."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pagehub_benchmarks.config import BenchmarkSpec
from pagehub_benchmarks.grader import GraderResult
from pagehub_benchmarks.harnesses.base import AttemptResult, Harness
from pagehub_benchmarks.runner.push import PushResult, github_https_url


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


class FakeFixtureFetcher:
    """Returns a scripted fixture body. Records each ``fetch(spec)`` call.

    ``raise_with`` makes ``fetch`` raise that exception instead — used by the
    "unresolved fixture" tests to exercise the renderer's error path.
    """

    def __init__(
        self,
        body: str = '{"version": 1, "collections": []}',
        *,
        raise_with: Exception | None = None,
    ) -> None:
        self.body = body
        self._raise_with = raise_with
        self.calls: list[BenchmarkSpec] = []

    def fetch(self, spec: BenchmarkSpec) -> str:
        self.calls.append(spec)
        if self._raise_with is not None:
            raise self._raise_with
        return self.body


class FakePusher:
    """Records pushes; never hits a real remote.

    ``target_empty`` lets a test pretend the target repo has no commits (so the
    runner asks for a default-branch push). ``fail_with`` makes the next push
    return a PushResult with that error string (the branch push fails);
    ``fail_default_with`` fails only the default-branch leg. ``raise_with``
    raises that exception from push() to exercise the runner's exception path.
    """

    def __init__(
        self,
        *,
        target_empty: bool = False,
        commit: str = "deadbeef" * 5,
        fail_with: str | None = None,
        fail_default_with: str | None = None,
        raise_with: Exception | None = None,
    ) -> None:
        self.target_empty = target_empty
        self._commit = commit
        self._fail_with = fail_with
        self._fail_default_with = fail_default_with
        self._raise_with = raise_with
        self.is_empty_calls: list[str] = []
        self.push_calls: list[dict] = []

    def is_target_empty(self, target_repo: str) -> bool:
        self.is_empty_calls.append(target_repo)
        return self.target_empty

    def push(
        self,
        *,
        worktree: Path,
        target_repo: str,
        branch: str,
        push_to_default_branch: bool,
    ) -> PushResult:
        self.push_calls.append(
            {
                "worktree": str(worktree),
                "target_repo": target_repo,
                "branch": branch,
                "push_to_default_branch": push_to_default_branch,
            }
        )
        if self._raise_with is not None:
            raise self._raise_with
        if self._fail_with is not None:
            return PushResult(
                pushed_commit=self._commit,
                error=self._fail_with,
            )
        url = f"{github_https_url(target_repo)}/tree/{branch}"
        result = PushResult(
            pushed_branch=branch,
            pushed_branch_url=url,
            pushed_commit=self._commit,
            pushed_at="2026-05-12T16:30:00Z",
        )
        if push_to_default_branch:
            if self._fail_default_with is not None:
                result.error = self._fail_default_with
            else:
                result.pushed_to_default_branch = True
        return result
