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
from common import PROJECT, CORPUS, DATA, log

IMPROVE_WORKERS = int(os.environ.get("IMPROVE_WORKERS", "2"))


def _verdicts():
    """(passed_classes, captured_classes). 'captured' = already persisted to the LOCAL store
    (pr.GENERATED/<slug>/meta-<class>.json — one meta PER CLASS), since PASSes now persist locally,
    not to jmt-* mirror PRs. Reading the panel JSON's old pr.url would keep classes captured by
    now-DELETED mirrors stuck in has_pr forever, so they would never re-persist their fresh output."""
    import pr
    passed, has_pr = set(), set()
    for f in glob.glob(str(CORPUS / "panel" / "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        c = d.get("class")
        if c and d.get("verdict") in ("PASS", "PASS_BUT_NOT_CONSERVED"):
            passed.add(c)
    for mf in glob.glob(os.path.join(pr.GENERATED, "*", "meta-*.json")):
        try:
            m = json.load(open(mf))
        except Exception:
            continue
        if m.get("class"):
            has_pr.add(m["class"])
    return passed, has_pr


def _run_one(t, open_pr):
    # unique dest per (repo, class) so parallel workers never share a clone dir. Use the FULL
    # FQCN (sanitized), not the simple name, so two same-simple-name classes never share a dest.
    dest = "clones/improve_" + t["repo"].replace("/", "_") + "__" + t["target_class"].replace(".", "_")
    try:
        gate.clone(t["repo"], dest=str(DATA / dest))
        r = panel.run_agent("openhands", dest, t["target_class"], t["target_tests"],
                            t["test_file"], t["src_file"], jdk=t.get("jdk"),
                            timeout=31_536_000, open_pr=open_pr)
        url = (r.get("pr") or {}).get("url")
        sb, sa, kb, ka = r.get("score_before"), r.get("score_after"), r.get("killed_before"), r.get("killed_after")
        cb, ca = r.get("line_cov_before"), r.get("line_cov_after")
        cov = "" if (cb is None or ca is None) else "  cov %.0f%%->%.0f%%" % (cb*100, ca*100)
        gain = "" if sa is None else "  mut %.3f->%.3f reward=+%d%s" % (sb, sa, (ka - kb), cov)
        print("%-44s %-22s %-14s%s  PR=%s" % (t["repo"], t["target_class"].split(".")[-1], r["verdict"], gain, url), flush=True)
        return r["verdict"]
    except Exception as e:
        print("%s ERROR %s" % (t["repo"], str(e)[:140]), flush=True)
        return "ERROR"
    finally:
        subprocess.run(["rm", "-rf", str(DATA / dest)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(cycle_sleep=600, pool=500):
    log("slow", "improve_start", mode="perpetual_fresh_first", workers=IMPROVE_WORKERS, pool=pool)
    cycle = 0
    with ThreadPoolExecutor(max_workers=IMPROVE_WORKERS) as ex:
        while True:
            cycle += 1
            cands = draw.draw(10**9)  # draw ALL admitted candidates — cover every class, not a top-N slice
            if not cands:
                log("slow", "improve_dry_sleep", cycle=cycle)
                time.sleep(cycle_sleep)
                continue
            passed, has_pr = _verdicts()
            # spend lanes on NEW classes, not churn. Skip classes already CAPTURED to the local store
            # (shipped/stored — re-running them just re-persists the same result and wastes lanes), and
            # run never-passed (truly new) classes FIRST so fresh candidates actually surface.
            uncaptured = [t for t in cands if t["target_class"] not in has_pr]
            fresh = [t for t in uncaptured if t["target_class"] not in passed]
            retry = [t for t in uncaptured if t["target_class"] in passed]
            queue = fresh + retry
            log("slow", "improve_cycle", cycle=cycle, total=len(queue),
                fresh=len(fresh), retry=len(retry), workers=IMPROVE_WORKERS)
            futs = [ex.submit(_run_one, t, t["target_class"] not in has_pr) for t in queue]
            for _ in as_completed(futs):
                pass
            log("slow", "improve_cycle_done", cycle=cycle)


if __name__ == "__main__":
    cs = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    pl = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    main(cs, pl)
