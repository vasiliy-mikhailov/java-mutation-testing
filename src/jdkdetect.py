"""Detect a repo's target JDK from its pom(s) and map to the LTS we run it under.

We run a repo under the JDK it DECLARES (not the newest), because the dominant NO_BASELINE cause
is old test-instrumentation (ByteBuddy/Mockito) crashing PIT's coverage minion on a too-new JDK.
So a project targeting 8 runs on 8, 17 on 17, etc. Maps to the nearest LTS at-or-above the
declared target; defaults to 21 when nothing is declared.
"""
import re, glob, os

LTS = [8, 11, 17, 21, 25]
_PATS = [r"<maven\.compiler\.release>\s*([\d.]+)", r"<maven\.compiler\.target>\s*([\d.]+)",
         r"<maven\.compiler\.source>\s*([\d.]+)", r"<java\.version>\s*([\d.]+)",
         r"<release>\s*([\d.]+)\s*</release>", r"<target>\s*([\d.]+)\s*</target>",
         r"<source>\s*([\d.]+)\s*</source>"]


def _norm(v):
    v = v.strip()
    if v.startswith("1."):           # 1.8 -> 8
        v = v[2:]
    m = re.match(r"(\d+)", v)
    return int(m.group(1)) if m else None


def detect_jdk(abs_repo, default=21):
    found = []
    for pom in glob.glob(os.path.join(abs_repo, "**", "pom.xml"), recursive=True):
        try:
            txt = open(pom, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for p in _PATS:
            for m in re.findall(p, txt):
                n = _norm(m)
                if n:
                    found.append(n)
    if not found:
        return default
    target = max(found)              # highest declared target across modules
    for lts in LTS:
        if target <= lts:
            return lts
    return LTS[-1]                    # >25 -> 25


if __name__ == "__main__":
    import sys
    print(detect_jdk(sys.argv[1]))
