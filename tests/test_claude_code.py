from __future__ import annotations

import json

import pytest

from pagehub_benchmarks.harnesses.claude_code import (
    HarnessError,
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


def test_usage_from():
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
