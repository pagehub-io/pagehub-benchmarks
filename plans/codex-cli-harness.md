# Tech spec — Codex CLI harness (`codex-cli`, GPT-6 Astra) for pagehub-benchmarks

Status: **APPROVED v5** (gate passed round 5: 0 critical, 0 important; nits swept in this text) — 2026-09-10. Amended after PR #29 review round 1 (raw keys, rule 3/5 wording, pricing tier note, Stage-2 prose list). Review history: round 1 on v1 (1 critical,
11 important, 10 nits) → v2; round 2 on v2 (0 critical, 5 important, 10 nits)
→ v3; round 3 on v3 (0 critical, 2 important, 11 nits) → v4; round 4 on v4
(0 critical, 2 important — both caused by v4's Stage-1 guard, now removed —
7 nits) → v5. Every critical/important finding from all rounds is addressed
below;
factual corrections were re-verified by execution on this box. Preflight items
0.2 and 0.4 remain blocked on `codex login` (§3.2). Everything marked
**UNVERIFIED** must be confirmed from the real fixtures before the usage
parser is coded (§6 sequences the work accordingly).

## 1. Goal

Run the existing benchmarks head-to-head between Claude Code and OpenAI's
Codex CLI running `gpt-6-astra`, producing run records that are directly
comparable: same run-record schema, same grader, same byte-identical build
prompt. Measured per harness: attempts-to-green, tokens, computed `cost_usd`,
wall time.

## 2. Scope and hard constraints

In scope: new adapter `pagehub_benchmarks/harnesses/codex_cli.py` registered
as `codex-cli`, mirroring `claude_code.py` in structure and in the
`AttemptResult` it returns (deviations listed in §4.12); `pricing.yaml` entry
for `gpt-6-astra`; matrix row in `benchmarks/eval-chess-backend.yaml`;
fixture-driven unit tests; README subsection; `.env.example` knobs.

Out of scope / must not change: run-record schema (`runner/results.py`),
site template column set, the Claude adapter, `prompts/*.md`, the
pagehub-evals repo, the runner loop's control flow. CI never invokes a real
`codex`. No token-burning run without the operator's explicit go-ahead.
Never `--sandbox danger-full-access` and never
`--dangerously-bypass-approvals-and-sandbox` (both exist on `exec` and
`exec resume`). The worktree is the only thing the harness writes; the
runner, not the harness, brings the built service up.

## 3. Preflight findings (codex-cli 0.154.0, 2026-09-10, this WSL box)

### 3.1 Verified by execution here

