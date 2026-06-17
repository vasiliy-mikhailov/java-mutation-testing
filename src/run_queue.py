"""P3 driver: refill the candidate queue. discover -> gate -> admit.

  python run_queue.py [n]      # discover up to n fresh repos, gate each, admit the green ones
"""
import sys, json
import discover, gate
import corpus_queue as queue
from common import log


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    found = discover.discover(n=n)
    print(f"discovered {len(found)} candidate repos")
    admitted = 0
    for c in found:
        repo = c["repo"]
        try:
            rec = gate.gate(repo)
        except Exception as e:
            log("medium", "gate_error", repo=repo, err=str(e)[:200])
            print(f"  {repo}: ERROR {e}")
            continue
        if rec.get("admitted"):
            queue.admit(rec)
            admitted += 1
            print(f"  ADMIT {repo}  tests={rec['test_count']} candidates={len(rec['candidate_classes'])}")
        else:
            print(f"  drop  {repo}: {rec.get('reason')}")
    print(f"admitted {admitted}/{len(found)}; queue size = {len(queue.load())}")


if __name__ == "__main__":
    main()
