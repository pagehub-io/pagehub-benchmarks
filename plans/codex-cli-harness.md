# Tech spec — Codex CLI harness (`codex-cli`, GPT-6 Astra) for pagehub-benchmarks

Status: **APPROVED v5** (gate passed round 5: 0 critical, 0 important; nits swept in this text) — 2026-09-10. Amended after PR #29 review round 1 (raw keys, rule 3/5 wording, pricing tier note, Stage-2 prose list). **Amended 2026-09-11 for Stage 2:** U1–U8 answered by execution (§3.4; U3 only as "not observed today"; U8's hooks/plugins/rules halves not exercised — none exist on the development box), usage mapping finalised (§4.5), three verified findings folded in — the stream's usage is the thread total; operator `AGENTS.md`/skills are not suppressed (pre-flight guard, rule 1); codex exposes the runner's secret-named env vars and writes login-shell snapshots to disk (§4.4 env policy, §4.10). **Amended again after PR #29 review round 3:** throwaway HOME moved out of the sandbox's writable roots and now also restores `PATH`; guard extended to `AGENTS.override.md` / `instructions.md`; resume legs must report the handle's thread id; `rate_limits` trimmed; `cache_tokens_reported` reflects the field's presence; §4.8 lists the new tests. **And after round 4 (passed, nits swept):** profile appends codex's own PATH after the runner's; HOME-base guard compares resolved paths; `BASH_ENV`/`ENV`/`ZDOTDIR` stripped; login vs non-login shells documented (§3.4); §4.11 step 3 folded into the first real run. **After rounds 5–6:** cache writes pinned on start, resume and failure paths (§4.8 item 18); remaining surviving mutants listed there. Review history: round 1 on v1 (1 critical,
11 important, 10 nits) → v2; round 2 on v2 (0 critical, 5 important, 10 nits)
→ v3; round 3 on v3 (0 critical, 2 important, 11 nits) → v4; round 4 on v4
(0 critical, 2 important — both caused by v4's Stage-1 guard, now removed —
7 nits) → v5. Every critical/important finding from all rounds is addressed
below;
factual corrections were re-verified by execution on this box. **Stage 2 is
done:** preflight items 0.2 and 0.4 were unblocked by `codex login`, the
success fixtures and the session rollout under `tests/fixtures/` were captured
from real turns, U1–U8 are answered in §3.4, and the usage parser is coded and
calibrated against those fixtures (§4.5). **Amended after PR #29 review round
7:** a leg whose usage no source can supply is recorded as missing rather than
free, and a rollout-sourced leg re-anchors the thread total on codex's own
`thread_token_usage` (§4.5) — *the second half is superseded: round 9 left no
rollout-sourced leg to re-anchor anything, and the cumulative now repairs the
baseline directly on every leg*. **Amended after PR #29 review rounds 8–9:**
round 8 turned the per-case fixes into a class-level *marking* invariant
(`usage_faithful` / `usage_caveats`, §4.5), and round 9 **deleted
rollout-based per-attempt usage recovery** — no rollout turn record is ever
attributed to a leg, because neither the turn id nor codex's cumulative
proves a record *belongs* to one; a leg's own figure is the stream delta or
nothing. The rollout keeps three jobs and no others (cumulative baseline
repair, dead-leg evidence, rate limits) and the caveat vocabulary holds only
values the code can actually produce (§4.5, §4.8 item 19). **Amended after
review round 11:** that vocabulary gained a third such value,
`"dead_leg_unmeasured"`, for an attempt whose figure is short by a thread a
dead start leg abandoned — previously published as `usage_faithful: true`
and therefore unflagged on the results site (§4.5, §4.8 item 19).
**Amended after review rounds 12–16** (this log ended at round 11 while the
body already cited four later rounds — corrected in round 16): round 12
extended lower-bound marking from the attempt row to every aggregate surface
(§4.5); round 13 added the statement-to-test table and its anti-drift guard
(§4.5); round 14 pinned both conjuncts of the absorbed-without-missing
premise and widened the citation contract to README (§4.5, §4.8); round 15
put `templates/` inside that contract, added the "what the guard does and
does not certify" paragraph, and fixed the half-reset `start_build` left
behind on its failure path (§4.4, §4.5); round 16 pinned the reset's
POSITION at every raise site before the `try` (§4.4, §4.8), pinned
`_marks.html`'s `lb_title` — the run-total rule as the index, benchmark and
theory pages state it — and gated the per-attempt caveat legend so it does
not mark the 27 published claude-code runs, none of which has a caveated
attempt (§4.5).

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
| 0.2 | `codex login status` | `Not logged in`, exit **1** at the time of this probe — the OK probe was blocked. Re-run after `codex login`: `Logged in using ChatGPT`, exit 0; `tests/fixtures/codex_exec_ok.jsonl`, `…_resume_ok.jsonl` and `codex_rollout_ok.jsonl` were captured then. Binary carries the mode lines this command prints: `Logged in using ChatGPT`, `Logged in using an API key`, `Logged in using access token`, `Logged in using personal access token`, `Logged in using workload identity`, `Logged in using Amazon Bedrock …`, `Not logged in`. |
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

### 3.2 Open questions while login was blocked (all answered in §3.4)

Kept as written for the record — these were the unknowns the usage parser
was not allowed to guess at. Every one was settled by execution once
`codex login` succeeded; read §3.4 for the answers.

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
  **Answered in §3.4, and its premise superseded (round 9):** `turn.completed`
  does carry usage, so the contingency never fired — and "the failure path
  must read the rollout" is no longer true either. §4.5 now takes a
  *no-fallback* stance, not a fallback-only one: a failed leg records zeros
  marked `usage_missing`, and no rollout turn record is ever attributed to a
  leg. Kept for the history of what was asked.
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

Unblock: `codex login --device-auth`, then capture the 0.2 probe **and a
resume leg** verbatim (two fixtures) and dump both turns' rollout usage
payloads (0.4). *Historical: this was done in Stage 2 by a throwaway
`scratchpad/capture_codex_ok.sh`, which was never committed and is not in the
repo — the fixtures it produced are, under `tests/fixtures/`.*

### 3.3 Verified by reading external sources (not executed here)

