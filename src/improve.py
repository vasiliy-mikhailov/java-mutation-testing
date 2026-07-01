"""Perpetual OpenHands improvement queue (parallel). Runs FOREVER: each cycle draws the ranked
corpus, processes already-PASSED targets FIRST (re-improve / re-score under the current skill), then
every other ranked target, across IMPROVE_WORKERS parallel OpenHands runs. Sleeps + redraws when the
corpus is momentarily dry (dig keeps filling). Opens a private-mirror PR only the FIRST time a class
passes (no duplicate PRs on re-runs). Each target gets its own clone dir + uniquely-named panel
container, so workers never collide. No cap — stop with `docker rm -f ijt-improve`.
  python improve.py [cycle_sleep_secs=600] [pool=500]   (workers via IMPROVE_WORKERS env, default 2)
"""
import sys, os, re, json, glob, subprocess, time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
import draw, gate, panel, pit, corpus_queue
from common import PROJECT, CORPUS, DATA, log

IMPROVE_WORKERS = int(os.environ.get("IMPROVE_WORKERS", "2"))
# Fair scheduling: ONE LANE PER REPO, but a repo processes at most this many files per cycle (across
# all its modules) then yields its lane so other repos get worked and the draw re-interleaves. Under
# 'take every class' a repo can hold thousands of mains; without this a giant repo would pin a lane for
# days. This batches WORK for diversity; it never caps an agent (each file's agent still runs unbounded)
# - the deferred files just re-enter the next draw.
REPO_FILE_BUDGET = int(os.environ.get("REPO_FILE_BUDGET", "40"))


