"""Unit tests for lead bucket categorization logic."""
from __future__ import annotations

import sqlite3
import pytest
from solution.db import init_db
from solution.ledger import LedgerService
from solution.schema_loader import SchemaCatalog


@pytest.fixture
def db_with_catalog():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    catalog = SchemaCatalog()
    init_db(conn, catalog)
    return conn, catalog


def test_bucket_categorization(db_with_catalog):
    conn, catalog = db_with_catalog
    cursor = conn.cursor()

    # Lead 1: No calls -> yet_to_reach_out
    cursor.execute("""
    INSERT INTO leads (workflow_id, phone_hash, phone_encrypted, phone_masked, customer_name_encrypted, external_ref, batch_id, created_at, updated_at)
    VALUES ('motor_renewal', 'hash_l1', 'enc', '+91******1111', 'enc_name', 'MOT/2026/001', 'batch_001', '2026-10-01T10:00:00+05:30', '2026-10-01T10:00:00+05:30')
    """)

    # Lead 2: 2 calls, 1 telephony failed, 1 terminal (renewed) -> completed
    cursor.execute("""
    INSERT INTO leads (workflow_id, phone_hash, phone_encrypted, phone_masked, customer_name_encrypted, external_ref, batch_id, created_at, updated_at)
    VALUES ('motor_renewal', 'hash_l2', 'enc', '+91******2222', 'enc_name', 'MOT/2026/002', 'batch_001', '2026-10-01T10:00:00+05:30', '2026-10-01T10:00:00+05:30')
    """)
    cursor.execute("""
    INSERT INTO events (event_id, event_type, workflow_id, call_id, phone_hash, occurred_at, payload, is_valid_disposition, file_path)
    VALUES ('e1', 'call.initiated', 'motor_renewal', 'c2_1', 'hash_l2', '2026-10-01T10:00:00+05:30', '{}', 1, 'f1'),
           ('e2', 'call.telephony_failed', 'motor_renewal', 'c2_1', 'hash_l2', '2026-10-01T10:00:05+05:30', '{"reason": "carrier_rejected"}', 1, 'f1'),
           ('e3', 'call.initiated', 'motor_renewal', 'c2_2', 'hash_l2', '2026-10-02T10:00:00+05:30', '{}', 1, 'f1'),
           ('e4', 'call.connected', 'motor_renewal', 'c2_2', 'hash_l2', '2026-10-02T10:00:10+05:30', '{}', 1, 'f1'),
           ('e5', 'disposition.set', 'motor_renewal', 'c2_2', 'hash_l2', '2026-10-02T10:05:00+05:30', '{"disposition_code": "renewed"}', 1, 'f1')
    """)

    # Lead 3: 2 calls, BOTH telephony failed -> in_progress_telephony_failed
    cursor.execute("""
    INSERT INTO leads (workflow_id, phone_hash, phone_encrypted, phone_masked, customer_name_encrypted, external_ref, batch_id, created_at, updated_at)
    VALUES ('motor_renewal', 'hash_l3', 'enc', '+91******3333', 'enc_name', 'MOT/2026/003', 'batch_001', '2026-10-01T10:00:00+05:30', '2026-10-01T10:00:00+05:30')
    """)
    cursor.execute("""
    INSERT INTO events (event_id, event_type, workflow_id, call_id, phone_hash, occurred_at, payload, is_valid_disposition, file_path)
    VALUES ('e6', 'call.initiated', 'motor_renewal', 'c3_1', 'hash_l3', '2026-10-01T11:00:00+05:30', '{}', 1, 'f1'),
           ('e7', 'call.telephony_failed', 'motor_renewal', 'c3_1', 'hash_l3', '2026-10-01T11:00:05+05:30', '{"reason": "sip_503"}', 1, 'f1'),
           ('e8', 'call.initiated', 'motor_renewal', 'c3_2', 'hash_l3', '2026-10-02T11:00:00+05:30', '{}', 1, 'f1'),
           ('e9', 'call.telephony_failed', 'motor_renewal', 'c3_2', 'hash_l3', '2026-10-02T11:00:05+05:30', '{"reason": "invalid_route"}', 1, 'f1')
    """)

    # Lead 4: 2 calls, 1 telephony failed, 1 no answer (call.ended without disposition) -> in_progress_dialed
    cursor.execute("""
    INSERT INTO leads (workflow_id, phone_hash, phone_encrypted, phone_masked, customer_name_encrypted, external_ref, batch_id, created_at, updated_at)
    VALUES ('motor_renewal', 'hash_l4', 'enc', '+91******4444', 'enc_name', 'MOT/2026/004', 'batch_001', '2026-10-01T10:00:00+05:30', '2026-10-01T10:00:00+05:30')
    """)
    cursor.execute("""
    INSERT INTO events (event_id, event_type, workflow_id, call_id, phone_hash, occurred_at, payload, is_valid_disposition, file_path)
    VALUES ('e10', 'call.initiated', 'motor_renewal', 'c4_1', 'hash_l4', '2026-10-01T12:00:00+05:30', '{}', 1, 'f1'),
           ('e11', 'call.telephony_failed', 'motor_renewal', 'c4_1', 'hash_l4', '2026-10-01T12:00:05+05:30', '{"reason": "carrier_rejected"}', 1, 'f1'),
           ('e12', 'call.initiated', 'motor_renewal', 'c4_2', 'hash_l4', '2026-10-02T12:00:00+05:30', '{}', 1, 'f1'),
           ('e13', 'call.ended', 'motor_renewal', 'c4_2', 'hash_l4', '2026-10-02T12:00:40+05:30', '{"duration_sec": 0, "end_reason": "no_answer"}', 1, 'f1')
    """)

    conn.commit()

    ledger = LedgerService(conn, catalog)
    aggs = ledger.get_lead_aggregates()

    l1 = next(a for a in aggs if a.phone_hash == 'hash_l1')
    assert l1.bucket == 'yet_to_reach_out'
    assert l1.total_calls == 0

    l2 = next(a for a in aggs if a.phone_hash == 'hash_l2')
    assert l2.bucket == 'completed'
    assert l2.total_calls == 2
    assert l2.connected_calls == 1
    assert l2.current_disposition == 'renewed'
    assert l2.ever_success is True

    l3 = next(a for a in aggs if a.phone_hash == 'hash_l3')
    assert l3.bucket == 'in_progress_telephony_failed'
    assert l3.total_calls == 2
    assert l3.connected_calls == 0

    l4 = next(a for a in aggs if a.phone_hash == 'hash_l4')
    assert l4.bucket == 'in_progress_dialed'
    assert l4.total_calls == 2
