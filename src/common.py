"""Shared env, paths, and frog's-eye logging for improve-java-tests.

Logs are JSON lines under /var/log/observe/app/ijt/<stream>.jsonl so the external
frog's eye auto-discovers them (no wiring). Streams mirror the digest cadence:
  fast   = per-step events (container start/exit, pit run)
  medium = per-target / per-survivor outcomes
  slow   = per-queue / per-pass summaries
"""
import os, json, time, pathlib

PROJECT = pathlib.Path(os.environ.get("IJT_HOME", os.path.expanduser("~/improve-java-tests/current_attempt")))
# DATA = the iteration's data root (corpus/clones/scratch); IJT_HOME stays the CODE root.
# Defaults to PROJECT so the pre-reorg flat layout keeps working unchanged.
DATA = pathlib.Path(os.environ.get("IJT_DATA", str(PROJECT / "current_iteration")))
CLONES = DATA / "clones"
CORPUS = DATA / "corpus"
SCRATCH = DATA / "scratch"
SETTINGS = PROJECT / "docker" / "sandbox-settings.xml"

LOG_DIR = pathlib.Path("/var/log/observe/app/ijt")


def _env_file():
    f = PROJECT / ".env"
    if not f.exists():
        return {}
    out = {}
    for line in f.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


_ENV = {**_env_file(), **os.environ}


def env(key, default=None):
    return _ENV.get(key, default)


def log(stream, event, **fields):
    """Append one JSON line to the frog's-eye stream. stream in {fast,medium,slow}."""
    rec = {"ts": time.time(), "t": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / f"{stream}.jsonl", "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass  # logging must never break the run
    return rec
