"""Perform mutation-testing improvement: repeatedly take the top-ranked scoreable target not yet
evaluated, run OpenHands following the skill, open a private-mirror PR on improvement, and move on.
NO_BASELINE targets are marked + skipped (corpus self-cleans). Reaps each clone after.
  python improve.py [max_improvements] [max_attempts]
"""
import sys, json, glob, subprocess
import draw, gate, panel
from common import PROJECT, log


def evaluated():
    s = set()
    for f in glob.glob(str(PROJECT / "corpus" / "panel" / "*.json")):
        try: s.add(json.load(open(f))["class"])
        except Exception: pass
    return s


def main(max_improvements=8, max_attempts=30):
    improved, attempts, prs = 0, 0, []
    log("slow", "improve_start", goal=max_improvements)
    while improved < max_improvements and attempts < max_attempts:
        done = evaluated()
        targets = [t for t in draw.draw(80) if t["target_class"] not in done]
        if not targets:
            log("slow", "improve_dry"); break
        t = targets[0]
        attempts += 1
        dest = "clones/improve_" + t["repo"].replace("/", "_")
        try:
            gate.clone(t["repo"], dest=str(PROJECT / dest))
            r = panel.run_agent("openhands", dest, t["target_class"], t["target_tests"],
                                t["test_file"], t["src_file"], timeout=1800)
            url = (r.get("pr") or {}).get("url")
            print("%-44s %-22s %-14s PR=%s" % (t["repo"], t["target_class"].split(".")[-1], r["verdict"], url), flush=True)
            if r["verdict"] == "NO_BASELINE":
                import corpus_queue as _q
                _q.mark_no_baseline(t["repo"])
            if r["verdict"] in ("PASS", "PASS_BUT_NOT_CONSERVED"):
                improved += 1
                if url: prs.append(url)
        except Exception as e:
            print("%s ERROR %s" % (t["repo"], str(e)[:140]), flush=True)
        finally:
            subprocess.run(["rm", "-rf", str(PROJECT / dest)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("=== improvements: %d / attempts: %d ===" % (improved, attempts), flush=True)
    for u in prs: print("  PR:", u, flush=True)
    log("slow", "improve_done", improved=improved, attempts=attempts)


if __name__ == "__main__":
    a = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    b = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    main(a, b)
