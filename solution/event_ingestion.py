"""Event ingestion pipeline for the append-only interaction ledger."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from solution.config import IST
from solution.crypto import hash_phone
from solution.normalizers import normalize_phone, parse_timestamp_ist
from solution.schema_loader import SchemaCatalog


def compute_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class EventIngestor:
    def __init__(self, conn: sqlite3.Connection, catalog: SchemaCatalog):
        self.conn = conn
        self.catalog = catalog
        self.seen_event_ids: set[str] | None = None

    def _get_seen_event_ids(self) -> set[str]:
        if self.seen_event_ids is None:
            cursor = self.conn.cursor()
            cursor.execute("SELECT event_id FROM events")
            self.seen_event_ids = {r[0] for r in cursor.fetchall()}
        return self.seen_event_ids

    def ingest_event_file(self, file_path: Path, drop_name: str) -> bool:
        """Ingest a single event JSON Lines file. Returns True if processed, False if already ingested."""
        content_hash = compute_file_hash(file_path)
        norm_file_path = str(file_path.resolve())

        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT content_hash FROM ingested_files WHERE file_path = ?",
            (norm_file_path,),
        )
        row = cursor.fetchone()
        if row and row["content_hash"] == content_hash:
            # Already ingested
            return False

        seen_event_ids = self._get_seen_event_ids()

        events_total = 0
        malformed_events = 0
        duplicate_events_dropped = 0
        unknown_disposition_events = 0
        events_to_insert: list[tuple] = []

        now_iso = datetime.now(IST).isoformat()

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue

                events_total += 1

                try:
                    data = json.loads(stripped)
                    if not isinstance(data, dict):
                        malformed_events += 1
                        continue
                except Exception:
                    malformed_events += 1
                    continue

                event_id = data.get("event_id")
                if not event_id or not isinstance(event_id, str):
                    malformed_events += 1
                    continue

                event_id = event_id.strip()
                if not event_id:
                    malformed_events += 1
                    continue

                # Duplicate check
                if event_id in seen_event_ids:
                    duplicate_events_dropped += 1
                    continue

                seen_event_ids.add(event_id)

                event_type = data.get("event_type", "").strip()
                workflow_id = data.get("workflow_id", "").strip()
                call_id = data.get("call_id", "").strip()
                occurred_at_raw = data.get("occurred_at")

                if not event_type or not workflow_id or not call_id or not occurred_at_raw:
                    malformed_events += 1
                    continue

                try:
                    occurred_dt = parse_timestamp_ist(str(occurred_at_raw))
                    occurred_at_iso = occurred_dt.isoformat()
                except Exception:
                    malformed_events += 1
                    continue

                payload = data.get("payload")
                if not isinstance(payload, dict):
                    payload = {}

                # Check disposition catalog
                is_valid_disp = 1
                if event_type.startswith("disposition."):
                    disp_code = payload.get("disposition_code")
                    wf_disps = self.catalog.dispositions.get(workflow_id, {})
                    if not disp_code or str(disp_code).strip() not in wf_disps:
                        unknown_disposition_events += 1
                        is_valid_disp = 0

                # Phone normalization
                phone_hash = None
                raw_phone = data.get("phone")
                if raw_phone:
                    norm_p = normalize_phone(str(raw_phone))
                    if norm_p:
                        phone_hash = hash_phone(norm_p)

                events_to_insert.append(
                    (
                        event_id,
                        event_type,
                        workflow_id,
                        call_id,
                        phone_hash,
                        occurred_at_iso,
                        json.dumps(payload),
                        is_valid_disp,
                        norm_file_path,
                    )
                )

        # Batch insert events
        if events_to_insert:
            cursor.executemany(
                """
                INSERT INTO events (
                    event_id, event_type, workflow_id, call_id, phone_hash,
                    occurred_at, payload, is_valid_disposition, file_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                events_to_insert,
            )

        # Update stats
        cursor.execute(
            "INSERT INTO pipeline_stats (stat_key, stat_value) VALUES ('events_total', ?) ON CONFLICT(stat_key) DO UPDATE SET stat_value = stat_value + ?",
            (events_total, events_total),
        )
        cursor.execute(
            "INSERT INTO pipeline_stats (stat_key, stat_value) VALUES ('malformed_events', ?) ON CONFLICT(stat_key) DO UPDATE SET stat_value = stat_value + ?",
            (malformed_events, malformed_events),
        )
        cursor.execute(
            "INSERT INTO pipeline_stats (stat_key, stat_value) VALUES ('duplicate_events_dropped', ?) ON CONFLICT(stat_key) DO UPDATE SET stat_value = stat_value + ?",
            (duplicate_events_dropped, duplicate_events_dropped),
        )
        cursor.execute(
            "INSERT INTO pipeline_stats (stat_key, stat_value) VALUES ('unknown_disposition_events', ?) ON CONFLICT(stat_key) DO UPDATE SET stat_value = stat_value + ?",
            (unknown_disposition_events, unknown_disposition_events),
        )

        cursor.execute(
            """
            INSERT OR REPLACE INTO ingested_files (
                file_path, file_type, drop_name, content_hash, rows_count, ingested_at
            ) VALUES (?, 'event', ?, ?, ?, ?)
            """,
            (norm_file_path, drop_name, content_hash, events_total, now_iso),
        )

        self.conn.commit()
        return True
