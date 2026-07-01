"""Dig loop (P4): keep corpus/queue.jsonl topped up with admitted targets — runs as a daemon.

Resumable: discover.py already skips queued repos, so each round pulls a FRESH batch; gate.py
gates each (clone HEAD -> compile -> @Test count -> green -> candidate classes); the green ones
are admitted (with their star count threaded through for the draw). Bounded: gh<=30/min so
discover is sequential; gating runs a few workers. Guards disk. Logs to frog's eye. Stops at
DIG_TARGET admitted (0 = run forever, topping up). Designed to run inside a `docker run -d`
container (ijt-orch image) — nothing on the host but docker.
"""
import os, time, concurrent.futures
import discover, gate
import corpus_queue as queue
from common import log, CLONES
from maint import disk_free_gb, reap_clone

BATCH = int(os.environ.get("DIG_BATCH", "12"))
WORKERS = int(os.environ.get("DIG_WORKERS", "3"))    # concurrent gates; keep low (CPU+Nexus)
MIN_DISK_GB = int(os.environ.get("DIG_MIN_DISK_GB", "30"))


def _admitted():
    return [r for r in queue.load() if r.get("admitted")]


def _gate_one(cand):
    queue.mark_seen(cand["repo"])  # never re-gate, pass or fail
    if cand.get("build_tool") != "maven":
        log("fast", "dig_drop", repo=cand["repo"], reason="gradle_skip_preclone")
        return False
    dest = CLONES / cand["repo"].replace("/", "__")
    try:
        rec = gate.gate(cand["repo"])
        if rec.get("admitted"):
            rec["stars"] = cand.get("stars")          # thread stars through for draw ranking
            queue.admit(rec)
            log("medium", "dig_admit", repo=cand["repo"], stars=cand.get("stars"),
                tests=rec["test_count"], cands=len(rec["candidate_classes"]))
            return True
        log("fast", "dig_drop", repo=cand["repo"], reason=rec.get("reason"))
    except Exception as e:
        log("fast", "dig_error", repo=cand["repo"], err=str(e)[:200])
    finally:
        reap_clone(str(dest))
    return False


def main():
    log("slow", "dig_start", workers=WORKERS, batch=BATCH, have=len(_admitted()))
    while True:                                  # a dig has no finish line — it just digs
        if disk_free_gb() < MIN_DISK_GB:
            log("slow", "dig_disk_low", free_gb=round(disk_free_gb(), 1))
            time.sleep(600)
            continue
        batch = discover.discover(n=BATCH)
        if not batch:                            # updated-window temporarily dry — wait, never exit
            log("slow", "dig_dry", have=len(_admitted()))
            time.sleep(1800)
            continue
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            list(ex.map(_gate_one, batch))
        log("medium", "dig_round", have=len(_admitted()), free_gb=round(disk_free_gb(), 1))


if __name__ == "__main__":
    main()
