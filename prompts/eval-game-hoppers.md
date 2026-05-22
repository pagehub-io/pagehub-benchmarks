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

### Gameplay shape (theme-agnostic)

- `[data-testid="player"]` exists with `data-hopper-species="<one of
  the 5 species above>"` and `data-current-lane="<int>"`.
- `[data-testid="lane-<index>"]` for each visible lane (numbered
  relative to the player; lane 0 = the lane the player currently
  occupies).
- `[data-testid="obstacle-<lane>-<n>"]` for each visible obstacle in
  each lane.
- `[data-current-lane]` on the player matches the lane the player
  currently occupies (zero-indexed; starts at `0`; increments by 1
  for each successful hop).
- `[data-testid="score"]` is an element whose **text content** is the
  current score (a non-negative integer, no commas / formatting).
- `[data-score]` attribute somewhere on the document MAY mirror the
  same value; not asserted by the fixture but a useful convention.

## Deterministic playthrough mode

A real-time arcade game is **flaky to grade** — requestAnimationFrame
jitter, network races, etc. The grader sidesteps this with a
deterministic mode:

- URL params `?deterministic=1` puts the game in **JS-loop-driven**
  mode (no rAF; the game tick advances **only on tap**, not on a
  timer). Every tap is one tick.
- Combined with `?lane-speed=0&lane-density=0`: lanes do NOT scroll,
  NO obstacles spawn, each tap advances exactly one lane. Score
  equals tap count.
- URL param `?die-after=N` (deterministic-mode-only) forces a
  game-over **right after the Nth successful tap**. This is the
  test affordance the grader uses to assert pillar 1 (instant
  restart) and pillar 2 (shareable score) and pillar 5 (interstitial
  ad) without needing the simulator to predict a collision.

The grader's deterministic playthrough loads
`?seed=hop-test-001&lane-speed=0&lane-density=0&deterministic=1&die-after=6`,
clicks `[data-testid="game-root"]` six times, and asserts
`data-current-lane` reads `"1"`, `"2"`, ..., `"6"` after each tap,
then `data-game-state="game-over"` and `[data-testid="share-url"]`'s
`data-url` contains `score=6` and `seed=hop-test-001`.

`?deterministic=1` and `?die-after=N` MUST be honored exactly; **all
other modes (normal real-time play, the creator panel, sharing) must
still work without them.** The deterministic mode is a test surface,
not the default behavior.

## What the grader will do (so you can mentally simulate it)

The grader (pagehub-evals, driven by pagehub-browser) will:

{% raw %}
1. `POST {{pagehub-browser_url}}/v1/sessions` — open a headless
   browser session.
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
9. Tap-loop ×6: `POST .../click` on `[data-testid="game-root"]`,
   then read `data-current-lane` → expect `"1"`, `"2"`, ..., `"6"`.
   After the 6th tap, `die-after=6` fires: read `data-game-state` on
   `[data-testid="game-root"]` → expect `"game-over"`.
10. Read `data-url` off `[data-testid="share-url"]` → expect the
    response body to contain `score=6` and `seed=hop-test-001`
    substrings (pillar 2).
11. `POST .../find` with `[data-testid="ad-slot-interstitial"]` →
    expect `count == 1`. Click `[data-testid="ad-close"]` (pillar 5).
12. Click `[data-testid="game-root"]` → restart. Assert
    `data-game-state` flips back to `"playing"`, `data-current-lane`
    resets to `"0"`, score text resets to `"0"` (pillar 1).
13. Navigate to `?score=6&seed=hop-test-001` (pillar 2 banner check)
    → read text of `[data-testid="shared-score-banner"]` → expect
    the text to contain `"6"`.
14. `DELETE .../sessions/<sid>` — tear down.

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
