"""Database management and SQLite schema definitions."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from solution.config import DEFAULT_DB_PATH
from solution.schema_loader import SchemaCatalog


def get_db_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Create or connect to SQLite database with optimized pragmas."""
    target_path = Path(db_path) if db_path else DEFAULT_DB_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    # Performance pragmas
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA cache_size = -64000;")  # 64MB cache
    return conn


def init_db(conn: sqlite3.Connection, catalog: SchemaCatalog | None = None) -> None:
    """Initialize tables, indexes, and populate disposition catalogue."""
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS ingested_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE NOT NULL,
            file_type TEXT NOT NULL,
            drop_name TEXT NOT NULL,
            workflow_id TEXT,
            batch_id TEXT,
            content_hash TEXT NOT NULL,
            rows_count INTEGER DEFAULT 0,
            ingested_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL,
            phone_hash TEXT NOT NULL,
            phone_encrypted TEXT NOT NULL,
            phone_masked TEXT NOT NULL,
            customer_name_encrypted TEXT NOT NULL,
            external_ref TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            optional_data TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workflow_id, phone_hash)
        );

        CREATE INDEX IF NOT EXISTS idx_leads_lookup ON leads(workflow_id, phone_hash);
        CREATE INDEX IF NOT EXISTS idx_leads_batch ON leads(batch_id);
        CREATE INDEX IF NOT EXISTS idx_leads_ref ON leads(workflow_id, external_ref);

        CREATE TABLE IF NOT EXISTS rejections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            reason TEXT NOT NULL,
            file_path TEXT NOT NULL,
            UNIQUE(file_path, row_number)
        );

        CREATE INDEX IF NOT EXISTS idx_rejections_sort ON rejections(workflow_id, batch_id, row_number);

        CREATE TABLE IF NOT EXISTS pipeline_stats (
            stat_key TEXT PRIMARY KEY,
            stat_value INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS rejections_by_reason_stats (
            reason TEXT PRIMARY KEY,
            count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            call_id TEXT NOT NULL,
            phone_hash TEXT,
            occurred_at TEXT NOT NULL,
            payload TEXT NOT NULL,
            is_valid_disposition INTEGER NOT NULL DEFAULT 1,
            file_path TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_events_call ON events(call_id);
        CREATE INDEX IF NOT EXISTS idx_events_call_phone ON events(call_id, phone_hash);
        CREATE INDEX IF NOT EXISTS idx_events_phone ON events(workflow_id, phone_hash);
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
        CREATE INDEX IF NOT EXISTS idx_events_occurred ON events(occurred_at);

        CREATE TABLE IF NOT EXISTS dispositions_catalog (
            workflow_id TEXT NOT NULL,
            code TEXT NOT NULL,
            label TEXT NOT NULL,
            is_terminal INTEGER NOT NULL,
            is_success INTEGER NOT NULL,
            PRIMARY KEY (workflow_id, code)
        );
        """)

    # Populate dispositions catalogue if provided
    if catalog:
        with conn:
            for wf, disps in catalog.dispositions.items():
                for disp in disps.values():
                    conn.execute("""
                    INSERT OR REPLACE INTO dispositions_catalog (workflow_id, code, label, is_terminal, is_success)
                    VALUES (?, ?, ?, ?, ?)
                    """, (disp.workflow_id, disp.code, disp.label, 1 if disp.is_terminal else 0, 1 if disp.is_success else 0))
