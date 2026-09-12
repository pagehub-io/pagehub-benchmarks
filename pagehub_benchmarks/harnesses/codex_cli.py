"""Codex CLI harness adapter.

Drives OpenAI's ``codex`` CLI headlessly (``codex exec``), the way
:mod:`pagehub_benchmarks.harnesses.claude_code` drives ``claude -p``::

    start_build:    codex exec --ignore-user-config <ENV_POLICY> -m <model> --json \\
                        -C <worktree> --sandbox workspace-write \\
                        -c 'model_reasoning_effort="<effort>"' \\
                        -c 'sandbox_workspace_write.network_access=true' -
    continue_build: codex exec resume <thread_id> --ignore-user-config <ENV_POLICY> \\
                        --json -m <model> -c 'model_reasoning_effort="<effort>"' \\
                        -c 'sandbox_mode="workspace-write"' \\
                        -c 'sandbox_workspace_write.network_access=true' -

    <ENV_POLICY> = --disable shell_snapshot \\
                   -c 'shell_environment_policy.experimental_use_profile=false' \\
                   -c 'shell_environment_policy.exclude=["*KEY*","*SECRET*","*TOKEN*","*PASSWORD*"]'

Every codex subprocess runs with ``HOME`` = a per-run directory under
``~/.cache/pagehub-benchmarks/codex-homes`` (read-only to the sandboxed agent)
and ``CODEX_HOME`` pinned to the operator's real codex home.

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

**Agent shell environment.** Three layers, all verified 2026-09-11:
``--disable shell_snapshot`` (no plaintext login-shell snapshot under
``$CODEX_HOME/shell_snapshots``), a ``shell_environment_policy`` exclude list
(``*KEY*``, ``*SECRET*``, ``*TOKEN*``, ``*PASSWORD*``) for variables inherited
from the runner, and — the layer that actually closes the hole — a throwaway
``HOME`` for the codex subprocess with ``CODEX_HOME`` pinned to the real
login directory: codex runs the agent's commands with ``bash -lc``, and with
the operator's HOME that re-sourced ``~/.bashrc`` and its secrets into the
agent's shell; with the throwaway HOME the probe reported none. That HOME
holds only a ``.bash_profile`` restoring the runner's ``PATH`` and locale
(see ``_prepare_home``), and lives outside the sandbox's writable roots. The
restoration assumes the codex agent's shell is bash (it is ``/bin/bash -lc``
on the development box); ``BASH_ENV``/``ENV``/``ZDOTDIR`` are stripped so no
other profile is sourced.

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
- a *dead* turn — no non-error ``item.*`` event and no usage from any source
  (no stream delta, and no rollout cumulative beyond a baseline with no
  recorded gap: the third conjunct is review round 9's fix) — is retried
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
mapped onto token counts, calibrated against recorded fixtures
(``tests/fixtures/codex_exec_ok.jsonl``, ``codex_exec_resume_ok.jsonl``,
``codex_rollout_ok.jsonl``), not against docs. ``turn.completed.usage`` is the
**thread total** (verified: the resume fixture reports start + resume), so
each *returning* leg records the delta against the previous total (a retried
dead leg records none, so that delta covers every leg of the attempt); ``input_tokens``
includes the cached and cache-write slices (partitioned out for pricing);
``output_tokens`` includes reasoning. **A leg's own figure comes from that
stream delta and from nothing else** (review round 9): a failed turn carries
no usage on the stream, and the leg then records zeros marked
``usage_missing`` rather than borrowing a figure from codex's rollout, which
cannot say which turn a record belongs to. The rollout has three jobs and no
others: cumulative baseline repair, dead-leg evidence, rate limits — codex's
post-turn ``thread_token_usage``, a CUMULATIVE that repairs the baseline the
next delta is taken against; evidence that the ATTEMPT did work after all when
a leg's stream reported nothing (otherwise that leg is dead and retried); and
``rate_limits``
(5-hour and weekly ``used_percent``), the only place codex reports
subscription budget. The honest invariant is a *marking*
one: ``raw["usage_faithful"]`` is false on any attempt whose ``usage_delta``
is not a measure of that attempt alone — short (nothing readable: zeros, never
"free"), not provably its own (it may span an earlier unmeasured leg), or
short by an abandoned thread (a dead leg was retried onto a new one, so what
it spent is billed to no attempt: review round 11) — and
``raw["usage_caveats"]`` says which.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
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
# How long a throwaway codex HOME (see _throwaway_home_base) is kept after its
# run. Harness has no teardown hook and a run can be Ctrl-C'd out of any
# try/finally, so the directories are reaped on the NEXT start_build instead —
# late enough that a just-finished run's HOME is still there to inspect.
THROWAWAY_HOME_TTL_SECONDS = 7 * 24 * 3600
STDERR_TAIL_CHARS = 2000
SUBSCRIPTION_MODE_LINE = "Logged in using ChatGPT"
STRIPPED_ENV_VARS = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")
# Variables that make a shell source an operator file regardless of HOME:
# bash sources $BASH_ENV for every non-interactive shell (pyenv's shims are
# bash scripts) — the one that matters here; interactive sh/dash source $ENV;
# zsh reads its dotfiles from $ZDOTDIR.
# Any of them would undo the throwaway HOME (review finding, executed with a
# synthetic BASH_ENV file; all three are unset on the development box).
PROFILE_ENV_VARS = ("BASH_ENV", "ENV", "ZDOTDIR")
# config.effort -> codex model_reasoning_effort. Explicit, not a pass-through:
# codex accepts any string silently. ``ultra`` (automatic sub-agent delegation)
# is intentionally absent.
EFFORT_MAP: dict[str, str] = {e: e for e in ("low", "medium", "high", "xhigh", "max")}
# Env-name patterns codex must not expose to the agent's shell. Verified
# 2026-09-11 (gpt-5.6-sol probes): together with ``--disable shell_snapshot``
# and ``experimental_use_profile=false`` this hides every matching variable
# inherited from the runner process and stops codex writing a plaintext
# snapshot of the login-shell environment to ``$CODEX_HOME/shell_snapshots``.
# On its own it is partial — ``bash -lc`` would re-source the operator's
# ``~/.bashrc`` — which is why every codex subprocess also gets a throwaway
# HOME (``_throwaway_home_base`` / ``_prepare_home``); with both, a probe
# agent saw no secret-named variables.
SHELL_ENV_EXCLUDE = ("*KEY*", "*SECRET*", "*TOKEN*", "*PASSWORD*")
_SHELL_ENV_EXCLUDE_TOML = "shell_environment_policy.exclude=[" + ",".join(f'"{p}"' for p in SHELL_ENV_EXCLUDE) + "]"
_ENV_POLICY_ARGS = [
    "--disable",
    "shell_snapshot",
    "-c",
    "shell_environment_policy.experimental_use_profile=false",
    "-c",
    _SHELL_ENV_EXCLUDE_TOML,
]

# CSI sequences (colours, cursor) and OSC sequences (hyperlinks, titles).
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_TERMINAL_TYPES = ("turn.completed", "turn.failed")

__all__ = ["CodexCliHarness", "HarnessError", "EFFORT_MAP", "STRIPPED_ENV_VARS"]


# -- environment / config helpers -------------------------------------------


def _subprocess_env(home_override: str | None = None) -> dict[str, str]:
    """A copy of the current env minus every codex auth env var, with codex's
    home pinned and — when ``home_override`` is given — ``HOME`` swapped for a
    throwaway directory.

    ``CODEX_API_KEY`` is a live runtime credential in codex 0.154 (verified: a
    bogus value changes the 401 to "Incorrect API key provided"), so leaving
    it in place would divert a run onto metered API billing. ``CODEX_ACCESS_TOKEN``
    is read as an auth source too. ``OPENAI_API_KEY`` is not read at runtime
    by 0.154 but is stripped as well (task requirement; future-proof).

    ``CODEX_HOME`` is pinned to the operator's real codex home (the env value,
    else ``~/.codex`` of the *runner's* HOME) so the stored ChatGPT login,
    sessions and trust entries stay where they are. ``HOME`` is then pointed
    at a per-run throwaway directory (see ``_throwaway_home_base`` /
    ``_prepare_home``), and ``PROFILE_ENV_VARS`` are removed: codex runs the
    agent's commands with
    ``bash -lc``, which sources the profile of whatever ``HOME`` is — with the
    operator's real HOME that re-exported every secret in ``~/.bashrc`` into
    the agent's shell (37 secret-named variables on this runner box); with an
    empty HOME the same probe reported NONE, and login / pip / network kept
    working. Verified 2026-09-11 with gpt-5.6-sol.
    """
    env = dict(os.environ)
    for key in (*STRIPPED_ENV_VARS, *PROFILE_ENV_VARS):
        env.pop(key, None)
    env["CODEX_HOME"] = str(_codex_home())
    if home_override:
        env["HOME"] = home_override
    return env


_WARNED: set[str] = set()


def _warn_once(message: str) -> None:
    """Print ``message`` the first time it is seen in this process — for
    notices about a setting that is re-read on every leg."""
    if message not in _WARNED:
        _WARNED.add(message)
        print(message)


def _int_env(name: str, default: int) -> int:
    """``name`` as an int, else ``default`` — saying so on the console when the
    value was present but unparsable, so a typo is not silently ignored."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"(codex-cli: ignoring {name}={raw!r} — not an integer; using {default})")
        return default


