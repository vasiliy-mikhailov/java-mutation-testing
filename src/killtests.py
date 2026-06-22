"""Kill-test loop (P2 generation half) with error-feedback repair.

APPEND-ONLY: the LLM may only add new @Test methods, never touch existing ones, so no
existing test can be weakened by construction. A round is ACCEPTED only if the re-scoped
PIT run compiles, stays green (PIT aborts on any red test => rc!=0), and the killed count
rises; otherwise the test file is REVERTED and the maven/PIT error is fed back to the next
round so the model can repair (fix a compile error or a wrong assertion). A test asserting
mutant (wrong) behaviour fails against the ORIGINAL code, so PIT's green check rejects it.
"""
import os, re, json, time
import pit, llm, sandbox
from common import log, CORPUS

GEN_TOKENS = 24000

SYS = (
    "You are a Java test engineer raising a suite's PIT mutation score. You write ADDITIONAL "
    "JUnit test methods that kill specific surviving mutants by asserting the class's CORRECT "
    "behaviour. Never modify or weaken existing tests. Your assertions must pass against the "
    "real (unmutated) code. List EVERY import your methods use that the test class lacks."
)

PROMPT = """\
Target class `{cls}` has surviving PIT mutants — its tests execute these lines but don't \
verify them. Write NEW JUnit test methods (same JUnit version/style as the existing test \
class) that KILL the listed survivors by asserting correct behaviour.

=== SOURCE: {src_name} ===
{src}

=== EXISTING TEST CLASS: {test_name} (append-only; do not repeat or modify these) ===
{tests}

=== SURVIVING MUTANTS (kill these) ===
{survivors}
{feedback}
Respond with ONLY a fenced JSON object (```json ... ```), no prose outside the fence:
```json
{{"imports": ["fully.qualified.Import"],
  "methods": "<complete @Test methods, ready to paste inside the test class body>"}}
```
The methods must compile inside `{test_name}` using its existing imports plus any you list."""

FEEDBACK = """
=== YOUR PREVIOUS ATTEMPT WAS REJECTED ===
It either failed to compile or a test was red against the real code. Fix it. Error excerpt:
{error}
Previous methods:
{prev}
"""


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _write(path, txt):
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)


def _fmt_survivors(survivors):
    return "\n".join(
        f"- L{s['lineNumber']} {s['mutator']} in {s['mutatedMethod']}: {s['description']}"
        for s in survivors)


def _error_excerpt(log_tail):
    """Pull the salient compile/test-failure lines out of a maven/PIT log."""
    keep = [ln for ln in log_tail.splitlines()
            if re.search(r"\[ERROR\]|BUILD FAILURE|did not pass|cannot find symbol|"
                         r"\bexpected:|\bbut was:|AssertionError|\.java:\[?\d", ln)]
    return "\n".join(keep) if keep else log_tail


def _apply(test_src, imports, methods):
    """Append-only insert: new imports after the last import, methods before the final brace."""
    lines = test_src.splitlines()
    new_imports = [f"import {i};" for i in imports if f"import {i};" not in test_src]
    if new_imports:
        last_imp = max((n for n, ln in enumerate(lines) if ln.strip().startswith("import ")),
                       default=next((n for n, ln in enumerate(lines) if ln.strip().startswith("package ")), 0))
        lines[last_imp + 1:last_imp + 1] = new_imports
    body = "\n".join(lines)
    cut = body.rfind("}")
    return body[:cut] + "\n" + methods.strip() + "\n}" + body[cut + 1:]


def _gen(src_txt, test_txt, target_class, src_name, test_name, survivors, prev=None, error=None):
    feedback = FEEDBACK.format(error=error, prev=prev) if (prev and error) else ""
    prompt = PROMPT.format(cls=target_class, src_name=src_name, src=src_txt,
                           test_name=test_name, tests=test_txt,
                           survivors=_fmt_survivors(survivors), feedback=feedback)
    resp = llm.complete([{"role": "system", "content": SYS},
                         {"role": "user", "content": prompt}], max_tokens=GEN_TOKENS)
    obj = llm.extract_json(resp)
    return (obj.get("methods", "") or "").strip(), (obj.get("imports", []) or [])


def improve(repo, target_class, target_tests, test_file, src_file,
            jdk=21, rounds=4, mutators="DEFAULTS", timeout=31_536_000):
    abs_repo = sandbox.abs_repo(repo)
    test_path = os.path.join(abs_repo, test_file)
    src_path = os.path.join(abs_repo, src_file)
    src_name, test_name = os.path.basename(src_file), os.path.basename(test_file)

    base = pit.run_pit(repo, target_class, target_tests, jdk=jdk, mutators=mutators, timeout=timeout)
    if not base["ok"]:
        log("medium", "baseline_fail", repo=repo, cls=target_class, rc=base["rc"])
        return {"repo": repo, "class": target_class, "error": "baseline_pit_failed",
                "log_tail": base["log_tail"]}

    score_before, survivors, killed_now = base["score"], base["survivors"], base["killed"]
    src_txt = _read(src_path)
    tests_added, accepted_rounds = 0, []
    prev_methods, prev_error = None, None
    log("medium", "baseline", repo=repo, cls=target_class, score=round(score_before, 4),
        killed=base["killed"], total=base["total"], survivors=len(survivors))

    for rnd in range(1, rounds + 1):
        if not survivors:
            break
        test_txt = _read(test_path)
        try:
            methods, imports = _gen(src_txt, test_txt, target_class, src_name, test_name,
                                    survivors, prev=prev_methods, error=prev_error)
        except Exception as e:
            log("fast", "llm_fail", repo=repo, cls=target_class, rnd=rnd, err=str(e)[:200])
            continue
        if not methods:
            continue

        backup = test_txt
        _write(test_path, _apply(test_txt, imports, methods))
        res = pit.run_pit(repo, target_class, target_tests, jdk=jdk, mutators=mutators, timeout=timeout)

        if res["ok"] and res["killed"] > killed_now:
            tests_added += methods.count("@Test")
            killed_now, survivors = res["killed"], res["survivors"]
            prev_methods, prev_error = None, None
            accepted_rounds.append({"round": rnd, "killed": killed_now,
                                    "score": round(res["score"], 4), "survivors": len(survivors)})
            log("medium", "round_accept", repo=repo, cls=target_class, rnd=rnd,
                killed=killed_now, score=round(res["score"], 4), survivors=len(survivors))
        else:
            _write(test_path, backup)
            prev_methods = methods
            prev_error = _error_excerpt(res["log_tail"])
            log("fast", "round_reject", repo=repo, cls=target_class, rnd=rnd,
                ok=res["ok"], rc=res["rc"], err=prev_error[-400:])

    result = {
        "repo": repo, "class": target_class, "test_file": test_file,
        "score_before": round(score_before, 4), "score_after": round(killed_now / base["total"], 4),
        "killed_before": base["killed"], "killed_after": killed_now, "total": base["total"],
        "survived_remaining": len(survivors), "tests_added": tests_added,
        "rounds": accepted_rounds, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    CORPUS.joinpath("results").mkdir(parents=True, exist_ok=True)
    out = CORPUS / "results" / f"{repo.replace('/', '__')}__{target_class}.json"
    _write(str(out), json.dumps(result, indent=2))
    log("slow", "target_done", **{k: result[k] for k in
        ("repo", "class", "score_before", "score_after", "killed_before", "killed_after",
         "survived_remaining", "tests_added")})
    return result
