Build a chess API as a Python FastAPI service backed by the `python-chess`
library. It runs on **port 8003**. State may be kept in memory (a dict of
games is fine — no database needed).

## Stack & layout

- FastAPI app under `api/` (e.g. `api/main.py`), standard-stack shape.
- Every route uses a Pydantic `response_model`; every request body is a
  Pydantic model — no raw dicts. Group schemas in a `schemas.py`.
- `requirements.txt` including `fastapi`, `uvicorn`, and `python-chess`.
- A `Makefile` with `make up` (run the service on :8003) and `make test`
  (run the unit tests).
- A `docker-compose.yml` that runs the service on :8003.
- Unit tests (pytest) covering the endpoints below — legal/illegal moves,
  castling, en passant, promotion, pins, checkmate, stalemate, draws, the
  404/422 cases.

## Endpoints

### `POST /games`
Body: `{ "starting_fen": <string, optional> }`. Creates a game (from the
standard start position if `starting_fen` is omitted, otherwise from that
FEN). A syntactically/semantically invalid FEN → **422**.

Response: `{ "game_id": <string>, "fen": <string>, "turn": "white"|"black",
"status": "in_progress"|"white_won"|"black_won"|"draw" }`.

### `POST /games/{game_id}/moves`
Body: `{ "move": <uci string> }`, e.g. `"e2e4"`, `"e7e8q"`.

- Unknown `game_id` → **404**.
- A `move` that is not parseable as UCI (garbage like `"hello"`, or
  malformed coordinates) → **422**.
- A move that *parses* but is **illegal in the current position** → **200**
  with `legal: false` and the board **unchanged** (`fen` is the pre-move FEN,
  it is **not** appended to `move_history`).
- A legal move is applied: `legal: true`, `fen` updated, the move appended to
  `move_history`. If the move ends the game, `status` reflects the terminal
  outcome (`white_won` / `black_won` / `draw`).

Response: `{ "game_id", "fen", "turn", "status", "legal": <bool>,
"move_history": [<uci>, ...] }`.

### `GET /games/{game_id}`
Unknown `game_id` → **404**. Response: `{ "game_id", "fen", "turn", "status",
"move_history": [<uci>, ...], "legal_moves": [<uci>, ...] }` where
`legal_moves` is the list of legal UCI moves in the current position,
**sorted** (plain ascending string sort).

### `POST /legal-moves`
Body: `{ "fen": <string> }`. A bad FEN → **422**. Response:
`{ "fen", "legal_moves": [<uci>, ...], "turn": "white"|"black",
"is_game_over": <bool> }` — `legal_moves` **sorted** as above. This endpoint
is stateless (it does not create a game).

### `GET /health`
`{ "status": "ok", "commit": <string> }` — `commit` is the current git short
SHA if available, otherwise any non-empty string (e.g. `"dev"`).

### `GET /metrics`
A metrics endpoint (Prometheus text format is fine — even a minimal one).

## Notes on semantics (use `python-chess` for all of this)

- `turn` is `"white"` when it is White to move, `"black"` otherwise — derive
  it from the board, don't track it separately.
- Draw detection must use `python-chess`'s **claim-aware** game-over check so
  claimable draws count: a game is over and drawn when
  `board.is_game_over(claim_draw=True)` is true and
  `board.outcome(claim_draw=True).winner` is `None`. That covers stalemate,
  insufficient material, **threefold repetition**, and the **50-move rule**
  (as well as the forced fivefold / seventy-five-move conditions). So:
  `status` is `"white_won"` / `"black_won"` for a checkmate, `"draw"` for any
  such drawn outcome, otherwise `"in_progress"`.
- Promotion moves carry the promotion piece in the UCI string (`e7e8q`,
  `e7e8n`, ...). Underpromotion must work.
- For `POST /games/{game_id}/moves`, "illegal in the current position" means
  the parsed move is not in `board.legal_moves` (this covers moving a pinned
  piece, leaving your own king in check, castling through check, etc.) —
  return `200` with `legal: false`, do not mutate the board.

Build it, get the tests passing — that is all.
