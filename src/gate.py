"""Candidate gating (P7): clone HEAD, gate cheapest-first, find mutatable classes.

Gates in order (reject fast): (1) enough @Test + a paired Foo<-FooTest target (file scan, no build),
(2) the target's MODULE compiles (`-pl module -am install`, NOT the whole reactor — so multi-module
top-starred giants build one module instead of hitting compile_fail), (3) that module's tests green,
(4) PIT baselines the target. An admitted record carries candidate classes paired with their test
class (FQCN + repo-relative source/test paths) + the target module, so P5 picks a target with no
rediscovery.
"""
import os, re, subprocess, json
import sandbox
import pit
import jdkdetect
import maint
from common import CLONES, log

TEST_ANNO = re.compile(r"@(Test|ParameterizedTest|RepeatedTest)\b")
PKG = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)


def clone(repo, dest=None):
    dest = dest or str(CLONES / repo.replace("/", "__"))
    if os.path.exists(dest):
        maint.reap_clone(dest)
        if os.path.exists(dest):
            # rm left files behind: an orphaned build container (e.g. one orphaned by a dig/improve
            # restart) is still holding this clone, so rm -rf hits "Directory not empty" on a busy
            # file, gh clone then lands in a non-empty dir and the whole gate fails. Kill any
            # container bind-mounting this path, then clear it again.
            for cid in subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True).stdout.split():
                src = subprocess.run(["docker", "inspect", "-f", "{{range .Mounts}}{{.Source}}:{{end}}", cid],
                                     capture_output=True, text=True).stdout
                if any(s == dest or s.startswith(dest + "/") for s in src.split(":")):
                    subprocess.run(["docker", "rm", "-f", cid], check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            maint.reap_clone(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    subprocess.run(["gh", "repo", "clone", repo, dest, "--", "--depth", "1"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    sha = subprocess.run(["git", "-C", dest, "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    return dest, sha


def build_tool(repo_dir):
    if os.path.exists(os.path.join(repo_dir, "pom.xml")):
        return "maven"
    if any(os.path.exists(os.path.join(repo_dir, f)) for f in ("build.gradle", "build.gradle.kts")):
        return "gradle"
    return None


def _fqcn(java_file, roots):
    txt = open(java_file, encoding="utf-8", errors="replace").read()
    m = PKG.search(txt)
    cls = os.path.basename(java_file)[:-5]
    return (m.group(1) + "." + cls) if m else cls, txt


def count_tests(repo_dir):
    n = 0
    test_files = []
    for base, _, files in os.walk(repo_dir):
        if os.sep + "test" + os.sep not in base + os.sep:
            continue
        for fn in files:
            if fn.endswith("Test.java") or fn.endswith("Tests.java") or "Test" in fn:
                if fn.endswith(".java"):
                    p = os.path.join(base, fn)
                    c = len(TEST_ANNO.findall(open(p, encoding="utf-8", errors="replace").read()))
                    if c:
                        n += c
                        test_files.append((p, c))
    return n, test_files


def _rel_module(rel_path):
    """Module dir from a repo-rel source path: the segment before /src/main/ or /src/test/
    ('.' for a root-level source). Cheap per-candidate module resolution without re-globbing."""
    rp = (rel_path or "").replace(os.sep, "/")
    for marker in ("/src/main/", "/src/test/"):
        i = rp.find(marker)
        if i > 0:
            return rp[:i]
    return "."


def _conventional_test(main_fqcn, module):
    """The <pkg>.<Class>Test FQCN + repo-rel path the agent CREATES for a class that has no test."""
    test_fqcn = main_fqcn + "Test"
    prefix = "" if module == "." else module + "/"
    return test_fqcn, prefix + "src/test/java/" + test_fqcn.replace(".", "/") + ".java"


def candidate_classes(repo_dir):
    """EVERY main class is a candidate (not only test-paired ones): a class with an existing FooTest
    carries it; an untested class carries the conventional <pkg>.<Class>Test the agent will CREATE
    (PIT baselines an untested class all-NO_COVERAGE, so a no-test class is a first-class target).
    Each candidate is stamped with its owning module + a `has_test` flag, ranked test-density-first
    (tested classes lead, untested at n_test 0), uncapped (stoicism: cover all classes)."""
    mains = {}
    for base, _, files in os.walk(repo_dir):
        if os.sep + "main" + os.sep not in base + os.sep:
            continue
        for fn in files:
            if not fn.endswith(".java") or fn in ("package-info.java", "module-info.java"):
                continue  # not real classes; nothing to mutate or test
            fq, _ = _fqcn(os.path.join(base, fn), repo_dir)
            mains[fq] = os.path.relpath(os.path.join(base, fn), repo_dir)
    # map each main FQCN to its BEST existing test (most @Test), same stem-pairing as before but
    # keyed by the MAIN so every main resolves at most one test; unpaired mains stay untested
    tests_for = {}
    for base, _, files in os.walk(repo_dir):
        if os.sep + "test" + os.sep not in base + os.sep:
            continue
        for fn in files:
            if not fn.endswith(".java"):
                continue
            p = os.path.join(base, fn)
            ntest = len(TEST_ANNO.findall(open(p, encoding="utf-8", errors="replace").read()))
            if ntest == 0:
                continue
            test_fq, _ = _fqcn(p, repo_dir)
            test_rel = os.path.relpath(p, repo_dir)
            test_mod = _rel_module(test_rel)
            stem = os.path.basename(fn)[:-5]
            for cand_stem in (stem[:-4] if stem.endswith("Test") else None,
                              stem[:-5] if stem.endswith("Tests") else None,
                              stem[4:] if stem.startswith("Test") and len(stem) > 4 else None):
                if not cand_stem:
                    continue
                matches = [fq for fq in mains if fq.endswith("." + cand_stem) or fq == cand_stem]
                # prefer a main in the SAME module as the test (a cross-module FQCN collision would
                # otherwise false-pair testA to mainB and build the wrong module); fall back to any hit
                hit = next((fq for fq in matches if _rel_module(mains[fq]) == test_mod), None) \
                    or (matches[0] if matches else None)
                if hit:
                    if tests_for.get(hit, (None, None, -1))[2] < ntest:
                        tests_for[hit] = (test_fq, test_rel, ntest)
                    break
    cands = []
    for fq, src_rel in mains.items():
        module = _rel_module(src_rel)
        if fq in tests_for:
            test_fq, test_rel, ntest = tests_for[fq]
            cands.append({"target_class": fq, "src_file": src_rel, "target_tests": test_fq,
                          "test_file": test_rel, "module": module, "n_test": ntest, "has_test": True})
        else:
            test_fq, test_rel = _conventional_test(fq, module)
            cands.append({"target_class": fq, "src_file": src_rel, "target_tests": test_fq,
                          "test_file": test_rel, "module": module, "n_test": 0, "has_test": False})
    cands.sort(key=lambda c: -c["n_test"])
    return cands


def _module_dir(repo_dir, target_class):
    """repo-relative dir of the module owning target_class ('.' for the root), via pit's resolver."""
    try:
        return pit._module_of(repo_dir, target_class)
    except Exception:
        return "."


# build flags: Nexus settings + skip plugins that fail on a shallow clone / keyless env (NOT on test quality)
_F = "-B -q -s /sandbox-settings.xml -Dmaven.buildNumber.skip=true -Dgpg.skip=true"

# jdkdetect reads the source level, but a dep/plugin (jline, spotless, ...) may need a NEWER JDK.
_JDKERR = re.compile(r"bad class file|class file version|more recent version of the Java"
                     r"|UnsupportedClassVersion|release version \d+ not supported|invalid target release", re.I)


def _build_retry(cmd, repo_dir, jdk, timeout):
    """Run cmd at jdk; on a class-file-version failure retry one LTS tier up. Returns (rc, out, jdk_used)."""
    rc, out = sandbox.run(cmd, repo_dir, jdk=jdk, timeout=timeout)
    if rc != 0 and _JDKERR.search(out or ""):
        for j2 in (11, 17, 21, 25):
            if j2 <= jdk:
                continue
            rc, out = sandbox.run(cmd, repo_dir, jdk=j2, timeout=timeout)
            if not (rc != 0 and _JDKERR.search(out or "")):
                jdk = j2
                break
    return rc, out, jdk


def gate(repo, jdk=21, min_tests=20, run_green=True, probe_pit=True, timeout=31_536_000):
    log("medium", "gate_start", repo=repo)
    repo_dir, sha = clone(repo)
    jdk = jdkdetect.detect_jdk(repo_dir)
    tool = build_tool(repo_dir)
    rel = os.path.relpath(repo_dir, str(__import__("common").DATA))  # DATA-relative; abs_repo resolves against DATA
    rec = {"repo": repo, "sha": sha, "build_tool": tool, "repo_dir": rel, "jdk": jdk}
    if tool not in ("maven", "gradle"):
        return {**rec, "admitted": False, "reason": f"unsupported_build_tool:{tool}"}

    # Cheapest-first, BEFORE any build: count tests + find a paired target by file scan. This also
    # tells us which MODULE to build — so a multi-module giant builds one module, not the whole reactor
    # (the old whole-reactor `test-compile` is why top-starred repos all hit compile_fail).
    n_tests, _ = count_tests(repo_dir)
    if n_tests < min_tests:
        return {**rec, "admitted": False, "reason": f"too_few_tests:{n_tests}"}
    cands = candidate_classes(repo_dir)
    if not cands:
        return {**rec, "admitted": False, "reason": "no_paired_target"}
    # every main is now a candidate, so `cands` is non-empty even when NO test pairs to a main
    # (production in another JVM lang, or behaviour-named suites). Reject that BEFORE the costly
    # module build: admission needs at least one tested class for the PIT probe to validate on.
    if not any(c.get("has_test") for c in cands):
        return {**rec, "admitted": False, "reason": "no_tested_target"}

    module = _module_dir(repo_dir, cands[0]["target_class"])
    rec["module"] = module
    if tool == "gradle":
        jdk = min(jdk, pit._gradle_max_lts(repo_dir))  # cap to what the wrapper supports
        rec["jdk"] = jdk
        # No separate build/green for gradle. The PIT probe's `./gradlew :module:pitest` already
        # compiles the module AND runs its tests (PIT refuses to baseline on red tests), so the probe
        # IS the gate. Worse, running `./gradlew testClasses` + `test` first leaves build / config-cache
        # state that POISONS the follow-up pitest (proven: pitest fails right after test on a fresh
        # clone, but baselines cleanly when run as the first gradle invocation). So let the probe gate.
    else:
        scope = "" if module == "." else f"-pl {module} -am"
        # build the target module + its reactor deps (skip their tests); .m2 is a persistent volume so the
        # installed artifacts are visible to the green/PIT steps below
        rc, out, jdk = _build_retry(f"mvn {_F} {scope} -DskipTests install", repo_dir, jdk, timeout)
        rec["jdk"] = jdk  # a class-file-version retry may have bumped the JDK — use it for green + PIT
        if rc != 0:
            log("medium", "gate_reject", repo=repo, reason="compile", module=module, rc=rc)
            return {**rec, "admitted": False, "reason": "compile_fail", "log_tail": out}
        if run_green:
            tscope = "" if module == "." else f"-pl {module}"
            rc, out = sandbox.run(f"mvn {_F} {tscope} test", repo_dir, jdk=jdk, timeout=timeout)
            if rc != 0:
                log("medium", "gate_reject", repo=repo, reason="green", module=module, rc=rc)
                return {**rec, "admitted": False, "reason": "tests_red", "log_tail": out}

    if probe_pit:
        # the top class (most tests) can be awkward for PIT even when others baseline cleanly;
        # try EVERY candidate (stoicism: no top-N) and admit on the first that baselines — this only
        # costs extra probes when the early ones fail, since it breaks on the first success
        probed = None
        for cand in cands:
            if not cand.get("has_test", True):
                continue  # admission validates PIT on a REAL tested class; an untested one baselines
                # trivially at 0 coverage and would not prove the minion survives test execution
            probe = pit.run_pit(rec["repo_dir"], cand["target_class"], cand["target_tests"], jdk=jdk, timeout=31_536_000)
            if not probe.get("ok"):
                # PIT can flake on the first run of a fresh clone (gradle still building the module
                # jars its coverage classpath needs); a retry with the jars in place usually baselines.
                probe = pit.run_pit(rec["repo_dir"], cand["target_class"], cand["target_tests"], jdk=jdk, timeout=31_536_000)
            if probe.get("ok"):
                probed = (cand, probe)
                break
        if not probed:
            log("medium", "gate_reject", repo=repo, reason="pit_no_baseline")
            return {**rec, "admitted": False, "reason": "pit_no_baseline"}
        cand, probe = probed
        rec["probe_class"] = cand["target_class"]
        rec["probe_score"] = round(probe["score"], 4)
    # unique owning modules, ranked by their densest class, so walk-modules (P9) can iterate them
    # highest-value-first (admission itself stays cheapest-first on cands[0]'s module above)
    mod_rank = {}
    for c in cands:
        m = c.get("module", ".")
        mod_rank[m] = max(mod_rank.get(m, 0), c.get("n_test", 0) or 0)
    rec["modules"] = [m for m, _ in sorted(mod_rank.items(), key=lambda kv: -kv[1])]
    rec.update({"admitted": True, "test_count": n_tests, "candidate_classes": cands})
    log("slow", "gate_admit", repo=repo, test_count=n_tests, candidates=len(cands),
        module=module, modules=len(rec["modules"]))
    return rec
