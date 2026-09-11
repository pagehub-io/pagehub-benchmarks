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
    homes = tmp_path / "codex-homes"
    monkeypatch.setattr(codex_cli, "_throwaway_home_base", lambda: homes)
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
        "codex", "exec", "--ignore-user-config",
        "--disable", "shell_snapshot",
        "-c", "shell_environment_policy.experimental_use_profile=false",
        "-c", 'shell_environment_policy.exclude=["*KEY*","*SECRET*","*TOKEN*","*PASSWORD*"]',
        "-m", "gpt-6-astra", "--json",
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
        "codex", "exec", "resume", START_THREAD_ID, "--ignore-user-config",
        "--disable", "shell_snapshot",
        "-c", "shell_environment_policy.experimental_use_profile=false",
        "-c", 'shell_environment_policy.exclude=["*KEY*","*SECRET*","*TOKEN*","*PASSWORD*"]',
        "--json",
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
    assert f'model_reasoning_effort="{effort}"' in cmd
    assert cmd[cmd.index(f'model_reasoning_effort="{effort}"') - 1] == "-c"


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
        assert env["CODEX_HOME"].endswith("codex-home")  # pinned to the (test) codex home
    assert STRIPPED_ENV_VARS == ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")
    # nothing added beyond the inherited env except the pinned CODEX_HOME — on
    # the leg AND the pre-flight; HOME is swapped, never dropped
    expected = {k for k in os.environ if k not in STRIPPED_ENV_VARS} | {"CODEX_HOME", "HOME"}
    assert set(legs.calls[0]["env"]) == expected
    assert set(preflight.calls[0]["env"]) == expected


def test_env_home_is_a_throwaway_dir_and_codex_home_is_pinned(monkeypatch, tmp_path, isolated_env):
    """codex runs the agent's commands with `bash -lc`; with the operator's
    HOME that re-sources ~/.bashrc and its secrets into the agent's shell.
    The subprocess gets an empty HOME, while CODEX_HOME still points at the
    real login directory (verified 2026-09-11: secrets NONE, login works)."""
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    legs = _Legs([(NON_DEAD_START, "", 1), (NON_DEAD_RESUME, "", 1)])
    preflight = _install(monkeypatch, legs)
    h = CodexCliHarness()
    r1 = h.start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    h.continue_build(r1.session_handle, "again")
    envs = [preflight.calls[0]["env"], legs.calls[0]["env"], legs.calls[1]["env"]]
    homes = {e["HOME"] for e in envs}
    assert len(homes) == 1  # one throwaway HOME per run, shared by pre-flight and both legs
    home = Path(homes.pop())
    assert home.is_dir() and home != Path(os.environ["HOME"])
    assert home.parent == tmp_path / "codex-homes" and home.name.startswith("run-")
    # nothing but the PATH + locale profile: no secrets, no operator dotfiles
    assert [p.name for p in home.iterdir()] == [".bash_profile"]
    assert (home / ".bash_profile").read_text() == (
        "export PATH=/usr/bin\n"
        "export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 LC_CTYPE=en_US.UTF-8\n"
    )
    for e in envs:
        assert e["CODEX_HOME"] == os.environ["CODEX_HOME"]  # the real codex home, not <HOME>/.codex


def test_home_profile_restores_runner_path_and_locale(monkeypatch, tmp_path):
    """codex forces C.UTF-8 and a stock /etc/profile resets PATH for login
    shells; the profile re-exports the runner's PATH and locale (LC_ALL over
    LANG), shell-quoted."""
    monkeypatch.setenv("PATH", "/opt/py/bin:/usr/bin")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    codex_cli._prepare_home(str(tmp_path))
    assert (tmp_path / ".bash_profile").read_text() == (
        "export PATH=/opt/py/bin:/usr/bin\n"
        "export LANG=de_DE.UTF-8 LC_ALL=de_DE.UTF-8 LC_CTYPE=de_DE.UTF-8\n"
    )
    monkeypatch.setenv("LC_ALL", "x; touch /tmp/pwned")  # hostile values are quoted, never executed
    monkeypatch.setenv("PATH", "/usr/bin:$(touch /tmp/pwned)")
    codex_cli._prepare_home(str(tmp_path))
    text = (tmp_path / ".bash_profile").read_text()
    assert "LC_ALL='x; touch /tmp/pwned'" in text
    assert "PATH='/usr/bin:$(touch /tmp/pwned)'" in text


