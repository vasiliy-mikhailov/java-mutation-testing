"""Candidate discovery (P7) — the HIGHEST-STARRED maintained Java repos.

Policy: maintained, top-stars. Rank by stars desc and take the most popular test-bearing Java
repos at HEAD (a merge into a popular repo is a stronger adoption signal). Multi-module is fine
— PIT is routed to the module owning the target class. Rejects junk with ONE cheap recursive-tree
call per repo BEFORE any clone. gh search = 30/min global, so this runs sequentially. Skips repos
already gated (queue.has / is_seen). The size cap is a BUILD-COST guard (P8), not a popularity
filter — it only keeps out pathological mega-monorepos the sandbox cannot build cheaply.
"""
import json, subprocess
import corpus_queue as queue

import re
_JUNK_NAME = re.compile(r"(example|demo|sample|tutorial|playground|workshop|learning|practice|"
                        r"exercise|hello[-_]?world|getting[-_]?started|guide|course|study|"
                        r"test[-_]?project|boilerplate|starter|template|scaffold)", re.I)


def _gh_json(args):
    p = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if p.returncode != 0:
        return []
    try:
        return json.loads(p.stdout or "[]")
    except json.JSONDecodeError:
        return []


def _classify(repo, branch):
    """One recursive-tree call: return 'maven'/'gradle' if a tractable target (root build file +
    a src/test tree + not Android), else None. Truncation is rare under the size cap."""
    if not branch:
        return None
    p = subprocess.run(["gh", "api", f"repos/{repo}/git/trees/{branch}?recursive=1",
                        "-q", ".tree[].path"], capture_output=True, text=True)
    if p.returncode != 0:
        return None
    paths = p.stdout.splitlines()
    if not paths:
        return None
    if any(x.endswith("AndroidManifest.xml") for x in paths):
        return None
    if not any("src/test/java" in x for x in paths):
        return None
    pset = set(paths)
    if "pom.xml" in pset:
        return "maven"
    if "build.gradle" in pset or "build.gradle.kts" in pset:
        return "gradle"
    return None


def discover(n=10, min_stars=1000, max_stars=200000, max_size_kb=150000,
             pushed_after="2025-06-01", max_scan=1000):
    # maintained, top-stars: rank by stars DESC, high floor, size is only a build-cost guard
    rows = _gh_json([
        "search", "repos", "--language=java",
        f"--stars={min_stars}..{max_stars}", f"--size=<{max_size_kb}",
        "--archived=false", "--include-forks=false", f"--updated=>={pushed_after}",
        "--sort=stars", "--order=desc", f"--limit={max_scan}",  # scan deep (1000) so re-gated repos below the top-120 are reached
        "--json", "fullName,stargazersCount,pushedAt,defaultBranch"])
    out = []
    for r in rows:
        repo = r["fullName"]
        if queue.has(repo) or queue.is_seen(repo):
            continue
        if _JUNK_NAME.search(repo.split("/")[1]):
            continue
        tool = _classify(repo, r.get("defaultBranch"))
        if not tool:
            continue
        out.append({"repo": repo, "stars": r.get("stargazersCount"),
                    "pushedAt": r.get("pushedAt"), "build_tool": tool})
        if len(out) >= n:
            break
    return out


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    for c in discover(n):
        print(f"{c['stars']:>6} {c['build_tool']:6}  {c['repo']}")
