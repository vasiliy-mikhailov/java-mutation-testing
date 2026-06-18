#!/usr/bin/env bash
# Run the 3-agent scoring sweep as a DETACHED, resume-on-crash container.
# Scores corpus/eval_set.json[:N] across openhands/opencode/kilocode (N*3 agent runs,
# sequential to bound load). Resumable: a (agent,target) whose result file exists is skipped,
# so a restart picks up where it left off. Writes corpus/sweep/summary.json at the end.
#   N=20 docker/sweep.sh            # start (default N=20)
#   docker logs -f jmt-sweep        # watch
#   docker rm -f jmt-sweep          # stop
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
set -a; [ -f .env ] && . ./.env; set +a
N="${N:-20}"
docker rm -f jmt-sweep >/dev/null 2>&1 || true
docker run -d --name jmt-sweep --restart unless-stopped \
  --network mvn-cache \
  -e QWEN_API_KEY -e QWEN_BASE_URL -e QWEN_MODEL -e OC_KEY -e JMT_HOME="$ROOT" -e JMT_DATA="$ROOT/current_iteration" -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$ROOT":"$ROOT" -w "$ROOT/src" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/log/observe/app/jmt:/var/log/observe/app/jmt \
  -v "$HOME/.config/gh":/root/.config/gh:ro \
  jmt-orch python -u sweep.py "$N"
echo "jmt-sweep started (N=$N) — docker logs -f jmt-sweep"
