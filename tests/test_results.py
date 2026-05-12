from __future__ import annotations

from datetime import UTC, datetime

from pagehub_benchmarks.runner.results import config_slug, result_filename


def test_config_slug():
    assert config_slug(None) == "default"
    assert config_slug({}) == "default"
    assert config_slug({"effort": "xhigh"}) == "effort-xhigh"
    # keys are sorted; non-alnum collapses to '-'
    assert config_slug({"temperature": 0.7, "effort": "xhigh"}) == "effort-xhigh_temperature-0.7"
    assert config_slug({"weird key!": "a/b c"}) == "weird-key-a-b-c"


def test_result_filename():
    when = datetime(2026, 5, 12, 16, 30, 5, tzinfo=UTC)
    name = result_filename("claude-code", "claude-opus-4-7", {"effort": "xhigh"}, when)
    assert name == "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-05Z.json"
    # naive/other-tz input is normalized to UTC
    when2 = datetime(2026, 1, 1, 0, 0, 0)
    name2 = result_filename("h", "m", {}, when2)
    assert name2.endswith("__default__2026-01-01T00-00-00Z.json")
