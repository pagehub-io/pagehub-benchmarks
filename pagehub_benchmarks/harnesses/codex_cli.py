"""Codex CLI harness adapter.

Drives OpenAI's ``codex`` CLI headlessly (``codex exec``), the way
:mod:`pagehub_benchmarks.harnesses.claude_code` drives ``claude -p``::

    start_build:    codex exec --ignore-user-config -m <model> --json -C <worktree> \\
                        --sandbox workspace-write \\
                        -c 'model_reasoning_effort="<effort>"' \\
                        -c 'sandbox_workspace_write.network_access=true' -
    continue_build: codex exec resume <thread_id> --ignore-user-config --json \\
                        -m <model> -c 'model_reasoning_effort="<effort>"' \\
                        -c 'sandbox_mode="workspace-write"' \\
                        -c 'sandbox_workspace_write.network_access=true' -

Design notes (the full rationale, with what was verified how, is in
``plans/codex-cli-harness.md``):

**Prompt on stdin.** The build prompt / follow-up is written to the
subprocess's stdin and ``-`` is passed as the positional prompt. Verified
byte-identical in the recorded session; also closes the hole where codex
appends a piped stdin to a positional prompt as a ``<stdin>`` block.

**Effort is required and explicit.** ``config["effort"]`` must be one of
``low|medium|high|xhigh|max`` (an explicit map onto the values ``codex debug
models`` lists for gpt-6-astra; ``ultra`` is deliberately unmapped). Anything
else raises before any subprocess. It is re-passed on every resume: a bare
``codex exec resume`` was observed to reset the effort to the model default.
Codex itself does not validate the value, so this map is the only guard.

**Sandbox.** ``workspace-write`` (never ``danger-full-access``) with network
enabled inside the sandbox, on both legs, so a resume never changes the
resolved sandbox settings (codex injects a ``<permissions instructions>``
message into the thread when they change). ``--ignore-user-config`` on both
legs keeps the operator's ``~/.codex/config.toml`` out of the run; the two
things that matter for comparability (effort, sandbox) are pinned explicitly.

**Auth — subscription only.** ``CODEX_API_KEY`` (a live runtime auth source
that would silently move the run onto metered API billing), ``CODEX_ACCESS_TOKEN``
and ``OPENAI_API_KEY`` are removed from the subprocess environment. Before the
first leg, ``codex login status`` must exit 0 and report ``Logged in using
ChatGPT``; any other mode (not logged in, a stored API key, an access token)
raises. ``cost_usd`` in a run record is therefore a *computed* figure
(tokens × ``pricing.yaml``), as for the Claude adapter.

**Failure semantics.** Classified structurally from the ``--json`` event
stream, never by matching error text:

- no ``thread.started`` at all → :class:`HarnessError` (nothing to resume);
- a *dead* turn — no non-error ``item.*`` event and no usage — is retried
  ``CODEX_DEAD_TURN_RETRIES`` times (default 2, 5 s apart) and then raises on
  either leg, exactly as the Claude adapter raises on a non-zero exit. Auth /
  transport failures and a rejected (``invalid_prompt``) prompt are dead turns;
- a turn in which the model *did* work and then failed is **captured**: an
  :class:`AttemptResult` with the error text under ``raw["harness_error"]``
  (the ``turn.failed`` message, else the last ``error`` event, else the
  stderr tail, else the exit code), so the runner grades whatever was written
  and resumes the thread;
- ``turn.completed`` with no usage from any source raises — a success is
  never recorded with zero tokens;
- timeout (``CODEX_BUILD_TIMEOUT_SECONDS``, default 3600) kills the whole
  process group, drains the pipes and raises.

**Token usage.** ``_usage_from`` is the single place the event stream is
mapped onto token counts. It is calibrated against recorded fixtures
(``tests/fixtures/codex_exec_ok.jsonl`` / ``codex_exec_resume_ok.jsonl``), not
against docs; until those fixtures exist it finds no usage and returns
``usage_source="none"`` (see ``plans/codex-cli-harness.md`` §6).
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import re
import signal
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pagehub_benchmarks.harnesses.base import AttemptResult, Harness
from pagehub_benchmarks.harnesses.claude_code import HarnessError

DEFAULT_BUILD_TIMEOUT_SECONDS = 3600
DEFAULT_DEAD_TURN_RETRIES = 2
DEAD_TURN_RETRY_PAUSE_SECONDS = 5.0
PREFLIGHT_TIMEOUT_SECONDS = 30
KILL_GRACE_SECONDS = 10
DRAIN_TIMEOUT_SECONDS = 10
RAW_LIST_LIMIT = 20
STDERR_TAIL_CHARS = 2000
SUBSCRIPTION_MODE_LINE = "Logged in using ChatGPT"
STRIPPED_ENV_VARS = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")
# config.effort -> codex model_reasoning_effort. Explicit, not a pass-through:
# codex accepts any string silently. ``ultra`` (automatic sub-agent delegation)
# is intentionally absent.
EFFORT_MAP: dict[str, str] = {e: e for e in ("low", "medium", "high", "xhigh", "max")}

# CSI sequences (colours, cursor) and OSC sequences (hyperlinks, titles).
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_TERMINAL_TYPES = ("turn.completed", "turn.failed")

__all__ = ["CodexCliHarness", "HarnessError", "EFFORT_MAP", "STRIPPED_ENV_VARS"]


# -- environment / config helpers -------------------------------------------


def _subprocess_env() -> dict[str, str]:
    """A copy of the current env minus every codex auth env var.

    ``CODEX_API_KEY`` is a live runtime credential in codex 0.154 (verified: a
    bogus value changes the 401 to "Incorrect API key provided"), so leaving
    it in place would divert a run onto metered API billing. ``CODEX_ACCESS_TOKEN``
    is read as an auth source too. ``OPENAI_API_KEY`` is not read at runtime
    by 0.154 but is stripped as well (task requirement; future-proof).
    ``CODEX_HOME`` is left alone — that is where the operator's login lives.
    """
    env = dict(os.environ)
    for key in STRIPPED_ENV_VARS:
        env.pop(key, None)
    return env


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _build_timeout() -> int:
    return _int_env("CODEX_BUILD_TIMEOUT_SECONDS", DEFAULT_BUILD_TIMEOUT_SECONDS)


def _dead_turn_retries() -> int:
    return max(0, _int_env("CODEX_DEAD_TURN_RETRIES", DEFAULT_DEAD_TURN_RETRIES))


def _map_effort(config: dict[str, Any] | None) -> str:
    effort = (config or {}).get("effort")
    if not isinstance(effort, str) or effort not in EFFORT_MAP:
        raise HarnessError(
            f"codex-cli requires config.effort to be one of {sorted(EFFORT_MAP)}; "
            f"got {effort!r}. Effort is never inherited from ~/.codex/config.toml or "
            "the model default — it must be explicit so runs are comparable."
        )
    return EFFORT_MAP[effort]


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()


def _find_rollout(thread_id: str) -> str | None:
    """Best-effort path of codex's own session transcript for ``thread_id``
    (``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<thread_id>.jsonl``)."""
    if not thread_id:
        return None
    pattern = str(
        _codex_home() / "sessions" / "*" / "*" / "*" / f"rollout-*-{glob.escape(thread_id)}.jsonl"
    )
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else None


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def _stderr_tail(stderr: str) -> str:
    return _strip_ansi(stderr)[-STDERR_TAIL_CHARS:]


# -- event-stream parsing ----------------------------------------------------


def _parse_jsonl(stdout: str) -> tuple[list[dict[str, Any]], list[str], int]:
    """Split ``codex exec --json`` stdout into events.

    Returns ``(events, unparsed_lines[:RAW_LIST_LIMIT], unparsed_total)``.
    Non-JSON lines are never fatal on their own — the CLI is allowed chatter.
    """
    events: list[dict[str, Any]] = []
    unparsed: list[str] = []
    unparsed_total = 0
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            events.append(obj)
        else:
            unparsed_total += 1
            if len(unparsed) < RAW_LIST_LIMIT:
                unparsed.append(line[:500])
    return events, unparsed, unparsed_total


def _first_thread_id(events: list[dict[str, Any]]) -> str | None:
    for ev in events:
        if ev.get("type") == "thread.started":
            tid = ev.get("thread_id")
            if tid:
                return str(tid)
    return None


def _terminal_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for ev in reversed(events):
        if ev.get("type") in _TERMINAL_TYPES:
            return ev
    return None


def _has_model_activity(events: list[dict[str, Any]]) -> bool:
    """True when at least one ``item.*`` event carries a non-error item —
    i.e. the model produced something (a message, a command, a patch...)."""
    for ev in events:
        etype = ev.get("type")
        if isinstance(etype, str) and etype.startswith("item."):
            item = ev.get("item")
            if isinstance(item, dict) and item.get("type") != "error":
                return True
    return False


def _error_messages(events: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for ev in events:
        etype = ev.get("type")
        if etype == "error" and ev.get("message"):
            out.append(str(ev["message"]))
        elif etype == "turn.failed":
            err = ev.get("error")
            if isinstance(err, dict) and err.get("message"):
                out.append(str(err["message"]))
        elif isinstance(etype, str) and etype.startswith("item."):
            item = ev.get("item")
            if isinstance(item, dict) and item.get("type") == "error" and item.get("message"):
                out.append(str(item["message"]))
    return out


@dataclass(frozen=True)
class _Usage:
    """Token counts extracted for one leg, plus where they came from."""

    raw: dict[str, Any] | None
    source: str  # "stream" | "rollout" | "none"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cache_tokens_reported: bool = False


_NO_USAGE = _Usage(raw=None, source="none")


def _usage_from(events: list[dict[str, Any]], rollout_path: str | None) -> _Usage:
    """Map the event stream (and, for failed turns, codex's rollout file) onto
    token counts.

    **Not yet calibrated.** The mapping is written against recorded fixtures
    (``tests/fixtures/codex_exec_ok.jsonl`` and ``codex_exec_resume_ok.jsonl``),
    never against documentation or memory of the format; until those fixtures
    exist this returns "no usage found" (``source="none"``), which is the true
    state of the failure fixtures we do have. A ``turn.completed`` leg that
    lands here raises in :meth:`CodexCliHarness._attempt` rather than
    recording a zero-token success. See ``plans/codex-cli-harness.md`` §4.5/§6.
    """
    del events, rollout_path  # calibrated in Stage 2
    return _NO_USAGE


def _harness_error_text(
    terminal: dict[str, Any] | None, errors: list[str], stderr: str, returncode: int
) -> str:
    if terminal is not None and terminal.get("type") == "turn.failed":
        err = terminal.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    if errors:
        return errors[-1]
    tail = _stderr_tail(stderr).strip()
    if tail:
        return tail
    return f"codex exited {returncode}"


# -- subprocess plumbing -----------------------------------------------------


@dataclass
class _Leg:
    stdout: str
    stderr: str
    returncode: int
    wall_time_seconds: float


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGTERM the whole process group, wait, SIGKILL if still alive — mirrors
    ``runner.workspace._kill_group``. A plain ``subprocess.run(timeout=)`` only
    kills the direct child and then blocks while a sandbox helper or a
    backgrounded server the model started keeps the pipes open."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=KILL_GRACE_SECONDS)
    if proc.poll() is None:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def _close_pipes(proc: subprocess.Popen) -> None:
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None:
            with contextlib.suppress(OSError, ValueError):
                pipe.close()


class CodexCliHarness(Harness):
    name = "codex-cli"

    def __init__(self) -> None:
        # Remembered from start_build so continue_build resumes in the same
        # directory with the same model and effort (one instance per run).
        self._worktree_dir: str | None = None
        self._model: str | None = None
        self._effort: str | None = None
        # The ``Logged in using ...`` line the pre-flight saw; recorded in
        # attempt 1's raw.
        self._auth_mode: str | None = None

    # -- helpers ---------------------------------------------------------

    def _preflight(self) -> str:
        """Require a ChatGPT-subscription login before spending anything.

        Runs under the stripped env so a stray env credential can't make the
        check pass while the actual leg 401s. A missing ``codex`` binary
        raises :class:`FileNotFoundError` unwrapped, as the Claude adapter does.
        """
        try:
            proc = subprocess.run(  # noqa: S603 — args are constructed, not shell
                ["codex", "login", "status"],
                env=_subprocess_env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=PREFLIGHT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise HarnessError(
                f"`codex login status` did not answer within {PREFLIGHT_TIMEOUT_SECONDS}s"
            ) from exc
        combined = _strip_ansi(f"{proc.stdout or ''}\n{proc.stderr or ''}")
        mode_line = next(
            (ln.strip() for ln in combined.splitlines() if "ogged in" in ln),
            combined.strip()[:200],
        )
        if proc.returncode != 0 or SUBSCRIPTION_MODE_LINE not in combined:
            # Keep only the mode, not whatever follows " - " (an API-key login
            # line carries a redacted key fragment there).
            shown = mode_line.split(" - ", 1)[0]
            raise HarnessError(
                "codex is not logged in with a ChatGPT subscription "
                f"(`codex login status` exited {proc.returncode}: {shown!r}). "
                "Run `codex login` (or `codex login --device-auth` on a headless box); "
                "do not log in with an API key — runs must stay on subscription auth."
            )
        return mode_line

    def _run_leg(self, cmd: list[str], cwd: str, stdin_text: str) -> _Leg:
        timeout = _build_timeout()
        started = time.monotonic()
        proc = subprocess.Popen(  # noqa: S603 — args are constructed, not shell
            cmd,
            cwd=cwd,
            env=_subprocess_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(input=stdin_text, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_group(proc)
            try:
                proc.communicate(timeout=DRAIN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            finally:
                _close_pipes(proc)
            raise HarnessError(
                f"codex timed out after {timeout}s: {' '.join(cmd[:3])} ..."
            ) from exc
        except BaseException:
            # KeyboardInterrupt or anything else: the child is in its own
            # process group, so the terminal's SIGINT never reaches it — kill
            # the group ourselves rather than orphaning a live agent.
            _kill_group(proc)
            _close_pipes(proc)
            raise
        wall = time.monotonic() - started
        return _Leg(stdout or "", stderr or "", proc.returncode, wall)

    def _attempt(self, cmd: list[str], cwd: str, stdin_text: str, *, is_start: bool) -> AttemptResult:
        """Run one leg, retrying dead turns, and classify the outcome."""
        retries = _dead_turn_retries()
        total_wall = 0.0
        dead_legs = 0
        dead_leg_errors: list[str] = []
        leg_name = "start" if is_start else "resume"
        for leg_no in range(retries + 1):
            leg = self._run_leg(cmd, cwd, stdin_text)
            total_wall += leg.wall_time_seconds
            events, unparsed, unparsed_total = _parse_jsonl(leg.stdout)
            thread_id = _first_thread_id(events)
            if not thread_id:
                raise HarnessError(
                    f"codex ({leg_name}) produced no thread.started event, exit {leg.returncode}: "
                    f"stderr={_stderr_tail(leg.stderr)[-1000:]!r} stdout={leg.stdout[:500]!r}"
                )
            terminal = _terminal_event(events)
            rollout_path = _find_rollout(thread_id)
            usage = _usage_from(events, rollout_path)
            errors = _error_messages(events)
            completed = terminal is not None and terminal.get("type") == "turn.completed"
            if completed:
                if usage.source == "none":
                    raise HarnessError(
                        f"codex ({leg_name}) reported turn.completed for thread {thread_id} but no "
                        "token usage could be found in the event stream or the session rollout "
                        f"({rollout_path}). A success is never recorded with zero tokens — "
                        "the usage parser is uncalibrated or the CLI format changed "
                        "(plans/codex-cli-harness.md §4.5, §6)."
                    )
                return self._result(
                    leg=leg,
                    events=events,
                    thread_id=thread_id,
                    terminal=terminal,
                    usage=usage,
                    errors=errors,
                    unparsed=unparsed,
                    unparsed_total=unparsed_total,
                    rollout_path=rollout_path,
                    wall=total_wall,
                    dead_legs=dead_legs,
                    dead_leg_errors=dead_leg_errors,
                    is_start=is_start,
                    harness_error=None,
                )
            error_text = _harness_error_text(terminal, errors, leg.stderr, leg.returncode)
            dead = usage.source == "none" and not _has_model_activity(events)
            if dead:
                dead_legs += 1
                dead_leg_errors.extend(errors or [error_text])
                if leg_no < retries:
                    time.sleep(DEAD_TURN_RETRY_PAUSE_SECONDS)
                    continue
                raise HarnessError(
                    f"codex ({leg_name}) turn produced no model activity on {leg_no + 1} "
                    f"attempt(s) (exit {leg.returncode}, thread {thread_id}): {error_text}"
                )
            print(
                f"(codex-cli: {leg_name} turn failed after model activity — captured as an "
                f"attempt failure; usage_source={usage.source}: {error_text[:200]})"
            )
            return self._result(
                leg=leg,
                events=events,
                thread_id=thread_id,
                terminal=terminal,
                usage=usage,
                errors=errors,
                unparsed=unparsed,
                unparsed_total=unparsed_total,
                rollout_path=rollout_path,
                wall=total_wall,
                dead_legs=dead_legs,
                dead_leg_errors=dead_leg_errors,
                is_start=is_start,
                harness_error=error_text,
            )
        raise AssertionError("unreachable")  # pragma: no cover

    def _result(
        self,
        *,
        leg: _Leg,
        events: list[dict[str, Any]],
        thread_id: str,
        terminal: dict[str, Any] | None,
        usage: _Usage,
        errors: list[str],
        unparsed: list[str],
        unparsed_total: int,
        rollout_path: str | None,
        wall: float,
        dead_legs: int,
        dead_leg_errors: list[str],
        is_start: bool,
        harness_error: str | None,
    ) -> AttemptResult:
        raw: dict[str, Any] = {
            "thread_id": thread_id,
            "exit_code": leg.returncode,
            "usage": usage.raw,
            "usage_source": usage.source,
            "final_event": terminal,
            "event_counts": dict(Counter(str(ev.get("type")) for ev in events)),
            "errors": errors[:RAW_LIST_LIMIT],
            "errors_total": len(errors),
            "effort": self._effort,
            "model": self._model,
            "cache_tokens_reported": usage.cache_tokens_reported,
            "dead_turn_retries": dead_legs,
            # Why the retried legs were dead — the abandoned threads are not
            # otherwise referenced anywhere in the record.
            "dead_turn_errors": dead_leg_errors[:RAW_LIST_LIMIT],
            "dead_turn_errors_total": len(dead_leg_errors),
            "rollout_path": rollout_path,
            "unparsed_lines": unparsed,
            "unparsed_total": unparsed_total,
            "stderr_tail": _stderr_tail(leg.stderr),
        }
        if is_start:
            raw["auth_mode"] = self._auth_mode
        if harness_error is not None:
            raw["harness_error"] = harness_error
        return AttemptResult(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            wall_time_seconds=wall,
            session_handle=thread_id,
            cache_creation_tokens=usage.cache_creation_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            reported_cost_usd=None,
            raw=raw,
        )

    # -- Harness interface ----------------------------------------------

    def start_build(
        self,
        worktree_dir: str,
        prompt: str,
        model: str,
        config: dict[str, Any],
    ) -> AttemptResult:
        effort = _map_effort(config)  # before any subprocess
        self._auth_mode = self._preflight()
        self._worktree_dir = worktree_dir
        self._model = model
        self._effort = effort
        cmd = [
            "codex",
            "exec",
            "--ignore-user-config",
            "-m",
            model,
            "--json",
            "-C",
            worktree_dir,
            "--sandbox",
            "workspace-write",
            "-c",
            f'model_reasoning_effort="{effort}"',
            "-c",
            "sandbox_workspace_write.network_access=true",
            "-",
        ]
        return self._attempt(cmd, worktree_dir, prompt, is_start=True)

    def continue_build(
        self,
        session_handle: str,
        followup_prompt: str,
    ) -> AttemptResult:
        if not self._worktree_dir or not self._model or not self._effort:
            raise HarnessError("continue_build called before start_build")
        if not session_handle:
            raise HarnessError(
                "continue_build called with an empty session handle — no codex thread to resume"
            )
        cmd = [
            "codex",
            "exec",
            "resume",
            session_handle,
            "--ignore-user-config",
            "--json",
            "-m",
            self._model,
            "-c",
            f'model_reasoning_effort="{self._effort}"',
            "-c",
            'sandbox_mode="workspace-write"',
            "-c",
            "sandbox_workspace_write.network_access=true",
            "-",
        ]
        return self._attempt(cmd, self._worktree_dir, followup_prompt, is_start=False)