def test_home_profile_really_restores_path_and_locale_in_a_login_shell(monkeypatch, tmp_path):
    """Execute it: a real `/bin/bash -lc` with codex's forced C.UTF-8 env and
    WSL_DISTRO_NAME unset (so a stock /etc/profile resets PATH) must end up
    with the runner's PATH and locale."""
    import shutil as _shutil

    if not Path("/bin/bash").exists() or _shutil.which("bash") is None:
        pytest.skip("no bash")
    monkeypatch.setenv("PATH", f"{tmp_path / 'runner-bin'}:{os.environ['PATH']}")
    monkeypatch.setenv("LANG", "C")  # a locale every box can load
    monkeypatch.delenv("LC_ALL", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    codex_cli._prepare_home(str(home))
    env = {k: v for k, v in os.environ.items() if k != "WSL_DISTRO_NAME"}
    env.update(HOME=str(home), LANG="C.UTF-8", LC_ALL="C.UTF-8", LC_CTYPE="C.UTF-8")
    out = subprocess.run(
        ["/bin/bash", "-lc", 'echo "$PATH"; echo "$LC_ALL"'],
        env=env, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert out[0].split(":")[0] == str(tmp_path / "runner-bin")
    assert out[1] == "C"


def test_home_stays_empty_without_path_or_locale(monkeypatch, tmp_path):
    monkeypatch.delenv("PATH", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)
    codex_cli._prepare_home(str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_throwaway_home_base_is_outside_the_sandbox_writable_roots(monkeypatch):
    """A HOME under /tmp or $TMPDIR is writable by the sandboxed agent (it
    could plant $HOME/.agents/skills for its later turns) — refuse it."""
    monkeypatch.delenv("TMPDIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/someone")
    assert codex_cli._throwaway_home_base() == Path("/home/someone/.cache/pagehub-benchmarks/codex-homes")
    monkeypatch.setenv("XDG_CACHE_HOME", "/srv/cache")
    assert codex_cli._throwaway_home_base() == Path("/srv/cache/pagehub-benchmarks/codex-homes")
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/cache")
    with pytest.raises(HarnessError, match="writable to the agent"):
        codex_cli._throwaway_home_base()
    monkeypatch.setenv("TMPDIR", "/var/scratch")
    monkeypatch.setenv("XDG_CACHE_HOME", "/var/scratch/c")
    with pytest.raises(HarnessError, match="/var/scratch"):
        codex_cli._throwaway_home_base()


def test_throwaway_home_removed_when_preflight_fails(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(NON_DEAD_START, "", 1)])
    _install(monkeypatch, legs, _Preflight(stdout="", stderr="Not logged in\n", returncode=1))
    with pytest.raises(HarnessError):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "high"})
    assert list((tmp_path / "codex-homes").iterdir()) == []


def test_relative_codex_home_is_made_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_HOME", "rel-codex")
    assert codex_cli._subprocess_env("/x")["CODEX_HOME"] == str(tmp_path / "rel-codex")


def test_codex_home_defaults_to_runner_home_dot_codex_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    env = codex_cli._subprocess_env("/tmp/throwaway")
    assert env["CODEX_HOME"] == str(tmp_path / ".codex")
    assert env["HOME"] == "/tmp/throwaway"


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
    # a coloured TTY-style line (colon-form CSI params) must still match, a
    # preceding chatter line mentioning "logged in" must not be what gets
    # recorded, and any " - suffix" is never stored in the record
    coloured = f"note: previously logged in elsewhere\n\x1b[38:2:0:255:0m{CHATGPT_LINE} - workspace x\x1b[0m\n"
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


# --------------------------------------------------------------------------
# 6 / 8 / 12: token usage — calibrated against the REAL recorded success
# streams (codex-cli 0.154.0, gpt-6-astra, 2026-09-11) and the REAL rollout
# of that same thread (message bodies removed, usage lines verbatim).

OK_START = (FIXTURES / "codex_exec_ok.jsonl").read_text()
OK_RESUME = (FIXTURES / "codex_exec_resume_ok.jsonl").read_text()
OK_ROLLOUT = (FIXTURES / "codex_rollout_ok.jsonl").read_text()
OK_THREAD_ID = _thread_id_of(OK_START)


