"""P3 panel — score the SKILL.md by having an off-the-shelf agent follow it.

Three legs (OpenHands / opencode / kilocode), the agent the ONLY variable: identical SKILL.md
(installed in the workspace + referenced via AGENTS.md and the prompt), identical model (Qwen
FP8), identical scoring (pit.py, ours — the agent never self-reports). PASS iff killed rose AND
the suite stayed green AND no existing @Test was removed (append-only conserved). A
starved/misconfigured agent fakes a FAIL — see the thinking-budget + panel-config lessons.
"""
import os, re, json, time, shutil, shlex, subprocess, uuid
import pit, sandbox, jdkdetect
from common import PROJECT, env, log, CORPUS

SKILL_SRC = PROJECT / "skills" / "improve-mutation-score" / "SKILL.md"
SKILL_REL = ".openhands/skills/improve-mutation-score/SKILL.md"
TEST_ANNO = re.compile(r"@(Test|ParameterizedTest|RepeatedTest)\b")
AGENTS = ("openhands", "opencode", "kilocode")

PROMPT = (
    "This Maven project's tests pass but do not fully verify class `{cls}`. Raise its PIT "
    "mutation score. READ the skills in `.openhands/skills/` FIRST: `detect-java-version` and "
    "`improve-mutation-score` (`{skill}`).\n"
    "THIS ENVIRONMENT HAS NO LOCAL JDK SWITCHING. Run EVERY maven/test/PIT command inside the "
    "correct JDK container via the `jrun` helper: `jrun <JDK> \'<command>\'` "
    "(e.g. `jrun 17 \'mvn -B -ntp test\'`). FIRST detect the project JDK, then use it for all "
    "commands.\n"
    "Target ONLY `{cls}` (scope PIT with -DtargetClasses={cls} -DtargetTests={tests}). Add new "
    "JUnit test methods that make the suite detect the surviving mutants (raise the mutation score); do NOT modify or weaken existing tests. "
    "Finish when the scoped PIT mutation score is higher and all tests are still green."
)

COMPILE_FIX_PROMPT = (
    "The test class `{tests}` does NOT COMPILE after the previous edits - so the whole change is about "
    "to be discarded. Fix ONLY the compilation: correct or DELETE the offending NEW test method(s). Do "
    "NOT touch production code, do NOT modify or delete pre-existing tests, do NOT weaken anything. Run "
    "commands via the `jrun <JDK> '<command>'` helper; when `mvn -B -ntp test-compile` succeeds and the "
    "suite is green, stop.\n\nThe javac errors:\n{errors}"
)


def _install_skill(abs_repo):
    dst = os.path.join(abs_repo, os.path.dirname(SKILL_REL))
    os.makedirs(dst, exist_ok=True)
    shutil.copyfile(str(SKILL_SRC), os.path.join(abs_repo, SKILL_REL))
    djv = ".openhands/skills/detect-java-version/SKILL.md"
    os.makedirs(os.path.join(abs_repo, os.path.dirname(djv)), exist_ok=True)
    shutil.copyfile(str(PROJECT / "skills" / "detect-java-version" / "SKILL.md"),
                    os.path.join(abs_repo, djv))
    duf = ".openhands/skills/detect-unit-testing-framework/SKILL.md"
    os.makedirs(os.path.join(abs_repo, os.path.dirname(duf)), exist_ok=True)
    shutil.copyfile(str(PROJECT / "skills" / "detect-unit-testing-framework" / "SKILL.md"),
                    os.path.join(abs_repo, duf))
    with open(os.path.join(abs_repo, "AGENTS.md"), "w") as f:
        f.write("# Task skills\n"
                "1. `" + djv + "` — detect the JDK this project needs.\n"
                "2. `" + duf + "` — detect the unit-testing framework + version, wire PIT to it.\n"
                "3. `" + SKILL_REL + "` — raise the PIT mutation score.\n"
                "Read all three and follow them.\n")


def _ntests(path):
    try:
        return len(TEST_ANNO.findall(open(path, encoding="utf-8", errors="replace").read()))
    except OSError:
        return 0


