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


def test_loads_shipped_chess_backend_benchmark():
    spec = load_benchmark("chess-backend")
    assert spec.name == "chess-backend"
    assert spec.target_repo == "git@github.com:pagehub-io/eval-chess-backend.git"
    assert spec.target_start == "empty"
    assert spec.max_attempts == 5
    assert spec.build_prompt_file == "prompts/chess-backend.md"
    assert spec.grader.collection == "chess-rules"
    assert spec.grader.fixture_bundle == "fixtures/chess-rules.json"
    assert spec.grader.env == {"eval-chess-backend_url": "http://localhost:8003"}
    assert len(spec.harnesses) == 1
    h = spec.harnesses[0]
    assert h.harness == "claude-code"
    assert h.model == "claude-opus-4-7"
    assert h.config == {"effort": "xhigh"}
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
    assert spec.grader.evals_base_url == "http://localhost:4002"  # default
    assert spec.max_attempts == 5  # default
    assert spec.target_start == "empty"  # default
    assert spec.grader.fixture_bundle_path == tmp_path / "fixtures" / "x.json"


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


def test_load_pricing_rejects_incomplete_model(tmp_path):
    p = tmp_path / "pricing.yaml"
    p.write_text("models:\n  m:\n    input: 1.0\n    output: 2.0\n")  # missing cache rates
    with pytest.raises(ConfigError):
        load_pricing(p)
