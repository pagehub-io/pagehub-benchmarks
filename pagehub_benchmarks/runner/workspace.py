"""Worktree preparation + (best-effort) running of the built service.

``prepare_worktree`` makes a fresh checkout of the target repo at the start
state under ``base_dir``. For ``target_start == "empty"`` it ``git init``s an
empty directory (the target repo may have no commits yet); for a ref it clones
and checks that ref out.

``capture_built_sha`` snapshots whatever the harness produced as one commit so
the run record can point at an exact tree.

``run_service`` is an optional context manager: if the worktree has a
``Makefile`` with an ``up`` target it runs ``make up`` (else
``docker compose up -d`` if a compose file is present), waits for ``health_url``
to answer, yields, then tears the service down. The grader's HTTP run needs the
built service reachable at the URL in ``grader.env``; this is the simplest way
to make that true. Bypassed entirely on ``--dry-run`` and ``--no-serve``.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx


class WorkspaceError(RuntimeError):
    pass


def _git(args: list[str], cwd: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


def prepare_worktree(target_repo: str, target_start: str, base_dir: str | Path) -> Path:
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    if base.exists() and any(base.iterdir()):
        raise WorkspaceError(f"worktree base dir {base} is not empty")
    if (target_start or "empty").lower() == "empty":
        _git(["init", "-q", str(base)], cwd=Path.cwd())
        # A starting identity so capture_built_sha can commit later even on a
        # box with no global git config.
        _git(["config", "user.email", "benchmarks@pagehub.io"], cwd=base)
        _git(["config", "user.name", "pagehub-benchmarks"], cwd=base)
        return base
    try:
        _git(["clone", "-q", target_repo, str(base)], cwd=Path.cwd())
        _git(["checkout", "-q", target_start], cwd=base)
    except subprocess.CalledProcessError as exc:
        raise WorkspaceError(
            f"could not prepare worktree for {target_repo}@{target_start}: {exc.stderr}"
        ) from exc
    _git(["config", "user.email", "benchmarks@pagehub.io"], cwd=base)
    _git(["config", "user.name", "pagehub-benchmarks"], cwd=base)
    return base


def capture_built_sha(worktree: str | Path) -> str | None:
    """Commit whatever the harness wrote; return the resulting sha (or None)."""
    try:
        _git(["add", "-A"], cwd=worktree)
        # --allow-empty so a no-op build still yields a sha.
        _git(["commit", "-q", "--allow-empty", "-m", "pagehub-benchmarks: built state"], cwd=worktree)
        return _git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
    except subprocess.CalledProcessError:
        return None


def _has_make_target(worktree: Path, target: str) -> bool:
    makefile = worktree / "Makefile"
    if not makefile.is_file():
        return False
    return any(
        line.split(":", 1)[0].strip() == target
        for line in makefile.read_text().splitlines()
        if ":" in line and not line.startswith((" ", "\t"))
    )


def _wait_for(health_url: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(health_url, timeout=3.0)
            if r.status_code < 500:
                return
        except httpx.HTTPError as exc:  # noqa: PERF203
            last_err = exc
        time.sleep(1.0)
    raise WorkspaceError(f"service did not come up at {health_url}: {last_err}")


@contextlib.contextmanager
def run_service(
    worktree: str | Path,
    health_url: str | None,
    *,
    startup_timeout_s: float = 120.0,
) -> Iterator[None]:
    """Best-effort: bring the built service up, yield, tear it down."""
    worktree = Path(worktree)
    up: list[str] | None = None
    down: list[str] | None = None
    if shutil.which("make") and _has_make_target(worktree, "up"):
        up, down = ["make", "up"], ["make", "down"]
    elif any((worktree / f).is_file() for f in ("docker-compose.yml", "docker-compose.yaml", "compose.yml")):
        up, down = ["docker", "compose", "up", "-d"], ["docker", "compose", "down"]
    if up is None:
        # Nothing we know how to start — assume the operator runs it.
        yield
        return
    subprocess.run(up, cwd=str(worktree), check=True)  # noqa: S603
    try:
        if health_url:
            _wait_for(health_url, startup_timeout_s)
        yield
    finally:
        with contextlib.suppress(subprocess.SubprocessError, OSError):
            subprocess.run(down, cwd=str(worktree), check=False)  # noqa: S603
