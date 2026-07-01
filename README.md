# improve-java-tests

The research pipeline that **forges and scores** the portable
[improve-java-tests-skill](https://github.com/vasiliy-mikhailov/improve-java-tests-skill) —
Agent Skills that raise a Java project's PIT mutation kill-rate. This repo is the *engine*, not the
product: it discovers candidate repos, runs them through off-the-shelf agents (OpenHands, opencode,
kilocode) following the skill, scores the result with PIT, and opens improvement PRs.

**The governing spec is [AGENTS.md](AGENTS.md)** — five self-amplifying "problems": the skill, the
panel, the corpus, the substrate, and a meta-problem keeping the file lean. Everything is
**docker-bounded**: a minimal sandbox image per LTS JDK (8/11/17/21/25), agents that detect a repo's
JDK and run under it, and a perpetual "dig" that pre-filters PIT-scoreable repos into the corpus.

## Layout
- `src/` — discover/gate/dig (corpus), pit/sandbox/jdkdetect (scoring), panel/improve/parity (eval), pr (PRs).
- `docker/` — per-LTS sandbox + orchestrator + panel Dockerfiles, the `jrun` JDK-router, Nexus settings.
- `skills/` — the skill bundle under test (also published standalone).

Runs on a single host; inference, the Maven/Gradle Nexus mirror, and observability are external.
Secrets live in a gitignored `.env`.

## License
MIT
