"""Shared fixtures: a self-contained benchmark spec + pricing for runner tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pagehub_benchmarks.config import ModelPrice, parse_benchmark

TEST_MODEL = "test-model"
TEST_PRICING = {
    # Round numbers so cost math is exact: $10 / $20 / $5 / $1 per 1M tokens.
    TEST_MODEL: ModelPrice(input=10.0, output=20.0, cache_write=5.0, cache_read=1.0),
}


@pytest.fixture
def fixed_clock():
    """A clock that returns a fixed UTC instant (so started/finished are stable)."""
    return lambda: datetime(2026, 5, 12, 16, 30, 0, tzinfo=UTC)


@pytest.fixture
def bench_spec(tmp_path: Path):
    """A BenchmarkSpec with a real prompt file under tmp_path, max_attempts=3."""
    prompt = tmp_path / "prompts" / "demo.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("Build the demo. Get the tests passing — that is all.\n")
    data = {
        "name": "demo",
        "description": "a demo benchmark",
        "target_repo": "git@github.com:example/demo.git",
        "target_start": "empty",
        "build_prompt_file": str(prompt),
        "grader": {
            "evals_base_url": "http://localhost:8002",
            "fixture_bundle": "fixtures/demo.json",
            "collection": "demo-rules",
            "env": {"demo_url": "http://localhost:9999"},
        },
        "max_attempts": 3,
        "harnesses": [{"harness": "fake", "model": TEST_MODEL, "config": {"effort": "xhigh"}}],
    }
    return parse_benchmark(data, tmp_path / "benchmarks" / "demo.yaml")
