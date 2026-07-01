"""Open a PR for a significant kill-test improvement (P2 output).

Significant = killed gain >= MIN_KILLED and score gain >= MIN_GAIN. The PR is append-only test
additions; the body states the mutation score AND line coverage before->after and the mutants killed.

mode="private" (default): persist the strengthened test file(s) to a LOCAL store
  (GENERATED/<name>/) + a meta.json. NO GitHub repo is created (the operator dislikes per-repo
  ijt-* mirror repos). The upstream PR pipeline reads the generated tests from there.
mode="upstream": fork upstream to the authed user and open the PR against upstream.
Commits ONLY the changed test file (never build artifacts). gh provides auth.
"""
import subprocess, time, json, os, shutil, re
import reward_polish
import wartscan
import sandbox
from common import log


def _diff_added(abs_repo, tf, base_sha):
    """The agent's '+' lines for `tf` vs the upstream base (so wart checks never blame upstream code).
    Returns None when no base is known -> wartscan falls back to whole-file."""
    if not base_sha:
        return None
    out = subprocess.run(["git", "-C", abs_repo, "diff", base_sha, "--", tf],
                         capture_output=True, text=True).stdout
    return "\n".join(l[1:] for l in out.splitlines()
                     if l.startswith("+") and not l.startswith("+++"))

# The operator dislikes per-repo ijt-* mirror repos cluttering GitHub. Instead of mirroring a PASS
# into <login>/ijt-<name> and opening a PR there, persist the strengthened test file(s) to a local
# store; the upstream PR pipeline reads the generated tests from here.
# MUST live under current_attempt (the only host path bind-mounted into the panel/improve containers),
# else the persist lands in the container's ephemeral FS and is lost. current_iteration is runtime data.
GENERATED = os.environ.get("IJT_GENERATED",
                           "/home/vmihaylov/improve-java-tests/current_attempt/current_iteration/ijt-generated")


def _slug(repo_full):
    """Owner-qualified store key: a bare repo name collides across owners (two `utils` repos
    overwrite each other), so key by the FULL slug owner__name, sanitized for a path segment."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", repo_full.replace("/", "__"))


def _persist_local(abs_repo, slug, result, test_files, agent=None, base_sha=None):
    """Copy the strengthened test file(s) to GENERATED/<slug>/<repo-relative-path> + a PER-CLASS
    meta-<class>.json. `slug` is the owner-qualified repo key (see _slug). Mechanically polishes each
    file, then WART-GATES it (wartscan over the agent's added lines): a `junk` scratch file is dropped
    outright; any other warts are recorded per-file in the meta with a `clean` flag, so PR-prep ships
    only vetted material. The meta filename is keyed by the target class so a second class never
    overwrites the first and two lanes never write the same meta path. Returns (saved_paths, out_dir)."""
    out = os.path.join(GENERATED, slug)
    saved, file_warts = [], {}
    for tf in test_files:
        src = os.path.join(abs_repo, tf)
        if not os.path.exists(src):
            continue
        fixes = reward_polish.polish(src)  # mechanical 0.9->1.0 fixes (seed Random, drop unused imports)
        if fixes:
            log("medium", "reward_polish", cls=result.get("class"), file=tf, fixes=fixes)
        warts = wartscan.scan(src, _diff_added(abs_repo, tf, base_sha))
        if wartscan.is_junk(warts):
            log("medium", "wart_drop_junk", cls=result.get("class"), file=tf, warts=warts)
            continue  # scratch file (no @Test + main) -> never persist
        dest = os.path.join(out, tf)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(src, dest)
        saved.append(tf)
        if warts:
            file_warts[tf] = warts
            log("medium", "wart_flag", cls=result.get("class"), file=tf, warts=warts)
    os.makedirs(out, exist_ok=True)
    meta = {"upstream_name": slug, "class": result.get("class"), "agent": agent,
            "score_before": result.get("score_before"), "score_after": result.get("score_after"),
            "killed_before": result.get("killed_before"), "killed_after": result.get("killed_after"),
            "test_files": saved, "warts": file_warts, "clean": not file_warts,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    cls_key = re.sub(r"[^A-Za-z0-9._-]", "_", result.get("class") or "unknown")
    json.dump(meta, open(os.path.join(out, "meta-" + cls_key + ".json"), "w"), indent=2)
    return saved, out

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
        "**verify** them: mutating the code left tests still green. This PR adds JUnit test "
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
        f"| test methods added | n/a | {r['tests_added']} |",
        "",
    ]
    if r.get("killed_mutants"):
        lines.append("**Mutants now killed:**")
        lines += [f"- {m}" for m in r["killed_mutants"][:20]] + [""]
    lines += [
        "The additions are append-only (no existing test is modified or weakened) and "
        "all pass against the current code (PIT requires a green baseline).",
    ]
    return "\n".join(lines)


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
           f"Append-only PIT-guided tests; all green.")
    _sh(["git", "-C", abs_repo, "commit", "-m", msg])

    title = (f"Add tests killing {result['killed_after']-result['killed_before']} "
             f"PIT mutants in {cls_simple} ({result['score_before']:.0%}->{result['score_after']:.0%})")

    if mode == "private":
        saved, out = _persist_local(abs_repo, _slug(repo_full), result, [test_file], base_sha=base_sha)
        log("slow", "pr_persisted", mode="local", path=out, cls=result["class"], files=len(saved))
        return {"opened": True, "mode": "local", "path": out, "test_files": saved, "branch": branch}

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
    repo_full = "/".join((base[:-4] if base.endswith(".git") else base).split("/")[-2:])
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
           f"Append-only PIT-guided tests via the improve-java-tests skill.")
    _sh(["git", "-C", abs_repo, "commit", "-m", msg])
    saved, out = _persist_local(abs_repo, _slug(repo_full), result, test_files, agent=agent, base_sha=base_sha)
    log("slow", "panel_pr", mode="local", agent=agent, path=out, cls=result["class"], files=len(saved))
    return {"opened": True, "mode": "local", "path": out, "test_files": saved, "branch": branch}