| Item | Result |
|---|---|
| `invalid_prompt` (openai/codex #43237, open, filed 2026-09-06) | gpt-6-astra with ChatGPT-subscription auth rejects prompts with exit **1** and error text "Invalid prompt: your prompt was flagged as potentially violating our usage policy…". The original post reproduces it **consistently** (even for `hi`, isolated `CODEX_HOME`, `--ignore-user-config`); a 2026-09-07 commenter reports it **intermittent** ("10 failures in ~40 min across projects", `high`/`xhigh`/`max`, while `gpt-5.6-sol` turns succeeded); 2026-09-08 commenters report it **account-scoped and total** ("began rejecting every prompt", Pro plan at 0 % usage, `sol` fine). So on a given account it may be absent, intermittent, or total. The OP's repro also passed `--disable multi_agent --disable apps --disable plugins`, which the adapter does not. No `--json` sample exists; it arrives as ordinary error text (the binary also carries a second wording, "Invalid prompt: we've limited access…"). Re-checked 2026-09-10: still open. |
| pricing | `https://developers.openai.com/api/docs/pricing` — the page also lists a "Fast mode" (priority) tier at 2× the standard rates; the adapter pins no service tier, so runs are priced as standard (Stage 2 may record `turn_context.service_tier` from the rollout to confirm). (openai.com/api/pricing 403s for bots; platform.openai.com/docs/pricing 301s here). Table header **Input · Cached input · Cache writes · Output**, tiers "Short context (≤272K input tokens)" / "Long context (>272K input tokens)". `gpt-6-astra` standard short: **$10.00 · $1.00 · $12.50 · $50.00** per 1M; long: $20 · $2 · $25 · $75. Tooltip: "Input tokens are either Input, Cached Input, or Cache Write and writes are not an additive fee." |

### 3.4 Resolved on 2026-09-11 (after `codex login`; ChatGPT Plus, 5h/weekly usage 0% at start)

| # | Result |
|---|---|
| U1 | `turn.completed{usage:{input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens, reasoning_output_tokens}}` on the stream (`tests/fixtures/codex_exec_ok.jsonl`); the rollout additionally writes `token_usage_record{usage, turn_token_usage, thread_token_usage, turn_id}` and `event_msg token_count{info:{total_token_usage,last_token_usage}, rate_limits}` (`codex_rollout_ok.jsonl`, message bodies removed). A `turn.failed` was not observed with usage (unchanged assumption; **round 9 deleted the rollout fallback** — a failed leg now records zeros marked `usage_missing`, §4.6 rule 5). |
| U2 | **The stream's usage on a resumed thread is the thread total** (resume fixture: 31478 = 15359 + 16119 input; matches `thread_token_usage`) ⇒ per-leg delta on the instance. `total_tokens == input_tokens + output_tokens` on both turns ⇒ cached input is a subset of input (cache writes were 0 in every recording; treating them as a subset too rests on OpenAI's pricing tooltip, not on data); `output_tokens` includes reasoning. `cache_write_input_tokens` was 0. Sub-agent tokens: unobserved (delegation is off by default — see below). |
| U3/U4 | No `invalid_prompt` on this account today (both Astra turns completed). `gpt-5.6-sol` works (used for the CLI-mechanics probes to spare Astra allowance). |
| U5 | Sandbox executes commands: `/tmp` writable, `$HOME` read-only, `pip` and network work, a backgrounded `http.server 8003` did **not** outlive `codex exec`. |
| U6 | `codex login status` → `Logged in using ChatGPT` on **stderr**, exit 0; `--ignore-user-config` leaves the login working on both legs. |
| U7 | **Negative.** No default env filtering in exec mode: the agent listed 37 secret-named variables from the runner's shell (Stripe/JWT/Cloudflare/Supabase production credentials, a PEM private key exported as a variable). Root cause: they are exported by `~/.bashrc`, and codex runs commands with `bash -lc`. `-c shell_environment_policy.exclude=[…]` alone changed nothing; `--disable shell_snapshot` + `experimental_use_profile=false` + the exclude list hides every matching variable **inherited from the runner** and stops the snapshot files, but `~/.bashrc`'s own exports still reach the agent. Codex also writes `$CODEX_HOME/shell_snapshots/<thread>.sh` (mode 644) containing the full login-shell environment with values — 8 such files existed after the probes. |
| U7b | **Complete mitigation found:** give the codex subprocess a throwaway `HOME` (empty temp dir) with `CODEX_HOME` pinned to the real login directory. `bash -lc` then sources an empty profile: the same probe reported **NONE** secret-named variables (bashrc exports and inherited ones alike), login still worked via `CODEX_HOME`, `pip` and network still worked, no snapshot written. (First attempt failed harmlessly before any network call — `CODEX_HOME="$HOME/.codex"` expanded against the new HOME; codex refuses a non-existent codex home.) |
| Locale | `codex exec` forces `LANG`/`LC_ALL`/`LC_CTYPE=C.UTF-8` (plus `NO_COLOR=1`, `PAGER=cat`, `TERM=dumb`) for the agent's commands; `-c shell_environment_policy.set.LC_ALL=…` does **not** override it. This box's PATH resolves `bash` to linuxbrew bash 5.3, which cannot load `C.UTF-8`, so every pyenv shim (`python3`, `pip`, `pytest` are bash scripts) printed ~20 `setlocale` warnings into the agent's command output (21 for one `pip --version`). The throwaway HOME therefore carries one `.bash_profile` line exporting the runner's own locale (`LC_ALL` or `LANG`), which `/bin/bash -lc` reads after codex's injection: 39 → 0 warnings for pip + pytest (simulated), 0 in the real adapter run below. |
| Adapter smoke | The real adapter (`get_harness("codex-cli")`), real codex, `gpt-5.6-sol` at `low`, start + resume in a git-init'd scratch worktree: pre-flight `Logged in using ChatGPT`; agent `HOME` = the throwaway dir; `LANG`/`LC_ALL=en_US.UTF-8`; 0 setlocale warnings; 0 secret-named variables; fastapi/chess/uvicorn importable (host site-packages are readable); `usage_source="stream"` on both legs; resume delta = thread total − start (62429 − 30184 = 32245 input); `rate_limits` read (5h 3.0 %, weekly 1.0 %). |
| Login vs non-login shells | The model chooses per command: codex's `exec_command` tool takes `"login": true|false`. Login (the default) runs `/bin/bash -lc` → `/etc/profile` + the throwaway `.bash_profile` (runner PATH first, then codex's PATH; runner locale). Non-login runs `/bin/bash -c` → no profile at all: PATH is codex's own (`…/codex-path`, the `~/.codex/tmp/arg0/…` helper dir with `apply_patch`/`codex-linux-sandbox`, then the runner's PATH — the toolchain is intact) and the locale stays codex's `C.UTF-8`; only bash-script tools resolved through a bash that can't load it warn, and on the development box `python3`/`pip`/`pytest` resolve to real pyenv binaries first (the runner itself runs under `pyenv exec`), so none did. Secret-named variables: 0 in both modes. Verified 2026-09-11 through the real adapter (a `login:false` turn: arg0 dir on PATH, `LC_ALL=C.UTF-8`, 0 warnings, 0 secrets; a login turn: runner locale, 0 warnings, 0 secrets). |
| Resume preamble | Every resumed turn re-injects codex's `<skills_instructions>` developer message (3,257 chars) — codex's own behaviour, present identically in a thread run without any adapter flags. No `<permissions instructions>` is re-injected with the adapter's identical-settings legs. A per-resume token cost inherent to codex, noted for comparability. |
| U8 | **Negative.** `--ignore-user-config` suppresses neither a global `$CODEX_HOME/AGENTS.md` (marker instruction was obeyed) nor user skills (marker skill listed). ⇒ pre-flight guard (rule 1). |
| — | Delegation: turn 1 carries `<multi_agent_mode>` "proactive multi-agent delegation no longer applies — do not spawn sub-agents" at default settings, so Astra will not delegate on its own; `ultra` is the codex level that turns it on (rejected by the effort map; a future opt-in knob if wanted — it multiplies token spend). |
| — | Rate limits: after two tiny Astra turns and four `sol` probe turns, 5-hour window 1.0% used, weekly 0.0%. |

## 4. Design

### 4.1 Invocation

Both legs pass **the same resolved settings** so codex has no reason to
inject a `<permissions instructions>` message mid-thread (§3.1).

`start_build(worktree_dir, prompt, model, config)`:

```
codex exec --ignore-user-config \
  --disable shell_snapshot \
  -c 'shell_environment_policy.experimental_use_profile=false' \
  -c 'shell_environment_policy.exclude=["*KEY*","*SECRET*","*TOKEN*","*PASSWORD*"]' \
  -m <model> --json -C <worktree> --sandbox workspace-write \
  -c 'model_reasoning_effort="<effort>"' \
  -c 'sandbox_workspace_write.network_access=true' \
  -
```

`continue_build(session_handle, followup_prompt)`:

