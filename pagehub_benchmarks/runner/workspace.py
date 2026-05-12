"""Worktree preparation + (best-effort) running of the built service.

``prepare_worktree`` makes a fresh checkout of the target repo at the start
state under ``base_dir``. For ``target_start == "empty"`` it ``git init``s an
empty directory (the target repo may have no commits yet); for a ref it clones
and checks that ref out.

``capture_built_sha`` snapshots whatever the harness produced as one commit so
the run record can point at an exact tree.

``run_service`` is an optional context manager: it finds how to start the built
service — a ``Makefile`` ``up`` (or ``serve`` / ``run`` / ``dev`` / ``start``)
target, else ``docker compose up -d --build`` if a compose file is present —
launches it **detached in its own process group** (a ``make up`` is usually a
foreground ``uvicorn …`` process, so the runner must own and later kill it),
polls ``health_url`` until the service answers (and just warns + proceeds if it
never does — the grader will report the transport errors, which is the right
signal), yields, then kills the process group and runs the ``down`` target /
``docker compose down``. The grader's HTTP run needs the built service reachable
at the URL in ``grader.env``; this is the simplest way to make that true.
Bypassed entirely on ``--dry-run`` and ``--no-serve``.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx

_SERVICE_LOG = ".pagehub-benchmarks-service.log"
_MAKE_UP_TARGETS = ("up", "serve", "run", "dev", "start")
_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


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
        # Don't drag the service log (or other run artifacts) into the snapshot.
        gi = Path(worktree) / ".git" / "info" / "exclude"
        with contextlib.suppress(OSError):
            existing = gi.read_text() if gi.is_file() else ""
            if _SERVICE_LOG not in existing:
                gi.write_text(existing + f"\n{_SERVICE_LOG}\n")
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


def _resolve_up_down(worktree: Path) -> tuple[list[str] | None, list[str] | None]:
    if shutil.which("make"):
        for target in _MAKE_UP_TARGETS:
            if _has_make_target(worktree, target):
                down = ["make", "down"] if _has_make_target(worktree, "down") else None
                return ["make", target], down
    if shutil.which("docker") and any((worktree / f).is_file() for f in _COMPOSE_FILES):
        return ["docker", "compose", "up", "-d", "--build"], ["docker", "compose", "down"]
    return None, None


def _wait_for(health_url: str, timeout_s: float, proc: subprocess.Popen | None) -> bool:
    """Poll until the service answers (<500). Returns False (with a warning) on
    timeout or if the start command exits non-zero — never raises (a dead
    service surfaces as grader transport errors, not a crashed run)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc is not None:
            rc = proc.poll()
            if rc is not None and rc != 0:
                print(f"(warning: service start command exited {rc}; grading anyway)")
                return False
        with contextlib.suppress(httpx.HTTPError):
            r = httpx.get(health_url, timeout=3.0)
            if r.status_code < 500:
                return True
        time.sleep(1.5)
    print(f"(warning: service did not answer at {health_url} within {timeout_s:.0f}s — grading anyway)")
    return False


def _kill_group(proc: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)
    if proc.poll() is None:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


@contextlib.contextmanager
def run_service(
    worktree: str | Path,
    health_url: str | None,
    *,
    startup_timeout_s: float = 300.0,
) -> Iterator[None]:
    """Best-effort: bring the built service up, yield, tear it down."""
    worktree = Path(worktree)
    up, down = _resolve_up_down(worktree)
    if up is None:
        # Nothing we know how to start — assume the operator runs it.
        print("(no `make up` / compose file in the built repo — assuming the service is already running)")
        yield
        return
    log_path = worktree / _SERVICE_LOG
    print(f"starting built service: {' '.join(up)} (cwd={worktree}, log={_SERVICE_LOG})")
    log_fh = open(log_path, "w")  # noqa: SIM115 — closed in the finally below
    proc = subprocess.Popen(  # noqa: S603
        up, cwd=str(worktree), start_new_session=True, stdout=log_fh, stderr=subprocess.STDOUT
    )
    try:
        if health_url:
            _wait_for(health_url, startup_timeout_s, proc)
        yield
    finally:
        _kill_group(proc)
        if down is not None:
            with contextlib.suppress(subprocess.SubprocessError, OSError):
                subprocess.run(down, cwd=str(worktree), check=False, timeout=120)  # noqa: S603
        with contextlib.suppress(OSError):
            log_fh.close()