def _usage_line(stream: str) -> dict[str, int]:
    for line in stream.splitlines():
        obj = json.loads(line)
        if obj.get("type") == "turn.completed":
            return obj["usage"]
    raise AssertionError("no turn.completed in fixture")


def _rollout_turn_usages() -> list[dict[str, int]]:
    out = []
    for line in OK_ROLLOUT.splitlines():
        obj = json.loads(line)
        if obj.get("type") == "token_usage_record":
            out.append(obj["payload"]["turn_token_usage"])
    return out


def _install_rollout(tmp_path: Path) -> Path:
    """Place the recorded rollout where the adapter's glob finds it under
    the test's CODEX_HOME (isolated_env points CODEX_HOME at tmp_path/codex-home)."""
    d = tmp_path / "codex-home" / "sessions" / "2026" / "09" / "11"
    d.mkdir(parents=True)
    path = d / f"rollout-2026-09-11T10-08-21-{OK_THREAD_ID}.jsonl"
    path.write_text(OK_ROLLOUT)
    return path


def test_fixture_facts_hold():
    """Pin the facts the mapping rests on, straight from the fixtures."""
    start, resume = _usage_line(OK_START), _usage_line(OK_RESUME)
    turns = _rollout_turn_usages()
    assert len(turns) == 2
    # the stream's usage on a fresh thread == that turn's usage
    assert {k: start[k] for k in turns[0] if k != "total_tokens"} == {k: turns[0][k] for k in turns[0] if k != "total_tokens"}
    # the stream's usage on a RESUMED thread == the thread total (start + resume turn)
    for k in ("input_tokens", "cached_input_tokens", "output_tokens"):
        assert resume[k] == turns[0][k] + turns[1][k]
    # input_tokens includes the cached slice (total_tokens == input + output)
    for t in turns:
        assert t["total_tokens"] == t["input_tokens"] + t["output_tokens"]
        assert t["cached_input_tokens"] <= t["input_tokens"]
    assert _thread_id_of(OK_RESUME) == OK_THREAD_ID


def test_usage_start_leg_from_stream(monkeypatch, tmp_path, isolated_env):
    legs = _Legs([(OK_START, "", 0)])
    _install(monkeypatch, legs)
    r = CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "low"})
    u = _usage_line(OK_START)
    assert r.session_handle == OK_THREAD_ID
    assert r.input_tokens == u["input_tokens"] - u["cached_input_tokens"] - u["cache_write_input_tokens"] == 3199
    assert r.cache_read_tokens == u["cached_input_tokens"] == 12160
    assert r.cache_creation_tokens == u["cache_write_input_tokens"] == 0
    assert r.output_tokens == u["output_tokens"] == 5
    assert r.cache_tokens == 12160
    assert r.raw["usage"] == u  # verbatim
    assert r.raw["usage_source"] == "stream"
    assert r.raw["cache_tokens_reported"] is True
    assert r.raw["usage_delta"] == {**u}
    assert r.raw["reasoning_output_tokens"] == 0
    assert r.raw["rate_limits"] is None  # no rollout under this CODEX_HOME
    assert "harness_error" not in r.raw
    json.dumps(r.raw)


def test_usage_resume_leg_is_delta_of_thread_total(monkeypatch, tmp_path, isolated_env):
    """The resume stream reports the THREAD total; the attempt must record
    only its own turn — cross-checked against the rollout's turn_token_usage."""
    legs = _Legs([(OK_START, "", 0), (OK_RESUME, "", 0)])
    _install(monkeypatch, legs)
    h = CodexCliHarness()
    r1 = h.start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "low"})
    r2 = h.continue_build(r1.session_handle, "again")
    turn2 = _rollout_turn_usages()[1]
    assert r2.input_tokens == turn2["input_tokens"] - turn2["cached_input_tokens"] == 3959
    assert r2.cache_read_tokens == turn2["cached_input_tokens"] == 12160
    assert r2.output_tokens == turn2["output_tokens"] == 5
    assert r2.raw["usage"] == _usage_line(OK_RESUME)  # verbatim cumulative object
    assert r2.raw["usage_delta"] == {k: turn2[k] for k in r2.raw["usage_delta"]}
    assert r2.raw["usage_source"] == "stream"