def _build_timeout() -> int:
    """``CODEX_BUILD_TIMEOUT_SECONDS``, or the default.

    ``0`` is a common "no limit" idiom, and neither reading can be honoured:
    clamping it to one second would kill every leg instantly (burning the
    thread), and treating it as no limit would let a wedged run hang forever.
    So it falls back to the default and says so, as an unparsable value does
    (review round 8, N-2).
    """
    seconds = _int_env("CODEX_BUILD_TIMEOUT_SECONDS", DEFAULT_BUILD_TIMEOUT_SECONDS)
    if seconds <= 0:
        # Called once per leg; say it once per value, not once per attempt.
        _warn_once(
            f"(codex-cli: ignoring CODEX_BUILD_TIMEOUT_SECONDS={seconds} — not a timeout, and "
            f"not 'no limit' either; using {DEFAULT_BUILD_TIMEOUT_SECONDS})"
        )
        return DEFAULT_BUILD_TIMEOUT_SECONDS
    return seconds


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
    """The operator's codex home, absolute (a relative ``CODEX_HOME`` would
    otherwise be re-resolved against the worktree by the codex subprocess)."""
    return Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser().absolute()


def _throwaway_home_base() -> Path:
    """Where per-run throwaway HOMEs are created:
    ``${XDG_CACHE_HOME:-~/.cache}/pagehub-benchmarks/codex-homes``.

    It must lie OUTSIDE the sandbox's writable roots (the worktree, ``/tmp``,
    ``$TMPDIR``): a HOME the agent can write lets it plant
    ``$HOME/.agents/skills/*`` or edit ``.bash_profile`` for its later
    resumed turns (review finding, 2026-09-11 — reproduced with ``codex
    sandbox`` for a ``mkdtemp()`` under ``/tmp``; a directory under
    ``~/.cache`` was verified read-only to the sandboxed agent). Refuses a
    base under ``/tmp`` or ``$TMPDIR`` (compared after resolving symlinks and
    ``..``) rather than silently using a writable location. The worktree is
    not checked: the runner creates worktrees under ``.worktrees/`` (or
    ``--worktrees-dir``), and pointing that at the cache directory is not a
    supported configuration.
    """
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    # The XDG spec says a relative XDG_CACHE_HOME is invalid and must be ignored.
    cache = Path(xdg).expanduser() if xdg and Path(xdg).expanduser().is_absolute() else Path.home() / ".cache"
    # resolve(): compare real locations — a symlink or ``..`` must not route the
    # HOME back into a writable root (review finding, executed).
    base = (cache / "pagehub-benchmarks" / "codex-homes").resolve()
    writable_roots = [Path("/tmp")]
    if os.environ.get("TMPDIR"):
        writable_roots.append(Path(os.environ["TMPDIR"]))
    for root in writable_roots:
        root_real = root.expanduser().resolve()
        if base == root_real or base.is_relative_to(root_real):
            raise HarnessError(
                f"throwaway codex HOME base {base} is under {root_real}, which the codex sandbox "
                "makes writable to the agent; set XDG_CACHE_HOME to a directory outside /tmp "
                "and $TMPDIR"
            )
    return base


