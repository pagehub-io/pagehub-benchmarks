# Tech spec — `bench-lab`: spin built versions up/down and review them side by side

Status: **HANDED BACK at the round-3 cap** — 2026-09-18. Round 3 returned
**9 critical, 13 important**; the gate's 3-round cap is reached, so this is
not approved for implementation and no round 4 is run. **Recommendation:
pivot to lab-authored run specs (§4.4 recipes promoted from fallback to
primary) rather than patching the compose check a fourth time — 7 of round
3's 9 criticals are features of attacker-controlled compose files that a
lab-authored spec does not have.** Awaiting the operator's call on that
pivot, on §7's contract option, and on the separate pagehub-browser exposure
(§6.4). The v3 design below is preserved as-is for reference; its §4.3 is the
part the pivot would delete. See §11 for the full round log.

Previously: **DRAFT v3** — Round 2 (two fresh instances, design +
security) returned **8 critical, 17 important** against v2. The decisive one,
found independently by both: **compose merges `ports:` by concatenation, not
replacement**, so v2's rung-1 override left every instance published on
`0.0.0.0` *and* colliding with the next instance — breaking the one feature the
tool exists for while creating the exposure §7 was written to prevent. Fixed
with `ports: !override` (verified). Round 2 also found that v2's compose rung
**introduced three host-compromise paths** `docker run` did not have
(`include:`/`.env` bypassing the hazard scan; top-level `driver_opts`; an
out-of-tree `build.context`), all reproduced here, and that the screenshot
design was **non-functional in every configuration** §7.4 permitted. See §11.

