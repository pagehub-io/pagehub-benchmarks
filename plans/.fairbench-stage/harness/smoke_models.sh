#!/usr/bin/env bash
# Does the model actually start working on the REAL prompt? Short run, we only
# need to see step_finish events (it ran) vs an error event (it did not).
set -uo pipefail
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SP="$(dirname "$SB")"
K=$(cat ~/.config/fireworks.key)
PROMPT="$SB/smoke_prompt.txt"
{ echo "You are fixing a bug in the astropy/astropy repository at /testbed. Work only inside /testbed."
  echo; cat "$SP/fair/astropy__astropy-13033/statement_missing.md"; echo
  echo "Fix the underlying issue in the source. When you are done, stop."
} > "$PROMPT"

for M in nemotron-lightning-3p5-30b-a3b glm-5p3-flash gpt-oss-120b deepseek-v4p1-flash; do
  OUT=$(timeout 100 docker run --rm --network bench-isolated \
    -e HTTPS_PROXY=http://bench-proxy:8888 -e FIREWORKS_API_KEY="$K" -e HOME=/sb \
    -e XDG_CONFIG_HOME=/sb/.config -e XDG_DATA_HOME=/sb/.local/share \
    -v "$PROMPT:/p.txt:ro" bench-worker:astropy__astropy-13033 bash -lc "
      mkdir -p /sb/.config/opencode /sb/.local/share
      opencode run --pure --auto --dir /testbed --format json \
        -m fireworks-ai/accounts/fireworks/models/$M \"\$(cat /p.txt)\" 2>/dev/null" 2>/dev/null \
    | python3 -c "
import sys, json
steps=0; err=''
for l in sys.stdin:
    try: e=json.loads(l)
    except: continue
    t=e.get('type')
    if t=='step_finish': steps+=1
    if t=='error' and not err:
        err=((e.get('error',{}) or {}).get('data',{}) or {}).get('message','')[:90]
print(f'{steps}|{err}')")
  STEPS=${OUT%%|*}; ERR=${OUT#*|}
  if [ "${STEPS:-0}" -gt 0 ] 2>/dev/null; then printf "  %-34s USABLE   (%s steps in 100s)\n" "$M" "$STEPS"
  else printf "  %-34s UNUSABLE (%s)\n" "$M" "${ERR:-no steps, no error}"; fi
done
echo SMOKE DONE