```
codex exec resume <thread_id> --ignore-user-config \
  --disable shell_snapshot \
  -c 'shell_environment_policy.experimental_use_profile=false' \
  -c 'shell_environment_policy.exclude=["*KEY*","*SECRET*","*TOKEN*","*PASSWORD*"]' \
  --json -m <model> \
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
  both for resume and for the three jobs in §4.5 (cumulative baseline repair,
  dead-leg evidence, rate limits) — *the second half is superseded: this read
  "as the failure-path usage source" until round 9 deleted rollout-sourced
  usage; the failure path now records zeros marked `["missing"]`* (§4.5).
- The adapter remembers the **seven per-run attributes enumerated in
  `_reset_run_state`** from `start_build` — `worktree_dir`, `model` and
  mapped `effort` (mirroring `ClaudeCodeHarness._worktree_dir/_model`), plus
  `auth_mode`, the thread-cumulative baseline and its gap flag, and the
  throwaway HOME. §4.4 is the canonical list; restating a subset here is what
  round 15's bug was made of, so this points at it rather than repeating it.

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
`CODEX_HOME` is pinned (absolute) to the operator's real codex home, `HOME`
is swapped for the throwaway directory (D8, below), and `BASH_ENV`/`ENV`/
`ZDOTDIR` are removed so no operator file can be sourced through them.
Nothing else is added. The pre-flight (§4.6) runs under this same env.

**Agent shell environment (added 2026-09-11, U7).** `--disable shell_snapshot`
+ `-c shell_environment_policy.experimental_use_profile=false` + the exclude
list above, on both legs — the exact combination verified to hide every
secret-named variable inherited from the runner process and to stop codex
writing plaintext login-shell snapshots under `$CODEX_HOME/shell_snapshots`.
Those two are partial (codex runs `bash -lc`, which re-sources the profile of
whatever `HOME` is). The layer that closes the hole (**D8**, verified U7b):
the codex subprocess — pre-flight and both legs — gets **`HOME` = a fresh
per-run directory** `${XDG_CACHE_HOME:-~/.cache}/pagehub-benchmarks/codex-homes/run-XXXX`
with **`CODEX_HOME` pinned** (absolute) to the operator's real codex home (env
value, else `~/.codex` of the runner's HOME), so login, sessions and trust
entries stay put while the agent's login shell finds no operator profile to
source. The base **must lie outside the sandbox's writable roots** (worktree,
`/tmp`, `$TMPDIR`) — the adapter refuses a base under `/tmp` or `$TMPDIR`
(resolved paths); the worktree case can't arise, since worktrees live under
the repo's `.worktrees/`. Review round 3 showed,
with `codex sandbox`, that a `mkdtemp()` under `/tmp` let the agent create
`$HOME/.agents/skills/*` and append to `.bash_profile` for its later resumed
turns; the same probes against `~/.cache/…` were denied (read-only). The
throwaway HOME holds only a `.bash_profile` that re-exports the runner's
`PATH` (a stock `/etc/profile` resets it for login shells — this WSL box
skips that reset only because `WSL_DISTRO_NAME` is set; reproduced with it
unset) and the runner's locale (see the Locale row of §3.4); nothing else.
The directory is removed if the pre-flight fails; otherwise it is left
behind (one tiny file per run) until a later run reaps it: `start_build`
calls `_reap_throwaway_homes` before creating its own home and deletes every
`run-*` directory older than `THROWAWAY_HOME_TTL_SECONDS` (7 days), because
`Harness` has no teardown hook and Ctrl-C escapes any `try`/`finally`. The
reap is best-effort — every `OSError` is swallowed, so a root-owned or
read-only leftover can never fail a run
(`test_stale_throwaway_homes_are_reaped_on_the_next_start`,
`test_reaping_never_fails_a_run`,
`test_reaping_survives_a_home_that_genuinely_cannot_be_removed`).
**The override is not optional at the
boundary** (*round 15*): `_subprocess_env` requires a non-empty home and
raises without one, rather than defaulting to the operator's HOME, and
`continue_build` refuses a resume that has no throwaway home to run in.
Both exist because the harness's per-run state was how this layer came off
silently — a `start_build` that raised used to clear the home alone while
leaving the worktree, model and effort of the PREVIOUS run in place, so a
resume passed a guard that checked three of the seven per-run attributes and
spawned codex on the operator's real HOME (executed with the repo's fakes:
`env["HOME"] == /home/gavin`, the U7 configuration). `start_build` now clears
every per-run attribute on entry and on either failure path, so the instance
is in exactly one run or in none. Side effects: the agent has no
`~/.gitconfig`, `~/.ssh`, `~/.npmrc`, pip cache etc. — appropriate for a
benchmark build from an empty repo, and `.git` is read-only in the sandbox
anyway. Operator-side
hygiene (secrets out of `~/.bashrc`) is still recommended because the Claude
harness has no equivalent.

**Operator instructions (added 2026-09-11, U8).** Because
`--ignore-user-config` does not cover them, the pre-flight refuses to run
while `$CODEX_HOME/AGENTS.md` (verified injected), `AGENTS.override.md`
(takes precedence over it in codex's global scope) or the legacy
`instructions.md` (both named in the 0.154 binary; not executed) exists, or
`$CODEX_HOME/skills/` holds anything besides codex's bundled `.system`
(rule 1).

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

Usage — **calibrated 2026-09-11 against the recorded fixtures** (§3.4). The
rule, as implemented in `_usage_from`:

- `raw["usage_source"] ∈ {"stream", "none"}` — two values, because **a leg's
  own figure comes from the stream delta and from nothing else** (review
  round 9). Source: the usage object on the stream's terminal event (expected
  `turn.completed`, U1), stored verbatim in `raw["usage"]`. **Failure path
  (`turn.failed`, expected to carry no usage): tokens `0`, `"none"`,
  `usage_missing`** — nothing stands in for it.
  The rollout (`$CODEX_HOME`, default `~/.codex`,
  `sessions/**/rollout-*-<thread_id>.jsonl`) is still read on every leg, for
  three jobs and no others — cumulative baseline repair, dead-leg evidence,
  rate limits: `rate_limits`; codex's post-turn
  **`thread_token_usage`**, the *cumulative* that re-anchors the baseline the
  next delta is taken against; and the evidence that **this attempt** did work
  even though a leg's stream reported nothing (see the dead-leg rule below). Both of
  the latter read the last `token_usage_record` after the last `turn_context`
  line — the leg that just ran is the last turn appended, and a turn with no
  record yet reads as none, which is what keeps a dead leg dead. A cumulative
  names no turn, so adopting one cannot mis-attribute tokens; a `turn_token_usage`
  would, and is never used. The rollout is an unstable, codex-internal format
  (§3.1) and must never fail a leg the stream reported as completed.
- **A completed turn always has usage.** Terminal `turn.completed` with
  `usage_source == "none"` ⇒ `HarnessError` — that is a parser defect or a
  CLI format change, never a codex state, and recording `0` tokens / `$0.00`
  for a real success would be silent mis-recording. Capture-with-zeros is
  allowed only for the non-dead **failure** path (§4.6 rule 5), with a
  console warning.
- **The stream's usage is the thread total** (verified, U2): the adapter keeps
  the last total on the instance (reset per `start_build`) and records each
  leg's delta; a total that goes backwards ⇒ `HarnessError`. After a leg the
  stream could not measure, the total is re-anchored on the
  `thread_token_usage` codex records in the rollout — **only when it has
  advanced past the total already recorded**, so the baseline never walks
  backwards and bills a turn twice; there is no reconstruction from a turn
  figure (review rounds 7 and 9). `raw["usage"]` is the verbatim stream object
  (cumulative); `raw["usage_delta"]` is this attempt's share. A leg whose usage the
  stream did not report records a zero-filled delta plus `raw["usage_missing"]`
  — unknown, not free — and leaves the baseline flagged as not-known-whole,
  whatever it was re-anchored to, because *this* turn was never measured.
- **The honest invariant is a *marking* one** (review round 8), and marking is
  now the whole answer (review round 9). The adapter used to try to recover a
  leg's own share from the rollout's `turn_token_usage` whenever the baseline
  was short. That recovery needed to know a record was *this leg's*, and
  nothing in the rollout says so. Two gates were tried and both prove
  **staleness only**: the record's `turn_id` against the one last credited,
  and codex's post-turn `thread_token_usage` against the total already
  recorded. "Has not moved past what we recorded" does mean "already billed" —
  but the *converse* is not a proof of freshness, and the converse is the
  branch the code acted on. When the baseline is short, which is exactly the
  `usage_missing` state the recovery existed for, an earlier never-billed turn
  is beyond it too, and its id has never been seen. Executed against the
  recorded fixtures: a genuinely dead resume leg was handed turn 1's tokens,
  read as `usage_faithful: true`, and was **captured instead of retried**
  (attempts-to-green, not just cost); a later failed leg was billed an earlier
  turn while its own went uncounted; and a *winning* attempt recorded a turn
  that was not its own. So the inference is deleted, not refined: every
  **attempt** whose `usage_delta` is not a provable measure of *that attempt*
  alone is
  **marked**: `raw["usage_faithful"] = false` plus `raw["usage_caveats"]` from
  a three-value vocabulary — `"missing"` (short: nothing measured it, zeros),
  `"absorbed_missing_leg"` (the delta was taken against a baseline not known
  to be whole, so it may span an earlier unmeasured turn) and
  `"dead_leg_unmeasured"` (*added round 11*: a leg of this attempt was
  classified dead and retried onto a **new** thread, so the abandoned thread's
  spend is billed to no attempt anywhere in the run and this figure is short
  by a whole turn). All three values are
  reachable; a vocabulary entry that the code cannot emit is worse than none,
  which is why `"rollout_turn_rejected"` went with the recovery it described.
  The first two are leg-level and set in `_usage_from`; the third is a
  property of the **attempt** and can only be set in `_result`, the first
  place that knows a retry happened. It is gated on an abandoned thread id
  rather than on `dead_turn_retries` because a dead **resume** leg is retried
  on the *same* thread — its spend lands inside the next leg's stream delta,
  and both legs are this attempt, so nothing is lost and nothing is marked.
  Measured 2026-09-12, and the reason the granularity above is the **attempt**
  and not the leg: a dead resume leg that spent (4242, 77) turned the
  attempt's delta from (3959, 5) into (8201, 82), published with
  `usage_caveats []` and `usage_faithful true`. Read at leg granularity the
  rule would demand a caveat there; that reading is wrong and
  `test_a_dead_resume_leg_leaves_the_attempt_faithful` fails anyone who
  implements it.
  Why this is not merely a JSON nicety: the results site gates its warning
  marker solely on `usage_caveats`, so before round 11 a published page could
  show an understated cost with no marker at all (executed end to end by the
  reviewer, and now pinned by
  `test_an_abandoned_thread_is_flagged_on_the_published_page`, which carries
  the record through `execute_benchmark_run`, the results JSON and
  `tools/build_site.py`).
- **Which caveats make a RUN's totals a lower bound** (*added round 12*).
  Marking the attempt row is not enough: the site also publishes aggregates —
  the run headline, the index's head-to-head cost table, the benchmark page,
  the theory comparison — and round 12 found every one of them rendering the
  same understated figure with no marker. The rule is not "any caveat" but
  "did the spend leave the record", and it was settled by execution:
  - `"dead_leg_unmeasured"` — **lower bound**. The abandoned thread is never
    resumed and never read, so its spend is in no leg's delta anywhere.
  - `"missing"` — **lower bound**. A later leg's delta reabsorbs the
    unmeasured turn only if codex's rollout did *not* re-anchor the baseline
    in between, and the published record cannot say which happened: two runs
    whose attempt records are identical (`["missing"]`, then
    `["absorbed_missing_leg"]`) totalled 3,959 and 7,158 input tokens against
    a true spend of 7,158.
  - `"absorbed_missing_leg"` — **not** a lower bound by itself: it moves spend
    between attempts of one run and leaves the total whole. It also never
    appears alone, since the baseline gap that produces it is set only on the
    path that publishes `"missing"` and `start_build` resets it per run.
  The set is `RUN_TOTAL_LOWER_BOUND_CAVEATS` in `tools/build_site.py`; the
  affected figures render as `≥` plus the same `⚠` the attempt row uses; and
  one rendered-page test per surface pins it, each failing on its own when the
  marking is reverted.
  Marking errs toward marking: a delta taken against a re-anchored baseline is
  often exactly right and is still flagged, because the harness cannot show
  it. A consumer of the results file needs only `usage_faithful` to know an
  **attempt's** figure is an estimate; it never needs to know how the adapter
  works. The RUN-level rule — which caveats shorten a total — is the one thing
  such a consumer must apply itself (see the run-total paragraph in
  `README.md`). An
  over-reported attempt is exactly as unfaithful as a zero-reported one, so
  both carry it.
- **Every statement above is pinned by a named test** (*added round 13*). The
  leg-vs-attempt wording drifted for four consecutive review rounds because
  the instrument kept being a phrase sweep, and prose has no build that fails.
  The standing check is mechanical instead: **for every normative statement in
  this section, in `README.md`, in the page copy under `templates/`, and in
  the docstrings and comments of `codex_cli.py` and `tools/build_site.py` that
  asserts when `usage_faithful` is false, when a caveat is emitted, or which
  figures a caveat makes a lower bound, name the test that fails if the
  statement is implemented as written.** A statement no test can pin is a
  statement that will drift again, and is either wrong or a coverage gap.

  `templates/` joined that scope in round 15, and is the part of it a reader
  actually meets: no statement of these rules outside `templates/` is ever
  rendered to someone reading a published result. Three independent
  inversions of `run.html`'s copy — the neutral caveat made lower-bound, the
  dead leg retried on the same thread, the marked attempt called exact — each
  left the whole suite green.

  **Two templates carry it, not one** (corrected in round 16; rounds 15's
  wording said `run.html` was the only statement a reader ever sees, and that
  was wrong). `run.html` has the largest block — but it renders on the run
  page alone. `_marks.html`'s `lb_title` macro is the run-total rule as the
  index's head-to-head cost table, the benchmark page and the theory
  comparison state it, and a reader who never opens a run page sees only that
  sentence. It was unpinned for a round longer than `run.html` for exactly
  that reason: inverting its trailing clause to "the real figure is exactly
  this and needs no adjustment" left all 283 tests green. Both templates are
  now pinned verbatim on the rendered page — see the rows below.

  **What the guard does and does not certify.** `test_every_test_named_in_the_docs_exists`
  checks the *shape* of the table and that every cited name *resolves* to a
  test that exists: a renamed or deleted test, an emptied cell, or a deleted
  row fails the build. It does **not** check that a cited test pins the
  statement beside it — repointing a row at an unrelated real test passes.
  That property is established by mutation (implement the statement as
  written; the cited test must fail), which is done per review round and
  recorded in the commit messages, not by this test. It also checks that
  every file in the contract's declared set still cites **at least one** test
  — added in round 16, replacing two assertions that could not fail; it does
  not check that a file still carries every inline pin it once had. A green
  build means the citations resolve; it is not a certificate that the table
  is honest.

  | Statement | Pinned by |
  |---|---|
  | `usage_delta` and the caveats are at **attempt** granularity: a dead *resume* leg's spend is inside the attempt's own delta and is deliberately **not** marked | `test_a_dead_resume_leg_leaves_the_attempt_faithful` |
  | A leg the stream could not measure records zeros + `usage_missing`, and the attempt is marked `"missing"` — including the final leg, which has no successor | `test_a_final_leg_with_unreadable_usage_is_marked_with_nothing_after_it`, `test_non_dead_failure_records_no_usage_and_says_so` |
  | A delta taken against a baseline not known to be whole is marked `"absorbed_missing_leg"` (the reviewer's executed 17119 = own 1000 + lost 16119) | `test_persistently_unreadable_rollout_marks_the_attempt_that_absorbs_it` |
  | Over-marking is the honest direction: a delta against a *repaired* baseline is often exactly right and is still marked | `test_an_unmeasurable_legs_rollout_repairs_the_cumulative_baseline` |
  | …and the marker clears once the baseline is whole again — an always-on marker would be worthless | `test_the_absorbed_marker_clears_once_the_baseline_is_whole_again` |
  | `"absorbed_missing_leg"` never appears without `"missing"` in the same run — the baseline gap that produces it is set only on the path that publishes `"missing"` (conjunct a) | `test_a_baseline_gap_is_recorded_only_by_a_leg_that_also_publishes_missing`, `test_absorbed_missing_leg_never_appears_without_missing_in_the_same_run` |
  | …and it never crosses a run boundary, because `start_build` resets the gap (conjunct b). Both conjuncts together are the premise that lets the site treat it as run-total-neutral | `test_a_new_run_does_not_inherit_the_previous_runs_baseline_gap` |
  | A dead **start** leg abandons a thread nothing ever reads, so the attempt is marked `"dead_leg_unmeasured"` and the number itself is untouched | `test_a_start_leg_that_abandoned_a_thread_marks_the_figure_short` |
  | …and the module overview states the delta rule for a dead **resume** leg only: there the next delta covers both legs of the attempt, whereas a dead start leg's abandoned thread is covered by no delta anywhere. Round 17: the overview claimed unconditionally that a retried dead leg's delta "covers every leg of the attempt", which is the negation of the row above — prose that merely re-describes a rule more optimistically than it is was outside the table | `test_a_dead_resume_leg_leaves_the_attempt_faithful`, `test_a_start_leg_that_abandoned_a_thread_marks_the_figure_short` |
  | `usage_caveats` is a **list**, not an enum: more than one reason can apply to one attempt | `test_the_dead_leg_caveat_composes_with_the_leg_level_one` |
  | Every ordinary attempt is faithful — the marker is only worth reading if the ordinary paths clear it | `test_every_ordinary_attempt_is_recorded_as_faithful` |
  | **Leg**-level on purpose (round 9): no rollout turn record is *ever* adopted as a leg's share, over all four rollout states | `test_no_rollout_record_is_ever_adopted_as_a_legs_share` |
  | `"missing"` makes a RUN's totals a lower bound; `"absorbed_missing_leg"` does not; a faithful run carries no marking | `test_missing_makes_the_run_total_a_lower_bound`, `test_absorbed_missing_leg_alone_does_not_shorten_the_run_total`, `test_a_faithful_run_carries_no_lower_bound_marking` |
  | `"dead_leg_unmeasured"` makes them a lower bound too — its own pin, since dropping it from `RUN_TOTAL_LOWER_BOUND_CAVEATS` leaves the row above green (round 14) | `test_an_abandoned_thread_is_flagged_on_the_published_page`, `test_run_page_headline_presents_a_short_total_as_a_lower_bound` |
  | Every surface publishing a run total renders that lower bound | `test_run_page_headline_presents_a_short_total_as_a_lower_bound`, `test_index_presents_a_short_total_as_a_lower_bound`, `test_benchmark_page_presents_a_short_total_as_a_lower_bound`, `test_theory_page_presents_a_short_total_as_a_lower_bound` |
  | The per-attempt `⚠` is gated solely on `usage_caveats`, so an attempt the harness marks is marked on the page | `test_build_flags_an_attempt_whose_tokens_are_not_its_own`, `test_an_abandoned_thread_is_flagged_on_the_published_page` |
  | The metric-keyed lower bound on the theory comparison — the only surface that decides marking per METRIC — reaches **every** token-derived figure: cost and the input, output and cache token totals each carry the `≥` and the `⚠`. Round 17: three of the four were droppable with the suite green, because the other three surfaces mark their token columns from `totals_are_lower_bound` in the templates and never consult `LOWER_BOUND_METRICS` at all | `test_theory_page_presents_a_short_total_as_a_lower_bound` |
  | …and **nothing else**: wall time, attempts, the attempt cap and pass/fail are measured elsewhere, so marking them would devalue the marker on the figures it is right about | `test_a_short_runs_wall_time_and_attempts_are_not_marked_as_lower_bounds` |
  | …and the set itself is pinned by name, so a fifth member must be classified into one of those two directions rather than arriving unexamined on a table no test reads | `test_the_lower_bound_metric_set_is_pinned_by_name` |
  | The vocabulary is closed and every value is classified for run totals | `test_every_harness_caveat_is_classified_for_run_totals` |
  | …and the run-level filter is **deny-by-default**, so a caveat written by a harness version the renderer does not import shortens the total instead of silently clearing it — the one case the guard above cannot see, since it compares harness and renderer in the SAME checkout. Round 17: as an intersection with the lower-bound set it failed OPEN, rendering the attempt's ⚠ on the run page beside a clean `$12.3456` on the index | `test_an_unclassified_caveat_makes_the_run_total_a_lower_bound` |
  | A *completed* turn always has usage — zeros there are a defect, not a caveat | `test_turn_completed_without_usage_raises_never_zero_token_success` |
  | The reason list rendered beside a lower-bound total names only the caveats that shorten it — a neutral caveat present in the same run is filtered out | `test_a_neutral_caveat_is_never_named_as_a_reason_a_total_is_short` |
  | A record with no `raw` — claude-code and legacy runs — gets no marker rather than a false warning | `test_build_marks_no_attempt_when_the_harness_reports_none` |
  | The run page's own explanation of the marker states which caveats shorten a total and which do not, in the words a reader is given | `test_the_run_page_explainer_states_the_run_total_rule` |
  | …and its per-attempt legend states what a marked figure is — short, not provably its own, or short by an abandoned thread — never that it is exact | `test_the_run_page_legend_states_what_a_marked_attempt_means` |
  | …and that legend renders **only** on a run that has a marked attempt: ungated it put a ⚠ on all 27 published claude-code pages, none of which has one, and pages.yml rebuilds from source on push so it would ship at merge | `test_build_marks_no_attempt_when_the_harness_reports_none`, `test_a_clean_codex_run_carries_no_caveat_marking_at_all` |
  | The aggregate tooltip — the statement of the run-total rule on the index, benchmark and theory pages, and all a reader who never opens a run page sees — says the real figure is **higher by an unknown amount** | `test_index_presents_a_short_total_as_a_lower_bound` |
  | The run page says what `usage_faithful` means: whether the figure measures **that attempt alone**, not whether the run passed | `test_the_raw_json_explainer_says_what_usage_faithful_means` |
  | A `start_build` that raises leaves the instance exactly as constructed, whichever of its raise sites fires — including the four before the `try`, which is what pins the reset to the ENTRY rather than merely to the failure paths | `test_a_failed_start_clears_every_per_run_attribute` |
  | A clean codex run — every attempt `usage_faithful` with an empty `usage_caveats` — renders no marker of any kind on any surface | `test_a_clean_codex_run_carries_no_caveat_marking_at_all` |
- **Dead-leg detection** (§4.6 rule 2) is the second of the rollout's three
  jobs. A leg with
  no model activity *and* no stream usage is dead and is retried, not captured
  — unless codex's rollout records a turn beyond a baseline with **no recorded
  gap**, which is evidence that **this attempt** did work even though this
  leg's stream said nothing. (*Superseded, review round 10: this read "a
  baseline known to be whole … which is the one case where 'some turn ran
  beyond what we billed' really does mean 'this leg ran': nothing else could
  have moved it" — a claim about the LEG, and too strong. A dead-classified
  leg never reaches `_result`, so it leaves no recorded gap behind, which
  means a gapless baseline is not necessarily a whole one and the record
  beyond it can belong to an earlier leg of the same attempt. The decision is
  unchanged and still sound, because every leg that can move the cumulative
  under a gapless baseline belongs to the attempt being classified, and the
  attempt — not the leg — is the granularity every decision here is taken at.
  The corrected form is in `codex_cli.py`'s `_advances` and
  `_Usage.active_per_rollout`, and §4.6 rule 2 states it as the
  `not baseline_has_gap` conjunct.*) Against a baseline with a recorded gap the
  same record proves nothing, and the leg
  stays dead. Capturing a dead leg is the failure that corrupts a benchmark's
  headline metric rather than its cost column (review round 9).
- `raw["rate_limits"]` (5-hour + weekly `used_percent`, `plan_type`) is read
  from the rollout's last `token_count` on every leg, best-effort — the only
  place codex reports subscription budget. Printed to the console only for
  legs that return a result: a dead leg's rollout would show the previous
  turn's stale figures, so the retry path deliberately prints nothing.
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
from the dead legs] + "dead_turn_errors_total", "dead_turn_thread_ids":
[first 20 threads abandoned by dead legs, excluding this leg's own],
"rollout_path": str|None — relative to `$CODEX_HOME`, or the basename when it
lies outside it, "unparsed_lines": [first 20] + "unparsed_total",
"stderr_tail": str (last 2000 chars, ANSI-stripped), "usage_delta": this
attempt's share, "usage_missing": bool, "usage_faithful": bool, "usage_caveats":
[str], "reasoning_output_tokens", "rate_limits": {plan_type,
primary/secondary: {used_percent, window_minutes, resets_at}} — trimmed, no
credit balances}`.
`cache_tokens_reported` is true only when codex's usage object carries
`cached_input_tokens`.
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
   touches the worktree. Then refuse while `$CODEX_HOME/AGENTS.md`,
   `AGENTS.override.md` or `instructions.md` exists or `$CODEX_HOME/skills/`
   has entries other than `.system` (U8: not suppressed by
   `--ignore-user-config`). A missing `codex` binary raises `FileNotFoundError`
   from the subprocess call and is **not** wrapped — exactly as the Claude
   adapter; `__main__.main` already prints it as `error: …` with exit 2. Mode line recorded in `raw["auth_mode"]` of attempt 1.
