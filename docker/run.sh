#!/usr/bin/env bash
# Launch the ijt-orch container to run a pipeline module. Project tree mounted at its REAL host
# path so bind mounts inside sibling sandbox containers (spawned via the mounted docker socket)
# resolve against the host daemon. gh creds + frog's-eye log dir mounted in.
#   docker/run.sh python -u pit.py clones/JSON-java/target/pit-reports/mutations.xml
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
set -a; [ -f .env ] && . ./.env; set +a
docker rm -f ijt-orch >/dev/null 2>&1 || true
docker run --rm --name ijt-orch \
  --network mvn-cache \
  -e QWEN_API_KEY -e QWEN_BASE_URL -e QWEN_MODEL -e IJT_HOME="$ROOT" -e IJT_DATA="$ROOT/current_iteration" -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$ROOT":"$ROOT" -w "$ROOT/src" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/log/observe/app/ijt:/var/log/observe/app/ijt \
  -v "$HOME/.config/gh":/root/.config/gh:ro \
  ijt-orch "$@"