**Claim convention** (house style): **[X]** executed on this box 2026-09-18;
**[R]** read only, not executed. v1 was rejected for measuring with
`git ls-files` instead of disk; v2 carried a false `[X]` ("4 composes declare
two services" — all 37 declare exactly one). Both are recorded in §11 because
the convention only helps if its failures are logged.

## 1. Goal

A local UX to pick built trees from the benchmark history, spin them up, view
them **side by side**, and spin them down. The site answers "which run was
cheaper/faster"; this answers "which one built the better thing".

- **Aesthetic / design / UX review** — rendered UIs next to each other. The
  motivating mode, unsupported today, and the one future benchmark kinds are
  expected to be dominated by.
- **Behavioural review** — the same request fired at each version, diffed.

Non-goal: scoring aesthetics. The lab puts artifacts in front of a human (or,
later, a vision model); it does not judge them.

## 2. Ground truth

All **[X]**, measured against files **on disk** (what the lab starts):

| | count |
|---|---|
| trees under `.worktrees/` | **40** |
| …empty (only `.git`) | 3 |
| …with `Makefile` / `docker-compose.yml` / `Dockerfile` | 37 / 37 / 31 |
| …with neither Makefile nor Dockerfile | 3 (exactly the empty ones) |
| composes declaring exactly one service | **37 of 37** |
| composes with `environment:` / `volumes:` / a restart policy | 7 / 8 / 13 |
| composes with `network_mode:` / `external: true` net / no `ports:` | 2 / 4 / 2 |
| Makefiles parameterising the port | **4** — all `claude-opus-4-7` |
| Makefiles hard-coding `--host 0.0.0.0` | 11 |
| run records | 28 (27 resolve `worktree_path`, 1 stale) |
| trees with **no** run record | **13** |
| sum of `cost_usd` across records | **$228.70** |

**Rung-1 startable: 31/40** — 37 with a compose file, minus the 6 refused in
§4.3. 6 trees need a curated recipe; 3 are unstartable by anything. (v2 said
"37/40 = 100% of what can be started", conflating *has a compose file* with
*rung 1 can start it*.)

Other verified ground truth:

- 28/28 records carry `pushed_commit`, 0 `push_error`, 3 target repos **[X]**.
- `run_service()` (`workspace.py:232`) is grading-time only, single instance,
  no port control. `_resolve_up_down()` (`workspace.py:104`) falls through to
  "assuming the service is already running"; `_wait_for()` (`workspace.py:115`)
  **never raises**, so an unstartable tree grades anyway **[X]**.
- Trees are `git init` checkouts — all four benchmarks set `target_start:
  empty`, so `prepare_worktree` takes the init branch at `workspace.py:58` and
  the clone at `:65` never runs **[X]**. Either way they are not linked git
  worktrees, so removing one is safe.
- No tree ships a `docker-compose.override.yml`, a `.env`, or uses `${VAR}`
  interpolation, and none sets `X-Frame-Options`/`frame-ancestors`, so §4.5's
  iframes will render **[X]**.

## 3. The blockers

### 3.1 Every build of a benchmark wants the same port

The prompt pins one port per benchmark (backend 8003, frontend 8004, hoppers
8005) and both the emitted Makefile and compose bind it. Host-side pins across
the 37 composes: `8004:8004` ×25, `8005:8005` ×8, `8003:8003` ×2, 2 with no
`ports:` **[X]**. Two builds of one benchmark always collide.

Only **4 of 37 Makefiles parameterise the port, and all 4 are
`claude-opus-4-7` [X]** — so a make-based rung would systematically give one
model a different execution path from every other, which is precisely the
unclean comparison this tool exists to avoid.

Executed **[X]**: opus-4-7's backend on :8013 (`make up PORT=8013`) and
astra's on :8014 (bypassing make), both healthy at once, identical fifty-move
fixture request, identical `status: in_progress`. Side-by-side is possible;
it is not supported.

### 3.2 The start contract exists but nothing grades it

All four prompts already mandate a start command and a compose file —
`prompts/eval-chess-backend.md:11,13`, `eval-chess-frontend.md:13,15`,
`eval-chess-frontend-with-fixture.md:34,36`, `eval-game-hoppers.md:519,521`
**[X]**. It is **un-enforced**: no eval item asserts it and `_wait_for` never
fails a run for it. Models comply with the shape (37/40) and ignore the port,
because nothing checks either. The prompt delta for a `PORT` contract is
therefore one sentence, not a new requirement (§7).

### 3.3 Generated compose files are hostile input

Not merely "have hazards" — they are attacker-controlled artifacts written by
an unsupervised agent, and v2's "parse the compose file and refuse" check does
not see what compose actually runs. Reproduced here **[X]**:

- A tree whose `docker-compose.yml` contains no literal `privileged`, no
  `pid`, and no out-of-tree mount resolved to `pid: host`, `privileged: true`
  and `/` bind-mounted at `/hostfs` — the hazards arriving via `include:` and a
  `.env` file, both of which the build agent writes freely.
- A **top-level named volume** with `driver_opts: {type: none, device: <path
  outside the tree>, o: bind}` presents at the service level as
  `type: volume` — invisible to a service-level bind check — and the container
  read a canary file from outside the tree. 8 real trees already declare
  top-level named volumes, so the shape is in-distribution **[X]**.
- A `build.context` pointing outside the tree, combined with `up -d --build`,
  produced an image containing a file from outside the tree **[X]**. Build
  steps have network by default, so a `RUN curl -F` exfiltrates during
  `lab up` behind a normal-looking build log.

This is the single largest change from v2: the hazard check must run against
the **resolved model**, must be an **allowlist**, and must cover the build and
top-level-volume surfaces, not just service-level mounts.

## 4. Design

### 4.1 Shape and sequencing

The supervisor is a library; two milestones consume it.

- **M1 — CLI + read-only status page.** `lab up <build> <build> …` starts N
  instances and serves a **loopback-bound, GET-only status page** that lists
  panes and live state; `lab down` stops them. Starting and stopping stay in
  the terminal.
- **M2 — control plane.** §4.6's API and UI, adding spin-up/spin-down from the
  browser.

v2 claimed M1 "delivers the aesthetic mode end to end" while also calling
spin-up/spin-down "the operator's stated ask" for M2 — both cannot be the
summary, and a *generated static* page cannot reflect §4.8's `crashed` state,
so M1 as v2 specified it would actively misreport instance state. Hence the
read-only status endpoint: a GET-only, token-free, `127.0.0.1`-bound page is
not the authentication problem M2 defers, and it keeps M1 honest.

**M1 does not deliver the operator's full ask** — it delivers side-by-side
viewing and leaves both verbs in the terminal. Stated plainly rather than
elided. The sequencing buys putting the genuinely novel risk (the supervisor,
§4.3–§4.4) before the well-understood part, at the cost of M1's status-page
renderer being superseded by M2's UI.

FastAPI, uvicorn and **Playwright** (§4.5) land in an optional `lab` extra, not
in `dependencies`, so CI's hermetic checks do not grow a server stack. A new
`pagehub_benchmarks.lab` package must be added to `pyproject.toml`'s explicit
`[tool.setuptools] packages` list or it installs silently empty **[X]**.

### 4.2 Addressing a build

Addressed by **`<benchmark-dir>/<worktree-dirname>`** — the only identifier
unique and present for all 40. The run record is *attached* when found, and
**"no record" is a first-class state** (13/40 trees), showing no cost/attempt
metadata and no grader link while remaining fully startable.

- Record→tree join is by `worktree_path`, **falling back to a benchmark-scoped
  basename match that must be unique**. Two basenames each appear under two
  benchmark dirs **[X]**, so a global match can attach a record to the wrong
  build; an ambiguous or missing match degrades to "no record" rather than
  guessing. The 1 stale `worktree_path` (`eval-chess-backend` opus-4-7,
  pre-rename) resolves through this path.
- **`.worktrees/chess-backend/` is a benchmark dir with no
  `benchmarks/chess-backend.yaml`** **[X]** — the same tree as the stale
  record. It has no `kind`, no port fallback, no picker facet. Treated as an
  **orphan benchmark dir**: startable via a curated recipe only, `kind`
  defaulted to `api`, excluded from benchmark facets.
- The third path segment is **not** an effort — `_new_worktree_dir`
  (`run.py:422`) uses `config_slug(h.config)`, and three trees carry
  `gateway-url-http-localhost-4011-auth-token-env-GATEWAY-AUTH-TOKEN` there
  **[X]**. Facet on record fields where a record exists, on the raw slug
  otherwise.
- Segments validated `^[A-Za-z0-9._-]+$`, resolved, `is_relative_to(worktrees_
  root)`, symlinked build dirs refused.

**Materialising an absent tree from its record.** Records are executed input:
a prompt-injected build can rewrite its own record, and records are git-tracked
and pushed, so a hostile `target_repo` is code execution on any box that later
opens that build.

- `target_repo` is **scp-style with no scheme** in every record
  (`git@github.com:pagehub-io/…`) **[X]**, so a scheme allowlist would reject
  all 28. Normalise scp-style to `ssh://<host>/<path>` first, then allowlist
  `(scheme, host)` against `{ssh://github.com, https://github.com}`, refusing
  anything that does not normalise cleanly.
- `git -c protocol.ext.allow=never -c protocol.file.allow=never`.
- Check out **by SHA only** — `pushed_commit` validated `^[0-9a-f]{40}$`,
  `git fetch origin <sha>` then `git checkout <sha>`. `pushed_branch` never
  reaches a git argument (v2 validated the commit and left the branch, from the
  same attacker-writable record, unvalidated); if displayed, it is escaped
  text.

### 4.3 Starting a build — rung 1, compose

```
docker compose -p <slug> -f <tree>/docker-compose.yml -f <override>.yml up -d
```

**Resolve first, then check, then start from the resolved model.** The
supervisor runs `docker compose … config --format json` with the **identical
`-f` set and the identical scrubbed environment** used for `up`, and applies
the check to that output. A raw-YAML scan is bypassed by `include:`,
`extends:`, and `.env` interpolation — demonstrated in §3.3 **[X]**. There must
be no re-read between check and run; 8 trees bind-mount `.:/app` read-write
**[X]**, so a running instance can rewrite its own compose between starts,
which also means the check runs on **every** start and is never cached.

**The check is an allowlist**, not a denylist. Permit the small set of keys the
lab understands (`image`, `build` (constrained below), `command`,
`entrypoint`, `environment`, `ports`, `volumes` (constrained), `depends_on`,
`healthcheck`, `working_dir`, `user`, `restart`, `expose`, `networks`) and
refuse every other key **by name** in the failure message. v2's denylist
already missed `security_opt`, `userns_mode`, `sysctls`, `cgroup_parent`,
`device_cgroup_rules`, `group_add`, `extra_hosts`, and a denylist of compose
keys loses to every compose release.

Additionally refused, from §3.3:

- any **top-level volume** declaring `driver_opts`;
- any `build.context`, `build.dockerfile`, or `build.additional_contexts` not
  resolving inside the tree (`Path.resolve()` + `is_relative_to`);
- `build.secrets`, `build.ssh`, `build.network`;
- any service-level bind `source` outside the tree;
- `privileged`, `pid`, `ipc`, `network_mode`, `cap_add`, `devices`,
  `/var/run/docker.sock` (still refused — now by not being on the allowlist).

**The override file** (written **outside** the tree, so the model's checkout
stays clean; verified that `-f ../elsewhere/ov.yml` keeps the build context at
the first `-f` file's directory **[X]**):

```yaml
services:
  <svc>:
    ports: !override
      - "127.0.0.<n>:<alloc>:<declared>"
    container_name: <slug>-<svc>
    restart: "no"
    mem_limit: <cap>
    pids_limit: <cap>
    cpus: <cap>
```

- **`!override` is mandatory and is the fix for round 2's decisive finding.**
  A plain `ports:` override **concatenates**: verified that the tree's own
  `"8005:8005"` survived alongside the lab's mapping, that the second instance
  then failed with "port is already allocated", and that `docker port` showed
  `0.0.0.0` and `[::]` bindings **[X]**. With `!override` the resolved model
  contains exactly one mapping, `host_ip: 127.0.0.2` **[X]**. Requires a
  compose-version preflight (`!override`/`!reset` are not in older releases;
  verified on Compose v5.1.3 **[X]**).
- **`container_name` must be replaced**, because compose does **not**
  project-namespace an explicit one and 7 trees declare one **[X]** — without
  the rename, two builds of a benchmark collide on the container name, which is
  exactly the comparison the operator wants.
- **`restart: "no"`** because 13 trees set a restart policy **[X]**; otherwise
  a reboot before teardown brings model-generated servers back with no lab
  process running.
- **Resource caps** because nothing else bounds CPU/memory/PIDs, and the OOM
  killer's choice of victim is not the lab's — `~/staging/tmux-claude` is a
  plausible casualty and the operator's CLAUDE.md requires it always be up.
  Scalar keys replace cleanly **[X]**. The supervisor also refuses to start
  beyond a configured max concurrent instance count.
- `<declared>` comes from the tree's own `ports:` mapping. The 2 trees with no
  `ports:` block are the 2 `network_mode` trees, which are refused anyway — so
  there is no fallback rule and none is needed. (v2 invented one citing a
  benchmark-YAML `port` field that does not exist; the port appears only inside
  `grader.env.<name>_url` **[X]**.)
- **The project slug** must be lowercase — `docker compose -p` rejects a
  worktree dirname outright **[X]**, and the project name is the primary
  teardown path. A slugifier lowercases and replaces disallowed characters,
  and must stay injective across all 40 dirnames (asserted by test).

**Refusals** (6 trees): the 2 `network_mode: "container:…"` trees bind inside
another container's namespace, so no host mapping can separate two of them —
"not startable alongside another instance" is the honest answer, not a silently
different topology. (They also name `pagehub-browser-pagehub-browser-1` while
the container here is `pagehub-browser_pagehub-browser_1` **[X]**, so they
would fail regardless.) The 4 `external: true` network trees refuse with
"requires the pagehub-browser compose project up".

### 4.4 Rung 2 — curated recipes, and what is not a rung

`lab/recipes/<benchmark>.yaml` with optional per-tree overrides gives an
explicit start command and port-injection mechanism, for the **6 refused
trees** and any future shape. Bounded one-time authoring cost, **zero
comparability cost** — no prompt edit, no grader change.

**No recipe-parsing rung.** v2's "substitute the port into the `up` recipe"
breaks on the dominant frontend shape: `up: build`, `build: install`,
`install: npm install`, so the `up` line alone fails without `dist/` **[X]**.
Parsing make prerequisite chains is not this tool's job.

**Unstartable** is a terminal state, not a rung (3 empty trees, plus anything
refused with no recipe): a disabled card listing each mechanism tried and why
it failed. Never "assume the operator runs it" — that behaviour in `_wait_for`
is what let broken trees grade.

The mechanism used is displayed on every pane; a container-vs-recipe
comparison is not a clean one.

### 4.5 Isolation of spawned instances

- **The `docker compose` invocation itself is scrubbed.** Compose interpolates
  `${VAR}` from the **invoking** process's environment; the operator's shell
  carries 36 variables matching `TOKEN|KEY|SECRET` **[X]**, so a tree declaring
  `environment: {X: ${PAGEHUB_AUTH_JWT_SIGNING_KEY_PROD}}` — 7 trees already
  declare `environment:` **[X]** — would exfiltrate one, and it appears
  already-substituted in `config` output so the §4.3 check cannot flag it.
  Both `config` and `up` run with an explicitly constructed environment:
  `PATH`, `HOME`, `DOCKER_HOST`/`DOCKER_CONFIG` if set, nothing else.
- **Rung-2 processes** get an allowlist (`PATH`, `HOME`, `LANG`, `PORT`,
  `HOST`). `run_service` calls `Popen(...)` with no `env=`
  (`workspace.py:257`), so today a started tree reads `PAGEHUB_EVALS_TOKEN` and
  `PAGEHUB_EVALS_JWT_SIGNING_KEY` from its own environ **[R]**;
  `codex_cli._subprocess_env` already scrubs exactly this class.
- **Dependency installs.** Rung 1 installs inside the container. Rung 2 uses
  `npm ci` when a lockfile exists (35/35 JS trees ship one **[X]**),
  `--ignore-scripts` where the tree still builds, and a per-tree `.venv` for
  Python — never the lab's interpreter. `npm install` on a four-month-old
  manifest re-resolves `^` ranges to today's versions and runs `postinstall` as
  the operator; that is a *new* supply-chain exposure, which is why installs
  are never implicit.

### 4.6 Screenshots — host-side Playwright, not pagehub-browser

v2 routed screenshots through `pagehub-browser` over a shared docker network.
Round 2 showed this is **non-functional in every configuration §7.4 permitted**
and simultaneously dangerous:

- Container names resolve to **RFC1918 bridge addresses**, which
  `pagehub-browser/api/ssrf.py:88` blocks on `ip.is_private`. With the hatch
  closed — the remediation §8.4 calls for — screenshots fail for every
  instance **[X, with the hatch-closed half read at `ssrf.py:77-88`, not
  executed]**. So the design *required* the hatch it claimed not to entrench.
- Sharing a network with `pagehub-browser` hands model-generated code an
  **unauthenticated, SSRF-capable browser**: a container on that network
  drove it to `http://host.docker.internal:9005/api/health` and got the body
  back **[X]**. Binding `:4010` to loopback does not close this — container-to-
  container traffic never touches the host publish.
- It also contradicted §4.3, which refuses the 4 trees that join
  `pagehub-browser`'s network for exactly this reason.

**Resolution: drive Playwright from the host**, in the same `lab` extra that
takes FastAPI. It reaches `127.0.0.<n>:<alloc>` directly, needs no shared
network, no hatch, and no third party with an open gate. Playwright imports on
this host and chromium is already cached under `~/.cache/ms-playwright`
**[X]**. The screenshot strip takes **instance IDs, never URLs**.

### 4.7 The UX

- **Picker** — filter by benchmark, harness, model, config slug, pass/fail,
  has-record. 23 trees in one benchmark makes filtering mandatory.
- **Tray** — state, URL, mechanism used, mechanisms tried, per-build and
  bulk start/stop.
- **Side-by-side view** — N panes by benchmark `kind`:
  - `web` → iframe with a device-width selector, since hoppers is graded at
    390×844 (`prompts/eval-game-hoppers.md:442`).
  - `api` → one request editor firing at every instance, responses in columns
    with a diff toggle.
- **Screenshot strip** — §4.6, matched viewport per pane.

`kind` is a new benchmark-YAML field: `api` for `eval-chess-backend`, `web` for
`eval-chess-frontend`, `eval-chess-frontend-with-fixture`, **and
`eval-game-hoppers`** (v1 omitted hoppers — 9 trees, the most recent work, the
most visual benchmark there is). A new kind means a new pane renderer, not a
supervisor change. That is the extension point for future benchmark types.

### 4.8 State machine

```
stopped → building → starting → running ⇄ (crashed)
                  ↘ failed              ↘ stopping → stopped
unstartable (terminal)
```

- **`building`** is distinct because `up -d --build` spends minutes before
  anything binds; folding it into `starting` under a 300 s timeout makes a slow
  first build indistinguishable from a broken app, and the 23-tree fixture
  benchmark will hit it. 6 composes are image-only and build nothing **[X]**,
  so they skip it.
- **`failed`** is reachable from `building` and `starting` (timeout, hazard
  refusal, non-loopback bind), not only from `running`.
- **`crashed`** carries exit code and log tail, entered by the periodic
  liveness probe — without it a tray reports `running` for a dead process
  indefinitely, the main operability defect for a tool whose premise is
  "keeps it running". `crashed` → `stopped` on teardown.

### 4.9 API (M2)

FastAPI, every route with a Pydantic `response_model`, every body a Pydantic
model, `response_model_exclude_none=True` where fields are optional, schemas in
`pagehub_benchmarks/lab/schemas.py`:

| route | response model |
|---|---|
| `GET /api/builds` | `BuildListResponse` |
| `POST /api/instances` (`StartInstanceRequest`) | `InstanceResponse` |
| `GET /api/instances` | `InstanceListResponse` |
| `GET /api/instances/{id}` | `InstanceResponse` |
| `GET /api/instances/{id}/log` | `InstanceLogResponse` |
| `DELETE /api/instances/{id}` | `InstanceResponse` |
| `POST /api/instances/{id}/call` (`ProxyCallRequest`) | `ProxyCallResponse` |
| `POST /api/screenshots` (`ScreenshotBatchRequest`) | `ScreenshotBatchResponse` |

Plus `GET /` (the UI) and a shared `ErrorResponse`. `ProxyCallRequest` carries
a typed body (`str | None` plus an explicit content type) — never `dict`/`Any`,
per the operator's API rules, and a generic proxy is exactly where one gets
reached for. `InstanceResponse`: `state`, `url`, `mechanism`,
`mechanism_attempts`, `declared_port`, `allocated_port`, `host`, `started_at`,
`last_error`.

## 5. Lifecycle and failure modes

- **Teardown** is `docker compose -p <slug> down -v` per instance (the `-v`
  matters: 4 trees carry `node_modules` volumes, named and anonymous, at a few
  hundred MB each, project-scoped so a fresh one per instance per start).
  `lab down --all` sweeps by project prefix. Containers are **not** children of
  the lab process, so the registry is the *primary* teardown path, not a
  crash-recovery nicety.
- **Orphan reaping never signals on a bare PID** — a recorded PID is not stable
  across reboot or PID reuse, and the plausible victim here is the tmux-claude
  session the operator's CLAUDE.md says must never be disturbed. Match process
  start-time **and** a `bench-lab-` argv marker; for containers, the project
  label.
- **Port leases** per build for the session, so an open pane survives a
  restart. Bind-then-release to allocate, re-probe before start, 3 retries on
  `EADDRINUSE`.
- **Disk.** `.worktrees/` is 2.4 GB **[X]**; rung 1 adds an image per building
  tree (a Node+Vite image runs ~1 GB) plus per-instance volumes. `lab prune`
  removes `bench-lab-*` containers, **volumes**, images, dangling layers and
  the checkout cache; the UI reports the total.

## 6. Security

The lab executes **model-generated code** and serves it over HTTP. v1 claimed
it "does not widen" the existing exposure; that was false and is not repeated.

### 6.1 What it widens

The runner executes model code from an operator-initiated CLI run. The lab adds
concurrent instances, long-lived exposure, dependency re-resolution months
later, image builds, and — at M2 — a network-triggerable start. M1 carries all
but the last.

### 6.2 Actors

- **An attacker controlling a built tree's content** (prompt injection during
  the build, or a tampered target repo): the base case is code execution as the
  operator, which the runner already accepts. What the lab must not do is
  *escalate* that to host filesystem and secret access — §3.3 showed three
  paths that v2 would have opened. Mitigations: resolved-model allowlist check
  (§4.3), build-context and top-level-volume constraints (§4.3), scrubbed
  compose environment (§4.5), resource caps and a concurrency cap (§4.3), no
  implicit installs (§4.5), idle timeout (default 60 min, hard cap 8 h),
  visible stop-all.
- **A web page the operator visits** (M2): DNS rebinding defeats loopback
  binding — the page re-resolves its own hostname to `127.0.0.1`, so its
  `fetch` is same-origin and CORS never applies. **No `TrustedHostMiddleware`
  exists anywhere in this stack [X]**. Mitigation: exact-match `Host`
  allowlist of **`127.0.0.1:<port>` only** (not `localhost` — different origin
  for `localStorage` **[X]**, so a fragment-delivered token would be absent at
  one of them), rejected with 421 before routing; plus a per-process
  `secrets.token_urlsafe(32)`, compared with `hmac.compare_digest`, required on
  every `/api` route including GETs, carried in a **custom header** so any
  cross-origin attempt must preflight. Delivered via the startup URL fragment,
  held in `localStorage`, never a cookie. No CORS middleware at all.
- **A hostile page in a pane.** Each instance gets a distinct host in
  `127.0.0.0/8` (instance *n* at `127.0.0.<n+1>`, capped at 254; Linux-only —
  macOS configures only `127.0.0.1` by default), the lab UI alone on
  `127.0.0.1`. Round 2 verified in real chromium that this **does** separate
  cookie jars and `localStorage`, and that a pane's attempt to set a cookie for
  `domain=127.0.0.1` was rejected **[X]** — so `allow-same-origin` is
  defensible, which matters because dropping it breaks `localStorage` for the
  SPAs. Withhold `allow-popups`, `allow-modals`, `allow-top-navigation*`; add
  `referrerpolicy="no-referrer"` and `allow=""`. **This holds only if §4.3's
  `!override` is correct** — otherwise the instance is simultaneously reachable
  at `127.0.0.1:<declared>` and back in the shared jar.
- **Residual: a pane as a client of other loopback services.** Per-instance
  hosts fix cookie scope; they do nothing about a pane's JS issuing `fetch` at
  `127.0.0.1:9005` (tmux-claude), `:8002` (evals) or `:4010`
  (pagehub-browser, no credential needed **[X]**). iframe sandboxing cannot
  stop it and Private Network Access does not gate private→private. Stated as
  residual with its bound: the runner already executed this code with full host
  access, so the delta is **duration and concurrency, not capability**. **[R]**
- **The proxy route**: target reconstructed from the registry; body accepts a
  **rooted path only**, `^/[^/]` — verified to block the protocol-relative
  escape, `httpx.URL(...).join('//evil.com/a')` yielding `http://evil.com/a`
  **[X]** — `follow_redirects=False`, response size capped.
- **Rendered output** (log tails, proxied bodies) is attacker-chosen bytes:
  `textContent` only, never `innerHTML`/`|safe`; `Content-Security-Policy:
  default-src 'self'; script-src 'self'`; byte cap.

### 6.3 Loopback binding is verified, per rung, repeatedly

v2 specified one check in terms that are wrong for containers: inside a rung-1
container the app listens on `*:8004` **correctly and necessarily [X]**, so a
check against that view fails every healthy container.

- **Rung 1**: every `HostIp` in `docker inspect … NetworkSettings.Ports` must
  parse inside `127.0.0.0/8`; any `0.0.0.0` or `::` entry fails the instance
  with `bound-non-loopback`.
- **Rung 2**: enumerate host listening sockets owned by the instance's process
  tree and reject any non-loopback bind **including IPv6** — `HOST=127.0.0.2`
  does not constrain a server that also opens `[::]:port`, and 11 of 37
  Makefiles hard-code `--host 0.0.0.0` **[X]**.
- **Repeating, not one-shot**, folded into §4.8's liveness probe: a late bind
  (vite HMR, a uvicorn reload worker, a second thread) after a passing
  readiness check is otherwise invisible. **[R]**

### 6.4 pagehub-browser

Its SSRF hatch is open, it needs no credential, and it listens on `*:4010`
**[X]** — a pre-existing exposure this plan surfaced, does not cause, and (as
of §4.6) **no longer depends on in any way**. Tracked as separate work: bind
`:4010` to loopback, and replace the all-or-nothing hatch (`ssrf.py:113`
returns early past every check) with a scoped `host:port` allowlist. v2 made
the lab refuse to start while the hatch was on — which, since that is the box's
current state, meant the lab refused to start today; with §4.6 the lab has no
interest in the setting at all and the coupling is gone.

Detecting the hatch is no longer needed, which is just as well: `GET /health`
returns `status/commit/engine/env/live_sessions` and does **not** expose
`browser_allow_private_hosts` **[X]**, so v2's "it reads the setting" was never
implementable.

### 6.5 Twin overrides

Local-only, never deployed, so the global CLAUDE.md twin rules do not apply and
the lab accepts no `X-Twin-*` headers. Stated explicitly because the lab grows
dependency-URL-shaped settings and the twin middleware's gate is
`env == "development"`, which a local-only tool always is — adding it "for
consistency" would hand the rebinding attacker an outbound-redirect primitive.

## 7. The contract question

- **(a) Rungs + curated recipes, nothing else changes.** Zero comparability
  cost. 31/40 via compose, 6 via recipes, 3 unstartable. **Recommended.**
- **(b) (a) + a `PORT` contract for future runs.** The prompt delta is one
  sentence, but enforcement needs an **eval item**, which breaks comparability
  through the **grader collection**, not the prompt — so record the collection
  identifier and item count in the run record, and identify the prompt by a
  **content hash of the recorded `rendered_prompt`** (present for 26 of 28
  records; the two oldest have no snapshot) rather than a hand-maintained
  version someone forgets to bump.
- **(c) Contract now, re-run the matrix to re-baseline.** Costs **at least
  $228.70** **[X]** plus wall time.

(v2 listed (a) and (d) as separate options with verbatim-identical text; they
were one option and are now one.)

**Recommendation: (a) now, (b) decided separately.** The lab does not need a
prompt change to ship, and bundling one turns a tooling decision into a
benchmark-history decision.

## 8. Testing

- **Unit** (CI, hermetic, subprocess faked as `test_codex_cli.py` fakes codex):
  port allocator (collision, exhaustion, reuse, lease), project-slug
  injectivity across all 40 dirnames, build-address validation (traversal,
  symlink, bad segments), record→tree join (stale, missing, ambiguous
  basename), `target_repo` normalisation incl. scp-style and `ext::`/`file://`
  rejection, registry reap (start-time + marker), state machine incl.
  `building`, `crashed`, `stopping`.
- **`compose config` tests** (need the compose binary, no daemon) — this is the
  layer that catches round 2's decisive finding, which a shape-only assertion
  on generated YAML would have passed:
  - resolved model has **exactly one** published mapping with a `127.0.0.x`
    `host_ip`;
  - `restart` is `"no"`, `container_name` is the lab's, caps are applied;
  - the hazard allowlist refuses each §3.3 escape: `include:`-hidden
    `privileged`/`pid`, `.env`-interpolated `privileged`, top-level
    `driver_opts`, out-of-tree `build.context`;
  - the scrubbed environment leaves `${VAR}` interpolation nothing to take.
- **Fixtures are the real trees that break it [X]**: the 2 `network_mode`
  composes, the 4 `external: true` composes, the 7 `container_name` composes,
  the 13 with restart policies, the 8 with `volumes:`, the 7 with
  `environment:`, the 2 with no `ports:`, the 6 image-only, the 3 empty trees,
  astra's un-parameterised Makefile, the vite prerequisite-chain Makefile, the
  stale-`worktree_path` record, a record-less tree, the orphan
  `chess-backend/` dir, and the two duplicated basenames.
- **Integration** (`@pytest.mark.docker`, deselected by default, part of the
  local eval gate per the operator's SDLC rules — not CI): start one known-good
  tree end to end and assert `NetworkSettings.Ports` is loopback-only; start
  two builds of one benchmark and assert both run simultaneously.
- **Manual acceptance**: the §3.1 path — two chess backends, two distinct URLs,
  same request to both, stop both, ports released.

## 9. Open questions

1. §7 — (a) alone, or (a)+(b)? Recommendation is (a) now, (b) separately.
   Operator's call.
2. Screenshot captures: durable artifacts under `results/`, or ephemeral?
   Storing enriches the published site but grows the repo.

## 10. Answered, recorded so they are not re-asked

- Per-pane grader results are blank for 13/40 trees, so a pane links to the run
  page when a record exists and shows "no record" otherwise.
- The compose glob is `docker-compose.yml` — all 37 trees use `.yml`, none use
  `.yaml` **[X]**; §2's table now matches.

## 11. Review log

- **Round 1** (2026-09-18, design + security, clean context): **10 critical,
  26 important, 13 nits.** v1's coverage numbers were measured with
  `git ls-files` instead of disk, over-sizing the ladder ~3×; v1 asserted
  nothing forces a start contract when all four prompts mandate one; "instances
  bound to loopback" and "different port ⇒ different origin" were both
  disproved by execution; §7 omitted the actors the control plane creates.
- **Round 2** (2026-09-18, two fresh instances, design + security): **8
  critical, 17 important, 17 nits.** Both reviewers independently found the
  `ports:` concatenation defect. v2's compose rung — itself a round-1
  remediation — introduced three host-compromise paths `docker run` lacked
  (`include:`/`.env`, top-level `driver_opts`, out-of-tree `build.context`),
  all reproduced. v2's screenshot design was non-functional in every permitted
  configuration *and* handed instances an unauthenticated SSRF browser; it is
  replaced wholesale in §4.6. v2 carried a false `[X]` claim ("4 composes
  declare two services"; all 37 declare one), the same defect class that
  rejected v1. Round-2 mechanisms that **held**: per-instance-host cookie/
  localStorage separation, the control-plane auth shape, the proxy path
  restriction, scalar compose merge semantics.
- **Round 3** (2026-09-18, two fresh instances, design + security): **9
  critical, 13 important, 12 nits.** Both reviewers independently found the
  same escape class through different keys: a tree declaring a **top-level
  `external: true` volume or network** passes every §4.3 check — the
  service-level entry resolves to `type: volume`, so the bind-source rule
  never fires, and the `driver_opts` ban is the wrong predicate. Executed, an
  instance read `pagehub-evals`' Postgres data directory and reached
  `pagehub-browser:4010` — the unauthenticated browser §4.6 was rewritten to
  avoid. Also executed and confirmed here: **§4.3's check is advisory**, because
  `up` re-reads the path after `config` (verified: the check saw
  `command: [echo, BENIGN]`, `up` ran `privileged: true, pid: host` and printed
  the host's hostname); **`env_file:` inlines any host file into
  `environment`** under a fully scrubbed `env -i`, with the `env_file` key
  erased from `config` output so the check cannot see it; **`build.tags`
  overwrites local image tags** such as `postgres:16`, which other projects
  then start without pulling, surviving `lab prune` and outliving the lab;
  and `ports: !override` covers only the named service, so a second service
  publishes on `0.0.0.0`.
  Two further false `[X]` counts (the third consecutive round to find one):
  "8 trees declare top-level named volumes" — **2 do**; "8 trees bind-mount
  `.:/app` read-write" — **6 do**. Both were §2's count of trees with any
  service-level `volumes:` key, reused for a different claim. Every other
  `[X]` in §2 was independently reproduced exactly.
  **§4.3's slug is not injective**: 40 dirnames, **38 unique** — two appear
  under both `eval-chess-frontend/` and `eval-chess-frontend-with-fixture/`,
  which §4.2 already recorded and §4.3 contradicted. Worse, a lowercased
  dirname is exactly the default project name the grader already produced:
  live on this box are `claude-code__claude-opus-4-5__effort-medium__
  20260522t144523z_default` and a `..._chess_node_modules` volume, which
  `lab up` would adopt and `down -v` would destroy. Fix: prefix
  `bench-lab-<benchmark-dir>-<dirname>`.
  Round-3 mechanisms that **held**: `!override` replace semantics and
  loopback-only listener, scalar merge, `config` surfacing `include:`/`.env`
  hazards and absolutising `build.context`, profiles+`depends_on` not being a
  differential, an attacker `.env` being unable to hijack `DOCKER_HOST`/
  `COMPOSE_FILE`/`-p`, `down -v` sparing external volumes, and the proxy
  path rule.
  Independent of architecture: **Playwright `page.screenshot()` times out on
  this host at defaults** and needs
  `--disable-gpu --use-gl=swiftshader --disable-software-rasterizer`
  (verified working, 1.1 s). §4.6's `[X]` tested only that Playwright imports.

- **Outcome: handed back at the cap.** Every failure across three rounds is in
  one place — attempting to make an attacker-controlled compose file safe.
  Each round's fix opened new holes in the next. The product design (rungs,
  panes, addressing, state machine, per-instance hosts, control-plane auth)
  has been stable since v2 and round 3 found nothing new in it.
  **Recommended v4 shape:** the lab authors its own run spec per benchmark and
  never interprets model-written YAML. The tree's source code still runs — that
  is the tool's purpose — but the orchestration is lab-controlled. That removes
  by construction: `env_file`, top-level `networks`/`volumes`, `build.tags`,
  multi-service overrides, and the check-vs-run divergence, i.e. 7 of round 3's
  9 criticals. The remaining two are a correct slug and the count corrections.