def _verdicts():
    """(passed_classes, captured_classes). 'captured' = already persisted to the LOCAL store
    (pr.GENERATED/<slug>/meta-<class>.json — one meta PER CLASS), since PASSes now persist locally,
    not to ijt-* mirror PRs. Reading the panel JSON's old pr.url would keep classes captured by
    now-DELETED mirrors stuck in has_pr forever, so they would never re-persist their fresh output."""
    import pr
    passed, has_pr = set(), set()
    for f in glob.glob(str(CORPUS / "panel" / "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        c = d.get("class")
        if c and d.get("verdict") in ("PASS", "PASS_BUT_NOT_CONSERVED"):
            passed.add(c)
    for mf in glob.glob(os.path.join(pr.GENERATED, "*", "meta-*.json")):
        try:
            m = json.load(open(mf))
        except Exception:
            continue
        if m.get("class"):
            has_pr.add(m["class"])
    return passed, has_pr


def _score_line(repo, target_class, r):
    url = (r.get("pr") or {}).get("url")
    sb, sa = r.get("score_before"), r.get("score_after")
    kb, ka = r.get("killed_before"), r.get("killed_after")
    cb, ca = r.get("line_cov_before"), r.get("line_cov_after")
    cov = "" if (cb is None or ca is None) else "  cov %.0f%%->%.0f%%" % (cb * 100, ca * 100)
    gain = "" if sa is None else "  mut %.3f->%.3f reward=+%d%s" % (sb, sa, (ka - kb), cov)
    print("%-44s %-22s %-14s%s  PR=%s" % (repo, target_class.split(".")[-1], r["verdict"], gain, url),
          flush=True)


def _run_one(t, open_pr):
    # unique dest per (repo, class) so parallel workers never share a clone dir. Use the FULL
    # FQCN (sanitized), not the simple name, so two same-simple-name classes never share a dest.
    dest = "clones/improve_" + t["repo"].replace("/", "_") + "__" + t["target_class"].replace(".", "_")
    try:
        gate.clone(t["repo"], dest=str(DATA / dest))
        r = panel.run_agent("openhands", dest, t["target_class"], t["target_tests"],
                            t["test_file"], t["src_file"], jdk=t.get("jdk"),
                            timeout=31_536_000, open_pr=open_pr, has_test=t.get("has_test", True))
        _score_line(t["repo"], t["target_class"], r)
        return r["verdict"]
    except Exception as e:
        print("%s ERROR %s" % (t["repo"], str(e)[:140]), flush=True)
        return "ERROR"
    finally:
        subprocess.run(["rm", "-rf", str(DATA / dest)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _group_repos(queue):
    """Group the flat ranked file-targets into repo -> OrderedDict(module -> [file-targets]),
    preserving draw ranking: ONE LANE PER REPO (P8); within a repo, modules in value order and files
    in draw order. Draw order is NOT repo-contiguous, so grouping here is what makes the REPO the unit
    of a lane and lets it walk module by module, file by file."""
    repos = OrderedDict()
    for t in queue:
        repos.setdefault(t["repo"], OrderedDict()).setdefault(t.get("module") or ".", []).append(t)
    return repos


def _reset_worktree(real, ref):
    """Restore a module clone to its post-build baseline before the next file: drop the prior agent's
    appended tests / touched production, but KEEP the compiled build output so the module stays built.
    `-e **/target -e **/build` excludes the Maven/Gradle build dirs from the clean REGARDLESS of the
    repo's .gitignore (many Java repos never commit target/ but also never gitignore it), so a shared
    clone's build survives across files; only the agent's untracked source additions are removed."""
    subprocess.run(["git", "-C", real, "reset", "--hard", ref, "-q"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "-C", real, "clean", "-fdq", "-e", "**/target", "-e", "**/build"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _commit_baseline(real):
    """Commit the current clone state (this module's injected pitest pom + the installed skill files) as
    the per-file reset point; return its ref. Called once per module build inside the repo's shared clone
    (`add -f` the untracked skills each on its own so a missing one can't atomically abort, `add -u` the
    tracked pom edit; the gitignored build output stays out of git and survives the reset)."""
    for _ps in ("AGENTS.md", ".openhands"):
        subprocess.run(["git", "-C", real, "add", "-f", _ps],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "-C", real, "add", "-u"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "-C", real, "-c", "user.email=ijt@local", "-c", "user.name=ijt",
                    "commit", "-q", "-m", "ijt-baseline"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return subprocess.run(["git", "-C", real, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def _run_repo(repo, jdk, modules, has_pr):
    """One lane per repo (P8): clone the repo ONCE, then walk it module by module, file by file. For
    each maven module: build it once, then improve its files against that one build (a git-reset
    between files keeps them isolated over the single warm build). Gradle can't build-once (its pitest
    task must be the FIRST gradle invocation or config-cache poisons it), so gradle files use a fresh
    clone each. A per-repo file budget yields the lane so a giant repo re-competes rather than pinning
    a worker. `modules` is an OrderedDict module -> [file-targets] in value/draw order."""
    todo_repo = [t for files in modules.values() for t in files if t["target_class"] not in has_pr]
    if not todo_repo:
        return "CAPTURED"
    tool = todo_repo[0].get("build_tool") or "maven"
    if tool == "gradle":
        for t in todo_repo[:REPO_FILE_BUDGET]:
            _run_one(t, t["target_class"] not in has_pr)
        return "GRADLE"
    # maven: drop cached-unbuildable modules; skip the repo (no clone) if none are left to build
    live = OrderedDict((m, f) for m, f in modules.items()
                       if not corpus_queue.is_no_build(repo + "#" + m))
    if not any(t["target_class"] not in has_pr for f in live.values() for t in f):
        return "ALL_NO_BUILD"
    dest = "clones/improve_" + repo.replace("/", "_")
    real = str(DATA / dest)
    spent = 0
    try:
        gate.clone(repo, dest=real)
        panel._install_skill(real)   # once per repo clone; committed into each module's reset baseline
        for module, files in live.items():
            if spent >= REPO_FILE_BUDGET:
                break
            todo = [t for t in files if t["target_class"] not in has_pr]
            if not todo:
                continue
            built = pit.build_module(dest, todo[0]["target_class"], jdk=jdk or 21)
            if not built["ok"]:
                # PERMANENTLY blacklist the module only on a deterministic compile failure (its code does
                # not compile at HEAD); a transient resolver/network/OOM flake retries next cycle, so one
                # bad infra night never shrinks the admitted corpus.
                permanent = "COMPILATION ERROR" in (built.get("log_tail") or "")
                if permanent:
                    corpus_queue.mark_no_build(repo + "#" + module)
                log("slow", "module_no_build", repo=repo, module=module, rc=built["rc"],
                    files=len(todo), permanent=permanent)
                print("%-40s %-24s NO_BUILD%s" % (repo, module, "" if permanent else " (flake, will retry)"), flush=True)
                continue
            jdk_used, ctx = built["jdk_used"], built["ctx"]
            base_ref = _commit_baseline(real)   # module build + skills = the per-file reset point
            for t in todo:
                if spent >= REPO_FILE_BUDGET:
                    break
                if base_ref:
                    _reset_worktree(real, base_ref)
                try:
                    r = panel.run_agent("openhands", dest, t["target_class"], t["target_tests"],
                                        t["test_file"], t["src_file"], jdk=jdk_used,
                                        timeout=31_536_000, open_pr=(t["target_class"] not in has_pr),
                                        module_ctx=ctx, module_built=True, has_test=t.get("has_test", True))
                    _score_line(repo, t["target_class"], r)
                    spent += 1
                except Exception as e:
                    print("%s FILE-ERROR %s %s" % (repo, t["target_class"].split(".")[-1], str(e)[:120]), flush=True)
        if spent >= REPO_FILE_BUDGET:
            log("slow", "repo_file_budget", repo=repo, spent=spent)
        return "DONE"
    except Exception as e:
        print("%s REPO-ERROR %s" % (repo, str(e)[:140]), flush=True)
        return "ERROR"
    finally:
        subprocess.run(["rm", "-rf", real], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _reap_orphan_containers():
    """On a fresh start every ijt-panel-openhands container is an orphan from a prior improve.py
    instance that died (a restart) -- its stall-detector died with it, so it would hang forever.
    Remove them all so restarts are self-cleaning (the fresh loop has spawned none of its own yet)."""
    try:
        cids = subprocess.run(["docker", "ps", "-q", "--filter", "name=ijt-panel-openhands"],
                              capture_output=True, text=True, timeout=60).stdout.split()
        for cid in cids:
            subprocess.run(["docker", "rm", "-f", cid],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        if cids:
            log("slow", "reaped_orphan_panels", n=len(cids))
    except Exception as e:
        log("medium", "reap_orphans_err", err=str(e)[:120])


def main(cycle_sleep=600, pool=500):
    log("slow", "improve_start", mode="perpetual_repo_walk", workers=IMPROVE_WORKERS, pool=pool)
    _reap_orphan_containers()
    cycle = 0
    with ThreadPoolExecutor(max_workers=IMPROVE_WORKERS) as ex:
        while True:
            cycle += 1
            cands = draw.draw(10**9)  # draw ALL admitted candidates — cover every class, not a top-N slice
            if not cands:
                log("slow", "improve_dry_sleep", cycle=cycle)
                time.sleep(cycle_sleep)
                continue
            passed, has_pr = _verdicts()
            # spend lanes on NEW classes, not churn. Skip classes already CAPTURED to the local store
            # (shipped/stored — re-running them just re-persists the same result and wastes lanes), and
            # run never-passed (truly new) classes FIRST so fresh candidates actually surface.
            uncaptured = [t for t in cands if t["target_class"] not in has_pr]
            fresh = [t for t in uncaptured if t["target_class"] not in passed]
            retry = [t for t in uncaptured if t["target_class"] in passed]
            queue = fresh + retry
            # ONE LANE PER REPO (P8): a lane owns a whole repo and walks it module by module, file by
            # file against a single clone; IMPROVE_WORKERS repos run in parallel. Draw order ranks repos
            # by their best class, so high-value repos are worked first.
            groups = _group_repos(queue)
            futs = []
            for repo, mods in groups.items():
                jdk = next(iter(mods.values()))[0].get("jdk")
                futs.append(ex.submit(_run_repo, repo, jdk, mods, has_pr))
            log("slow", "improve_cycle", cycle=cycle, repos=len(futs), files=len(queue),
                fresh=len(fresh), retry=len(retry), workers=IMPROVE_WORKERS)
            for _ in as_completed(futs):
                pass
            log("slow", "improve_cycle_done", cycle=cycle)


if __name__ == "__main__":
    cs = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    pl = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    main(cs, pl)
