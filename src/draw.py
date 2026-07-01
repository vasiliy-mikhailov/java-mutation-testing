"""Draw selector (P3 feeder): rank the admitted corpus and emit the top-N targets for the panel.

Ranking is MERGE-VALUE first: a green test on a repo no maintainer will merge is wasted, so we
prioritize by a repo STAR TIER (the proxy for an active, merge-likely project) and only then by
class-level test density (n_test — the best single proxy for a logic-dense, well-covered class
where survivors hide) + repo test_count. Tiers (not raw stars) so a 1001★ repo doesn't rigidly
outrank a 1000★ one, but every ≥1k★ repo beats every <1k one. Emits flat target records the
panel sweep consumes directly. NB: this is scheduling, not a cap — low-star repos still drain in
the tail; we just serve high-merge-value targets first.
"""
import json
import corpus_queue as queue

W_NTEST, W_REPO_TESTS = 3.0, 0.05


def star_tier(st):
    st = st or 0
    if st >= 20000: return 5
    if st >= 10000: return 4
    if st >= 5000:  return 3
    if st >= 1000:  return 2
    if st >= 500:   return 1
    return 0


def score(repo_rec, cand):
    # intra-tier density score (stars handled by star_tier as the PRIMARY sort key)
    n = cand.get("n_test", 0) or 0
    tc = repo_rec.get("test_count", 0) or 0
    return W_NTEST * n + W_REPO_TESTS * tc


def _module_of_src(src_file):
    """Repo-rel module dir from a main source path (the segment before '/src/main/', '.' for root).
    Fallback for records predating gate's per-candidate module stamp; improve.py groups on this."""
    i = (src_file or "").find("/src/main/")
    return src_file[:i] if i > 0 else "."


def draw(n=10, build_tool="maven"):
    # load the class-keyed skip sets ONCE (otherwise they are re-read per candidate, and 'take every
    # class' makes both the candidate count and saturated.txt large - that would be quadratic per draw)
    skip = set()
    for f in (queue.NO_BASELINE_FILE, queue.SATURATED_FILE):
        if f.exists():
            skip |= set(f.read_text().split())
    targets = []
    for r in queue.load():
        if not r.get("admitted"):
            continue
        if build_tool and r.get("build_tool") != build_tool:
            continue
        for c in r.get("candidate_classes", []):
            # NO_BASELINE (un-baselineable) and SATURATED (zero survivors, nothing to improve) are
            # both CLASS-keyed skips: drop only that class, never the whole repo.
            if c["target_class"] in skip:
                continue
            targets.append({
                "repo": r["repo"], "repo_dir": r["repo_dir"], "sha": r.get("sha"),
                "build_tool": r.get("build_tool"), "stars": r.get("stars"), "jdk": r.get("jdk", 21),
                "repo_test_count": r.get("test_count"),
                "target_class": c["target_class"], "target_tests": c["target_tests"],
                "test_file": c["test_file"], "src_file": c["src_file"],
                "module": c.get("module") or _module_of_src(c.get("src_file", "")),
                "n_test": c.get("n_test"), "score": round(score(r, c), 2),
            })
    # MERGE-VALUE first: star tier is the primary key, density score the tiebreak within a tier
    targets.sort(key=lambda t: (-star_tier(t.get("stars")), -t["score"]))
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
