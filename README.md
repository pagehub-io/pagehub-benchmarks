# pagehub-benchmarks

A **benchmark runner for LLM coding harnesses.** Cheaply, repeatably compare
harnesses / models / configs / prompts on *"can it build this code, in how many
tries, how long, and how much money?"*

📊 **Live results site:** <https://pagehub-io.github.io/pagehub-benchmarks/> —
sortable table of every run (attempts · pass · tokens · cost · wall time) with
per-run and per-benchmark detail pages. Regenerated and published on every push
to `main` (see [Results site](#results-site)).

## What a benchmark is

A **benchmark** = a build task (a verbatim prompt) + a target repo to build
into + the pagehub-evals collection that grades the result. It lives in:

- `benchmarks/<name>.yaml` — the definition (target repo, prompt file, grader
  wiring, `max_attempts`, and the **matrix** of `(harness, model, config)` to
  run).
- `prompts/<name>.md` — the verbatim build prompt the harness receives.

A **run** picks one `(harness, model, config)` from the matrix and drives this
loop:

1. Make a fresh worktree of the target repo at the start state.
2. Invoke the harness headlessly with the build prompt — it writes code.
3. Grade it via pagehub-evals: import the fixture bundle, run the collection,
   read the verdict.
4. If the verdict isn't all-green and `attempt < max_attempts`: re-invoke the
   harness *in the same session* with the failing-eval output → back to (3).
5. Stop when green or attempts exhausted.

It records, per run: the attempt # that went green (or the cap if it never
did), `passed`, total input/output/cache tokens, `cost_usd` (tokens × the
per-model rate table in `pricing.yaml`), total wall time, and a per-attempt
breakdown — one JSON file under `results/<benchmark>/`.

## Layout

```
benchmarks/<name>.yaml          benchmark definitions (incl. the harness matrix)
prompts/<name>.md               verbatim build prompts
pricing.yaml                    per-model USD / 1M tokens (input/output/cache)
results/<benchmark>/*.json      committed run records (one per run) — the site's data
docs/                           generated static site (committed; published to GH Pages)
pagehub_benchmarks/
  harnesses/base.py             Harness ABC + AttemptResult
  harnesses/claude_code.py      Claude Code adapter (`claude -p ... --output-format json`)
  harnesses/codex_cli.py        Codex CLI adapter (`codex exec ... --json`, OpenAI)
  grader/client.py              pagehub-evals client (import bundle → run → verdict)
  runner/run.py                 the build→grade→retry loop + the CLI-facing wrapper
  runner/pricing.py             token counts → USD
  runner/results.py             the run record + on-disk filename
  runner/workspace.py           worktree prep + (best-effort) `make up` the built service
  config.py                     load/validate benchmark YAML + pricing
  __main__.py                   `python -m pagehub_benchmarks ...`  (list / run / site)
tools/build_site.py             results/**/*.json → docs/ (Jinja2; `make site`)
templates/, static/             site templates + plain CSS
tests/                          unit tests (FakeHarness + FakeGrader — no real claude / codex / evals)
tests/fixtures/                 recorded `codex exec --json` streams the codex adapter is tested against
```

## Usage

```bash
make install                       # pip install -r requirements.txt
make test                          # unit tests
make lint                          # ruff
make list                          # list defined benchmarks
make site                          # regenerate docs/ from results/**/*.json

# Sanity-check a benchmark's wiring offline — YAML + prompt + grader fixture +
# pricing — without calling claude or pagehub-evals:
make run BENCHMARK=eval-chess-backend DRY_RUN=1
#   == python -m pagehub_benchmarks run eval-chess-backend --dry-run

# Real run (builds the target repo for real — costs tokens). With no filter it
# runs EVERY row of the benchmark's matrix in order — for eval-chess-backend that
# is Claude Code and then Codex CLI (up to max_attempts high-effort gpt-6-astra
# attempts on your ChatGPT plan). Use --harness / --model to pick one row.
make run BENCHMARK=eval-chess-backend
#   == python -m pagehub_benchmarks run eval-chess-backend
python -m pagehub_benchmarks run eval-chess-backend --harness claude-code --model claude-opus-4-7 \
    --config effort=xhigh --max-attempts 5 --results-dir results
python -m pagehub_benchmarks run eval-chess-backend --harness codex-cli --model gpt-6-astra \
    --max-attempts 3
```

A real run needs (per matrix row: a row whose harness can't start — CLI missing,
not logged in — fails the run at that row; records of earlier rows are already
written and pushed, but the site rebuild is skipped, so run `make site`):

- **`codex` on PATH**, logged in with a ChatGPT subscription, for `codex-cli`
  rows — see [Codex CLI harness](#codex-cli-harness-codex-cli) below.
- **`claude` on PATH**, already logged in. Runs execute under the CLI's
  *existing subscription auth* (flat-rate) — the adapter explicitly **unsets
  `ANTHROPIC_API_KEY`** in the subprocess so a stray env key can't divert the
  run onto metered API billing. (`claude -p` saying "not logged in" → run
  `claude login`; don't set an API key.) Because of this, **`cost_usd` in a
  run record is a *computed* figure** — summed tokens × the rates in
  `pricing.yaml`, i.e. "what this would have cost at API rates" — useful for
  comparing harnesses / models / configs, not an actual API bill.
  `config.effort` (`low|medium|high|xhigh|max`) is passed as `claude --effort`.
- **pagehub-evals running** at `grader.evals_base_url` (default
  `http://localhost:8002`). The grader imports `grader.fixture_bundle` (a path
  *within the pagehub-evals repo* — resolved relative to `PAGEHUB_EVALS_REPO`,
  default `~/github/pagehub-io/pagehub-evals`), then runs `grader.collection`.
  Fixture import is operator-only; provide a bearer token via
  `PAGEHUB_EVALS_TOKEN`, or let the grader mint a dev HS256 token (works
  against a dev pagehub-evals — see `.env.example`).
- The **built service reachable** at the URL in `grader.env` (the chess
  benchmark uses `eval-chess-backend_url: http://localhost:8003`). If the
  built worktree has a `make up` target (or a `docker-compose.yml`), the runner
  brings it up before grading and tears it down after; pass `--no-serve` to
  manage it yourself.

### Codex CLI harness (`codex-cli`)

The same loop can drive OpenAI's Codex CLI (`codex exec`) so a benchmark runs
head-to-head against Claude Code with the identical prompt, grader and run
record. The adapter is `pagehub_benchmarks/harnesses/codex_cli.py`; the design
and every verified/unverified fact behind it is in `plans/codex-cli-harness.md`.

- **Version:** `codex` ≥ 0.153.0 on PATH (developed and tested against
  0.154.0; `npm i -g @openai/codex`). `benchmarks/eval-chess-backend.yaml`
  carries a `codex-cli` / `gpt-6-astra` row.
- **Login:** `codex login` (or `codex login --device-auth` on a headless box).
  Runs require the **ChatGPT-subscription** login: before the first attempt the
  adapter runs `codex login status` and refuses unless it reports `Logged in
  using ChatGPT`. `CODEX_API_KEY`, `CODEX_ACCESS_TOKEN` and `OPENAI_API_KEY`
  are unset in the subprocess — an API key would silently move the run onto
  metered billing. As for Claude, **`cost_usd` is a computed figure** (tokens ×
  `pricing.yaml`), not a bill. The pre-flight also refuses to run while a
  global `~/.codex/AGENTS.md`, `AGENTS.override.md` or legacy
  `instructions.md`, or user skills under `~/.codex/skills/`, exist:
  `--ignore-user-config` does not suppress them (verified), and they would
  change what every run measures.
- **Effort is required and explicit.** `config.effort` (`low|medium|high|xhigh|max`)
  is passed as `-c model_reasoning_effort=…` on **every** attempt. Codex would
  otherwise inherit `~/.codex/config.toml` or the model default (a bare
  `codex exec resume` was observed to reset the effort), silently changing
  results between runs; codex accepts any string here without validating it,
  so the adapter's explicit map is the only guard. `ultra` (automatic sub-agent
  delegation) is deliberately not mapped. `--ignore-user-config` is passed on
  both legs so the operator's config never leaks in; codex still appends a
  `[projects."<worktree>"] trust_level` entry to `~/.codex/config.toml` per run
  (harmless; prune occasionally).
- **Sandbox:** `--sandbox workspace-write` (never `danger-full-access`) with
  network enabled inside the sandbox. Compared with Claude Code (which runs
  unsandboxed with `--dangerously-skip-permissions`), the codex agent can
  write only the worktree, `/tmp` and `$TMPDIR`, sees the worktree's `.git` read-only,
  and can only `pip install` into a venv under the worktree or `/tmp` (prefer
  `/tmp` — a venv left in the worktree is committed and pushed with the build).
  It can read the whole filesystem and reach the network — the same exposure
  as Claude. **Secrets in your shell profile reach an agent's login shell**:
  codex runs commands with `bash -lc`, which sources the profile of whatever
  `HOME` is. The adapter therefore gives every codex subprocess a **throwaway
  `HOME`** under `~/.cache/pagehub-benchmarks/codex-homes/` — outside the
  sandbox's writable roots, so the agent cannot plant skills or edit it for
  its later turns — with `CODEX_HOME` pinned to your real login directory,
  disables codex's login-shell snapshot (otherwise written to
  `~/.codex/shell_snapshots/` in plaintext, values included) and filters
  inherited variables named `*KEY*`/`*SECRET*`/`*TOKEN*`/`*PASSWORD*` — a probe
  agent then saw no secret-named variables where it previously listed 37 from
  `~/.bashrc`. (That throwaway HOME holds only a `.bash_profile` restoring
  your `PATH` — a stock `/etc/profile` resets it for login shells — and your
  locale: codex forces `C.UTF-8`, which some boxes' bash cannot load, and the
  resulting `setlocale` warnings would flood every command's output. The
  per-run directories are tiny; prune them occasionally.)
  Claude Code has no equivalent and sees everything, so keeping
  secrets out of `~/.bashrc` on the runner box (or benchmarking under a
  dedicated user) is still the right hygiene. Codex also prepends its
  own instructions (bundled skills, apps and plugins instructions, a
  multi-agent role; proactive sub-agent delegation is off at the default
  effort levels) to every thread and re-injects its skills instructions on
  every resumed turn, as Claude Code does its system prompt. After each attempt the **runner** executes the
  built `Makefile` (`make up`) on the host, outside any sandbox, for both
  harnesses.
- **What is recorded:** `session_handle` is the codex `thread_id`; attempt 2+
  runs `codex exec resume <thread_id>` with the failing-eval output. Token
  counts come from the `--json` stream's `turn.completed.usage`, which on a
  resumed thread is the **thread total** — the adapter records each attempt's
  delta (`raw.usage` is the verbatim cumulative object, `raw.usage_delta` the
  attempt's share). `input_tokens` in the record is the non-cached slice;
  `cached_input_tokens` → cache reads, `cache_write_input_tokens` → cache
  writes (priced at OpenAI's write rate); `output_tokens` includes reasoning
  (`raw.reasoning_output_tokens` is recorded separately). A failed turn
  carries no usage on the stream, so that path reads the turn's usage from
  codex's rollout (`raw.usage_source: "rollout"`). `raw.rate_limits` carries
  codex's 5-hour and weekly `used_percent` for the subscription — watch it on
  a Plus plan. If codex ever reports no cache split, the cache columns show
  `0` and `raw.cache_tokens_reported` is `false`.
- **When a run crashes vs. records:** a turn in which the model never ran
  (not logged in, usage limit hit, provider outage, a rejected prompt — see
  openai/codex#43237) is retried `CODEX_DEAD_TURN_RETRIES` times (default 2)
  and then fails the run loudly with no record and no push — the same as the
  Claude adapter on a non-zero exit. A turn in which the model did work and
  then failed is recorded as a failed attempt with the error text under
  `raw.harness_error`, graded as-is, and the thread is resumed.
  `CODEX_BUILD_TIMEOUT_SECONDS` (default 3600) bounds each `codex exec` leg
  (a retried attempt is several legs). Ctrl-C during a leg kills the whole
  codex process group (it runs in its own session, so the terminal's SIGINT
  would not reach it otherwise).
- **Matrix note:** the `eval-chess-backend` row runs codex at
  `effort: high` while the existing Claude row is `xhigh`; both models accept
  `xhigh` — add a matching row on either side for a like-for-like comparison.

See `.env.example` for every knob.

## Run record

`results/<benchmark>/<harness>__<model>__<config-slug>__<ISO8601>.json` —
append-only, one per run:

```json
{
  "benchmark": "...", "harness": "...", "model": "...", "config": {...},
  "started_at": "...", "finished_at": "...",
  "target_repo": "...", "target_start": "empty", "built_git_sha": "...",
  "worktree_path": "...", "max_attempts": 5,
  "attempts": 2, "passed": true,
  "total_input_tokens": 0, "total_output_tokens": 0, "total_cache_tokens": 0,
  "cost_usd": 0.0, "total_wall_time_seconds": 0.0,
  "per_attempt": [
    {"attempt": 1, "input_tokens": 0, "output_tokens": 0, "cache_tokens": 0,
     "wall_time_seconds": 0.0, "grader_passed": false, "grader_failures": ["..."]},
    {"attempt": 2, "...": "...", "grader_passed": true, "grader_failures": []}
  ]
}
```

## The first benchmark — `eval-chess-backend`

Builds [`pagehub-io/eval-chess-backend`](https://github.com/pagehub-io/eval-chess-backend)
from empty: a `python-chess`-backed FastAPI chess API on :8003 (games / moves /
legal-moves, with the 404/422 edge cases spelled out in `prompts/eval-chess-backend.md`).
Graded by the pagehub-evals `eval-chess-backend` collection (fixture bundle:
`pagehub-evals/fixtures/eval-chess-backend.json`) — a rule-conformance battery
(castling, en passant, promotion, pins, check evasion, checkmate, stalemate,
the draw rules).

## Results site

**Live at <https://pagehub-io.github.io/pagehub-benchmarks/>.**

`results/**/*.json` (the committed run history) feeds a small static site under
`docs/`:

- `docs/index.html` — a sortable table of every run (benchmark · harness ·
  model · config · date · attempts · passed · tokens · cost · wall time), each
  row linking to that run's detail page, with a per-benchmark summary block on
  top (run count, pass rate, best/median attempts, cheapest passing run).
- `docs/runs/<run-id>.html` — one run: all metrics, the per-attempt breakdown
  (tokens / wall time / grader passed? / the grader failures listed), and a
  **Links** section with hyperlinks to the target repo, the built-code commit
  (`…/commit/<built_git_sha>`), the benchmark YAML, the build prompt, the
  grader fixture bundle (in the pagehub-evals repo), and the raw run JSON.
- `docs/benchmarks/<name>.html` — one benchmark: its description, links to its
  prompt + grader, and the list of all runs against it.

Regenerate it locally with `make site` (or `python -m tools.build_site`, or
`python -m pagehub_benchmarks site`). A real `run` regenerates it automatically
afterward unless you pass `--no-build-site`. `docs/` is committed and published
to **GitHub Pages** by `.github/workflows/pages.yml` on every push to `main`
(set repo *Settings → Pages → Source* to "GitHub Actions"). Generation is
static — Jinja2 templates in `templates/` + `static/style.css` + a few lines of
vanilla JS for column sort; no SPA framework, no JS build step. The GitHub URLs
in the Links sections default to the `pagehub-io` org repos; override via
`PAGEHUB_BENCHMARKS_REPO_URL` / `PAGEHUB_EVALS_REPO_URL` if you fork.

## CI

`.github/workflows/ci.yml` runs **ruff + pytest only**. CI never runs a real
benchmark — that would call `claude` / `codex` and pagehub-evals and cost
tokens. The runner is tested with `FakeHarness` / `FakeGrader`; the codex
adapter against recorded `codex exec --json` streams in `tests/fixtures/`. (`.github/workflows/pages.yml`
is separate — it regenerates and publishes the results site on push to `main`.)
