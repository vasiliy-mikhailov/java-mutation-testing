#!/bin/bash
# upstream_pr.sh UP CLS -- offer one JMT characterization test upstream as a PR.
# Reads the generated test from the LOCAL store (IJT_GENERATED/<name>/, written by pr.py — no
# more ijt-* GitHub mirrors), applies it to a fork of UP, build-verifies in
# java-<jdk>-improve-java-tests-sandbox (deepest-pom module detect, clean-append + real Tests-run
# gates, buildnumber/gpg substrate-skips); opens PR under owner name (always -s) only on green.
# P9 value experiment (see memory upstream-pr-campaign).
UP=$1; CLS=$2
SX=/home/vmihaylov/improve-java-tests/current_attempt/docker/sandbox-settings.xml
GEN="${IJT_GENERATED:-/home/vmihaylov/improve-java-tests/current_attempt/current_iteration/ijt-generated}/$(basename "$UP")"
slug=$(echo "$UP" | tr '/' '-'); D=/tmp/pr-$slug
SKIP="-Dmaven.buildNumber.skip=true -Dgpg.skip=true -Dspotless.check.skip=true -Dspotless.apply.skip=true -Dcheckstyle.skip=true -Denforcer.skip=true -Dpmd.skip=true -Dspotbugs.skip=true -Djacoco.skip=true -Dlicense.skip=true"
echo "================ $UP ($CLS) ================"
timeout 90 gh repo fork "$UP" --clone=false >/dev/null 2>&1; sleep 3
rm -rf "$D"
timeout 150 gh repo clone "vasiliy-mikhailov/$(basename "$UP")" "$D" -- --depth 1 -q 2>/dev/null || { echo "RESULT $UP SKIP clone-fail"; exit 0; }
# locate the generated test for CLS in the local store (repo-relative path preserved on export)
tf=$(cd "$GEN" 2>/dev/null && { find . -path "*${CLS}Test.java" -o -path "*${CLS}*Test.java"; } 2>/dev/null | head -1 | sed 's|^\./||')
[ -z "$tf" ] && { echo "RESULT $UP SKIP no-local-test (looked in $GEN)"; exit 0; }
cp "$GEN/$tf" /tmp/mt.java
[ ! -s /tmp/mt.java ] && { echo "RESULT $UP SKIP empty-test"; exit 0; }
if [ -f "$D/$tf" ]; then removed=$(diff "$D/$tf" /tmp/mt.java 2>/dev/null | grep -cE '^<'); else removed=0; fi
nt=$(grep -c '@Test' /tmp/mt.java); cp /tmp/mt.java "$D/$tf"
mod="."; d=$(dirname "$tf")
while [ "$d" != "." ] && [ "$d" != "/" ]; do [ -f "$D/$d/pom.xml" ] && { mod="$d"; break; }; d=$(dirname "$d"); done
jdk=$(grep -hoE '<(maven.compiler.release|maven.compiler.target|java.version|release)>[0-9.]+' "$D/$mod/pom.xml" "$D/pom.xml" 2>/dev/null | grep -oE '[0-9]+' | grep -vE '^1$' | sort -rn | head -1)
case "$jdk" in 8|11|17|21|25);; *) jdk=17;; esac
echo "module=$mod jdk=$jdk tests=$nt upstream-removed=$removed test=$tf"
[ ! -f "$D/pom.xml" ] && [ "$mod" = "." ] && { echo "RESULT $UP SKIP not-maven"; exit 0; }
[ "$removed" -gt 0 ] && { echo "RESULT $UP SKIP not-clean-append($removed)"; exit 0; }
_build() { timeout 1500 docker run --rm --network mvn-cache -v "$D:$D" -v "$SX:/sx.xml:ro" -w "$D" "java-$1-improve-java-tests-sandbox" bash -lc "git config --global --add safe.directory '*' && mvn -B -ntp -s /sx.xml $SKIP -pl $mod -am -DskipTests install -q 2>&1 | tail -2 && echo ---TP--- && mvn -B -ntp -s /sx.xml $SKIP -pl $mod test -Dtest=${CLS}Test 2>&1 | grep -E 'Tests run:|No tests were|BUILD'" 2>&1; }
# jdkdetect reads the source level, but a dep/plugin (jline, spotless, ...) may need a newer JDK.
# On a class-file-version failure, retry one LTS tier up until it clears (jline->17, spotless->17).
JDKERR='bad class file|class file version|more recent version of the Java Runtime|UnsupportedClassVersion|release version [0-9]+ not supported|invalid target release'
out=$(_build "$jdk")
if echo "$out" | grep -qiE "$JDKERR"; then
  for j2 in 11 17 21 25; do
    [ "$j2" -le "$jdk" ] && continue
    echo "  jdk $jdk too low (class-file-version) -> retry $j2"
    out=$(_build "$j2")
    echo "$out" | grep -qiE "$JDKERR" || { jdk=$j2; break; }
  done
fi
echo "$out" | tail -6
trun=$(echo "$out" | grep -oE 'Tests run: [0-9]+, Failures: [0-9]+, Errors: [0-9]+' | tail -1)
if echo "$out" | grep -q 'No tests were' || [ -z "$trun" ] || echo "$out" | grep -q 'BUILD FAILURE' || echo "$trun" | grep -qE 'Failures: [1-9]|Errors: [1-9]'; then
  echo "RESULT $UP SKIP build-or-test-fail"; exit 0
fi
cat > /tmp/body.md <<EOF
Additive unit tests for \`$CLS\` — edge cases and current behavior pinned with explicit assertions. No existing test or production code changed.

Verified green under Java $jdk (\`mvn -pl $mod test -Dtest=${CLS}Test\` → $trun).
EOF
cd "$D" || exit 0
git checkout -q -b "add-${CLS}-tests"
git add "$tf"
git -c user.name=vasiliy-mikhailov -c user.email=vasiliy-mikhailov@users.noreply.github.com commit -q -s -m "Add unit tests for $CLS

Additive unit tests only - no existing test or production code changed."
gh auth setup-git >/dev/null 2>&1
timeout 90 git push -q -u origin "add-${CLS}-tests" 2>&1 | tail -1
base=$(timeout 30 gh repo view "$UP" --json defaultBranchRef -q .defaultBranchRef.name)
url=$(timeout 60 gh pr create --repo "$UP" --base "$base" --head "vasiliy-mikhailov:add-${CLS}-tests" --title "Add unit tests for $CLS" --body-file /tmp/body.md 2>&1 | grep -oE "https://github.com/$UP/pull/[0-9]+")
echo "RESULT $UP PR ${url:-create-failed}"
