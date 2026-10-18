#!/usr/bin/env bash
# Grade one attempt.
#
# Two guards, both learned the hard way today:
#   1. Validate the harness against the GOLD patch first. If gold does not pass
#      both sets, the harness is broken and no model verdict from it means anything.
#   2. Require that every requested test actually ran. "no tests ran" is not a pass.
set -uo pipefail
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INST="${1:?usage: grade.sh <instance_id> <attempt>}"
ATT="${2:?}"
IMG="${EVAL_IMG:-swebench/sweb.eval.x86_64.$(echo "$INST" | sed 's#__#_1776_#')}:latest"
O="$SB/${RUNS_DIR:-runs}/$INST/a$ATT"
G="$O/host_only"
mkdir -p "$G"

python3 - "$INST" "$G" <<'PY'
import json, sys
c = json.load(open("/tmp/claude-1000/-home-gavin-github-pagehub-io-pagehub-benchmarks/"
                   "21ed3ec5-fb39-43f3-ab7c-c971a3ac9d32/scratchpad/swebench/candidates_full.json"))
r = c[sys.argv[1]]
open(sys.argv[2] + "/gold.diff", "w").write(r["patch"])
open(sys.argv[2] + "/test_patch.diff", "w").write(r["test_patch"])
def clean(ids, label):
    # SWE-bench ships some ids truncated at a space inside the parametrize
    # bracket (e.g. "test_tofile[home_is_data,"). They match no test, and one
    # unmatched id aborts pytest collection for the whole set. Drop and report.
    good = [i for i in ids if i.count("[") == i.count("]")]
    bad = [i for i in ids if i not in good]
    if bad:
        print(f"  NOTE: dropped {len(bad)} malformed {label} id(s) from the dataset:")
        for b in bad: print(f"        {b}")
    return good
json.dump(clean(json.loads(r["FAIL_TO_PASS"]), "FAIL_TO_PASS"), open(sys.argv[2] + "/f2p.json", "w"))
json.dump(clean(json.loads(r["PASS_TO_PASS"]), "PASS_TO_PASS"), open(sys.argv[2] + "/p2p.json", "w"))
PY

run_tests() {  # $1 = patch path inside container, or "none"
  docker run --rm --network none -v "$G:/g:ro" -v "$O/agent:/a:ro" \
    -v "$SB/runtests.py:/runtests.py:ro" "$IMG" bash -lc "
      cd /testbed
      if [ '$1' != none ]; then git apply '$1' >/dev/null 2>&1 || { echo '{\"apply\":false}'; exit 0; }; fi
      git apply /g/test_patch.diff >/dev/null 2>&1 || { echo '{\"testpatch\":false}'; exit 0; }
      source /opt/miniconda3/bin/activate 2>/dev/null; conda activate testbed 2>/dev/null
      echo -n 'F2P '; python /runtests.py /g/f2p.json
      echo -n 'P2P '; python /runtests.py /g/p2p.json
    " 2>/dev/null
}

verdict() {  # reads 'F2P {...}\nP2P {...}'
  python3 -c "
import json,sys
txt=sys.stdin.read()
r={}
for line in txt.splitlines():
    if line.startswith(('F2P ','P2P ')):
        try: r[line[:3]]=json.loads(line[4:])
        except Exception: pass
f,p=r.get('F2P'),r.get('P2P')
if not f or not p: print('INVALID|could not parse results'); raise SystemExit
for k,v in (('F2P',f),('P2P',p)):
    if not v.get('collected_all'):
        print(f'INVALID|{k} collected {v[\"ran\"]}/{v[\"requested\"]} — not all tests ran'); raise SystemExit
ok = f['ok'] and p['ok']
print(('RESOLVED' if ok else 'NOT_RESOLVED') +
      f\"|F2P {f['passed']}/{f['requested']} pass, P2P {p['passed']}/{p['requested']} pass\")
"
}

GOLDCACHE="$SB/${RUNS_DIR:-runs}/$INST/.gold_verdict"
if [ -s "$GOLDCACHE" ] && [ "${FORCE_GOLD:-0}" != "1" ]; then
  GV=$(cat "$GOLDCACHE")
else
  GV=$(run_tests /g/gold.diff | verdict); echo "$GV" > "$GOLDCACHE"
fi
if [ "${GV%%|*}" != "RESOLVED" ]; then
  echo "  HARNESS INVALID — gold did not pass: ${GV#*|}"; echo INVALID > "$O/verdict"; exit 2
fi
echo "  harness validated — gold: ${GV#*|}"

MV=$(run_tests /a/model_patch.diff | verdict)
echo "  model: ${MV#*|}"
echo "  RESULT: ${MV%%|*}"
echo "${MV%%|*}" > "$O/verdict"
