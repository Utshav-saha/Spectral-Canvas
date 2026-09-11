"""In-memory session store.

Deliberately not a database. A session is one encode or one decode, holds the
audio array + metadata + rendered PNGs, and expires after an hour. If the
server restarts, everything is gone - which is correct for a simulator.
"""

import time
import uuid
import threading

from app.config import SESSION_TTL_SECONDS, MAX_SESSIONS

_lock = threading.Lock()
_sessions = {}


def _prune():
    now = time.time()
    dead = [k for k, v in _sessions.items() if now - v["created"] > SESSION_TTL_SECONDS]
    for k in dead:
        _sessions.pop(k, None)
    while len(_sessions) > MAX_SESSIONS:
        oldest = min(_sessions, key=lambda k: _sessions[k]["created"])
        _sessions.pop(oldest, None)


def create(payload: dict) -> str:
    session_id = uuid.uuid4().hex[:16]
    with _lock:
        _prune()
        _sessions[session_id] = {"created": time.time(), **payload}
    return session_id


def get(session_id: str):
    with _lock:
        return _sessions.get(session_id)


def update(session_id: str, payload: dict):
    with _lock:
        if session_id in _sessions:
            _sessions[session_id].update(payload)
