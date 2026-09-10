"""Codex CLI adapter, driven by REAL recorded ``codex exec --json`` streams.

``tests/fixtures/codex_exec_unauthenticated.jsonl`` (start) and
``…_resume_unauthenticated.jsonl`` (resume) are verbatim captures from codex-cli
0.154.0 on a logged-out box: ``thread.started`` → ``turn.started`` → ``error``
× n → ``item.completed{type: error}`` → ``turn.failed``; exit 1. They are the
ground truth for the failure envelope. No test here spawns a real ``codex``:
``subprocess.Popen`` (the legs) and ``subprocess.run`` (the ``codex login
status`` pre-flight) are faked.

Streams marked SYNTHETIC below are the real envelope with one line changed to
exercise a classification branch; they assert behaviour of *our* classifier,
never a shape of codex's output. Token-usage parsing is not tested here until
the success fixtures exist (plans/codex-cli-harness.md §6).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pagehub_benchmarks.harnesses import HARNESSES, codex_cli, get_harness
from pagehub_benchmarks.harnesses.codex_cli import (
    EFFORT_MAP,
    STRIPPED_ENV_VARS,
    CodexCliHarness,
    HarnessError,
    _parse_jsonl,
)

FIXTURES = Path(__file__).parent / "fixtures"
START_FIXTURE = (FIXTURES / "codex_exec_unauthenticated.jsonl").read_text()
RESUME_FIXTURE = (FIXTURES / "codex_exec_resume_unauthenticated.jsonl").read_text()
CHATGPT_LINE = "Logged in using ChatGPT"


def _thread_id_of(stream: str) -> str:
    for line in stream.splitlines():
        obj = json.loads(line)
        if obj.get("type") == "thread.started":
            return obj["thread_id"]
    raise AssertionError("fixture has no thread.started")


START_THREAD_ID = _thread_id_of(START_FIXTURE)


def _synthetic_non_dead_failure(stream: str) -> str:
    """SYNTHETIC: the real failure envelope plus one non-error item after
    ``turn.started`` — 'the model did something, then the turn failed'."""
    lines = stream.splitlines()
    idx = next(i for i, ln in enumerate(lines) if '"turn.started"' in ln)
    item = json.dumps(
        {"type": "item.completed", "item": {"id": "synthetic_1", "type": "synthetic_non_error_item"}}
    )
    return "\n".join(lines[: idx + 1] + [item] + lines[idx + 1 :]) + "\n"


def _synthetic_completed_no_usage(stream: str) -> str:
    """SYNTHETIC: the real envelope with ``turn.failed`` swapped for a bare
    ``turn.completed`` — a 'success' that reports nothing we can price."""
    lines = [ln for ln in stream.splitlines() if '"turn.failed"' not in ln]
    return "\n".join(lines + [json.dumps({"type": "turn.completed"})]) + "\n"


# --------------------------------------------------------------------------
# fakes for the subprocess layer


class _FakePipe:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    """``dies_on_sigterm``: after ``wait()`` the process reports exited, so no
    SIGKILL follows. Otherwise ``poll()`` keeps saying alive ⇒ SIGKILL path."""

    def __init__(self, stdout: str, stderr: str, returncode: int, *,
                 raise_first: BaseException | None = None, dies_on_sigterm: bool = False):
        self._stdout, self._stderr, self.returncode = stdout, stderr, returncode
        self._raise_first = raise_first
        self._dies_on_sigterm = dies_on_sigterm
        self.pid = 4242
        self.stdin, self.stdout, self.stderr = _FakePipe(), _FakePipe(), _FakePipe()
        self.communicate_calls: list[dict[str, Any]] = []
        self._waited = False

    def communicate(self, input=None, timeout=None):  # noqa: ANN001
        self.communicate_calls.append({"input": input, "timeout": timeout})
        if self._raise_first is not None and len(self.communicate_calls) == 1:
            raise self._raise_first
        return self._stdout, self._stderr

    def wait(self, timeout=None):  # noqa: ANN001
        self._waited = True
        return self.returncode

    def poll(self):
        return self.returncode if (self._dies_on_sigterm and self._waited) else None

    @property
    def pipes_closed(self) -> bool:
        return all(p.closed for p in (self.stdin, self.stdout, self.stderr))


class _Legs:
    """Scripted ``subprocess.Popen`` replacement; records every call.

    ``raise_first`` makes the FIRST leg's first ``communicate`` raise that
    exception (``TimeoutExpired`` for the timeout path, ``KeyboardInterrupt``
    for the interrupt path)."""

    def __init__(self, script: list[tuple[str, str, int]], *,
                 raise_first: BaseException | None = None, dies_on_sigterm: bool = False):
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.procs: list[_FakeProc] = []
        self._raise_first = raise_first
        self._dies_on_sigterm = dies_on_sigterm

    def __call__(self, cmd, **kwargs):  # noqa: ANN001
        stdout, stderr, rc = self.script[min(len(self.calls), len(self.script) - 1)]
        self.calls.append({"cmd": list(cmd), **kwargs})
        proc = _FakeProc(
            stdout, stderr, rc,
            raise_first=self._raise_first if not self.procs else None,
            dies_on_sigterm=self._dies_on_sigterm,
        )
        self.procs.append(proc)
        return proc


class _Preflight:
    """Scripted ``subprocess.run`` replacement for ``codex login status``."""

    def __init__(self, stdout: str = "", stderr: str = CHATGPT_LINE + "\n", returncode: int = 0,
                 *, raise_with: Exception | None = None):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode
        self.raise_with = raise_with
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd, **kwargs):  # noqa: ANN001
        self.calls.append({"cmd": list(cmd), **kwargs})
        if self.raise_with is not None:
            raise self.raise_with
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """No real codex home is globbed; no real sleeps; a known env to check."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.delenv("CODEX_DEAD_TURN_RETRIES", raising=False)
    monkeypatch.delenv("CODEX_BUILD_TIMEOUT_SECONDS", raising=False)
    for var in STRIPPED_ENV_VARS:
        monkeypatch.setenv(var, f"{var.lower()}-should-be-dropped")
    sleeps: list[float] = []
    monkeypatch.setattr(codex_cli.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def _install(monkeypatch, legs: _Legs, preflight: _Preflight | None = None) -> _Preflight:
    preflight = preflight or _Preflight()
    monkeypatch.setattr(codex_cli.subprocess, "Popen", legs)
    monkeypatch.setattr(codex_cli.subprocess, "run", preflight)
    return preflight


NON_DEAD_START = _synthetic_non_dead_failure(START_FIXTURE)
NON_DEAD_RESUME = _synthetic_non_dead_failure(RESUME_FIXTURE)


# --------------------------------------------------------------------------
# 1 + 2: argv, byte-exact; prompt on stdin verbatim; cwd = worktree


def test_start_argv_byte_exact_and_prompt_on_stdin(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(NON_DEAD_START, "", 1)])
    _install(monkeypatch, legs)
    prompt = 'Line one "quoted" and `ticks`.\n\n## {{ not_jinja }} $HOME — ✓\n- bullet\n'
    h = CodexCliHarness()
    h.start_build(str(tmp_path), prompt, "gpt-6-astra", {"effort": "high"})

    call = legs.calls[0]
    assert call["cmd"] == [
        "codex", "exec", "--ignore-user-config", "-m", "gpt-6-astra", "--json",
        "-C", str(tmp_path), "--sandbox", "workspace-write",
        "-c", 'model_reasoning_effort="high"',
        "-c", "sandbox_workspace_write.network_access=true",
        "-",
    ]
    assert call["cwd"] == str(tmp_path)
    assert call["start_new_session"] is True
    assert call["stdin"] is subprocess.PIPE
    assert legs.procs[0].communicate_calls[0]["input"] == prompt  # verbatim, on stdin
    assert "danger-full-access" not in call["cmd"]


