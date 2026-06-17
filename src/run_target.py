"""P2 driver: kill-test loop on one queued target, then open an upstream PR if the gain is
significant.

  python run_target.py <repo> [idx]        # improve candidate #idx, PR if significant
  python run_target.py <repo> [idx] --no-pr # improve only, never open a PR
"""
import sys, json
import killtests, pr
import corpus_queue as queue


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    no_pr = "--no-pr" in sys.argv
    repo = args[0]
    idx = int(args[1]) if len(args) > 1 else 0

    rec = next((r for r in queue.load() if r["repo"] == repo and r.get("admitted")), None)
    if not rec:
        print(f"no admitted queue record for {repo}; run gate first")
        sys.exit(2)
    c = rec["candidate_classes"][idx]
    print(f"target: {repo}  {c['target_class']}  (tests={c['n_test']})")

    result = killtests.improve(
        repo=rec["repo_dir"], target_class=c["target_class"],
        target_tests=c["target_tests"], test_file=c["test_file"], src_file=c["src_file"])
    print(json.dumps(result, indent=2))

    if no_pr:
        return
    if pr.is_significant(result):
        out = pr.open_for_result(rec["repo"], rec["repo_dir"], result)
        print("PR:", json.dumps(out))
    else:
        g = result.get("killed_after", 0) - result.get("killed_before", 0)
        print(f"no PR: gain not significant (killed +{g}, "
              f"score +{result.get('score_after',0)-result.get('score_before',0):.1%})")


if __name__ == "__main__":
    main()
