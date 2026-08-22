"""Robot-scoped memory and command queues for the cloud brain.

The in-memory implementation is deterministic and dependency-free for local
tests.  Firestore is the production implementation: it survives cold starts
and makes /chat and the ESP32 polling endpoints safe when they land on
different Cloud Run/Functions instances.
"""

from __future__ import annotations

import datetime
import os
import threading
import uuid
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class QueuedItem:
    id: str
    value: str


class InMemoryStateBackend:
    """Thread-safe local backend. Acknowledged items are removed."""

    def __init__(self):
        self._lock = threading.RLock()
        self._memory: dict[str, list[tuple[str, str, str]]] = {}
        self._queues: dict[tuple[str, str], list[QueuedItem]] = {}

    def append_memory(self, robot_id: str, role: str, text: str) -> None:
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._memory.setdefault(robot_id, []).append((timestamp, role, text))

    def recent_memory(self, robot_id: str, max_chars: int = 4000) -> str:
        with self._lock:
            rows = list(self._memory.get(robot_id, ()))
        blob = "\n".join(f"[{ts}] {role}: {text}" for ts, role, text in rows)
        return blob[-max_chars:]

    def enqueue(self, robot_id: str, queue_name: str, value: str) -> QueuedItem:
        item = QueuedItem(str(uuid.uuid4()), value)
        with self._lock:
            self._queues.setdefault((robot_id, queue_name), []).append(item)
        return item

    def next_item(self, robot_id: str, queue_name: str) -> Optional[QueuedItem]:
        with self._lock:
            items = self._queues.get((robot_id, queue_name), ())
            return items[0] if items else None

    def ack(self, robot_id: str, queue_name: str, item_id: str) -> bool:
        key = (robot_id, queue_name)
        with self._lock:
            items = self._queues.get(key, [])
            for index, item in enumerate(items):
                if item.id == item_id:
                    del items[index]
                    return True
        return False


class FirestoreStateBackend:
    """Durable backend for Cloud Run and Google/Firebase Functions."""

    def __init__(self, client=None):
        if client is None:
            from google.cloud import firestore

            client = firestore.Client()
        self._client = client

    def _robot(self, robot_id: str):
        return self._client.collection("meccanoid_robots").document(robot_id)

    def append_memory(self, robot_id: str, role: str, text: str) -> None:
        from google.cloud import firestore

        self._robot(robot_id).collection("memory").document().set({
            "role": role,
            "text": text,
            "created_at": firestore.SERVER_TIMESTAMP,
        })

    def recent_memory(self, robot_id: str, max_chars: int = 4000) -> str:
        rows = (
            self._robot(robot_id)
            .collection("memory")
            .order_by("created_at", direction="DESCENDING")
            .limit(50)
            .stream()
        )
        entries = []
        for row in rows:
            data = row.to_dict()
            created = data.get("created_at")
            stamp = created.isoformat(timespec="seconds") if created else "pending"
            entries.append((stamp, data.get("role", "unknown"), data.get("text", "")))
        entries.reverse()
        blob = "\n".join(f"[{ts}] {role}: {text}" for ts, role, text in entries)
        return blob[-max_chars:]

    def enqueue(self, robot_id: str, queue_name: str, value: str) -> QueuedItem:
        from google.cloud import firestore

        ref = self._robot(robot_id).collection(f"{queue_name}_queue").document()
        ref.set({"value": value, "created_at": firestore.SERVER_TIMESTAMP})
        return QueuedItem(ref.id, value)

    def next_item(self, robot_id: str, queue_name: str) -> Optional[QueuedItem]:
        rows = list(
            self._robot(robot_id)
            .collection(f"{queue_name}_queue")
            .order_by("created_at")
            .limit(1)
            .stream()
        )
        if not rows:
            return None
        row = rows[0]
        return QueuedItem(row.id, row.to_dict().get("value", ""))

    def ack(self, robot_id: str, queue_name: str, item_id: str) -> bool:
        if not item_id:
            return False
        self._robot(robot_id).collection(f"{queue_name}_queue").document(item_id).delete()
        return True


def build_state_backend(name: Optional[str] = None):
    backend = (name or os.environ.get("STATE_BACKEND", "memory")).lower()
    if backend == "memory":
        return InMemoryStateBackend()
    if backend == "firestore":
        return FirestoreStateBackend()
    raise ValueError("STATE_BACKEND must be 'memory' or 'firestore'")

