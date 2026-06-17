#!/usr/bin/env bash
# Clone a GitHub repo HEAD (depth 1) into clones/<dest> via a throwaway jmt-orch container, so
# even ad-hoc workspace provisioning is docker-bounded — the host is only a docker host (no
# reliance on host gh/git/python). Reaps any existing (possibly root-owned) tree via a root
# container first.
#   docker/provision.sh stleary/JSON-java [clones/JSON-java]
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
REPO="$1"
DEST="${2:-clones/$(echo "$REPO" | tr '/' '_')}"
ABS="$ROOT/$DEST"
docker run --rm -v "$ROOT/clones":"$ROOT/clones" python:3-slim rm -rf "$ABS" 2>/dev/null || true
docker run --rm \
  -v "$ROOT":"$ROOT" -w "$ROOT" \
  -v "$HOME/.config/gh":/root/.config/gh:ro \
  jmt-orch gh repo clone "$REPO" "$ABS" -- --depth 1
echo "provisioned $DEST"
