"""reward_polish.py — harness-side mechanical fixes that push a generated test from reward 0.9 -> 1.0.

Runs POST-agent: don't depend on the agent closing the reward loop ([[harness-applied-beats-agent]]).
ONLY safe, behaviour-preserving fixes that cannot drop a mutant kill or change an assertion:
  - seed an unseeded `new Random()` -> `new Random(42L)`  (rule 5 deterministic)
  - drop an import whose type never appears in the body   (rule 7 no-unused-code)
Semantic warts (reflection, coverage-theater, partial-assert) are NOT touched — they need a real
rewrite, so they stay HELD by the gate. The PR-time pipeline re-verifies green + re-gates anyway.
"""
import re

_RANDOM = re.compile(r'new\s+(java\.util\.)?Random\s*\(\s*\)')


def polish(path):
    """Apply safe fixes in-place. Returns the list of fixes made (empty if none)."""
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    fixes = []

    src, n = _RANDOM.subn(lambda m: "new " + (m.group(1) or "") + "Random(42L)", src)
    if n:
        fixes.append(f"seeded {n} Random()")

    body = re.sub(r'(?m)^\s*import\s.*;', '', src)  # references must be in the body, not imports
    dropped = 0
    for m in list(re.finditer(r'(?m)^[ \t]*import\s+(?!static\b)([\w.]+)\s*;[ \t]*\n', src)):
        fqn = m.group(1)
        if fqn.endswith(".*"):
            continue
        simple = fqn.rsplit(".", 1)[-1]
        if not re.search(r'\b' + re.escape(simple) + r'\b', body):
            src = src.replace(m.group(0), "", 1)
            dropped += 1
    if dropped:
        fixes.append(f"dropped {dropped} unused import(s)")

    if fixes:
        open(path, "w", encoding="utf-8").write(src)
    return fixes


if __name__ == "__main__":
    import sys
    print(polish(sys.argv[1]))