def _reap_throwaway_homes(base: Path) -> int:
    """Delete ``run-*`` HOMEs under ``base`` older than
    ``THROWAWAY_HOME_TTL_SECONDS``; return how many went.

    Called from ``start_build`` BEFORE this run's own HOME is created, so the
    live one is never a candidate. Best-effort: every OS error is swallowed —
    a run must never fail because an old cache directory could not be removed
    (a read-only or root-owned leftover, an NFS mount gone away).
    """
    removed = 0
    cutoff = time.time() - THROWAWAY_HOME_TTL_SECONDS
    try:
        candidates = sorted(base.glob("run-*"))
    except OSError:
        return 0
    for path in candidates:
        try:
            if not path.is_dir() or path.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        # No ignore_errors: with it on, rmtree could never raise and this
        # suppress was dead code. The suppress IS the best-effort guard (a
        # root-owned leftover, a read-only parent, an NFS mount gone away).
        # exists() is INSIDE it: on the pinned Python it re-raises anything
        # outside ENOENT/ENOTDIR/EBADF/ELOOP (verified on 3.11 — EACCES
        # raises; only 3.12+ swallows every OSError), and this runs before any
        # codex process, so an error here would fail the whole run.
        with contextlib.suppress(OSError):
            shutil.rmtree(path)
            if not path.exists():
                removed += 1
    return removed


def _prepare_home(home: str) -> None:
    """Seed the throwaway HOME with a login profile that restores only the
    runner's ``PATH`` and locale.

    ``PATH``: codex runs commands with ``/bin/bash -lc``; a stock Debian/Ubuntu
    ``/etc/profile`` resets ``PATH`` for login shells (this WSL box skips the
    reset only because ``WSL_DISTRO_NAME`` is set), and with an empty HOME
    nothing would restore pyenv / linuxbrew / ``~/.local/bin`` — the agent
    would lose the runner's toolchain (review finding, reproduced with
    ``WSL_DISTRO_NAME`` unset). The profile puts the runner's ``PATH`` first
    and then appends whatever PATH the login shell has at that point — so the
    helper directory codex itself prepends (``~/.codex/tmp/arg0/…``:
    ``apply_patch``, ``codex-linux-sandbox``) survives wherever
    ``/etc/profile`` does not reset PATH (review finding: a plain
    ``export PATH=<runner>`` dropped it).

    Locale:
    ``codex exec`` forces ``LANG``/``LC_ALL``/``LC_CTYPE=C.UTF-8`` for the
    agent's commands and ignores ``shell_environment_policy.set`` for them
    (verified 2026-09-11). On boxes where the ``bash`` that PATH resolves
    cannot load ``C.UTF-8`` (linuxbrew bash 5.3 here), every bash-script shim
    — pyenv's ``python3``/``pip``/``pytest`` — then printed ~20 ``setlocale``
    warnings into the agent's command output: noise, tokens and a confound
    the Claude agent never sees. Codex runs commands with ``/bin/bash -lc``
    by default, which reads ``~/.bash_profile`` *after* codex's env
    injection, so exporting the runner's own locale there (``LC_ALL`` or else
    ``LANG`` — what the Claude agent inherits) removes it: 39 warnings → 0 for
    one pip + one pytest call.

    Non-login commands: the model may ask codex's ``exec_command`` tool for
    ``login: false``; codex then runs ``/bin/bash -c``, which reads no
    profile at all. That shell keeps codex's own PATH (its helper dirs, then
    the runner's PATH — toolchain intact) and codex's ``C.UTF-8``; only
    bash-script tools resolved through a bash that can't load it would warn
    (none did on the development box, where the runner's ``pyenv exec`` puts
    real ``python3``/``pip`` binaries first). Verified 2026-09-11.

    Values are shell-quoted; nothing else goes in the file, and with neither
    ``PATH`` nor a locale set no file is written.
    """
    lines: list[str] = []
    path = os.environ.get("PATH")
    if path:
        lines.append(f'export PATH={shlex.quote(path)}:"$PATH"')
    locale = os.environ.get("LC_ALL") or os.environ.get("LANG")
    if locale:
        quoted = shlex.quote(locale)
        lines.append(f"export LANG={quoted} LC_ALL={quoted} LC_CTYPE={quoted}")
    if lines:
        Path(home, ".bash_profile").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _find_rollout(thread_id: str) -> str | None:
    """Best-effort path of codex's own session transcript for ``thread_id``
    (``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<thread_id>.jsonl``)."""
    if not thread_id:
        return None
    pattern = os.path.join(
        glob.escape(str(_codex_home())),
        "sessions", "*", "*", "*", f"rollout-*-{glob.escape(thread_id)}.jsonl",
    )
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else None


def _publishable_rollout_path(rollout_path: str | None) -> str | None:
    """The rollout's location relative to ``$CODEX_HOME`` (else its basename).

    ``raw`` is published to the results site, and the absolute path carries the
    operator's home directory — the same reason ``_trim_rate_limits`` drops
    fields. Relative to CODEX_HOME the path is still enough for the operator to
    find the transcript.
    """
    if not rollout_path:
        return None
    path = Path(rollout_path)
    with contextlib.suppress(ValueError):
        return str(path.relative_to(_codex_home()))
    return path.name


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


_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)


# Why an attempt's recorded figure is not a faithful measure of that attempt.
# Recorded verbatim in raw["usage_caveats"]; raw["usage_faithful"] is their
# absence. Three values, and the code can produce all three — a vocabulary is
# only useful if every entry is reachable, so "rollout_turn_rejected" went
# with the rollout recovery it described (review round 9).
#
# The first two are properties of one leg and are set in _usage_from. The
# third is a property of the ATTEMPT and can only be set in
# CodexCliHarness._result, which is the first place that knows a retry
# happened: a dead START leg spends on a thread the harness then walks away
# from, so that spend is billed to no attempt anywhere in the record and the
# figure published here is short by a whole turn (review round 11).
USAGE_CAVEAT_MISSING = "missing"
USAGE_CAVEAT_ABSORBED = "absorbed_missing_leg"
USAGE_CAVEAT_DEAD_LEG = "dead_leg_unmeasured"
# The whole vocabulary, so a consumer can prove it has classified every value
# rather than the ones it happened to think of. tools/build_site.py uses it to
# decide which caveats make a RUN's totals a lower bound (review round 12):
# MISSING and DEAD_LEG lose spend out of the record entirely, so every
# aggregate derived from it is short; ABSORBED only moves spend between
# attempts of the same run, so the run total is unaffected.
USAGE_CAVEATS = (USAGE_CAVEAT_MISSING, USAGE_CAVEAT_ABSORBED, USAGE_CAVEAT_DEAD_LEG)


