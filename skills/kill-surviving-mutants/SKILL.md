---
name: kill-surviving-mutants
description: Raise a Java (Maven or Gradle) project's PIT mutation score by finding mutants its tests run but fail to kill, then adding tests that kill them — asserting real behaviour, never weakening an existing test. Use when asked to improve mutation coverage or mutation score, kill surviving mutants, strengthen a Java test suite that passes but does not assert much, or turn line coverage into real verification.
---

# Kill surviving PIT mutants in a Java project

A passing test suite proves nothing about whether it would **catch a regression**. PIT mutation
testing makes that measurable: it mutates the code (flips `<` to `<=`, replaces a return with
`null`, removes a void call) and reruns the tests. A mutant the tests still pass on **survived** —
that line is executed but **not verified**. Your job: find the survivors and add tests that **kill**
them, so the mutation score (killed / total) goes up — **without weakening any existing test**.

Work on **one class at a time** (whole-repo mutation is far too slow). Everything below uses only
standard tools: the build's PIT plugin, `git`, and `gh`.

---

## 0. Preconditions

- **Detect and use the right JDK FIRST** — follow the **`detect-java-version`** skill. A project's real build floor can exceed its declared target, and PIT's forked coverage minion crashes (`Minion exited abnormally`) under the wrong JDK. Determine the JDK, then run EVERY command below under it (`JAVA_HOME` / the matching JDK container).
- **Detect the build tool:** a root `pom.xml` → **Maven**; a `build.gradle`/`.kts` (+ `gradlew`) and
  no `pom.xml` → **Gradle**. Use the project's wrapper when present (`./mvnw`, `./gradlew`).
- **Green baseline.** PIT refuses to run if any in-scope test already fails. Confirm the suite is
  green first; tests already red in the baseline (no DB/network/Docker) are **not** your concern —
  scope PIT away from them.
- **Detect the test framework:** JUnit 5 (`org.junit.jupiter`) vs JUnit 4 (`org.junit.Test`) vs
  TestNG — it changes how PIT is wired (§2) and how you write the new tests.
- `git` — commit a baseline first so your additions are an isolated diff.

## 1. Pick one target class

Pick the class where survivors are most likely and most worth killing:
- **Coverage first** — a class with **high line coverage but low mutation score** is the richest
  target: the tests run it but don't assert on it. If you already have a PIT report, read it.
- Otherwise pick a **logic-dense** class (branches, arithmetic, parsing, state) that has an existing
  `FooTest`. Skip trivial getters/DTOs — their mutants are mostly equivalent.

Let `C` = fully-qualified class (e.g. `org.json.CDL`), `T` = its test class or package glob
(e.g. `org.json.junit.CDLTest` or `org.json.junit.*`).

## 2. Measure the baseline — run PIT scoped to that one class

**Maven, JUnit 4** — invoke PIT as a one-off goal, **no `pom.xml` change needed**:

```bash
./mvnw -B -DskipTests test-compile
./mvnw -B org.pitest:pitest-maven:1.15.2:mutationCoverage \
  -DtargetClasses=C -DtargetTests=T \
  -DoutputFormats=XML,HTML -DtimestampedReports=false
```
(Any recent `pitest-maven` is fine; newer is OK.)

**Maven, JUnit 5** — PIT needs the **`pitest-junit5-plugin`** on the plugin's classpath, which a CLI
`-D` cannot add. Add the plugin to `pom.xml` (this enables mutation testing and is fine to keep in
the PR):

```xml
<plugin>
  <groupId>org.pitest</groupId><artifactId>pitest-maven</artifactId><version>1.15.2</version>
  <dependencies>
    <dependency><groupId>org.pitest</groupId>
      <artifactId>pitest-junit5-plugin</artifactId><version>1.2.1</version></dependency>
  </dependencies>
</plugin>
```
then `./mvnw -B test-compile org.pitest:pitest-maven:mutationCoverage -DtargetClasses=C -DtargetTests=T -DoutputFormats=XML`.

**Gradle** — apply `info.solidsoft.pitest` (and `pitest-junit5-plugin` for JUnit 5) in
`build.gradle`, scope it, and run `./gradlew pitest`:

```groovy
plugins { id 'info.solidsoft.pitest' version '1.15.0' }
pitest { targetClasses = ['C']; targetTests = ['T']; outputFormats = ['XML','HTML']; junit5PluginVersion = '1.2.1' }
```