def test_resume_argv_byte_exact(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(NON_DEAD_START, "", 1), (NON_DEAD_RESUME, "", 1)])
    _install(monkeypatch, legs)
    h = CodexCliHarness()
    r1 = h.start_build(str(tmp_path), "p1", "gpt-6-astra", {"effort": "xhigh"})
    followup = "The conformance evals are still failing:\n\n- thing broke\n\nFix it."
    h.continue_build(r1.session_handle, followup)

    call = legs.calls[1]
    assert call["cmd"] == [
        "codex", "exec", "resume", START_THREAD_ID, "--ignore-user-config", "--json",
        "-m", "gpt-6-astra",
        "-c", 'model_reasoning_effort="xhigh"',
        "-c", 'sandbox_mode="workspace-write"',
        "-c", "sandbox_workspace_write.network_access=true",
        "-",
    ]
    assert "-C" not in call["cmd"] and "--sandbox" not in call["cmd"]  # not flags of `resume`
    assert call["cwd"] == str(tmp_path)
    assert legs.procs[1].communicate_calls[0]["input"] == followup


# --------------------------------------------------------------------------
# 3: effort mapping


@pytest.mark.parametrize("effort", sorted(EFFORT_MAP))
def test_effort_maps_one_to_one(monkeypatch, tmp_path, isolated_env, effort):
    legs = _Legs([(NON_DEAD_START, "", 1)])
    _install(monkeypatch, legs)
    CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": effort})
    cmd = legs.calls[0]["cmd"]
    assert cmd[cmd.index("-c") + 1] == f'model_reasoning_effort="{effort}"'


