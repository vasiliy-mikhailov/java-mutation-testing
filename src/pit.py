"""PIT report parsing + runner for java-mutation-testing.

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


def _uses_junit5(abs_repo):
    for pom in glob.glob(os.path.join(abs_repo, "**", "pom.xml"), recursive=True):
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


def run_pit(repo, target_class, target_tests, jdk=21, timeout=900,
            mutators="ALL", pit_version=PIT_VERSION):
    abs_repo = _sandbox.abs_repo(repo)
    module = _module_of(abs_repo, target_class)
    pom_dir = abs_repo if module == "." else os.path.join(abs_repo, module)
    j5 = _uses_junit5(abs_repo)
    common_d = (f"-DtargetClasses={target_class} -DtargetTests={target_tests} "
                f"-Dmutators={mutators} -DoutputFormats=XML -DtimestampedReports=false "
                f"-DfullMutationMatrix=false")
    if jdk >= 11:
        common_d += f" -DjvmArgs={ADD_OPENS}"
    if j5:
        _inject_pitest(os.path.join(pom_dir, "pom.xml"), abs_repo)
        goal = "org.pitest:pitest-maven:mutationCoverage " + common_d
    else:
        goal = f"org.pitest:pitest-maven:{pit_version}:mutationCoverage " + common_d
    S = "-s /sandbox-settings.xml"
    # skip project quality gates hostile to scoped mutation testing (a coverage/style threshold on the
    # whole build breaks our build when we run a subset) - jacoco check, checkstyle, enforcer, etc.
    G = ("-Djacoco.skip=true -Dcheckstyle.skip=true -Denforcer.skip=true -Dspotless.check.skip=true "
         "-Dspotless.apply.skip=true -Dspotbugs.skip=true -Dpmd.skip=true -Dforbiddenapis.skip=true "
         "-Danimal.sniffer.skip=true -Dmaven.javadoc.skip=true -Dlicense.skip=true")
    if module == ".":
        cmd = f"mvn -B {S} {G} -DskipTests test-compile && mvn -B {S} {G} {goal}"
    else:
        cmd = (f"mvn -B {S} {G} -pl {module} -am -DskipTests install && "
               f"mvn -B {S} {G} -pl {module} {goal}")
    rc, out = _sandbox.run(cmd, repo, jdk=jdk, timeout=timeout)
    report = os.path.join(pom_dir, "target", "pit-reports", "mutations.xml")
    result = {"rc": rc, "report": report, "ok": False, "junit5": j5, "module": module,
              "log_tail": out[-3000:]}
    cm = _re.search(r"Line Coverage[^:]*:\s*(\d+)\s*/\s*(\d+)", out)
    if cm:
        cn, cd = int(cm.group(1)), int(cm.group(2))
        result["line_covered"], result["line_total"] = cn, cd
        result["line_cov"] = round(cn / cd, 4) if cd else 0.0
    if rc == 0 and os.path.exists(report):
        result.update(parse_report(report))
        result["ok"] = True
    return result


if __name__ == "__main__":
    r = parse_report(sys.argv[1])
    print(f"score = {r['killed']}/{r['total']} = {r['score']:.2%}  counts {r['counts']}")