2. **Run the leg.** If the stream has **no `thread.started`** at all ⇒
   `HarnessError` immediately, no retry (nothing to resume, nothing to
   classify; test 9 asserts a single call). Otherwise a turn is **dead** when
   it ended without model activity: no `item.*` event whose
   `item.type != "error"`, **no stream usage**, **and** no rollout cumulative
   beyond a baseline with no recorded gap (*amended round 9* — the
   `not baseline_has_gap` conjunct is the fix for that round's critical:
   against a short baseline the rollout proves nothing, because an earlier
   never-billed turn sits beyond it too, so the leg stays dead and is
   retried).
   Auth/transport failures and `invalid_prompt` on an untouched prompt are
   dead; "wrote half the code then the backend 500'd" is not. A turn whose
   terminal event is `turn.completed` is **never** dead — rule 8 governs it.
   **The rollout half of this test is conditional on a gapless baseline** —
   "no *recorded* gap", which is not quite "whole": a dead-classified leg
   never records one (review round 10). A turn that spent tokens on reasoning
   alone and then failed is captured rather than retried (M21) only while
   nothing earlier in the run went unmeasured; after an unmeasurable leg the
   identical turn is instead retried, and if the retries are alike the run
   aborts under rule 4
   (§4.8 item 19). Losing that attempt is the honest outcome — the
   alternative, deleted in round 9, was billing it an earlier turn's tokens
   and marking the result faithful.