| # | Item | Result |
|---|------|--------|
| 0.1 | `codex --version` | **Not installed** on the box. Installed `@openai/codex@0.154.0` via `npm i -g` (latest stable; 0.155.0 is alpha-only). → `codex-cli 0.154.0`. The task's floor is ≥ 0.153.0. |
| 0.1 | `codex exec --help` | Flags used: `-m`, `--json` (JSONL events on stdout), `-C <DIR>`, `-s/--sandbox {read-only,workspace-write,danger-full-access}`, `-c key=value` (value parsed as TOML), `--ignore-user-config` ("do not load `$CODEX_HOME/config.toml`; auth still uses `CODEX_HOME`"), `--skip-git-repo-check`. Positional `[PROMPT]`; `-` reads instructions from stdin. **If stdin is a pipe and a prompt arg is also given, stdin is appended as a `<stdin>` block** — the adapter must own stdin. |
| 0.2 | `codex login status` | `Not logged in`, exit **1**. The OK probe cannot run; `tests/fixtures/codex_exec_ok.jsonl` does not exist yet. Binary carries the mode lines this command prints: `Logged in using ChatGPT`, `Logged in using an API key`, `Logged in using access token`, `Logged in using personal access token`, `Logged in using workload identity`, `Logged in using Amazon Bedrock …`, `Not logged in`. |
| 0.2 | unauthenticated `codex exec … --json` | Exit **1**. stdout is clean JSONL: `thread.started{thread_id}`, `turn.started`, repeated `error{message}`, `item.completed{item:{id,type:"error",message}}`, terminal `turn.failed{error:{message}}`. Human log lines go to stderr. Saved verbatim: `tests/fixtures/codex_exec_unauthenticated.jsonl` (start) and `…_resume_unauthenticated.jsonl` (resume). The user message is persisted in the thread even when the turn 401s. Every probe session on this box died at the 401 **before any tool call** — see U5. |
| 0.3 | resume mechanism | `codex exec resume <SESSION_ID> [PROMPT]`; `SESSION_ID` = `thread_id` from `thread.started`. Resume appends to the same rollout file and re-emits `thread.started` with the same id. `exec resume` has **no `-C` and no `--sandbox`**; it accepts `-m`, `-c`, `--json`, `--ignore-user-config`, `--skip-git-repo-check`. Process cwd must be inside a git repo ("Not inside a trusted directory…" otherwise — refused before any lookup); a fresh `git init` passes (`session_meta.git = {}`). Resume-by-id was verified **only from the original cwd**. |
| 0.3 | what a resume inherits | No `-c`: sandbox carried over, **`effort` dropped to `None`** (= model default). With `-c model_reasoning_effort=…` / `-c sandbox_mode=…`: honoured. ⇒ re-pass effort on every resume. |
| 0.3 | `<permissions instructions>` injection | Injected as a developer message immediately before a turn whose **resolved sandbox settings changed** (observed before the read-only flip and before the network flip). Start via `--sandbox workspace-write` then resume via `-c sandbox_mode="workspace-write"` with the same network setting → **no** injection (wt-probe turn 2; both turns of the `--ignore-user-config` session). The two spellings resolve equal. |
| 0.4 | token usage location | **BLOCKED** (needs a successful turn) — U1/U2. Binary strings only: usage struct `input_tokens cached_input_tokens cache_write_input_tokens output_tokens reasoning_output_tokens total_tokens`; both `last_token_usage` and `total_token_usage` are tracked; a metric named `codex.turn.token_usage.non_cached_input_tokens` sits beside `cached_input_tokens` (hint that cached ⊂ input); `--json` event names `thread.started turn.started turn.completed turn.failed item.started item.updated item.completed error`. Hints, not contract. |
| 0.5 | accepted `model_reasoning_effort` | `codex debug models` (no auth needed): `gpt-6-astra` → `low, medium, high, xhigh, max, ultra`, default `low`; `gpt-5.6-sol` same set. `ultra` = "maximum reasoning with automatic task delegation". A **bogus value is accepted silently** even with `--strict-config` and recorded verbatim in `turn_context.effort`. |
| — | stdin prompt fidelity | 176-character (180-byte UTF-8) multi-line probe (quotes, backticks, `{{ }}`, `$HOME`, em dash, blank lines) via `-` recorded **byte-identical** as the user message; no `<stdin>` wrapper. |
| — | `-C <dir> --sandbox workspace-write` | `turn_context`: `cwd=<dir>`, `sandbox_policy={type: workspace-write, network_access: false, …}`, `approval_policy: never`. `-c sandbox_workspace_write.network_access=true` → `network_access: true`. |
| — | sandbox **filesystem** profile (from the `<environment_context>` codex sends the model) | Writable: the worktree, `/tmp`, `$TMPDIR`. **Read-only inside the worktree: `.git`, `.agents`, `.codex`.** Everything else on the box: **readable**. So the agent cannot `git commit`; `pip install` into system/user site-packages or `~/.cache/pip` is blocked (a venv inside the worktree or `/tmp` works); Docker socket access unknown. |
| — | auth env vars | `CODEX_API_KEY=sk-bogus` → 401 text changes to **"Incorrect API key provided: sk-bogus…"** ⇒ live runtime auth source; would silently move a run onto metered API billing. `CODEX_ACCESS_TOKEN=bogus` → `login status` errors "invalid agent identity JWT format" ⇒ read. `OPENAI_API_KEY=sk-bogus` → unchanged "Missing bearer" ⇒ **not** read at runtime by 0.154 (only by `codex login --with-api-key`). |
| — | `~/.codex/config.toml` | **Exists** (created by the probes): one `[projects."<path>"] trust_level = "trusted"` entry per directory codex has run in. **Still written with `--ignore-user-config`** (verified). ⇒ every benchmark worktree path accumulates an entry. Harmless; documented. |
| — | `--ignore-user-config` on both legs | Reaches `thread.started` on start and resume; `-c` overrides still honoured (`effort=high`, `workspace-write`, `network_access=true`, `model=gpt-6-astra` recorded on both turns). Auth interaction: U6. |
| — | session files | `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<thread_id>.jsonl`; line `type ∈ {session_meta, turn_context, event_msg, response_item, world_state}`; `turn_context` records `model`, `effort`, `cwd`, `sandbox_policy`, `turn_id` per turn; `event_msg` payloads carry `turn_id`. `history_mode=paginated`; a rollout-migration feature exists ⇒ treat the on-disk format as unstable. |
| — | thread preamble | Turn 1 carries developer-message content codex adds on its own: one developer message whose three content items are `<skills_instructions>` (bundled `~/.codex/skills/.system/*`), `<permissions instructions>` and `<collaboration_mode>`, then `<multi_agent_role>` and `<multi_agent_mode>` messages, plus a user-role `<environment_context>` (rollout `…01a08d2c…` lines 2–5; `…01a08d43…` likewise). Not controllable from the prompt; comparability caveat (Claude Code adds its own system prompt too). |
| — | `codex doctor` | auth ✗; websocket ⚠ (HTTPS fallback works); Landlock ABI 7, seccomp, user namespaces available. |
| — | mid-run dead-turn causes exist in the binary | `usage_limit_reached`, `WorkspaceOwnerUsageLimitReached`, `WorkspaceOwnerCreditsDepleted` — a subscription run can hit its 5-hour/weekly limit partway through 5 high-effort attempts. |

### 3.2 UNVERIFIED — blocked on login

- **U1** Success-path stream: is the terminal event `turn.completed`, and
  does it carry usage? Does `turn.failed` ever carry usage (upstream
  knowledge says no ⇒ the failure path must read the rollout)? If the stream
  has none, the only source is the rollout's `event_msg` payloads — and do
  those payloads carry a `turn_id` (not every `event_msg` payload does: the
  recorded `thread_settings_applied` payloads have no `turn_id` key at all)?
  **Contingency:** if U1 comes back negative (no usage on `turn.completed`),
  the rollout would have to become the primary source for every leg, which
  contradicts §4.5's fallback-only stance and the step-3 acceptance — that
  outcome requires a v6 of this spec before Stage 2, not a quiet change.
