from __future__ import annotations

import json
from typing import Any

import pytest

from pagehub_benchmarks.harnesses import claude_code
from pagehub_benchmarks.harnesses.claude_code import (
    ClaudeCodeHarness,
    HarnessError,
    _gateway_env_overlay,
    _parse_cli_json,
    _subprocess_env,
    _usage_from,
)


def test_parse_cli_json_plain():
    obj = {"type": "result", "session_id": "abc", "result": "done", "usage": {"input_tokens": 5}}
    assert _parse_cli_json(json.dumps(obj)) == obj


def test_parse_cli_json_falls_back_to_last_json_line():
    noise = "starting up...\nsome log line\n" + json.dumps({"session_id": "z", "usage": {}})
    assert _parse_cli_json(noise)["session_id"] == "z"


def test_parse_cli_json_empty_raises():
    with pytest.raises(HarnessError):
        _parse_cli_json("   ")


def test_usage_from_legacy_usage_shape():
    # Older CLI builds (and gateway-routed responses) report only the
    # top-level snake_case ``usage`` block.
    data = {
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_creation_input_tokens": 300,
            "cache_read_input_tokens": 400,
        }
    }
    assert _usage_from(data) == (100, 20, 300, 400)
    # missing usage -> all zeros
    assert _usage_from({}) == (0, 0, 0, 0)


def test_usage_from_sums_modelUsage_when_present():
    # Newer CLI builds dispatch sub-tasks to a routing agent (e.g. Haiku 4.5
    # invoked from within an Opus 4.7 run). The top-level ``usage`` field
    # captures only the primary model's slice; ``modelUsage`` carries the
    # complete per-sub-model breakdown. We sum across all entries so the
    # sub-agent's tokens are visible to cost computation. (Real shape from a
    # 2026-05-22 Opus 4.7 smoke; numbers shrunk for the test.)
    data = {
        "usage": {  # primary-only slice — would under-count
            "input_tokens": 6,
            "output_tokens": 6,
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 200,
        },
        "modelUsage": {
            "claude-haiku-4-5-20251001": {
                "inputTokens": 440,
                "outputTokens": 12,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
            },
            "claude-opus-4-7": {
                "inputTokens": 6,
                "outputTokens": 6,
                "cacheReadInputTokens": 200,
                "cacheCreationInputTokens": 100,
            },
        },
    }
    assert _usage_from(data) == (446, 18, 100, 200)


