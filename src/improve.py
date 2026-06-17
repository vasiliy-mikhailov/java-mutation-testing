"""Perpetual OpenHands improvement queue (parallel). Runs FOREVER: each cycle draws the ranked
corpus, processes already-PASSED targets FIRST (re-improve / re-score under the current skill), then
every other ranked target, across IMPROVE_WORKERS parallel OpenHands runs. Sleeps + redraws when the
corpus is momentarily dry (dig keeps filling). Opens a private-mirror PR only the FIRST time a class
passes (no duplicate PRs on re-runs). Each target gets its own clone dir + uniquely-named panel
container, so workers never collide. No cap — stop with `docker rm -f jmt-improve`.
  python improve.py [cycle_sleep_secs=600] [pool=500]   (workers via IMPROVE_WORKERS env, default 2)
"""
import sys, os, json, glob, subprocess, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import draw, gate, panel
from common import PROJECT, log

IMPROVE_WORKERS = int(os.environ.get("IMPROVE_WORKERS", "2"))


def _verdicts():
    """(passed_classes, classes_with_a_pr) from prior panel result files."""
    passed, has_pr = set(), set()
    for f in glob.glob(str(PROJECT / "corpus" / "panel" / "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        c = d.get("class")
        if not c:
            continue
        if d.get("verdict") in ("PASS", "PASS_BUT_NOT_CONSERVED"):
            passed.add(c)
        if (d.get("pr") or {}).get("url"):
            has_pr.add(c)
    return passed, has_pr


def _run_one(t, open_pr):
    # unique dest per (repo, class) so parallel workers never share a clone dir
    dest = "clones/improve_" + t["repo"].replace("/", "_") + "__" + t["target_class"].rsplit(".", 1)[-1]
    try:
        gate.clone(t["repo"], dest=str(PROJECT / dest))
        r = panel.run_agent("openhands", dest, t["target_class"], t["target_tests"],
                            t["test_file"], t["src_file"], timeout=3000, open_pr=open_pr)
        url = (r.get("pr") or {}).get("url")
        sb, sa, kb, ka = r.get("score_before"), r.get("score_after"), r.get("killed_before"), r.get("killed_after")
        gain = "" if sa is None else "  %.3f->%.3f reward=+%d" % (sb, sa, (ka - kb))
        print("%-44s %-22s %-14s%s  PR=%s" % (t["repo"], t["target_class"].split(".")[-1], r["verdict"], gain, url), flush=True)
        if r["verdict"] == "NO_BASELINE":
            import corpus_queue as _q
            _q.mark_no_baseline(t["repo"])
        return r["verdict"]
    except Exception as e:
        print("%s ERROR %s" % (t["repo"], str(e)[:140]), flush=True)
        return "ERROR"
    finally:
        subprocess.run(["rm", "-rf", str(PROJECT / dest)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(cycle_sleep=600, pool=500):
    log("slow", "improve_start", mode="perpetual_passed_first", workers=IMPROVE_WORKERS, pool=pool)
    cycle = 0
    with ThreadPoolExecutor(max_workers=IMPROVE_WORKERS) as ex:
        while True:
            cycle += 1
            cands = draw.draw(pool)
            if not cands:
                log("slow", "improve_dry_sleep", cycle=cycle)
                time.sleep(cycle_sleep)
                continue
            passed, has_pr = _verdicts()
            front = [t for t in cands if t["target_class"] in passed]      # already passed -> front
            rest  = [t for t in cands if t["target_class"] not in passed]
            queue = front + rest
            log("slow", "improve_cycle", cycle=cycle, total=len(queue),
                passed_first=len(front), workers=IMPROVE_WORKERS)
            futs = [ex.submit(_run_one, t, t["target_class"] not in has_pr) for t in queue]
            for _ in as_completed(futs):
                pass
            log("slow", "improve_cycle_done", cycle=cycle)


if __name__ == "__main__":
    cs = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    pl = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    main(cs, pl)
