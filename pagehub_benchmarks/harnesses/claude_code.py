"""Claude Code harness adapter.

Invokes the ``claude`` CLI headlessly:

    start_build:    claude -p "<prompt>"   --model <model> [--effort <effort>] \\
                        --output-format json --dangerously-skip-permissions
    continue_build: claude -p "<followup>" --resume <session_id> \\
                        --output-format json --dangerously-skip-permissions

Both run with ``cwd`` set to the worktree. Timing uses a monotonic clock.

**Auth.** Runs use the ``claude`` CLI's *existing logged-in auth* (a Claude
subscription) — flat-rate, not metered API billing. This adapter explicitly
**unsets ``ANTHROPIC_API_KEY``** in the subprocess environment so a stray env
key can't divert the run onto metered API billing; the CLI falls back to its
stored OAuth/subscription credentials. If ``claude -p`` errors with "not
logged in", the CLI's credentials aren't reachable from the subprocess — fix
that (``claude login``), don't set an API key. The ``cost_usd`` in a run
record is a *computed* figure (tokens × ``pricing.yaml``), not an API bill.

**Effort.** ``config["effort"]`` (one of low/medium/high/xhigh/max) is passed
through as ``--effort <effort>``.

**Build timeout.** ``CLAUDE_BUILD_TIMEOUT_SECONDS`` (default 3600) bounds each
invocation. On timeout the subprocess is killed and a :class:`HarnessError`
is raised.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

from pagehub_benchmarks.harnesses.base import AttemptResult, Harness

DEFAULT_BUILD_TIMEOUT_SECONDS = 3600
_VALID_EFFORT = {"low", "medium", "high", "xhigh", "max"}


class HarnessError(RuntimeError):
    """The harness invocation failed (non-zero exit, timeout, or unparsable output)."""


def _subprocess_env() -> dict[str, str]:
    """A copy of the current env with ``ANTHROPIC_API_KEY`` removed.

    Headless ``claude`` runs under the CLI's stored subscription auth
    (flat-rate). An ``ANTHROPIC_API_KEY`` in the environment would divert it
    onto metered API billing — so we drop it. (If the CLI's credentials aren't
    reachable, ``claude -p`` will say "not logged in" — that's the signal to
    re-auth, not to set a key.)
    """
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def _build_timeout() -> int:
    raw = os.environ.get("CLAUDE_BUILD_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_BUILD_TIMEOUT_SECONDS
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_BUILD_TIMEOUT_SECONDS


def _parse_cli_json(stdout: str) -> dict[str, Any]:
    """Parse ``claude -p --output-format json`` output (a single JSON object).

    Falls back to the last JSON-looking line if the harness emitted log noise
    before the result object.
    """
    stdout = (stdout or "").strip()
    if not stdout:
        raise HarnessError("claude produced no stdout")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    raise HarnessError(f"could not parse claude JSON output: {stdout[:500]!r}")


def _usage_from(data: dict[str, Any]) -> tuple[int, int, int, int]:
    usage = data.get("usage") or {}
    return (
        int(usage.get("input_tokens", 0) or 0),
        int(usage.get("output_tokens", 0) or 0),
        int(usage.get("cache_creation_input_tokens", 0) or 0),
        int(usage.get("cache_read_input_tokens", 0) or 0),
    )


class ClaudeCodeHarness(Harness):
    name = "claude-code"

    def __init__(self) -> None:
        # Remembered from start_build so continue_build resumes in the same
        # directory. (One harness instance per run — see Harness docstring.)
        self._worktree_dir: str | None = None

    # -- helpers ---------------------------------------------------------

    def _run(self, cmd: list[str], cwd: str) -> AttemptResult:
        env = _subprocess_env()
        started = time.monotonic()
        try:
            proc = subprocess.run(  # noqa: S603 — args are constructed, not shell
                cmd,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=_build_timeout(),
            )
        except subprocess.TimeoutExpired as exc:
            raise HarnessError(
                f"claude timed out after {_build_timeout()}s: {' '.join(cmd[:3])} ..."
            ) from exc
        wall = time.monotonic() - started
        if proc.returncode != 0:
            raise HarnessError(
                f"claude exited {proc.returncode}: stderr={proc.stderr[:1000]!r} "
                f"stdout={proc.stdout[:500]!r}"
            )
        data = _parse_cli_json(proc.stdout)
        if data.get("is_error"):
            raise HarnessError(f"claude reported an error: {data.get('result') or data}")
        in_tok, out_tok, cache_w, cache_r = _usage_from(data)
        session_id = data.get("session_id") or self._worktree_dir or ""
        return AttemptResult(
            input_tokens=in_tok,
            output_tokens=out_tok,
            wall_time_seconds=wall,
            session_handle=str(session_id),
            cache_creation_tokens=cache_w,
            cache_read_tokens=cache_r,
            reported_cost_usd=data.get("total_cost_usd"),
            raw=data,
        )

    # -- Harness interface ----------------------------------------------

    def start_build(
        self,
        worktree_dir: str,
        prompt: str,
        model: str,
        config: dict[str, Any],
    ) -> AttemptResult:
        self._worktree_dir = worktree_dir
        cmd = [
            "claude",
            "-p",
            prompt,
            "--model",
            model,
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
        ]
        effort = (config or {}).get("effort")
        if effort:
            if effort not in _VALID_EFFORT:
                raise HarnessError(
                    f"unknown effort {effort!r}; valid: {sorted(_VALID_EFFORT)}"
                )
            cmd += ["--effort", str(effort)]
        return self._run(cmd, cwd=worktree_dir)

    def continue_build(
        self,
        session_handle: str,
        followup_prompt: str,
    ) -> AttemptResult:
        if not self._worktree_dir:
            raise HarnessError("continue_build called before start_build")
        cmd = [
            "claude",
            "-p",
            followup_prompt,
            "--resume",
            session_handle,
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
        ]
        return self._run(cmd, cwd=self._worktree_dir)
