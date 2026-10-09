#!/usr/bin/env bash
# Run the fair astropy-13033 against a price ladder of models, 10 attempts each.
set -uo pipefail
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
run_model() {
  export MODEL_TAG="$1" OC_MODEL="fireworks-ai/accounts/fireworks/models/$2"
  echo "################ $1  ($2) ################"
  N=10 bash "$SB/fair_multi.sh"
}
run_model nemotron-lightning nemotron-lightning-3p5-30b-a3b
run_model glm-5p3-flash      glm-5p3-flash
run_model gpt-oss-120b       gpt-oss-120b
echo "################ LADDER COMPLETE ################"