@pytest.mark.parametrize(
    "config",
    [{"effort": "ultra"}, {"effort": "minimal"}, {"effort": "bogus"}, {"effort": "High"},
     {"effort": None}, {"effort": 1}, {"effort": ""}, {}, None],
)
def test_effort_unmapped_or_missing_raises_before_any_subprocess(monkeypatch, tmp_path, isolated_env, config):
    legs = _Legs([(NON_DEAD_START, "", 1)])
    preflight = _install(monkeypatch, legs)
    with pytest.raises(HarnessError, match="config.effort"):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", config)
    assert legs.calls == [] and preflight.calls == []


# --------------------------------------------------------------------------
# 4: environment


def test_env_strips_all_three_auth_vars_on_legs_and_preflight(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(NON_DEAD_START, "", 1)])
    preflight = _install(monkeypatch, legs)
    CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    for env in (legs.calls[0]["env"], preflight.calls[0]["env"]):
        for var in STRIPPED_ENV_VARS:
            assert var not in env
        assert env["PATH"] == "/usr/bin"
        assert env["CODEX_HOME"].endswith("codex-home")
    assert STRIPPED_ENV_VARS == ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")
    # nothing added beyond the inherited env — on the leg AND the pre-flight
    expected = {k for k in os.environ if k not in STRIPPED_ENV_VARS}
    assert set(legs.calls[0]["env"]) == expected
    assert set(preflight.calls[0]["env"]) == expected


# --------------------------------------------------------------------------
# 5: pre-flight — ChatGPT-subscription login required


def test_preflight_not_logged_in_raises_without_exec(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(NON_DEAD_START, "", 1)])
    _install(monkeypatch, legs, _Preflight(stdout="", stderr="Not logged in\n", returncode=1))
    with pytest.raises(HarnessError, match="not logged in with a ChatGPT subscription"):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert legs.calls == []


def test_preflight_api_key_login_raises_without_exec(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(NON_DEAD_START, "", 1)])
    _install(monkeypatch, legs, _Preflight(stdout="Logged in using an API key - sk-…\n", stderr="", returncode=0))
    with pytest.raises(HarnessError, match="Logged in using an API key") as excinfo:
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert legs.calls == []
    assert "sk-" not in str(excinfo.value)  # the key fragment after " - " is not echoed


def test_preflight_chatgpt_proceeds_and_records_auth_mode(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(NON_DEAD_START, "", 1)])
    coloured = f"\x1b[32m{CHATGPT_LINE}\x1b[0m\n"  # a coloured TTY-style line must still match
    preflight = _install(monkeypatch, legs, _Preflight(stdout=coloured, stderr="", returncode=0))
    r = CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert preflight.calls[0]["cmd"] == ["codex", "login", "status"]
    assert preflight.calls[0]["timeout"] == codex_cli.PREFLIGHT_TIMEOUT_SECONDS
    assert len(legs.calls) == 1
    assert r.raw["auth_mode"] == CHATGPT_LINE


