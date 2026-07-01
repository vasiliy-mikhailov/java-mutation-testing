"""Harden loop (P3 reward): keep a SKILL.md edit ONLY if no agent's eval PASS-rate regresses.

  python harden.py baseline [n]        # sweep the frozen eval set, record as the baseline
  python harden.py check "<note>" [n]  # AFTER editing SKILL.md: re-sweep, compare, KEEP or REVERT

SKILL.md is backed up before each check and restored on any regression — so a bad edit can never
stick. The eval set is corpus/eval_set.json (fixed, for apples-to-apples comparison). Decisions are
appended to corpus/harden_log.jsonl. n defaults to 3 targets (x3 agents) for a faster inner loop.
"""
import sys, json, time, shutil, hashlib
import sweep

LEARN_AGENTS = ("openhands",)   # skill learning = OpenHands oracle only (fast)
from common import PROJECT, CORPUS, log

SKILL = PROJECT / "skills" / "improve-java-tests" / "SKILL.md"
BASELINE = CORPUS / "harden_baseline.json"
LOG = CORPUS / "harden_log.jsonl"


def skill_sha():
    return hashlib.sha1(SKILL.read_bytes()).hexdigest()[:12]


def _tally(summary):
    return {a: (v["pass_rate"] or 0) for a, v in summary["tally"].items()}


def baseline(n):
    s = sweep.sweep(n, agents=LEARN_AGENTS)
    t = _tally(s)
    BASELINE.write_text(json.dumps({"skill_sha": skill_sha(), "tally": t, "summary": s}, indent=2))
    log("slow", "harden_baseline", tally=t, skill_sha=skill_sha())
    print("baseline pass-rates:", t)


def check(note, n):
    if not BASELINE.exists():
        print("no baseline — run `harden.py baseline` first"); return
    base = json.load(open(BASELINE))
    bt = base["tally"]
    backup = f"{SKILL}.bak.{int(time.time())}"
    shutil.copyfile(str(SKILL), backup)
    new = sweep.sweep(n, agents=LEARN_AGENTS)
    nt = _tally(new)
    regressed = sorted(a for a in bt if nt.get(a, 0) < bt[a])
    improved = sorted(a for a in bt if nt.get(a, 0) > bt[a])
    decision = "REVERT" if regressed else "KEEP"
    if regressed:
        shutil.copyfile(backup, str(SKILL))            # restore the pre-edit skill
    else:
        BASELINE.write_text(json.dumps({"skill_sha": skill_sha(), "tally": nt, "summary": new}, indent=2))
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": note, "decision": decision,
           "baseline": bt, "new": nt, "regressed": regressed, "improved": improved,
           "skill_sha": skill_sha(), "backup": backup}
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
    log("slow", "harden_decision", note=note, decision=decision,
        regressed=regressed, improved=improved)
    print(json.dumps(rec, indent=2))


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "baseline":
        baseline(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    elif mode == "check":
        note = sys.argv[2] if len(sys.argv) > 2 else "(no note)"
        check(note, int(sys.argv[3]) if len(sys.argv) > 3 else 3)
    else:
        print("usage: harden.py baseline [n] | check <note> [n]")