- **U2** Usage semantics: is `cached_input_tokens` a subset of
  `input_tokens`? Is `cache_write_input_tokens` a subset too, and ever
  non-zero? Does `output_tokens` include `reasoning_output_tokens`? **On a
  resumed thread, is the reported usage this turn's or the thread total?**
  Are sub-agent tokens included? (`spawn_agent` exists; `codex features
  list` shows `multi_agent` stable and on by default, and the turn-1
  `<multi_agent_role>` preamble tells the model it may delegate — so
  delegation is possible even without `ultra`. Features are left at codex's
  defaults, as Claude Code's sub-agents are not disabled either.)
- **U3** Whether this account's `gpt-6-astra` returns `invalid_prompt`, and
  if it does, confirm from the stream that the turn carries **no non-error
  `item.*` and no usage** — i.e. that it classifies as *dead* under §4.6
  rule 2. That classification is reasoned (the server rejects before any
  output), not observed; if the account never hits it, the README says so.
- **U4** Whether `-m gpt-5.6-sol` works (control).
- **U5** Whether the sandbox executes tool calls on this box, how
  `pip`/`make test` behave inside it, and whether a background server the
  agent starts survives `codex exec` exiting (it would hold `:8003` against
  the runner's `make up`).
- **U6** `codex login status` when logged in: exit code, and which stream
  carries the `Logged in using …` line; whether `--ignore-user-config` leaves
  the ChatGPT login working.
- **U7** Whether codex's default `shell_environment_policy` excludes
  (`*KEY*`, `*SECRET*`, `*TOKEN*`) apply in `exec` mode.
- **U8** Whether `--ignore-user-config` also suppresses a global
  `$CODEX_HOME/AGENTS.md`, user skills under `$CODEX_HOME/skills/`, user
  hooks (`hooks/hooks.json`) and plugins (`plugins/`) — `codex features list`
  shows `hooks`, `plugins`, `apps`, `multi_agent` all stable and on. The help
  text names only `config.toml`; the binary reads all of these; none exist on
  this box today.

Unblock: `codex login --device-auth`, then `scratchpad/capture_codex_ok.sh`
records the 0.2 probe **and a resume leg** verbatim (two fixtures) and dumps
both turns' rollout usage payloads (0.4).

### 3.3 Verified by reading external sources (not executed here)

| Item | Result |
|---|---|
| `invalid_prompt` (openai/codex #43237, open, filed 2026-09-06) | gpt-6-astra with ChatGPT-subscription auth rejects prompts with exit **1** and error text "Invalid prompt: your prompt was flagged as potentially violating our usage policy…". The original post reproduces it **consistently** (even for `hi`, isolated `CODEX_HOME`, `--ignore-user-config`); a 2026-09-07 commenter reports it **intermittent** ("10 failures in ~40 min across projects", `high`/`xhigh`/`max`, while `gpt-5.6-sol` turns succeeded); 2026-09-08 commenters report it **account-scoped and total** ("began rejecting every prompt", Pro plan at 0 % usage, `sol` fine). So on a given account it may be absent, intermittent, or total. The OP's repro also passed `--disable multi_agent --disable apps --disable plugins`, which the adapter does not. No `--json` sample exists; it arrives as ordinary error text (the binary also carries a second wording, "Invalid prompt: we've limited access…"). Re-checked 2026-09-10: still open. |
| pricing | `https://developers.openai.com/api/docs/pricing` — the page also lists a "Fast mode" (priority) tier at 2× the standard rates; the adapter pins no service tier, so runs are priced as standard (Stage 2 may record `turn_context.service_tier` from the rollout to confirm). (openai.com/api/pricing 403s for bots; platform.openai.com/docs/pricing 301s here). Table header **Input · Cached input · Cache writes · Output**, tiers "Short context (≤272K input tokens)" / "Long context (>272K input tokens)". `gpt-6-astra` standard short: **$10.00 · $1.00 · $12.50 · $50.00** per 1M; long: $20 · $2 · $25 · $75. Tooltip: "Input tokens are either Input, Cached Input, or Cache Write and writes are not an additive fee." |

## 4. Design

### 4.1 Invocation

Both legs pass **the same resolved settings** so codex has no reason to
inject a `<permissions instructions>` message mid-thread (§3.1).

`start_build(worktree_dir, prompt, model, config)`:

```
codex exec --ignore-user-config -m <model> --json -C <worktree> \
  --sandbox workspace-write \
  -c 'model_reasoning_effort="<effort>"' \
  -c 'sandbox_workspace_write.network_access=true' \
  -
```

`continue_build(session_handle, followup_prompt)`:

```
codex exec resume <thread_id> --ignore-user-config --json -m <model> \
  -c 'model_reasoning_effort="<effort>"' \
  -c 'sandbox_mode="workspace-write"' \
  -c 'sandbox_workspace_write.network_access=true' \
  -
```

- Prompt / follow-up on **stdin** (`input=…`, `-` positional): byte-identical
  (verified) and closes the `<stdin>`-block hole.
- `cwd=worktree` on both subprocesses (resume has no `-C`; the cwd must be a
  git repo — `prepare_worktree` always `git init`s or clones).
- `-m`, effort, sandbox mode, network are re-passed on every resume (effort
  verified to reset otherwise; the rest for the same reason the Claude
  adapter replays `--model`).
- `-c` values are TOML: the argv element is literally
  `model_reasoning_effort="high"`. Argv list, never a shell string.
- `--ignore-user-config` on both legs (**D3**, §4.4).
- No `--skip-git-repo-check` (a non-repo cwd is a runner bug; fail loudly).
  No `--color` (stdout is JSONL; stderr is only used for bounded,
  ANSI-stripped diagnostic tails). No `--ephemeral`: the rollout is needed
  both for resume and as the failure-path usage source (§4.5).
- The adapter remembers `worktree_dir`, `model`, mapped `effort` from
  `start_build` (mirrors `ClaudeCodeHarness._worktree_dir/_model`).

### 4.2 Effort mapping

`config.effort` is **required** for `codex-cli`. Missing → `HarnessError`
before any subprocess.

| `config.effort` | codex `model_reasoning_effort` |
|---|---|
| `low` / `medium` / `high` / `xhigh` / `max` | same string |
| anything else — `ultra`, `minimal`, `none`, `""`, `"High"`, non-`str`, `None` | **`HarnessError`**, never passed through |

Explicit dict, not a pass-through: codex does no client-side validation.
`ultra` is excluded because it is outside the benchmark vocabulary and turns
on automatic sub-agent delegation, which changes what is measured.

### 4.3 Sandbox — decision D1 (network) and the filesystem asymmetry

`--sandbox workspace-write`, never `danger-full-access`. Network inside the
sandbox **on** (`-c sandbox_workspace_write.network_access=true`, verified):
the Claude adapter runs with `--dangerously-skip-permissions` (no sandbox,
full network), so leaving codex offline would handicap only one side's
tooling. Fixed in the adapter, not a YAML knob, so the matrix `config` stays
`{effort}` and the record's config slug is unchanged. This is a deviation
from the task's literal invocation (§4.12).

Filesystem asymmetry (accepted for v1, documented): inside the sandbox
`.git` is read-only and package installs must land in the worktree or
`/tmp`; Claude has neither restriction. Note the consequence of an
in-worktree venv: `capture_built_sha` runs `git add -A` and
`_push_built_tree` pushes the tree to the real target repo, so a venv the
model did not `.gitignore` ships with the build (same runner path for
Claude, but the codex sandbox makes in-worktree installs the likely
choice). The README recommends `/tmp` for installs. The prompt-mandated `make up` is
executed by the **runner on the host** (outside the sandbox), so it works if
the codex-built Makefile installs on the host or uses a venv it created in
the worktree. Smoke acceptance: the runner's `make up` starts the codex-built
service and `:8003` was free when codex exited (U5). `--add-dir` is not used.

Note what the sandbox does **not** contain: one step after every codex leg,
the **runner** executes the model-authored `Makefile` (`make up`, `make down`)
or compose file **on the host, unsandboxed, with the runner's full
environment** (`workspace.run_service` passes no `env=`). That is existing
behaviour, identical for the Claude harness, but it means the sandbox bounds
only what the agent does *during* its turn — see §4.10.

### 4.4 Environment and user config

`_subprocess_env()` = `os.environ` minus **`CODEX_API_KEY`** (live runtime
auth source — would silently switch to metered billing), **`CODEX_ACCESS_TOKEN`**
(read as an auth source), and **`OPENAI_API_KEY`** (task requirement; not
read at runtime by 0.154 but harmless and future-proof). **D2 is mandatory.**
`CODEX_HOME` is left untouched. Nothing is added. The pre-flight (§4.6)
runs under this same stripped env.

**D3: `--ignore-user-config` on both legs.** Verified offline that `-c`
overrides still apply and both legs run. A benchmark should not inherit the
operator's MCP servers, personality, model-provider or reasoning-summary
settings. Known: trust entries are still appended to `~/.codex/config.toml`
per worktree path (harmless; prune occasionally). Residual U6 is the smoke
run's first check; if it fails, drop the flag and record why.

### 4.5 Output parsing → `AttemptResult`

Parse stdout as JSONL (`encoding="utf-8", errors="replace"`): one
`json.loads` per non-empty line; lines that aren't JSON objects are kept
under `raw["unparsed_lines"]` (bounded: first 20, plus a total count; never
fatal).

Verified fields:

- `session_handle` = `thread_id` of the first `thread.started`. **If no
  `thread.started` was seen, `start_build` raises `HarnessError`** — there
  is nothing to resume. No fallback to `worktree_dir` as a handle: codex
  cannot resume by path.
- `wall_time_seconds` = monotonic clock around the codex subprocess, as
  Claude. All legs of a retried attempt count (codex was running); the 5 s
  pauses between retries and the pre-flight `login status` do **not** (they
  are harness overhead, not codex time — keeps the number comparable to the
  Claude adapter's).
- `reported_cost_usd = None` (codex reports no cost figure).

Usage — **the mapping rule is provisional until both success fixtures exist
(U1/U2)**; only the shape below is committed:

- `raw["usage_source"] ∈ {"stream", "rollout", "none"}`. Primary: the usage
  object on the stream's terminal event (expected `turn.completed`, U1).
  **Failure path (`turn.failed`, expected to carry no usage): read the
  rollout** (`$CODEX_HOME`, default `~/.codex`,
  `sessions/**/rollout-*-<thread_id>.jsonl`): the last usage-bearing
  `event_msg` (the binary names both `last_token_usage` and
  `total_token_usage`; which to read is settled by the fixture pair, U2)
  belonging to this leg's turn — matched by `turn_id` if the payload carries
  one, else **positionally** (payloads after the last `turn_context` line);
  `"rollout"`. Neither ⇒ tokens `0`, `"none"`. The rollout is an unstable
  format (§3.1), hence fallback-only and always marked. The raw usage object
  is stored verbatim in `raw["usage"]`.
- **A completed turn always has usage.** Terminal `turn.completed` with
  `usage_source == "none"` ⇒ `HarnessError` — that is a parser defect or a
  CLI format change, never a codex state, and recording `0` tokens / `$0.00`
  for a real success would be silent mis-recording. Capture-with-zeros is
  allowed only for the non-dead **failure** path (§4.6 rule 5), with a
  console warning.
- Per-turn vs. thread-total (U2): if the reported usage on a resumed thread
  is cumulative, the adapter records the delta against the previous leg's
  total (kept on the instance) and documents it the way `_usage_from`
  documents `modelUsage`. Determined from the start+resume fixture pair.
- `cached_input_tokens` → `cache_read_tokens`; `cache_write_input_tokens` →
  `cache_creation_tokens` (never dropped — `pricing.yaml` carries the $12.50
  write rate and writes are billed *instead of* the input rate, which is how
  `runner/pricing.py` partitions input vs. cache-write tokens). If both are
  subsets of `input_tokens` (API convention): `input_tokens = input − cached
  − cache_write`; if disjoint: `input_tokens = input`. A negative result ⇒
  `HarnessError` (assumption broken — loud).
- `output_tokens` → `output_tokens`; whether `reasoning_output_tokens` is
  already included is decided from the fixture.
- No cache counts at all ⇒ both cache fields `0` and
  `raw["cache_tokens_reported"] = False` (site shows `0`, record says why).

`raw` (bounded; the full transcript lives in codex's rollout):
`{"thread_id", "exit_code", "auth_mode", "usage", "usage_source",
"final_event", "event_counts": {type: n}, "errors": [first 20 messages] +
"errors_total", "effort", "model", "cache_tokens_reported",
"harness_error"?, "dead_turn_retries", "dead_turn_errors": [first 20 messages
from the dead legs] + "dead_turn_errors_total", "rollout_path": str|None,
"unparsed_lines": [first 20] + "unparsed_total", "stderr_tail": str (last
2000 chars, ANSI-stripped)}`.
Plain JSON types only; must round-trip through `RunRecord.write`.

### 4.6 Failure semantics — decision D4 (symmetric)

Task text: "Handle wall time and exit codes the same way as the Claude
adapter. A non-zero exit or an `invalid_prompt` error is an attempt failure
with the error text captured, not a crash of the whole run."

The Claude adapter raises on any non-zero exit; the runner does not catch it,
so the run dies with a traceback and **no record, no site rebuild, no push**.
Capturing *every* failure (v1) — or every failure after attempt 1 (v2) —
would let a turn in which the model never ran (logged out, usage limit hit
mid-run, credits depleted, OAuth refresh failure, provider outage) be written
as `passed=false, attempts=5`, published by `_rebuild_site`, and pushed by
`_push_built_tree` as a `bench/…` branch — indistinguishable in the site's
pass-rate summary from a real 5-attempt model failure (the site's attempts
statistics are computed over passing runs only, so the harm is the pass rate
and the push, not the attempts column). v3 classifies **structurally, never by matching
error text**, and treats "the model never ran" the same on every attempt:

1. **Pre-flight (start_build only):** `codex login status` under the
   stripped env, own 30 s timeout, stdout **and** stderr captured (the
   `Not logged in` line is on stderr). Require exit `0` **and** the substring
   `Logged in using ChatGPT` in either stream; anything else (`Not logged
   in`, `Logged in using an API key - …` from a stored `auth.json` key,
   access token, timeout) ⇒ `HarnessError` naming the mode seen, before codex
   touches the worktree. A missing `codex` binary raises `FileNotFoundError`
   from the subprocess call and is **not** wrapped — exactly as the Claude
   adapter; `__main__.main` already prints it as `error: …` with exit 2. Mode line recorded in `raw["auth_mode"]` of attempt 1.
2. **Run the leg.** If the stream has **no `thread.started`** at all ⇒
   `HarnessError` immediately, no retry (nothing to resume, nothing to
   classify; test 9 asserts a single call). Otherwise a turn is **dead** when
   it ended without model activity: no `item.*` event whose
   `item.type != "error"` **and** no usage reported (stream or rollout).
   Auth/transport failures and `invalid_prompt` on an untouched prompt are
   dead; "wrote half the code then the backend 500'd" is not. A turn whose
   terminal event is `turn.completed` is **never** dead — rule 8 governs it.
3. **Dead turn ⇒ retry the identical leg** after a 5 s pause, up to
   `CODEX_DEAD_TURN_RETRIES` times (default **2**; **D5**). Rationale:
   transient transport/backend failures cost no tokens to retry, and a
   retry avoids consuming an `attempt` for something the model never saw.
   A deterministic `invalid_prompt` costs ≤ 2 dead legs (~15 s each with
   codex's own reconnects) before failing loudly. Dead-leg wall time is
   counted; `raw["dead_turn_retries"]` and the dead legs' error messages
   (`raw["dead_turn_errors"]`, bounded) are recorded on the attempt that
   eventually returns — the abandoned threads are not otherwise referenced. Side effects: a dead *start* leg leaves an
   abandoned thread (new `thread_id` on retry); a dead *resume* leg leaves
   the follow-up user message in the thread history (the model may see it
   up to three times).
4. **Still dead after retries ⇒ `HarnessError` on any leg** (Claude parity).
   The worktree, the codex rollout, and the console output of earlier
   attempts survive on disk; what is *not* produced is a misleading record
   and a pushed branch. This is the one place `invalid_prompt` crashes the
   run; the message tells the operator to rerun or switch model.
5. **Non-dead failure** (model activity, then `turn.failed` / non-zero exit)
   ⇒ **captured**: usage from the rollout (§4.5), `session_handle` =
   thread_id, `raw["harness_error"]` = the `turn.failed` message (else the
   last `error` event's message, else the ANSI-stripped stderr tail, else
   `"codex exited N"`), `raw["exit_code"]`.
   The runner grades whatever was written and resumes the thread. This is
   the case the task's "attempt failure, not a crash" sentence protects.
6. **Timeout** (`CODEX_BUILD_TIMEOUT_SECONDS`, default 3600) ⇒
   `Popen(start_new_session=True)`; on expiry `os.killpg` SIGTERM → wait
   10 s → SIGKILL (as `workspace._kill_group`), then a second
   `communicate(timeout=10)` to drain and close the pipes (a grandchild that
   escaped the group could otherwise hold stdout; a second `TimeoutExpired`
   is caught and the pipes are closed regardless) ⇒ `HarnessError`, as
   Claude. **Any other exception** out of `communicate` (a `KeyboardInterrupt`
   above all — the child is in its own process group, so the terminal's
   SIGINT never reaches it) also kills the group and closes the pipes before
   re-raising unwrapped; otherwise an interrupted benchmark would orphan a
   live agent for up to the timeout.
7. Exit `0` with a `turn.failed` terminal event, or with no terminal event,
   is a failure per rules 2–5 (exit codes are advisory; the stream is the
   truth).
8. **`turn.completed` with no usage from any source ⇒ `HarnessError`**
   (§4.5). A success is never recorded with zero tokens.

Consequences of a *captured* failure (unchanged runner): the FAIL record is
written, the site rebuilt, the possibly-partial tree pushed as `bench/…` —
exactly as a Claude FAIL run today. `raw.harness_error` and the console line
make the cause visible.

### 4.7 Wiring

- `harnesses/__init__.py`: `HARNESSES["codex-cli"] = CodexCliHarness`,
  exported in `__all__`. `HarnessError` is **imported from `claude_code`**
  (one class; the Claude adapter cannot be modified to move it).
- `__main__.py`: import `HARNESSES` and list `sorted(HARNESSES)` in the
  `--harness` help string. No `choices=`. Note: `--dry-run` validates the
  **whole** matrix and ignores `--harness/--model/--config`; the task's
  dry-run command still passes and covers both rows.
- Other "claude-code is named here" touchpoints to update: `runner/run.py`
  module docstring, README "Layout" and "CI never runs a real benchmark"
  lines, `tests/fakes.py` docstring, `.github/workflows/ci.yml` comment.
  **Stage 2, prose only (column set untouched):** `templates/base.html`
  footer "(runs execute on the Claude CLI's subscription auth)" and
  `templates/run.html` "The full `claude -p --output-format json` response",
  and `runner/results.py`'s `AttemptRecord.raw` docstring ("full JSON …
  preserved verbatim") become false once a codex record exists.
- `pricing.yaml`, OpenAI block:
  `gpt-6-astra: {input: 10.00, output: 50.00, cache_write: 12.50, cache_read: 1.00}`
  with a comment: source URL + date (§3.3), standard tier ≤272K input
  tokens (long-context tier higher, not modelled), and that OpenAI now
  publishes a **cache-write** rate for this model, so the block's older
  "cache_write: 0 for OpenAI" note becomes "for models without a published
  write rate".
- `benchmarks/eval-chess-backend.yaml`: append
  `{harness: codex-cli, model: gpt-6-astra, config: {effort: high}}` as the
  task specifies. **D6:** the Claude row is `effort: xhigh`; both models
  accept `xhigh`. Default: add the row as specified and say so in README.
- Dry run must pass:
  `python -m pagehub_benchmarks run eval-chess-backend --harness codex-cli --model gpt-6-astra --config effort=high --dry-run`.

### 4.8 Tests (no real `codex` in CI)

`tests/test_codex_cli.py`, faking the subprocess layer (capture argv, `cwd`,
`env`, `input`, timeout; return scripted stdout/stderr/returncode; the fake
must let a test assert process-group kill and drain; `time.sleep` patched):

1. **start argv, byte-exact:** `["codex","exec","--ignore-user-config","-m",
   model,"--json","-C",wt,"--sandbox","workspace-write","-c",
   'model_reasoning_effort="high"',"-c",
   "sandbox_workspace_write.network_access=true","-"]`; `input` == prompt
   verbatim (multi-line, quotes, braces, non-ASCII); `cwd == wt`.
2. **resume argv, byte-exact:** `["codex","exec","resume",thread_id,
   "--ignore-user-config","--json","-m",model,"-c",
   'model_reasoning_effort="high"',"-c",'sandbox_mode="workspace-write"',
   "-c","sandbox_workspace_write.network_access=true","-"]`; no `-C`, no
   `--sandbox`; `cwd == wt`; `input` == follow-up verbatim.
3. **effort:** all five map; `ultra`, `minimal`, `bogus`, `"High"`, `None`,
   `1`, missing ⇒ `HarnessError`, no subprocess call.
4. **env:** the three vars absent; `PATH`, `CODEX_HOME` preserved; nothing
   added; **the pre-flight call also ran under the stripped env**.
5. **pre-flight:** rc 1 `Not logged in` (on stderr) ⇒ raise; rc 0 `Logged in
   using an API key - …` ⇒ raise (no exec call in either); rc 0 `Logged in
   using ChatGPT` ⇒ the exec leg is invoked and, on a stream that returns,
   `raw["auth_mode"]` is set — in Stage 1 the returning stream is a
   **labelled synthetic non-dead failure** (the real 401 envelope plus one
   non-error `item.completed`, then `turn.failed`; no rollout ⇒ captured with
   zeros and `usage_source == "none"`), because the dead fixture raises and
   any `turn.completed` hits rule 8 until the parser is calibrated;
   `FileNotFoundError` propagates unwrapped (as Claude); pre-flight timeout
   ⇒ `HarnessError`.
6. **token parsing** (after smoke): exact numbers from
   `codex_exec_ok.jsonl` **and** `codex_exec_resume_ok.jsonl` per the
   confirmed rule; `session_handle` == fixture thread_id; `raw["usage"]`
   verbatim; `usage_source == "stream"`; `cache_tokens_reported`.
7. **dead-turn path** on `codex_exec_unauthenticated.jsonl` + rc 1: on
   `start_build` **and** on `continue_build` (with the resume fixture),
   the leg is invoked `1 + CODEX_DEAD_TURN_RETRIES` times, `sleep(5)` between,
   then `HarnessError` whose message contains `401 Unauthorized`. **7b:** a
   dead start leg followed by a captured leg keeps the dead leg's messages in
   `raw["dead_turn_errors"]` (+ total) on the returned attempt.
8. **non-dead failure** (after smoke — needs the rollout usage shape):
   labelled synthetic stream = the real failure envelope plus a non-error
   `item.completed`, then `turn.failed`, with a recorded rollout under a
   temp `CODEX_HOME` ⇒ captured, no retry, `usage_source == "rollout"`,
   tokens counted, `raw["harness_error"]` set.
9. **no `thread.started`** ⇒ `start_build` raises after exactly **one**
   subprocess call (no dead-turn retry).
10. **timeout** ⇒ SIGTERM then SIGKILL to the group, drain attempted,
    `HarnessError`; a second case where the group exits on SIGTERM asserts
    no SIGKILL follows. **10b (interrupt):** a `KeyboardInterrupt` out of
    `communicate` ⇒ SIGTERM to the group, pipes closed, the interrupt
    re-raised unwrapped (never a `HarnessError`), no drain attempt.
11. **parser:** non-JSON chatter ⇒ `unparsed_lines` bounded + count; exit 0 +
    `turn.failed` ⇒ failure; **`turn.completed` with no usage anywhere ⇒
    `HarnessError`**; `errors` bounded + count; stderr tail bounded (2000)
    and ANSI-stripped.
12. **attempt-1→2 chain** (after smoke) through `execute_benchmark_run` with
    the fake returning `codex_exec_ok.jsonl` then `codex_exec_resume_ok.jsonl`:
    resume argv carries fixture 1's `thread_id`; per-attempt tokens are
    per-turn (delta applied if cumulative); `RunRecord.write` round-trips
    `raw`.
13. **registry:** `get_harness("codex-cli")` is a `CodexCliHarness`; unknown
    name lists both. **Dry run** (Stage 2, with the matrix row):
    `dry_run_report` with the two-row `eval-chess-backend` matrix passes.
14. `continue_build` before `start_build`, and with `""` ⇒ `HarnessError`.

`make test` and `make lint` green at each stage (§6).

### 4.9 Docs

README → Usage → "Codex CLI harness": required version (≥ 0.153.0; tested
0.154.0); `codex login` (`--device-auth` for headless) and that runs require
the **ChatGPT** login mode (a stored API key is refused); effort **required**
in YAML and why; sandbox = workspace-write + network on, `.git` read-only and
installs confined to the worktree/`/tmp` (asymmetry vs. Claude); what tokens
are and aren't reported (from the fixtures; cache `0` +
`cache_tokens_reported:false` when absent; `usage_source`);
`CODEX_BUILD_TIMEOUT_SECONDS`, `CODEX_DEAD_TURN_RETRIES`;
`--ignore-user-config` and the trust-entry accumulation; codex's own
preamble as a comparability caveat; when a run crashes vs. records (§4.6);
the `effort: high` vs `xhigh` matrix note; that the runner executes the
built `Makefile` on the host outside any sandbox (both harnesses); `cost_usd`
computed from `pricing.yaml`, not billed. `.env.example` gets both knobs.

### 4.10 Security — what does an attacker who controls X get?

| Actor controlled | Gets | Mitigation / status |
|---|---|---|
| **The model** (its tool calls) | Write: worktree (minus `.git`), `/tmp`, `$TMPDIR`. **Read: the entire filesystem** (`~/.ssh`, `~/.codex/auth.json`, `~/.claude/`, any `.env`). Network egress (D1). Subprocess env minus the three auth vars; codex's default `shell_environment_policy` additionally filters `*KEY*`/`*SECRET*`/`*TOKEN*` names from the agent's shell (U7), which Claude Code does not. ⇒ read-anything + egress is an exfiltration path. Can start background processes; whether they outlive `codex exec` is U5. **And, one step later, arbitrary unsandboxed execution as the runner user with the runner's full environment** (`PAGEHUB_EVALS_TOKEN`, `PAGEHUB_BROWSER_ADMIN_TOKEN`, `GATEWAY_AUTH_TOKEN`, …): the runner runs the model-authored `make up` / `make down` / compose file on the host with no `env=` filtering — U7 does not apply there. | During the turn: same or narrower than the Claude harness (no sandbox). After the turn: identical to Claude — the host-side `make up` is existing runner behaviour for every harness. Stated plainly in README; changing it is a runner change out of scope. |
| **Target repo contents** (`AGENTS.md`, `.codex/`, `.rules` for a non-empty `target_start`) | Instructions/rules codex would load from the checkout. | Moot today: all four benchmarks are `target_start: empty`. `--ignore-rules` exists if a non-empty start is ever added. |
| **Operator's shell env** | Inherited minus the three vars; a `CODEX_API_KEY` would have switched billing silently. | Stripped. |
| **Operator's stored login** (`auth.json`) | An API-key login would pass an exit-code-only check and bill the API. | Pre-flight requires `Logged in using ChatGPT`. |
| **Operator** (runs it) | Spends subscription allowance; trust entries appended to `~/.codex/config.toml`; a captured FAIL pushes a `bench/…` branch (existing behaviour); a mid-run usage-limit exhaustion **crashes** the run (no record, no push) rather than laundering it; their shell secrets reach the model-authored `Makefile` via the host-side `make up` (existing behaviour, both harnesses). | Documented; no real run without go-ahead; run the benchmark from a shell without unrelated secrets. |
| **CI** | Nothing: fixtures are inert JSONL; subprocess is faked; no network. | Enforced by the tests' fake layer. |

No authn/authz or twin-override surface is added; the harness makes no HTTP
calls of its own.

### 4.11 Smoke plan (needs operator go-ahead; burns subscription allowance)

Each step is cheap and gates the next. Back up `~/.codex/config.toml` first.

1. `codex login status` ⇒ exit 0 + `Logged in using ChatGPT` (U6; note
   which stream). Run `scratchpad/capture_codex_ok.sh`: records the exact
   0.2 probe **verbatim** to `tests/fixtures/codex_exec_ok.jsonl`, then a
   resume leg on the same thread to `tests/fixtures/codex_exec_resume_ok.jsonl`,
   and dumps both turns' rollout usage payloads (U1/U2, incl. per-turn vs.
   cumulative). These two fixtures are recorded with the task's exact probe
   command, **not** the adapter's flags — they fix the event *shape*; the
   adapter's flags are exercised in steps 2 and 3. If `invalid_prompt`: the script runs the `gpt-5.6-sol`
   control and stops (U3/U4) — report and wait.
2. One probe turn in a throwaway git dir **with the exact adapter flags**;
   prompt: "run `printenv | sort`; try to create `/tmp/x` and `$HOME/x`; run
   `pip --version`; start `python3 -m http.server 8003 &` and finish".
   Verifies U5 (incl. whether `:8003` is still held after codex exits), U6,
   U7 and the filesystem profile. Before this step, drop a marker
   `$CODEX_HOME/AGENTS.md` and a marker user skill and ask the model whether
   it sees them (U8); remove both afterwards. Then one resume on that
   thread; its rollout turn must show **no** `<permissions instructions>`
   injection.
3. The single-attempt benchmark smoke:

```
python -m pagehub_benchmarks run eval-chess-backend \
  --harness codex-cli --model gpt-6-astra --config effort=high \
  --max-attempts 1 --no-build-site \
  --results-dir /tmp/pagehub-benchmarks-smoke
```

`--results-dir` keeps the smoke record out of committed `results/`; drop it
and `--no-build-site` for a real recorded run. **The push is not optional:**
`_push_built_tree` pushes the built tree to the real
`pagehub-io/eval-chess-backend` as `bench/codex-cli/gpt-6-astra/effort-high/<ts>`
(and to its default branch if the target is empty and the smoke passes),
exactly as every Claude run does. Needs pagehub-evals on `:8002` and `:8003`
free. Acceptance: the runner's host-side `make up` starts the
codex-built service; `raw.usage` matches the fixture-derived rule;
`raw.usage_source == "stream"`; the pushed tree contains no venv or
`site-packages`.

### 4.12 Deliberate deviations from "mirror claude_code.py one-for-one"

Each is required by a verified codex behaviour or a review finding:
prompt on stdin (§4.1); `--ignore-user-config` (D3); network enabled inside
the sandbox and the extra `-c` argv elements (D1); effort required (§4.2);
pre-flight ChatGPT-mode login check (D4 rule 1); dead-turn retry (D5);
symmetric raise on dead-after-retries and capture of non-dead failures
(D4 — the Claude adapter raises on *every* non-zero exit; codex captures the
ones where the model actually ran, per the task); process-group kill and
drain on timeout (§4.6 rule 6); no `worktree_dir` fallback for the session
handle (§4.5); three env vars stripped, not one (D2); `HarnessError`
imported from `claude_code` rather than redefined; `pricing.yaml` carries a
fourth number, `cache_write: 12.50`, beyond the task's three (verified on the
live page, §3.3); a completed turn with no usage raises rather than recording
zeros (§4.6 rule 8); `raw` is a **bounded summary** (§4.5) rather than the
Claude adapter's verbatim CLI JSON — `AttemptRecord.raw`'s "back-fill
insurance" is preserved for what matters (the verbatim usage object plus
`rollout_path` to the full transcript, which codex keeps on disk in an
unstable format); codex feature flags left at defaults (`multi_agent` on),
mirroring the Claude adapter, which does not disable Claude Code's sub-agents. Everything else — helper
layout, `_subprocess_env`, `_build_timeout`, `_run`, remembered state,
`AttemptResult` fields — mirrors the Claude adapter.

## 5. Decisions for the operator (defaults in bold)

- **D1** network inside workspace-write: **on**.
- **D2** strip `CODEX_API_KEY` + `CODEX_ACCESS_TOKEN` + `OPENAI_API_KEY`: **mandatory**.
- **D3** `--ignore-user-config` on both legs: **yes** (U6 on smoke).
- **D4** failure semantics: **pre-flight ChatGPT-mode check; dead-after-retries raises on any attempt; non-dead failures captured** (§4.6). Alternative rejected: capture dead turns after attempt 1 (v2) — launders mid-run auth/limit failures into FAIL records and pushes.
- **D5** dead-turn retries: **2**, 5 s apart, env-tunable.
- **D6** matrix row effort: **`high` as the task specifies**; mismatch with Claude's `xhigh` documented.
- **D7** staging: **Stage 1 ships the adapter registered but without the matrix row**; the row lands in Stage 2 with the calibrated parser (§6). Registration alone cannot spend tokens — only a YAML row selects a harness — so no guard is needed and the unfiltered `make run BENCHMARK=eval-chess-backend` is unaffected during Stage 1. The one exception is an operator-authored YAML path carrying a `codex-cli` row: in Stage 1 a real success then raises under rule 8 (tokens spent, but never a `$0` record) and a non-dead failure records zeros with the warning.

## 6. Implementation sequencing

Tests 6, 8, 12 and the two-row dry run need fixtures that only a logged-in
smoke can record. To avoid inferring the event shape from docs or memory,
and to leave the default `make run` path untouched in the meantime (**D7**):

- **Stage 1 (now):** `codex_cli.py` with everything above except the usage
  mapping rule; `_usage_from(...)` is a single isolated function that, until
  the fixtures exist, finds no usage and returns `usage_source="none"` (the
  real behaviour for the failure fixtures we have; a completed turn would hit
  rule 8 and raise — never a zero-token success). **Registered** in
  `HARNESSES` and listed in the `--harness` help; **no matrix row** yet, so
  nothing can select it — `python -m pagehub_benchmarks run eval-chess-backend
  --harness codex-cli` fails fast with the existing "no harness in … matched"
  `ConfigError`, spending nothing, and the unfiltered `make run` still runs
  only the Claude row. Pricing entry, `.env.example` knobs, README section
  (marked "pending calibration" where token reporting is described), tests
  1–5, 7, 9–11, 13 (registry half), 14. `make test` / `make lint` green. PR
  review round on this diff.
- **Stage 2 (after login + §4.11 steps 1–2):** commit both success fixtures
  verbatim; implement the confirmed mapping in `_usage_from` (if usage turns
  out cumulative across a thread, the function gains a `previous_total`
  argument fed from instance state — its Stage-1 signature is not final); add the matrix
  row; tests 6, 8, 12, 13 (dry-run half); the task's dry-run command passes
  for both rows; finish README's "what is reported"; then §4.11 step 3. PR
  review round on the diff.