def _spec(agent, abs_repo, prompt, timeout):
    """Return (image, env_list, inner_cmd) for one agent leg."""
    key = env("OC_KEY") or env("QWEN_API_KEY")
    q = shlex.quote(prompt)
    if agent == "openhands":
        slug = os.path.basename(abs_repo.rstrip("/")) + "-" + str(int(time.time()))
        ev_log = str(CORPUS / "dialogs" / (slug + ".jsonl"))
        persist = str(CORPUS / "dialogs" / (slug + "-tree"))
        os.makedirs(os.path.dirname(ev_log), exist_ok=True)
        envs = ["-e", f"OC_BASE={env('QWEN_BASE_URL')}", "-e", f"OC_MODEL={env('QWEN_MODEL')}",
                "-e", f"OC_KEY={key}", "-e", "OH_MAX_ITER=100000",
                "-e", f"OH_EVENT_LOG={ev_log}", "-e", f"OH_PERSIST_DIR={persist}"]
        inner = (f"timeout {timeout} /opt/ohvenv/bin/python "
                 f"{PROJECT}/src/panel_oh_run.py {abs_repo} {q}")
        return "jmt-panel-openhands", envs, inner, ev_log
    # node agents (opencode / kilocode) share one image + config-copy idiom
    envs = ["-e", f"OC_KEY={key}", "-e", f"OPENAI_API_KEY={key}", "-e", f"QWEN_API_KEY={key}"]
    cfg, cli = ("opencode", "opencode") if agent == "opencode" else ("kilo", "kilo")
    inner = (f"export HOME=/root; mkdir -p /root/.config/{cfg}; "
             f"cp /cfg/{cfg if agent=='opencode' else 'kilo'}.json /root/.config/{cfg}/opencode.json; "
             f"cd {abs_repo}; timeout {timeout} {cli} run -m qwen/qwen-3.6-27b-fp8 {q}")
    return "jmt-panel-node", envs, inner, None


