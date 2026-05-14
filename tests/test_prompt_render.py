"""Jinja2 prompt rendering: auto-vars, custom template_vars, error paths.

The fetcher is faked here — these tests don't hit pagehub-evals over HTTP.
The HTTP fetcher itself is tested separately in test_fixture_fetch.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pagehub_benchmarks.config import parse_benchmark
from pagehub_benchmarks.runner.fixture_fetch import FixtureFetchError
from pagehub_benchmarks.runner.prompt_render import PromptRenderError, render_prompt
from tests.fakes import FakeFixtureFetcher


def _spec(
    tmp_path: Path,
    *,
    prompt_body: str,
    template_vars: dict | None = None,
    name: str = "demo",
    env: dict | None = None,
):
    prompt = tmp_path / "prompts" / f"{name}.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(prompt_body)
    data = {
        "name": name,
        "description": "a demo benchmark",
        "target_repo": "git@github.com:example/demo.git",
        "target_start": "empty",
        "build_prompt_file": str(prompt),
        "grader": {
            "evals_base_url": "http://localhost:8002",
            "fixture_bundle": f"fixtures/{name}.json",
            "collection": f"{name}-rules",
            "env": env or {f"{name}_url": "http://localhost:9999"},
        },
        "max_attempts": 3,
        "harnesses": [{"harness": "fake", "model": "test-model", "config": {}}],
    }
    if template_vars is not None:
        data["template_vars"] = template_vars
    return parse_benchmark(data, tmp_path / "benchmarks" / f"{name}.yaml")


def test_renders_all_auto_vars(tmp_path):
    spec = _spec(
        tmp_path,
        prompt_body=(
            "name: {{ benchmark_name }}\n"
            "repo: {{ target_repo }}\n"
            "port: {{ target_port }}\n"
            "evals: {{ pagehub_evals_url }}\n"
            "fixture: {{ grader_fixture }}\n"
        ),
    )
    fetcher = FakeFixtureFetcher(body='{"k":1}')
    out = render_prompt(spec, fetcher=fetcher)

    assert "name: demo" in out.text
    assert "repo: git@github.com:example/demo.git" in out.text
    assert "port: 9999" in out.text  # demo_url port
    assert "evals: http://localhost:8002" in out.text
    assert "fixture: " in out.text and '{"k":1}' in out.text
    assert out.template_vars["benchmark_name"] == "demo"
    assert out.template_vars["grader_fixture"] == '{"k":1}'
    assert out.unused_vars == []
    assert fetcher.calls == [spec]  # fetched once


def test_custom_template_vars_substituted(tmp_path):
    spec = _spec(
        tmp_path,
        prompt_body="Hello {{ greeting }} {{ thing }}!",
        template_vars={"greeting": "hi", "thing": "world"},
    )
    out = render_prompt(spec, fetcher=FakeFixtureFetcher())
    assert out.text == "Hello hi world!"
    assert out.template_vars["greeting"] == "hi"
    assert out.unused_vars == []


def test_unused_custom_var_is_warned_not_an_error(tmp_path):
    spec = _spec(
        tmp_path,
        prompt_body="Hi {{ greeting }}!",
        template_vars={"greeting": "there", "unused": "yep"},
    )
    out = render_prompt(spec, fetcher=FakeFixtureFetcher())
    assert out.text == "Hi there!"
    assert out.unused_vars == ["unused"]


def test_undefined_var_raises_at_render_time(tmp_path):
    spec = _spec(tmp_path, prompt_body="Hello {{ no_such_var }}!")
    with pytest.raises(PromptRenderError) as exc:
        render_prompt(spec, fetcher=FakeFixtureFetcher())
    assert "no_such_var" in str(exc.value)


def test_template_vars_cannot_override_reserved_auto_vars(tmp_path):
    spec = _spec(
        tmp_path,
        prompt_body="{{ benchmark_name }}",
        template_vars={"benchmark_name": "should-not-win"},
    )
    with pytest.raises(PromptRenderError) as exc:
        render_prompt(spec, fetcher=FakeFixtureFetcher())
    assert "benchmark_name" in str(exc.value)
    assert "reserved" in str(exc.value).lower()


def test_template_syntax_error_is_render_error(tmp_path):
    # An unclosed `{%` block is a Jinja syntax error; surface it cleanly.
    spec = _spec(tmp_path, prompt_body="{% if foo %}\nno endif\n")
    with pytest.raises(PromptRenderError) as exc:
        render_prompt(spec, fetcher=FakeFixtureFetcher())
    assert "syntax" in str(exc.value).lower() or "line" in str(exc.value).lower()


def test_fixture_fetch_failure_propagates_through_renderer(tmp_path):
    spec = _spec(tmp_path, prompt_body="x = {{ grader_fixture }}")
    fetcher = FakeFixtureFetcher(raise_with=FixtureFetchError("http 500 dummy"))
    with pytest.raises(FixtureFetchError):
        render_prompt(spec, fetcher=fetcher)


def test_target_port_prefers_url_keyed_by_benchmark_name(tmp_path):
    spec = _spec(
        tmp_path,
        prompt_body="p={{ target_port }}",
        name="my-app",
        env={
            "pagehub-browser_url": "http://host:4010",
            "my-app_url": "http://host:8004",
            "other_url": "http://host:7777",
        },
    )
    out = render_prompt(spec, fetcher=FakeFixtureFetcher())
    assert out.text == "p=8004"


def test_target_port_falls_back_to_first_non_browser_url(tmp_path):
    # No URL keyed by the benchmark name; the first non-browser URL wins.
    spec = _spec(
        tmp_path,
        prompt_body="p={{ target_port }}",
        name="mismatch-name",
        env={
            "pagehub-browser_url": "http://host:4010",
            "other_url": "http://host:7777",
        },
    )
    out = render_prompt(spec, fetcher=FakeFixtureFetcher())
    assert out.text == "p=7777"


def test_target_port_empty_when_grader_env_has_no_urls(tmp_path):
    # env keys without _url suffix -> nothing to extract a port from.
    spec = _spec(
        tmp_path,
        prompt_body="p={{ target_port }}!",
        env={"some_key": "some-value"},
    )
    out = render_prompt(spec, fetcher=FakeFixtureFetcher())
    assert out.text == "p=!"


def test_grader_fixture_is_full_json_bundle_substituted_verbatim(tmp_path):
    # Real-shaped fixture body, pretty-printed by the fetcher (this test
    # uses a fake fetcher that returns the pretty body directly — the
    # pretty-print round-trip happens in HTTPFixtureFetcher; here we just
    # verify the renderer passes the body through unchanged).
    bundle = {
        "version": 1,
        "collections": [{"name": "demo-rules", "items": ["r1"]}],
        "requests": [{"name": "r1", "method": "GET", "url": "{{HOST}}/x"}],
    }
    pretty = json.dumps(bundle, indent=2)
    spec = _spec(tmp_path, prompt_body="<<\n{{ grader_fixture }}\n>>")
    out = render_prompt(spec, fetcher=FakeFixtureFetcher(body=pretty))
    assert pretty in out.text
    # Bracketing must be preserved.
    assert out.text.startswith("<<\n")
    assert out.text.endswith(">>")


def test_capture_per_attempt_rendered_prompt_in_run_record(tmp_path):
    """Smoke: integrate with execute_benchmark_run; AttemptRecords carry
    the full rendered build prompt on attempt 1 and the retry follow-up
    on attempts 2+."""
    from pagehub_benchmarks.runner.run import execute_benchmark_run
    from tests.conftest import TEST_PRICING
    from tests.fakes import FakeGrader, FakeHarness, ar, gr

    spec = _spec(
        tmp_path,
        prompt_body="Build {{ benchmark_name }} on port {{ target_port }}.",
    )
    harness = FakeHarness([ar(), ar(), ar()])
    grader = FakeGrader([gr(False, ["A failed"]), gr(False, ["B failed"]), gr(True)])
    fetcher = FakeFixtureFetcher()
    rec = execute_benchmark_run(
        spec=spec,
        harness_spec=spec.harnesses[0],
        harness=harness,
        grader=grader,
        worktree_dir=tmp_path / "wt",
        pricing=TEST_PRICING,
        fixture_fetcher=fetcher,
        clock=lambda: __import__("datetime").datetime(2026, 5, 12, 16, 30, tzinfo=__import__("datetime").timezone.utc),
    )
    assert rec.passed is True
    assert rec.attempts == 3
    # Run-level snapshot: full build prompt + the template_vars map.
    assert rec.rendered_prompt == "Build demo on port 9999."
    assert rec.template_vars["benchmark_name"] == "demo"
    assert rec.template_vars["grader_fixture"]  # non-empty
    # Per-attempt: attempt 1 is the build prompt, 2+ are the retry follow-up.
    assert rec.per_attempt[0].rendered_prompt == "Build demo on port 9999."
    assert "A failed" in rec.per_attempt[1].rendered_prompt
    assert "B failed" in rec.per_attempt[2].rendered_prompt
    # The harness's actual sent prompts mirror per-attempt rendered_prompt.
    assert harness.calls[0][2] == rec.per_attempt[0].rendered_prompt
    assert harness.calls[1][2] == rec.per_attempt[1].rendered_prompt
    assert harness.calls[2][2] == rec.per_attempt[2].rendered_prompt
