"""PIT report parsing + runner for improve-java-tests.

run_pit handles single- AND multi-module Maven repos: it locates the module that contains the
target class, builds that module + its reactor deps, injects the pitest(+junit5) plugin into
THAT module pom (disposable clone), and runs PIT scoped to the module. JUnit4 single-module keeps
the bare CLI goal (no pom edit). Standalone:  python3 pit.py <path/to/mutations.xml>
"""
import sys, os, glob, collections
import xml.etree.ElementTree as ET
import sandbox as _sandbox

DETECTED = {"KILLED", "TIMED_OUT", "MEMORY_ERROR"}
SURVIVOR = {"SURVIVED", "NO_COVERAGE"}
ADD_OPENS = ",".join([
    "--add-opens=java.base/java.lang=ALL-UNNAMED",
    "--add-opens=java.base/java.lang.reflect=ALL-UNNAMED",
    "--add-opens=java.base/java.util=ALL-UNNAMED",
    "--add-opens=java.base/java.text=ALL-UNNAMED",
    "--add-opens=java.base/java.io=ALL-UNNAMED",
    "--add-opens=java.base/java.nio=ALL-UNNAMED",
    "--add-opens=java.base/java.time=ALL-UNNAMED",
    "--add-opens=java.base/sun.nio.ch=ALL-UNNAMED",
    "--add-opens=java.desktop/java.awt.font=ALL-UNNAMED",
    "--add-opens=java.management/java.lang.management=ALL-UNNAMED",
])

PIT_VERSION = "1.16.1"
JUNIT5_PLUGIN_VERSION = "1.2.1"
# JUnit 6 unified platform+jupiter versioning (junit-platform-* == jupiter version). PIT 1.16 /
# plugin 1.2.1 bundle JUnit Platform 1.9 and crash on JUnit >= 6 ("OutputDirectoryCreator not
# available; unaligned junit-platform-engine/launcher"). For JUnit >= 6 use a current PIT + plugin
# and pin junit-platform-launcher to the project's own platform version so engine == launcher.
PIT_VERSION_NEW = "1.25.4"
JUNIT5_PLUGIN_VERSION_NEW = "1.2.3"

import re as _re


def _verkey(v):
    parts = _re.findall(r"\d+", v or "")
    return tuple(int(x) for x in parts[:3]) if parts else (0,)


