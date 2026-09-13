from __future__ import annotations

from pathlib import Path

import pytest

from pagehub_benchmarks.config import (
    ConfigError,
    load_benchmark,
    load_pricing,
    parse_benchmark,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_loads_shipped_eval_chess_backend_benchmark():
    spec = load_benchmark("eval-chess-backend")
    assert spec.name == "eval-chess-backend"
    assert spec.target_repo == "git@github.com:pagehub-io/eval-chess-backend.git"
    assert spec.target_start == "empty"
    assert spec.max_attempts == 5
    assert spec.build_prompt_file == "prompts/eval-chess-backend.md"
    assert spec.grader.collection == "eval-chess-backend"
    assert spec.grader.fixture_bundle == "fixtures/eval-chess-backend.json"
    assert spec.grader.env == {"eval-chess-backend_url": "http://host.docker.internal:8003"}
    assert len(spec.harnesses) == 2  # claude-code + codex-cli rows
    h = spec.harnesses[0]
    assert h.harness == "claude-code"
    assert h.model == "claude-opus-4-7"
    assert h.config == {"effort": "xhigh"}
    h2 = spec.harnesses[1]
    assert h2.harness == "codex-cli"
    assert h2.model == "gpt-6-astra"
    assert h2.config == {"effort": "high"}
    # the prompt file exists, is non-empty, and does not mention PRs / git push
    text = spec.read_prompt()
    assert text and "that is all" in text
    assert "pull request" not in text.lower() and "git push" not in text.lower()


def test_grader_defaults_and_fixture_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PAGEHUB_EVALS_REPO", str(tmp_path))
    data = {
        "name": "x",
        "target_repo": "git@github.com:e/x.git",
        "build_prompt_file": "prompts/x.md",
        "grader": {"fixture_bundle": "fixtures/x.json", "collection": "x-rules"},
        "harnesses": [{"harness": "claude-code", "model": "claude-opus-4-7"}],
    }
    spec = parse_benchmark(data, tmp_path / "x.yaml")
    assert spec.grader.evals_base_url == "http://localhost:8002"  # default
    assert spec.max_attempts == 5  # default
    assert spec.target_start == "empty"  # default
    assert spec.grader.fixture_bundle_path == tmp_path / "fixtures" / "x.json"
    # DOM readiness probe is opt-in — absent when not declared.
    assert spec.grader.ready_testid is None
    assert spec.grader.ready_timeout_seconds == 60.0


def test_grader_ready_testid_round_trips(tmp_path):
    data = {
        "name": "x",
        "target_repo": "git@github.com:e/x.git",
        "build_prompt_file": "prompts/x.md",
        "grader": {
            "fixture_bundle": "fixtures/x.json",
            "collection": "x-rules",
            "ready_testid": "board",
            "ready_timeout_seconds": 30,
        },
        "harnesses": [{"harness": "claude-code", "model": "claude-opus-4-7"}],
    }
    spec = parse_benchmark(data, tmp_path / "x.yaml")
    assert spec.grader.ready_testid == "board"
    assert spec.grader.ready_timeout_seconds == 30.0


def test_grader_empty_ready_testid_normalizes_to_none(tmp_path):
    # An empty-string ready_testid is the same as omitting it — easier to land
    # in YAML when authors quote the field but mean "no probe".
    data = {
        "name": "x",
        "target_repo": "git@github.com:e/x.git",
        "build_prompt_file": "prompts/x.md",
        "grader": {
            "fixture_bundle": "fixtures/x.json",
            "collection": "x-rules",
            "ready_testid": "  ",
        },
        "harnesses": [{"harness": "claude-code", "model": "claude-opus-4-7"}],
    }
    spec = parse_benchmark(data, tmp_path / "x.yaml")
    assert spec.grader.ready_testid is None


def test_grader_invalid_ready_testid_type_raises(tmp_path):
    data = {
        "name": "x",
        "target_repo": "git@github.com:e/x.git",
        "build_prompt_file": "prompts/x.md",
        "grader": {
            "fixture_bundle": "fixtures/x.json",
            "collection": "x-rules",
            "ready_testid": ["not", "a", "string"],
        },
        "harnesses": [{"harness": "claude-code", "model": "claude-opus-4-7"}],
    }
    with pytest.raises(ConfigError, match="ready_testid"):
        parse_benchmark(data, tmp_path / "x.yaml")


def test_grader_invalid_ready_timeout_raises(tmp_path):
    data = {
        "name": "x",
        "target_repo": "git@github.com:e/x.git",
        "build_prompt_file": "prompts/x.md",
        "grader": {
            "fixture_bundle": "fixtures/x.json",
            "collection": "x-rules",
            "ready_timeout_seconds": -5,
        },
        "harnesses": [{"harness": "claude-code", "model": "claude-opus-4-7"}],
    }
    with pytest.raises(ConfigError, match="ready_timeout_seconds"):
        parse_benchmark(data, tmp_path / "x.yaml")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("name"),
        lambda d: d.pop("target_repo"),
        lambda d: d.pop("grader"),
        lambda d: d.__setitem__("harnesses", []),
        lambda d: d["grader"].pop("collection"),
        lambda d: d.__setitem__("max_attempts", 0),
        lambda d: d["harnesses"][0].pop("model"),
    ],
)
def test_malformed_benchmark_rejected(mutate, tmp_path):
    data = {
        "name": "x",
        "target_repo": "git@github.com:e/x.git",
        "build_prompt_file": "prompts/x.md",
        "grader": {"fixture_bundle": "fixtures/x.json", "collection": "x-rules"},
        "max_attempts": 5,
        "harnesses": [{"harness": "claude-code", "model": "claude-opus-4-7"}],
    }
    mutate(data)
    with pytest.raises(ConfigError):
        parse_benchmark(data, tmp_path / "x.yaml")


