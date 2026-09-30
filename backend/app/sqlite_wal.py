"""Switching a SQLite file several processes share to WAL.

WAL lets readers go on while a writer writes — without it a long write in
one process holds every reader in another off until it commits. The switch
itself needs the file to itself, so while another process holds a
transaction it can't happen, and two processes switching at once can have
SQLite refuse the loser at once rather than wait. So: a short-lived
connection of its own (a caller's shared connection keeps its own busy
timeout, and its concurrent users never see it changed), retried until
`within_s` runs out. The mode is persistent in the file, and every
connection follows a switch made by another.
"""

import sqlite3
import time


def switch_to_wal(path: str, within_s: float) -> bool:
    """Whether `path` is in WAL mode, switching it if it isn't, trying for
    at most `within_s` seconds."""
    deadline = time.monotonic() + within_s
    while True:
        remaining = max(deadline - time.monotonic(), 0.01)
        try:
            conn = sqlite3.connect(path, timeout=remaining, isolation_level=None)
            try:
                if conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal":
                    return True
            finally:
                conn.close()
        except sqlite3.OperationalError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