@dataclass(frozen=True)
class _Usage:
    """Token counts extracted for one leg, plus where they came from.

    ``raw`` is the thread-cumulative usage object exactly as codex reported
    it on the stream's ``turn.completed``, or ``None`` when the stream
    reported none. There is no other source: round 9 deleted the rollout
    branch, so the only two values this field can take are the stream's
    verbatim object and ``None``. ``delta`` is the ATTEMPT's share after
    partitioning — the numbers that go into the :class:`AttemptResult`. Not
    one leg's: a dead RESUME leg records nothing of its own, so the returning
    leg's delta covers it too. ``thread_total`` is
    the cumulative thread usage after this leg, remembered on the harness so
    the next leg's delta can be taken.

    ``caveats`` is the honesty record for what this LEG can see: empty when
    ``delta`` measures THIS ATTEMPT and nothing else, otherwise the reasons it
    does not. The attempt, not the leg, is the granularity that makes the
    empty case true — measured 2026-09-12: a dead resume leg that spent
    4242/77 turned the returning leg's delta from 3959/5 into 8201/82, with no
    caveat and correctly so, because both legs are the one attempt this figure
    is published for — short (:data:`USAGE_CAVEAT_MISSING`) or not provably its own
    (:data:`USAGE_CAVEAT_ABSORBED`). The published
    ``raw["usage_caveats"]`` is this plus :data:`USAGE_CAVEAT_DEAD_LEG`, which
    only :meth:`CodexCliHarness._result` can add because only it knows a
    retry abandoned a thread (review round 11).
    """

    raw: dict[str, Any] | None
    source: str  # "stream" (the delta) | "none" (nothing measured this leg)
    # Always a dict: a published field must not change type between legs.
    delta: dict[str, int]
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cache_tokens_reported: bool = False
    reasoning_output_tokens: int = 0
    thread_total: dict[str, int] | None = None
    rate_limits: dict[str, Any] | None = None
    # This leg spent tokens that could not be read from any source: its zeros
    # are "unknown", not "free". Recorded as raw["usage_missing"].
    usage_missing: bool = False
    # ``thread_total`` is not known to be codex's cumulative after THIS leg
    # (this leg was never measured, whatever the baseline was repaired to), so
    # the next leg cannot prove a stream delta taken against it is its own.
    baseline_gap: bool = False
    # Why ``delta`` is not a faithful measure of this leg (empty when it is).
    caveats: tuple[str, ...] = ()
    # Codex's rollout records the thread past everything this harness has
    # billed, with no RECORDED gap in the baseline: THIS ATTEMPT did work even
    # though this leg's stream reported none. Not "this leg did work" — a
    # dead-classified leg bypasses ``_result``, so it leaves no gap behind and
    # its own tokens can be what moved the cumulative (review round 10). The
    # decision is still right because every leg that can move the cumulative
    # under a gapless baseline belongs to the attempt being classified, and
    # the attempt is the granularity this decision is taken at. Only
    # meaningful on the ``source == "none"`` path, where it separates an
    # attempt to capture from a dead leg to retry.
    active_per_rollout: bool = False


def _as_usage(obj: Any) -> dict[str, int] | None:
    """Normalise a codex usage object to the five known integer fields, or
    ``None`` if it isn't one (missing/invalid ``input_tokens``/``output_tokens``).

    That presence check is the calibration tripwire: a renamed or dropped
    ``input_tokens``/``output_tokens`` turns into the never-a-zero-token-
    success :class:`HarnessError` rather than a silent zero. It is incomplete
    on purpose but worth naming (review round 10): ``cached_input_tokens`` is
    covered by ``cache_tokens_reported``, while a renamed or dropped
    ``cache_write_input_tokens`` normalises to 0 and prices the run as plain
    non-cached input, OVERSTATING cost with nothing flagged. Every recording
    so far has it at 0, so no fixture would notice — a future recording with
    non-zero writes must be re-calibrated here.
    """
    if not isinstance(obj, dict) or "input_tokens" not in obj or "output_tokens" not in obj:
        return None
    out: dict[str, int] = {}
    for key in _USAGE_KEYS:
        try:
            out[key] = int(obj.get(key) or 0)
        except (TypeError, ValueError):
            return None
    return out


def _stream_usage(events: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, int] | None]:
    """``(verbatim usage object, normalised)`` from the terminal ``turn.completed``.

    Recorded shape (``tests/fixtures/codex_exec_ok.jsonl``, codex-cli 0.154.0)::

        {"type":"turn.completed","usage":{"input_tokens":15359,"cached_input_tokens":12160,
         "cache_write_input_tokens":0,"output_tokens":5,"reasoning_output_tokens":0}}

    On a **resumed** thread this object is the thread-cumulative total, not the
    turn's (``codex_exec_resume_ok.jsonl``: 31478 = 15359 + 16119 input,
    matching the rollout's ``thread_token_usage``). Hence the delta logic in
    :func:`_usage_from`.
    """
    terminal = _terminal_event(events)
    if terminal is None or terminal.get("type") != "turn.completed":
        return None, None
    verbatim = terminal.get("usage")
    return (verbatim if isinstance(verbatim, dict) else None), _as_usage(verbatim)


