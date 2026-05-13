"""Unit-level checks for the pure helpers in ``runner.push`` (no subprocess,
no real git)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pagehub_benchmarks.runner.push import (
    branch_for,
    github_https_url,
    github_owner_repo,
)


def test_branch_for_uses_refname_safe_timestamp():
    when = datetime(2026, 5, 12, 23, 48, 7, tzinfo=UTC)
    b = branch_for(
        harness="claude-code",
        model="claude-opus-4-7",
        config_slug="effort-xhigh",
        when=when,
    )
    # No ':' (illegal in refnames); slashes are fine.
    assert b == "bench/claude-code/claude-opus-4-7/effort-xhigh/2026-05-12T23-48-07Z"
    assert ":" not in b


@pytest.mark.parametrize(
    "remote, expected",
    [
        ("git@github.com:pagehub-io/eval-chess-backend.git", ("pagehub-io", "eval-chess-backend")),
        ("git@github.com:pagehub-io/eval-chess-backend", ("pagehub-io", "eval-chess-backend")),
        ("https://github.com/pagehub-io/eval-chess-backend.git", ("pagehub-io", "eval-chess-backend")),
        ("https://github.com/pagehub-io/eval-chess-backend", ("pagehub-io", "eval-chess-backend")),
        ("https://github.com/pagehub-io/eval-chess-backend/", ("pagehub-io", "eval-chess-backend")),
    ],
)
def test_github_owner_repo_handles_common_forms(remote, expected):
    assert github_owner_repo(remote) == expected


def test_github_owner_repo_returns_none_on_garbage():
    assert github_owner_repo("not-a-remote") is None
    assert github_owner_repo("") is None


def test_github_https_url_normalizes():
    assert (
        github_https_url("git@github.com:pagehub-io/eval-chess-backend.git")
        == "https://github.com/pagehub-io/eval-chess-backend"
    )
    assert (
        github_https_url("https://github.com/pagehub-io/eval-chess-backend/")
        == "https://github.com/pagehub-io/eval-chess-backend"
    )
