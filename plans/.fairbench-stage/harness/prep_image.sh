#!/usr/bin/env bash
# Pull an instance's official SWE-bench image and layer node+opencode on it.
set -uo pipefail
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INST="${1:?usage: prep_image.sh <instance_id>}"
SLUG=$(echo "$INST" | sed 's#__#_1776_#')
BASE="swebench/sweb.eval.x86_64.${SLUG}:latest"
TAG="bench-worker:${INST}"

if docker image inspect "$TAG" >/dev/null 2>&1; then echo "  $INST: worker image already present"; exit 0; fi
echo "  $INST: pulling $BASE"
timeout 2400 docker pull -q "$BASE" >/dev/null 2>&1 || { echo "  $INST: PULL FAILED"; exit 1; }
cat > "$SB/Dockerfile.$INST" <<D
FROM $BASE
USER root
RUN (apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null 2>&1) || true
RUN curl -fsSL https://deb.nodesource.com/setup_20.x -o /tmp/n.sh && bash /tmp/n.sh >/dev/null 2>&1 \
 && apt-get install -y -qq nodejs >/dev/null 2>&1 && rm -f /tmp/n.sh
RUN npm install -g --silent opencode-ai@1.18.34 >/dev/null 2>&1
D
timeout 2400 docker build -q -f "$SB/Dockerfile.$INST" -t "$TAG" "$SB" >/dev/null 2>&1 \
  && echo "  $INST: worker image built" || { echo "  $INST: BUILD FAILED"; exit 1; }
