# AGENTS.md — java-mutation-testing

What this is. A research project whose **product is a portable Agent Skill bundle** — `detect-java-version` + `improve-mutation-score`
(one `SKILL.md` each, markdown + YAML frontmatter) — that a coding agent loads under **OpenHands (primary),
opencode, or kilocode** and follows to **raise a Java repo's PIT mutation kill-rate**: detect the JDK the
project needs, then find code the tests run but don't verify, add tests that make the suite detect the surviving mutants under
that JDK, keep only green improvements, open a PR. The harness triggers the skill; the host agent does the work
with **standard tools only** (the build's PIT plugin, the matching JDK, `git`, `gh`) — the skill is
instructions, not a program we ship. Everything else in this repo
exists to *forge and score* that skill, not to be shipped.

Access & host. Compute is `ssh mh` (user vmihaylov). Project at `~/java-mutation-testing`. GitHub via the
`gh` CLI. Inference (the panel's fixed model + the strong-rung reference driver) is the OpenAI-compatible
Qwen FP8 endpoint; key in `.env` (`OC_KEY`/`QWEN_API_KEY`), read at runtime, never hardcoded. Maven/Gradle
resolve through the on-host **Nexus** mirror on the `mvn-cache` docker network with warm cache volumes.
Observability is the external frog's eye: write logs under `/var/log/observe/app/jmt/`, read the digest at
`~/observe/{fast,medium,slow}.jsonl`.

Clusters. meta (P1); the skill — the product (P2); harden-on-the-panel (P3) fed by its corpus (P4);
substrate (P5). One problem is in foreground at a time; resume an interrupted one, never restart.

Problem (keep this file compact and outcome-named).
Value: short, outcome-named problems force the agent to re-derive the *how* from its tools each pass; the
file rots when it accretes mechanism the agent could fill itself.
Contract and constraints (operator-only; the agent does not edit this section). A problem is a self-amplifying
attractor: one concern with a single extremum (its Reward) and a single trigger (its Attention mechanism),
written for an intelligent agent searching a fuzzy environment — supply no detail the agent can fill itself.
Five sections each: Value, Contract and constraints, Solution search approach and hints, Reward, Attention
mechanism. Backtick-pinned concretes (paths, command shapes, names) survive trims; aging enumerations
(versions, model ids) get stripped. The one-liner names what the problem produces, not how.
Solution search approach and hints: for each clause ask "why is this here?" — strip mechanism the agent
re-derives, keep what scopes when a rule applies. Re-audit a problem's agent-mutable sections after the
operator touches its Contract.
Reward: cuts that lose words without losing a rule or its scope.
Attention mechanism: an operator edit to a Contract, or a problem visibly bloating, pulls this problem.

Problem (the skill — one portable SKILL.md that makes a host agent raise a repo's PIT mutation score).
Value: a green suite proves nothing about catching regressions; mutation score (killed/total) is the real
strength signal, and line coverage that kills no mutants is code executed but unverified. The deliverable is
a hand manual any agent under any of the three harnesses can follow — so it must lean only on tools all three
share, and must be good enough that the agent, not us, does the killing.
Contract and constraints. The artifact is a single `SKILL.md` (frontmatter `name` + a `description` that
triggers on "improve mutation coverage / improve the mutation score / strengthen Java tests") plus the
`.claude-plugin` manifest — no project-specific scripts. The procedure it encodes: **detect the JDK the project actually needs
(`detect-java-version`) and run every build/test/PIT command under it** — a too-new JDK crashes PIT's forked
minion; detect the **build tool** (`pom.xml` → `pitest-maven`, `build.gradle` → `gradle-pitest-plugin`) AND the
**unit-testing framework**, then take the framework-specific PIT-wiring path: **JUnit 4** → the bare
`mutationCoverage` goal (no plugin); **JUnit 5** → add `pitest-junit5-plugin` to the PIT plugin classpath;
**JUnit 6** (versioning unified, platform == jupiter version) → a current PIT + `pitest-junit5-plugin` plus a
`junit-platform-launcher` pinned to the project's platform version so engine and launcher align (otherwise
the minion dies with `OutputDirectoryCreator not available`); **TestNG** → its own wiring. Inject the PIT
plugin into the project's main `<build>`, never a `<profile>` build. A minion crash on a too-new JDK =
test-instrumentation too old → apply Mockito/ByteBuddy floors or `--add-opens`; run PIT scoped to **one** logic-dense,
already-line-covered class (whole-repo mutation is too expensive); read each survivor (`file:line:mutator`)
from the PIT report; add tests that make the suite detect it by asserting the **correct** behaviour; re-run PIT scoped to
confirm the lift; keep the improvement **only if every originally-passing test still passes and no existing assertion
was weakened** — additions are **append-only**, an existing test is never edited; recognize equivalent mutants
and set them aside; **(framing: the skill IMPROVES the mutation score by strengthening tests to DETECT what the suite misses — not "killing"; PIT still labels a detected mutant KILLED, the tool's term)** then branch, commit the additions, and open a **private-mirror PR** (test-only diff) whose body states the score before→after.
The score must rise from a STRONGER test, never a laxer one. The skill encodes its objective as a **reward — +1 per mutant that no longer survives after the new tests (`survived_before − survived_after`, the reduction in PIT's surviving-mutant count; e.g. 4000 survivors → 10 = reward 3990)** — and drives it with an in-skill **Ralph loop**: it re-runs the scoped-PIT → read-survivors → add-tests → re-score cycle on itself, iterating while the reward is positive and stopping when a full pass removes no survivors (reward 0) or only equivalent mutants remain.
Solution search approach and hints: coverage first — a high-line-coverage, low-mutation-score class is the
richest target. Per survivor, read the mutated operator+line and write the minimal assertion that
distinguishes original from mutant. Don't chase equivalent mutants — recognize the no-observable-effect
pattern and skip. The wall patterns the strong-rung reference (`src/`, Qwen-driven) hits are the raw material
for the manual; promote a pattern into `SKILL.md` only once it recurs. Re-enter when a panel run shows the host
agent misread an instruction.
Reward: **+1 for each mutant that no longer survives after the added tests** — the scalar reward of a
run is `survived_before − survived_after` (the reduction in PIT's surviving-mutant count; e.g. 4000
survivors before, 10 after → reward 3990). Fewer survivors → higher reward. Aggregate signals: the corpus mutation-score lift
(survived% before→after) and the panel PASS rate (a run PASSES iff score rises, all baseline-passing
tests stay green, and the PR opens).
Attention mechanism: a panel run where an agent following `SKILL.md` fails to lift the score, weakens a test,
or breaks the build pulls this problem to fix the manual.

Problem (harden the skill — OpenHands is the learning oracle).
Value: during skill learning one strong reference agent gives the fastest honest signal of whether the
manual works at all — if OpenHands following the skill cannot kill survivors, the skill is the weak link, not
the agent. Running all three agents per edit is slow and conflates "the skill is weak" with "an agent is weak"
— separate concerns (see the agent-parity problem).
Contract and constraints. The learning loop runs **OpenHands only** (the primary/reference agent) against the
frozen eval set, `SKILL.md` installed at `.openhands/skills/`, driving the fixed FP8 model. Score before/after
is measured by `src/pit.py` (never self-reported); **NO_BASELINE** (un-scoreable baseline) is excluded from the
rate, never counted as a skill FAIL. A skill edit is admitted **only if OpenHands' eval pass-rate does not
regress** vs the pre-edit baseline; on regression the edit is reverted from backup. Gate each verdict on the
result file MTIME **advancing**, not its existence; never hand-flip a verdict — only a real re-run updates it.
Solution search approach and hints: read the survivors OpenHands FAILS to kill (NO_GAIN/BROKE_BUILD) — they name
the skill's weak spots; sharpen the manual there. A starved/misconfigured OpenHands fakes a FAIL — confirm
`drop_params=False` + the starvation check before trusting it. Re-enter per skill edit.
Reward: OpenHands' eval pass-rate before vs after an edit; keep iff it does not regress.
Attention mechanism: an OpenHands pass-rate drop after a skill edit pulls this problem.

Problem (agent parity — bring opencode/kilocode up to OpenHands on the same skill).
Value: a skill hardened only against OpenHands may lean on its strength; the honest portability signal is
unrelated off-the-shelf agents succeeding on the *identical* manual. But when OpenHands passes and another
agent fails, the variable is the **agent**, not the skill — so the fix is to tune the lagging agent, not
rewrite the manual.
Contract and constraints. Periodically (NOT every skill edit) run **all three agents** — OpenHands, opencode,
kilocode — on the **current** skill over the eval set, the agent the only variable (same skill, same FP8 model,
same `pit.py` scoring). A target where **OpenHands PASSes but an off-the-shelf agent does NOT** (baseline
scored — NO_BASELINE excluded) is a **fine-tuning work item for THAT agent**: tune the agent's config / harness /
scaffolding (a silent-401 no-op from a missing `apiKey: {env:OC_KEY}`, a starved budget, a wrapper-execution
fumble) until it matches OpenHands — **skill and model held fixed**. The skill is NOT edited here. Escalate to a
skill clarification ONLY if the SAME gap hits multiple agents (then it is a manual ambiguity → the learning
problem).
Solution search approach and hints: diff per-target reference(OpenHands)-vs-agent verdicts; per gap read the
lagging agent's transcript — a tiny no-op log = a config/401 problem; an engaged-but-fumbled run = an execution
limit (tune its scaffolding or accept it). Re-enter when a parity sweep shows OpenHands-pass / agent-fail targets.
Reward: per-agent parity gap (count of OpenHands-pass-but-agent-fail targets) shrinking toward zero.
Attention mechanism: a parity sweep where OpenHands passes a target an off-the-shelf agent fails pulls this problem.

Problem (candidate corpus — latest-version, compilable, test-bearing, mutatable).
Value: the panel needs a stable set of green, test-bearing repos with a non-trivial target; feeding repos that
don't compile, have no tests, or only do CRUD wastes the expensive runs.
Contract and constraints. Discovery is TIGHT — mid-size, maintained, single-module, test-bearing Java repos
at HEAD (exclude Android, giants, and demo/example names; one cheap recursive-tree probe before any clone).
Shallow-clone the **current** commit (not a historical walk). Gate cheapest-first **under the repo's detected
JDK** (`jdkdetect`): (1) compiles; (2) static `@Test` count ≥ threshold; (3) tests green; (4) **PIT can baseline
the top candidate class** — a repo whose minion crashes is pre-filtered OUT, never admitted to waste eval runs.
An admitted record `{repo, sha, build_tool, jdk, test_count, candidate_classes, probe_score}` persists to
`corpus/queue.jsonl`, deduped per repo against a **seen-set** (every repo gated, admitted OR dropped, is never
re-gated). The dig runs **perpetually** (no target). Junk never enters.
Solution search approach and hints: reject fast on no-tests / won't-compile / red before any costly run. Prefer
a logic-dense, well-covered class (`Foo`↔`FooTest`) over trivial getters. Re-enter when the corpus runs low.
Reward: hit-rate (discovered → admitted green + test-bearing + mutatable) and the supply of un-scored targets.
Attention mechanism: the queue file — a fresh admitted target pulls P3; a gate failure drops the repo.

Problem (substrate — docker-bounded, reproducible, observable).
Value: a score must vary only with the skill and the code under it; an unstable toolchain or a full disk
corrupts the comparison, and a blind stalled run hides itself.
Contract and constraints. Project at `~/java-mutation-testing` (the one pinned path). Everything runs in
containers: a **minimal sandbox image per LTS JDK** (`java-{8,11,17,21,25}-mutation-testing-sandbox`, Maven via
Nexus) for build+PIT — **NO scripts or config are baked in; the Nexus `settings.xml`, the `jrun` helper, and
all pipeline code are bind-mounted at runtime, so editing them never triggers a rebuild**. Each repo is routed
to the image matching its **detected JDK**. A `jmt-orch` image (python + docker CLI + `gh`) spawns sandbox
siblings via the mounted socket; the per-harness panel image likewise mounts the socket + `jrun` so the **agent
runs its own build/test/PIT in the proper LTS sandbox** (`jrun <jdk> '<cmd>'`). Bind-mount data at its real host
path so paths resolve across the socket. Bound
every build/PIT run with an **inner** timeout (a host-side kill drops only the docker client). Pin Maven/Gradle
caches. A file a root container wrote is reaped by a root container, never host `rm`. Every process writes logs
under `/var/log/observe/app/jmt/` so frog's eye captures it with no wiring; spot-check raw `/var/log` before any
irreversible call.
Solution search approach and hints: pin a cache when a download repeats; bound every container; reap scratch via
a root container in a `finally`; guard disk before a PIT run. Re-enter on disk pressure, a stalled run, or a
leaked scratch dir.
Reward: zero disk-full crashes, reproducible per-run wall-clock, docker-bounded everything.
Attention mechanism: host metrics + the frog's-eye digest — disk creeping full, or a run gone silent.
