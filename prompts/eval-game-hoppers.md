{# Operator-side note (stripped at render time, never sent to the model):
   Before each attempt, the runner POSTs to pagehub-browser's
   /v1/admin/reset-sessions endpoint (Bearer auth from the runner-env
   var PAGEHUB_BROWSER_ADMIN_TOKEN). This kills the leaked-sessions
   capacity cascade that tanked the 2026-05-22 chess-frontend sweep:
   a prior attempt or run that filled MAX_SESSIONS would 503 every
   subsequent /v1/sessions call and the grader saw the entire battery
   as failed before the model even got a chance. If the admin endpoint
   isn't reachable (404 / 401 / unset token), the runner logs and
   continues — older deploys keep working. -#}
Build a **single-tap hopper web game** as a pure-frontend TypeScript
application. It runs on **port 8005** (port 8003 is reserved for
eval-chess-backend, 8004 for eval-chess-frontend; pick 8005). No backend
— all state lives in the browser, no database, no API server, no auth.

== THE EVAL THAT WILL GRADE YOUR BUILD ==
The grader is the FixtureBundle below. Every request will be made
against your service in this exact order with these exact assertions.
Build the contract that satisfies these requests verbatim; do not add
fields beyond what they read; do not change DOM testids or attribute
names.

```json
{{ grader_fixture }}
```
== END EVAL ==

## The game

Inspired by Crossy Road and Frogger, **not a clone of either**. The
player is a "hopper" that auto-faces forward. Gameplay:

- Lane-hopping endless game. The playfield scrolls; the player advances
  forward one lane per tap.
- **Single-tap input**: a tap (mouse click / touch) **anywhere** on
  `[data-testid="game-root"]`, OR a click on a dedicated
  `[data-testid="hop"]` zone, OR pressing Space on desktop, all do
  exactly the same thing: hop forward one lane. No other input. No
  swipes, no arrow keys, no D-pad.
- Lanes scroll left or right with obstacles themed to the species.
  Mis-time the hop → collide / fall → game over.
- **Score = number of lanes successfully crossed since start.**
- Game over → instant restart on the next tap (no menu, no dialog
  blocking the restart).

### Species (PICK ONE — and explicitly NOT a frog)

The hopper species the grader will accept must be **exactly one of**:

| species      | setting               | obstacles / hazards                                   |
|--------------|-----------------------|-------------------------------------------------------|
| kangaroo     | outback desert        | jeeps in road lanes, drying riverbeds as gaps         |
| rabbit       | suburban street       | cars + bikes in road lanes, storm drains as gaps      |
| grasshopper  | lawn                  | lawnmower blades + sprinklers + gardener sandals      |
| flea         | a row of dogs in a park (each dog = one "lane") | hopping between scratching dogs |
| jerboa       | sand dunes            | sand-snakes + camel feet + dune-edge falls            |

**Hard guardrail — DO NOT BUILD A FROG GAME.** No frog. No toad. No
amphibian. No lily-pads. No "crossing the road to reach a swamp." This
is a Frogger-distant build deliberately; we don't want the legal
similarity. Pick one of the five species above and theme the entire
build around it.

You also need to write the chosen species into `[data-testid="player"]`
as `data-hopper-species="<species>"`. The grader asserts this is one
of the five names above with a CSS allowlist; a frog will fail the
fixture outright.

The README in eval-game-hoppers documents which species this build
picked, so the next build can pick a different one.

## The 5 pillars — DOM contract (load-bearing — the grader binds to it)

The grader is **pagehub-browser-driven**: it loads the page, clicks /
types by `data-testid`, calls `find` with CSS selectors to assert
counts, and reads attributes off elements.

### Pillar 1 — Instant restart

```
<div data-testid="game-root" data-game-state="playing|game-over"> ... </div>
<div data-testid="game-state">playing|game-over</div>      <!-- text matches data-game-state -->
<div data-testid="restart-hint">Tap to restart</div>       <!-- optional UX cue; not load-bearing -->
```

- `data-game-state` on `[data-testid="game-root"]` is the **single
  source of truth** for game state. It reads **exactly** `"playing"`
  or `"game-over"` — no other values. No `"menu"`, no `"paused"`, no
  `"ready"`.
- `[data-testid="game-state"]` is a separate element whose **text
  content** mirrors that value (`"playing"` or `"game-over"`). The
  grader may read either; keep them in sync.
- After game-over, **any** tap / click on `[data-testid="game-root"]`
  flips `data-game-state` back to `"playing"` within 200 ms, the score
  resets to `0`, the player snaps back to `data-current-lane="0"`.
- `[data-testid="restart-hint"]` MAY show "Tap to restart" or similar
  — purely for UX. The grader does not assert on it.

### Pillar 2 — Shareable score

```
<a data-testid="share-url" data-url="https://<host>/?score=<N>&seed=<S>">Share</a>
<div data-testid="shared-score-banner">You scored 6 on seed hop-test-001</div>
<button data-testid="play">Play</button>
```

- On game-over, `[data-testid="share-url"]` carries the full
  shareable URL in its **`data-url` attribute** (NOT just `href`; the
  grader reads `data-url` so there's no ambiguity about anchor
  semantics). Shape: `https://<host>/?score=<N>&seed=<S>` where N is
  the score just achieved and S is the seed used for that run.
- Visiting that URL in a **fresh navigate** (no auto-play) renders
  `[data-testid="shared-score-banner"]` whose **text content includes
  the score** (the grader greps for the score digit in the banner's
  text). A `[data-testid="play"]` button starts a fresh run with the
  same seed.
- The URL must be **copy-paste-shareable** — no auth, no JS-only
  state, no fragment that breaks on copy.

### Pillar 3 — Content-creator-friendly difficulty

URL params override defaults:

| param          | default | effect                                                         |
|----------------|---------|----------------------------------------------------------------|
| `?lane-speed`  | `1.0`   | multiplier on lane scroll speed                                |
| `?lane-density`| `0.5`   | fraction of lane cells that spawn an obstacle                  |
| `?seed`        | random RFC-friendly token (e.g. `hop-9f2a`) | RNG seed for lane layout / obstacle placement |

- `[data-testid="difficulty-active"]` reads back the active values as
  text content, exact format:

  ```
  lane-speed=<value>|lane-density=<value>|seed=<value>
  ```

  using the **literal URL-param values verbatim** (no float
  re-formatting). So `?lane-speed=0&lane-density=0&seed=hop-test-001`
  yields `"lane-speed=0|lane-density=0|seed=hop-test-001"`. The
  grader parses this string with that pipe-separated format.

- `?creator=1` reveals `[data-testid="creator-panel"]` containing
  sliders that two-way-bind to `lane-speed` and `lane-density`.
  Changing a slider updates the URL hash; a reload preserves the
  values. (The fixture does not assert this; the prompt requires it.)

### Pillar 4 — Zero install friction

- Page load → `[data-game-state]` reaches `"playing"` within 2 s. No
  modal, no dialog, no permission prompt, no "install" card, no
  cookie banner — nothing to dismiss before the game starts.
- The DOM contains **zero** elements matching any of the
  auth-shaped selectors (the grader asserts `count == 0` on each):
  - `[data-testid*="sign-in"]`
  - `[data-testid*="sign-up"]`
  - `[data-testid*="login"]`
  - `input[type="password"]`
  - `input[type="email"]`

  Pure anonymous play. No registration, no email gate, no "guest
  mode" toggle — just the game.

### Pillar 5 — Payment via ads

```
<div data-testid="ad-slot-banner"
     style="width: 320px; height: 50px; ...">AD SLOT (320x50 banner)</div>

<!-- on game-over only -->
<div data-testid="ad-slot-interstitial"
     style="width: 300px; height: 250px; ...">
  AD SLOT (300x250 medium rectangle)
  <button data-testid="ad-close">Close ad</button>
</div>
```

- `[data-testid="ad-slot-banner"]` exists **while playing**. Visible
  placeholder div labeled `"AD SLOT (320x50 banner)"` in dev mode.
  Valid IAB-standard dimensions (`320x50` mobile banner OR `728x90`
  leaderboard).
- `[data-testid="ad-slot-interstitial"]` appears **on game-over**,
  between runs. `[data-testid="ad-close"]` is a button that dismisses
  it and reveals the restart hint. Valid IAB dimensions
  (`300x250` medium rectangle OR a full-screen container).
- **No real ad code loaded** — these are placeholders. Real ad
  provider integration is a documented v2 follow-up; v1 just proves
  the integration points exist at the right shape.

### Gameplay shape (theme-agnostic — graded)

**Player**
- `[data-testid="player"]` exists with `data-hopper-species="<one of
  the 5 species above>"` and `data-current-lane="<int>"`.
- `data-current-lane` starts at `0` and increments by 1 for each
  successful hop (zero-indexed).
- `data-player-x` (integer 0..100) — the player's lateral position as
  a percent across the current lane. Starts at `50` on every lane
  entry. The fixture doesn't pin this in the graded path, but it's
  load-bearing for the log-riding mechanic below.

**Lanes**
- `[data-testid="lane-<index>"]` for each visible lane (numbered
  relative to the player; lane 0 = the lane the player currently
  occupies). Visible viewport is 2 lanes behind + 6 ahead = 9 lanes.
- Each lane carries a `data-lane-kind` attribute declaring its kind:
  one of `"bank"`, `"grass"`, `"road"`, `"riverbed"`. The kind is a
  pure function of the absolute lane index (see "Lane-kind cadence"
  below), so the rendering is stable across seeds for any given
  lane.

**Lane-kind cadence** (load-bearing — the grader counts these)

| absolute lane index | kind       | meaning                                          |
|---------------------|-----------|---------------------------------------------------|
| `≤ 0`               | `bank`    | safe — the kangaroo's starting riverbank          |
| `n % 3 == 1`        | `grass`   | safe rest lane — wait here as long as you want    |
| `n % 3 == 2`        | `road`    | vehicles drift across; collision = die            |
| `n % 3 == 0` (n>0)  | `riverbed`| logs drift across; must be ON a log or die        |

At the start of the run (world.lane = 0) the rendered 9-lane
viewport spans absolute lanes -2 through 6, so the kind counts are
**3 bank** (-2, -1, 0), **2 grass** (1, 4), **2 road** (2, 5),
**2 riverbed** (3, 6). The grader binds to these exact counts AND
to the cadence (asserts lane 1 is grass and lane 3 is riverbed
specifically — same counts under a different starting offset would
not satisfy the grader).

**Objects on each lane**
- `[data-testid="obstacle-<rel-lane>-<n>"]` for each obstacle in
  each lane.
- Every obstacle carries `data-obstacle-kind` = `"vehicle"` or
  `"log"`.
- Riverbed lanes ALWAYS spawn **exactly 3 logs**, uniformly spaced
  at approximate xBase positions `17%`, `50%`, `83%` (+ small
  jitter). This guarantees a log is always within reach of lane
  center, so the lane is always hopable. The grader asserts the
  log count for every visible riverbed.
- Road lanes spawn 0..4 vehicles per slot-density coin flip
  (a road lane with no vehicles is fine — it's a free lane). The
  grader does NOT pin road vehicle counts; that's a per-seed
  variable.

**Score**
- `[data-testid="score"]` is an element whose **text content** is the
  current score (a non-negative integer, no commas / formatting).
- `[data-score]` attribute somewhere on the document MAY mirror the
  same value; not asserted by the fixture but a useful convention.

### Real-time gameplay (required but NOT directly graded)

The fixture pins down the lane-kind shape via deterministic mode.
The behaviors below are required for the game to actually be
*playable* in real-time mode, but they're timing-flaky to assert
deterministically so the fixture doesn't bind to them directly:

- **rAF drift** — in non-deterministic mode, vehicles and logs
  drift laterally via a `requestAnimationFrame` loop. Drift
  direction alternates by lane parity (so adjacent lanes go
  opposite ways). Drift speed = `lane-speed` URL param multiplier ×
  per-object speedMul. In deterministic mode the rAF loop does NOT
  run — objects render at their xBase positions and stay there.
- **Road collision** — on a road lane, if any vehicle's hit-box
  (~±half its `data-width-pct`) overlaps the player's
  `data-player-x` after the grace window expires, the player dies
  (state → `"game-over"`).
- **Riverbed log-riding** — on a riverbed lane:
  - On lane entry, the player tries to board the log nearest to
    lateral center (`data-player-x = 50`). If no log is within
    boarding reach (~±32% of center) the player falls in and dies.
  - While riding, the player's `data-player-x` tracks the log's
    current lateral position. If the log drifts off-screen the
    player dies.
  - With 3 evenly-spaced logs per riverbed (this contract),
    *some* log is always within reach of center, so the riverbed
    is always hopable on entry.
- **Lane-entry grace** — for ~1000 ms after every successful hop,
  road-vehicle collisions are suppressed. The player needs this
  window to see what's drifting on the new lane before the
  hit-box arms. Riverbed log-boarding still resolves on entry
  (no grace there — the log would drift away during grace).
- **Safe lanes** — `bank` and `grass` lanes never trigger collision;
  the player can stay on them indefinitely. This is the rhythm the
  cadence creates: hop through threats, rest on the grass, repeat.

## Mobile design quality (this is a MOBILE game — graded at 390×844)

The grader creates its browser session at a **390×844 mobile
viewport** and audits the layout. The first build of this game passed
the functional contract but was a small letterboxed widget floating
in dead space, leaked a debug string into the HUD, had 16px text-link
controls, and hid the interstitial's close button below the fold — a
design review flagged all of it. The following are now **graded**
(two `/evaluate`-driven design-audit requests):

- **Playfield fills the viewport.** `[data-testid="game-root"]`'s
  bottom edge must reach **≥80% of the viewport height**. No fixed
  `height: 420px` playfield with a tan dead band beneath it — use
  flexbox / `dvh` units so the play area fills the phone screen
  (which also shows more lanes = more lookahead). *Graded:
  `playfield-fills-viewport`.*
- **Difficulty string present but hidden.** `[data-testid=
  "difficulty-active"]` MUST stay in the DOM with its exact
  `lane-speed=…|lane-density=…|seed=…` text (pillar 3 reads it via
  textContent), but in normal play it must be **visually hidden**
  (sr-only: `position:absolute; width:1px; height:1px; clip:rect(0
  0 0 0); overflow:hidden` — NOT `display:none`, which is fine too
  since get-text reads textContent regardless). Surface it visibly
  only under `?creator=1`. Don't make raw telemetry the most
  prominent thing on the player's screen. *Graded:
  `difficulty-active-present` + `…-visually-hidden`.*
- **≥44px tap targets.** `[data-testid="share-url"]`,
  `[data-testid="play"]`, and `[data-testid="ad-close"]` must each be
  **≥44px tall** (iOS HIG / Material minimum). The Share control in
  particular must be a real button, not a tiny text link. *Graded:
  `share-tap-target`, `play-tap-target`, `ad-close-tap-target`.*
- **Interstitial reachable without scrolling.** On game-over,
  `[data-testid="ad-close"]` must be **within the viewport**
  (`top ≥ 0 && bottom ≤ innerHeight`). The interstitial is still a
  sibling of `game-root` (never an occluding overlay — see pillar 1),
  but it must render *in the visible area*, e.g. directly under the
  playfield, not appended below a tall scroll. *Graded:
  `ad-close-reachable-without-scroll-on-mobile`.*
- **Death feedback.** On game-over, render
  `[data-testid="death-cause"]` with **non-empty text** telling the
  player why the run ended — `"Splash!"` / `"drowned"` when they miss
  a log, `"Squashed!"` / `"hit by a jeep"` on a road collision, any
  short message for the `die-after` test path. The first build just
  froze the dead hopper with no explanation. *Graded:
  `death-cause-feedback-present-on-game-over`.*

**Also expected (not directly graded — design quality the review
called out, satisfy them for a good build):**

- **Score is the hero.** It's an endless runner; the score should be
  the most prominent thing in the HUD, not equal-weight with QA
  labels. Drop the visible `STATE: playing/game-over` label from the
  player's view (keep the testid/attribute for the grader).
- **Banner ad at the bottom.** Anchor `[data-testid="ad-slot-banner"]`
  to the bottom of the viewport (standard mobile placement) so the
  playfield owns the top of the screen, rather than wedging the
  banner between the HUD and the playfield.
- **On-theme hazards.** Match the chosen species. For the kangaroo:
  jeeps / road-trains and dingoes on roads (not generic cars + pet
  dogs); the "gap" lanes were specced as **drying riverbeds** (cracked
  tan), so reconcile that with water-readability rather than defaulting
  to generic blue Frogger water.
- **Player faces travel direction.** The hopper moves *up* the screen;
  it should face up, not sideways.
- **Death + hop animation.** A short splash/sink on death and a
  visible hop arc on each tap (the player shouldn't just teleport
  between lanes). Preserve the player's lateral position when hopping
  off a log onto the next lane — don't snap to center.

## Deterministic playthrough mode

A real-time arcade game is **flaky to grade** — requestAnimationFrame
jitter, network races, etc. The grader sidesteps this with a
deterministic mode:

- URL param `?deterministic=1` puts the game in **JS-loop-driven**
  mode: NO `requestAnimationFrame` loop runs at all. The game tick
  advances **only on tap**, not on a timer. Objects render at their
  `xBase` positions and stay frozen there. Collisions are NOT
  computed.
- Combined with `?lane-speed=0&lane-density=0`: lanes do NOT scroll,
  NO obstacles spawn, each tap advances exactly one lane. Score
  equals tap count. (This is the **playthrough** sub-mode the
  grader uses for the 6-tap die-after sequence.)
- Combined with `?lane-density=0.5`: objects DO spawn (logs on
  riverbeds, vehicles on roads) at their `xBase` positions but do
  NOT drift and do NOT trigger collisions. (This is the **shape**
  sub-mode the grader uses to count lane kinds and verify the
  log-spawn contract without timing flakiness.)
- URL param `?die-after=N` (deterministic-mode-only) forces a
  game-over **right after the Nth successful tap**. This is the
  test affordance the grader uses to assert pillar 1 (instant
  restart) and pillar 2 (shareable score) and pillar 5 (interstitial
  ad) without needing the simulator to predict a collision.

The grader makes **two deterministic navigations** per run:

1. **Playthrough**: `?seed=hop-test-001&lane-speed=0&lane-density=0
   &deterministic=1&die-after=6` — clicks `[data-testid="game-root"]`
   six times, asserts `data-current-lane` reads `"1"`, `"2"`, ...,
   `"6"` after each tap, then `data-game-state="game-over"` and the
   share URL contains `score=6` and `seed=hop-test-001`.
2. **Shape**: `?seed=hop-test-001&lane-speed=0&lane-density=0.5
   &deterministic=1` — counts lane kinds, asserts the cadence on
   lanes 1 and 3, asserts exactly 3 logs in lanes 3 and 6.

`?deterministic=1`, `?lane-density`, `?lane-speed`, `?die-after=N`
MUST all be honored exactly; **all other modes (normal real-time
play, the creator panel, sharing) must still work without them.**
The deterministic mode is a test surface, not the default behavior.

## What the grader will do (so you can mentally simulate it)

The grader (pagehub-evals, driven by pagehub-browser) will:

{% raw %}
1. `POST {{pagehub-browser_url}}/v1/sessions` body
   `{headless: true, viewport_width: 390, viewport_height: 844}` —
   open a headless **mobile-sized** session. Every assertion below
   reflects that 390×844 viewport.
2. `POST .../navigate` body
   `{url: "{{eval-game-hoppers_url}}/?seed=hop-test-001&lane-speed=0&lane-density=0&deterministic=1&die-after=6"}`.
{% endraw %}
3. Read `data-game-state` off `[data-testid="game-root"]` → expect
   `"playing"` (pillar 4).
4. `POST .../find` with `input[type="password"]`, `input[type="email"]`,
   `[data-testid*="sign-in"]`, `[data-testid*="sign-up"]`,
   `[data-testid*="login"]` → expect `count == 0` for each (pillar 4).
5. `POST .../find` with the 5-way comma-OR'd species CSS allowlist
   → expect `count == 1` (gameplay shape — the player exists and
   carries one of the five species).
6. Read `data-current-lane` off `[data-testid="player"]` → expect
   `"0"`. Read text of `[data-testid="score"]` → expect `"0"`.
7. Read text of `[data-testid="difficulty-active"]` → expect
   `"lane-speed=0|lane-density=0|seed=hop-test-001"` (pillar 3).
8. `POST .../find` with `[data-testid="ad-slot-banner"]` → expect
   `count == 1` (pillar 5).
8b. **Design audit (playing)** — `POST .../evaluate` returns a
    `key=ok|key=fail` status string; the grader asserts each
    `key=ok` substring: `playfield-fills` (game-root bottom ≥80% of
    viewport height), `difficulty-present` + `difficulty-hidden`
    (difficulty-active in the DOM but visually hidden),
    `share-tap-target` (Share ≥44px tall).
9. Tap-loop ×6: `POST .../click` on `[data-testid="game-root"]`,
   then read `data-current-lane` → expect `"1"`, `"2"`, ..., `"6"`.
   After the 6th tap, `die-after=6` fires: read `data-game-state` on
   `[data-testid="game-root"]` → expect `"game-over"`.
10. Read `data-url` off `[data-testid="share-url"]` → expect the
    response body to contain `score=6` and `seed=hop-test-001`
    substrings (pillar 2).
10b. **Design audit (game-over)** — `POST .../evaluate` returns a
    `key=ok|key=fail` status string; the grader asserts each
    `key=ok`: `ad-close-present`, `ad-close-in-viewport` (close
    button within the 844px viewport, no scroll), `ad-close-tap-target`
    (≥44px), `death-cause` (`[data-testid="death-cause"]` has
    non-empty text), `play-tap-target` (≥44px).
11. `POST .../find` with `[data-testid="ad-slot-interstitial"]` →
    expect `count == 1`. Click `[data-testid="ad-close"]` (pillar 5).
12. Click `[data-testid="game-root"]` → restart. Assert
    `data-game-state` flips back to `"playing"`, `data-current-lane`
    resets to `"0"`, score text resets to `"0"` (pillar 1).
13. **Shape sub-suite** — navigate to
    `?seed=hop-test-001&lane-speed=0&lane-density=0.5&deterministic=1`
    and assert (all via `POST .../find` with `$.count`):
    - `[data-lane-kind="bank"]` → 3
    - `[data-lane-kind="grass"]` → 2
    - `[data-lane-kind="road"]` → 2
    - `[data-lane-kind="riverbed"]` → 2
    - `[data-testid="lane-1"][data-lane-kind="grass"]` → 1
      (enforces cadence ordering, not just counts)
    - `[data-testid="lane-3"][data-lane-kind="riverbed"]` → 1
    - `[data-testid="lane-3"] [data-obstacle-kind="log"]` → 3
    - `[data-testid="lane-6"] [data-obstacle-kind="log"]` → 3
14. Navigate to `?score=6&seed=hop-test-001` (pillar 2 banner check)
    → read text of `[data-testid="shared-score-banner"]` → expect
    the text to contain `"6"`.
15. `DELETE .../sessions/<sid>` — tear down.

## Stack & layout

- **Vite + TypeScript** (strict mode, no `any` types). React is fine
  but not required — any framework or vanilla TS is OK as long as
  the DOM contract is met.
- A `Makefile` with `make up` (serve on `:8005`) and `make test`
  (run any unit tests you write).
- A `docker-compose.yml` that runs the app on `:8005`.
- A `package.json` with build / serve scripts.
- A `README.md` documenting:
  - which species was picked (so the next build can pick a different
    one);
  - the deterministic-mode contract (`?deterministic=1`, `?die-after=N`)
    as a documented test surface;
  - that the ad slots are placeholders and real provider integration
    is a v2 follow-up.

Build it, get any tests you write passing — that is all.