def _jupiter_version(abs_repo):
    """Highest JUnit Jupiter/Platform version the project declares. Handles any junit-related
    <*version> property (incl. plain `junit.version`), the junit BOM, and junit-jupiter deps -
    resolving ${prop} references (e.g. smallrye uses <junit.version>6.x</> + ${junit.version})."""
    cands = []
    for pom in glob.glob(os.path.join(abs_repo, "**", "pom.xml"), recursive=True):
        try:
            txt = open(pom, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        # any junit-ish <*version> property, keyed by full tag name (for ${...} resolution)
        props = {n: v for n, v in
                 _re.findall(r"<(junit[a-zA-Z0-9._-]*version)>\s*([0-9][^<]*?)\s*</", txt, _re.I)}
        cands += list(props.values())
        # junit-jupiter / junit-bom dep versions, resolving a ${prop} reference if present
        for _art, ver in _re.findall(
                r"(junit-jupiter(?:-api|-engine|-params)?|junit-bom)</artifactId>\s*<version>\s*([^<]+?)\s*</version>", txt):
            pm = _re.match(r"\$\{([^}]+)\}", ver)
            if pm:
                ver = props.get(pm.group(1), "")
            if ver[:1].isdigit():
                cands.append(ver)
    return max(cands, key=_verkey) if cands else None


def _platform_version(jver):
    """Platform version for a Jupiter version: JUnit 6 unified (== jver); JUnit 5 -> 1.<minor>.<patch>."""
    n = _re.findall(r"\d+", jver or "")
    if not n:
        return None
    if int(n[0]) >= 6:
        return jver
    if int(n[0]) == 5 and len(n) >= 2:
        return "1." + ".".join(n[1:3])
    return None


def _pit_plugin(abs_repo):
    """The PIT plugin XML to inject, version-matched to the project's JUnit generation."""
    jver = _jupiter_version(abs_repo)
    if _verkey(jver)[0] >= 6:
        plat = _platform_version(jver) or jver
        return (
            "<plugin><groupId>org.pitest</groupId><artifactId>pitest-maven</artifactId>"
            "<version>" + PIT_VERSION_NEW + "</version><dependencies>"
            "<dependency><groupId>org.pitest</groupId><artifactId>pitest-junit5-plugin</artifactId>"
            "<version>" + JUNIT5_PLUGIN_VERSION_NEW + "</version></dependency>"
            "<dependency><groupId>org.junit.platform</groupId>"
            "<artifactId>junit-platform-launcher</artifactId><version>" + plat + "</version></dependency>"
            "</dependencies></plugin>")
    return (
        "<plugin><groupId>org.pitest</groupId><artifactId>pitest-maven</artifactId>"
        "<version>" + PIT_VERSION + "</version><dependencies><dependency>"
        "<groupId>org.pitest</groupId><artifactId>pitest-junit5-plugin</artifactId>"
        "<version>" + JUNIT5_PLUGIN_VERSION + "</version></dependency></dependencies></plugin>")


def parse_report(path):
    root = ET.parse(path).getroot()
    counts = collections.Counter()
    survivors = []
    total = 0
    for m in root:
        st = m.get("status")
        counts[st] += 1
        total += 1
        d = {x.tag: (x.text or "") for x in m}
        if st in SURVIVOR:
            survivors.append({"status": st, "mutatedClass": d.get("mutatedClass", ""),
                              "mutatedMethod": d.get("mutatedMethod", ""),
                              "methodDescription": d.get("methodDescription", ""),
                              "lineNumber": int(d.get("lineNumber", 0) or 0),
                              "mutator": d.get("mutator", "").split(".")[-1],
                              "description": d.get("description", ""),
                              "sourceFile": d.get("sourceFile", ""), "index": d.get("index", "")})
    killed = sum(counts[s] for s in DETECTED)
    score = killed / total if total else 0.0
    survivors.sort(key=lambda s: (s["sourceFile"], s["lineNumber"]))
    return {"total": total, "killed": killed, "survived": total - killed,
            "score": score, "counts": dict(counts), "survivors": survivors}


def _module_of(abs_repo, target_class):
    """Return the repo-relative dir of the module holding target_class ('.' if the root)."""
    rel = target_class.replace(".", "/") + ".java"
    hits = glob.glob(os.path.join(abs_repo, "**", "src", "main", "java", rel), recursive=True)
    if not hits:
        return "."
    marker = os.sep + os.path.join("src", "main", "java") + os.sep
    module_dir = hits[0].split(marker)[0]
    rp = os.path.relpath(module_dir, abs_repo)
    return "." if rp == "." else rp


def _uses_junit5(abs_repo, module="."):
    """Per-MODULE junit5 detection: scan the target module's own test sources + module pom for the
    jupiter import/dep. Repo-wide detection mis-fires on multi-module repos that mix junit4 and
    junit5 (a junit4 module + the junit5 plugin crashes the PIT minion with no tests)."""
    base = abs_repo if module == "." else os.path.join(abs_repo, module)
    for f in glob.glob(os.path.join(base, "src", "test", "**", "*.java"), recursive=True):
        try:
            if "org.junit.jupiter" in open(f, encoding="utf-8", errors="replace").read():
                return True
        except OSError:
            continue
    pom = os.path.join(base, "pom.xml")
    try:
        if "junit-jupiter" in open(pom, encoding="utf-8", errors="replace").read():
            return True
    except OSError:
        pass
    return False


def _inject_pitest(pom_path, abs_repo):
    if not os.path.exists(pom_path):
        return False
    s = open(pom_path, encoding="utf-8", errors="replace").read()
    if "pitest-maven" in s:
        return True
    block = _pit_plugin(abs_repo)
    # avoid a <build> nested in <profiles> (only active under that profile) - target the main build
    ps, pe = s.find("<profiles>"), s.find("</profiles>")
    def _outside(i):
        return not (ps != -1 and pe != -1 and ps < i < pe)
    bi, start = -1, 0
    while True:
        b = s.find("<build>", start)
        if b == -1:
            break
        if _outside(b):
            bi = b
            break
        start = b + 1
    if bi != -1:
        pi, be = s.find("<plugins>", bi), s.find("</build>", bi)
        if pi != -1 and (be == -1 or pi < be):
            s = s[:pi + len("<plugins>")] + block + s[pi + len("<plugins>"):]
        else:
            s = s[:be] + "<plugins>" + block + "</plugins>" + s[be:]
    elif "</project>" in s:
        s = s.replace("</project>", "<build><plugins>" + block + "</plugins></build></project>", 1)
    else:
        return False
    open(pom_path, "w", encoding="utf-8").write(s)
    return True


GRADLE_PIT_PLUGIN = "1.15.0"


def _build_tool(abs_repo):
    if os.path.exists(os.path.join(abs_repo, "pom.xml")):
        return "maven"
    if any(os.path.exists(os.path.join(abs_repo, f)) for f in
           ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")):
        return "gradle"
    return "maven"


def _gradle_module_path(abs_repo, target_class):
    """(':a:b' gradle project path, repo-rel module dir) for the module owning target_class."""
    rel = target_class.replace(".", "/") + ".java"
    hits = glob.glob(os.path.join(abs_repo, "**", "src", "main", "java", rel), recursive=True)
    if not hits:
        return ":", "."
    marker = os.sep + os.path.join("src", "main", "java") + os.sep
    module_dir = os.path.relpath(hits[0].split(marker)[0], abs_repo)
    if module_dir == ".":
        return ":", "."
    return ":" + module_dir.replace(os.sep, ":"), module_dir


def _gradle_uses_junit5(abs_repo, module_dir="."):
    """Per-MODULE junit5 detection: scan the target module's own test sources for the jupiter
    import. Repo-wide detection mis-fires on multi-module repos that mix junit4 and junit5 (a
    junit4 module + the junit5 plugin crashes the PIT minion with no tests)."""
    base = abs_repo if module_dir == "." else os.path.join(abs_repo, module_dir)
    for f in glob.glob(os.path.join(base, "src", "test", "**", "*.java"), recursive=True):
        try:
            if "org.junit.jupiter" in open(f, encoding="utf-8", errors="replace").read():
                return True
        except OSError:
            continue
    return False


def _gradle_jupiter_version(abs_repo):
    """Highest JUnit Jupiter/Platform version a gradle repo declares - scans build.gradle(.kts) and
    version-catalog .toml files for junit-jupiter / junit-bom coordinates (pom-only _jupiter_version
    finds nothing in a gradle repo)."""
    cands = []
    pats = (os.path.join(abs_repo, "**", "*.gradle"), os.path.join(abs_repo, "**", "*.gradle.kts"),
            os.path.join(abs_repo, "**", "*.toml"))
    for pat in pats:
        for f in glob.glob(pat, recursive=True):
            try:
                txt = open(f, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            # [versions] table entries (toml version catalog), keyed by name for version.ref resolution
            vers = {n: v for n, v in _re.findall(r'([A-Za-z0-9._-]+)\s*=\s*"([0-9][0-9.]*)"', txt)}
            # direct coordinate or BOM with inline version: org.junit.jupiter:junit-jupiter:6.0.0
            cands += _re.findall(
                r"junit(?:-jupiter(?:-api|-engine|-params)?|-bom)[:\"',]+\s*v?([0-9][0-9.]*)", txt)
            # version-catalog entry: module = "...junit-jupiter", version(.ref) = "..."
            for ver in _re.findall(
                    r'junit(?:-jupiter(?:-api|-engine|-params)?|-bom)"[^\n}]*?version(?:\.ref)?\s*=\s*"([^"]+)"', txt):
                cands.append(vers.get(ver, ver) if not ver[:1].isdigit() else ver)
    return max(cands, key=_verkey) if cands else None


def _gradle_init_script(target_class, target_tests, j5, proj_path, jdk, jver=None):
    jvm = ('jvmArgs = ["' + '","'.join(ADD_OPENS.split(",")) + '"]') if jdk >= 11 else ""
    # JUnit >= 6: bump PIT + the junit5 plugin and pin junit-platform-launcher to the project's own
    # platform version so engine == launcher (else the minion dies "OutputDirectoryCreator not
    # available"), mirroring the maven _pit_plugin path.
    if j5 and _verkey(jver)[0] >= 6:
        plat = _platform_version(jver) or jver
        j5line = ('pitestVersion = "' + PIT_VERSION_NEW + '"\n'
                  '        junit5PluginVersion = "' + JUNIT5_PLUGIN_VERSION_NEW + '"')
        launcher = ('p.dependencies.add("pitest", '
                    '"org.junit.platform:junit-platform-launcher:' + plat + '")')
    else:
        j5line = ('junit5PluginVersion = "' + JUNIT5_PLUGIN_VERSION + '"') if j5 else ""
        launcher = ""
    tpl = (
        'initscript {\n'
        '  repositories { mavenCentral() }\n'
        '  dependencies { classpath "info.solidsoft.gradle.pitest:gradle-pitest-plugin:__PLUGIN__" }\n'
        '}\n'
        'allprojects { p ->\n'
        '  p.afterEvaluate {\n'
        '    if (p.path == "__PATH__") {\n'
        '      p.pluginManager.apply(info.solidsoft.gradle.pitest.PitestPlugin)\n'
        '      __LAUNCHER__\n'
        '      p.pitest {\n'
        '        targetClasses = ["__CLASS__"]\n'
        '        targetTests = ["__TESTS__"]\n'
        '        outputFormats = ["XML"]\n'
        '        timestampedReports = false\n'
        '        threads = 1\n'
        '        __J5__\n'
        '        __JVM__\n'
        '      }\n'
        '    }\n'
        '  }\n'
        '}\n')
    return (tpl.replace("__PLUGIN__", GRADLE_PIT_PLUGIN).replace("__PATH__", proj_path)
            .replace("__CLASS__", target_class).replace("__TESTS__", target_tests)
            .replace("__LAUNCHER__", launcher)
            .replace("__J5__", j5line).replace("__JVM__", jvm))



def _gradle_wrapper_version(abs_repo):
    for p in glob.glob(os.path.join(abs_repo, "**", "gradle", "wrapper", "gradle-wrapper.properties"), recursive=True):
        try:
            m = _re.search(r"gradle-(\d+)\.(\d+)", open(p, encoding="utf-8", errors="replace").read())
        except OSError:
            continue
        if m:
            return (int(m.group(1)), int(m.group(2)))
    return None


def _gradle_max_lts(abs_repo):
    """Highest LTS JDK the repo's gradle wrapper can run under (the wrapper<->JDK footgun)."""
    w = _gradle_wrapper_version(abs_repo)
    if not w:
        return 25
    maj, mnr = w
    if maj < 5:
        return 8
    if maj < 7:
        return 11
    if maj == 7 and mnr < 3:
        return 11
    if maj == 7:
        return 17
    if maj == 8 and mnr < 5:
        return 17
    if maj == 8:
        return 21
    return 25


def _run_pit_gradle(repo, abs_repo, target_class, target_tests, jdk, timeout):
    proj_path, module_dir = _gradle_module_path(abs_repo, target_class)
    j5 = _gradle_uses_junit5(abs_repo, module_dir)
    jver = _gradle_jupiter_version(abs_repo) if j5 else None
    init = _gradle_init_script(target_class, target_tests, j5, proj_path, jdk, jver)
    with open(os.path.join(abs_repo, "ijt-pitest.init.gradle"), "w") as f:
        f.write(init)
    task = (proj_path + ":pitest") if proj_path != ":" else "pitest"
    gw = "./gradlew" if os.path.exists(os.path.join(abs_repo, "gradlew")) else "gradle"
    rdir = abs_repo if module_dir == "." else os.path.join(abs_repo, module_dir)
    report = os.path.join(rdir, "build", "reports", "pitest", "mutations.xml")
    # stale-report guard (see pit_class): drop any prior report in the root container first
    cmd = ("rm -f " + report + "; chmod +x gradlew 2>/dev/null; " + gw +
           " --no-daemon --console=plain -Dorg.gradle.java.installations.auto-download=false --init-script ijt-pitest.init.gradle " + task)
    rc, out = _sandbox.run(cmd, repo, jdk=jdk, timeout=timeout)
    result = {"rc": rc, "report": report, "ok": False, "junit5": j5,
              "module": module_dir, "log_tail": out}
    if rc == 0 and os.path.exists(report):
        result.update(parse_report(report))
        result["ok"] = True
    return result


_S = "-s /sandbox-settings.xml"
# skip project quality gates hostile to scoped mutation testing (a coverage/style threshold on the
# whole build breaks our build when we run a subset) - jacoco check, checkstyle, enforcer, etc.
_G = ("-Djacoco.skip=true -Dcheckstyle.skip=true -Denforcer.skip=true -Dspotless.check.skip=true "
      "-Dspotless.apply.skip=true -Dspotbugs.skip=true -Dpmd.skip=true -Dforbiddenapis.skip=true "
      "-Danimal.sniffer.skip=true -Dmaven.javadoc.skip=true -Dlicense.skip=true")


def build_module(repo, target_class, jdk=21, timeout=31_536_000):
    """Walk-modules (P9): build + prepare the module owning target_class ONCE, so every file in it
    reuses the reactor install instead of re-walking it per class. Returns {ok, jdk_used, rc,
    log_tail, ctx}; ctx carries the module metadata pit_class needs (build_tool, module dir, pom dir,
    junit5). Maven: `-pl <module> -am -DskipTests install` compiles + installs the module and its
    upstream reactor deps into the shared .m2 ONCE, and the pitest plugin is injected into the module
    pom ONCE. Gradle: a no-op returning ok=True (its build is fused into the pitest task, which must
    be the FIRST gradle invocation - see _run_pit_gradle), so per-file pit_class runs full pitest.
    A rc!=0 here is the walk-modules 'does not build' signal to skip the whole module wholesale."""
    abs_repo = _sandbox.abs_repo(repo)
    if _build_tool(abs_repo) == "gradle":
        return {"ok": True, "jdk_used": jdk, "rc": 0, "log_tail": "",
                "ctx": {"build_tool": "gradle"}}
    module = _module_of(abs_repo, target_class)
    pom_dir = abs_repo if module == "." else os.path.join(abs_repo, module)
    j5 = _uses_junit5(abs_repo, module)
    if j5:
        _inject_pitest(os.path.join(pom_dir, "pom.xml"), abs_repo)
    if module == ".":
        cmd = f"mvn -B {_S} {_G} -DskipTests test-compile"
    else:
        cmd = f"mvn -B {_S} {_G} -pl {module} -am -DskipTests install"
    rc, out = _sandbox.run(cmd, repo, jdk=jdk, timeout=timeout)
    return {"ok": rc == 0, "jdk_used": jdk, "rc": rc, "log_tail": out,
            "ctx": {"build_tool": "maven", "module": module, "pom_dir": pom_dir, "j5": j5}}


def pit_class(repo, ctx, target_class, target_tests, jdk=21, timeout=31_536_000,
              mutators="ALL", pit_version=PIT_VERSION):
    """Walk-files (P10): score ONE class with PIT, reusing the module build from build_module.
    Maven: recompile ONLY this module (`-pl <module> -DskipTests test-compile`, no `-am install`, so
    the agent's freshly-appended tests are picked up while the reactor is NOT re-walked - that is the
    build-once win) then run PIT scoped to the one class. Gradle: the full per-class pitest task (no
    separable build). Same result shape as the old run_pit."""
    abs_repo = _sandbox.abs_repo(repo)
    if ctx.get("build_tool") == "gradle":
        return _run_pit_gradle(repo, abs_repo, target_class, target_tests, jdk, timeout)
    module, pom_dir, j5 = ctx["module"], ctx["pom_dir"], ctx["j5"]
    common_d = (f"-DtargetClasses={target_class} -DtargetTests={target_tests} "
                f"-Dmutators={mutators} -DoutputFormats=XML -DtimestampedReports=false "
                f"-DfullMutationMatrix=false")
    if jdk >= 11:
        common_d += f" -DjvmArgs={ADD_OPENS}"
    if j5:
        goal = "org.pitest:pitest-maven:mutationCoverage " + common_d
    else:
        goal = f"org.pitest:pitest-maven:{pit_version}:mutationCoverage " + common_d
    report = os.path.join(pom_dir, "target", "pit-reports", "mutations.xml")
    # stale-report guard: under the module walk a shared clone keeps target/ across files, so a prior
    # file's report would be re-parsed if THIS PIT run writes none (e.g. a class with no mutations).
    # rm it in the ROOT container (the host user cannot unlink root-owned build output) so a no-report
    # run honestly reads as no-baseline instead of inheriting the previous class's numbers.
    rmr = f"rm -f {report}; "
    if module == ".":
        cmd = rmr + f"mvn -B {_S} {_G} -DskipTests test-compile && mvn -B {_S} {_G} {goal}"
    else:
        cmd = rmr + (f"mvn -B {_S} {_G} -pl {module} -DskipTests test-compile && "
                     f"mvn -B {_S} {_G} -pl {module} {goal}")
    rc, out = _sandbox.run(cmd, repo, jdk=jdk, timeout=timeout)
    result = {"rc": rc, "report": report, "ok": False, "junit5": j5, "module": module,
              "log_tail": out}  # P2: full output, never a tail (the compile-gate scans this for COMPILATION ERROR)
    cm = _re.search(r"Line Coverage[^:]*:\s*(\d+)\s*/\s*(\d+)", out)
    if cm:
        cn, cd = int(cm.group(1)), int(cm.group(2))
        result["line_covered"], result["line_total"] = cn, cd
        result["line_cov"] = round(cn / cd, 4) if cd else 0.0
    if rc == 0 and os.path.exists(report):
        result.update(parse_report(report))
        result["ok"] = True
    elif rc == 0:
        # rc==0 with no report at the (rm-guarded) path means PIT found nothing to mutate: a zero-mutant
        # class (interface / enum / annotation / pure DTO). A valid EMPTY baseline, not a failure - so
        # survived==0 and the caller saturates it, instead of a NO_BASELINE zombie that re-probes forever.
        result.update({"ok": True, "total": 0, "killed": 0, "survived": 0, "score": 0.0, "survivors": []})
    return result


def run_pit(repo, target_class, target_tests, jdk=21, timeout=31_536_000,
            mutators="ALL", pit_version=PIT_VERSION):
    """Back-compat single-class entry (gate probe, killtests, probe_eval, and panel when the module
    is not pre-built): build the owning module then score the class. The module walk instead calls
    build_module ONCE per module + pit_class per file, so the reactor is not re-walked per class."""
    b = build_module(repo, target_class, jdk=jdk, timeout=timeout)
    if not b["ok"]:
        ctx = b["ctx"]
        pom_dir = ctx.get("pom_dir", _sandbox.abs_repo(repo))
        return {"rc": b["rc"], "ok": False, "junit5": ctx.get("j5", False),
                "module": ctx.get("module", "."), "log_tail": b["log_tail"],
                "report": os.path.join(pom_dir, "target", "pit-reports", "mutations.xml")}
    return pit_class(repo, b["ctx"], target_class, target_tests, jdk=b["jdk_used"],
                     timeout=timeout, mutators=mutators, pit_version=pit_version)


if __name__ == "__main__":
    r = parse_report(sys.argv[1])
    print(f"score = {r['killed']}/{r['total']} = {r['score']:.2%}  counts {r['counts']}")