def _read_rollout(
    rollout_path: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``(codex's post-turn thread_token_usage for the last turn in the file,
    the last rate_limits)`` from codex's rollout.

    Recorded shape (``tests/fixtures/codex_rollout_ok.jsonl``): top-level
    ``{"type":"token_usage_record","payload":{"turn_id":…,"usage":…,
    "turn_token_usage":…,"thread_token_usage":…}}`` after each model response,
    and ``{"type":"event_msg","payload":{"type":"token_count","info":{…},
    "rate_limits":{"primary":{"used_percent":…,"window_minutes":300,…},
    "secondary":{…"window_minutes":10080…},"plan_type":…}}}``.

    **Not a per-turn usage source** (review round 9). The rollout has three
    jobs and no others: cumulative baseline repair, dead-leg evidence, rate
    limits — how far along the thread's CUMULATIVE usage is (which repairs the
    harness's baseline, and can never say which turn ran), whether the ATTEMPT
    did work after all when a leg's stream reported nothing, and the
    ``rate_limits`` this
    function also returns. Which turn a record belongs to it cannot answer:
    both attempts to infer that (the last turn id read, then this cumulative
    having advanced) prove staleness only, and were read backwards as proof of
    freshness. See :func:`_advances`.

    "The last turn in the file" = the lines after the last ``turn_context``
    (the leg that just ran is the last turn appended). A leg that died before
    codex wrote its ``turn_context`` leaves the file ending on the PREVIOUS
    turn's record, and that turn's cumulative must not surface as evidence that
    THIS ATTEMPT did work (round 8, N-15) — so the reset is what keeps a dead leg
    dead. Best-effort: any read/parse problem ⇒ ``(None, None)``. The on-disk
    format is codex-internal and may change.
    """
    if not rollout_path:
        return None, None
    try:
        lines = Path(rollout_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None, None
    thread_usage: dict[str, Any] | None = None
    rate_limits: dict[str, Any] | None = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        payload = obj.get("payload")
        if obj.get("type") == "turn_context":
            # A new turn starts; only its own records count from here.
            thread_usage = None
        elif obj.get("type") == "token_usage_record" and isinstance(payload, dict):
            if isinstance(payload.get("turn_token_usage"), dict):
                # Each record of a turn carries the running cumulative; the
                # last one is the turn's. Paired with a turn figure so a
                # record shaped some other way is ignored rather than guessed
                # at.
                candidate = payload.get("thread_token_usage")
                thread_usage = candidate if isinstance(candidate, dict) else None
        elif (
            obj.get("type") == "event_msg"
            and isinstance(payload, dict)
            and payload.get("type") == "token_count"
            and isinstance(payload.get("rate_limits"), dict)
        ):
            rate_limits = _trim_rate_limits(payload["rate_limits"])
    return thread_usage, rate_limits


def _trim_rate_limits(rate_limits: dict[str, Any]) -> dict[str, Any]:
    """Keep the subscription-budget fields and nothing else (the raw object
    also carries credit balances and account flags; the record is published)."""
    out: dict[str, Any] = {"plan_type": rate_limits.get("plan_type")}
    for window in ("primary", "secondary"):
        w = rate_limits.get(window)
        if isinstance(w, dict):
            out[window] = {
                k: w.get(k) for k in ("used_percent", "window_minutes", "resets_at") if k in w
            }
    return out


def _partition(usage: dict[str, int]) -> tuple[int, int, int, int]:
    """``(non-cached input, output, cache writes, cache reads)``.

    ``input_tokens`` is the whole prompt; ``cached_input_tokens`` and
    ``cache_write_input_tokens`` are the slices of it billed at the cache
    rates. For cached reads the recordings confirm it (the rollout's
    ``total_tokens == input_tokens + output_tokens``, with cached > 0). For
    cache writes no recording has yet shown a non-zero value; the reading
    rests on OpenAI's pricing page ("Input tokens are either Input, Cached
    Input, or Cache Write and writes are not an additive fee"). ``output_tokens``
    already includes ``reasoning_output_tokens``: on every real rollout usage
    object with reasoning > 0 on the development box (gpt-5.6-sol,
    2026-09-11; dozens across ``usage`` / ``turn_token_usage`` /
    ``thread_token_usage``) ``total_tokens == input_tokens + output_tokens``
    held, so reasoning is recorded but not added.
    """
    cached = usage["cached_input_tokens"]
    cache_write = usage["cache_write_input_tokens"]
    non_cached = usage["input_tokens"] - cached - cache_write
    if non_cached < 0:
        raise HarnessError(
            f"codex usage is inconsistent: cached ({cached}) + cache-write ({cache_write}) "
            f"exceed input_tokens ({usage['input_tokens']}) — the subset assumption "
            "in _partition no longer holds; refusing to record a wrong cost"
        )
    return non_cached, usage["output_tokens"], cache_write, cached


def _advances(thread_total: dict[str, int] | None, baseline: dict[str, int]) -> bool:
    """``thread_total`` — a rollout record's post-turn cumulative — is beyond
    ``baseline``: no component smaller, at least one larger.

    **Sound in one direction only, and which one is the whole point** (review
    round 9). *False* means the record's turn is already inside what the
    harness has recorded: codex's cumulative is monotone and the harness's
    baseline is never ahead of it (a repaired baseline is short, never long),
    so "has not moved past what we recorded" does mean "already covered".
    *True* means only "some turn ran beyond the baseline" — never "this leg's
    turn". When the baseline is short, which is exactly the state an
    unmeasurable leg leaves behind, an earlier never-billed turn is beyond it
    too. The predecessor of this code read True as freshness and adopted such
    records as a leg's own share; three executed consequences later
    (a dead leg captured with turn 1's tokens, a failed leg billed an earlier
    turn, a winning attempt recording a turn that was not its own — all marked
    faithful) that inference is gone.

    So True is used for two of the rollout's three jobs and nothing else
    (the rate-limit job never consults this function): to move the baseline forward
    (a cumulative names no turn, so adopting one cannot mis-attribute — at
    worst it is short), and, against a baseline with no RECORDED gap, as
    evidence that the ATTEMPT being classified did work even though this leg's
    stream said nothing. Never to attribute tokens.

    Read that second use precisely (review round 10). ``not baseline_has_gap``
    means "no *recorded* gap", not "whole": a dead-classified leg never
    reaches :meth:`CodexCliHarness._result`, so a leg that spent tokens and
    was then retried records no gap, and the record sitting beyond the
    baseline can be that leg's rather than this one's. Capturing is still the
    right call, because every leg that can move the cumulative under a gapless
    baseline belongs to the SAME ATTEMPT — and the attempt, not the leg, is
    the granularity every decision here is taken at.
    """
    if thread_total is None:
        return False
    return all(thread_total[k] >= baseline.get(k, 0) for k in _USAGE_KEYS) and any(
        thread_total[k] > baseline.get(k, 0) for k in _USAGE_KEYS
    )


def _usage_from(
    events: list[dict[str, Any]],
    rollout_path: str | None,
    *,
    previous_thread_total: dict[str, int] | None,
    baseline_has_gap: bool = False,
) -> _Usage:
    """Map one leg's event stream onto token counts, with codex's rollout for
    the thread's cumulative and its rate limits — never for a leg's own share.
    Calibrated against the recorded fixtures named in :func:`_stream_usage` /
    :func:`_read_rollout`, not against docs.

    **A leg's own figure comes from the stream delta and from nothing else**
    (review round 9). ``turn.completed.usage`` is the thread total, so this
    leg = total − ``previous_thread_total`` (zero for the first leg of a
    thread). A ``turn.failed`` carries no usage, and nothing may stand in for
    it: the leg records zeros with ``usage_missing`` and
    :data:`USAGE_CAVEAT_MISSING`. (A *completed* turn landing there raises in
    :meth:`CodexCliHarness._attempt` — a success is never recorded as free.)

    The rollout has three jobs and no others: cumulative baseline repair,
    dead-leg evidence, rate limits. Its ``thread_token_usage`` is codex's
    own post-turn CUMULATIVE: once it has moved past the baseline it becomes
    the new baseline, which is the only thing stopping every later delta from
    inheriting an unmeasured turn (review round 7, I-1). A cumulative names no
    turn, so adopting one cannot mis-attribute tokens — at worst it is short,
    and :func:`_advances` keeps it from moving backwards. Adopting it is also
    what makes the backwards-usage guard in this function reachable at all
    (review round 10): the repaired baseline is codex's ROLLOUT accounting
    while the next leg's delta is taken from its STREAM accounting, so a
    divergence between the two shows up as a negative delta and aborts the run
    rather than recording a wrong figure. The two agree on every recorded
    fixture, so this needs codex-side drift — and failing loudly is the right
    direction. And when a leg's stream shows neither usage nor model activity,
    that same cumulative beyond a baseline with no RECORDED gap is evidence
    the ATTEMPT worked anyway (``active_per_rollout``, whose soundness
    :func:`_advances` spells out); against a short baseline it is no evidence
    at all, so the leg is dead and gets retried rather than captured.

    ``baseline_has_gap`` says the baseline is not known to equal codex's
    cumulative, because an earlier leg spent tokens nobody could measure. A
    stream delta taken against it may span that leg as well as this one and
    nothing can prove otherwise, so the leg is marked
    :data:`USAGE_CAVEAT_ABSORBED`. The harness used to try to recover this
    leg's own share from the rollout's turn record instead. It could not:
    neither the last turn id read nor the cumulative watermark proves a record
    BELONGS to this leg, and against the very short baseline that makes
    recovery necessary, an earlier never-billed turn passes both tests. That
    inference is deleted, not refined.

    **The invariant this function exists to keep** (review round 8, I-1;
    unchanged by round 9's deletion): ``delta`` either measures THIS ATTEMPT
    and nothing else, or ``caveats`` — the ones set here plus the one
    :meth:`CodexCliHarness._result` adds — says why not — short
    (:data:`USAGE_CAVEAT_MISSING`: zeros meaning "unknown", never "free") or
    not provably its own (:data:`USAGE_CAVEAT_ABSORBED`). Over-marking is the
    honest direction: a delta taken against a repaired baseline is often
    exactly right, and is still marked, because the harness cannot show it.
    The one way an attempt's figure can be unfaithful that this function
    cannot see is a leg abandoned by a retry — no leg here ever observes it,
    so :data:`USAGE_CAVEAT_DEAD_LEG` is added in
    :meth:`CodexCliHarness._result` instead (review round 11).
    """
    prev = previous_thread_total or {k: 0 for k in _USAGE_KEYS}
    verbatim, total = _stream_usage(events)
    rollout_thread, rate_limits = _read_rollout(rollout_path)
    # The one thing the rollout is allowed to say: codex's cumulative has
    # moved past everything this harness has recorded. Which turn moved it is
    # a question it is no longer asked.
    watermark = _as_usage(rollout_thread)
    beyond_baseline = _advances(watermark, prev)
    if total is not None:
        delta = {k: total[k] - prev.get(k, 0) for k in _USAGE_KEYS}
        if any(v < 0 for v in delta.values()):
            raise HarnessError(
                f"codex thread usage went backwards (previous total {prev}, now {total}) — "
                "the cumulative reading in _usage_from no longer holds; refusing to record"
            )
        non_cached, out, cache_write, cached = _partition(delta)
        return _Usage(
            raw=verbatim,
            source="stream",
            input_tokens=non_cached,
            output_tokens=out,
            cache_creation_tokens=cache_write,
            cache_read_tokens=cached,
            # The object the recorded counts were read from — which is what
            # cache_tokens_reported annotates (round 8, N-6).
            cache_tokens_reported="cached_input_tokens" in (verbatim or {}),
            reasoning_output_tokens=delta["reasoning_output_tokens"],
            delta=delta,
            # Codex's own figure: the baseline it leaves behind is whole, so
            # the marking stops with this leg.
            thread_total=total,
            rate_limits=rate_limits,
            caveats=(USAGE_CAVEAT_ABSORBED,) if baseline_has_gap else (),
        )
    return _Usage(
        raw=None,
        source="none",
        # Zeros, not None: a published field must not change type, and this
        # leg's share really is unknown — usage_missing is what says so.
        delta={k: 0 for k in _USAGE_KEYS},
        # Codex's cumulative when it has moved on, else what we had. Never
        # backwards, and never reconstructed out of a turn record.
        thread_total=watermark if beyond_baseline else previous_thread_total,
        rate_limits=rate_limits,
        usage_missing=True,
        # Whatever the baseline was repaired to, it is not known to be codex's
        # cumulative after THIS leg: this turn was never measured.
        baseline_gap=True,
        caveats=(USAGE_CAVEAT_MISSING,),
        active_per_rollout=beyond_baseline and not baseline_has_gap,
    )


def _refuse_operator_instructions() -> None:
    """Refuse to run while operator-level instructions would be injected.

    ``--ignore-user-config`` covers ``config.toml`` only: a global
    ``$CODEX_HOME/AGENTS.md`` and user skills under ``$CODEX_HOME/skills/``
    were verified (2026-09-11, marker files) to still reach the model. Either
    would silently change what every run measures, so the harness refuses
    until they are moved aside. Codex's own bundled skills live under
    ``skills/.system`` and are part of the product, not operator input.
    """
    home = _codex_home()
    # AGENTS.md verified (marker obeyed); AGENTS.override.md takes precedence
    # over it in codex's global scope and instructions.md is the legacy global
    # file — both are named in the 0.154 binary.
    for name in ("AGENTS.override.md", "AGENTS.md", "instructions.md"):
        path = home / name
        if path.is_file():
            raise HarnessError(
                f"{path} exists and would be injected into every codex turn "
                "(--ignore-user-config does not suppress it); move it aside before benchmarking"
            )
    skills = home / "skills"
    if skills.is_dir():
        extra = sorted(p.name for p in skills.iterdir() if p.name != ".system")
        if extra:
            raise HarnessError(
                f"user skills {extra} under {skills} would be offered to the model in every "
                "codex turn (--ignore-user-config does not suppress them); move them aside "
                "before benchmarking"
            )


def _print_rate_limits(leg_name: str, rate_limits: dict[str, Any]) -> None:
    def _pct(window: Any) -> str:
        # Best-effort display only: the rollout is an unstable format, and a
        # type drift here must never fail a leg that already spent tokens.
        value = window.get("used_percent") if isinstance(window, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "?" if value is None else f"{value}%"
        return f"{value:.1f}%"

    print(
        f"(codex-cli: {leg_name} leg done; subscription usage 5h={_pct(rate_limits.get('primary'))} "
        f"weekly={_pct(rate_limits.get('secondary'))} plan={rate_limits.get('plan_type')})"
    )


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


def _kill_group(proc: subprocess.Popen[str]) -> None:
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


def _close_pipes(proc: subprocess.Popen[str]) -> None:
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
        # Thread-cumulative usage after the last leg that reported any: the
        # stream's turn.completed.usage is the thread total, so an attempt's
        # own share is its returning leg's delta against this. Reset per start_build (new
        # thread).
        self._thread_total: dict[str, int] | None = None
        # True once a leg's own share could not be measured: _thread_total is
        # then not known to be codex's cumulative after that leg, so a stream
        # delta taken against it is not provably the next leg's own (it may
        # span the unmeasured one). See _usage_from.
        self._thread_total_gap = False
        # Empty directory used as HOME for every codex subprocess of this run
        # (see _subprocess_env). Created in start_build, reused by resumes.
        self._home_override: str | None = None

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
                env=_subprocess_env(self._home_override),
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
        lines = [ln.strip() for ln in combined.splitlines() if ln.strip()]
        mode_line = next(
            (ln for ln in lines if SUBSCRIPTION_MODE_LINE in ln),
            next((ln for ln in lines if "ogged in" in ln), combined.strip()[:200]),
        )
        # Keep only the mode, never whatever follows " - " (an API-key login
        # line carries a redacted key fragment there). This is what gets
        # raised on failure AND recorded in raw["auth_mode"] on success.
        mode_line = mode_line.split(" - ", 1)[0]
        if proc.returncode != 0 or SUBSCRIPTION_MODE_LINE not in combined:
            shown = mode_line
            raise HarnessError(
                "codex is not logged in with a ChatGPT subscription "
                f"(`codex login status` exited {proc.returncode}: {shown!r}). "
                "Run `codex login` (or `codex login --device-auth` on a headless box); "
                "do not log in with an API key — runs must stay on subscription auth."
            )
        _refuse_operator_instructions()
        return mode_line

    def _run_leg(self, cmd: list[str], cwd: str, stdin_text: str) -> _Leg:
        timeout = _build_timeout()
        started = time.monotonic()
        proc = subprocess.Popen(  # noqa: S603 — args are constructed, not shell
            cmd,
            cwd=cwd,
            env=_subprocess_env(self._home_override),
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
            try:
                _kill_group(proc)
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
            try:
                _kill_group(proc)
            finally:
                _close_pipes(proc)
            raise
        wall = time.monotonic() - started
        return _Leg(stdout or "", stderr or "", proc.returncode, wall)

    def _attempt(
        self,
        cmd: list[str],
        cwd: str,
        stdin_text: str,
        *,
        is_start: bool,
        expected_thread_id: str | None = None,
    ) -> AttemptResult:
        """Run legs until one returns (dead turns are retried), and classify
        the outcome as one attempt.

        ``expected_thread_id`` (resume legs): the thread the stream must report.
        A different id would mean codex resumed or forked something else, and
        the thread-total delta would be taken against the wrong baseline."""
        retries = _dead_turn_retries()
        total_wall = 0.0
        dead_legs = 0
        dead_leg_errors: list[str] = []
        # A dead START leg leaves a codex thread behind that nothing else in
        # the record references; without these ids an orphaned thread cannot
        # be traced back to the run that created it (review round 7).
        dead_leg_thread_ids: list[str] = []
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
            if expected_thread_id is not None and thread_id != expected_thread_id:
                raise HarnessError(
                    f"codex resume reported thread {thread_id}, expected {expected_thread_id}"
                )
            terminal = _terminal_event(events)
            rollout_path = _find_rollout(thread_id)
            usage = _usage_from(
                events,
                rollout_path,
                previous_thread_total=self._thread_total,
                baseline_has_gap=self._thread_total_gap,
            )
            errors = _error_messages(events)
            completed = terminal is not None and terminal.get("type") == "turn.completed"
            if completed:
                if usage.source == "none":
                    raise HarnessError(
                        f"codex ({leg_name}) reported turn.completed for thread {thread_id} but "
                        "no token usage could be found in the event stream — and the rollout "
                        f"({rollout_path}) is not a usage source. A success is never recorded "
                        "with zero tokens: the usage parser is uncalibrated or the CLI format "
                        "changed (plans/codex-cli-harness.md §4.5, §6)."
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
                    dead_leg_thread_ids=dead_leg_thread_ids,
                    is_start=is_start,
                    harness_error=None,
                )
            error_text = _harness_error_text(terminal, errors, leg.stderr, leg.returncode)
            # Nothing from the model, nothing measured, and no turn recorded
            # in the rollout beyond a baseline with no RECORDED gap: this turn
            # never happened, so retry it rather than capture it as the
            # attempt's result. ("No recorded gap", not "whole" — see
            # _advances, which spells out why the attempt, not the leg, is the
            # granularity that makes this sound: review round 10.) A rollout record that merely sits beyond a SHORT
            # baseline proves nothing — reading it as "this leg spent tokens"
            # captured genuinely dead legs and corrupted attempts-to-green
            # (review round 9, C-1).
            dead = (
                usage.source == "none"
                and not _has_model_activity(events)
                and not usage.active_per_rollout
            )
            if dead:
                dead_legs += 1
                dead_leg_errors.extend(errors or [error_text])
                dead_leg_thread_ids.append(thread_id)
                if leg_no < retries:
                    time.sleep(DEAD_TURN_RETRY_PAUSE_SECONDS)
                    continue
                raise HarnessError(
                    f"codex ({leg_name}) turn produced no model activity on {leg_no + 1} "
                    f"leg(s) (exit {leg.returncode}, thread {thread_id}): {error_text}"
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
                dead_leg_thread_ids=dead_leg_thread_ids,
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
        dead_leg_thread_ids: list[str],
        is_start: bool,
        harness_error: str | None,
    ) -> AttemptResult:
        if usage.thread_total is not None:
            self._thread_total = usage.thread_total
        self._thread_total_gap = usage.baseline_gap
        if usage.rate_limits:
            # Printed only for legs that return: a dead leg's rollout shows
            # the previous turn's (stale) figures.
            with contextlib.suppress(Exception):  # display only; never fails a spent leg
                _print_rate_limits("start" if is_start else "resume", usage.rate_limits)
        # A dead leg is retried rather than captured, so it never reaches this
        # method and records no delta of its own. WHERE its spend lands is
        # decided by the thread it ran on, and only one of the two cases needs
        # marking.
        #   Dead RESUME leg — same thread (its id is validated equal to this
        # one's), so the baseline never moved and its spend is inside the
        # delta below: measured 2026-09-12, a dead resume leg that spent
        # 4242/77 turned this attempt's delta from 3959/5 into 8201/82. Both
        # legs are this one ATTEMPT, which is the granularity the figure is
        # published at, so no caveat is due.
        #   Dead START leg — a DIFFERENT thread, which nothing in the run ever
        # resumes or reads, so the figure below is short by a whole turn and
        # so is every total derived from it. Mark it, or the results site
        # shows an understated cost with nothing to say so: both the attempt
        # marker and the run-level lower-bound marking are driven by this list
        # (review rounds 11 I-2, 12 I-2).
        abandoned_thread_ids = [t for t in dead_leg_thread_ids if t != thread_id]
        caveats = list(usage.caveats)
        if abandoned_thread_ids:
            caveats.append(USAGE_CAVEAT_DEAD_LEG)
        raw: dict[str, Any] = {
            "thread_id": thread_id,
            "exit_code": leg.returncode,
            "usage": usage.raw,
            "usage_source": usage.source,
            # This ATTEMPT's own share (the stream reports the thread
            # total, and a retried dead resume leg is inside this figure).
            "usage_delta": usage.delta,
            # True when this leg spent tokens nobody could read: the zeros
            # above mean "unknown", not "free". Never true on a completed
            # turn (that raises in _attempt).
            "usage_missing": usage.usage_missing,
            # THE field to read before trusting usage_delta (or the token
            # columns derived from it) as this attempt's cost: false when the
            # figure is not a measure of this attempt alone — short because
            # nothing measured it, not provably its own because the baseline
            # it was taken against may be short by an earlier unmeasured leg,
            # or short by an abandoned thread. usage_caveats names which,
            # from a fixed three-value vocabulary; none of them needs any
            # harness internals to interpret. An over-reported attempt is no
            # more honest than a zero-reported one, so both are marked
            # (review round 8).
            "usage_faithful": not caveats,
            "usage_caveats": caveats,
            "reasoning_output_tokens": usage.reasoning_output_tokens,
            # Subscription budget as codex last reported it (5-hour + weekly
            # windows, used_percent) — best-effort from the rollout.
            "rate_limits": usage.rate_limits,
            "final_event": terminal,
            "event_counts": dict(Counter(str(ev.get("type")) for ev in events)),
            "errors": errors[:RAW_LIST_LIMIT],
            "errors_total": len(errors),
            "effort": self._effort,
            "model": self._model,
            "cache_tokens_reported": usage.cache_tokens_reported,
            "dead_turn_retries": dead_legs,
            # Why the retried legs were dead.
            "dead_turn_errors": dead_leg_errors[:RAW_LIST_LIMIT],
            "dead_turn_errors_total": len(dead_leg_errors),
            # The threads those dead legs abandoned (a dead start leg creates a
            # fresh thread each time); the only record that they exist. A dead
            # RESUME leg reports the thread named above — its id is validated
            # equal — so it is filtered out rather than repeating thread_id.
            "dead_turn_thread_ids": abandoned_thread_ids[:RAW_LIST_LIMIT],
            # Relative to $CODEX_HOME: the record is published, and the
            # absolute path names the operator's home directory.
            "rollout_path": _publishable_rollout_path(rollout_path),
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
        base = _throwaway_home_base()
        base.mkdir(parents=True, exist_ok=True)
        # Nothing tears these down at the end of a run (no Harness teardown
        # hook, and Ctrl-C escapes any try/finally), so each run reaps the
        # ones that have aged out before adding its own.
        _reap_throwaway_homes(base)
        home = tempfile.mkdtemp(prefix="run-", dir=base)
        try:
            _prepare_home(home)
            self._home_override = home
            self._auth_mode = self._preflight()
        except BaseException:
            self._home_override = None
            shutil.rmtree(home, ignore_errors=True)
            raise
        self._thread_total = None
        self._thread_total_gap = False
        self._worktree_dir = worktree_dir
        self._model = model
        self._effort = effort
        cmd = [
            "codex",
            "exec",
            "--ignore-user-config",
            *_ENV_POLICY_ARGS,
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
            *_ENV_POLICY_ARGS,
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
        return self._attempt(
            cmd,
            self._worktree_dir,
            followup_prompt,
            is_start=False,
            expected_thread_id=session_handle,
        )
