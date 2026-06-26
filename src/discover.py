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
             pushed_after="2025-06-01"):
    """Infinite-depth discovery — NO arbitrary scan cap (P2/stoicism: never cap depth). GitHub
    search hard-caps at 1000 results PER QUERY, so sweep the whole star space in DESCENDING
    windows: search the band [min_stars, hi], take fresh candidates, then lower `hi` past the
    band just covered. A persisted cursor resumes the sweep across rounds; at the floor it wraps
    to the top to re-sweep (catching newly-pushed repos + any cleared from the seen-set). Every
    repo >= the floor is eventually reached, with no fixed depth."""
    cur = queue.CORPUS / "dig_star_cursor"
    try:
        hi = int(cur.read_text().strip())
    except Exception:
        hi = max_stars
    out = []
    while len(out) < n and hi >= min_stars:
        rows = _gh_json([
            "search", "repos", "--language=java",
            f"--stars={min_stars}..{hi}", f"--size=<{max_size_kb}",
            "--archived=false", "--include-forks=false", f"--updated=>={pushed_after}",
            "--sort=stars", "--order=desc", "--limit", "1000",
            "--json", "fullName,stargazersCount,pushedAt,defaultBranch"])
        if not rows:
            hi = min_stars - 1
            break
        lowest = min((r.get("stargazersCount") or hi) for r in rows)
        for r in rows:
            if len(out) >= n:
                break
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
            break  # batch full — keep cursor here; taken ones become seen, re-search this band next round
        hi = lowest - 1  # band exhausted of fresh repos -> descend to the next 1000-repo band
    try:
        cur.write_text(str(max_stars if hi < min_stars else hi))  # wrap to the top at the floor
    except Exception:
        pass
    return out

if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    for c in discover(n):
        print(f"{c['stars']:>6} {c['build_tool']:6}  {c['repo']}")
