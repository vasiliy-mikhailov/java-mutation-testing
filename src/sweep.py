"""P3 sweep: draw the top corpus targets, run every agent on each, tally per-agent PASS-rate.

The per-agent PASS-rate over the corpus IS the skill's score; a skill edit is admitted only if
no agent regresses (the no-regression loop). Resumable: a (agent,target) whose result file
already exists is skipped. Each (agent,target) gets a FRESH docker-bounded clone (gate.clone in
this orch container) so baselines are clean. Runs sequentially to bound load while the dig runs.
"""
import os, sys, json, time
import draw, panel, gate
from common import DATA, CLONES, CORPUS, log

AGENTS = ("openhands", "opencode", "kilocode")
SWEEP_DIR = CORPUS / "sweep"


def _result_path(agent, repo, cls):
    dest = CLONES / f"sweep_{agent}_{repo.replace('/', '_')}"
    rel = os.path.relpath(str(dest), str(DATA))                  # same DATA-relative rel sweep passes panel
    return CORPUS / "panel" / f"{agent}__{rel.replace('/', '__')}__{cls}.json"   # mirror panel.py's write


def sweep(n_targets=3, agents=AGENTS):
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    ev = CORPUS / "eval_set.json"
    targets = (json.load(open(ev))[:n_targets] if ev.exists() else draw.draw(n_targets))
    log("slow", "sweep_start", n_targets=len(targets), agents=list(agents))
    rows = []
    for t in targets:
        for agent in agents:
            san = t["repo"].replace("/", "_")
            dest = CLONES / f"sweep_{agent}_{san}"
            rp = _result_path(agent, t["repo"], t["target_class"])
            if rp.exists():
                rows.append(json.load(open(rp)))
                log("fast", "sweep_skip_done", agent=agent, repo=t["repo"], cls=t["target_class"])
                continue
            try:
                gate.clone(t["repo"], dest=str(dest))           # fresh, docker-bounded
                rel = os.path.relpath(str(dest), str(DATA))     # DATA-relative; abs_repo resolves against DATA
                r = panel.run_agent(agent, rel, t["target_class"], t["target_tests"],
                                    t["test_file"], t["src_file"])
                rows.append(r)
            except Exception as e:
                log("medium", "sweep_error", agent=agent, repo=t["repo"], err=str(e)[:200])

    # tally per-agent
    INVALID = {"NO_BASELINE", "AGENT_ERROR", "MINION_CRASH"}   # infra (un-scoreable / forked-JVM crash), not a skill outcome
    tally = {}
    for a in agents:
        rs = [r for r in rows if r.get("agent") == a]
        valid = [r for r in rs if r.get("verdict") not in INVALID]
        passes = sum(1 for r in valid if r.get("verdict") == "PASS")
        tally[a] = {"runs": len(rs), "valid": len(valid), "no_baseline": len(rs) - len(valid),
                    "pass": passes, "pass_rate": round(passes / len(valid), 3) if valid else None}
    summary = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "n_targets": len(targets), "tally": tally,
               "targets": [{"repo": t["repo"], "class": t["target_class"], "score": t["score"]} for t in targets]}
    with open(SWEEP_DIR / "summary.json", "w") as f:
        json.dump({"summary": summary, "rows": [{k: v for k, v in r.items() if k != "log_tail"} for r in rows]}, f, indent=2)
    log("slow", "sweep_done", **{a: tally[a]["pass_rate"] for a in agents})
    return summary


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    s = sweep(n)
    print(json.dumps(s, indent=2))