def test_preflight_file_not_found_propagates_unwrapped(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(NON_DEAD_START, "", 1)])
    _install(monkeypatch, legs, _Preflight(raise_with=FileNotFoundError("codex")))
    with pytest.raises(FileNotFoundError):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert legs.calls == []


def test_preflight_timeout_raises(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(NON_DEAD_START, "", 1)])
    _install(monkeypatch, legs, _Preflight(raise_with=subprocess.TimeoutExpired("codex", 30)))
    with pytest.raises(HarnessError, match="login status"):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert legs.calls == []


# --------------------------------------------------------------------------
# 7: dead turns — the real logged-out streams — retried, then raise on either leg


def test_dead_start_turn_is_retried_then_raises(monkeypatch, tmp_path, isolated_env):
    sleeps = isolated_env
    legs = _Legs([(START_FIXTURE, "some stderr", 1)])
    _install(monkeypatch, legs)
    with pytest.raises(HarnessError, match="401 Unauthorized"):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert len(legs.calls) == 1 + codex_cli.DEFAULT_DEAD_TURN_RETRIES
    assert sleeps == [codex_cli.DEAD_TURN_RETRY_PAUSE_SECONDS] * codex_cli.DEFAULT_DEAD_TURN_RETRIES


def test_dead_resume_turn_is_retried_then_raises(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(NON_DEAD_START, "", 1), (RESUME_FIXTURE, "", 1)])
    _install(monkeypatch, legs)
    h = CodexCliHarness()
    r1 = h.start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    with pytest.raises(HarnessError, match="no model activity"):
        h.continue_build(r1.session_handle, "fix it")
    assert len(legs.calls) == 1 + (1 + codex_cli.DEFAULT_DEAD_TURN_RETRIES)


def test_dead_turn_retry_count_is_env_tunable(monkeypatch, tmp_path, isolated_env):
    monkeypatch.setenv("CODEX_DEAD_TURN_RETRIES", "0")
    legs = _Legs([(START_FIXTURE, "", 1)])
    _install(monkeypatch, legs)
    with pytest.raises(HarnessError):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert len(legs.calls) == 1 and isolated_env == []


# --------------------------------------------------------------------------
# non-dead failure (model activity, then turn.failed) is CAPTURED, not raised


def test_non_dead_failure_is_captured_with_error_text(monkeypatch, tmp_path, isolated_env):
    osc_link = "\x1b]8;;https://example\x1b\\link\x1b]8;;\x1b\\"
    legs = _Legs([(NON_DEAD_START, f"\x1b[31mERROR\x1b[0m codex_api: ws failed {osc_link}\n", 1)])
    _install(monkeypatch, legs)
    r = CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert len(legs.calls) == 1  # never retried
    assert r.session_handle == START_THREAD_ID
    assert (r.input_tokens, r.output_tokens, r.cache_tokens) == (0, 0, 0)
    assert r.reported_cost_usd is None
    raw = r.raw
    assert "401 Unauthorized" in raw["harness_error"]
    assert raw["exit_code"] == 1
    assert raw["thread_id"] == START_THREAD_ID
    assert raw["final_event"]["type"] == "turn.failed"
    assert raw["usage"] is None and raw["usage_source"] == "none"
    assert raw["cache_tokens_reported"] is False
    assert raw["dead_turn_retries"] == 0
    assert raw["rollout_path"] is None  # CODEX_HOME points at an empty dir
    assert raw["effort"] == "high" and raw["model"] == "gpt-6-astra"
    assert raw["event_counts"]["error"] >= 1 and raw["event_counts"]["turn.failed"] == 1
    assert raw["errors_total"] == len(raw["errors"]) >= 1
    assert raw["stderr_tail"] == "ERROR codex_api: ws failed link\n"  # CSI colours + OSC link stripped
    json.dumps(raw)  # plain JSON types only — must round-trip through RunRecord.write


