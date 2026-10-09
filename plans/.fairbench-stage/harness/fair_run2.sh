#!/usr/bin/env bash
# astropy__astropy-13033 under the REWRITTEN (fair) statement and verifier.
#
# Statement: statement_missing.md — two message forms, variable table, the four
#   clarifications the blind authors had to guess.
# Verifier:  verifier_blind2.py — written by an author who saw only the statement
#   and the pre-fix repo, never the merged fix or any other verifier. 40 cases.
# Regression: every existing test in astropy/timeseries/ except the three the
#   statement names as superseded, and the 8 that already fail at base.
#
# Harness validation: a reference implementation must pass. "Gold passes" is not
# available — the merged fix does not implement this requirement (it scores 1/40).
set -uo pipefail
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SP="$(dirname "$SB")"
INST=astropy__astropy-13033
FAIR="$SP/fair/$INST"
BLIND="$SP/blind"
IMG=bench-worker:$INST
EVAL=swebench/sweb.eval.x86_64.astropy_1776_astropy-13033:latest
N="${N:-10}"
RUNS="$SB/runs-fair2/$INST"
K=$(cat ~/.config/fireworks.key)
DES="--deselect astropy/timeseries/tests/test_sampled.py::test_required_columns \
 --deselect astropy/timeseries/tests/test_sampled.py::test_empty_initialization_invalid \
 --deselect astropy/timeseries/tests/test_binned.py::test_empty_initialization_invalid \
 --ignore=astropy/timeseries/periodograms/bls"

grade() {  # $1 = dir holding agent/model_patch.diff ; $2 = "ref" to use the reference impl
  docker run --rm --network none -v "$FAIR:/f:ro" -v "$BLIND:/b:ro" -v "$1/agent:/a:ro" "$EVAL" bash -lc "
    cd /testbed
    if [ '${2:-}' = ref ]; then python /f/reference_impl.py >/dev/null
    elif [ -s /a/model_patch.diff ]; then git apply /a/model_patch.diff >/dev/null 2>&1 || { echo 'V APPLY_FAIL'; echo 'R APPLY_FAIL'; exit 0; }; fi
    source /opt/miniconda3/bin/activate 2>/dev/null; conda activate testbed 2>/dev/null
    W='ignore::astropy.utils.exceptions.AstropyWarning'
    R=\$(python -m pytest -q --no-header astropy/timeseries/ $DES -W \"\$W\" 2>&1 | tail -1)
    cp /b/verifier_blind2.py astropy/timeseries/tests/test_v.py
    V=\$(python -m pytest -q --no-header astropy/timeseries/tests/test_v.py -W \"\$W\" 2>&1 | tail -1)
    echo \"V \$V\"; echo \"R \$R\"
  " 2>/dev/null
}

mkdir -p "$RUNS/a0/agent"; : > "$RUNS/a0/agent/model_patch.diff"
echo "=== harness validation: reference implementation ==="
GV=$(grade "$RUNS/a0" ref); echo "$GV" | sed 's/^/  /'
echo "$GV" | grep '^V ' | grep -q 'failed\|error' && { echo "  HARNESS INVALID"; exit 2; }
echo "$GV" | grep '^R ' | grep -q 'failed\|error' && { echo "  HARNESS INVALID (regression)"; exit 2; }
echo "  harness validated"

for ((a=1;a<=N;a++)); do
  O="$RUNS/a$a"
  [ -s "$O/verdict" ] && { echo "  a$a done ($(cat "$O/verdict"))"; continue; }
  rm -rf "$O"; mkdir -p "$O/agent" "$O/home/.config/opencode" "$O/home/.local/share"
  cp "$SB/home/.config/opencode/opencode.json" "$O/home/.config/opencode/"
  { echo "You are fixing a bug in the astropy/astropy repository at /testbed. Work only inside /testbed."
    echo; cat "$FAIR/statement_missing.md"; echo
    echo "Fix the underlying issue in the source. You may write your own scratch tests to check"
    echo "your work. When you are done, stop."
  } > "$O/prompt.txt"

  START=$(date +%s)
  timeout 2700 docker run --rm --network bench-isolated \
    -e HTTPS_PROXY=http://bench-proxy:8888 -e HTTP_PROXY=http://bench-proxy:8888 \
    -e FIREWORKS_API_KEY="$K" -e HOME=/sbhome \
    -e XDG_CONFIG_HOME=/sbhome/.config -e XDG_DATA_HOME=/sbhome/.local/share \
    -v "$O/home:/sbhome" -v "$O/prompt.txt:/prompt.txt:ro" -v "$O/agent:/agent" \
    "$IMG" bash -lc '
      cd /testbed
      opencode run --pure --auto --dir /testbed --format json \
        -m fireworks-ai/accounts/fireworks/models/deepseek-v4p1-flash \
        "$(cat /prompt.txt)" > /agent/events.jsonl 2>/agent/stderr.log
      git -C /testbed diff > /agent/model_patch.diff 2>/dev/null
    ' >/dev/null 2>&1
  echo "$(( $(date +%s) - START ))" > "$O/wall_seconds"

  RES=$(grade "$O")
  V=$(echo "$RES" | grep '^V '); R=$(echo "$RES" | grep '^R ')
  if ! echo "$V" | grep -q 'failed\|error\|APPLY_FAIL' && ! echo "$R" | grep -q 'failed\|error\|APPLY_FAIL'; then
    echo RESOLVED > "$O/verdict"; else echo NOT_RESOLVED > "$O/verdict"; fi
  echo "  a$a  $(cat "$O/wall_seconds")s  $(cat "$O/verdict")  ${V#V } | ${R#R }"
done
echo "FAIR RUN 2 COMPLETE"
