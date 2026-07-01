"""Agent-parity eval (the fine-tuning problem): run ALL 3 agents on the CURRENT skill over the
eval set; a target where OpenHands (the reference) PASSes but an off-the-shelf agent does NOT is a
fine-tuning work item for THAT agent (tune its config/harness — skill + model fixed). The skill is
NOT edited here. Distinct from the OpenHands-only learning loop (harden.py).

  python parity.py [n]
"""
import sys, json
import sweep
from common import CORPUS, log

REF = "openhands"
PANEL = ("openhands", "opencode", "kilocode")
SKILL_OK = {"PASS"}
EXCLUDE = {"NO_BASELINE", "AGENT_ERROR", "MINION_CRASH", "NO_SURVIVORS", None}   # infra / saturated (0-survivor), not an agent gap (matches sweep.py INVALID)


def parity(n=3):
    sweep.sweep(n, agents=PANEL)
    data = json.load(open(CORPUS / "sweep" / "summary.json"))
    by = {(r["class"], r["agent"]): r.get("verdict") for r in data["rows"]}
    classes = sorted({r["class"] for r in data["rows"]})
    gaps = {a: [] for a in PANEL if a != REF}
    for cls in classes:
        if by.get((cls, REF)) not in SKILL_OK:
            continue                               # only judge where the oracle passed
        for a in gaps:
            v = by.get((cls, a))
            if v in EXCLUDE:
                continue
            if v not in SKILL_OK:                  # oracle passed, this agent did not -> parity gap
                gaps[a].append({"class": cls, "verdict": v})
    summary = {"ref": REF, "n": n,
               "gap_count": {a: len(g) for a, g in gaps.items()},
               "detail": gaps}
    (CORPUS).mkdir(parents=True, exist_ok=True)
    json.dump(summary, open(CORPUS / "parity.json", "w"), indent=2)
    log("slow", "parity_done", ref=REF, gap_count=summary["gap_count"])
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    parity(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
