#!/usr/bin/env bash
# Run one SWE-bench instance in the sealed sandbox, then grade it.
#
# Containment: --network bench-isolated (no route off it) + allowlist proxy
# (api.fireworks.ai only), throwaway HOME, --pure, scrubbed env, and the test
# patch withheld until after the agent stops.
set -uo pipefail
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INST="${1:?usage: run_instance.sh <instance_id> [attempt]}"
ATT="${2:-1}"
IMG="${IMG:-bench-worker:astropy-13033}"
OUT="$SB/${RUNS_DIR:-runs}/$INST/a$ATT"
mkdir -p "$OUT/agent" "$OUT/host_only"
rm -rf "$OUT/home"; mkdir -p "$OUT/home/.config/opencode" "$OUT/home/.local/share"
cp "$SB/home/.config/opencode/opencode.json" "$OUT/home/.config/opencode/"
touch "$OUT/prompt.txt"
K=$(cat ~/.config/fireworks.key)

python3 - "$INST" "$OUT" <<'PY'
import json, sys
c = json.load(open("/tmp/claude-1000/-home-gavin-github-pagehub-io-pagehub-benchmarks/"
                   "21ed3ec5-fb39-43f3-ab7c-c971a3ac9d32/scratchpad/swebench/candidates_full.json"))
r = c[sys.argv[1]]
open(sys.argv[2] + "/problem.txt", "w").write(r["problem_statement"])
open(sys.argv[2] + "/repo.txt", "w").write(r["repo"])
open(sys.argv[2] + "/hints.txt", "w").write(r.get("hints_text") or "")
import os; os.makedirs(sys.argv[2]+"/host_only", exist_ok=True)
open(sys.argv[2] + "/host_only/test_patch.diff", "w").write(r["test_patch"])
json.dump({"f2p": json.loads(r["FAIL_TO_PASS"]), "p2p": json.loads(r["PASS_TO_PASS"])},
          open(sys.argv[2] + "/host_only/tests.json", "w"))
PY

REPO=$(cat "$OUT/repo.txt" 2>/dev/null || echo "the")
HINTBLOCK=""
if [ "${WITH_HINTS:-0}" = "1" ] && [ -s "$OUT/hints.txt" ]; then
  HINTBLOCK="

## Discussion from the issue thread

$(cat "$OUT/hints.txt")"
fi
PROMPT="You are fixing a bug in the $REPO repository at /testbed. Work only inside /testbed.

$(cat "$OUT/problem.txt")$HINTBLOCK

Fix the underlying issue in the source. Do not modify any existing tests; you may
write your own scratch tests to check your work. When you are done, stop."

printf '%s' "$PROMPT" > "$OUT/prompt.txt"
echo "=== $INST attempt $ATT ==="
START=$(date +%s)
timeout 2700 docker run --rm --name "bw-$INST-$ATT" --network bench-isolated \
  -e HTTPS_PROXY=http://bench-proxy:8888 -e HTTP_PROXY=http://bench-proxy:8888 \
  -e FIREWORKS_API_KEY="$K" -e HOME=/sbhome \
  -e XDG_CONFIG_HOME=/sbhome/.config -e XDG_DATA_HOME=/sbhome/.local/share \
  -v "$OUT/home:/sbhome" -v "$OUT/prompt.txt:/prompt.txt:ro" -v "$OUT/agent:/agent" \
  "$IMG" bash -lc '
    cd /testbed
    opencode run --pure --auto --dir /testbed --format json \
      -m fireworks-ai/accounts/fireworks/models/deepseek-v4p1-flash \
      "$(cat /prompt.txt)" > /agent/events.jsonl 2>/agent/stderr.log
    rc=$?
    git -C /testbed diff > /agent/model_patch.diff 2>/dev/null
    git -C /testbed status --short > /agent/status.txt 2>/dev/null
    echo $rc > /agent/exit_code
  ' 2>&1 | tail -3
echo "$(( $(date +%s) - START ))" > "$OUT/wall_seconds"
echo "  wall=$(cat "$OUT/wall_seconds")s rc=$(cat "$OUT/agent/exit_code" 2>/dev/null) patch=$(wc -l < "$OUT/agent/model_patch.diff" 2>/dev/null) lines"