The report lands at `target/pit-reports/mutations.xml` (Maven) or
`build/reports/pitest/mutations.xml` (Gradle).

## 3. Read the survivors

In `mutations.xml`, each `<mutation status="...">` is one mutant. The survivors — your work list —
are `status="SURVIVED"` and `status="NO_COVERAGE"`. For each, read:
`<lineNumber>`, `<mutator>` (e.g. `…ConditionalsBoundaryMutator`), `<mutatedMethod>`,
`<description>` (e.g. "changed conditional boundary"). Count: mutation score = (total − survivors) / total.

## 4. Kill each survivor — append-only

For each survivor, open the source at its line, understand **what the mutation changed**, and add a
**new** test method that **fails on the mutant but passes on the real code**. Map the mutator to the
assertion it demands:

| Mutator | What it changes | The assertion that kills it |
|---|---|---|
| `ConditionalsBoundary` | `<`↔`<=`, `>`↔`>=` | exercise the value **exactly at the boundary**; assert the branch taken there |
| `NegateConditionals` | `==`↔`!=`, etc. | assert behaviour on **both** sides of the condition (true and false case) |
| `Math` | `+`↔`-`, `*`↔`/`, `%` | pick inputs where the two operations **differ**; assert the exact numeric result |
| `Increments` | `i++`↔`i--` | assert the **final counted/accumulated** value, not just that it ran |
| `(Null/Empty/Primitive/Boolean)ReturnVals` | return → `null`/`""`/`0`/`false` | assert the **actual returned value** (`assertNotNull`, `assertEquals(expected, …)`, `assertFalse(x.isEmpty())`) — never just "doesn't throw" |
| `VoidMethodCall` | removes a `foo()` call | assert the **observable side effect** of that call (state change, output, exception) |
| `EmptyObjectReturnVals` | return → empty `""`/`[]`/`0` | assert the returned object's **content** (length, a known element) |

**Hard rules — a kill must come from a *stronger* test, never a laxer one:**
- **Append-only.** Add new `@Test` methods; **never edit, delete, or relax an existing test** — that
  guarantees you can't weaken the suite.
- Your assertions must **pass against the real (unmutated) code**. A test that asserts the *mutant's*
  wrong behaviour will fail the green baseline — that's the build telling you the assertion is wrong.
- Match the existing test class's framework, imports, and style; put new methods in the matching
  `FooTest`.

## 5. Confirm the kill

Re-run the scoped PIT from §2. **Keep the additions only if:** PIT runs clean (all tests green), and
the **killed count went up**. If a new test won't compile or is red, fix it or drop it — never leave
the suite red. Iterate §3–§5 until survivors stop falling.

## 6. Don't chase equivalent mutants

Some survivors are **equivalent** — the mutation produces semantically identical behaviour, so **no**
test can kill them (e.g. a mutated branch with no observable effect, a redundant boundary on an
unreachable value, reordered commutative ops). Recognize the pattern, note it, and **move on**. A
class rarely reaches 100%; stop when the remaining survivors are equivalent or genuinely untestable.

## 7. Open a PR

Branch, commit the **append-only** test additions (plus the PIT build config if you added it for
JUnit 5), and open a PR whose body states the gain:

```bash
git switch -c mutation-tests/<Class>
git add <the test file(s)>            # tests only — not target/ or build artifacts
git commit -m "test(<Class>): kill N surviving mutants (<before>% -> <after>%)"
gh pr create --title "Kill N PIT mutants in <Class> (<before>%->%<after>)" --body "<table>"
```

PR body should report **mutation score before → after**, **mutants killed**, and that the additions
are append-only and green.

---

## Gotchas

- **JUnit 5 + PIT** = needs `pitest-junit5-plugin` (§2); without it PIT reports *0 tests* and every
  mutant as `NO_COVERAGE`. That symptom means the framework wiring is wrong, not that coverage is bad.
- **`NO_COVERAGE` survivors** mean the line isn't executed by the scoped tests at all — you need a
  test that **reaches** the code first, then **asserts** on it.
- **Flaky `TIMED_OUT`** mutants (infinite-loop mutations under load) can flip run-to-run; compare
  before/after in the **same** PIT config and don't treat a lone timeout flip as a real change.
- **Keep PIT scoped** to the one class (`targetClasses`) — unscoped mutation of a whole module can run
  for hours.
