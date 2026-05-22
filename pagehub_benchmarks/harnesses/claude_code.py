"""Claude Code harness adapter.

Invokes the ``claude`` CLI headlessly:

    start_build:    claude -p "<prompt>"   --model <model> [--effort <effort>] \\
                        --output-format json --dangerously-skip-permissions
    continue_build: claude -p "<followup>" --resume <session_id> \\
                        --output-format json --dangerously-skip-permissions

Both run with ``cwd`` set to the worktree. Timing uses a monotonic clock.

**Auth — default path (Claude subscription).** Runs use the ``claude`` CLI's
*existing logged-in auth* (a Claude subscription) — flat-rate, not metered API
billing. This adapter explicitly **unsets ``ANTHROPIC_API_KEY``** in the
subprocess environment so a stray env key can't divert the run onto metered
API billing; the CLI falls back to its stored OAuth/subscription credentials.
If ``claude -p`` errors with "not logged in", the CLI's credentials aren't
reachable from the subprocess — fix that (``claude login``), don't set an API
key. The ``cost_usd`` in a run record is a *computed* figure (tokens ×
``pricing.yaml``), not an API bill.

**Auth — gateway path.** When the harness config carries a ``gateway`` block
(``{url, auth_token_env}``), the adapter sets ``ANTHROPIC_BASE_URL=<url>`` and
``ANTHROPIC_AUTH_TOKEN=<value of env var named by auth_token_env>`` in the
subprocess env (default env-var name: ``GATEWAY_AUTH_TOKEN``). The actual key
value is read from the runner's process env at dispatch — never baked into the
YAML, never written to the run record. ``--model`` is passed through verbatim
to ``claude -p`` and on to the gateway, which routes by model-name prefix.
``ANTHROPIC_API_KEY`` is still stripped — we use ``AUTH_TOKEN``, not
``API_KEY``, and the CLI's Max-subscription auth must not bleed through into a
gateway-routed run.

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
DEFAULT_GATEWAY_AUTH_TOKEN_ENV = "GATEWAY_AUTH_TOKEN"
_VALID_EFFORT = {"low", "medium", "high", "xhigh", "max"}


class HarnessError(RuntimeError):
    """The harness invocation failed (non-zero exit, timeout, or unparsable output)."""


def _subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A copy of the current env with ``ANTHROPIC_API_KEY`` removed, plus any
    ``extra`` overlay merged on top.

    Headless ``claude`` runs under the CLI's stored subscription auth
    (flat-rate). An ``ANTHROPIC_API_KEY`` in the environment would divert it
    onto metered API billing — so we drop it. (If the CLI's credentials aren't
    reachable, ``claude -p`` will say "not logged in" — that's the signal to
    re-auth, not to set a key.)

    ``extra`` is how the gateway path injects ``ANTHROPIC_BASE_URL`` +
    ``ANTHROPIC_AUTH_TOKEN``. When ``extra`` is empty/None, behavior is
    identical to the no-gateway path.
    """
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    if extra:
        env.update(extra)
    return env


def _gateway_env_overlay(config: dict[str, Any] | None) -> dict[str, str]:
    """Translate a ``gateway: {url, auth_token_env}`` config block into the
    env-var overlay that points ``claude -p`` at the gateway.

    Returns ``{}`` (no overlay) when the block is absent — that's the
    subscription/default path.

    Raises :class:`HarnessError` when the block is present but malformed
    (no ``url``, or the named auth-token env var isn't set in the runner's
    process env). The auth token's *value* is never logged or echoed.
    """
    gw = (config or {}).get("gateway")
    if gw is None:
        return {}
    if not isinstance(gw, dict):
        raise HarnessError(
            f"harness config 'gateway' must be a mapping, got {type(gw).__name__}"
        )
    url = str(gw.get("url") or "").strip()
    if not url:
        raise HarnessError("harness config 'gateway' is missing required 'url'")
    auth_env = str(gw.get("auth_token_env") or DEFAULT_GATEWAY_AUTH_TOKEN_ENV).strip()
    if not auth_env:
        raise HarnessError("harness config 'gateway.auth_token_env' must be a non-empty string")
    token = os.environ.get(auth_env, "")
    if not token:
        raise HarnessError(
            f"gateway auth token env var {auth_env!r} is unset in the runner's "
            "environment (set it before invoking, or set ANTHROPIC_AUTH_TOKEN "
            "via a different env-var name in gateway.auth_token_env)"
        )
    return {
        "ANTHROPIC_BASE_URL": url,
        "ANTHROPIC_AUTH_TOKEN": token,
    }


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
    """Extract (input, output, cache_creation, cache_read) token counts.

    Newer Claude Code CLI builds (≥ Opus 4.7) dispatch sub-tasks to a
    secondary model (e.g. Haiku 4.5 as a routing agent) and surface those
    tokens *only* under ``modelUsage`` — the top-level ``usage`` field
    captures the primary model's slice. If we read top-level ``usage``
    alone, the sub-agent's tokens vanish and recomputed cost is artificially
    low. (We saw exactly this on the Opus 4.7 / effort=high run: 12 input +
    132 output reported, but the CLI billed for additional Haiku traffic
    invisible to us.)

    Resolution: when ``modelUsage`` is present (a mapping of model-id ->
    per-model usage block, camelCase keys), sum across all entries. Otherwise
    fall back to the top-level ``usage`` (snake_case keys) for the legacy
    shape.

    Caveat: summed tokens are priced at the matrix row's model rate — i.e.
    a Haiku sub-agent's 100 input tokens get charged at Opus rates in our
    ``cost_usd``. Acceptable approximation; the alternative is per-sub-model
    pricing, which means weighting the per-row price table by ``modelUsage``.
    """
    model_usage = data.get("modelUsage")
    if isinstance(model_usage, dict) and model_usage:
        in_tok = out_tok = cache_create = cache_read = 0
        for per_model in model_usage.values():
            if not isinstance(per_model, dict):
                continue
            in_tok += int(per_model.get("inputTokens", 0) or 0)
            out_tok += int(per_model.get("outputTokens", 0) or 0)
            cache_create += int(per_model.get("cacheCreationInputTokens", 0) or 0)
            cache_read += int(per_model.get("cacheReadInputTokens", 0) or 0)
        return (in_tok, out_tok, cache_create, cache_read)
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
        # Env overlay (gateway routing, if configured). Set in start_build,
        # reused by every continue_build on this run.
        self._env_overlay: dict[str, str] = {}
        # The model id passed to start_build, replayed on every continue_build.
        # ``claude -p --resume`` does NOT carry the model selection from the
        # resumed session — the CLI falls back to its stored default (typically
        # claude-opus-4-7), which a gateway-routed run can't service: the
        # gateway routes by model-name prefix, so a stray claude-* on attempt 2
        # 400s with "model not handled by any registered provider". Even on
        # the native subscription path this is safer than letting the CLI's
        # default leak into a continuation.
        self._model: str | None = None

    # -- helpers ---------------------------------------------------------

    def _run(self, cmd: list[str], cwd: str) -> AttemptResult:
        env = _subprocess_env(self._env_overlay)
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
        self._env_overlay = _gateway_env_overlay(config)
        self._model = model
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
        if not self._model:
            raise HarnessError("continue_build called before start_build (no model remembered)")
        cmd = [
            "claude",
            "-p",
            followup_prompt,
            "--resume",
            session_handle,
            "--model",
            self._model,
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
        ]
        return self._run(cmd, cwd=self._worktree_dir)
