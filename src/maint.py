"""Substrate upkeep (P4): reap root-owned build scratch and guard disk.

A file a root sandbox container wrote (clones/*/target, pit-reports) is removed by a ROOT
container, never host rm. disk_free_gb() lets drivers refuse to start a PIT run when the box
is near full.
"""
import os, shutil, subprocess
from common import PROJECT, CLONES, log


def disk_free_gb(path="/"):
    s = os.statvfs(path)
    return s.f_bavail * s.f_frsize / 1e9


def reap_build_dirs():
    """Remove maven 'target' dirs under clones/ via a root container (they are root-owned)."""
    root = str(CLONES)
    if not os.path.isdir(root):
        return 0
    script = (
        "import os,shutil;"
        "[shutil.rmtree(os.path.join(b,'target'),ignore_errors=True) "
        f"for b,_,_ in os.walk('{root}') if os.path.isdir(os.path.join(b,'target'))]"
    )
    subprocess.run(["docker", "run", "--rm", "-v", f"{root}:{root}", "python:3-slim",
                    "python3", "-c", script], check=False)
    log("fast", "reap_build_dirs", root=root, free_gb=round(disk_free_gb(), 1))
    return 1


def reap_clone(repo_dir):
    """Drop a whole clone (root-owned trees included) via a root container."""
    d = repo_dir if os.path.isabs(repo_dir) else str(PROJECT / repo_dir)
    subprocess.run(["docker", "run", "--rm", "-v", f"{os.path.dirname(d)}:{os.path.dirname(d)}",
                    "python:3-slim", "rm", "-rf", d], check=False)
    log("fast", "reap_clone", dir=d, free_gb=round(disk_free_gb(), 1))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "reap":
        reap_build_dirs()
    print(f"disk free: {disk_free_gb():.1f} GB")