3. **Dead turn ⇒ retry the identical leg** after a 5 s pause, up to
   `CODEX_DEAD_TURN_RETRIES` times (default **2**; **D5**). Rationale:
   transient transport/backend failures cost no tokens to retry, and a
   retry avoids consuming an `attempt` for something the model never saw.
   (*Qualified round 11*: "cost no tokens" is the expectation, not something
   the harness can verify — a dead leg is precisely one whose spend nothing
   could read. Where the retry abandons a thread, the attempt is marked
   `["dead_leg_unmeasured"]` rather than assumed free; §4.5.)
   A deterministic `invalid_prompt` costs ≤ 2 dead legs (~15 s each with
   codex's own reconnects) before failing loudly. Dead-leg wall time is
   counted; `raw["dead_turn_retries"]` and the dead legs' error messages
   (`raw["dead_turn_errors"]`, bounded) are recorded on the attempt that
   eventually returns, along with `raw["dead_turn_thread_ids"]` — the
   abandoned threads are not otherwise referenced, and their unbillable spend
   is what `["dead_leg_unmeasured"]` marks (§4.5). Side effects: a dead *start* leg leaves an
   abandoned thread (new `thread_id` on retry); a dead *resume* leg leaves
   the follow-up user message in the thread history (the model may see it
   up to three times).
4. **Still dead after retries ⇒ `HarnessError` on any leg** (Claude parity).
   The worktree, the codex rollout, and the console output of earlier
   attempts survive on disk; what is *not* produced is a misleading record
   and a pushed branch. This is the one place `invalid_prompt` crashes the
   run; the message tells the operator to rerun or switch model.
5. **Non-dead failure** (model activity, then `turn.failed` / non-zero exit)
   ⇒ **captured**: the attempt records **zeros** with `usage_source: "none"`,
   `usage_missing: true` and `usage_caveats: ["missing"]` (§4.5; plus
   `"dead_leg_unmeasured"` if a retry of this same attempt also abandoned a
   thread — the caveats are a list, not an enum) — **the
   rollout is not a usage source** (*amended round 9*: a `turn.failed` carries
   no usage on the stream and nothing stands in for it, because no rollout
   record can be shown to belong to this leg). `session_handle` =
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
8. **`turn.completed` whose STREAM reported no usage ⇒ `HarnessError`**
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

1. **start argv, byte-exact:** `["codex","exec","--ignore-user-config",
   *ENV_POLICY,"-m",model,"--json","-C",wt,"--sandbox","workspace-write","-c",
   'model_reasoning_effort="high"',"-c",
   "sandbox_workspace_write.network_access=true","-"]`; `input` == prompt
   verbatim (multi-line, quotes, braces, non-ASCII); `cwd == wt`.
2. **resume argv, byte-exact:** `["codex","exec","resume",thread_id,
   "--ignore-user-config",*ENV_POLICY,"--json","-m",model,"-c",
   'model_reasoning_effort="high"',"-c",'sandbox_mode="workspace-write"',
   "-c","sandbox_workspace_write.network_access=true","-"]`; no `-C`, no
   `--sandbox`; `cwd == wt`; `input` == follow-up verbatim.
3. **effort:** all five map; `ultra`, `minimal`, `bogus`, `"High"`, `None`,
   `1`, missing ⇒ `HarnessError`, no subprocess call.
4. **env:** the three auth vars and `BASH_ENV`/`ENV`/`ZDOTDIR` absent; `PATH`
   preserved; `CODEX_HOME` pinned; `HOME` swapped; nothing else added; **the
   pre-flight call also ran under the same env**. (`ENV_POLICY` in items 1–2 =
   `--disable shell_snapshot -c shell_environment_policy.experimental_use_profile=false
   -c shell_environment_policy.exclude=[…]`.)
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
8. **non-dead failure:** labelled synthetic stream = the real failure
   envelope plus a non-error `item.completed`, then `turn.failed`, with a
   recorded rollout under a temp `CODEX_HOME` ⇒ captured, no retry,
   `usage_source == "none"` with zeros marked `["missing"]` (the rollout is
   not a usage source — review round 9), `raw["harness_error"]` set, and
   `rate_limits` still read from that rollout.
9. **no `thread.started`** ⇒ `start_build` raises after exactly **one**
   subprocess call (no dead-turn retry).
10. **timeout** ⇒ SIGTERM then SIGKILL to the group, drain attempted,
    `HarnessError`; a second case where the group exits on SIGTERM asserts
    no SIGKILL follows. **10b (interrupt):** a `KeyboardInterrupt` out of
    `communicate` ⇒ SIGTERM to the group, pipes closed, the interrupt
    re-raised unwrapped (never a `HarnessError`), no drain attempt.
11. **parser:** non-JSON chatter ⇒ `unparsed_lines` bounded + count; exit 0 +
    `turn.failed` ⇒ failure; **`turn.completed` with no STREAM usage ⇒
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
14. `continue_build` before `start_build`, and with `""` ⇒ `HarnessError`; a
    resume stream reporting a different thread id ⇒ `HarnessError`.
15. **Parser rules pinned from the real rollout lines** (review round 3 —
    each was shown to survive the suite as a mutation before these existed):
    a turn whose `turn_context` has no usage record reads as *no usage* (not
    the previous turn's), end to end a dead resume with the real rollout on
    disk is retried then raises; after a failed leg whose turn the rollout
    covers, the thread total advances so the next leg's delta is exactly its
    own turn (`test_thread_total_advances_after_a_failed_leg_the_rollout_covers`
    — the failed leg itself records zeros); within a
    turn the **last** `token_usage_record` wins.
16. **Throwaway HOME:** under the configured base, shared by pre-flight and
    both legs, contains only the PATH + locale profile; the base refuses
    `/tmp` and `$TMPDIR`; removed when the pre-flight fails; a real
    `/bin/bash -lc` with codex's C.UTF-8 env and `WSL_DISTRO_NAME` unset ends
    up with the runner's PATH and locale; hostile values stay quoted;
    relative `CODEX_HOME` made absolute.
17. Guard covers `AGENTS.override.md` and `instructions.md`;
    `cache_tokens_reported` false when the field is absent; `rate_limits`
    trimmed; `gpt-6-astra` prices pinned; the two-row dry run runs against a
    stub bundle (so it runs in CI).
18. **Cache writes** (review round 5 — every recorded usage object has
    `cache_write_input_tokens == 0`, so these use SYNTHETIC writes on the real
    envelopes): a start leg with 1000 writes records 2199 non-cached input +
    1000 cache-creation; a resume leg after a 300-write start records its
    1000 share of a 1300 total; a failed leg whose turn the rollout covers
    records **(0, 0, 0)** marked `["missing"]` — *round 9 deleted the rollout
    as a usage source, so its 400 writes are not billed to it* — while the
    baseline-repair half still holds: the thread total advances so the next
    leg's delta is only its own 100; through the runner with real pricing
    `cost_usd == $0.0469` (writes priced once, at the write rate). A failing
    rate-limit display never fails a leg. Also pinned: the pre-flight needs exit 0 even when the
    ChatGPT line is present; `harness_error` falls back to the LAST error;
    `rate_limits` come from the LAST `token_count`; `stderr_tail` keeps the
    end; a resume leg's `reasoning_output_tokens` is its share of the thread
    total. Each rule was shown to survive the suite as a mutation first.
    Still surviving, accepted as low-value (review round 6; the list was
    re-executed in round 16 and two of its five entries were wrong):
    first-vs-last rollout file match and first-vs-last terminal event
    (equivalent on real data: one rollout per thread, one terminal event per
    leg), the 500-char bound on unparsed lines, and the `root_real` HALF of
    the writable-root comparison (needs a writable location outside `/tmp`
    to test). Corrected in round 16: (a) the `base` half of that comparison
    is no longer unpinned — dropping `.resolve()` on `base` is KILLED by
    `test_throwaway_home_base_resolves_dotdot_and_ignores_relative_xdg`
    (executed: 1 failed, 288 passed), so only the `root_real` side survives;
    (b) "the rate-limit print label" was already pinned —
    `test_rate_limit_print_never_fails_a_completed_leg` asserts the literal
    `5h=0.0%`. What actually survived was the window MAPPING: swapping
    `rate_limits.get("primary")` / `.get("secondary")`, so the 5-hour figure
    publishes under the weekly label and vice versa, left the suite green.
    That is materially worse than the label it displaced — the 5-hour window
    is the one an operator watches on a Plus plan — and it is now killed:
    the same test gives the two windows different figures (`5h=0.0%
    weekly=42.5%`), which is what the pin needed, because with both at the
    fixture's 0.0 no assertion on either figure can tell the mapping apart.
19. **Per-attempt usage is faithful or says it is not** (review rounds 7–9,
    on the REAL rollout lines and REAL streams). A leg the stream could not
    measure is marked `usage_missing` / `["missing"]` and records zeros —
    including the final leg of a run, which has no successor to reconcile
    against. Its successor's delta is marked `usage_faithful: false` /
    `["absorbed_missing_leg"]` (reproducing the reviewer's executed 17119 =
    its own 1000 + the lost leg's 16119) and the marker clears on the leg
    after, since the stream's own thread total is a whole baseline.
    **No rollout turn record is ever adopted as a leg's share** — pinned over
    the four rollout states that used to be treated differently (this leg's
    own record, a previous turn's, an unpartitionable one, one bigger than the
    stream delta): the answer is the same marked stream delta in all four, and
    none of them raises. Round 9's three executed scenarios have their own
    tests: a dead resume after an unmeasured start leg is **retried, not
    captured** (the control — a *completing* start leg — always was), a failed
    leg on a short baseline is not billed an earlier turn, and the winning
    attempt is not handed one either. Two of the rollout's three remaining
    jobs are pinned in both directions (the third, `rate_limits`, is asserted
    on the real rollout by the fixture tests): its cumulative re-anchors the baseline so the
    next leg keeps only its own turn (cache-write component included), and is
    refused when it has not advanced or is not component-wise comparable; and
    a leg with no items and no stream usage is captured when a baseline with
    no recorded gap says the rollout has moved on, retried when the baseline
    is short. Tests
    that care which turn a leg reads stage the rollout the way codex fills it,
    one turn at a time. **An attempt short by an abandoned thread says so**
    (*round 11*): a dead start leg retried onto a new thread yields
    `usage_faithful: false` / `["dead_leg_unmeasured"]` while a dead *resume*
    leg (same thread, spend inside the next delta) stays faithful — and,
    because the defect was invisible at the harness boundary,
    `test_an_abandoned_thread_is_flagged_on_the_published_page` carries the
    record through `execute_benchmark_run`, the results JSON and
    `tools/build_site.py` and asserts the rendered page shows the warning
    marker.

`make test` and `make lint` green at each stage (§6).

### 4.9 Docs

README → Usage → "Codex CLI harness": required version (≥ 0.153.0; tested
0.154.0); `codex login` (`--device-auth` for headless) and that runs require
the **ChatGPT** login mode (a stored API key is refused); effort **required**
in YAML and why; sandbox = workspace-write + network on, `.git` read-only and
installs confined to the worktree/`/tmp` (asymmetry vs. Claude); what tokens
are and aren't reported (from the fixtures; cache `0` +
`cache_tokens_reported:false` when absent; `usage_source`, and the
`usage_faithful` / `usage_caveats` marking with what each caveat means and
what it implies for the run totals);
`CODEX_BUILD_TIMEOUT_SECONDS`, `CODEX_DEAD_TURN_RETRIES`;
`--ignore-user-config` and the trust-entry accumulation; codex's own
preamble as a comparability caveat; when a run crashes vs. records (§4.6);
the `effort: high` vs `xhigh` matrix note; that the runner executes the
built `Makefile` on the host outside any sandbox (both harnesses); `cost_usd`
computed from `pricing.yaml`, not billed. `.env.example` gets both knobs.

### 4.10 Security — what does an attacker who controls X get?

| Actor controlled | Gets | Mitigation / status |
|---|---|---|
| **The model** (its tool calls) | Write: worktree (minus `.git`), `/tmp`, `$TMPDIR`. **Read: the entire filesystem** (`~/.ssh`, `~/.codex/auth.json`, `~/.claude/`, any `.env` — by path; `$HOME` in its shell is the throwaway dir, which holds only the PATH/locale `.bash_profile` and is read-only to it). Network egress (D1). Subprocess env minus the three auth vars, minus every inherited variable matching `*KEY*`/`*SECRET*`/`*TOKEN*`/`*PASSWORD*`, and shells that source no operator profile (D8: a login shell reads only `/etc/profile` and the throwaway PATH/locale `.bash_profile`; a non-login shell reads nothing) — a probe agent reported no secret-named variables, where before D8 it listed 37 from `~/.bashrc` (production Stripe/JWT/Cloudflare/Supabase credentials and a PEM key). Claude Code sees all of them with no filtering. Can start background processes (verified not to outlive `codex exec`). **And, one step later, arbitrary unsandboxed execution as the runner user with the runner's full environment**: the runner runs the model-authored `make up` / `make down` / compose file on the host with no `env=` filtering. ⇒ read-anything + egress + a secret-laden shell is an exfiltration path **today, for both harnesses**. | During the turn: narrower than the Claude harness (no sandbox there). After the turn: identical to Claude — the host-side `make up` is existing runner behaviour for every harness. Stated plainly in README; the profile-secrets problem needs the operator-side fix below. |
| **Target repo contents** (`AGENTS.md`, `.codex/`, `.rules` for a non-empty `target_start`) | Instructions/rules codex would load from the checkout. | Moot today: all four benchmarks are `target_start: empty`. `--ignore-rules` exists if a non-empty start is ever added. |
| **Operator's shell env** | Inherited minus the three auth vars; a `CODEX_API_KEY` would have switched billing silently. Everything `~/.bashrc` exports reaches a login shell started with the operator's HOME — which is what the **Claude** harness gives its agent, and what codex gave it before D8; codex additionally wrote plaintext login-shell snapshots (values included, mode 644) to `$CODEX_HOME/shell_snapshots/` until `--disable shell_snapshot` was added. | Codex: three auth vars stripped, inherited secret-named vars filtered, snapshots disabled, **and a throwaway HOME so the profile is never sourced (verified NONE)**. Claude: unchanged, sees everything. **Operator action still recommended:** move secrets out of `~/.bashrc` (source them on demand) or benchmark under a dedicated user; delete the existing snapshot files. |
| **Operator's stored login** (`auth.json`) | An API-key login would pass an exit-code-only check and bill the API. | Pre-flight requires `Logged in using ChatGPT`. |
| **Operator** (runs it) | Spends subscription allowance; trust entries appended to `~/.codex/config.toml`; a captured FAIL pushes a `bench/…` branch (existing behaviour); a mid-run usage-limit exhaustion **crashes** the run (no record, no push) rather than laundering it; their shell secrets reach the model-authored `Makefile` via the host-side `make up` (existing behaviour, both harnesses). | Documented; no real run without go-ahead; run the benchmark from a shell without unrelated secrets. |
| **CI** | Nothing: fixtures are inert JSONL; subprocess is faked; no network. | Enforced by the tests' fake layer. |

No authn/authz or twin-override surface is added; the harness makes no HTTP
calls of its own.

### 4.11 Smoke plan (steps 1–2 done 2026-09-11 — §3.4; step 3 = the first real run)

Each step is cheap and gates the next. Back up `~/.codex/config.toml` first.

1. `codex login status` ⇒ exit 0 + `Logged in using ChatGPT` (U6; note
   which stream). Capture (done in Stage 2 with a throwaway script that was
   never committed — see §3.2): the exact
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
3. The single-attempt benchmark smoke. **Superseded by budget (2026-09-11):**
   the operator's ChatGPT Plus plan allows only a handful of full Astra
   builds, so a throwaway single-attempt smoke is not run separately; the
   first real run (below, `--max-attempts 3`, recorded) doubles as the smoke
   and is checked against the same acceptance list before its record is
   committed. The adapter itself was exercised end to end on real codex with
   `gpt-5.6-sol` (§3.4). The original command, for reference:

```
python -m pagehub_benchmarks run eval-chess-backend \
  --harness codex-cli --model gpt-6-astra --config effort=high \
  --max-attempts 1 --no-build-site \
  --results-dir /tmp/pagehub-benchmarks-smoke
```

First real run (recorded, pushed):
`python -m pagehub_benchmarks run eval-chess-backend --harness codex-cli --model gpt-6-astra --max-attempts 3`.
`--results-dir` keeps a smoke record out of committed `results/`; drop it
and `--no-build-site` for a real recorded run. **The push is not optional:**
`_push_built_tree` pushes the built tree to the real
`pagehub-io/eval-chess-backend` as `bench/codex-cli/gpt-6-astra/effort-high/<ts>`
(and to its default branch if the target is empty and the smoke passes),
exactly as every Claude run does. Needs pagehub-evals on `:8002` and `:8003`
free. Acceptance: the runner's host-side `make up` starts the
codex-built service; `raw.usage` matches the fixture-derived rule;
`raw.usage_source == "stream"`; the pushed tree contains no venv or
`site-packages`; no attempt raised "thread usage went backwards" (if codex's
context compaction ever resets the thread total, the adapter fails loudly
rather than mis-recording — record it as a finding and revisit the delta rule).

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
- **D8** throwaway `HOME` (outside the sandbox's writable roots; restores only PATH + locale) + pinned `CODEX_HOME` for every codex subprocess: **yes** (U7b; the only layer that fully hides profile-exported secrets).
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
- **Stage 2 (done 2026-09-11 on PR #29):** commit both success fixtures
  verbatim; implement the confirmed mapping in `_usage_from` (if usage turns
  out cumulative across a thread, the function gains a `previous_total`
  argument fed from instance state — its Stage-1 signature is not final); add the matrix
  row; tests 6, 8, 12, 13 (dry-run half); the task's dry-run command passes
  for both rows; finish README's "what is reported". PR review rounds on the
  diff (rounds 3 onward, until one is clean on the final HEAD). §4.11 step 3
  is folded into the first real run, after merge.
