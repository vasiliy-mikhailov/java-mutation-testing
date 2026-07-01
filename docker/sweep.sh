#!/usr/bin/env bash
# Run the 3-agent scoring sweep as a DETACHED, resume-on-crash container.
# Scores corpus/eval_set.json[:N] across openhands/opencode/kilocode (N*3 agent runs,
# sequential to bound load). Resumable: a (agent,target) whose result file exists is skipped,
# so a restart picks up where it left off. Writes corpus/sweep/summary.json at the end.
#   N=20 docker/sweep.sh            # start (default N=20)
#   docker logs -f ijt-sweep        # watch
#   docker rm -f ijt-sweep          # stop
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
set -a; [ -f .env ] && . ./.env; set +a
N="${N:-20}"
docker rm -f ijt-sweep >/dev/null 2>&1 || true
docker run -d --name ijt-sweep --restart unless-stopped \
  --network mvn-cache \
  -e QWEN_API_KEY -e QWEN_BASE_URL -e QWEN_MODEL -e OC_KEY -e IJT_HOME="$ROOT" -e IJT_DATA="$ROOT/current_iteration" -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$ROOT":"$ROOT" -w "$ROOT/src" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/log/observe/app/ijt:/var/log/observe/app/ijt \
  -v "$HOME/.config/gh":/root/.config/gh:ro \
  ijt-orch python -u sweep.py "$N"
echo "ijt-sweep started (N=$N) — docker logs -f ijt-sweep"
