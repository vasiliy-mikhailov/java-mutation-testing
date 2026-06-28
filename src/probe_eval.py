"""Pre-probe a scoreable eval set (pre-filter before the expensive agent sweep).

draw the ranked pool, run a baseline PIT (no agent) on each, keep the first N that
actually score, and freeze them to corpus/eval_set.json with the detected JDK +
baseline score. This guarantees every target in the 3-agent sweep produces a real
per-agent score (no agent run wasted on a dead baseline). Resumable: re-running
re-probes from the top of the pool; pass a larger pool for more margin.
"""
import os, sys, json, shutil
import draw, gate, pit, jdkdetect, sandbox
from common import DATA, CLONES, CORPUS, log


def probe(n=20, pool=None):
    pool = pool or max(40, n * 2)
    cand = draw.draw(pool)
    log("slow", "probe_start", want=n, pool=len(cand))
    kept = []
    for t in cand:
        if len(kept) >= n:
            break
        san = t["repo"].replace("/", "_")
        dest = CLONES / f"probe_{san}"
        try:
            gate.clone(t["repo"], dest=str(dest))
            rel = os.path.relpath(str(dest), str(DATA))     # DATA-relative; abs_repo resolves against DATA
            abs_repo = sandbox.abs_repo(rel)
            jdk = jdkdetect.detect_jdk(abs_repo)
            base = pit.run_pit(rel, t["target_class"], t["target_tests"], jdk=jdk, timeout=31_536_000)
            if base.get("ok"):
                t2 = dict(t)
                t2["jdk"] = jdk
                t2["probe_score"] = round(base["score"], 4)
                t2["probe_killed"] = base["killed"]
                t2["probe_total"] = base["total"]
                kept.append(t2)
                json.dump(kept, open(CORPUS / "eval_set.json", "w"), indent=2)  # checkpoint
                log("slow", "probe_keep", repo=t["repo"], cls=t["target_class"],
                    jdk=jdk, score=t2["probe_score"], kept=len(kept))
            else:
                log("medium", "probe_drop", repo=t["repo"], cls=t["target_class"], rc=base.get("rc"))
        except Exception as e:
            log("medium", "probe_err", repo=t["repo"], err=str(e)[:200])
        finally:
            shutil.rmtree(dest, ignore_errors=True)
    json.dump(kept, open(CORPUS / "eval_set.json", "w"), indent=2)
    log("slow", "probe_done", kept=len(kept), want=n)
    return kept


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    pool = int(sys.argv[2]) if len(sys.argv) > 2 else None
    k = probe(n, pool)
    print(f"kept {len(k)}/{n}")
    for t in k:
        print(f"  {t['repo']:42} jdk{t['jdk']} score={t['probe_score']} {t['target_class']}")
