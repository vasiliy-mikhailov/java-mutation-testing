"""Migrate the admitted corpus to 'take every java': re-derive candidate_classes (EVERY main class,
has_test-stamped) for each admitted repo by re-cloning + re-walking, updating the queue record.
Run with ijt-dig PAUSED so this is the sole writer to queue.jsonl (the improve loop only reads it)."""
import sys, subprocess, concurrent.futures as cf
import gate, corpus_queue as q
from common import DATA, log

def _migrate(rec):
    repo = rec["repo"]
    dest = "clones/migrate_" + repo.replace("/", "_")
    real = str(DATA / dest)
    try:
        subprocess.run(["rm", "-rf", real], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        gate.clone(repo, dest=real)
        cands = gate.candidate_classes(real)
        if not any(c.get("has_test") for c in cands):
            return (repo, "kept_old(no_tested_after_rederive)")
        mod_rank = {}
        for c in cands:
            m = c.get("module", ".")
            mod_rank[m] = max(mod_rank.get(m, 0), c.get("n_test", 0) or 0)
        old_n = len(rec.get("candidate_classes", []))
        rec2 = dict(rec)
        rec2["candidate_classes"] = cands
        rec2["modules"] = [m for m, _ in sorted(mod_rank.items(), key=lambda kv: -kv[1])]
        q.admit(rec2)
        n_un = sum(1 for c in cands if not c.get("has_test"))
        return (repo, f"ok {old_n}->{len(cands)} (+{n_un} untested, {len(rec2['modules'])} mods)")
    except Exception as e:
        return (repo, "err " + str(e)[:80])
    finally:
        subprocess.run(["rm", "-rf", real], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def main(workers=2):
    recs = [r for r in q.load() if r.get("admitted")]
    log("slow", "migrate_start", repos=len(recs))
    done, added = 0, 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for repo, status in ex.map(_migrate, recs):
            done += 1
            print(f"[{done}/{len(recs)}] {repo}: {status}", flush=True)
    log("slow", "migrate_done", repos=len(recs))

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2)
