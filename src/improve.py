"""Perpetual OpenHands improvement queue. Runs FOREVER: each cycle draws the ranked corpus,
processes already-PASSED targets FIRST (re-improve / re-score them under the current skill), then
every other ranked target; sleeps + redraws when the corpus is momentarily dry (dig keeps filling).
Opens a private-mirror PR only the FIRST time a class passes (no duplicate PRs on re-runs). Reaps
each clone. No cap — stop with `docker rm -f jmt-improve`.
  python improve.py [cycle_sleep_secs=600] [pool=500]
"""
import sys, json, glob, subprocess, time
import draw, gate, panel
from common import PROJECT, log


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
    dest = "clones/improve_" + t["repo"].replace("/", "_")
    try:
        gate.clone(t["repo"], dest=str(PROJECT / dest))
        r = panel.run_agent("openhands", dest, t["target_class"], t["target_tests"],
                            t["test_file"], t["src_file"], timeout=1800, open_pr=open_pr)
        url = (r.get("pr") or {}).get("url")
        print("%-44s %-22s %-14s PR=%s" % (t["repo"], t["target_class"].split(".")[-1], r["verdict"], url), flush=True)
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
    log("slow", "improve_start", mode="perpetual_passed_first", pool=pool)
    cycle = 0
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
        log("slow", "improve_cycle", cycle=cycle, total=len(queue), passed_first=len(front))
        for t in queue:
            open_pr = t["target_class"] not in has_pr                  # PR only first time a class passes
            v = _run_one(t, open_pr)
            if v in ("PASS", "PASS_BUT_NOT_CONSERVED"):
                has_pr.add(t["target_class"])
        log("slow", "improve_cycle_done", cycle=cycle)


if __name__ == "__main__":
    cs = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    pl = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    main(cs, pl)