def _run_container(agent, abs_repo, prompt, timeout):
    image, envs, inner, ev_log = _spec(agent, abs_repo, prompt, timeout)
    name = f"jmt-panel-{agent}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    args = (["docker", "run", "--rm", "--name", name, "--network", sandbox.NETWORK,
             "--memory", "6g", "--cpus", "4"] + envs +
            ["-v", f"{PROJECT}:{PROJECT}", "-v", "/var/run/docker.sock:/var/run/docker.sock",
             "-v", f"{PROJECT}/docker/jrun:/usr/local/bin/jrun:ro", "-e", f"JMT_HOME={PROJECT}",
             "-w", abs_repo, image, "bash", "-lc", inner])
    # Hang guard = STALL DETECTION, not a wall-clock cap. A productive run is never cut; only a stuck
    # one is reaped. Watch the freshest of {OH dialog log, container stdout} mtime - while the agent
    # emits events the files keep growing. Kill only after STALL secs of zero progress. The inner
    # `timeout {timeout}` plus the absolute +180 line below remain a high backstop.
    STALL = int(env("JMT_STALL_SECS", "2400"))   # 40 min with no new output = stuck
    out_path = f"/tmp/{name}.log"
    killed = None
    start = time.time()
    with open(out_path, "w") as fout:
        proc = subprocess.Popen(args, stdout=fout, stderr=subprocess.STDOUT, text=True)
        while proc.poll() is None:
            now = time.time()
            refs = [start]
            for pth in (ev_log, out_path):
                try:
                    if pth and os.path.exists(pth):
                        refs.append(os.path.getmtime(pth))
                except OSError:
                    pass
            idle = now - max(refs)
            if idle > STALL:
                killed = "stall"
            elif now - start > timeout + 180:
                killed = "hard"
            if killed:
                subprocess.run(["docker", "rm", "-f", name],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    proc.kill()
                except OSError:
                    pass
                log("medium", "panel_hang_guard", agent=agent, why=killed,
                    idle=int(idle), ran=int(now - start))
                break
            time.sleep(15)
        proc.wait()
    try:
        out = open(out_path, errors="replace").read()
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
    return (124 if killed else proc.returncode), out


def run_agent(agent, repo_dir, target_class, target_tests, test_file, src_file,
              jdk=21, timeout=2400, open_pr=True):
    assert agent in AGENTS, agent
    abs_repo = sandbox.abs_repo(repo_dir)
    jdk = jdkdetect.detect_jdk(abs_repo)
    log("fast", "panel_jdk", agent=agent, repo=repo_dir, jdk=jdk)
    test_path = os.path.join(abs_repo, test_file)

    base = pit.run_pit(repo_dir, target_class, target_tests, jdk=jdk, timeout=900)
    if not base["ok"]:
        log("medium", "panel_baseline_fail", agent=agent, repo=repo_dir, cls=target_class)
        import corpus_queue as _q
        _q.mark_no_baseline(target_class)
        return {"agent": agent, "repo": repo_dir, "class": target_class,
                "verdict": "NO_BASELINE", "rc": base["rc"]}

    _install_skill(abs_repo)
    ntests_before = _ntests(test_path)
    try:
        original_test_text = open(test_path, encoding="utf-8", errors="replace").read()
    except OSError:
        original_test_text = None
    log("medium", "panel_start", agent=agent, repo=repo_dir, cls=target_class,
        score_before=round(base["score"], 4), survivors=len(base["survivors"]))

    prompt = PROMPT.format(cls=target_class, tests=target_tests, skill=SKILL_REL)
    rc, out = _run_container(agent, abs_repo, prompt, timeout)
    try:
        tdir = CORPUS / "panel" / "transcripts"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / f"{agent}__{repo_dir.replace('/', '__')}__{target_class}.log").write_text(out)
    except OSError:
        pass

    after = pit.run_pit(repo_dir, target_class, target_tests, jdk=jdk, timeout=900)

    # COMPILE-GATE: a broken APPENDED test must not discard the whole run. If the re-score failed purely
    # because the test class no longer compiles (rc==0, "COMPILATION ERROR"), give the agent ONE focused
    # fix-pass with the exact javac errors; if it still will not compile, restore the test file to baseline
    # so we never emit a compile-broken build (worst case NO_GAIN, never a false BROKE_BUILD losing gains).
    if (not after["ok"]) and rc == 0 and "COMPILATION ERROR" in (after.get("log_tail", "") or ""):
        log("medium", "panel_compile_gate", agent=agent, repo=repo_dir, phase="broken")
        _run_container(agent, abs_repo,
                       COMPILE_FIX_PROMPT.format(tests=target_tests, errors=after["log_tail"][-3000:]),
                       timeout)
        after = pit.run_pit(repo_dir, target_class, target_tests, jdk=jdk, timeout=900)
        if (not after["ok"]) and "COMPILATION ERROR" in (after.get("log_tail", "") or ""):
            if original_test_text is not None:
                try:
                    with open(test_path, "w", encoding="utf-8") as _tf:
                        _tf.write(original_test_text)
                except OSError:
                    pass
            after = pit.run_pit(repo_dir, target_class, target_tests, jdk=jdk, timeout=900)
            log("medium", "panel_compile_gate", agent=agent, repo=repo_dir, phase="reverted")
        else:
            log("medium", "panel_compile_gate", agent=agent, repo=repo_dir, phase="fixed")

    ntests_after = _ntests(test_path)
    conserved = ntests_after >= ntests_before

    if not after["ok"]:
        # classify the failed re-score: agent process crash (rc!=0); PIT minion crash (forked JVM died -
        # environmental/flaky, retry, NOT a skill fail); else a genuinely broken build the agent left.
        _tail = after.get("log_tail", "") or ""
        if rc != 0:
            verdict = "AGENT_ERROR"
        elif "Minion exited abnormally" in _tail or "UNKNOWN_ERROR" in _tail:
            verdict = "MINION_CRASH"
        else:
            verdict = "BROKE_BUILD"
    elif after["killed"] > base["killed"] and conserved:
        verdict = "PASS"
    elif after["killed"] > base["killed"]:
        verdict = "PASS_BUT_NOT_CONSERVED"
    else:
        verdict = "NO_GAIN"

    result = {
        "agent": agent, "repo": repo_dir, "class": target_class, "verdict": verdict,
        "agent_rc": rc,
        "score_before": round(base["score"], 4),
        "score_after": round(after["score"], 4) if after["ok"] else None,
        "killed_before": base["killed"], "killed_after": after["killed"] if after["ok"] else None,
        "total": base["total"], "tests_before": ntests_before, "tests_after": ntests_after,
        "line_cov_before": base.get("line_cov"),
        "line_cov_after": after.get("line_cov") if after["ok"] else None,
        "lines_total": base.get("line_total"),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "log_tail": out[-1500:],
    }
    if open_pr and verdict in ("PASS", "PASS_BUT_NOT_CONSERVED"):
        try:
            import pr
            result["pr"] = pr.open_panel_pr(repo_dir, result, agent)
        except Exception as e:
            log("fast", "panel_pr_fail", agent=agent, err=str(e)[:200])
            result["pr"] = {"opened": False, "error": str(e)[:200]}
    CORPUS.joinpath("panel").mkdir(parents=True, exist_ok=True)
    out_path = CORPUS / "panel" / f"{agent}__{repo_dir.replace('/', '__')}__{target_class}.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    log("slow", "panel_verdict", **{k: result[k] for k in
        ("agent", "repo", "class", "verdict", "score_before", "score_after",
         "killed_before", "killed_after")})
    return result


# back-compat
def run_openhands(*a, **k):
    return run_agent("openhands", *a, **k)


if __name__ == "__main__":
    import sys, corpus_queue as queue
    agent = sys.argv[1]
    repo = sys.argv[2]
    idx = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    rec = next((r for r in queue.load() if r["repo"] == repo and r.get("admitted")), None)
    c = rec["candidate_classes"][idx]
    r = run_agent(agent, rec["repo_dir"], c["target_class"], c["target_tests"],
                  c["test_file"], c["src_file"])
    print(json.dumps({k: v for k, v in r.items() if k != "log_tail"}, indent=2))
