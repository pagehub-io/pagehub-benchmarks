"""The --dry-run path: validate YAML + prompt + grader fixture + pricing, offline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pagehub_benchmarks.config import ConfigError, parse_benchmark
from pagehub_benchmarks.runner.run import dry_run_report

_MINIMAL_BUNDLE = {
    "version": 1,
    "environments": [{"name": "demo-local", "variables": {"demo_url": "http://localhost:9999"}, "secrets": {}}],
    "requests": [{"name": "r", "method": "GET", "url": "{{demo_url}}/health",
                  "evaluations": [{"name": "ok", "kind": "status_eq", "config": {"expected": 200}}]}],
    "collections": [{"name": "demo-rules", "items": ["r"]}],
}


def _spec(tmp_path: Path, *, collection="demo-rules", bundle=_MINIMAL_BUNDLE):
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "demo.json").write_text(json.dumps(bundle))
    prompt = tmp_path / "demo.md"
    prompt.write_text("Build the demo. Get the tests passing — that is all.\n")
    data = {
        "name": "demo",
        "description": "demo",
        "target_repo": "git@github.com:e/demo.git",
        "build_prompt_file": str(prompt),
        "grader": {"fixture_bundle": "fixtures/demo.json", "collection": collection, "env": {"demo_url": "http://localhost:9999"}},
        "max_attempts": 3,
        "harnesses": [{"harness": "claude-code", "model": "claude-opus-4-7", "config": {"effort": "xhigh"}}],
    }
    return parse_benchmark(data, tmp_path / "demo.yaml")


def test_dry_run_ok(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PAGEHUB_EVALS_REPO", str(tmp_path))
    notes = dry_run_report(_spec(tmp_path))
    text = "\n".join(notes)
    assert "demo-rules" in text
    assert "claude-opus-4-7" in text
    assert "OK" in text


def test_dry_run_missing_bundle(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PAGEHUB_EVALS_REPO", str(tmp_path / "nonexistent"))
    # build a spec whose fixture path won't exist
    (tmp_path / "fixtures").mkdir()
    prompt = tmp_path / "demo.md"
    prompt.write_text("Build it. That is all.\n")
    data = {
        "name": "demo", "target_repo": "git@github.com:e/d.git", "build_prompt_file": str(prompt),
        "grader": {"fixture_bundle": "fixtures/demo.json", "collection": "demo-rules"},
        "harnesses": [{"harness": "claude-code", "model": "claude-opus-4-7"}],
    }
    spec = parse_benchmark(data, tmp_path / "d.yaml")
    with pytest.raises(ConfigError):
        dry_run_report(spec)


def test_dry_run_collection_not_in_bundle(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PAGEHUB_EVALS_REPO", str(tmp_path))
    with pytest.raises(ConfigError):
        dry_run_report(_spec(tmp_path, collection="not-there"))


def test_dry_run_rejects_non_v1_bundle(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PAGEHUB_EVALS_REPO", str(tmp_path))
    bad = dict(_MINIMAL_BUNDLE)
    bad["version"] = 2
    with pytest.raises(ConfigError):
        dry_run_report(_spec(tmp_path, bundle=bad))


def test_dry_run_unknown_model(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PAGEHUB_EVALS_REPO", str(tmp_path))
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "demo.json").write_text(json.dumps(_MINIMAL_BUNDLE))
    prompt = tmp_path / "demo.md"
    prompt.write_text("Build it. That is all.\n")
    data = {
        "name": "demo", "target_repo": "git@github.com:e/d.git", "build_prompt_file": str(prompt),
        "grader": {"fixture_bundle": "fixtures/demo.json", "collection": "demo-rules"},
        "harnesses": [{"harness": "claude-code", "model": "no-such-model"}],
    }
    spec = parse_benchmark(data, tmp_path / "d.yaml")
    with pytest.raises(ConfigError):
        dry_run_report(spec)