def test_usage_thread_total_going_backwards_raises(monkeypatch, tmp_path, isolated_env):
    # SYNTHETIC ordering: a resume that reports LESS than the start leg did.
    legs = _Legs([(OK_RESUME, "", 0), (OK_START, "", 0)])
    _install(monkeypatch, legs)
    h = CodexCliHarness()
    r1 = h.start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "low"})
    with pytest.raises(HarnessError, match="went backwards"):
        h.continue_build(r1.session_handle, "again")


def test_usage_cached_exceeding_input_raises(monkeypatch, tmp_path, isolated_env):
    # SYNTHETIC: cached > input breaks the subset partition — refuse, don't mis-price.
    bad = OK_START.replace('"cached_input_tokens":12160', '"cached_input_tokens":99999')
    legs = _Legs([(bad, "", 0)])
    _install(monkeypatch, legs)
    with pytest.raises(HarnessError, match="subset assumption"):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "low"})


def test_rate_limits_recorded_from_rollout_on_success(monkeypatch, tmp_path, isolated_env):
    rollout = _install_rollout(tmp_path)
    legs = _Legs([(OK_START, "", 0)])
    _install(monkeypatch, legs)
    r = CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "low"})
    assert r.raw["rollout_path"] == str(rollout)
    rl = r.raw["rate_limits"]
    assert rl["plan_type"] == "plus"
    assert rl["primary"]["window_minutes"] == 300 and rl["primary"]["used_percent"] == 0.0
    assert rl["secondary"]["window_minutes"] == 10080
    assert r.raw["usage_source"] == "stream"  # the rollout is NOT the usage source on success


def test_non_dead_failure_takes_usage_from_rollout(monkeypatch, tmp_path, isolated_env):
    """SYNTHETIC stream: the real success envelope with turn.completed swapped
    for the real turn.failed line (model produced an item, then the turn
    failed — a failed turn carries no usage on the stream). Usage must come
    from the rollout's turn_token_usage of the LAST turn."""
    _install_rollout(tmp_path)
    failed_line = next(ln for ln in START_FIXTURE.splitlines() if '"turn.failed"' in ln)
    stream = "\n".join(
        [ln for ln in OK_START.splitlines() if '"turn.completed"' not in ln] + [failed_line]
    ) + "\n"
    legs = _Legs([(stream, "", 1)])
    _install(monkeypatch, legs)
    r = CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "low"})
    turn2 = _rollout_turn_usages()[1]  # last turn_context in the rollout is turn 2
    assert len(legs.calls) == 1  # non-dead: not retried
    assert r.raw["usage_source"] == "rollout"
    assert r.raw["usage"] == turn2  # verbatim turn_token_usage
    assert r.input_tokens == turn2["input_tokens"] - turn2["cached_input_tokens"]
    assert r.cache_read_tokens == turn2["cached_input_tokens"]
    assert r.output_tokens == turn2["output_tokens"]
    assert "401 Unauthorized" in r.raw["harness_error"]
    assert r.raw["rate_limits"]["plan_type"] == "plus"


