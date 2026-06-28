"""The candidate queue (P3): corpus/queue.jsonl, one admitted record per repo, deduped."""
import json, os, threading
from common import CORPUS

QUEUE = CORPUS / "queue.jsonl"

# dig runs DIG_WORKERS threads in ONE process; the queue/no_baseline files are read-modify-written.
# Serialize every such update so a concurrent reader can't drop a sibling thread's write.
_LOCK = threading.Lock()


def load():
    if not QUEUE.exists():
        return []
    return [json.loads(l) for l in QUEUE.read_text().splitlines() if l.strip()]


def admit(record):
    """Append a candidate record, replacing any prior entry for the same repo."""
    CORPUS.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        rows = [r for r in load() if r.get("repo") != record.get("repo")]
        rows.append(record)
        tmp = str(QUEUE) + ".tmp"
        with open(tmp, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        os.replace(tmp, str(QUEUE))
    return record


def has(repo):
    return any(r.get("repo") == repo for r in load())


SEEN = CORPUS / "seen.txt"


def is_seen(repo):
    """True if this repo was already gated (admitted OR dropped) — so the dig never re-gates it."""
    return SEEN.exists() and repo in set(SEEN.read_text().split())


def mark_seen(repo):
    CORPUS.mkdir(parents=True, exist_ok=True)
    with open(SEEN, "a") as f:
        f.write(repo + "\n")

NO_BASELINE_FILE = CORPUS / "no_baseline.txt"


def is_no_baseline(cls):
    return NO_BASELINE_FILE.exists() and cls in set(NO_BASELINE_FILE.read_text().split())


def mark_no_baseline(cls):
    CORPUS.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        if not is_no_baseline(cls):
            with open(NO_BASELINE_FILE, "a") as f:
                f.write(cls + "\n")
