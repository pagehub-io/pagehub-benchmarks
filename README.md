# pagehub-benchmarks

A **benchmark runner for LLM coding harnesses.** Cheaply, repeatably compare
harnesses / models / configs / prompts on *"can it build this code, in how many
tries, how long, and how much money?"*

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
  grader/client.py              pagehub-evals client (import bundle → run → verdict)
  runner/run.py                 the build→grade→retry loop + the CLI-facing wrapper
  runner/pricing.py             token counts → USD
  runner/results.py             the run record + on-disk filename
  runner/workspace.py           worktree prep + (best-effort) `make up` the built service
  config.py                     load/validate benchmark YAML + pricing
  __main__.py                   `python -m pagehub_benchmarks ...`  (list / run / site)
tools/build_site.py             results/**/*.json → docs/ (Jinja2; `make site`)
templates/, static/             site templates + plain CSS
tests/                          unit tests (FakeHarness + FakeGrader — no real claude / evals)
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
make run BENCHMARK=chess-backend DRY_RUN=1
#   == python -m pagehub_benchmarks run chess-backend --dry-run

# Real run (builds the target repo for real — costs tokens):
make run BENCHMARK=chess-backend
#   == python -m pagehub_benchmarks run chess-backend
python -m pagehub_benchmarks run chess-backend --harness claude-code --model claude-opus-4-7 \
    --config effort=xhigh --max-attempts 5 --results-dir results
```

A real run needs:

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
  `http://localhost:4002`). The grader imports `grader.fixture_bundle` (a path
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

## The first benchmark — `chess-backend`

Builds [`pagehub-io/eval-chess-backend`](https://github.com/pagehub-io/eval-chess-backend)
from empty: a `python-chess`-backed FastAPI chess API on :8003 (games / moves /
legal-moves, with the 404/422 edge cases spelled out in `prompts/chess-backend.md`).
Graded by the pagehub-evals `chess-rules` collection (fixture bundle:
`pagehub-evals/fixtures/chess-rules.json`) — a rule-conformance battery
(castling, en passant, promotion, pins, check evasion, checkmate, stalemate,
the draw rules).

## Results site

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
benchmark — that would call `claude` and pagehub-evals and cost tokens. The
runner is tested with `FakeHarness` / `FakeGrader`. (`.github/workflows/pages.yml`
is separate — it regenerates and publishes the results site on push to `main`.)
