"""Draw selector (P3 feeder): rank the admitted corpus and emit the top-N targets for the panel.

Each admitted repo carries candidate classes (Foo<->FooTest pairs). We score each candidate by
a blend of: class-level test density (n_test — the best single proxy for a logic-dense,
well-covered class where survivors hide), repo test_count, and repo stars (maintained/real).
Higher = better mutation target. Emits flat target records the panel sweep consumes directly.
"""
import json, math
import corpus_queue as queue

W_NTEST, W_REPO_TESTS, W_STARS = 3.0, 0.05, 0.5


def score(repo_rec, cand):
    n = cand.get("n_test", 0) or 0
    tc = repo_rec.get("test_count", 0) or 0
    st = repo_rec.get("stars", 0) or 0
    return W_NTEST * n + W_REPO_TESTS * tc + W_STARS * math.log1p(st)


def draw(n=10, build_tool="maven"):
    targets = []
    for r in queue.load():
        if not r.get("admitted"):
            continue
        if build_tool and r.get("build_tool") != build_tool:
            continue
        if queue.is_no_baseline(r["repo"]):
            continue
        for c in r.get("candidate_classes", []):
            if queue.is_no_baseline(c["target_class"]):
                continue
            targets.append({
                "repo": r["repo"], "repo_dir": r["repo_dir"], "sha": r.get("sha"),
                "build_tool": r.get("build_tool"), "stars": r.get("stars"), "jdk": r.get("jdk", 21),
                "repo_test_count": r.get("test_count"),
                "target_class": c["target_class"], "target_tests": c["target_tests"],
                "test_file": c["test_file"], "src_file": c["src_file"],
                "n_test": c.get("n_test"), "score": round(score(r, c), 2),
            })
    targets.sort(key=lambda t: -t["score"])
    # one candidate per repo (diversity) before filling with the rest
    seen, top, rest = set(), [], []
    for t in targets:
        (top if t["repo"] not in seen else rest).append(t)
        seen.add(t["repo"])
    ranked = (top + rest)[:n]
    return ranked


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    rows = draw(n)
    if "--json" in sys.argv:
        print(json.dumps(rows, indent=2))
    else:
        print(f"{'score':>7}  {'n_test':>6} {'stars':>6}  {'repo':32} {'class'}")
        for t in rows:
            print(f"{t['score']:>7}  {t['n_test'] or 0:>6} {t['stars'] or 0:>6}  "
                  f"{t['repo'][:32]:32} {t['target_class']}")