def test_attempt_chain_through_runner_records_per_turn_usage(monkeypatch, tmp_path, isolated_env):
    """Attempt 1 (start fixture) → grader fails → attempt 2 (resume fixture)
    → grader passes, through execute_benchmark_run with real pricing."""
    from pagehub_benchmarks.config import load_pricing, parse_benchmark
    from pagehub_benchmarks.runner.run import execute_benchmark_run
    from tests.fakes import FakeFixtureFetcher, FakeGrader, gr

    prompt = tmp_path / "demo.md"
    prompt.write_text("Build the demo. Get the tests passing — that is all.\n")
    spec = parse_benchmark(
        {
            "name": "demo",
            "target_repo": "git@github.com:example/demo.git",
            "build_prompt_file": str(prompt),
            "grader": {"fixture_bundle": "fixtures/demo.json", "collection": "demo-rules",
                       "env": {"demo_url": "http://localhost:9999"}},
            "max_attempts": 3,
            "harnesses": [{"harness": "codex-cli", "model": "gpt-6-astra", "config": {"effort": "high"}}],
        },
        tmp_path / "demo.yaml",
    )
    legs = _Legs([(OK_START, "", 0), (OK_RESUME, "", 0)])
    _install(monkeypatch, legs)
    pricing = load_pricing()
    rec = execute_benchmark_run(
        spec=spec,
        harness_spec=spec.harnesses[0],
        harness=CodexCliHarness(),
        grader=FakeGrader([gr(False, ["thing broke"]), gr(True)]),
        worktree_dir=tmp_path / "wt",
        pricing=pricing,
        fixture_fetcher=FakeFixtureFetcher(),
        built_sha="deadbeef",
    )
    assert rec.passed is True and rec.attempts == 2
    # attempt 2 resumed attempt 1's thread with the failing-eval follow-up
    assert legs.calls[1]["cmd"][2:4] == ["resume", OK_THREAD_ID]
    assert "thing broke" in legs.procs[1].communicate_calls[0]["input"]
    a1, a2 = rec.per_attempt
    assert (a1.input_tokens, a1.output_tokens, a1.cache_tokens) == (3199, 5, 12160)
    assert (a2.input_tokens, a2.output_tokens, a2.cache_tokens) == (3959, 5, 12160)
    assert rec.total_input_tokens == 7158 and rec.total_output_tokens == 10 and rec.total_cache_tokens == 24320
    p = pricing["gpt-6-astra"]
    assert rec.cost_usd == pytest.approx((7158 * p.input + 10 * p.output + 24320 * p.cache_read) / 1_000_000)
    assert rec.per_attempt[0].raw["auth_mode"] == CHATGPT_LINE and "auth_mode" not in rec.per_attempt[1].raw
    # the record round-trips to disk as plain JSON
    out = rec.write(tmp_path / "results")
    back = json.loads(out.read_text())
    assert back["per_attempt"][1]["raw"]["usage_source"] == "stream"
    assert back["per_attempt"][1]["raw"]["usage"] == _usage_line(OK_RESUME)


# --------------------------------------------------------------------------
# pre-flight guard: operator-level instructions are refused


def test_preflight_refuses_global_agents_md(monkeypatch, tmp_path, isolated_env):
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "AGENTS.md").write_text("Begin every reply with PINEAPPLE.\n")
    legs = _Legs([(OK_START, "", 0)])
    _install(monkeypatch, legs)
    with pytest.raises(HarnessError, match="AGENTS.md"):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "low"})
    assert legs.calls == []


@pytest.mark.parametrize("name", ["AGENTS.override.md", "instructions.md"])
def test_preflight_refuses_other_global_instruction_files(monkeypatch, tmp_path, isolated_env, name):
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / name).write_text("x\n")
    legs = _Legs([(OK_START, "", 0)])
    _install(monkeypatch, legs)
    with pytest.raises(HarnessError, match=name.replace(".", r"\.")):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "low"})
    assert legs.calls == []


def test_preflight_refuses_user_skills_but_allows_bundled_system_skills(monkeypatch, tmp_path, isolated_env):
    home = tmp_path / "codex-home"
    (home / "skills" / ".system" / "bundled").mkdir(parents=True)
    legs = _Legs([(OK_START, "", 0)])
    _install(monkeypatch, legs)
    CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "low"})  # .system only: fine
    (home / "skills" / "zzmarker").mkdir()
    with pytest.raises(HarnessError, match="zzmarker"):
        CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "low"})
    assert len(legs.calls) == 1


def test_dry_run_validates_the_two_row_matrix(monkeypatch, tmp_path):
    """The task's dry-run command: the committed eval-chess-backend matrix now
    carries both harnesses and both models must price."""
    from pagehub_benchmarks.config import load_benchmark
    from pagehub_benchmarks.runner.run import dry_run_report

    evals_repo = tmp_path / "pagehub-evals"  # stub bundle: runs in CI, which has no checkout
    (evals_repo / "fixtures").mkdir(parents=True)
    (evals_repo / "fixtures" / "eval-chess-backend.json").write_text(
        json.dumps({"version": 1, "collections": [{"name": "eval-chess-backend", "items": []}]})
    )
    monkeypatch.setenv("PAGEHUB_EVALS_REPO", str(evals_repo))
    text = "\n".join(dry_run_report(load_benchmark("eval-chess-backend")))
    assert "harness=claude-code" in text and "harness=codex-cli model=gpt-6-astra" in text


