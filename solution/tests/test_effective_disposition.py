"""Unit tests for effective disposition resolution and catalogue mapping."""
from __future__ import annotations

import sqlite3
import pytest
from solution.db import init_db
from solution.ledger import LedgerService
from solution.schema_loader import SchemaCatalog


@pytest.fixture
def test_db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    catalog = SchemaCatalog()
    init_db(conn, catalog)
    return conn, catalog


def test_effective_disposition_precedence(test_db_conn):
    conn, catalog = test_db_conn
    cursor = conn.cursor()

    # Create dummy lead
    cursor.execute("""
    INSERT INTO leads (workflow_id, phone_hash, phone_encrypted, phone_masked, customer_name_encrypted, external_ref, batch_id, created_at, updated_at)
    VALUES ('motor_renewal', 'hash123', 'enc', '+91******1234', 'enc_name', 'MOT/2026/001', 'batch_001', '2026-10-01T10:00:00+05:30', '2026-10-01T10:00:00+05:30')
    """)

    # Call 1: has disposition.set 'callback_requested', then override 'renewed'
    cursor.execute("""
    INSERT INTO events (event_id, event_type, workflow_id, call_id, phone_hash, occurred_at, payload, is_valid_disposition, file_path)
    VALUES ('e1', 'call.initiated', 'motor_renewal', 'c1', 'hash123', '2026-10-01T10:00:00+05:30', '{}', 1, 'f1')
    """)
    cursor.execute("""
    INSERT INTO events (event_id, event_type, workflow_id, call_id, phone_hash, occurred_at, payload, is_valid_disposition, file_path)
    VALUES ('e2', 'call.connected', 'motor_renewal', 'c1', 'hash123', '2026-10-01T10:00:15+05:30', '{}', 1, 'f1')
    """)
    cursor.execute("""
    INSERT INTO events (event_id, event_type, workflow_id, call_id, phone_hash, occurred_at, payload, is_valid_disposition, file_path)
    VALUES ('e3', 'disposition.set', 'motor_renewal', 'c1', 'hash123', '2026-10-01T10:02:00+05:30', '{"disposition_code": "callback_requested"}', 1, 'f1')
    """)
    cursor.execute("""
    INSERT INTO events (event_id, event_type, workflow_id, call_id, phone_hash, occurred_at, payload, is_valid_disposition, file_path)
    VALUES ('e4', 'disposition.override', 'motor_renewal', 'c1', NULL, '2026-10-01T14:00:00+05:30', '{"disposition_code": "renewed"}', 1, 'f2')
    """)

    # Call 2: has only disposition.set 'payment_link_sent'
    cursor.execute("""
    INSERT INTO events (event_id, event_type, workflow_id, call_id, phone_hash, occurred_at, payload, is_valid_disposition, file_path)
    VALUES ('e5', 'call.initiated', 'motor_renewal', 'c2', 'hash123', '2026-10-02T11:00:00+05:30', '{}', 1, 'f1')
    """)
    cursor.execute("""
    INSERT INTO events (event_id, event_type, workflow_id, call_id, phone_hash, occurred_at, payload, is_valid_disposition, file_path)
    VALUES ('e6', 'disposition.set', 'motor_renewal', 'c2', 'hash123', '2026-10-02T11:05:00+05:30', '{"disposition_code": "payment_link_sent"}', 1, 'f1')
    """)

    # Call 3: has an unknown disposition code ('unknown_code') which should be ignored
    cursor.execute("""
    INSERT INTO events (event_id, event_type, workflow_id, call_id, phone_hash, occurred_at, payload, is_valid_disposition, file_path)
    VALUES ('e7', 'call.initiated', 'motor_renewal', 'c3', 'hash123', '2026-10-03T12:00:00+05:30', '{}', 1, 'f1')
    """)
    cursor.execute("""
    INSERT INTO events (event_id, event_type, workflow_id, call_id, phone_hash, occurred_at, payload, is_valid_disposition, file_path)
    VALUES ('e8', 'disposition.set', 'motor_renewal', 'c3', 'hash123', '2026-10-03T12:05:00+05:30', '{"disposition_code": "unknown_code"}', 0, 'f1')
    """)

    conn.commit()

    ledger = LedgerService(conn, catalog)
    calls = ledger.get_all_linked_calls()
    assert len(calls) == 3

    c1 = next(c for c in calls if c.call_id == 'c1')
    assert c1.effective_disposition == 'renewed'
    assert c1.is_terminal is True
    assert c1.is_success is True

    c2 = next(c for c in calls if c.call_id == 'c2')
    assert c2.effective_disposition == 'payment_link_sent'
    assert c2.is_terminal is False
    assert c2.is_success is False

    c3 = next(c for c in calls if c.call_id == 'c3')
    assert c3.effective_disposition is None
    assert c3.is_terminal is False
    assert c3.is_success is False
