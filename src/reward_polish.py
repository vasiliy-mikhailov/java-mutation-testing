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

    # drop unused private helper methods/fields (name referenced only at its own declaration) — these
    # are left behind when tests that used them are removed. Conservative: word-boundary count <= 1.
    def _skip(s, j):  # if s[j] starts a string/char literal or //|/* comment, return index past it
        c = s[j]
        if c == '"' or c == "'":
            j += 1
            while j < len(s) and s[j] != c:
                j += 2 if s[j] == "\\" else 1
            return j + 1
        if c == "/" and j + 1 < len(s):
            if s[j + 1] == "/":
                e = s.find("\n", j)
                return len(s) if e < 0 else e
            if s[j + 1] == "*":
                e = s.find("*/", j + 2)
                return len(s) if e < 0 else e + 2
        return j + 1

    def _brace_end(s, b):
        d = 0
        j = b
        while j < len(s):
            c = s[j]
            if c == '"' or c == "'" or c == "/":
                j = _skip(s, j)
                continue
            if c == "{":
                d += 1
            elif c == "}":
                d -= 1
                if d == 0:
                    return j
            j += 1
        return len(s) - 1

    def _semi_end(s, b):  # index of the statement-terminating ';' not inside a literal/comment
        j = b
        while j < len(s):
            c = s[j]
            if c == '"' or c == "'" or c == "/":
                j = _skip(s, j)
                continue
            if c == ";":
                return j
            j += 1
        return len(s) - 1
    removed_members = 0
    for _ in range(8):  # iterate: removing A may make its callee B unused
        cut = None
        for m in re.finditer(r'(?m)^[ \t]*private\s+(?:static\s+|final\s+)*[\w.$<>,\[\]]+\s+(\w+)\s*\([^;{]*\)\s*(?:throws[\w.,\s]+?)?\{', src):
            if len(re.findall(r'\b' + re.escape(m.group(1)) + r'\b', src)) <= 1:
                ls = src.rfind("\n", 0, m.start()) + 1
                cut = (ls, _brace_end(src, src.index("{", m.start())) + 1)
                break
        if not cut:
            for m in re.finditer(r'(?m)^[ \t]*private\s+(?:static\s+|final\s+)*[\w.$<>,\[\]\s]+?\b(\w+)\s*=[^;{]*;', src):
                if len(re.findall(r'\b' + re.escape(m.group(1)) + r'\b', src)) <= 1:
                    ls = src.rfind("\n", 0, m.start()) + 1
                    cut = (ls, _semi_end(src, m.start()) + 1)
                    break
        if not cut:
            break
        a, b = cut
        e = b
        while e < len(src) and src[e] in " \t":
            e += 1
        if e < len(src) and src[e] == "\n":
            e += 1
        src = src[:a] + src[e:]
        removed_members += 1
    if removed_members:
        fixes.append(f"dropped {removed_members} unused private member(s)")
        # member removal can orphan the imports those members used — sweep imports again
        body = re.sub(r'(?m)^\s*import\s.*;', '', src)
        extra = 0
        for m in list(re.finditer(r'(?m)^[ \t]*import\s+(?!static\b)([\w.]+)\s*;[ \t]*\n', src)):
            fqn = m.group(1)
            if fqn.endswith(".*"):
                continue
            if not re.search(r'\b' + re.escape(fqn.rsplit(".", 1)[-1]) + r'\b', body):
                src = src.replace(m.group(0), "", 1)
                extra += 1
        if extra:
            fixes.append(f"dropped {extra} import(s) orphaned by member removal")

    if fixes:
        open(path, "w", encoding="utf-8").write(src)
    return fixes


if __name__ == "__main__":
    import sys
    print(polish(sys.argv[1]))