def test_gpt_6_astra_prices_are_pinned():
    """Standard tier, short context — developers.openai.com/api/docs/pricing, 2026-09-10."""
    from pagehub_benchmarks.config import load_pricing

    p = load_pricing()["gpt-6-astra"]
    assert (p.input, p.output, p.cache_write, p.cache_read) == (10.0, 50.0, 12.5, 1.0)



# --------------------------------------------------------------------------
# Parser rules that a mutation of _read_rollout / _thread_total would break
# (review round 3). All built from the REAL rollout lines.


def _rollout_lines() -> list[str]:
    return OK_ROLLOUT.splitlines()


def _write_rollout(tmp_path: Path, lines: list[str], thread_id: str = OK_THREAD_ID) -> Path:
    d = tmp_path / "codex-home" / "sessions" / "2026" / "09" / "11"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"rollout-2026-09-11T10-08-21-{thread_id}.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


def _with_thread_id(stream: str, thread_id: str) -> str:
    out = []
    for line in stream.splitlines():
        obj = json.loads(line)
        if obj.get("type") == "thread.started":
            obj["thread_id"] = thread_id
        out.append(json.dumps(obj, separators=(",", ":")))
    return "\n".join(out) + "\n"


def _last_turn_context_line() -> str:
    return [ln for ln in _rollout_lines() if json.loads(ln).get("type") == "turn_context"][-1]


def test_read_rollout_ignores_usage_from_earlier_turns():
    """A resumed turn that 401s gets a turn_context line appended to the
    rollout but no usage record. Its usage must read as NONE — not as the
    previous turn's — or a dead resume would be captured with stale tokens."""
    import tempfile as _tf

    lines = _rollout_lines() + [_last_turn_context_line()]  # SYNTHETIC tail: the dead turn's context line
    with _tf.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        fh.write("\n".join(lines) + "\n")
    turn, rate_limits = codex_cli._read_rollout(fh.name)
    os.unlink(fh.name)
    assert turn is None
    assert rate_limits is not None and rate_limits["plan_type"] == "plus"


def test_dead_resume_with_real_rollout_is_retried_then_raises(monkeypatch, tmp_path, isolated_env):
    """End to end: start leg OK; the resume 401s (real dead envelope, thread id
    rewritten to the thread under test) while the rollout on disk holds turn
    1–2 usage plus the dead turn's turn_context. Must be dead → retried →
    HarnessError, never a captured attempt carrying turn 2's tokens."""
    _write_rollout(tmp_path, _rollout_lines() + [_last_turn_context_line()])
    dead_resume = _with_thread_id(RESUME_FIXTURE, OK_THREAD_ID)
    legs = _Legs([(OK_START, "", 0), (dead_resume, "", 1)])
    _install(monkeypatch, legs)
    h = CodexCliHarness()
    r1 = h.start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "low"})
    with pytest.raises(HarnessError, match="no model activity"):
        h.continue_build(r1.session_handle, "fix it")
    assert len(legs.calls) == 1 + 1 + codex_cli.DEFAULT_DEAD_TURN_RETRIES


