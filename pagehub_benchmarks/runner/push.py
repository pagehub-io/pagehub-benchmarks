"""Push the built worktree to the target repo as a per-run branch.

Every run gets a ``bench/<harness>/<model>/<config-slug>/<ISO8601>`` branch on
the target. If the run passed AND the target's default branch is empty (no
refs at all), the same tree is also pushed to the default branch, giving every
fresh target a canonical "first passing build" reference while later runs
accumulate as branches without fighting over the default.

The push is a side effect of the run, never a gate: failures are recorded into
the run record (``push_error``) and the run still reports its grader verdict.

``Pusher`` is a Protocol so tests can inject a :class:`FakePusher`; ``GitPusher``
is the real implementation that shells out to ``git push`` + ``gh api``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _branch_timestamp(dt: datetime) -> str:
    # Git refnames can't contain ':' — mirror the result-filename timestamp.
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")


def branch_for(
    *, harness: str, model: str, config_slug: str, when: datetime
) -> str:
    """``bench/<harness>/<model>/<config-slug>/<refname-safe ISO8601>``."""
    return f"bench/{harness}/{model}/{config_slug}/{_branch_timestamp(when)}"


_SSH_RE = re.compile(r"^git@github\.com:(.+?)(?:\.git)?$")
_HTTPS_RE = re.compile(r"^https://github\.com/(.+?)/?$")


def github_owner_repo(remote: str) -> tuple[str, str] | None:
    """``git@github.com:org/repo.git`` / ``https://...`` -> ``('org', 'repo')``."""
    s = (remote or "").strip()
    for rx in (_SSH_RE, _HTTPS_RE):
        m = rx.match(s)
        if m:
            owner_repo = m.group(1).rstrip("/")
            if owner_repo.endswith(".git"):
                owner_repo = owner_repo[:-4]
            if "/" in owner_repo:
                owner, repo = owner_repo.split("/", 1)
                return owner, repo
    return None


def github_https_url(remote: str) -> str:
    pair = github_owner_repo(remote)
    if pair:
        return f"https://github.com/{pair[0]}/{pair[1]}"
    return (remote or "").rstrip("/")


@dataclass
class PushResult:
    pushed_branch: str | None = None
    pushed_branch_url: str | None = None
    pushed_commit: str | None = None
    pushed_to_default_branch: bool = False
    pushed_at: str | None = None
    error: str | None = None


class Pusher(Protocol):
    def is_target_empty(self, target_repo: str) -> bool: ...

    def push(
        self,
        *,
        worktree: Path,
        target_repo: str,
        branch: str,
        push_to_default_branch: bool,
    ) -> PushResult: ...


class GitPusher:
    """Real pusher: shells out to ``git push`` and uses ``gh api`` (when
    available) to discover the default branch on empty targets."""

    def __init__(self, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._clock = clock

    def is_target_empty(self, target_repo: str) -> bool:
        """True iff the remote has no branch refs (a brand-new repo)."""
        cp = subprocess.run(  # noqa: S603
            ["git", "ls-remote", "--heads", target_repo],
            capture_output=True,
            text=True,
            check=False,
        )
        if cp.returncode != 0:
            return False
        return cp.stdout.strip() == ""

    def push(
        self,
        *,
        worktree: Path,
        target_repo: str,
        branch: str,
        push_to_default_branch: bool,
    ) -> PushResult:
        worktree = Path(worktree)
        result = PushResult()

        head_cp = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "HEAD"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            check=False,
        )
        if head_cp.returncode != 0:
            result.error = (
                f"could not read HEAD in {worktree}: "
                + (head_cp.stderr or "").strip()
            )
            return result
        result.pushed_commit = head_cp.stdout.strip()

        push_cp = self._git_push(worktree, target_repo, f"HEAD:refs/heads/{branch}")
        if push_cp.returncode != 0:
            result.error = self._fmt_push_err(push_cp, branch)
            return result
        result.pushed_branch = branch
        result.pushed_branch_url = f"{github_https_url(target_repo)}/tree/{branch}"
        result.pushed_at = _iso(self._clock())

        if push_to_default_branch:
            default_branch = self._discover_default_branch(target_repo)
            cp = self._git_push(
                worktree, target_repo, f"HEAD:refs/heads/{default_branch}"
            )
            if cp.returncode == 0:
                result.pushed_to_default_branch = True
            else:
                result.error = self._fmt_push_err(cp, default_branch)
        return result

    def _git_push(
        self, worktree: Path, target_repo: str, refspec: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ["git", "push", target_repo, refspec],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            check=False,
        )

    def _fmt_push_err(
        self, cp: subprocess.CompletedProcess[str], branch: str
    ) -> str:
        msg = (cp.stderr or cp.stdout or "").strip()
        return f"push to {branch!r} failed (rc={cp.returncode}): {msg}"

    def _discover_default_branch(self, target_repo: str) -> str:
        if shutil.which("gh"):
            pair = github_owner_repo(target_repo)
            if pair:
                cp = subprocess.run(  # noqa: S603
                    [
                        "gh",
                        "api",
                        f"repos/{pair[0]}/{pair[1]}",
                        "--jq",
                        ".default_branch",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                name = (cp.stdout or "").strip()
                if cp.returncode == 0 and name:
                    return name
        # Fallback: git protocol can surface the default branch only if HEAD
        # already has a symref target (won't on truly-empty repos).
        cp = subprocess.run(  # noqa: S603
            ["git", "ls-remote", "--symref", target_repo, "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if cp.returncode == 0:
            for line in cp.stdout.splitlines():
                if line.startswith("ref:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].startswith("refs/heads/"):
                        return parts[1][len("refs/heads/") :]
        return "main"


__all__ = [
    "Pusher",
    "GitPusher",
    "PushResult",
    "branch_for",
    "github_owner_repo",
    "github_https_url",
]
