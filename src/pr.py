"""Open a PR for a significant kill-test improvement (P2 output).

Significant = killed gain >= MIN_KILLED and score gain >= MIN_GAIN. The PR is append-only test
additions; the body states the mutation score AND line coverage before->after and the mutants killed.

mode="private" (default): mirror the repo into a PRIVATE repo <login>/jmt-<name>, push the
  unmodified base as `main` and the improved branch, open the PR WITHIN that private repo so the
  operator can review it safely. Nothing reaches upstream.
mode="upstream": fork upstream to the authed user and open the PR against upstream.
Commits ONLY the changed test file (never build artifacts). gh provides auth.
"""
import subprocess, time, json
import sandbox
from common import log

MIN_KILLED = 2
MIN_GAIN = 0.02


def _sh(args, cwd=None, check=True):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} -> rc{p.returncode}: {p.stderr.strip()[:300]}")
    return p.stdout.strip()


def is_significant(r, min_killed=MIN_KILLED, min_gain=MIN_GAIN):
    return bool(r and "score_after" in r
                and r["killed_after"] - r["killed_before"] >= min_killed
                and r["score_after"] - r["score_before"] >= min_gain)


def _body(r):
    cls = r["class"]
    killed = r["killed_after"] - r["killed_before"]
    lines = [
        f"## Strengthen tests: kill {killed} surviving mutants in `{cls}`",
        "",
        "PIT mutation testing showed the existing suite **executes** these lines but does not "
        "**verify** them — mutating the code left tests still green. This PR adds JUnit test "
        "methods that kill those surviving mutants by asserting the correct behaviour.",
        "",
        "| metric | before | after |",
        "|---|---|---|",
        f"| mutation score | {r['score_before']:.1%} | **{r['score_after']:.1%}** |",
        f"| mutants killed | {r['killed_before']}/{r['total']} | **{r['killed_after']}/{r['total']}** |",
        f"| survivors | {r['total']-r['killed_before']} | {r['total']-r['killed_after']} |",
        (f"| line coverage | {r['line_cov_before']:.1%} | **{r['line_cov_after']:.1%}** |"
         if r.get('line_cov_before') is not None and r.get('line_cov_after') is not None
         else "| line coverage | n/a | n/a |"),
        f"| test methods added | — | {r['tests_added']} |",
        "",
    ]
    if r.get("killed_mutants"):
        lines.append("**Mutants now killed:**")
        lines += [f"- {m}" for m in r["killed_mutants"][:20]] + [""]
    lines += [
        "The additions are **append-only** — no existing test was modified or weakened — and "
        "all pass against the current code (PIT requires a green baseline).",
        "",
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
    ]
    return "\n".join(lines)


def _ensure_private_mirror(login, name):
    mirror = f"{login}/jmt-{name}"
    if subprocess.run(["gh", "repo", "view", mirror],
                      capture_output=True, text=True).returncode != 0:
        _sh(["gh", "repo", "create", mirror, "--private",
             "--description", f"Private mutation-testing mirror of {name}"])
        log("medium", "mirror_created", mirror=mirror)
    return mirror


