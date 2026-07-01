"""Docker-bounded execution for build/PIT runs (P4).

Every Maven/Gradle/PIT invocation goes through run(): a --rm container on the
mvn-cache network (so `nexus` resolves), repo bind-mounted at its real host path,
Nexus mirror via -s, warm m2/gradle volumes, and an INNER timeout so a hung JVM
self-exits (a host-side kill would only drop the docker client, not the container).
"""
import subprocess, shlex, os
from common import SETTINGS, PROJECT, DATA, log

NETWORK = "mvn-cache"
M2_VOL = "oh-m2-cache"
GRADLE_VOL = "oh-gradle-cache"


def image(jdk):
    return f"java-{jdk}-improve-java-tests-sandbox"


def abs_repo(repo):
    """Resolve a repo path to its REAL host absolute path. Relative paths are taken
    under IJT_HOME (mounted identically in orch + sandbox), so the host daemon's bind
    mount resolves the same path the orchestrator sees."""
    repo = str(repo)
    return repo if os.path.isabs(repo) else str(DATA / repo)


def ensure_image(jdk):
    name = image(jdk)
    if subprocess.run(["docker", "image", "inspect", name],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        return name
    log("fast", "build_image", jdk=jdk, image=name)
    subprocess.run(
        ["docker", "build", "-f", "docker/Dockerfile.sandbox",
         "--build-arg", f"JDK={jdk}", "-t", name, "."],
        cwd=str(PROJECT), check=True)
    return name


def run(cmd, repo, jdk=21, timeout=31_536_000, name=None, mem="4g"):
    """Run `cmd` (a shell string) inside the sandbox over `repo`. Returns (rc, output).
    An inner `timeout` wraps cmd; container is --rm and force-removed first if named."""
    repo = abs_repo(repo)
    img = ensure_image(jdk)
    inner = f"timeout {timeout} bash -lc {shlex.quote(cmd)}"
    args = ["docker", "run", "--rm", "--network", NETWORK,
            "--memory", mem, "--cpus", "4",
            "-v", f"{repo}:{repo}", "-w", repo,
            "-v", f"{SETTINGS}:/sandbox-settings.xml:ro",
            "-v", f"{M2_VOL}:/root/.m2", "-v", f"{GRADLE_VOL}:/root/.gradle"]
    if name:
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        args += ["--name", name]
    args += [img, "bash", "-lc", inner]
    # No OUTER (Python) timeout: subprocess.run converts it to poll() milliseconds and a 1-year value
    # overflows C INT_MAX ("timeout is too large"). The INNER coreutils `timeout {timeout}` (1y, set by
    # callers) is the real bound and has no such limit; here we wait indefinitely for the container.
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout
