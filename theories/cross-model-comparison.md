---
name: cross-model-comparison
hypothesis: >-
  A state-of-the-art OpenAI reasoning model (gpt-5.5-pro, via
  pagehub-llm-gateway) given the same fixture-injecting build prompt as
  Claude Opus 4.7 converges in a similar number of attempts and within
  a similar token / wall-time budget — i.e. the eval-fixture-injection
  effect generalizes across providers, and the two frontier models are
  not separated by a large margin on this task.
baseline: eval-chess-frontend-with-fixture
treatment: eval-chess-frontend-with-fixture-via-openai
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

The [eval-fixture-injection](eval-fixture-injection.html) theory established
that injecting the grader fixture into the build prompt cut the Claude Opus
4.7 run on `eval-chess-frontend` from a 2-attempt PASS (~$30, ~19 min) to a
1-attempt PASS (~$10.80, ~9 min). That experiment held the model constant
(claude-opus-4-7 / effort xhigh) and varied the prompt.

The natural next question is whether the effect is model-specific or a
general property of "more information in the prompt is better." We can't
answer that with one provider — Anthropic and OpenAI have different
training recipes, different tool-use scaffolds, and different attention
patterns. So we want a head-to-head: same prompt (the
fixture-injecting one), same target repo, same grader, same
`max_attempts` — and swap the model out for a frontier OpenAI reasoning
model routed via [pagehub-llm-gateway](https://github.com/pagehub-io/pagehub-llm-gateway).

The gateway translates the Anthropic-compatible `/v1/messages` protocol
(what `claude -p` speaks) into OpenAI's `/v1/responses` upstream, so the
harness invocation is bit-for-bit identical to the baseline run except
for two env vars (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`) and the
`--model` arg (`gpt-5.5-pro` vs `claude-opus-4-7`).

**Why gpt-5.5-pro?** It's the highest-tier reasoning model OpenAI's
`/v1/models` currently exposes (released 2026-04-23, verified at
authoring time against the live `/v1/models` list). The pro tier is
the closest analogue to Claude Opus 4.7 / effort xhigh in terms of
"the strongest reasoning model the provider sells at the endpoint."
Pricing is asymmetric: $30/$180 per Mtok (gpt-5.5-pro) vs $15/$75 per
Mtok (claude-opus-4-7) — OpenAI is 2× the input rate and 2.4× the
output rate. Neither has a published cached-input discount at the pro
tier, so cache traffic doesn't tilt the comparison.

## What would change our mind

- **Supported** if the OpenAI run also passes within the cap, in the
  same order-of-magnitude on attempts, tokens, and wall-time as the
  Opus run (rough rule of thumb: within 2× on each numeric metric).
  That's the "models are not far apart on this task" outcome.
- **Refuted (Opus dominant)** if gpt-5.5-pro fails the benchmark
  outright (no PASS in 5 attempts) while Opus passed on attempt 1, OR
  costs >5× more tokens / wall-time for the same outcome. Plausible
  failure mode: the OpenAI model overruns on tool-use planning or
  fails to follow the prompt's explicit DOM-contract section.
- **Refuted (GPT dominant)** symmetric — gpt-5.5-pro passes faster /
  cheaper than Opus by >2×. Less interesting for the "are they close"
  question, but still tells us something about the cross-provider gap
  on this surface.
- **Inconclusive** if both pass on attempt 1 with metrics within
  ±30%. That's the noise floor on n=1 runs.

## Expected outcome

Prior: **gpt-5.5-pro passes on attempt 1**, similar to Opus on the
fixture-injecting prompt. Both models are reasoning-tier, and the
prompt itself encodes the contract that fixed the Opus run on
attempt 1; the bottleneck shouldn't be model capability but
prompt-following. Token counts should be in the same order of
magnitude. Cache traffic between providers isn't directly
comparable — Anthropic has explicit cache writes priced at 1.25×
input and cache reads at 0.1×, while gpt-5.5-pro publishes no
cached-input discount at all — so the right side-by-side comparison
is `cost_usd`, which normalizes by the per-Mtok rate table.

## How to test

1. Merge gateway-aware harness ([PR #8 on pagehub-benchmarks](https://github.com/pagehub-io/pagehub-benchmarks/pull/8)). [done — on main.]
2. Merge this PR (this file + the treatment benchmark YAML).
3. Bring up pagehub-llm-gateway on `:4011` with `OPENAI_API_KEY` and
   `GATEWAY_AUTH_TOKEN` in its env. (Requires
   [pagehub-llm-gateway PR #1](https://github.com/pagehub-io/pagehub-llm-gateway/pulls)
   to be merged or that branch checked out locally.)
4. Bring up pagehub-evals on `:8002` and pagehub-browser on `:4010`
   (same services the baseline run needed).
5. Export `GATEWAY_AUTH_TOKEN=<value>` into the benchmark runner's
   env (must match the gateway's `GATEWAY_AUTH_TOKEN`); the harness
   reads it from there at dispatch.
6. Run: `make run BENCHMARK=eval-chess-frontend-with-fixture-via-openai`.
7. Update `status:` in this file based on the resulting comparison.

## Observed outcome

_pending — fill in once the run lands. Compare against the
fixture-injecting Opus run
([2026-05-14T14-20-19Z](https://pagehub-io.github.io/pagehub-benchmarks/runs/claude-code__claude-opus-4-7__effort-xhigh__2026-05-14T14-20-19Z.html))
on the six metrics above._