def test_thread_total_advances_after_rollout_sourced_failure(monkeypatch, tmp_path, isolated_env):
    """Leg 2 fails after model activity (usage from the rollout: turn 2).
    Leg 3's stream reports the new thread total; its delta must be exactly
    turn 3 — the failed turn must not be counted twice."""
    _write_rollout(tmp_path, _rollout_lines())
    failed_line = next(ln for ln in START_FIXTURE.splitlines() if '"turn.failed"' in ln)
    # SYNTHETIC: the real success envelope of the resume with turn.completed
    # swapped for the real turn.failed line.
    failed_resume = "\n".join(
        [ln for ln in OK_RESUME.splitlines() if '"turn.completed"' not in ln] + [failed_line]
    ) + "\n"
    total_after_turn2 = _usage_line(OK_RESUME)
    turn3 = {"input_tokens": 1000, "cached_input_tokens": 800, "cache_write_input_tokens": 0,
             "output_tokens": 7, "reasoning_output_tokens": 3}  # SYNTHETIC turn-3 increment
    total_after_turn3 = {k: total_after_turn2[k] + turn3[k] for k in turn3}
    ok_turn3 = _with_thread_id(OK_RESUME, OK_THREAD_ID).replace(
        json.dumps(_usage_line(OK_RESUME), separators=(",", ":")),
        json.dumps(total_after_turn3, separators=(",", ":")),
    )
    assert json.dumps(total_after_turn3, separators=(",", ":")) in ok_turn3
    legs = _Legs([(OK_START, "", 0), (failed_resume, "", 1), (ok_turn3, "", 0)])
    _install(monkeypatch, legs)
    h = CodexCliHarness()
    r1 = h.start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "low"})
    r2 = h.continue_build(r1.session_handle, "fix it")
    r3 = h.continue_build(r1.session_handle, "fix it again")
    turn2 = _rollout_turn_usages()[1]
    assert r2.raw["usage_source"] == "rollout" and r2.input_tokens == 16119 - 12160
    assert r3.raw["usage_source"] == "stream"
    assert r3.raw["usage_delta"] == turn3
    assert (r3.input_tokens, r3.cache_read_tokens, r3.output_tokens) == (200, 800, 7)
    # all three attempts sum to the thread total — nothing double- or under-counted
    assert r1.input_tokens + r1.cache_read_tokens + r2.input_tokens + r2.cache_read_tokens \
        + r3.input_tokens + r3.cache_read_tokens == total_after_turn3["input_tokens"]
    assert turn2["input_tokens"] == 16119


def test_read_rollout_takes_the_last_usage_record_of_the_turn():
    """A build turn makes many model requests; each token_usage_record carries
    the turn's RUNNING total, so the last one is the turn's usage."""
    import tempfile as _tf

    lines = _rollout_lines()
    idx = max(i for i, ln in enumerate(lines) if json.loads(ln).get("type") == "token_usage_record")
    real = json.loads(lines[idx])
    earlier = json.loads(lines[idx])  # SYNTHETIC: an earlier, smaller running total in the same turn
    earlier["payload"]["turn_token_usage"] = {
        k: (v // 2 if isinstance(v, int) else v) for k, v in real["payload"]["turn_token_usage"].items()
    }
    lines.insert(idx, json.dumps(earlier, separators=(",", ":")))
    with _tf.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        fh.write("\n".join(lines) + "\n")
    turn, _ = codex_cli._read_rollout(fh.name)
    os.unlink(fh.name)
    assert turn == real["payload"]["turn_token_usage"]


# --------------------------------------------------------------------------
# misc (review round 3 nits)


def test_resume_reporting_a_different_thread_raises(monkeypatch, tmp_path, isolated_env):
    other = _with_thread_id(OK_RESUME, "00000000-0000-0000-0000-000000000000")
    legs = _Legs([(OK_START, "", 0), (other, "", 0)])
    _install(monkeypatch, legs)
    h = CodexCliHarness()
    r1 = h.start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "low"})
    with pytest.raises(HarnessError, match="expected " + OK_THREAD_ID):
        h.continue_build(r1.session_handle, "again")


def test_cache_tokens_reported_false_when_codex_omits_the_cached_field(monkeypatch, tmp_path, isolated_env):
    # SYNTHETIC: a usage object without cached_input_tokens.
    no_cache = OK_START.replace('"cached_input_tokens":12160,', "")
    assert '"cached_input_tokens"' not in no_cache
    legs = _Legs([(no_cache, "", 0)])
    _install(monkeypatch, legs)
    r = CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "low"})
    assert r.raw["cache_tokens_reported"] is False
    assert r.cache_read_tokens == 0 and r.input_tokens == 15359


def test_rate_limits_are_trimmed_to_budget_fields(monkeypatch, tmp_path, isolated_env):
    _write_rollout(tmp_path, _rollout_lines())
    legs = _Legs([(OK_START, "", 0)])
    _install(monkeypatch, legs)
    r = CodexCliHarness().start_build(str(tmp_path), "p", "gpt-6-astra", {"effort": "low"})
    rl = r.raw["rate_limits"]
    assert set(rl) == {"plan_type", "primary", "secondary"}
    assert set(rl["primary"]) == {"used_percent", "window_minutes", "resets_at"}
    assert "credits" not in json.dumps(rl) and "balance" not in json.dumps(rl)
