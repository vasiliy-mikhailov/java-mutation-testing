#!/usr/bin/env bash
# Build a sandbox image per LTS JDK. JDK 25 may lack a maven:eclipse-temurin-25 base -> fallback
# to eclipse-temurin:25 + the maven tarball.
cd "$(dirname "$0")/.."
for J in 8 11 17 21 25; do
  echo "=== building java-$J-mutation-testing-sandbox ==="
  if docker build -f docker/Dockerfile.sandbox --build-arg JDK="$J" \
       -t "java-$J-mutation-testing-sandbox" . >/tmp/build-j$J.log 2>&1; then
    echo "java-$J OK"
  else
    echo "java-$J FAILED (base maybe missing) — see /tmp/build-j$J.log; tail:"; tail -3 /tmp/build-j$J.log
  fi
done
echo "=== built ==="; docker images | grep mutation-testing-sandbox | sort
