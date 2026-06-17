"""Tabulate corpus/results/*.json — the per-target mutation-score lifts (the deliverable)."""
import json, glob
from common import CORPUS


def rows():
    out = []
    for p in sorted(glob.glob(str(CORPUS / "results" / "*.json"))):
        d = json.load(open(p))
        if "score_before" in d:
            out.append(d)
    return out


def main():
    rs = rows()
    if not rs:
        print("no results yet")
        return
    print(f"{'repo':28} {'class':34} {'before':>7} {'after':>7} {'killed':>10} {'+tests':>6}")
    tb = ta = 0
    for d in rs:
        tb += d["killed_before"]; ta += d["killed_after"]
        print(f"{d['repo'][:28]:28} {d['class'][-34:]:34} "
              f"{d['score_before']:7.2%} {d['score_after']:7.2%} "
              f"{d['killed_before']:>4}->{d['killed_after']:<4} {d['tests_added']:>6}")
    print(f"\n{len(rs)} targets | total killed {tb}->{ta} (+{ta-tb})")


if __name__ == "__main__":
    main()