def test_wall_time_sums_legs_and_excludes_pauses(monkeypatch, tmp_path, isolated_env):
    ticks = iter([0.0, 10.0, 100.0, 130.0])  # leg 1: 10 s, leg 2: 30 s
    monkeypatch.setattr(codex_cli.time, "monotonic", lambda: next(ticks))
    legs = _Legs([(START_FIXTURE, "", 1), (NON_DEAD_START, "", 1)])
    _install(monkeypatch, legs)
    r = CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert r.wall_time_seconds == pytest.approx(40.0)
    assert r.raw["dead_turn_retries"] == 1
    assert isolated_env == [codex_cli.DEAD_TURN_RETRY_PAUSE_SECONDS]


def test_dead_leg_error_messages_survive_into_the_returned_attempt(monkeypatch, tmp_path, isolated_env):
    """A retried attempt must say WHY its earlier legs were dead — the
    abandoned thread is not referenced anywhere else in the record."""
    legs = _Legs([(START_FIXTURE, "", 1), (NON_DEAD_START, "", 1)])
    _install(monkeypatch, legs)
    r = CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    dead_msgs = codex_cli._error_messages(_parse_jsonl(START_FIXTURE)[0])
    assert r.raw["dead_turn_retries"] == 1
    assert r.raw["dead_turn_errors_total"] == len(dead_msgs) >= 1
    assert r.raw["dead_turn_errors"] == dead_msgs[: codex_cli.RAW_LIST_LIMIT]
    assert any("401 Unauthorized" in m for m in r.raw["dead_turn_errors"])
    assert r.raw["errors_total"] == len(codex_cli._error_messages(_parse_jsonl(NON_DEAD_START)[0]))


# --------------------------------------------------------------------------
# 9: no thread.started ⇒ raise after exactly one call (no retry)


@pytest.mark.parametrize("stdout", ["", "not json at all\n", json.dumps({"type": "turn.started"}) + "\n"])
def test_no_thread_started_raises_after_one_call(monkeypatch, tmp_path, isolated_env, stdout):
    legs = _Legs([(stdout, "", 1)])
    _install(monkeypatch, legs)
    with pytest.raises(HarnessError, match="no thread.started"):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert len(legs.calls) == 1 and isolated_env == []


# --------------------------------------------------------------------------
# 10: timeout kills the whole process group, drains, raises


def test_timeout_kills_process_group_and_drains(monkeypatch, tmp_path, isolated_env):
    monkeypatch.setenv("CODEX_BUILD_TIMEOUT_SECONDS", "7")
    legs = _Legs([(NON_DEAD_START, "", 1)], raise_first=subprocess.TimeoutExpired("codex", 7))
    _install(monkeypatch, legs)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(codex_cli.os, "getpgid", lambda pid: pid + 1)
    monkeypatch.setattr(codex_cli.os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))
    with pytest.raises(HarnessError, match="timed out after 7s"):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    proc = legs.procs[0]
    assert proc.communicate_calls[0]["timeout"] == 7
    # SIGTERM to the group; poll() still None afterwards ⇒ SIGKILL to the group
    assert signals == [(4243, codex_cli.signal.SIGTERM), (4243, codex_cli.signal.SIGKILL)]
    # a second, bounded communicate() drained the pipes, then they were closed
    assert proc.communicate_calls[1]["timeout"] == codex_cli.DRAIN_TIMEOUT_SECONDS
    assert proc.pipes_closed