def open_for_result(repo_full, repo_dir, result, mode="private",
                    min_killed=MIN_KILLED, min_gain=MIN_GAIN):
    if not is_significant(result, min_killed, min_gain):
        log("medium", "pr_skip_insignificant", repo=repo_full,
            killed_gain=result.get("killed_after", 0) - result.get("killed_before", 0))
        return {"opened": False, "reason": "not_significant"}

    abs_repo = sandbox.abs_repo(repo_dir)
    test_file = result["test_file"]
    _sh(["git", "config", "--global", "--add", "safe.directory", "*"])
    if not _sh(["git", "-C", abs_repo, "status", "--porcelain", test_file]):
        return {"opened": False, "reason": "no_diff"}

    login = _sh(["gh", "api", "user", "-q", ".login"])
    name = repo_full.split("/")[1]
    cls_simple = result["class"].split(".")[-1]
    branch = f"mutation-tests/{cls_simple}-{time.strftime('%Y%m%d-%H%M%S')}"
    base_sha = _sh(["git", "-C", abs_repo, "rev-parse", "HEAD"])  # unmodified base, before our commit

    _sh(["git", "-C", abs_repo, "config", "user.name", login])
    _sh(["git", "-C", abs_repo, "config", "user.email", f"{login}@users.noreply.github.com"])
    _sh(["gh", "auth", "setup-git"])
    _sh(["git", "-C", abs_repo, "checkout", "-b", branch])
    _sh(["git", "-C", abs_repo, "add", test_file])
    msg = (f"test({cls_simple}): kill {result['killed_after']-result['killed_before']} "
           f"surviving mutants ({result['score_before']:.1%} -> {result['score_after']:.1%})\n\n"
           f"Append-only PIT-guided tests; all green.\n\n"
           f"Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>")
    _sh(["git", "-C", abs_repo, "commit", "-m", msg])

    title = (f"Add tests killing {result['killed_after']-result['killed_before']} "
             f"PIT mutants in {cls_simple} ({result['score_before']:.0%}->{result['score_after']:.0%})")

    if mode == "private":
        mirror = _ensure_private_mirror(login, name)
        subprocess.run(["git", "-C", abs_repo, "fetch", "--unshallow"], capture_output=True, text=True)  # need full history to push to a fresh repo
        url_remote = f"https://github.com/{mirror}.git"
        subprocess.run(["git", "-C", abs_repo, "remote", "remove", "mine"],
                       capture_output=True, text=True)
        _sh(["git", "-C", abs_repo, "remote", "add", "mine", url_remote])
        _sh(["git", "-C", abs_repo, "push", "mine", f"{base_sha}:refs/heads/main", "--force"])
        _sh(["git", "-C", abs_repo, "push", "mine", branch, "--force"])
        url = _sh(["gh", "pr", "create", "--repo", mirror, "--base", "main",
                   "--head", branch, "--title", title, "--body", _body(result)])
        log("slow", "pr_opened", mode="private", mirror=mirror, cls=result["class"], url=url)
        return {"opened": True, "mode": "private", "mirror": mirror, "url": url, "branch": branch}

    # mode == "upstream"
    subprocess.run(["gh", "repo", "fork", repo_full, "--clone=false"], capture_output=True, text=True)
    subprocess.run(["git", "-C", abs_repo, "remote", "remove", "fork"], capture_output=True, text=True)
    _sh(["git", "-C", abs_repo, "remote", "add", "fork", f"https://github.com/{login}/{name}.git"])
    _sh(["git", "-C", abs_repo, "push", "-u", "fork", branch, "--force"])
    base = _sh(["gh", "repo", "view", repo_full, "--json", "defaultBranchRef",
                "-q", ".defaultBranchRef.name"])
    url = _sh(["gh", "pr", "create", "--repo", repo_full, "--base", base,
               "--head", f"{login}:{branch}", "--title", title, "--body", _body(result)])
    log("slow", "pr_opened", mode="upstream", repo=repo_full, cls=result["class"], url=url)
    return {"opened": True, "mode": "upstream", "url": url, "branch": branch, "base": base}


if __name__ == "__main__":
    import sys
    repo_full, repo_dir, result_path = sys.argv[1], sys.argv[2], sys.argv[3]
    mode = "upstream" if "--upstream" in sys.argv else "private"
    r = json.load(open(result_path))
    if "--dry-run" in sys.argv:
        print("SIGNIFICANT:", is_significant(r), "| mode:", mode)
        print(_body(r))
    else:
        print(json.dumps(open_for_result(repo_full, repo_dir, r, mode=mode), indent=2))


