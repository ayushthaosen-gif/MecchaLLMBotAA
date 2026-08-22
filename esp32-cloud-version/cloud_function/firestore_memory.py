"""
firestore_memory.py
----------------------
Firestore-backed replacement for main.py's in-process MemoryStore.

Why this exists: MemoryStore's in-memory list is fine for local testing
(simulate_full_run.py, test_real_modules.py) but doesn't actually give
"permanent memory that survives reboots" once deployed — a Cloud
Function/Cloud Run instance's process memory is wiped on every cold
start, and there can be multiple instances at once, each with its own
disconnected copy of the list. Firestore fixes both: durable storage,
shared across every instance and every cold start.

Same interface as MemoryStore (append / recent_text), so main.py needs
no other changes — see build_memory_store() below and its use in main.py.

Enable with:
    MEMORY_BACKEND=firestore
    (optionally) FIRESTORE_MEMORY_COLLECTION=meccanoid_memory
    (optionally) GOOGLE_CLOUD_PROJECT=your-project-id

Requires: pip install google-cloud-firestore

Auth: Application Default Credentials.
  - Deployed on Cloud Functions/Cloud Run: handled automatically, nothing
    to configure.
  - Local testing against a real Firestore project:
        gcloud auth application-default login
  - Local testing against the Firestore emulator instead of a real
    project (no GCP billing/project needed):
        gcloud emulators firestore start --host-port=localhost:8080
        export FIRESTORE_EMULATOR_HOST=localhost:8080
"""

import os
import datetime
from typing import List, Tuple

from google.cloud import firestore  # hard dependency ONLY when this
                                     # module is actually imported, i.e.
                                     # only when MEMORY_BACKEND=firestore


class FirestoreMemoryStore:
    """Persists conversation turns to a Firestore collection. One document
    per turn, ordered by server timestamp so ordering survives even if
    multiple instances write concurrently."""

    # Firestore charges/limits per document read — pulling only the last
    # N docs keeps recent_text() cheap regardless of how long the robot's
    # been running, mirroring MemoryStore's own bounded-growth fix.
    QUERY_LIMIT = 200

    def __init__(self, collection: str = None):
        self._db = firestore.Client()
        self._collection_name = collection or os.environ.get(
            "FIRESTORE_MEMORY_COLLECTION", "meccanoid_memory"
        )
        self._collection = self._db.collection(self._collection_name)

    def append(self, role: str, text: str) -> None:
        self._collection.add({
            "role": role,
            "text": text,
            "created_at": firestore.SERVER_TIMESTAMP,
        })

    def recent_text(self, max_chars: int = 4000) -> str:
        docs = (
            self._collection
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(self.QUERY_LIMIT)
            .stream()
        )
        entries: List[Tuple[str, str, str]] = []
        for doc in docs:
            data = doc.to_dict()
            ts = data.get("created_at")
            ts_str = ts.isoformat(timespec="seconds") if isinstance(ts, datetime.datetime) else "?"
            entries.append((ts_str, data.get("role", "?"), data.get("text", "")))
        entries.reverse()  # oldest -> newest, matching MemoryStore's ordering
        blob = "\n".join(f"[{ts}] {role}: {text}" for ts, role, text in entries)
        return blob[-max_chars:]
