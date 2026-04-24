"""Pre-M2 Kuzu concurrency validation (PHASE1.md L106-145).

Outcome recorded on 2026-04-25: Kuzu 0.11.3 uses an **exclusive file
lock** on the database directory, exactly as documented at
https://docs.kuzudb.com/concurrency. That means:

- At most one process can open the DB for write.
- While a writer holds the lock, no other process (read or write) can
  open the DB.
- Multiple read-only processes can coexist, **but only** when no writer
  holds the exclusive lock at the same time.

The ARCHITECTURE §4.1 "single writer (librarian) + multiple in-process
readers (coordinator, dashboard, linter, user CLI)" model therefore does
**not** support multiple OS processes poking at ``dag.kz/`` directly.
Per PHASE1.md L117-123, Phase I revises the storage layer so that
librarian is the sole Kuzu-opening process; other processes request
reads via librarian's IPC command channel. See ARCHITECTURE §4.1
"Revised concurrency model (2026-04-25)" for the updated contract.

These tests lock in the observed behaviour so that if a future Kuzu
release relaxes the lock (or tightens it further), the regression is
visible.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


_LOCK_RO_SCRIPT = textwrap.dedent(
    """
    import sys, kuzu
    try:
        db = kuzu.Database(sys.argv[1], read_only=True)
        conn = kuzu.Connection(db)
        res = conn.execute("MATCH (n:Stub) RETURN count(n)")
        count = res.get_next()[0]
        print(f"RO_OK count={count}", flush=True)
    except Exception as exc:
        print(f"RO_FAIL {type(exc).__name__}: {exc}", flush=True)
    """
).strip()


def _bootstrap(db_path: Path) -> None:
    import kuzu

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    conn.execute("CREATE NODE TABLE IF NOT EXISTS Stub(id STRING, PRIMARY KEY(id))")
    conn.execute("MERGE (s:Stub {id: 'seed'})")
    # explicit teardown so the lock is released before the next step
    del conn, db


def test_concurrent_writers_are_rejected(tmp_path: Path) -> None:
    """Two processes opening Kuzu for write simultaneously: the second fails.

    This is the expected, documented behaviour. It codifies why §4.1 was
    revised to make librarian the sole Kuzu-opening process.
    """
    db_path = tmp_path / "dag.kz"
    _bootstrap(db_path)

    writer_script = textwrap.dedent(
        """
        import sys, time, kuzu
        try:
            db = kuzu.Database(sys.argv[1])
            conn = kuzu.Connection(db)
            # hold the lock for a moment
            for _ in range(5):
                conn.execute("MERGE (s:Stub {id: 'w'})")
                time.sleep(0.05)
            print("WRITE_OK", flush=True)
        except Exception as exc:
            print(f"WRITE_FAIL {type(exc).__name__}: {exc}", flush=True)
        """
    ).strip()

    # Start two writer subprocesses nearly simultaneously.
    p1 = subprocess.Popen(
        [sys.executable, "-c", writer_script, str(db_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    p2 = subprocess.Popen(
        [sys.executable, "-c", writer_script, str(db_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    o1, _ = p1.communicate(timeout=30)
    o2, _ = p2.communicate(timeout=30)

    outcomes = {o1.strip(), o2.strip()}
    # One must succeed, the other must fail with a lock error — that's
    # the single-writer constraint. Accept either ordering.
    success_count = sum(1 for line in outcomes if line.startswith("WRITE_OK"))
    fail_lock_count = sum(
        1
        for line in outcomes
        if line.startswith("WRITE_FAIL") and "lock" in line.lower()
    )
    assert success_count == 1, (
        f"exactly one writer must succeed, got outcomes={outcomes!r}"
    )
    assert fail_lock_count == 1, (
        f"exactly one writer must fail on the DB lock, got outcomes={outcomes!r}"
    )


def test_multi_process_readonly_coexists_when_no_writer(tmp_path: Path) -> None:
    """When no writer holds the lock, multiple read-only processes can open the DB."""
    db_path = tmp_path / "dag.kz"
    _bootstrap(db_path)

    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _LOCK_RO_SCRIPT, str(db_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(3)
    ]
    for p in procs:
        out, err = p.communicate(timeout=30)
        assert p.returncode == 0, f"rc={p.returncode} err={err!r}"
        assert out.strip().startswith("RO_OK"), out


def test_readonly_rejected_while_writer_holds_lock(tmp_path: Path) -> None:
    """A read-only open fails while another process holds the write lock.

    This is the key observation: Kuzu's lock is exclusive regardless of
    read/write intent, so §4.1 cannot rely on "readers open Kuzu while
    librarian writes" — readers must go through librarian IPC.
    """
    import kuzu

    db_path = tmp_path / "dag.kz"
    _bootstrap(db_path)

    # Take the write lock in-process and keep it for the subprocess call.
    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    conn.execute("MERGE (s:Stub {id: 'w'})")  # force lock acquisition

    p = subprocess.Popen(
        [sys.executable, "-c", _LOCK_RO_SCRIPT, str(db_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    out, err = p.communicate(timeout=30)
    assert p.returncode == 0, (p.returncode, err)
    stripped = out.strip()
    assert stripped.startswith("RO_FAIL"), (
        f"read-only open must fail while writer holds lock, got {stripped!r}"
    )
    assert "lock" in stripped.lower()

    # Release the lock; a subsequent read-only open should now succeed.
    del conn, db
    p2 = subprocess.Popen(
        [sys.executable, "-c", _LOCK_RO_SCRIPT, str(db_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    out2, err2 = p2.communicate(timeout=30)
    assert p2.returncode == 0, err2
    assert out2.strip().startswith("RO_OK"), out2


def test_in_process_connections_share_one_database(tmp_path: Path) -> None:
    """Within a single process, multiple Connection handles against the
    same Database instance coexist. This is the pattern librarian will
    use to serve concurrent read RPCs."""
    import kuzu

    db_path = tmp_path / "dag.kz"
    _bootstrap(db_path)

    db = kuzu.Database(str(db_path))
    c1 = kuzu.Connection(db)
    c2 = kuzu.Connection(db)
    c3 = kuzu.Connection(db)
    # All three read the seeded stub row concurrently (serialised by the
    # Python GIL, but at the Kuzu level the multiple-connections pattern
    # is the supported "in-process multi-reader" mode).
    for conn in (c1, c2, c3):
        res = conn.execute("MATCH (n:Stub) RETURN count(n)")
        assert res.get_next()[0] == 1
