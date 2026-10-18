"""Synthetic feasibility experiments: archive recovery and quota concurrency.

Not a production repository implementation. Uses disposable databases only.
"""
import concurrent.futures
import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/feasibility"


class SimulatedCrash(Exception):
    pass


def archive(db: sqlite3.Connection, folder: Path, crash_at: str | None = None) -> None:
    rows = db.execute("SELECT seq, payload FROM messages ORDER BY seq LIMIT 1000").fetchall()
    count = db.execute("SELECT count(*) FROM messages").fetchone()[0]
    if count < 2000:
        return
    blob = json.dumps([json.loads(payload) for _, payload in rows], ensure_ascii=False, separators=(",", ":")).encode()
    destination = folder / f"{rows[0][0]}-{rows[-1][0]}.json"
    if crash_at == "before_write":
        raise SimulatedCrash()
    temporary = destination.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        stream.write(blob)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(destination)
    if crash_at == "after_rename":
        raise SimulatedCrash()
    with db:
        db.execute("INSERT OR IGNORE INTO archives VALUES(?,?,?,?)", (
            str(destination), rows[0][0], rows[-1][0], hashlib.sha256(blob).hexdigest()))
        db.execute("DELETE FROM messages WHERE seq BETWEEN ? AND ?", (rows[0][0], rows[-1][0]))
        if crash_at == "before_commit":
            raise SimulatedCrash()
    if crash_at == "after_commit":
        raise SimulatedCrash()


def archive_test(crash_at: str) -> dict:
    folder = OUT / "storage-runs" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    with sqlite3.connect(folder / "state.db") as db:
        db.executescript("CREATE TABLE messages(seq INTEGER PRIMARY KEY,payload TEXT NOT NULL);"
                         "CREATE TABLE archives(path TEXT PRIMARY KEY,lo INTEGER,hi INTEGER,sha256 TEXT);")
        db.executemany("INSERT INTO messages VALUES(?,?)", [(n, json.dumps({"seq": n, "text": f"synthetic-{n}"})) for n in range(1, 3001)])
        db.commit()
        try:
            archive(db, folder, crash_at)
        except SimulatedCrash:
            pass
        # Recovery sees either committed data or the still-intact source rows.
        physical = {n for n, in db.execute("SELECT seq FROM messages")}
        for path in folder.glob("*.json"):
            physical.update(item["seq"] for item in json.loads(path.read_text(encoding="utf8")))
        assert physical == set(range(1, 3001)), "A simulated crash lost messages"
        while db.execute("SELECT count(*) FROM messages").fetchone()[0] >= 2000:
            archive(db, folder)
        effective = [n for n, in db.execute("SELECT seq FROM messages")]
        for path, digest in db.execute("SELECT path,sha256 FROM archives"):
            blob = Path(path).read_bytes()
            assert hashlib.sha256(blob).hexdigest() == digest
            effective.extend(item["seq"] for item in json.loads(blob))
        assert len(effective) == len(set(effective)) == 3000
        assert sorted(effective) == list(range(1, 3001))
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1000
        return {"crash_at": crash_at, "recovered_without_loss_or_duplicates": True,
                "current_rows": 1000, "archived_rows": 2000}


def quota_test() -> dict:
    path = OUT / "storage-runs" / f"quota-{uuid.uuid4().hex}.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE quota(id INTEGER PRIMARY KEY,remaining INTEGER CHECK(remaining>=0))")
        db.execute("INSERT INTO quota VALUES(1,10)")
    def reserve(_: int) -> bool:
        with sqlite3.connect(path, timeout=10) as db:
            return db.execute("UPDATE quota SET remaining=remaining-1 WHERE id=1 AND remaining>0").rowcount == 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(reserve, range(32)))
    with sqlite3.connect(path) as db:
        remaining = db.execute("SELECT remaining FROM quota").fetchone()[0]
    assert sum(results) == 10 and remaining == 0
    return {"concurrent_requests": 32, "initial_quota": 10, "accepted": sum(results), "remaining": remaining}


def checkin_test() -> dict:
    def classify(value: str):
        moment = datetime.fromisoformat(value)  # Explicit synthetic Asia/Shanghai wall time.
        minutes = moment.hour * 60 + moment.minute
        if 270 <= minutes < 720:
            return "morning", str(moment.date())
        if minutes >= 1200:
            return "night", str(moment.date())
        if minutes < 180:
            return "night", str((moment - timedelta(days=1)).date())
        return None
    cases = {
        "2026-10-15T04:29": None, "2026-10-15T04:30": ("morning", "2026-10-15"),
        "2026-10-15T11:59": ("morning", "2026-10-15"), "2026-10-15T12:00": None,
        "2026-10-15T19:59": None, "2026-10-15T20:00": ("night", "2026-10-15"),
        "2026-10-16T00:00": ("night", "2026-10-15"), "2026-10-16T02:59": ("night", "2026-10-15"),
        "2026-10-16T03:00": None,
    }
    for moment, expected in cases.items():
        assert classify(moment) == expected
    return {"boundary_cases": len(cases), "passed": True,
            "interval_assumption": "start inclusive, end exclusive; Asia/Shanghai"}


def main() -> None:
    (OUT / "storage-runs").mkdir(parents=True, exist_ok=True)
    report = {"synthetic_only": True, "archive": [archive_test(stage) for stage in (
        "before_write", "after_rename", "before_commit", "after_commit")],
        "quota": quota_test(), "checkin": checkin_test()}
    (OUT / "storage-probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
