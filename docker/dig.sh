#!/usr/bin/env bash
# Run the dig as a DETACHED, restart-on-crash container (ijt-orch image). Fills
# corpus/queue.jsonl continuously; spawns gate sandbox siblings via the mounted socket.
# Host does nothing but `docker run -d`.
#   DIG_TARGET=40 DIG_WORKERS=2 docker/dig.sh   # start
#   docker logs -f ijt-dig                       # watch
#   docker rm -f ijt-dig                         # stop
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
set -a; [ -f .env ] && . ./.env; set +a
docker rm -f ijt-dig >/dev/null 2>&1 || true
docker run -d --name ijt-dig --restart unless-stopped \
  --network mvn-cache \
  -e QWEN_API_KEY -e QWEN_BASE_URL -e QWEN_MODEL -e OC_KEY -e IJT_HOME="$ROOT" -e IJT_DATA="$ROOT/current_iteration" -e PYTHONDONTWRITEBYTECODE=1 \
  -e DIG_WORKERS="${DIG_WORKERS:-3}" -e DIG_BATCH="${DIG_BATCH:-12}" \
  -v "$ROOT":"$ROOT" -w "$ROOT/src" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/log/observe/app/ijt:/var/log/observe/app/ijt \
  -v "$HOME/.config/gh":/root/.config/gh:ro \
  ijt-orch python -u dig.py
echo "ijt-dig started — docker logs -f ijt-dig"
