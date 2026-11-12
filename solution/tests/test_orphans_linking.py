"""Unit tests for event-to-lead linking and orphan resolution across drops."""
from __future__ import annotations

import sqlite3
import pytest
from solution.db import init_db
from solution.ledger import LedgerService
from solution.schema_loader import SchemaCatalog
from solution.crypto import hash_phone


@pytest.fixture
def db_with_catalog():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    catalog = SchemaCatalog()
    init_db(conn, catalog)
    return conn, catalog


def test_orphans_linked_across_drops(db_with_catalog):
    conn, catalog = db_with_catalog
    cursor = conn.cursor()
    ledger = LedgerService(conn, catalog)

    phone_canonical = "+919876543210"
    p_hash = hash_phone(phone_canonical)

    # 1. An event arrives in Drop 1 BEFORE the lead exists
    cursor.execute("""
    INSERT INTO events (event_id, event_type, workflow_id, call_id, phone_hash, occurred_at, payload, is_valid_disposition, file_path)
    VALUES ('evt_early_1', 'call.initiated', 'motor_renewal', 'call_101', ?, '2026-10-01T10:00:00+05:30', '{}', 1, 'drop1/events.jsonl'),
           ('evt_early_2', 'call.connected', 'motor_renewal', 'call_101', ?, '2026-10-01T10:00:10+05:30', '{}', 1, 'drop1/events.jsonl')
    """, (p_hash, p_hash))
    conn.commit()

    # Before lead arrives, both events are orphans
    assert ledger.count_orphan_events() == 2
    # And no linked calls exist yet
    assert len(ledger.get_all_linked_calls()) == 0

    # 2. In Drop 2, the lead arrives!
    cursor.execute("""
    INSERT INTO leads (workflow_id, phone_hash, phone_encrypted, phone_masked, customer_name_encrypted, external_ref, batch_id, created_at, updated_at)
    VALUES ('motor_renewal', ?, 'enc_p', '+91******3210', 'enc_n', 'MOT/2026/101', 'batch_003', '2026-10-05T10:00:00+05:30', '2026-10-05T10:00:00+05:30')
    """, (p_hash,))
    conn.commit()

    # Now that the lead has arrived, orphan count reflects final linked state: 0 orphans!
    assert ledger.count_orphan_events() == 0

    # And the call is now linked and recognized!
    calls = ledger.get_all_linked_calls()
    assert len(calls) == 1
    assert calls[0].call_id == 'call_101'
    assert calls[0].is_connected is True

    # 3. An override arrives with NO phone_hash, linking via call_id
    cursor.execute("""
    INSERT INTO events (event_id, event_type, workflow_id, call_id, phone_hash, occurred_at, payload, is_valid_disposition, file_path)
    VALUES ('evt_override', 'disposition.override', 'motor_renewal', 'call_101', NULL, '2026-10-05T15:00:00+05:30', '{"disposition_code": "renewed"}', 1, 'drop2/events.jsonl')
    """)
    conn.commit()

    # The override links through call_101 to the lead, so orphan count is still 0
    assert ledger.count_orphan_events() == 0

    calls_after_override = ledger.get_all_linked_calls()
    assert len(calls_after_override) == 1
    assert calls_after_override[0].effective_disposition == 'renewed'
    assert calls_after_override[0].is_terminal is True
