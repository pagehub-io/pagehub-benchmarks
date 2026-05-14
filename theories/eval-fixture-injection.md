---
name: eval-fixture-injection
hypothesis: >-
  Injecting the grader fixture bundle (request templates + assertions)
  directly into the build prompt reduces attempts, total tokens, and
  wall-time vs a build prompt that does not reference the evals.
baseline: eval-chess-frontend
treatment: eval-chess-frontend-with-fixture
metrics:
  - attempts
  - total_output_tokens
  - total_cache_tokens
  - cost_usd
  - total_wall_time_seconds
  - passed
status: pending
---

## Background

`eval-chess-frontend` Run #1 (`claude-code` / `claude-opus-4-7` / `effort=xhigh`)
**PASSED in 2 / 5 attempts**, ~19 minutes wall-time, ~30M cache tokens,
computed cost ~$30 at API rates. Attempt 1 failed wholesale because the
harness did not initially render the testid-bearing board element, so
every grader `get-attribute` returned 404. Attempt 2 was the runner's
retry prompt, which carries the failing-eval output verbatim — the
harness corrected the DOM contract on that prompt and went green.

**Why is this interesting?** The information that fixed the bug on
attempt 2 (which testids the grader binds to, which attributes it reads
off the board, the exact request sequence) was *already statically known
at build time* — it lives in `fixtures/eval-chess-frontend.json` in the
pagehub-evals repo, and the grader's behavior is fully determined by
that bundle. The harness only learned it on attempt 2 because attempt 1
hadn't been graded yet. If we *give* the harness the fixture bytes in
the build prompt itself, it should be able to internalize the exact
contract on attempt 1 — converging in fewer attempts, with fewer
total tokens, and faster wall-time, **on average**.

(Note: we're testing a single LLM run, so we cannot draw a statistically
significant conclusion from one PASS / FAIL pair. The metrics we
*compare* — `attempts`, `total_output_tokens`, `total_cache_tokens`,
`cost_usd`, `total_wall_time_seconds` — are noisy on n=1, but the
*direction* of the deltas is the qualitative signal we're after. Later
theories may add multi-run averaging.)

## What would change our mind

- **Refuted** if the treatment run takes *more* attempts than the
  baseline, OR uses materially more tokens (>1.5× output tokens), OR
  takes materially longer wall-time (>1.5×). One plausible failure
  mode: the harness over-fits to the fixture's request order and
  ignores the wider DOM-contract section of the prompt, missing
  un-graded-but-correct behavior.
- **Inconclusive** if the deltas are within noise (say, ±30% on
  tokens / wall-time, same `attempts` count). One run can't
  distinguish "fixture injection didn't help" from "the harness got
  lucky / unlucky on this particular attempt-1 draw."
- **Supported** if the treatment cuts attempts (e.g., 2 → 1) AND
  meaningfully reduces tokens or wall-time. The bigger the delta, the
  cleaner the signal, but a single attempt-1 PASS would be a strong
  qualitative signal even before we have a statistical comparison.

## Expected outcome

Based on what we saw in Run #1 (the retry message that fixed everything
was the failing-eval summary, which the fixture *encodes* statically),
the prior is: **treatment finishes in 1 attempt, ~half the total
output tokens, ~half the wall-time, ~half the cost.** Cache tokens are
the wild card — injecting the fixture in the prompt makes the prompt
~30 KB larger, which inflates *prompt-cache-write* tokens on attempt 1
but should *reduce* total cache traffic across the run (no retry → no
second round of cache reads). Net direction expected to be down, but
the magnitude is uncertain.

## How to test

1. Merge PR A (pagehub-evals fixtures endpoint) and PR B (pagehub-benchmarks
   prompt templating). [done; both are on `main`.]
2. Merge PR C (this file + the treatment benchmark + the site pages).
3. Run the treatment benchmark:
   `make run BENCHMARK=eval-chess-frontend-with-fixture`. Same harness +
   model + effort + `max_attempts` as the baseline so only the prompt varies.
4. Land the resulting run record under `results/eval-chess-frontend-with-fixture/`
   (committed automatically by the runner).
5. Update `status:` in this file from `pending` to `supported` / `refuted` /
   `inconclusive` based on the comparison the site renders.