def test_template_vars_default_empty_and_optional(tmp_path):
    data = {
        "name": "x",
        "target_repo": "git@github.com:e/x.git",
        "build_prompt_file": "prompts/x.md",
        "grader": {"fixture_bundle": "fixtures/x.json", "collection": "x-rules"},
        "harnesses": [{"harness": "claude-code", "model": "claude-opus-4-7"}],
    }
    spec = parse_benchmark(data, tmp_path / "x.yaml")
    assert spec.template_vars == {}


def test_template_vars_parsed_from_yaml(tmp_path):
    data = {
        "name": "x",
        "target_repo": "git@github.com:e/x.git",
        "build_prompt_file": "prompts/x.md",
        "grader": {"fixture_bundle": "fixtures/x.json", "collection": "x-rules"},
        "harnesses": [{"harness": "claude-code", "model": "claude-opus-4-7"}],
        "template_vars": {"foo": "bar", "n": 42},
    }
    spec = parse_benchmark(data, tmp_path / "x.yaml")
    assert spec.template_vars == {"foo": "bar", "n": "42"}  # values stringified


def test_template_vars_must_be_mapping(tmp_path):
    data = {
        "name": "x",
        "target_repo": "git@github.com:e/x.git",
        "build_prompt_file": "prompts/x.md",
        "grader": {"fixture_bundle": "fixtures/x.json", "collection": "x-rules"},
        "harnesses": [{"harness": "claude-code", "model": "claude-opus-4-7"}],
        "template_vars": ["not", "a", "mapping"],
    }
    with pytest.raises(ConfigError):
        parse_benchmark(data, tmp_path / "x.yaml")


def test_load_pricing_rejects_missing_required_input_or_output(tmp_path):
    # input/output are required (every provider charges for those).
    p = tmp_path / "pricing.yaml"
    p.write_text("models:\n  m:\n    input: 1.0\n")  # missing 'output'
    with pytest.raises(ConfigError):
        load_pricing(p)


def test_load_pricing_treats_missing_cache_rates_as_zero(tmp_path):
    # Providers without a prompt-cache concept (xAI/Grok) omit the cache
    # fields entirely; null/absent => 0.0, the cost computation contributes
    # nothing for cache tokens. (Anthropic + OpenAI still set them.)
    p = tmp_path / "pricing.yaml"
    p.write_text("models:\n  grok-4:\n    input: 3.0\n    output: 15.0\n")
    table = load_pricing(p)
    assert table["grok-4"].input == 3.0
    assert table["grok-4"].output == 15.0
    assert table["grok-4"].cache_write == 0.0
    assert table["grok-4"].cache_read == 0.0


def test_load_pricing_accepts_null_cache_rates(tmp_path):
    # Explicit null is the documented "no prompt-cache" signal.
    p = tmp_path / "pricing.yaml"
    p.write_text(
        "models:\n  grok-4:\n"
        "    input: 3.0\n    output: 15.0\n"
        "    cache_write: null\n    cache_read: null\n"
    )
    table = load_pricing(p)
    assert table["grok-4"].cache_write == 0.0
    assert table["grok-4"].cache_read == 0.0
