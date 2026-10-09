# Fair Benchmarks — public repos + pagehub pages

Status: **draft for review** · 2026-10-07

## Problem

We are converting popular AI coding benchmarks into *fair* versions: a problem
statement that actually states its requirement, and a verifier written blind to
the reference fix. One conversion exists (SWE-bench Verified
`astropy__astropy-13033`) and its evidence is strong:

| condition | model | result |
|---|---|---|
| original statement, dataset grader | deepseek-v4p1-flash | 0/10 |
| original + issue comment thread | deepseek-v4p1-flash | 0/10 |
| rewritten statement, blind verifier | deepseek-v4p1-flash | **10/10** |
| rewritten statement, blind verifier | glm-5p3-flash | **10/10** |
| rewritten statement, blind verifier | gpt-oss-120b | 2/10 |
| rewritten statement, blind verifier | nemotron-lightning | 2/10 |

**All of this work currently lives only in a session scratch directory and will
be lost when the session ends.** ~108KB of statements, verifiers, reference
implementations and sandbox scripts. Preserving it is the first priority; the
publishing structure is the second.

## Goal

A public, citable home for the conversion work, with a hosted page per
benchmark and one overview, reachable from pagehub.io.

## Shape

```
pagehub.io  (pagehub-marketing, private, Cloudflare Pages)
  └─ link ──▶ fairbench.pagehub.io/            overview page
                ├─ /swebench-verified/         one page per conversion
                └─ /<next-benchmark>/
                        │
                        └─ links out to ──▶ github.com/pagehub-io/fairbench-<benchmark>
```

### Repos — one per benchmark conversion

| repo | visibility | holds |
|---|---|---|
| `fairbench-swebench-verified` | **public** | per-instance statements, verifiers, reference impls, harness, results |
| `fairbench-site` | **public** | the overview + per-benchmark pages (static) |

Naming follows the existing public convention (`eval-chess-backend`,
`eval-game-hoppers`): a stable prefix plus the subject. `fairbench-` reads as a
family and leaves room for `fairbench-swebench-lite`, `fairbench-humaneval`.

### Repo layout — `fairbench-swebench-verified`

```
README.md                     method, status, how to reproduce, what is NOT settled
instances/
  astropy__astropy-13033/
    original_statement.md     what the benchmark ships
    statement.md              our rewrite
    verifier.py               blind-authored, 40 cases
    reference_impl.py         validates the harness
    FINDINGS.md               what was wrong, what changed, what is still open
    results.json              per-run outcomes, modes, cost, wall time
harness/
  proxy.py run_instance.sh grade.sh fair_multi.sh ladder.sh smoke_models.sh
  README.md                   the three gates and why each exists
method/
  BLIND_RECONSTRUCTION.md     the procedure, and the prompt used
  PROMPT_BLIND.txt
```

### Why a separate site repo

A public repo cannot call the private `pagehub-infra` reusable workflows, so
hosted pages from public repos go through the private
`pagehub-deploy-public-repos` harness (manual dispatch). Keeping the site in one
repo means **one** deploy path to maintain regardless of how many benchmark
conversions follow, and the conversion repos stay pure content — no CI, no
deploy secrets, nothing to keep in sync.

Alternative considered: a subdomain per benchmark
(`swebench.fairbench.pagehub.io`). Rejected — a DNS entry and a deploy per
conversion, for no gain over a path.

## Sequencing

1. **Preserve first.** Create `fairbench-swebench-verified`, commit the 108KB of
   artifacts from scratch. Nothing is published or linked yet. This de-risks the
   data loss independently of every decision below.
2. Close the `{found}` ambiguity in the statement (one line), re-run the two
   rungs that tripped on it, update `results.json`.
3. Build `fairbench-site` — overview + the SWE-bench page. Deploy to
   `staging.fairbench.pagehub.io` via the public-repo harness. Verify.
4. **Only then** add the pagehub.io link: `pagehub-marketing` gets one `<a>`,
   cut `staging-*` tag → verify on staging.pagehub.io → cut `preprod-*` tag.
   Staging before preprod, per the fleet ordering rule.

## What I am NOT doing without a decision from the owner

- **Publishing an unfinished entry.** `{found}` is a known, documented gap in
  the statement. Step 1 preserves the work privately-in-public (a public repo
  nobody is linked to yet); step 3 is what actually announces it. If you would
  rather close `{found}` before anything is pushed, say so and I will reorder.
- **Licence and citation.** A benchmark-derivative repo needs both. SWE-bench
  Verified is MIT (OpenAI's subset of the SWE-bench corpus, itself MIT), and the
  astropy code under test is BSD-3. Our rewrites are new text about an existing
  dataset. I propose MIT for our contents plus a `NOTICE` naming upstream, but
  this is a licensing call, not a technical one.
- **Naming.** `fairbench` is my proposal, not a decision.

## Risks

| risk | mitigation |
|---|---|
| Scratch lost before step 1 | Do step 1 first, before any other work |
| Publishing a wrong fairness claim | Every claim on the page is an executed run with its output committed; `FINDINGS.md` names what is unsettled |
| pagehub.io regression | One `<a>` in a static file; staging tag verified before preprod; `404.html` and canonical invariants untouched |
| Conversion repo rots vs harness | Harness and instances in one repo so they version together |
| Someone reproduces and disagrees | That is the point — the verifier and reference impl ship, so disagreement is checkable |

## Security review ("what does an attacker who controls X get?")

- **Public repo contents** — no secrets. Scripts read `~/.config/fireworks.key`
  by path; scanned, no literal keys. Run outputs contain model-authored patches
  and pytest output only. *Verified by grep for `fw_`/`sk-`/`Bearer` across the
  committed subset: zero hits.*
- **A reader of the public repo** gets the method, the statements, the verifiers
  and our results. All intended. No infrastructure topology beyond "we run
  official SWE-bench images in a network-isolated container".
- **The CONNECT proxy allowlist** (`api.fireworks.ai:443`) ships in the open.
  It is a benchmark-isolation control, not a security boundary for anything of
  ours; publishing it lets others verify the isolation claim.
- **pagehub.io write access** is unchanged — no new tokens, no new workflow, no
  new deploy path on the marketing repo.

## Open question for the owner

The overview page's framing. The honest version is "benchmarks we have found
problems in, and our corrections" — which names upstream projects as having
flaws. That is defensible (every claim is an executed run) but it is a public
position about other people's work. Confirm the tone before step 3.