def test_timeout_sigterm_alone_when_group_exits(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(NON_DEAD_START, "", 1)], raise_first=subprocess.TimeoutExpired("codex", 1),
                 dies_on_sigterm=True)
    _install(monkeypatch, legs)
    signals: list[int] = []
    monkeypatch.setattr(codex_cli.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(codex_cli.os, "killpg", lambda pgid, sig: signals.append(sig))
    with pytest.raises(HarnessError, match="timed out"):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert signals == [codex_cli.signal.SIGTERM]  # no SIGKILL once the group is gone


def test_interrupt_during_leg_kills_group_closes_pipes_and_reraises(monkeypatch, tmp_path, isolated_env):
    """Ctrl-C: the child is in its own process group, so the terminal's SIGINT
    never reaches it — the adapter must kill the group itself, then re-raise
    the interrupt unwrapped (never as a HarnessError)."""
    legs = _Legs([(NON_DEAD_START, "", 1)], raise_first=KeyboardInterrupt(), dies_on_sigterm=True)
    _install(monkeypatch, legs)
    signals: list[int] = []
    monkeypatch.setattr(codex_cli.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(codex_cli.os, "killpg", lambda pgid, sig: signals.append(sig))
    with pytest.raises(KeyboardInterrupt):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert signals == [codex_cli.signal.SIGTERM]
    assert legs.procs[0].pipes_closed
    assert len(legs.procs[0].communicate_calls) == 1  # no drain attempt on interrupt


# --------------------------------------------------------------------------
# 11: parser + rule 8


def test_parse_jsonl_keeps_chatter_bounded():
    chatter = "\n".join(f"noise line {i}" for i in range(30))
    stdout = chatter + "\n" + START_FIXTURE
    events, unparsed, total = _parse_jsonl(stdout)
    assert len(events) == len(START_FIXTURE.splitlines())
    assert total == 30 and len(unparsed) == codex_cli.RAW_LIST_LIMIT
    assert unparsed[0] == "noise line 0"


def test_exit_zero_with_turn_failed_is_still_a_failure(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(NON_DEAD_START, "", 0)])  # exit codes are advisory; the stream is the truth
    _install(monkeypatch, legs)
    r = CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert "harness_error" in r.raw and r.raw["exit_code"] == 0


def test_turn_completed_without_usage_raises_never_zero_token_success(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(_synthetic_completed_no_usage(START_FIXTURE), "", 0)])
    _install(monkeypatch, legs)
    with pytest.raises(HarnessError, match="never recorded with zero tokens"):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert len(legs.calls) == 1  # a completed turn is never "dead" — no retry


def test_errors_list_is_bounded_and_counted(monkeypatch, tmp_path, isolated_env):
    many = "\n".join(json.dumps({"type": "error", "message": f"e{i}"}) for i in range(50))
    lines = NON_DEAD_START.splitlines()
    idx = next(i for i, ln in enumerate(lines) if '"turn.started"' in ln)
    stream = "\n".join(lines[: idx + 1] + [many] + lines[idx + 1 :]) + "\n"
    legs = _Legs([(stream, "", 1)])
    _install(monkeypatch, legs)
    r = CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert len(r.raw["errors"]) == codex_cli.RAW_LIST_LIMIT
    baseline = len(codex_cli._error_messages(_parse_jsonl(NON_DEAD_START)[0]))
    assert baseline >= 1
    assert r.raw["errors_total"] == 50 + baseline


def test_stderr_tail_is_bounded(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(NON_DEAD_START, "x" * 5000, 1)])
    _install(monkeypatch, legs)
    r = CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert len(r.raw["stderr_tail"]) == codex_cli.STDERR_TAIL_CHARS


# --------------------------------------------------------------------------
# 13: registry


def test_registry_has_both_harnesses():
    assert isinstance(get_harness("codex-cli"), CodexCliHarness)
    assert sorted(HARNESSES) == ["claude-code", "codex-cli"]
    with pytest.raises(ValueError, match="codex-cli"):
        get_harness("nope")


# --------------------------------------------------------------------------
# 14: continue_build guards


def test_continue_before_start_raises():
    with pytest.raises(HarnessError, match="before start_build"):
        CodexCliHarness().continue_build("sess", "p")


def test_continue_with_empty_handle_raises(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(NON_DEAD_START, "", 1)])
    _install(monkeypatch, legs)
    h = CodexCliHarness()
    h.start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    with pytest.raises(HarnessError, match="empty session handle"):
        h.continue_build("", "p2")
    assert len(legs.calls) == 1
