Build a chess web app as a **pure-frontend** TypeScript application backed
by the `chess.js` library for rule enforcement. It runs on **port 8004**
(port 8003 is reserved for eval-chess-backend; pick 8004). There is **no
backend** — all state lives in the browser, no database, no API server.

## Stack & layout

- **Vite + TypeScript** (strict mode, no `any` types). React is fine but
  not required — any framework or vanilla TS is OK as long as the DOM
  contract below is met.
- `chess.js` for legal-move generation and rule enforcement (don't roll
  your own chess engine — `chess.js` already encodes every rule we grade).
- A `Makefile` with `make up` (serve the app on `:8004`) and `make test`
  (run any unit tests you write).
- A `docker-compose.yml` that runs the app on `:8004`.
- A `package.json` with the build/serve scripts.

## The DOM contract (load-bearing — the grader binds to it exactly)

The grader is **pagehub-browser-driven**: it loads the page, types into
inputs by `data-testid`, clicks buttons by `data-testid`, and reads
attributes off the board element. The contract:

```
<div data-testid="board"
     data-fen="<current FEN>"
     data-turn="white|black"
     data-status="in_progress|white_won|black_won|draw"
     data-move-count="<int, count of legal moves accepted so far>"
     data-last-move="<uci of the most recent legal move, or empty string>"
     data-move-history="<space-separated UCIs, or empty string>"
>
  ...rendered board (squares, pieces — any visual style is fine)...
</div>

<input  data-testid="fen-input" />
<button data-testid="apply-fen">Set Position</button>
<input  data-testid="uci-input" />
<button data-testid="submit-move">Submit</button>
<button data-testid="reset-startpos">Reset</button>
```

**Behavior of the action buttons:**

- `apply-fen` (click) — read the value out of `fen-input`, validate it
  with `chess.js`, and if valid load it as the current position. If
  invalid, leave `data-fen` (and all sibling attributes) unchanged.
- `submit-move` (click) — read the value out of `uci-input`, parse it as
  a UCI move, and attempt to play it. Three outcomes, in order:
  1. **legal in the current position** — apply the move; update
     `data-fen` to `chess.js`'s post-move FEN, advance `data-turn`,
     re-derive `data-status` (see below), append the UCI to
     `data-move-history`, increment `data-move-count`, set
     `data-last-move` to the move's UCI.
  2. **parses as UCI but is illegal in the current position** (pinned
     piece, leaves own king in check, castling through check, no piece
     on the from-square, wrong-color piece, etc.) — **do nothing**:
     `data-fen` stays exactly as it was, ditto every sibling attribute.
     No exception thrown, no banner, just no state change.
  3. **doesn't parse as UCI** (garbage input) — same as (2): do nothing.
- `reset-startpos` (click) — reset to the standard starting position
  (`rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1`),
  clear `data-move-history` (empty string), `data-last-move` (empty
  string), `data-move-count` (`"0"`), `data-status` (`"in_progress"`),
  `data-turn` (`"white"`).

## Notes on semantics (use `chess.js` for all of this)

- **`data-fen`** must be the FEN string `chess.js` itself emits
  (`game.fen()`) — including castling rights, en passant square,
  halfmove clock, and fullmove number. The grader compares it to a
  python-chess-derived FEN; both libraries emit the same canonical form,
  so as long as you don't post-process the FEN string, they match.
- **`data-turn`** is `"white"` when it is White to move, `"black"`
  otherwise. Derive it from the board, don't track it separately.
- **`data-status`**:
  - `"in_progress"` while the game is ongoing
  - `"white_won"` / `"black_won"` on checkmate (winner is the color
    that just moved)
  - `"draw"` on stalemate, the 50-move rule, threefold repetition,
    insufficient material, or any other claim-aware draw
    (`chess.js`'s `isDraw()` covers this — call it after every move)
  - On a terminal position the status MUST be terminal **immediately
    after the move that triggered it**, not on the next move.
- **Promotion** moves carry the promotion piece **in the UCI string** —
  `"e7e8q"`, `"e7e8n"`, `"e7e8r"`, `"e7e8b"`. No promotion picker popup;
  parse the 5th character as the promotion piece. Underpromotion must
  work too. (`chess.js`'s `move({from, to, promotion})` handles this if
  you split the UCI; the grader will type `"e7e8q"` etc. into
  `uci-input` and expect `submit-move` to play it directly.)
- **An illegal move never mutates state.** Test your code by loading a
  pinned-piece FEN, submitting the illegal move, and reading `data-fen`
  back — it must equal the pre-submit FEN. This is the single most
  common bug in chess UIs; the grader has multiple cases hitting it.
- **`apply-fen` on a bad FEN never mutates state either.** Same
  invariant — if `chess.js`'s FEN validator rejects it, leave the
  board where it was.

## What the grader will do (so you can mentally simulate it)

The grader (pagehub-evals, driven by pagehub-browser) will:

{% raw %}
1. `POST {{pagehub-browser_url}}/v1/sessions` — open a headless browser
   session.
2. `POST .../navigate` body `{url: "{{eval-chess-frontend_url}}"}` —
   load your app.
{% endraw %}
3. For each rule: `POST .../type {locator: {strategy: "testid", value:
   "fen-input"}, text: "<FEN>"}` → `POST .../click {locator: {strategy:
   "testid", value: "apply-fen"}}` → `POST .../type {locator: {...,
   value: "uci-input"}, text: "<uci>"}` → `POST .../click {locator: {...,
   value: "submit-move"}}` → `POST .../get-attribute {locator: {...,
   value: "board"}, attribute: "data-fen" | "data-status"}` and assert
   `$.text` of the response equals the expected value.
4. `DELETE .../sessions/<sid>` — tear down.

The grader does NOT use the per-square click affordances (no
`data-testid="square-e2"` etc. is required by the eval); you can include
them for ergonomics but the grader exclusively uses `uci-input` +
`submit-move`. This keeps request counts down and removes flakiness.

## Rules the grader exercises

(Each is its own sequence — your job is just to make each one pass.)

- Legal move accepted (`data-fen` changes to the post-move FEN).
- Illegal-given-position rejected (`data-fen` UNCHANGED).
- Kingside castling.
- Queenside castling.
- Castling rights respected (no K-right in the FEN → `e1g1` rejected).
- En passant capture.
- Promotion to queen.
- Pinned piece cannot move (a knight pinned to its king by an enemy
  rook on the same file, attempting to move sideways → rejected,
  `data-fen` unchanged).
- Checkmate → `data-status` terminal (`"white_won"` for a white-mates
  case, `"black_won"` for black).
- Stalemate → `data-status == "draw"`.
- 50-move rule (load a FEN with halfmove clock at 99, play a non-pawn,
  non-capture move → halfmove ticks to 100 → `data-status == "draw"`).

Use `chess.js` for everything — it already implements claim-aware draw
detection.

Build it, get any tests you write passing — that is all.