def test_usage_from_modelUsage_takes_precedence_over_top_level_usage():
    # The two are not redundant — modelUsage is the complete picture, top-level
    # ``usage`` is only the primary model's slice. When both are present, sum
    # the modelUsage entries.
    data = {
        "usage": {
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        "modelUsage": {
            "claude-opus-4-7": {
                "inputTokens": 50,
                "outputTokens": 25,
                "cacheReadInputTokens": 10,
                "cacheCreationInputTokens": 5,
            },
        },
    }
    assert _usage_from(data) == (50, 25, 5, 10)


def test_usage_from_empty_modelUsage_falls_back_to_top_level():
    # An empty modelUsage dict shouldn't suppress the top-level usage —
    # otherwise a malformed response could zero out a run silently.
    data = {
        "usage": {
            "input_tokens": 7,
            "output_tokens": 3,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        "modelUsage": {},
    }
    assert _usage_from(data) == (7, 3, 0, 0)


def test_usage_from_skips_non_dict_modelUsage_entries():
    # Tolerant of garbage values in modelUsage entries (defensive: a future
    # CLI shape change shouldn't crash us).
    data = {
        "modelUsage": {
            "claude-opus-4-7": {"inputTokens": 10, "outputTokens": 5},
            "garbage-entry": "not a dict",
        },
    }
    assert _usage_from(data) == (10, 5, 0, 0)


def test_subprocess_env_strips_anthropic_api_key(monkeypatch):
    # The CLI must run on its subscription auth — a stray API key would divert
    # the run onto metered billing, so the adapter removes it.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-dropped")
    monkeypatch.setenv("PATH", "/usr/bin")  # an unrelated var that must survive
    env = _subprocess_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert env["PATH"] == "/usr/bin"
    # also fine when it wasn't set
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert "ANTHROPIC_API_KEY" not in _subprocess_env()


def test_subprocess_env_still_strips_api_key_when_overlay_provided(monkeypatch):
    # Gateway path must NOT inherit a stray ANTHROPIC_API_KEY — we use
    # AUTH_TOKEN, and an API key would bypass the gateway by hitting the
    # native Anthropic endpoint with subscription auth.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-dropped")
    env = _subprocess_env({"ANTHROPIC_BASE_URL": "http://gw", "ANTHROPIC_AUTH_TOKEN": "tk"})
    assert "ANTHROPIC_API_KEY" not in env
    assert env["ANTHROPIC_BASE_URL"] == "http://gw"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "tk"


# --------------------------------------------------------------------------
# gateway env overlay
#
# The overlay is the surgical thing: when a benchmark's harness config carries
# a ``gateway: {url, auth_token_env}`` block, the subprocess env that drives
# ``claude -p`` must set ``ANTHROPIC_BASE_URL`` + ``ANTHROPIC_AUTH_TOKEN`` (and
# nothing else). When the block is absent, the env must be untouched —
# behavior identical to the pre-gateway codebase.


def test_gateway_overlay_absent_when_no_block():
    assert _gateway_env_overlay(None) == {}
    assert _gateway_env_overlay({}) == {}
    assert _gateway_env_overlay({"effort": "xhigh"}) == {}


def test_gateway_overlay_reads_token_from_env(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH_TOKEN", "tk-secret")
    overlay = _gateway_env_overlay({"gateway": {"url": "http://localhost:4011"}})
    assert overlay == {
        "ANTHROPIC_BASE_URL": "http://localhost:4011",
        "ANTHROPIC_AUTH_TOKEN": "tk-secret",
    }


def test_gateway_overlay_honors_custom_auth_env_var(monkeypatch):
    monkeypatch.delenv("GATEWAY_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("CUSTOM_GATEWAY_KEY", "tk-custom")
    overlay = _gateway_env_overlay(
        {"gateway": {"url": "http://gw:4011", "auth_token_env": "CUSTOM_GATEWAY_KEY"}}
    )
    assert overlay["ANTHROPIC_AUTH_TOKEN"] == "tk-custom"


def test_gateway_overlay_missing_url_raises():
    with pytest.raises(HarnessError, match="missing required 'url'"):
        _gateway_env_overlay({"gateway": {}})


def test_gateway_overlay_token_env_unset_raises(monkeypatch):
    monkeypatch.delenv("GATEWAY_AUTH_TOKEN", raising=False)
    with pytest.raises(HarnessError, match="GATEWAY_AUTH_TOKEN"):
        _gateway_env_overlay({"gateway": {"url": "http://gw"}})


def test_gateway_overlay_block_must_be_mapping():
    with pytest.raises(HarnessError, match="must be a mapping"):
        _gateway_env_overlay({"gateway": "http://gw"})


# --------------------------------------------------------------------------
# end-to-end through ClaudeCodeHarness, with subprocess.run faked. This is the
# closest we get to a "FakeHarness exercises the new gateway code path" test
# without forking a real claude CLI.


class _FakeCompletedProcess:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


def _install_fake_subprocess(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch ``subprocess.run`` inside the harness module so we can intercept
    the env dict and the command. Returns a dict captured-by-reference so the
    caller can assert on what was actually passed."""
    captured: dict[str, Any] = {}

    def fake_run(cmd, *, cwd, env, capture_output, text, timeout):  # noqa: ANN001
        captured["cmd"] = list(cmd)
        captured["cwd"] = cwd
        captured["env"] = dict(env)
        captured["timeout"] = timeout
        return _FakeCompletedProcess(
            stdout=json.dumps(
                {
                    "type": "result",
                    "session_id": "sess-fake",
                    "result": "ok",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                }
            )
        )

    monkeypatch.setattr(claude_code.subprocess, "run", fake_run)
    return captured


def test_start_build_without_gateway_does_not_set_gateway_env(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    captured = _install_fake_subprocess(monkeypatch)

    h = ClaudeCodeHarness()
    h.start_build(str(tmp_path), "build a thing", "claude-opus-4-7", {"effort": "xhigh"})

    # neither gateway var present
    assert "ANTHROPIC_BASE_URL" not in captured["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in captured["env"]
    # --model was passed through verbatim, --effort was forwarded
    assert "--model" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "claude-opus-4-7"
    assert "--effort" in captured["cmd"]


def test_start_build_with_gateway_sets_base_url_and_auth_token(monkeypatch, tmp_path):
    monkeypatch.setenv("GATEWAY_AUTH_TOKEN", "tk-secret")
    captured = _install_fake_subprocess(monkeypatch)

    h = ClaudeCodeHarness()
    h.start_build(
        str(tmp_path),
        "build a thing",
        "gpt-5-pro",  # passed straight through — the gateway routes on prefix
        {"gateway": {"url": "http://localhost:4011"}},
    )

    assert captured["env"]["ANTHROPIC_BASE_URL"] == "http://localhost:4011"
    assert captured["env"]["ANTHROPIC_AUTH_TOKEN"] == "tk-secret"
    # --model arg is whatever the YAML said, NOT normalized to claude-*
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "gpt-5-pro"


def test_continue_build_reuses_gateway_overlay_from_start_build(monkeypatch, tmp_path):
    """The gateway overlay is sticky for the run: continue_build does not
    receive ``config`` (the runner only hands it a session handle + prompt),
    so the harness must remember the overlay from start_build and apply it
    again. Otherwise the second leg would silently fall off the gateway and
    onto the CLI's subscription auth."""
    monkeypatch.setenv("GATEWAY_AUTH_TOKEN", "tk-secret")
    captured = _install_fake_subprocess(monkeypatch)

    h = ClaudeCodeHarness()
    h.start_build(str(tmp_path), "p1", "gpt-5-pro", {"gateway": {"url": "http://gw:4011"}})
    captured.clear()
    h.continue_build("sess-fake", "p2")

    assert captured["env"]["ANTHROPIC_BASE_URL"] == "http://gw:4011"
    assert captured["env"]["ANTHROPIC_AUTH_TOKEN"] == "tk-secret"
    assert "ANTHROPIC_API_KEY" not in captured["env"]


def test_start_build_with_malformed_gateway_block_raises(monkeypatch, tmp_path):
    # Don't even reach the subprocess if the config is broken — fail loudly.
    monkeypatch.delenv("GATEWAY_AUTH_TOKEN", raising=False)
    captured = _install_fake_subprocess(monkeypatch)
    h = ClaudeCodeHarness()
    with pytest.raises(HarnessError):
        h.start_build(str(tmp_path), "p", "gpt-5", {"gateway": {"url": "http://gw"}})
    assert "cmd" not in captured  # subprocess.run was never called
