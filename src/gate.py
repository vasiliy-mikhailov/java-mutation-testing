"""Candidate gating (P3): clone HEAD, gate cheapest-first, find mutatable classes.

Gates in order (reject fast): (1) compiles, (2) static @Test count >= threshold,
(3) tests green. An admitted record carries candidate classes paired with their test
class (FQCN + repo-relative source/test paths) so P2 can pick a target with no rediscovery.
"""
import os, re, subprocess, json
import sandbox
import pit
import jdkdetect
from common import CLONES, log

TEST_ANNO = re.compile(r"@(Test|ParameterizedTest|RepeatedTest)\b")
PKG = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)


def clone(repo, dest=None):
    dest = dest or str(CLONES / repo.replace("/", "__"))
    if os.path.exists(dest):
        subprocess.run(["rm", "-rf", dest], check=False)
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


def candidate_classes(repo_dir, max_n=12):
    """Pair each test class with its main class (Foo <- FooTest). Returns FQCN + rel paths,
    ranked by test count (proxy for a logic-dense, well-covered target)."""
    mains = {}
    for base, _, files in os.walk(repo_dir):
        if os.sep + "main" + os.sep not in base + os.sep:
            continue
        for fn in files:
            if fn.endswith(".java"):
                fq, _ = _fqcn(os.path.join(base, fn), repo_dir)
                mains[fq] = os.path.relpath(os.path.join(base, fn), repo_dir)
    cands = []
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
            stem = os.path.basename(fn)[:-5]
            for cand_stem in (stem[:-4] if stem.endswith("Test") else None,
                              stem[:-5] if stem.endswith("Tests") else None):
                if not cand_stem:
                    continue
                hit = next((fq for fq in mains if fq.endswith("." + cand_stem) or fq == cand_stem), None)
                if hit:
                    cands.append({"target_class": hit, "src_file": mains[hit],
                                  "target_tests": test_fq,
                                  "test_file": os.path.relpath(p, repo_dir), "n_test": ntest})
                    break
    cands.sort(key=lambda c: -c["n_test"])
    return cands[:max_n]


def gate(repo, jdk=21, min_tests=20, run_green=True, probe_pit=True, timeout=1200):
    log("medium", "gate_start", repo=repo)
    repo_dir, sha = clone(repo)
    jdk = jdkdetect.detect_jdk(repo_dir)
    tool = build_tool(repo_dir)
    rel = os.path.relpath(repo_dir, str(__import__("common").PROJECT))
    rec = {"repo": repo, "sha": sha, "build_tool": tool, "repo_dir": rel, "jdk": jdk}
    if tool != "maven":  # Maven first; Gradle gating lands next
        return {**rec, "admitted": False, "reason": f"unsupported_build_tool:{tool}"}

    rc, out = sandbox.run("mvn -B -q -s /sandbox-settings.xml -DskipTests test-compile",
                          repo_dir, jdk=jdk, timeout=timeout)
    if rc != 0:
        log("medium", "gate_reject", repo=repo, reason="compile", rc=rc)
        return {**rec, "admitted": False, "reason": "compile_fail", "log_tail": out[-800:]}

    n_tests, _ = count_tests(repo_dir)
    if n_tests < min_tests:
        return {**rec, "admitted": False, "reason": f"too_few_tests:{n_tests}"}

    if run_green:
        rc, out = sandbox.run("mvn -B -q -s /sandbox-settings.xml test", repo_dir, jdk=jdk, timeout=timeout)
        if rc != 0:
            log("medium", "gate_reject", repo=repo, reason="green", rc=rc)
            return {**rec, "admitted": False, "reason": "tests_red", "log_tail": out[-800:]}

    cands = candidate_classes(repo_dir)
    if not cands:
        return {**rec, "admitted": False, "reason": "no_paired_target"}
    if probe_pit:
        top = cands[0]
        probe = pit.run_pit(rec["repo_dir"], top["target_class"], top["target_tests"], jdk=jdk, timeout=600)
        if not probe.get("ok"):
            log("medium", "gate_reject", repo=repo, reason="pit_no_baseline")
            return {**rec, "admitted": False, "reason": "pit_no_baseline"}
        rec["probe_class"] = top["target_class"]
        rec["probe_score"] = round(probe["score"], 4)
    rec.update({"admitted": True, "test_count": n_tests, "candidate_classes": cands})
    log("slow", "gate_admit", repo=repo, test_count=n_tests, candidates=len(cands))
    return rec