def open_panel_pr(repo_dir, result, agent, min_killed=1):
    """Open a PRIVATE-mirror PR with the test changes a PANEL agent made, so improvements are
    reviewable. Stages ONLY src/test changes (excludes the pit-injected pom, .openhands/, AGENTS.md).
    Significant = killed rose by >= min_killed. Derives the upstream repo from the clone's origin."""
    kb, ka = result.get("killed_before"), result.get("killed_after")
    if ka is None or kb is None or (ka - kb) < min_killed:
        return {"opened": False, "reason": "not_significant"}
    abs_repo = sandbox.abs_repo(repo_dir)
    _sh(["git", "config", "--global", "--add", "safe.directory", "*"])
    origin = _sh(["git", "-C", abs_repo, "remote", "get-url", "origin"])
    base = origin.rstrip("/")
    name = (base[:-4] if base.endswith(".git") else base).split("/")[-1]
    changed = subprocess.run(["git", "-C", abs_repo, "ls-files", "-mo", "--exclude-standard"],
                             capture_output=True, text=True).stdout.splitlines()
    test_files = [f for f in changed if "src/test/" in f]
    if not test_files:
        return {"opened": False, "reason": "no_test_diff"}
    login = _sh(["gh", "api", "user", "-q", ".login"])
    cls = result["class"].split(".")[-1]
    delta = ka - kb
    branch = f"mutation-tests/{agent}-{cls}-{time.strftime('%Y%m%d-%H%M%S')}"
    base_sha = _sh(["git", "-C", abs_repo, "rev-parse", "HEAD"])
    _sh(["git", "-C", abs_repo, "config", "user.name", login])
    _sh(["git", "-C", abs_repo, "config", "user.email", f"{login}@users.noreply.github.com"])
    _sh(["gh", "auth", "setup-git"])
    _sh(["git", "-C", abs_repo, "checkout", "-b", branch])
    for f in test_files:
        _sh(["git", "-C", abs_repo, "add", f])
    msg = (f"test({cls}): {agent} killed {delta} PIT mutants "
           f"({result['score_before']:.1%} -> {result['score_after']:.1%})\n\n"
           f"Append-only PIT-guided tests via the improve-mutation-score skill.\n\n"
           f"Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>")
    _sh(["git", "-C", abs_repo, "commit", "-m", msg])
    mirror = _ensure_private_mirror(login, name)
    subprocess.run(["git", "-C", abs_repo, "fetch", "--unshallow"], capture_output=True, text=True)
    subprocess.run(["git", "-C", abs_repo, "remote", "remove", "mine"], capture_output=True, text=True)
    _sh(["git", "-C", abs_repo, "remote", "add", "mine", f"https://github.com/{mirror}.git"])
    _sh(["git", "-C", abs_repo, "push", "mine", f"{base_sha}:refs/heads/main", "--force"])
    _sh(["git", "-C", abs_repo, "push", "mine", branch, "--force"])
    body = (f"## {agent}: killed {delta} surviving mutants in `{result['class']}`\n\n"
            f"| metric | before | after |\n|---|---|---|\n"
            f"| mutation score | {result['score_before']:.1%} | **{result['score_after']:.1%}** |\n"
            f"| mutants killed | {kb}/{result['total']} | **{ka}/{result['total']}** |\n"
            f"| test methods | {result.get('tests_before')} | {result.get('tests_after')} |\n\n"
            f"Append-only PIT-guided tests, agent **{agent}**, verdict `{result['verdict']}`. "
            f"Diff is test-only (scoring scaffolding excluded).\n\n"
            f"\U0001F916 Generated with [Claude Code](https://claude.com/claude-code)")
    title = (f"[{agent}] kill {delta} PIT mutants in {cls} "
             f"({result['score_before']:.0%}->{result['score_after']:.0%})")
    url = _sh(["gh", "pr", "create", "--repo", mirror, "--base", "main", "--head", branch,
               "--title", title, "--body", body])
    log("slow", "panel_pr", agent=agent, mirror=mirror, cls=result["class"], url=url)
    return {"opened": True, "mirror": mirror, "url": url, "branch": branch}
